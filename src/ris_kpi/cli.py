"""Command line entry point: `ris-kpi <command>` (or `python -m ris_kpi <command>`).

    ris-kpi run                                  # ris_view -> ris_trends (full KPI creation)
    ris-kpi run --keep-going                     # still run ris_trends when ris_view fails
    ris-kpi status-update                        # only defect statuses changed: RIS view (scenario 2 / 1)
    ris-kpi ris-view [--step silver-status intermediate view]   # RIS view; --step: those steps only (UPDATE)
    ris-kpi ris-trends
    ris-kpi compare OLD_DIR NEW_DIR              # before/after output check
    ris-kpi sync-db-ids [--dry-run]              # after a run: id / created_at from the DB KPI tables

Exit code 1 when anything fails.
"""
import argparse
import logging
import sys

from ris_kpi.common.logging import setup_logging

log = logging.getLogger("ris_kpi")


def _build_parser():
    from ris_kpi import pipelines, ris_view
    from ris_kpi.tools.compare_outputs import DEFAULT_IGNORE

    parser = argparse.ArgumentParser(prog="ris-kpi", description="RIS KPI pipelines (ris_view_data, ris_trends_data).")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="full KPI creation: ris_view -> ris_trends")
    run.add_argument("--only", nargs="+", choices=list(pipelines.STEPS), help="run only these steps")
    run.add_argument("--keep-going", action="store_true", help="continue with independent steps after a failure")

    sub.add_parser("status-update", help="defect statuses changed in the DB: refresh ris_view_data (same as ris-view)")

    rv = sub.add_parser("ris-view", help="run the RIS view (scenario 1 / 2, reads the latest defect statuses from S3)")
    rv.add_argument("--step", nargs="+", choices=list(ris_view.STEPS),
                    help="run only these steps, in UPDATE mode for all surveys (default: the full scenario run)")

    sub.add_parser("ris-trends", help="run the RIS trends KPI")

    cmp_ = sub.add_parser("compare", help="compare two output_data folders (before/after a code change)")
    cmp_.add_argument("old_dir")
    cmp_.add_argument("new_dir")
    cmp_.add_argument("--ignore-columns", nargs="*", default=list(DEFAULT_IGNORE),
                      help=f"columns left out of the comparison (default: {' '.join(DEFAULT_IGNORE)})")
    cmp_.add_argument("--sort-rows", action="store_true", help="compare rows regardless of their order")

    sync = sub.add_parser("sync-db-ids", help="copy id / created_at of ris_view_kpi / ris_trends_kpi (DB export) "
                                              "into the saved ris_view_data / ris_trends_data")
    sync.add_argument("--dry-run", action="store_true", help="report only, nothing saved")
    sync.add_argument("--only", nargs="+", choices=["ris_view", "ris_trends"], help="these tables only")
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    setup_logging()

    from ris_kpi import pipelines
    from ris_kpi.ris_trends import run_ris_trends
    from ris_kpi.ris_view import run_ris_view
    from ris_kpi.tools.compare_outputs import compare_dirs

    try:
        if args.command == "run":
            results = pipelines.run_full(only=args.only, keep_going=args.keep_going)
            ok = all(status == "OK" for status, _, _ in results.values())
        elif args.command in ("ris-view", "status-update"):
            run_ris_view(getattr(args, "step", None))
            ok = True
        elif args.command == "ris-trends":
            run_ris_trends()
            ok = True
        elif args.command == "compare":
            ok = compare_dirs(args.old_dir, args.new_dir, args.ignore_columns, args.sort_rows)
        elif args.command == "sync-db-ids":
            from ris_kpi.tools.db_ids_sync import run_sync
            ok = run_sync(only=args.only, dry_run=args.dry_run)
    except Exception:
        log.exception("%s FAILED", args.command)
        ok = False
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
