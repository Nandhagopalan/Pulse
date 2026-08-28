"""
The only module that reads or writes the strategy_* tables.

Everything above it works in memory: `rules.py` decides, `book.py` advances,
and this maps that to rows. Keeping the SQL in one place means the schema can
change without touching anything that reasons about trading.

Uses psycopg directly, like `compute/publish.py`, rather than the Node adapter.
"""
from __future__ import annotations

from datetime import date as Date
from typing import Dict, List, Optional, Tuple

import numpy as np

from .book import BookState, Position
from .config import StrategyConfig
from .rules import Candidate

_ISO = "%Y-%m-%d"


def _d(value) -> str:
    """numpy date / date / str -> 'YYYY-MM-DD', the schema's date form."""
    if isinstance(value, str):
        return value[:10]
    if isinstance(value, np.datetime64):
        return str(value.astype("datetime64[D]"))
    if isinstance(value, Date):
        return value.strftime(_ISO)
    return str(value)[:10]


# ── books ───────────────────────────────────────────────────────────────────
def ensure_book(cur, book_id: str, cfg: StrategyConfig, capital: float,
                today: str, note: str = "created", fill_mode: str = "auto") -> int:
    """
    Create the book if new, or version its config if it changed.

    Returns the config version in force. A changed config is never overwritten
    in place: past trades stay attributable to the parameters that produced
    them, which is the only way "what did this setting actually do" survives a
    tweak.
    """
    cur.execute("SELECT config, config_version FROM strategy_books WHERE id = %s", (book_id,))
    row = cur.fetchone()
    blob = cfg.to_json()

    if row is None:
        cur.execute(
            """INSERT INTO strategy_books
               (id, enabled, fill_mode, config, config_version, capital, started_on,
                created_at, updated_at)
               VALUES (%s, TRUE, %s, %s, 1, %s, %s, %s, %s)""",
            (book_id, fill_mode, blob, capital, today, today, today),
        )
        cur.execute(
            """INSERT INTO strategy_config_log (book_id, version, config, changed_at, note)
               VALUES (%s, 1, %s, %s, %s)""",
            (book_id, blob, today, note),
        )
        return 1

    stored, version = row
    if stored == blob:
        return int(version)

    version = int(version) + 1
    cur.execute(
        "UPDATE strategy_books SET config = %s, config_version = %s, updated_at = %s WHERE id = %s",
        (blob, version, today, book_id),
    )
    cur.execute(
        """INSERT INTO strategy_config_log (book_id, version, config, changed_at, note)
           VALUES (%s, %s, %s, %s, %s)""",
        (book_id, version, blob, today, note),
    )
    return version


def load_config(cur, book_id: str) -> Optional[Tuple[StrategyConfig, int, float, str]]:
    """(config, version, capital, fill_mode) as currently stored."""
    cur.execute(
        "SELECT config, config_version, capital, fill_mode FROM strategy_books WHERE id = %s",
        (book_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return StrategyConfig.from_json(row[0]), int(row[1]), float(row[2]), row[3]


def enabled_books(cur) -> List[str]:
    cur.execute("SELECT id FROM strategy_books WHERE enabled ORDER BY id")
    return [r[0] for r in cur.fetchall()]


# ── state reconstruction ────────────────────────────────────────────────────
def load_state(cur, book_id: str, col_of: Dict[str, int], capital: float,
               config_version: int, before_session: Optional[str] = None) -> BookState:
    """
    Rebuild the in-memory book from Postgres.

    Positions are keyed by column index, and those indices belong to whatever
    `MarketData` was loaded this run — a symbol's column moves as the universe
    changes. So they are resolved through `col_of` here rather than stored.
    """
    # `before_session` matters on a forced re-advance: resuming from the row
    # about to be overwritten would accrue that session's interest twice.
    if before_session:
        cur.execute("""SELECT cash FROM strategy_state
                        WHERE book_id = %s AND date < %s ORDER BY date DESC LIMIT 1""",
                    (book_id, before_session))
    else:
        cur.execute("""SELECT cash FROM strategy_state
                        WHERE book_id = %s ORDER BY date DESC LIMIT 1""", (book_id,))
    row = cur.fetchone()
    state = BookState(cash=float(row[0]) if row else capital,
                      config_version=config_version)

    cur.execute(
        """SELECT id, symbol, sector, entry_date, entry_px, qty, init_stop, stop,
                  r_per_share, last_px, bars, stale, pending_exit, config_version, origin
           FROM strategy_positions WHERE book_id = %s AND status = 'open'""",
        (book_id,),
    )
    for r in cur.fetchall():
        symbol = r[1]
        col = col_of.get(symbol)
        if col is None:
            # Delisted or renamed out of the current universe. The stale exit
            # will realise it once the loader stops returning bars for it; until
            # then there is nothing to mark it against.
            continue
        pos = Position(
            symbol=symbol, col=col, entry_date=np.datetime64(_d(r[3])),
            entry=float(r[4]), qty=int(r[5]), init_stop=float(r[6]), stop=float(r[7]),
            r_per_share=float(r[8]), sector=r[2],
            last_px=float(r[9]) if r[9] is not None else float(r[4]),
            bars=int(r[10]), stale=int(r[11]),
            config_version=int(r[13]), origin=r[14], id=int(r[0]),
        )
        pos.peak = max(pos.entry, pos.last_px)
        state.positions[col] = pos
        if r[12]:
            state.pending_exits.append((col, r[12]))
    return state


def load_prev_equity(cur, book_id: str, session: str) -> Optional[float]:
    """
    Closing equity of the last session recorded *before* `session`.

    The base a session's return is measured against, and deliberately the stored
    number rather than one re-derived from the book's current state: on a manual
    book the API moves cash and positions between runs, so the reconstruction is
    already past the point the day started from. See `book.advance`.
    """
    cur.execute("""SELECT equity FROM strategy_state
                    WHERE book_id = %s AND date < %s ORDER BY date DESC LIMIT 1""",
                (book_id, session))
    row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else None


def load_pending_signals(cur, book_id: str, col_of: Dict[str, int],
                         session: str) -> List[Candidate]:
    """
    Unfilled candidates from *before* this session, in rank order.

    The `date < session` bound is load-bearing, not defensive. A signal is
    computed from a session's close and filled at the next session's open;
    without the bound, re-running a session would fill that session's own
    signals at its own open — an open that happened hours before the close that
    produced them. That is lookahead, and it would inflate results silently.
    """
    cur.execute(
        """SELECT symbol, rank, ref_close, stop, stop_pct, atr, rs_pct, sector, turnover_20d
           FROM strategy_signals
           WHERE book_id = %s AND status = 'pending' AND date < %s
             AND date = (SELECT MAX(date) FROM strategy_signals
                          WHERE book_id = %s AND status = 'pending' AND date < %s)
           ORDER BY rank""",
        (book_id, session, book_id, session),
    )
    out: List[Candidate] = []
    for r in cur.fetchall():
        col = col_of.get(r[0])
        if col is None:
            continue
        out.append(Candidate(col=col, symbol=r[0], rank=int(r[1]), ref_close=float(r[2]),
                             stop=float(r[3]), stop_pct=float(r[4]), atr=float(r[5]),
                             rs_pct=float(r[6]) if r[6] is not None else 0.0,
                             sector=r[7], turnover_20d=float(r[8] or 0.0)))
    return out


def pending_cashflow(cur, book_id: str, session: str) -> float:
    """Deposits and withdrawals dated on or before this session, not yet applied."""
    cur.execute(
        """SELECT COALESCE(SUM(amount), 0) FROM strategy_cashflows
           WHERE book_id = %s AND date = %s""",
        (book_id, session),
    )
    return float(cur.fetchone()[0] or 0.0)


# ── writes ──────────────────────────────────────────────────────────────────
def save_day(cur, book_id: str, day, state: BookState, config_version: int) -> None:
    """Persist one advanced session: state row, position changes, new signals."""
    session = _d(day.date)

    cur.execute(
        """INSERT INTO strategy_state
             (book_id, date, regime_on, ew_index, ew_ma, universe_n, equity, cash,
              deployed, n_open, net_flow, twr_factor)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (book_id, date) DO UPDATE SET
             regime_on=EXCLUDED.regime_on, ew_index=EXCLUDED.ew_index, ew_ma=EXCLUDED.ew_ma,
             universe_n=EXCLUDED.universe_n, equity=EXCLUDED.equity, cash=EXCLUDED.cash,
             deployed=EXCLUDED.deployed, n_open=EXCLUDED.n_open,
             net_flow=EXCLUDED.net_flow, twr_factor=EXCLUDED.twr_factor""",
        (book_id, session, day.regime_on, getattr(day, "ew_index", None),
         getattr(day, "ew_ma", None), getattr(day, "universe_n", None),
         day.equity, day.cash, day.deployed, day.n_open, day.net_flow, day.twr_factor),
    )

    for pos in day.opened:
        cur.execute(
            """INSERT INTO strategy_positions
                 (book_id, config_version, origin, symbol, sector, entry_date, entry_px,
                  qty, init_stop, stop, r_per_share, last_px, bars, stale, status)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'open')
               RETURNING id""",
            (book_id, pos.config_version, pos.origin, pos.symbol, pos.sector,
             _d(pos.entry_date), pos.entry, pos.qty, pos.init_stop, pos.stop,
             pos.r_per_share, pos.last_px, pos.bars, pos.stale),
        )
        pos.id = int(cur.fetchone()[0])

    for pos in day.closed:
        cur.execute(
            """UPDATE strategy_positions
                  SET status='closed', exit_date=%s, exit_px=%s, exit_reason=%s,
                      pnl=%s, r_multiple=%s, bars=%s, last_px=%s, pending_exit=NULL
                WHERE book_id=%s AND symbol=%s AND status='open'""",
            (_d(pos.exit_date), pos.exit, pos.reason, pos.pnl, pos.r_multiple,
             pos.bars, pos.last_px, book_id, pos.symbol),
        )

    # Survivors: ageing plus any exit queued for tomorrow's open.
    queued = dict(state.pending_exits)
    for col, pos in state.positions.items():
        cur.execute(
            """UPDATE strategy_positions
                  SET bars=%s, stale=%s, stop=%s, last_px=%s, pending_exit=%s
                WHERE book_id=%s AND symbol=%s AND status='open'""",
            (pos.bars, pos.stale, pos.stop, pos.last_px, queued.get(col),
             book_id, pos.symbol),
        )

    # Yesterday's signals are resolved: anything still pending was not taken.
    cur.execute(
        """UPDATE strategy_signals SET status='skipped', skip_reason='not filled'
            WHERE book_id=%s AND status='pending' AND date < %s""",
        (book_id, session),
    )
    filled = {p.symbol for p in day.opened}
    if filled:
        cur.execute(
            """UPDATE strategy_signals SET status='filled', skip_reason=NULL
                WHERE book_id=%s AND symbol = ANY(%s) AND status IN ('pending','skipped')
                  AND date < %s""",
            (book_id, list(filled), session),
        )

    # Only the candidates that were actually queued are 'pending'. The rest are
    # recorded for the record, but must never be filled: book.advance() queued
    # `candidates[:free]`, and reloading the full list tomorrow would fill names
    # the in-memory run never considered — which is exactly how the live book
    # and the backtest drift apart.
    queued_syms = {c.symbol for c in state.pending_entries}
    for cand in day.candidates:
        pending = cand.symbol in queued_syms
        cur.execute(
            """INSERT INTO strategy_signals
                 (book_id, date, symbol, rank, ref_close, stop, stop_pct, atr, rs_pct,
                  sector, turnover_20d, qty, position_value, risk_amount,
                  status, skip_reason)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (book_id, date, symbol) DO UPDATE SET
                 rank=EXCLUDED.rank, ref_close=EXCLUDED.ref_close, stop=EXCLUDED.stop,
                 stop_pct=EXCLUDED.stop_pct, atr=EXCLUDED.atr, rs_pct=EXCLUDED.rs_pct,
                 qty=EXCLUDED.qty, position_value=EXCLUDED.position_value,
                 risk_amount=EXCLUDED.risk_amount, status=EXCLUDED.status,
                 skip_reason=EXCLUDED.skip_reason""",
            (book_id, session, cand.symbol, cand.rank, cand.ref_close, cand.stop,
             cand.stop_pct, cand.atr, cand.rs_pct, cand.sector, cand.turnover_20d,
             getattr(cand, "qty", 0), getattr(cand, "position_value", 0.0),
             getattr(cand, "risk_amount", 0.0),
             "pending" if pending else "skipped",
             None if pending else "beyond available slots"),
        )


def already_advanced(cur, book_id: str, session: str) -> bool:
    """Whether this session has already been recorded for this book."""
    cur.execute("SELECT 1 FROM strategy_state WHERE book_id = %s AND date = %s",
                (book_id, session))
    return cur.fetchone() is not None
