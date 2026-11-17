"""RIS view step 3 — RIS view (modular version of code_notebooks/RIS View Pipeline_v2 in_use.ipynb).

Reads the newest saved silver status (RisView.SILVER_STATUS_ROOT, step 1) and final_intermediate
(RisView.INTERMEDIATE_ROOT, step 2), the child 10m tables from RisView.SILVER_INPUT_DIR (IRC counts of ravelling /
roughness / rutting) and the DB tables in RisView.DB_TABLES_DIR (master_upc_project: UPC filter and lengths;
ID_DB_TABLES: DB id columns). Builds ris_view_dw in memory, adds the DB id columns (ris_view/db_ids.py) and saves
ris_view_data under RisView.KPI_OUTPUT_ROOT (year=/month=/day=<run date> + latest). ris_view/pipeline.py runs it in
one of two modes (ris_view/incremental.py):

  INSERT (new surveys)  calculated for the new surveys only -> rows added to the newest saved ris_view_data,
                        created_at = updated_at = this run
  UPDATE (all surveys)  calculated for every survey -> only the counts / affected lengths (RIS_VIEW_UPDATE) of the
                        matching rows (RIS_VIEW_KEY) of the newest saved ris_view_data are updated; every other
                        column stays as it is, updated_at = this run on the rows whose values changed. No row is
                        added. No saved table yet -> saved whole.

    ris-kpi ris-view --step view        # this step only (UPDATE)
"""
import logging
from datetime import datetime

from ris_kpi.common.db_tables import load_db_tables
from ris_kpi.common.keys import filter_upcs
from ris_kpi.common.storage import as_text, load_latest, parent_of, require_latest, save_run
from ris_kpi.config import RisView as cfg
from ris_kpi.ris_view.affected_length import attach_defect_lengths, build_total_rows, subtract_closed_from_open
from ris_kpi.ris_view.db_ids import ID_DB_TABLES, build_ris_view_data
from ris_kpi.ris_view.defect_counts import CHILD_COLUMNS, combine_defects, count_defects
from ris_kpi.ris_view.incremental import (INSERT, RIS_VIEW_KEY, RIS_VIEW_UPDATE, SILVER_SURVEY, TABLE_SURVEY, UPDATE,
                                          insert_surveys, keep_surveys, update_rows)
from ris_kpi.ris_view.loaders import load_defect_table, load_intermediate
from ris_kpi.ris_view.output import build_ris_view_dw
from ris_kpi.ris_view.project_master import filter_project_upcs, load_project_master

log = logging.getLogger("ris_view")

DEFECT_TABLES = ["master_pothole", "master_ravelling_parent", "master_roughness_parent", "master_rutting_parent"]
# parent table (silver status) -> child 10m table (SILVER_INPUT_DIR) that gives its IRC counts
CHILD_TABLES = {
    "master_ravelling_parent": "master_ravelling_child_10m",
    "master_roughness_parent": "master_roughness_child_10m",
    "master_rutting_parent": "master_rutting_child_10m",
}


def run(mode=UPDATE, surveys=None, run_time=None):
    """mode INSERT / UPDATE; surveys: survey keys to calculate (None = all); run_time: time of the run.
    Returns the saved ris_view_data."""
    run_time = run_time or datetime.now()  # one time per run (pipeline.py): run date folder, created_at / updated_at
    run_date = run_time.date()

    # --- 1. Load sources (newest saved silver status / final_intermediate; only the surveys to calculate) ---
    status_dir = parent_of(require_latest(cfg.SILVER_STATUS_ROOT, cfg.CRACKING_TABLE))
    intermediate_path = require_latest(cfg.INTERMEDIATE_ROOT, cfg.INTERMEDIATE_NAME)
    log.info("Silver status from %s, final_intermediate from %s", status_dir, intermediate_path)
    names = [cfg.CRACKING_TABLE] + DEFECT_TABLES
    tables = {name: keep_surveys(load_defect_table(status_dir, name), surveys, SILVER_SURVEY) for name in names}
    children = {child: keep_surveys(load_defect_table(cfg.SILVER_INPUT_DIR, child, columns=CHILD_COLUMNS),
                                    surveys, SILVER_SURVEY)
                for child in CHILD_TABLES.values()}
    db_tables = load_db_tables(cfg.DB_TABLES_DIR, ID_DB_TABLES)
    project_info = load_project_master(db_tables["master_upc_project"])
    intermediate = keep_surveys(load_intermediate(intermediate_path), surveys, TABLE_SURVEY)

    # Optional UPC filter (KEEP_UPCS empty = all UPCs); every output row belongs to one UPC
    if cfg.KEEP_UPCS:
        log.info("KEEP_UPCS: %s", ", ".join(cfg.KEEP_UPCS))
        tables = {name: filter_upcs(df, cfg.KEEP_UPCS) for name, df in tables.items()}
        children = {name: filter_upcs(df, cfg.KEEP_UPCS) for name, df in children.items()}
        intermediate = filter_upcs(intermediate, cfg.KEEP_UPCS)

    # --- 2. Keep only UPCs in master_upc_project ---
    tables = filter_project_upcs(tables, project_info, cfg.FILTER_BY_PROJECT_MASTER)
    children = filter_project_upcs(children, project_info, cfg.FILTER_BY_PROJECT_MASTER)

    # --- 3. Defect rows (IRC of ravelling/roughness/rutting from child 10m) + UPC lengths
    #        -> breached-defect counts per grain ---
    children = {parent: children[child] for parent, child in CHILD_TABLES.items()}
    counts = count_defects(combine_defects(tables, children), project_info)

    # --- 4. Affected lengths per defect / pavement / status (source: LENGTH_SOURCE in ris_view/affected_length.py),
    #        'Total' rows from final_intermediate ---
    defect_rows = attach_defect_lengths(counts, tables, intermediate)
    total_rows = build_total_rows(intermediate, defect_rows)

    # --- 5. Cracking: Open affected length minus Closed, then the ris_view_dw table (in memory) ---
    defect_rows = subtract_closed_from_open(defect_rows)
    ris_view_dw = build_ris_view_dw(defect_rows, total_rows)
    log.info("ris_view_dw — %s rows × %s cols", f"{len(ris_view_dw):,}", ris_view_dw.shape[1])

    # --- 6. DB ids -> this run's ris_view_data rows ---
    created = build_ris_view_data(ris_view_dw, db_tables)
    log.info("ris_view_data of this run — %s rows × %s cols", f"{len(created):,}", created.shape[1])

    # --- 7. Into the newest saved ris_view_data -> run date + latest ---
    fresh = as_text(created.assign(created_at=run_time, updated_at=run_time))
    stamp = fresh["updated_at"].iloc[0] if len(fresh) else str(run_time)  # as written in the CSV
    base, _ = load_latest(cfg.KPI_OUTPUT_ROOT, cfg.RIS_VIEW_DATA_NAME)
    if mode == INSERT:
        out = insert_surveys(base, fresh, TABLE_SURVEY, cfg.RIS_VIEW_DATA_NAME)
    else:
        out = fresh if base is None else update_rows(base, fresh, RIS_VIEW_KEY, RIS_VIEW_UPDATE,
                                                     cfg.RIS_VIEW_DATA_NAME, touch=("updated_at", stamp))
    save_run(out, cfg.KPI_OUTPUT_ROOT, cfg.RIS_VIEW_DATA_NAME, run_date)
    return out
