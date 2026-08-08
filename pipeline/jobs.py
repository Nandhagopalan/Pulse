"""
Scheduled job chains.

`eod` is what the nightly GitHub Action runs. Every step is idempotent, so a
re-run — after a failure, or because the cron fired twice — converges to the same
state rather than double-counting. That also means a missed day is fixed by
simply running it again.
"""
from __future__ import annotations

from datetime import date
from typing import Optional


def eod(session: Optional[date] = None) -> None:
    from . import analytics, backfill, corporate_actions as ca, publish, reference

    today = session or date.today()
    year = today.year

    print("── reference ────────────────────────────────────────────")
    try:
        reference.refresh()
    except Exception as err:  # noqa: BLE001 — stale sectors beat a failed run
        print(f"[eod] reference refresh failed ({err}) — continuing with cached map")

    print("── ingest ───────────────────────────────────────────────")
    # Re-running the open year picks up today's session and repairs any day the
    # cron missed. Sessions already cached in R2 are not re-fetched from NSE.
    backfill.run(start=date(year, 1, 1), end=today, force=True)

    print("── corporate actions ────────────────────────────────────")
    try:
        table = ca.build(refresh_years={year})
        print("[eod]", ca.summary(table))
    except Exception as err:  # noqa: BLE001
        print(f"[eod] corporate action refresh failed ({err}) — using last good dataset")

    print("── analytics + publish ──────────────────────────────────")
    snap = analytics.compute()
    publish.run(snap)

    b = snap["breadth"]
    print(f"[eod] {snap['date']}: {b['universe']} stocks · "
          f"adv {b['advances']} / dec {b['declines']} · "
          f"{b['newHighs']} new highs · {b['athCount']} at ATH")
