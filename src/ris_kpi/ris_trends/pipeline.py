"""RIS Trends KPI (modular version of code_notebooks/RIS_Trends_v6 in_use.ipynb). All paths and settings are in
config.RisTrends.

    ris-kpi ris-trends

Only new surveys (ris_trends/append.py): reads the newest saved ris_trends_data (RisTrends.OUTPUT_ROOT,
year=/month=/day= folders, else latest/), keeps the silver tables (RisTrends.SILVER_INPUT_DIR) of the surveys
(upc + phase + cycle + detected_on) not in it yet, builds ris_trends_dw for those in memory, adds the DB id columns
(UPC_ID, Survey_ID, Survey_Source_ID, id; ris_trends/db_ids.py, from the DB tables in RisTrends.DB_TABLES_DIR),
appends the rows (created_at = updated_at = this run) and saves the table under the run date + latest/
(common/storage.py). Existing rows are never changed. Nothing saved yet -> every survey; no new survey -> nothing is
calculated or written.
"""
import logging
import time
from datetime import datetime

from ris_kpi.common.db_tables import load_db_tables
from ris_kpi.common.storage import latest_location, save_run
from ris_kpi.config import RisTrends as cfg
from ris_kpi.ris_trends.append import append_ris_trends_data, keep_new_surveys, load_existing
from ris_kpi.ris_trends.categories import build_category_table, merge_categories
from ris_kpi.ris_trends.db_ids import ID_DB_TABLES, build_ris_trends_data
from ris_kpi.ris_trends.filters import filter_contract_upcs, filter_pavement_types, filter_upcs, log_table_summary
from ris_kpi.ris_trends.kpi_metrics import build_ris_trends
from ris_kpi.ris_trends.loaders import load_contract_upcs, load_silver_tables, load_threshold_mapping
from ris_kpi.ris_trends.output import finalize_ris_trends
from ris_kpi.ris_trends.pci import attach_pci

log = logging.getLogger(__name__)


def run_ris_trends(run_time=None):
    """Returns the location of the saved ris_trends_data (its latest/ copy when nothing was written)."""
    run_time = run_time or datetime.now()
    start = time.time()
    output_path = latest_location(cfg.OUTPUT_ROOT, cfg.RIS_TRENDS_OUTPUT_NAME)

    # --- 1. Load & filter silver tables, keep only the surveys not in ris_trends_data yet ---
    tables = load_silver_tables(cfg.SILVER_INPUT_DIR)
    tables = filter_upcs(tables, cfg.KEEP_UPCS)
    tables = filter_pavement_types(tables, cfg.KEEP_PAVEMENT_TYPES)
    existing = load_existing(cfg.OUTPUT_ROOT, cfg.RIS_TRENDS_OUTPUT_NAME)
    tables, n_new = keep_new_surveys(tables, existing)
    if not n_new:
        log.info("No new survey in the silver tables — nothing written (%s unchanged)", output_path)
        return output_path
    log_table_summary(tables)

    # --- 2. Reference data: DB tables (UPC filter, DB ids), thresholds ---
    db_tables = load_db_tables(cfg.DB_TABLES_DIR, ID_DB_TABLES)
    cm_upcs = load_contract_upcs(db_tables["master_upc_project"], cfg.FILTER_BY_PROJECT_MASTER)
    thresholds = load_threshold_mapping(cfg.SILVER_INPUT_DIR, cfg.THRESHOLD_MAPPING_TABLE)

    # --- 3. Roughness_Average / Average_Rutting_Depth per grain ---
    ris_trends_df = build_ris_trends(
        tables["master_roughness_parent"], tables["master_rutting_parent"], cfg.USE_BREACH_FLAG
    )

    # --- 4. PCI_Value from 1 km chainage buckets ---
    ris_trends_df = attach_pci(
        ris_trends_df,
        tables["master_chainage"],
        [tables["master_roughness_parent"], tables["master_rutting_parent"]],
    )

    # --- 5. master_upc_project UPC filter ---
    ris_trends_df = filter_contract_upcs(ris_trends_df, cm_upcs)

    # --- 6. CA / IRC condition categories (3 rows per grain) ---
    cat_df = build_category_table(tables, thresholds)
    ris_trends_df = merge_categories(ris_trends_df, cat_df, cm_upcs)

    # --- 7. Final ris_trends_dw table (in memory) ---
    ris_trends_dw = finalize_ris_trends(ris_trends_df)
    log.info("ris_trends_dw finalised — %s rows × %s cols", f"{len(ris_trends_dw):,}", ris_trends_dw.shape[1])

    # --- 8. DB ids -> rows of the new surveys ---
    created = build_ris_trends_data(ris_trends_dw, db_tables)
    log.info("ris_trends_data of the new surveys — %s rows × %s cols", f"{len(created):,}", created.shape[1])

    # --- 9. Appended to the saved ris_trends_data (created_at = updated_at = this run) -> run date + latest ---
    if created.empty and existing is not None:
        log.info("No row left for the new surveys — nothing written (%s unchanged)", output_path)
        return output_path
    ris_trends_data = append_ris_trends_data(existing, created, run_time)

    path = save_run(ris_trends_data, cfg.OUTPUT_ROOT, cfg.RIS_TRENDS_OUTPUT_NAME, run_time.date())
    log.info("RIS trends done in %.1fs — %s", time.time() - start, path)
    return path
