"""DB id columns for the RIS view: ris_view_dw (in memory) -> ris_view_data (saved).

Lookups come from the DB tables in RisView.DB_TABLES_DIR (ID_DB_TABLES, common/db_tables.py):

    upc_id            master_upc_project    UPC = upc                                              -> id
    survey_source_id  master_survey_system  Source_System ('NSV') = system_code                    -> id
    survey_id         survey_data_map       UPC + Phase + Cycle + Defect_Detected_On
                                            = upc + phase + cycle + survey_date                     -> survey_schedule_id
    defect_type_id    master_defect_type    Source_System + asset_type + defect_name
                                            = survey_system + asset_type + defect_name             -> id
                      (asset_type / defect_name from Defect_Type + pavement type, see defect_type_keys; when the
                      first key has no id, DEFECT_TYPE_FALLBACK is tried, e.g. Rutting: PAVEMENT -> Flexible Pavement)
    id                random uuid4 per row (as ris_trends_data). Set when a survey's rows are inserted; an UPDATE
                      run keeps the saved id (incremental.update_rows); a survey inserted again gets new ids

Every lookup is de-duplicated on its key (keep first), so the joins never add rows; a row whose id is not found is
kept with an empty id. populated_on -> created_at (KPI run time); updated_at = time of this run.
"""
import logging
import uuid
from datetime import datetime

import numpy as np
import pandas as pd

from ris_kpi.common.db_ids import (SURVEY_KEYS, date_text, dedup, key_text, left_join_ids, log_match,
                                   survey_lookup, survey_source_lookup, upc_lookup)

log = logging.getLogger(__name__)

ID_DB_TABLES = ["master_upc_project", "master_survey_system", "survey_data_map", "master_defect_type"]
ADDED_COLUMNS = ["id", "upc_id", "survey_source_id", "survey_id", "defect_type_id", "updated_at"]
RENAME_COLUMNS = {"populated_on": "created_at"}

# Pavement type column of ris_view_dw (Cracking / Pothole only; empty -> Asphalt)
PAVEMENT_COLUMNS = ["Pavement_Type", "pavement_type"]

# Row grain (checked for duplicate rows, logged): UPC / upc_id / Phase / Cycle / Defect_Detected_On / Defect_Type / defect_type_id / survey_source_id
# + Status (+ the pavement type column, added to the grain when ris_view_dw has one), which split rows in ris_view_dw
ID_GRAIN = ["UPC", "upc_id", "Phase", "Cycle", "Defect_Detected_On", "Defect_Type", "defect_type_id",
            "survey_source_id", "Status"]

# A row with any of these empty is dropped from ris_view_data
REQUIRED_IDS = ["survey_id", "survey_source_id", "upc_id"]
# Not written to ris_view_data (Source_System is used for the joins)
DROP_AFTER_USE = ["Source_System", "Zone"]
# ris_view_data column names
OUTPUT_RENAME = {
    "UPC": "upc", "UPC_ID": "upc_id",
    "Phase": "phase", "Cycle": "cycle",
    "Defect_Detected_On": "defect_detected_on",
    "Defect_Type": "defect_type", "Defect_Type_ID": "defect_type_id",
    "Survey_ID": "survey_id", "Survey_Source_ID": "survey_source_id",
    "Total_Defects_IRC": "total_defects_irc", "Total_Defects_CA": "total_defects_ca",
    "UPC Length(in km)": "upc_length_km",
    "UPC Lane Length(in km)": "upc_lane_length_km",
    "Affected_length_CA(in_m)": "affected_length_ca_m",
    "Affected_Length_IRC(in_m)": "affected_length_irc_m",
    "Affected_length_CA(in_km)": "affected_length_ca_km",
    "Affected_Length_IRC(in_km)": "affected_length_irc_km",
    "Pavement_Type": "pavement_type",
    "Status": "status",
}

# Each id column is placed right after the column it describes
PLACE_AFTER = {
    "Source_System": "survey_source_id",
    "UPC": "upc_id",
    "Defect_Detected_On": "survey_id",
    "Defect_Type": "defect_type_id",
}

DEFECT_TYPE_KEYS = ["survey_system", "asset_type", "defect_name"]
# (asset_type, defect_name) -> key tried next when the first one has no id in master_defect_type for that survey system
DEFECT_TYPE_FALLBACK = {("PAVEMENT", "Rutting"): ("Flexible Pavement", "Rutting")}


def defect_type_lookup(master_defect_type):
    """survey_system + asset_type + defect_name -> id (exact text match)."""
    lk = master_defect_type[DEFECT_TYPE_KEYS + ["id"]].rename(columns={"id": "defect_type_id"})
    return dedup(lk.dropna(subset=DEFECT_TYPE_KEYS), DEFECT_TYPE_KEYS, "master_defect_type")


# --- defect type / row id --------------------------------------------------------------------------------------

def pavement_column(df):
    return next((c for c in PAVEMENT_COLUMNS if c in df.columns), None)


def defect_type_keys(defect_type, pavement_type):
    """Defect_Type + pavement type -> (asset_type, defect_name) in master_defect_type.

    - empty / missing pavement type -> 'Asphalt'
    - Pothole, Cracking : Cement Concrete -> 'Rigid Pavements', else -> 'Flexible Pavement'
                          Cracking on Cement Concrete -> 'Crack on Rigid Pavement', else 'Cracking'
    - Roughness, Ravelling -> 'Pavement (NSV)' ; Rutting -> 'PAVEMENT'
    - anything else (e.g. 'Total') -> no key, defect_type_id stays empty
    """
    pavement = pavement_type.astype("string").replace("", pd.NA).fillna("Asphalt")
    is_point = defect_type.isin(["Pothole", "Cracking"])
    is_concrete = pavement.str.strip().str.lower().isin(["cement concrete", "cememt concrete"])

    asset_type = np.select(
        [is_point & is_concrete, is_point & ~is_concrete,
         defect_type.isin(["Roughness", "Ravelling"]), defect_type == "Rutting"],
        ["Rigid Pavements", "Flexible Pavement", "Pavement (NSV)", "PAVEMENT"],
        default=None,
    )
    defect_name = np.select(
        [(defect_type == "Cracking") & is_concrete, (defect_type == "Cracking") & ~is_concrete,
         defect_type == "Pothole", defect_type == "Roughness", defect_type == "Ravelling", defect_type == "Rutting"],
        ["Crack on Rigid Pavement", "Cracking", "Pothole", "Roughness", "Ravelling", "Rutting"],
        default=None,
    )
    return (pd.Series(asset_type, index=defect_type.index, dtype="string"),
            pd.Series(defect_name, index=defect_type.index, dtype="string"))


def make_row_id(df, grain):
    """Random uuid4 per row (as ris_trends_data). Rows that share the grain (empty values compare equal) are logged,
    never dropped."""
    dups = df[grain].astype("string").fillna("").duplicated(keep=False)
    if dups.any():
        log.warning("id: %s row(s) share the grain %s — not unique:\n%s",
                    int(dups.sum()), grain, df.loc[dups, grain].to_string(index=False))
    else:
        log.info("id: %s rows, grain %s unique", f"{len(df):,}", grain)
    return [str(uuid.uuid4()) for _ in range(len(df))]


def _defect_type_fallback(df, type_lk):
    """Rows still without defect_type_id whose (asset_type, defect_name) is in DEFECT_TYPE_FALLBACK: try the fallback
    key (same survey system); the key that matched is kept in _asset_type / _defect_name."""
    for (asset, name), (fb_asset, fb_name) in DEFECT_TYPE_FALLBACK.items():
        rows = df["defect_type_id"].isna() & (df["_asset_type"] == asset) & (df["_defect_name"] == name)
        if not rows.any():
            continue
        keys = pd.DataFrame({"survey_system": df.loc[rows, "_src"], "asset_type": fb_asset, "defect_name": fb_name})
        found = keys.merge(type_lk, on=DEFECT_TYPE_KEYS, how="left", validate="many_to_one")["defect_type_id"]
        found.index = keys.index
        hit = found.notna()
        df.loc[hit[hit].index, "defect_type_id"] = found[hit]
        df.loc[hit[hit].index, ["_asset_type", "_defect_name"]] = [fb_asset, fb_name]
        log.info("defect_type_id: %s / %s not found for %s row(s) — %s / %s used for %s of them",
                 asset, name, int(rows.sum()), fb_asset, fb_name, int(hit.sum()))
    return df


# --- ris_view_data ---------------------------------------------------------------------------------------------

def build_ris_view_data(ris_view_dw, db_tables):
    """ris_view_dw + upc_id / survey_source_id / survey_id / defect_type_id / id; populated_on -> created_at,
    updated_at added. All ris_view_dw rows and values are kept, except rows without one of REQUIRED_IDS."""
    existing = [c for c in ADDED_COLUMNS + list(RENAME_COLUMNS.values()) if c in ris_view_dw.columns]
    if existing:
        log.warning("ris_view_dw already has %s — replaced", existing)
    df = ris_view_dw.drop(columns=existing)
    n_rows = len(df)

    # Normalised join keys (temporary): the output keeps the original values
    df["_upc"] = df["UPC"].astype("string")
    df["_src"] = df["Source_System"].astype("string")
    df["_phase"] = key_text(df["Phase"])
    df["_cycle"] = key_text(df["Cycle"])
    df["_date"] = date_text(df["Defect_Detected_On"])

    # 1. upc_id — master_upc_project: UPC = upc
    df = left_join_ids(df, upc_lookup(db_tables["master_upc_project"], "upc_id"), ["_upc"], ["upc"], "upc_id")
    log_match(df, "upc_id", ["UPC"])

    # 2. survey_source_id — master_survey_system: Source_System ('NSV') = system_code
    df = left_join_ids(df, survey_source_lookup(db_tables["master_survey_system"], "survey_source_id"),
                       ["_src"], ["system_code"], "survey_source_id")
    log_match(df, "survey_source_id", ["Source_System"])

    # 3. survey_id — survey_data_map: UPC + Phase + Cycle + Defect_Detected_On
    #    = upc + phase + cycle + survey_date -> survey_schedule_id
    df = left_join_ids(df, survey_lookup(db_tables["survey_data_map"], "survey_id"),
                       ["_upc", "_phase", "_cycle", "_date"], SURVEY_KEYS, "survey_id")
    log_match(df, "survey_id", ["UPC", "Phase", "Cycle", "Defect_Detected_On"])

    # 4. defect_type_id — master_defect_type: Source_System + asset_type + defect_name
    pav_col = pavement_column(df)
    if pav_col:
        log.info("defect_type_id: pavement type taken from '%s' (empty -> Asphalt)", pav_col)
        pavement = df[pav_col]
    else:
        log.info("defect_type_id: no pavement type column in ris_view_dw — all rows treated as Asphalt")
        pavement = pd.Series(pd.NA, index=df.index, dtype="string")
    df["_asset_type"], df["_defect_name"] = defect_type_keys(df["Defect_Type"], pavement)
    type_lk = defect_type_lookup(db_tables["master_defect_type"])
    df = left_join_ids(df, type_lk, ["_src", "_asset_type", "_defect_name"], DEFECT_TYPE_KEYS, "defect_type_id")
    df = _defect_type_fallback(df, type_lk)
    log_match(df, "defect_type_id",
              ["Source_System", "Defect_Type"] + ([pav_col] if pav_col else []) + ["_asset_type", "_defect_name"])

    df = df.drop(columns=["_upc", "_src", "_phase", "_cycle", "_date", "_asset_type", "_defect_name"])

    # 5. Timestamps: created_at = KPI run time (populated_on), updated_at = time of this run
    df = df.rename(columns=RENAME_COLUMNS)
    df["updated_at"] = datetime.now()

    # 6. id — unique uuid4 per row
    df["id"] = make_row_id(df, ID_GRAIN + ([pav_col] if pav_col else []))
    if len(df) != n_rows:
        raise AssertionError("row count changed")

    # Column order: id first, then ris_view_dw order with each id after its source column, updated_at after
    # created_at
    order = ["id"]
    for col in ris_view_dw.columns:
        col = RENAME_COLUMNS.get(col, col)
        if col in existing:
            continue
        order.append(col)
        if col in PLACE_AFTER:
            order.append(PLACE_AFTER[col])
        if col == "created_at":
            order.append("updated_at")
    order += [c for c in df.columns if c not in order]
    df = df[order]

    # 7. Rows without one of REQUIRED_IDS are dropped (logged with their keys)
    missing = df[REQUIRED_IDS].isna().any(axis=1)
    if missing.any():
        log.warning("Dropped %s / %s rows with an empty %s:\n%s", int(missing.sum()), len(df), " / ".join(REQUIRED_IDS),
                    df.loc[missing, ["UPC", "Phase", "Cycle", "Defect_Detected_On"] + REQUIRED_IDS]
                    .drop_duplicates(["UPC", "Phase", "Cycle", "Defect_Detected_On"]).to_string(index=False))
    df = df[~missing]

    # 8. Columns used only for the joins are dropped; output names in snake_case
    return df.drop(columns=[c for c in DROP_AFTER_USE if c in df.columns]).rename(columns=OUTPUT_RENAME) \
        .reset_index(drop=True)
