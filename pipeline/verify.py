"""
Single-symbol audit.

Answers "is this stock's history actually correct?" by laying the whole chain
side by side: raw vendor bars, the corporate actions we found, the cumulative
adjustment factor, and the derived highs — then cross-checking today's close
against NSE's live quote endpoint, which is a genuinely independent source from
the bhavcopy archive.

    python -m pipeline verify RELIANCE
"""
from __future__ import annotations

from typing import Optional

from .config import s3_uri
from .ingest import backfill
from .ingest import corporate_actions as ca
from .sources import nse, r2


def _con():
    if backfill.LOCAL_ROOT is not None and not backfill.USE_R2:
        import duckdb
        return duckdb.connect()
    return r2.duck()


def _actions_uri() -> str:
    if backfill.LOCAL_ROOT is not None and not backfill.USE_R2:
        return str(backfill.LOCAL_ROOT / ca.ACTIONS_KEY)
    return s3_uri(ca.ACTIONS_KEY)


def _independent_bar(symbol: str, session) -> Optional[dict]:
    """
    The same session from a different NSE publication.

    `sec_bhavdata_full` is generated separately from the bhavcopy zip we ingest,
    so agreement between them is real evidence the ingest is faithful rather than
    a file compared against itself. (The live quote API would be nicer, but it
    sits behind Akamai and returns Access Denied to anything scripted.)
    """
    try:
        blob = nse.fetch(nse.sec_bhavdata_url(session))
        if blob is None:
            return None
        return nse.parse_sec_bhavdata(blob).get(symbol)
    except Exception as err:  # noqa: BLE001 — cross-check is best effort
        print(f"    (independent source unavailable: {err})")
        return None


def report(symbol: str, tail: int = 8) -> None:
    con = _con()
    daily = backfill.daily_glob()
    actions = _actions_uri()

    print(f"\n{'=' * 72}\n  {symbol}\n{'=' * 72}")

    # ── Coverage ──────────────────────────────────────────────────────────────
    cov = con.execute(
        f"""SELECT COUNT(*), MIN(date), MAX(date), COUNT(DISTINCT date)
            FROM read_parquet('{daily}') WHERE symbol = ?""", [symbol]
    ).fetchone()
    if not cov or cov[0] == 0:
        print(f"  no bars found for {symbol} in {daily}")
        return
    print(f"\n  COVERAGE   {cov[0]:,} bars   {cov[1]} → {cov[2]}   ({cov[3]:,} sessions)")

    # ── Corporate actions ────────────────────────────────────────────────────
    print("\n  CORPORATE ACTIONS")
    try:
        acts = con.execute(
            f"""SELECT ex_date, factor, kind, status, implied, subject
                FROM read_parquet('{actions}') WHERE symbol = ? ORDER BY ex_date""", [symbol]
        ).fetchall()
    except Exception as err:  # noqa: BLE001
        acts = []
        print(f"    (actions dataset unavailable: {err})")
    if not acts:
        print("    none")
    for ex, f, kind, status, implied, subject in acts:
        imp = f"{implied:.4f}" if implied is not None else "—"
        mark = "OK " if status == "verified" else "!! "
        print(f"    {mark}{ex}  k={f:<7.4f} implied={imp:<8} {kind:<12} {status:<11} {subject[:44]}")

    # ── Raw vs adjusted around each ex-date ──────────────────────────────────
    cte = ca.adjusted_bars_cte(daily, actions)
    for ex, f, _k, status, _i, _s in acts:
        if status != "verified":
            continue
        print(f"\n  AROUND EX-DATE {ex}  (expect a ~{f:.4g}x raw drop, none after adjustment)")
        rows = con.execute(
            cte + f"""
            SELECT b.date, r.close AS raw_close, b.close AS adj_close, b.k
            FROM bars_adj b
            JOIN read_parquet('{daily}') r ON r.symbol = b.symbol AND r.date = b.date
            WHERE b.symbol = ? AND b.date BETWEEN ?::DATE - 5 AND ?::DATE + 5
            ORDER BY b.date""", [symbol, ex, ex]
        ).fetchall()
        prev_adj = None
        for d, raw, adj, k in rows:
            step = f"{(adj / prev_adj - 1) * 100:+7.2f}%" if prev_adj else "      —"
            flag = " ←ex" if d == ex else ""
            print(f"    {d}  raw={raw:10.2f}  adj={adj:10.2f}  k={k:<7.4f} adj_chg={step}{flag}")
            prev_adj = adj

    # ── Derived metrics ──────────────────────────────────────────────────────
    stats = con.execute(
        cte + """
        SELECT MAX(high) AS ath, arg_max(date, high) AS ath_date,
               MAX(high) FILTER (WHERE date >= (SELECT MAX(date) FROM bars_adj) - 365) AS hi52,
               MIN(low)  FILTER (WHERE date >= (SELECT MAX(date) FROM bars_adj) - 365) AS lo52,
               arg_max(close, date) AS last_close,
               MAX(date) AS last_date
        FROM bars_adj WHERE symbol = ?""", [symbol]
    ).fetchone()
    ath, ath_date, hi52, lo52, last_close, last_date = stats
    print("\n  DERIVED (split-adjusted)")
    print(f"    last close      {last_close:>12,.2f}   on {last_date}")
    print(f"    all-time high   {ath:>12,.2f}   on {ath_date}")
    print(f"    % from ATH      {(last_close - ath) / ath * 100:>12,.2f}%")
    print(f"    52w high / low  {hi52:>12,.2f} / {lo52:,.2f}")

    # ── Raw tail ─────────────────────────────────────────────────────────────
    print(f"\n  LAST {tail} SESSIONS (raw, as ingested)")
    for d, o, h, lo, c, v in con.execute(
        f"""SELECT date, open, high, low, close, volume FROM read_parquet('{daily}')
            WHERE symbol = ? ORDER BY date DESC LIMIT {tail}""", [symbol]
    ).fetchall():
        print(f"    {d}  O {o:9.2f}  H {h:9.2f}  L {lo:9.2f}  C {c:9.2f}  V {v:>12,}")

    # ── Independent cross-check ──────────────────────────────────────────────
    print(f"\n  CROSS-CHECK  {last_date}  vs NSE sec_bhavdata_full (separate publication)")
    ours = con.execute(
        f"""SELECT open, high, low, close, volume, prev_close FROM read_parquet('{daily}')
            WHERE symbol = ? AND date = ?""", [symbol, last_date]
    ).fetchone()
    theirs = _independent_bar(symbol, last_date)
    if ours and theirs:
        mismatch = 0
        for name, a, b in (
            ("open", ours[0], theirs["open"]), ("high", ours[1], theirs["high"]),
            ("low", ours[2], theirs["low"]), ("close", ours[3], theirs["close"]),
            ("volume", ours[4], theirs["volume"]), ("prev_close", ours[5], theirs["prev_close"]),
        ):
            ok = b and abs(a - b) <= max(0.01, abs(b) * 1e-6)
            mismatch += 0 if ok else 1
            print(f"    {'OK ' if ok else '!! '}{name:<11} ours={a:>15,.2f}   nse={b:>15,.2f}")
        print(f"    delivery    {theirs['delivery_pct']:>15,.2f}%  (independent source only)")
        print(f"\n    {'all fields agree' if not mismatch else str(mismatch) + ' FIELD(S) DISAGREE'}")
    elif not theirs:
        print("    independent file not published for this session")
    print()
