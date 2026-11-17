"""RIS view step 2 — affected-length intermediate (modular version of
code_notebooks/Affected length intermediate logic v3 in_use.ipynb, on the smallest-reporting-interval grain).

Reads the silver tables and the reporting intervals from RisView.SILVER_INPUT_DIR and the silver defect ids /
statuses from the newest saved silver status (RisView.SILVER_STATUS_ROOT, written by step 1 in the same run), and
saves final_intermediate under RisView.INTERMEDIATE_ROOT (year=/month=/day=<run date> + latest): one row per
smallest-reporting-interval window of each upc / zone / phase / cycle, with defect flags, affected lengths, ids
and statuses. ris_view/pipeline.py runs it in one of two modes (ris_view/incremental.py):

  INSERT (new surveys)  calculated for the new surveys only -> rows added to the newest saved final_intermediate
  UPDATE (all surveys)  calculated for every survey -> the ids / statuses (INTERMEDIATE_UPDATE) of the matching rows
                        of the newest saved final_intermediate are updated. No saved table yet -> saved whole.

    ris-kpi ris-view --step intermediate        # this step only (UPDATE)
"""
import logging
from datetime import datetime

from ris_kpi.config import RisView as cfg
from ris_kpi.ris_view.defect_ids import attach_segment_ids
from ris_kpi.ris_view.defect_report import SOURCE_TABLES, SRC_COLS, build_defect_report, prepare_source
from ris_kpi.common.keys import filter_upcs
from ris_kpi.common.storage import as_text, load_latest, parent_of, require_latest, save_run
from ris_kpi.ris_view.incremental import (INSERT, INTERMEDIATE_KEY, INTERMEDIATE_UPDATE, SILVER_SURVEY, TABLE_SURVEY,
                                          UPDATE, insert_surveys, keep_surveys, update_rows)
from ris_kpi.ris_view.interval_spine import build_interval_spine, flag_interval_spine
from ris_kpi.ris_view.loaders import load_table
from ris_kpi.ris_view.reporting_intervals import load_reporting_intervals

log = logging.getLogger("intermediate_logic")

# id columns of final_intermediate (statuses follow in the same order)
ID_COLUMNS = ["roughness_parent_defect_id", "rutting_parent_defect_id", "cracking_defect_id"]


def run(mode=UPDATE, surveys=None, run_time=None):
    """mode INSERT / UPDATE; surveys: survey keys to calculate (None = all); run_time: time of the run.
    Returns the saved final_intermediate."""
    run_time = run_time or datetime.now()  # one time per run (pipeline.py): run date folder
    run_date = run_time.date()

    # --- 1. Load sources (only the surveys to calculate) ---
    # Roughness / rutting parent and child tables are optional: a missing one is treated as empty and routing
    # falls back to the next source. Cracking uses only the child 10m table (CRACKING_TABLE)
    sources = {key: prepare_source(load_table(cfg.SILVER_INPUT_DIR, name, optional=True, columns=SRC_COLS))
               for key, name in SOURCE_TABLES.items()}
    sources["cracking"] = prepare_source(load_table(cfg.SILVER_INPUT_DIR, cfg.CRACKING_TABLE))
    sources = {key: keep_surveys(df, surveys, SILVER_SURVEY) for key, df in sources.items()}
    chainage = keep_surveys(load_table(cfg.SILVER_INPUT_DIR, "master_chainage"), surveys, TABLE_SURVEY)
    ri = load_reporting_intervals(cfg.SILVER_INPUT_DIR, cfg.REPORTING_INTERVALS_TABLE, cfg.DEFAULT_INTERVAL)
    log.info("master_chainage rows to calculate: %s", f"{len(chainage):,}")

    # Optional UPC filter (KEEP_UPCS empty = all UPCs); every row grain includes upc, so UPCs are independent
    if cfg.KEEP_UPCS:
        log.info("KEEP_UPCS: %s", ", ".join(cfg.KEEP_UPCS))
        sources = {key: filter_upcs(df, cfg.KEEP_UPCS) for key, df in sources.items()}
        chainage = filter_upcs(chainage, cfg.KEEP_UPCS)
        ri = filter_upcs(ri, cfg.KEEP_UPCS)
        log.info("master_chainage rows after KEEP_UPCS: %s", f"{len(chainage):,}")

    # --- 2. Metric per reporting-interval window, flagged against the threshold
    #        (parent / child 100m as-is, else the child measures inputs averaged per window, then the formula) ---
    defect_report = build_defect_report(sources, ri)

    # --- 3. Rows on the smallest reporting interval per upc / zone / phase / cycle -> flags, affected lengths ---
    final = flag_interval_spine(build_interval_spine(chainage, ri, cfg.DEFAULT_INTERVAL), defect_report)

    # --- 4. Roughness / rutting parent and cracking ids + statuses (newest saved silver status, from step 1);
    #        cracking_status: Open if any cracking child in the row is Open, Closed if all are Closed ---
    status_dir = parent_of(require_latest(cfg.SILVER_STATUS_ROOT, cfg.CRACKING_TABLE))
    log.info("Silver status read from %s", status_dir)
    final = attach_segment_ids(final, status_dir, cfg.CRACKING_TABLE, ID_COLUMNS,
                               rollup_status=["cracking_defect_id"])

    # --- 5. Into the newest saved final_intermediate -> run date + latest ---
    fresh = as_text(final)
    base, _ = load_latest(cfg.INTERMEDIATE_ROOT, cfg.INTERMEDIATE_NAME)
    if mode == INSERT:
        out = insert_surveys(base, fresh, TABLE_SURVEY, cfg.INTERMEDIATE_NAME)
    else:
        out = fresh if base is None else update_rows(base, fresh, INTERMEDIATE_KEY, INTERMEDIATE_UPDATE,
                                                     cfg.INTERMEDIATE_NAME)
    save_run(out, cfg.INTERMEDIATE_ROOT, cfg.INTERMEDIATE_NAME, run_date)
    return out
