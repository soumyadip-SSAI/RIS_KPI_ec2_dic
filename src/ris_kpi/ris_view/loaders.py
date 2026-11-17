"""Step-specific table loaders for the RIS view (each normalises the data the way its original pipeline did):

  load_silver_table  silver_status   every column as raw text, so the table is written back unchanged
  load_table         intermediate    phase / cycle as '2' (not '2.0'), upc as text; optional tables
  load_defect_table  view            as load_table, plus detected_on as a date; can read only some columns
  load_intermediate  view            final_intermediate (newest saved copy)
"""
import logging

import pandas as pd

from ris_kpi.common.io import find_table_file, local_file, name_of, read_table_file, read_text_csv, suffix_of
from ris_kpi.common.keys import to_date, to_str_id

log = logging.getLogger(__name__)


# --- silver_status ---

def load_silver_table(input_dir, name):
    """Silver table as raw text (CSV) so every existing value is written back unchanged
    (no 100 -> 100.0 or date re-formatting); only the status columns are added."""
    path = find_table_file(input_dir, name)
    if suffix_of(path) == ".parquet":
        with local_file(path) as local:
            df = pd.read_parquet(local)
    else:
        df = read_text_csv(path)
    log.info("Loaded %-30s rows: %s", name_of(path), f"{len(df):,}")
    return df


# --- intermediate ---

def load_table(input_dir, name, optional=False, columns=()):
    """Load <input_dir>/<name>.*; with optional=True a missing file gives an empty table with `columns`."""
    try:
        path = find_table_file(input_dir, name)
    except FileNotFoundError:
        if not optional:
            raise
        log.warning("%s not found in %s — treated as an empty table", name, input_dir)
        return pd.DataFrame({col: pd.Series(dtype="string") for col in columns})
    df = read_table_file(path)
    # The notebook joined on phase/cycle; compare them as strings ('2.0' -> '2') across sources
    for col in ("phase", "cycle"):
        if col in df.columns:
            df[col] = to_str_id(df[col])
    if "upc" in df.columns:
        df["upc"] = df["upc"].astype("string")
    log.info("Loaded %-30s rows: %s", name_of(path), f"{len(df):,}")
    return df


# --- view ---

def _normalize_keys(df):
    # Compare phase/cycle as strings ('2.0' -> '2'), upc as text and detected_on as a date
    # (CSV sources give text dates) across all sources
    for col in ("phase", "cycle"):
        if col in df.columns:
            df[col] = to_str_id(df[col])
    if "upc" in df.columns:
        df["upc"] = df["upc"].astype("string")
    if "detected_on" in df.columns:
        df["detected_on"] = to_date(df["detected_on"])
    return df


def load_defect_table(input_dir, name, columns=None):
    path = find_table_file(input_dir, name)
    df = _normalize_keys(read_table_file(path, columns))
    log.info("Loaded %-35s rows: %s", name_of(path), f"{len(df):,}")
    return df


def load_intermediate(path):
    """final_intermediate (Databricks: <OUTPUT_SCHEMA>.final_intermediate) from the intermediate step: the newest
    saved copy (common/storage.require_latest)."""
    df = _normalize_keys(read_table_file(path))
    df["defect_detected_on"] = to_date(df["defect_detected_on"])
    log.info("final_intermediate loaded — %s rows (from %s)", f"{len(df):,}", path)
    return df
