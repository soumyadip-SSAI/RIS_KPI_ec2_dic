"""Loading the silver tables and reference files for the RIS trends."""
import logging

import pandas as pd

from ris_kpi.common.io import find_table_file, name_of, read_table_file
from ris_kpi.common.keys import to_str_id

log = logging.getLogger(__name__)

SILVER_TABLES = [
    "master_roughness_parent", "master_roughness_child_10m",
    "master_rutting_parent", "master_rutting_child_10m",
    "master_chainage",
]

# Tables that may be missing from INPUT_DIR (loaded as empty) and the columns the code reads from them
OPTIONAL_TABLES = {"master_roughness_child_10m", "master_rutting_child_10m"}
OPTIONAL_TABLE_COLUMNS = ["upc", "source_system", "detected_on", "phase", "cycle", "zone", "pavement_type",
                          "threshold_name", "metric_value", "start_chainage", "end_chainage"]


def load_silver_tables(input_dir, table_names=SILVER_TABLES):
    tables = {}
    for name in table_names:
        try:
            path = find_table_file(input_dir, name)
        except FileNotFoundError:
            if name not in OPTIONAL_TABLES:
                raise
            # old_silver_data has no child 10m tables: IRC categories stay empty
            log.warning("%s not found in %s — treated as an empty table (no IRC categories)", name, input_dir)
            tables[name] = pd.DataFrame({c: pd.Series(dtype="string") for c in OPTIONAL_TABLE_COLUMNS})
            continue
        df = read_table_file(path)
        # The notebook compared phase/cycle as strings ('2.0' -> '2') before every join
        for col in ("phase", "cycle"):
            if col in df.columns:
                df[col] = to_str_id(df[col])
        tables[name] = df
        log.info("Loaded %-30s rows: %s", name, f"{len(df):,}")
    return tables


def load_contract_upcs(master_upc_project, enabled=True):
    """Set of UPCs in the master_upc_project table, or None when the filter is switched off."""
    if not enabled:
        log.warning("RisTrends.FILTER_BY_PROJECT_MASTER = False (ris_kpi/config.py): UPCs are NOT filtered against "
                    "master_upc_project")
        return None
    upc = master_upc_project["upc"]
    upcs = set(upc.dropna().astype(str).str.strip())
    log.info("master_upc_project — %s rows | unique UPCs: %s", f"{len(upc):,}", f"{len(upcs):,}")
    return upcs


def load_threshold_mapping(input_dir, table_name):
    """Threshold mapping (nhai_dhms.dlm_kpi.dlm_threshold_mapping_display) from the silver folder."""
    try:
        path = find_table_file(input_dir, table_name)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Threshold mapping '{table_name}' (.parquet/.csv/.xlsx) not found in {input_dir}. "
            "Add it there or change RisTrends.THRESHOLD_MAPPING_TABLE in ris_kpi/config.py."
        ) from None
    df = read_table_file(path)
    log.info("Threshold mapping loaded — %s rows (from %s)", f"{len(df):,}", name_of(path))
    return df
