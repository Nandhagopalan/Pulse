"""
The strategy engine: a rules-based swing book, advanced once a night.

    from pipeline.compute import strategy
    strategy.run()

Layout, in dependency order — nothing points back up the list:

    config.py    every tunable, in one frozen dataclass
    windows.py   trailing-window primitives (no lookahead by construction)
    rules.py     THE strategy: universe, regime, entries, exits, sizing
    book.py      one session of the paper book
    store.py     the only reader/writer of the strategy_* tables
    data.py      MarketData from R2, or from a local mirror
    backtest.py  book.advance() in a loop, over 18.6 years

`rules.py` and `book.py` are imported by both the live path and the backtest, so
re-validating a parameter change exercises exactly the code that produces
tomorrow morning's orders. See docs/strategy-engine.md.
"""
from __future__ import annotations

from datetime import date as Date
from typing import List, Optional

import numpy as np

from ...config import config as pipeline_config
from ...sources import r2
from . import book, data, rules, store
from .config import DEFAULT, StrategyConfig, preset

__all__ = ["DEFAULT", "DEFAULT_CAPITAL", "MANUAL_BOOK", "StrategyConfig", "preset", "run"]

# Sessions loaded beyond what the rules strictly need. Cheap insurance: a short
# window silently yields no signals rather than failing, which is the hardest
# kind of bug to notice in a nightly job.
_WARMUP_MARGIN = 60

# The operator's own book. Same rules, same capital; the engine only advises.
MANUAL_BOOK = "manual"

# Opening capital for a book created without --capital.
#
# This used to read `user_prefs.capital` so the terminal's number and the
# book's were the same one. That stopped being true: the seed is only ever read
# on the very first run, when `strategy_books` is still empty, while the number
# the book actually sizes from is `strategy_books.capital` — which the Strategy
# tab rewrites on reset. Two capitals, only one of them live. The profile field
# is gone; `--capital` sets this deliberately, and the book owns it afterwards.
DEFAULT_CAPITAL = 4_000_000.0


def run(book_ids: Optional[List[str]] = None, session: Optional[Date] = None,
        capital: Optional[float] = None, force: bool = False,
        since: Optional[Date] = None) -> None:
    """
    Advance every enabled book through the latest session.

    Re-running a session already recorded is a no-op, so a repeated nightly job
    converges instead of double-counting. `force` re-advances it anyway, which
    is for repairing a bad run — it does not undo the previous one.

    `since` advances every session from that date to the latest in one pass,
    loading the lake once. That is how a book catches up after missed nights,
    and it is what makes a multi-session replay cheap enough to test.
    """
    import psycopg

    url = pipeline_config.require_supabase()
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        ids = book_ids or store.enabled_books(cur)
        if not ids:
            # First run creates two books on the same capital and the same
            # rules: one the engine trades, one the operator does. Side by side
            # they answer different questions — does the strategy work, and how
            # does my execution compare — and neither can contaminate the other.
            seed = capital if capital is not None else DEFAULT_CAPITAL
            today = (session or Date.today()).isoformat()
            store.ensure_book(cur, DEFAULT.name, DEFAULT, seed, today,
                              note="seeded from --capital" if capital is not None
                              else "seeded from the default opening capital")
            store.ensure_book(cur, MANUAL_BOOK, DEFAULT, seed, today,
                              fill_mode="manual",
                              note="operator's own book; the engine only advises")
            conn.commit()
            ids = [DEFAULT.name, MANUAL_BOOK]
            print(f"[strategy] created books '{DEFAULT.name}' (auto) and "
                  f"'{MANUAL_BOOK}' (manual) with Rs {seed:,.0f}")

        for book_id in ids:
            _advance_book(conn, cur, book_id, session, force=force, since=since)
            conn.commit()


def _advance_book(conn, cur, book_id: str, session: Optional[Date],
                  force: bool = False, since: Optional[Date] = None) -> None:
    loaded = store.load_config(cur, book_id)
    if loaded is None:
        print(f"[strategy] {book_id}: no such book — skipped")
        return
    cfg, version, capital, fill_mode = loaded
    manual = fill_mode == "manual"

    # Only as much history as the rules need. Scanning 19 years nightly to
    # compute a 250-day high would cost minutes for no benefit.
    span = cfg.lookback_needed + _WARMUP_MARGIN
    con = r2.duck()
    if since is not None:
        # Catch-up needs the warm-up *plus* every session being replayed.
        span += _sessions_since(con, since.isoformat())
    start = _window_start(con, span)
    md = data.from_lake(con, start=start)
    feats = rules.compute_features(md, cfg)

    targets = _targets(md, session, since)
    if not targets:
        print(f"[strategy] {book_id}: nothing to advance")
        return

    col_of = {str(s): i for i, s in enumerate(md.symbols)}
    for t in targets:
        session_str = str(md.dates[t])[:10]
        if not force and store.already_advanced(cur, book_id, session_str):
            if since is None:
                print(f"[strategy] {book_id}: {session_str} already recorded — nothing to do")
            continue

        # Resuming from the session *before* this one is what stops a forced
        # re-advance accruing the day's interest twice. During a forward replay
        # that row is simply the latest one, so the two agree.
        #
        # It must not apply to a manual book: the API owns that book's cash,
        # `advance` never moves it, and rewinding would silently discard
        # positions the operator added during the session being re-run.
        state = store.load_state(cur, book_id, col_of, capital, version,
                                 before_session=None if manual else session_str)
        state.pending_entries = store.load_pending_signals(cur, book_id, col_of, session_str)
        flow = store.pending_cashflow(cur, book_id, session_str)
        # The base for the day's return comes from the record, not from the
        # state just rebuilt: on a manual book the two differ by whatever the
        # operator did through the API since. `book.advance` has the reasoning.
        prev_equity = store.load_prev_equity(cur, book_id, session_str)

        day = book.advance(state, md, feats, cfg, t, net_flow=flow, manual=manual,
                           prev_equity=prev_equity)
        # Context for the regime banner; carried on the day rather than
        # recomputed by the API, so the UI shows what the decision actually used.
        day.ew_index = float(feats.ew_index[t])          # type: ignore[attr-defined]
        day.ew_ma = float(feats.ew_ma[t])                # type: ignore[attr-defined]
        day.universe_n = int(feats.universe[t].sum())    # type: ignore[attr-defined]

        _size_candidates(day, cfg)
        store.save_day(cur, book_id, day, state, version)
        conn.commit()

        if since is None or day.opened or day.closed:
            tag = " (advisory)" if manual else ""
            print(f"[strategy] {book_id}{tag} {session_str}: "
                  f"regime {'ON' if day.regime_on else 'OFF'} · "
                  f"equity Rs {day.equity:,.0f} · {day.n_open} open · "
                  f"{len(day.opened)} in, {len(day.closed)} out · "
                  f"{len(day.candidates)} candidates")


def _targets(md, session: Optional[Date], since: Optional[Date]) -> List[int]:
    """Which session indices to advance, oldest first."""
    if since is not None:
        lo = int(np.searchsorted(md.dates, np.datetime64(since)))
        return list(range(lo, len(md.dates)))
    if session is None:
        return [len(md.dates) - 1]
    want = np.datetime64(session)
    idx = int(np.searchsorted(md.dates, want))
    if idx >= len(md.dates) or md.dates[idx] != want:
        print(f"[strategy] no session {session} in the lake — skipped")
        return []
    return [idx]


def _sessions_since(con, start: str) -> int:
    from ...config import CURATED_DAILY, s3_uri
    glob = s3_uri(f"{CURATED_DAILY}/*/data.parquet")
    row = con.execute(
        f"SELECT COUNT(DISTINCT date) FROM read_parquet('{glob}') WHERE date >= DATE '{start}'"
    ).fetchone()
    return int(row[0] or 0)


def _window_start(con, span: int) -> str:
    """The date `span` sessions back, so the scan stays bounded."""
    from ...config import CURATED_DAILY, s3_uri
    glob = s3_uri(f"{CURATED_DAILY}/*/data.parquet")
    row = con.execute(f"""
        SELECT MIN(date) FROM (
            SELECT DISTINCT date FROM read_parquet('{glob}')
            ORDER BY date DESC LIMIT {span}
        )
    """).fetchone()
    return row[0].isoformat()


def _size_candidates(day, cfg: StrategyConfig) -> None:
    """
    Attach quantity, position value and rupee risk to each candidate.

    Sized here rather than in the UI so the panel shows the same numbers the
    book will act on, and so `stop_pct` and `risk_amount` are auditable after
    the fact.
    """
    for cand in day.candidates:
        qty = rules.position_size(day.equity, day.cash, cand.ref_close, cand.stop,
                                  cand.turnover_20d, cfg)
        object.__setattr__(cand, "qty", qty)
        object.__setattr__(cand, "position_value", qty * cand.ref_close)
        object.__setattr__(cand, "risk_amount", qty * (cand.ref_close - cand.stop))
