"""RIS view step 1 — silver status update (modular version of code_notebooks/silver_status_update.ipynb).

Adds defect_reference_id / current_status / final_status to the silver defect tables in RisView.SILVER_INPUT_DIR and
saves them under RisView.SILVER_STATUS_ROOT (year=/month=/day=<run date> + latest, common/storage.py).
ris_view/pipeline.py runs it in one of two modes (ris_view/incremental.py):

  INSERT (new surveys)  silver rows of the new surveys, final_status 'Open', no DB lookup -> added to the newest saved
                        silver status table
  UPDATE (all surveys)  every silver row, status from udm_defect_reference / dlm_defect_case (RisView.DB_TABLES_DIR,
                        written there by the DB export job) -> rows with a DB case update their status in the newest
                        saved table; the others stay as they are. No saved table yet -> the whole table is saved.

    ris-kpi ris-view --step silver-status        # this step only (UPDATE)
"""
import logging
from datetime import datetime

from ris_kpi.common.db_tables import load_db_tables
from ris_kpi.common.keys import filter_upcs
from ris_kpi.common.storage import load_latest, save_run
from ris_kpi.config import RisView as cfg
from ris_kpi.ris_view.incremental import (INSERT, SILVER_SURVEY, UPDATE, insert_surveys, keep_surveys,
                                          update_silver_status)
from ris_kpi.ris_view.loaders import load_silver_table
from ris_kpi.ris_view.status import (SILVER_KEYS, STATUS_TABLES, add_open_status, add_status, case_status,
                                     log_closed_case_check)

log = logging.getLogger("silver_table_update")


def run(mode=UPDATE, surveys=None, run_time=None):
    """mode INSERT / UPDATE; surveys: survey keys to calculate (None = all); run_time: time of the run.
    Returns {table: saved table}."""
    run_time = run_time or datetime.now()  # one time per run (pipeline.py): run date folder, created_at / updated_at
    run_date = run_time.date()
    if not cfg.KEEP_UPCS:
        log.info("KEEP_UPCS empty — all UPCs kept")

    # --- 1. Status tables (DB export), UPDATE only ---
    if mode == UPDATE:
        db = load_db_tables(cfg.DB_TABLES_DIR, cfg.STATUS_TABLES)
        udm_defect_reference, dlm_defect_case = db["udm_defect_reference"], db["dlm_defect_case"]
        cases = case_status(dlm_defect_case)

    # --- 2. Silver tables + status columns, into the newest saved table -> run date + latest ---
    outputs = {}
    for table, defect_collection in STATUS_TABLES.items():
        silver = keep_surveys(filter_upcs(load_silver_table(cfg.SILVER_INPUT_DIR, table), cfg.KEEP_UPCS),
                              surveys, SILVER_SURVEY)
        base, _ = load_latest(cfg.SILVER_STATUS_ROOT, table, empty_as_null=False)
        if mode == INSERT:
            fresh = add_open_status(silver, defect_collection)
            out = insert_surveys(base, fresh, SILVER_SURVEY, table)
        else:
            fresh = add_status(silver, defect_collection, udm_defect_reference, cases,
                               silver_key=SILVER_KEYS.get(table, "id"))
            out = fresh if base is None else update_silver_status(base, fresh, table)
        save_run(out, cfg.SILVER_STATUS_ROOT, table, run_date)
        outputs[table] = out

    # --- 3. Check: CLOSED / CANCELLED cases found in udm_defect_reference ---
    if mode == UPDATE:
        log_closed_case_check(udm_defect_reference, dlm_defect_case)
    return outputs
