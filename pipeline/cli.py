"""
Command-line interface.

Commands are dispatched here; `__main__.py` only forwards to `main()` so that
`python -m pipeline` and a console entry point can share one implementation.


    python -m pipeline backfill            # 2007 → today, NSE archives → R2
    python -m pipeline reference           # refresh index constituents / sectors
    python -m pipeline industry            # industry + sector labels, keyed by ISIN
    python -m pipeline actions             # rebuild the corporate action dataset
    python -m pipeline analytics           # compute the daily snapshot (dry run)
    python -m pipeline publish             # compute + upsert into Supabase
    python -m pipeline eod                 # nightly chain: ingest → actions → publish
    python -m pipeline verify RELIANCE     # audit one symbol end to end
    python -m pipeline summary             # what the lake currently holds
    python -m pipeline sync --local DIR    # push a local mirror into R2
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

    p = sub.add_parser("summary", help="lake contents")
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
        table = industry.build(refresh=args.refresh, limit=args.limit)
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

    elif args.cmd == "verify":
        from . import verify
        verify.report(args.symbol.upper())

    elif args.cmd == "summary":
        backfill.lake_summary()

    elif args.cmd == "sync":
        backfill.sync_to_r2(prefixes=args.prefix)

    return 0

