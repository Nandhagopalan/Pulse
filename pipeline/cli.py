"""
Command-line interface.

Commands are dispatched here; `__main__.py` only forwards to `main()` so that
`python -m pipeline` and a console entry point can share one implementation.


    python -m pipeline backfill            # 2007 → today, NSE archives → R2
    python -m pipeline reference           # refresh index constituents / sectors
    python -m pipeline industry            # industry + sector labels, keyed by ISIN
    python -m pipeline industry --normalize  # reconcile stored labels, no fetching
    python -m pipeline actions             # rebuild the corporate action dataset
    python -m pipeline analytics           # compute the daily snapshot (dry run)
    python -m pipeline publish             # compute + upsert into Supabase
    python -m pipeline eod                 # nightly chain: ingest → actions → publish
    python -m pipeline verify RELIANCE     # audit one symbol end to end
    python -m pipeline audit               # audit the whole universe for unapplied actions
    python -m pipeline summary             # what the lake currently holds
    python -m pipeline sync --local DIR    # push a local mirror into R2
    python -m pipeline fno --start 2011-01-01 --end 2022-12-31 --bucket NAME
                                           # index futures + options into the F&O bucket
"""
from __future__ import annotations

import argparse
from datetime import date


def _add_store_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--local", metavar="DIR", help="mirror objects to a local directory")
    p.add_argument("--no-r2", action="store_true", help="work only against --local (no R2 traffic)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("backfill", help="historical ingest into the lake")
    p.add_argument("--start", type=date.fromisoformat)
    p.add_argument("--end", type=date.fromisoformat)
    p.add_argument("--force", action="store_true", help="rewrite years already present")
    _add_store_args(p)

    p = sub.add_parser("reference", help="refresh index constituents and sector map")

    p = sub.add_parser("industry", help="industry/sector labels for the lake, from BSE")
    p.add_argument("--refresh", action="store_true", help="re-fetch every scrip")
    p.add_argument("--limit", type=int, help="stop after N scrips (for a smoke test)")
    p.add_argument("--normalize", action="store_true",
                   help="reconcile the stored labels only; fetches nothing")

    p = sub.add_parser("actions", help="rebuild corporate actions from the NSE feed")
    p.add_argument("--refresh", action="store_true", help="re-fetch years already cached in R2")
    _add_store_args(p)

    p = sub.add_parser("analytics", help="compute the snapshot without publishing")
    _add_store_args(p)

    p = sub.add_parser("publish", help="compute and upsert into Supabase")
    _add_store_args(p)

    p = sub.add_parser("eod", help="nightly chain")
    p.add_argument("--date", type=date.fromisoformat, help="session to ingest (default: latest)")

    p = sub.add_parser("strategy", help="advance the paper book for the latest session")
    p.add_argument("--book", action="append", help="book id; repeatable, default all enabled")
    p.add_argument("--date", type=date.fromisoformat,
                   help="session to advance (default: latest)")
    p.add_argument("--capital", type=float, help="opening capital when creating the first book")
    p.add_argument("--since", type=date.fromisoformat,
                   help="advance every session from this date to the latest (catch-up)")
    p.add_argument("--force", action="store_true",
                   help="re-advance a session already recorded (repair; does not undo)")
    p.add_argument("--set-preset", metavar="NAME",
                   help="point --book at this preset, then exit unless --since is given")
    p.add_argument("--wipe", action="store_true",
                   help="with --set-preset: clear that book's history so the new "
                        "rules start flat (never touches the manual book)")

    p = sub.add_parser("verify", help="audit one symbol end to end")
    p.add_argument("symbol")
    _add_store_args(p)

    p = sub.add_parser("audit", help="scan the adjusted history for unapplied actions")
    p.add_argument("--warn", action="store_true",
                   help="report and exit 0 even when something is unexplained")
    p.add_argument("--accept-current", action="store_true",
                   help="rewrite accepted_residuals.json from what the lake shows now")
    _add_store_args(p)

    p = sub.add_parser("summary", help="lake contents")
    _add_store_args(p)

    p = sub.add_parser("fno", help="index futures and options into the separate F&O bucket")
    p.add_argument("--start", type=date.fromisoformat)
    p.add_argument("--end", type=date.fromisoformat)
    p.add_argument("--symbols", default="NIFTY,BANKNIFTY", help="comma-separated index symbols")
    p.add_argument("--bucket", help="R2 bucket for the F&O lake (default: FNO_R2_BUCKET)")
    p.add_argument("--force", action="store_true", help="rebuild years already curated")
    p.add_argument("--summary", action="store_true", help="only print what the F&O lake holds")
    _add_store_args(p)

    p = sub.add_parser("sync", help="upload a local mirror into R2")
    p.add_argument("--local", metavar="DIR", required=True)
    p.add_argument("--prefix", action="append", help="limit to key prefixes (repeatable)")

    args = ap.parse_args(argv)

    from .ingest import backfill
    if getattr(args, "local", None) or getattr(args, "no_r2", False):
        backfill.use_local(getattr(args, "local", None), with_r2=not getattr(args, "no_r2", False))

    if args.cmd == "backfill":
        backfill.run(start=args.start, end=args.end, force=args.force)
        backfill.lake_summary()

    elif args.cmd == "reference":
        from .ingest import reference
        print(f"[reference] {reference.refresh()} constituent rows written")

    elif args.cmd == "industry":
        from .ingest import industry
        table = (industry.normalize() if args.normalize
                 else industry.build(refresh=args.refresh, limit=args.limit))
        print("[industry]", industry.summary(table))

    elif args.cmd == "actions":
        from .ingest import corporate_actions as ca
        table = ca.build(refresh=args.refresh)
        print("[actions]", ca.summary(table))

    elif args.cmd == "analytics":
        from .compute import analytics
        snap = analytics.compute()
        b = snap["breadth"]
        breaks = sum(1 for s in snap["stocks"] if s.get("trendBreak"))
        print(f"[analytics] {snap['date']}: {b['universe']} stocks, "
              f"{len(snap['sectors'])} sectors, adv {b['advances']} / dec {b['declines']}, "
              f"new highs {b['newHighs']}, at ATH {b['athCount']}, trendline breaks {breaks}")

    elif args.cmd == "publish":
        from .compute import publish
        publish.run()

    elif args.cmd == "eod":
        from . import jobs
        jobs.eod(args.date)

    elif args.cmd == "strategy":
        from .compute import strategy
        if args.set_preset:
            if not args.book:
                raise SystemExit("--set-preset needs --book")
            for book_id in args.book:
                strategy.retune(book_id, args.set_preset, wipe=args.wipe,
                                capital=args.capital, started_on=args.since)
            if args.since is None:
                return 0
        strategy.run(book_ids=args.book, session=args.date, capital=args.capital,
                     force=args.force, since=args.since)

    elif args.cmd == "audit":
        from . import audit as audit_mod
        if args.accept_current:
            n = audit_mod.rebuild_accepted()
            print(f"[audit] accepted_residuals.json now lists {n} reviewed residuals")
        else:
            # A findings failure is an expected outcome, not a crash: report it
            # as an exit code rather than a traceback, so CI logs show the
            # verdict and not a stack.
            try:
                audit_mod.run(strict=not args.warn)
            except audit_mod.AuditFailed as err:
                print(f"[audit] FAILED — {err}")
                return 1

    elif args.cmd == "verify":
        from . import verify
        verify.report(args.symbol.upper())

    elif args.cmd == "summary":
        backfill.lake_summary()

    elif args.cmd == "fno":
        from .ingest import fno
        fno.configure(bucket=args.bucket, local=args.local, with_r2=not args.no_r2)
        if not args.summary:
            if args.start is None or args.end is None:
                raise SystemExit("fno needs --start and --end (or --summary)")
            symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            fno.run(args.start, args.end, symbols=symbols, force=args.force)
        fno.summary()

    elif args.cmd == "sync":
        backfill.sync_to_r2(prefixes=args.prefix)

    return 0

