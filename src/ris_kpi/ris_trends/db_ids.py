"""DB id columns for the RIS trends: ris_trends_dw (in memory) -> ris_trends_data (saved).

Lookups come from the DB tables in RisTrends.DB_TABLES_DIR (ID_DB_TABLES, common/db_tables.py):

    UPC_ID            master_upc_project    UPC = upc                                                    -> id
    Survey_Source_ID  master_survey_system  Survey_Source ('NSV') = system_code                          -> id
    Survey_ID         survey_data_map       UPC + phase + cycle + Defect_Detected_On
                                            = upc + phase + cycle + survey_date                          -> survey_schedule_id
    id                random uuid4 per row (new on every run)

Every lookup is de-duplicated on its key (keep first), so the joins never add rows; a row whose id is not found is
kept with an empty id; at the end, rows with an empty UPC_ID / Survey_ID / Survey_Source_ID (REQUIRED_IDS) are
dropped. App schema: zone / Survey_Source / row_rank dropped (phase / cycle kept), Roughness_Average ->
roughness_average_mm_km, Average_Rutting_Depth -> average_rutting_depth_mm, populated_on -> created_at (KPI run
time), updated_at = time of this run.
"""
import logging
import uuid
from datetime import datetime

from ris_kpi.common.db_ids import (SURVEY_KEYS, date_text, key_text, left_join_ids, log_match, survey_lookup,
                                   survey_source_lookup, upc_lookup)

log = logging.getLogger(__name__)

ID_DB_TABLES = ["master_upc_project", "master_survey_system", "survey_data_map"]
ID_COLUMNS = ["Primary_ID", "id", "UPC_ID", "Survey_ID", "Survey_Source_ID", "updated_at"]
# A row with any of these empty is dropped from ris_trends_data
REQUIRED_IDS = ["UPC_ID", "Survey_ID", "Survey_Source_ID"]
# ris_trends_data column names (applied last)
OUTPUT_RENAME = {
    "UPC": "upc", "UPC_ID": "upc_id",
    "Survey_ID": "survey_id", "Survey_Source_ID": "survey_source_id",
    "PCI_Value": "pci_value",
    "Roughness_Average": "roughness_average_mm_km",
    "Average_Rutting_Depth": "average_rutting_depth_mm",
    "Defect_Detected_On": "defect_detected_on",
}

# ris_trends_dw columns not carried into ris_trends_data
DROP_COLUMNS = ["zone", "Survey_Source", "row_rank"]

RENAME_COLUMNS = {
    "Primary_ID": "id",
    "Roughness_Average": "roughness_average_mm_km",
    "Average_Rutting_Depth": "average_rutting_depth_mm",
    "populated_on": "created_at",  # KPI run time; updated_at = time of this run
}

# Output column order (after renames / drops); any other ris_trends_dw column is appended at the end
FINAL_COLUMNS = [
    "id", "UPC", "UPC_ID", "phase", "cycle", "pavement_type", "Survey_ID", "Survey_Source_ID", "Defect_Detected_On",
    "PCI_Value", "roughness_average_mm_km", "average_rutting_depth_mm",
    "roughness_ca_category", "roughness_ca_threshold", "lane_length_roughness_ca",
    "roughness_irc_category", "roughness_irc_threshold", "lane_length_roughness_irc",
    "rutting_ca_category", "rutting_ca_threshold", "lane_length_rutting_ca",
    "rutting_irc_category", "rutting_irc_threshold", "lane_length_rutting_irc",
    "created_at", "updated_at",
]


def build_ris_trends_data(ris_trends_dw, db_tables):
    """ris_trends_dw + UPC_ID / Survey_Source_ID / Survey_ID / id, in the app schema (FINAL_COLUMNS)."""
    existing = [c for c in ID_COLUMNS if c in ris_trends_dw.columns]
    if existing:
        log.warning("ris_trends_dw already has %s — replaced", existing)
    df = ris_trends_dw.drop(columns=existing)
    n_rows = len(df)

    # Normalised join keys (temporary): the output keeps the original values
    df["_upc"] = df["UPC"].astype("string")
    df["_src"] = df["Survey_Source"].astype("string")
    df["_phase"] = key_text(df["phase"])
    df["_cycle"] = key_text(df["cycle"])
    df["_date"] = date_text(df["Defect_Detected_On"])

    # 1. UPC_ID — master_upc_project: UPC = upc
    df = left_join_ids(df, upc_lookup(db_tables["master_upc_project"], "UPC_ID"), ["_upc"], ["upc"], "UPC_ID")
    log_match(df, "UPC_ID", ["UPC"])

    # 2. Survey_Source_ID — master_survey_system: Survey_Source ('NSV') = system_code
    df = left_join_ids(df, survey_source_lookup(db_tables["master_survey_system"], "Survey_Source_ID"),
                       ["_src"], ["system_code"], "Survey_Source_ID")
    log_match(df, "Survey_Source_ID", ["Survey_Source"])

    # 3. Survey_ID — survey_data_map: UPC + phase + cycle + Defect_Detected_On = upc + phase + cycle + survey_date
    df = left_join_ids(df, survey_lookup(db_tables["survey_data_map"], "Survey_ID"),
                       ["_upc", "_phase", "_cycle", "_date"], SURVEY_KEYS, "Survey_ID")
    log_match(df, "Survey_ID", ["UPC", "phase", "cycle", "Defect_Detected_On"])

    # 4. id — unique uuid4 per row
    df["Primary_ID"] = [str(uuid.uuid4()) for _ in range(len(df))]

    df = df.drop(columns=["_upc", "_src", "_phase", "_cycle", "_date"])
    if len(df) != n_rows:
        raise AssertionError("row count changed")

    # 5. App schema: drop / rename columns, updated_at = time of this run
    df = df.drop(columns=[c for c in DROP_COLUMNS if c in df.columns]).rename(columns=RENAME_COLUMNS)
    df["updated_at"] = datetime.now()

    extra = [c for c in df.columns if c not in FINAL_COLUMNS]
    missing = [c for c in FINAL_COLUMNS if c not in df.columns]
    if missing:
        log.warning("Columns missing from ris_trends_dw (left out): %s", missing)
    if extra:
        log.info("Extra ris_trends_dw columns appended at the end: %s", extra)
    df = df[[c for c in FINAL_COLUMNS if c in df.columns] + extra]

    # 6. Rows without one of REQUIRED_IDS are dropped (logged with their keys)
    missing = df[REQUIRED_IDS].isna().any(axis=1)
    if missing.any():
        keys = ["UPC", "phase", "cycle", "Defect_Detected_On"]
        log.warning("Dropped %s / %s rows with an empty %s:\n%s", int(missing.sum()), len(df), " / ".join(REQUIRED_IDS),
                    df.loc[missing, keys + REQUIRED_IDS].drop_duplicates(keys).to_string(index=False))
    return df[~missing].rename(columns=OUTPUT_RENAME).reset_index(drop=True)
