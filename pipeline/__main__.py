"""
Pipeline CLI.

    python -m pipeline backfill            # 2007 → today, NSE archives → R2
    python -m pipeline reference           # refresh index constituents / sectors
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
import sys
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

    p = sub.add_parser("actions", help="rebuild corporate actions from the NSE feed")
    p.add_argument("--refresh", action="store_true", help="re-fetch years already cached in R2")
    _add_store_args(p)

    p = sub.add_parser("analytics", help="compute the snapshot without publishing")
    _add_store_args(p)

    p = sub.add_parser("publish", help="compute and upsert into Supabase")
    _add_store_args(p)

    p = sub.add_parser("eod", help="nightly chain")
    p.add_argument("--date", type=date.fromisoformat, help="session to ingest (default: latest)")

    p = sub.add_parser("verify", help="audit one symbol end to end")
    p.add_argument("symbol")
    _add_store_args(p)

    p = sub.add_parser("summary", help="lake contents")
    _add_store_args(p)

    p = sub.add_parser("sync", help="upload a local mirror into R2")
    p.add_argument("--local", metavar="DIR", required=True)
    p.add_argument("--prefix", action="append", help="limit to key prefixes (repeatable)")

    args = ap.parse_args(argv)

    from . import backfill
    if getattr(args, "local", None) or getattr(args, "no_r2", False):
        backfill.use_local(getattr(args, "local", None), with_r2=not getattr(args, "no_r2", False))

    if args.cmd == "backfill":
        backfill.run(start=args.start, end=args.end, force=args.force)
        backfill.lake_summary()

    elif args.cmd == "reference":
        from . import reference
        print(f"[reference] {reference.refresh()} constituent rows written")

    elif args.cmd == "actions":
        from . import corporate_actions as ca
        table = ca.build(refresh=args.refresh)
        print("[actions]", ca.summary(table))

    elif args.cmd == "analytics":
        from . import analytics
        snap = analytics.compute()
        b = snap["breadth"]
        print(f"[analytics] {snap['date']}: {b['universe']} stocks, "
              f"{len(snap['sectors'])} sectors, adv {b['advances']} / dec {b['declines']}, "
              f"new highs {b['newHighs']}, at ATH {b['athCount']}")

    elif args.cmd == "publish":
        from . import publish
        publish.run()

    elif args.cmd == "eod":
        from . import jobs
        jobs.eod(args.date)

    elif args.cmd == "verify":
        from . import verify
        verify.report(args.symbol.upper())

    elif args.cmd == "summary":
        backfill.lake_summary()

    elif args.cmd == "sync":
        backfill.sync_to_r2(prefixes=args.prefix)

    return 0


if __name__ == "__main__":
    sys.exit(main())
