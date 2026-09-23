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
    from . import audit
    from .compute import analytics, publish, strategy
    from .ingest import backfill, industry, reference, symbol_changes
    from .ingest import corporate_actions as ca

    today = session or date.today()
    year = today.year

    print("── reference ────────────────────────────────────────────")
    try:
        reference.refresh()
    except Exception as err:  # noqa: BLE001 — stale sectors beat a failed run
        print(f"[eod] reference refresh failed ({err}) — continuing with cached map")

    # Building the labels is a manual job — a few thousand exchange calls — but
    # reconciling the ones already stored is a read-modify-write on a single R2
    # object, so it belongs here. Nightly, the sector names cannot drift back
    # apart between the builds; and the split they drift into is invisible
    # downstream, where every label is an opaque key.
    try:
        industry.normalize()
    except Exception as err:  # noqa: BLE001 — the labels are context, not the run
        print(f"[eod] label reconciliation failed ({err}) — using them as stored")

    # Which symbols are the same company under a new name. Soft: a stale map
    # costs a night of history being split at whatever renamed today, which is
    # how the lake read for years. It is not worth the publish.
    try:
        symbol_changes.refresh()
    except Exception as err:  # noqa: BLE001 — stale renames beat a failed run
        print(f"[eod] symbol change refresh failed ({err}) — continuing with stored map")

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

    print("── strategy ─────────────────────────────────────────────")
    # Fails soft on purpose. The paper book is downstream of everything the
    # terminal actually needs; a fault here must not cost the night's breadth,
    # sector and metrics publish.
    try:
        strategy.run(session=today)
    except Exception as err:  # noqa: BLE001 — a paper book is not worth a failed run
        print(f"[eod] strategy engine failed ({err}) — snapshot already published")

    b = snap["breadth"]
    print(f"[eod] {snap['date']}: {b['universe']} stocks · "
          f"adv {b['advances']} / dec {b['declines']} · "
          f"{b['newHighs']} new highs · {b['athCount']} at ATH")

    print("── audit ────────────────────────────────────────────────")
    # Last, and the only step allowed to fail the night.
    #
    # Deliberately after publish: the snapshot is already in Supabase by now, so
    # raising here costs nobody their data. What it buys is the one notification
    # channel this repo actually has — a failed scheduled workflow, which GitHub
    # mails. An audit that printed and returned would land exactly where the
    # `unverified` bucket landed, which is how a 10x re-basing on AHCL went five
    # months without anyone seeing it.
    #
    # The distinction that matters: a *finding* fails the run, an *outage* does
    # not. R2 being unreachable is not evidence of bad data, and failing on it
    # would teach everyone to ignore the mail.
    try:
        findings = audit.run(strict=False)
    except Exception as err:  # noqa: BLE001 — an unreachable lake is not a finding
        print(f"[eod] audit could not run ({err}) — snapshot already published")
        return

    if findings:
        detail = ", ".join(f"{f.symbol}@{f.date} k={f.implied:.2f}" for f in findings[:10])
        publish.log_event("audit", snap["date"], "failed",
                          f"{len(findings)} unexplained re-basing(s): {detail}")
        raise audit.AuditFailed(
            f"{len(findings)} unexplained re-basing(s) in the adjusted history: {detail}. "
            "Each is a split or bonus that was not applied, or a demerger to record "
            "in pipeline/accepted_residuals.json."
        )
    publish.log_event("audit", snap["date"], "ok", "no unexplained re-basings")
