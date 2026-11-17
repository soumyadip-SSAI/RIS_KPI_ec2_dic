"""All settings for the RIS KPI pipelines, in one place, grouped by pipeline step.

Where the data lives is chosen with the RIS_KPI_STORAGE environment variable:
  s3     (default) read the inputs from S3 and write every output to S3 — the EC2 runs
  local  input_data/ and output_data/ in the repository — development / before-after checks without AWS access

AWS credentials are not set here: boto3 finds them itself (the EC2 instance role, or `aws configure` locally).
"""
import os
from pathlib import Path

STORAGE = (os.getenv("RIS_KPI_STORAGE") or "s3").strip().lower()

if STORAGE == "s3":
    S3_ROOT = "s3://dhms-new-stgae/NSV"
    # Inputs
    # DB tables written by the DB export job: dlm_defect_case, udm_defect_reference (statuses), master_upc_project,
    # master_survey_system, survey_data_map, master_defect_type (UPC filter / lengths, DB id columns) — <table>.csv
    DB_TABLES_DIR = f"{S3_ROOT}/DHMS/DIC_Staging/Latest"
    # Silver tables: master_*.csv, master_chainage.csv, upc_reporting_intervals.csv, dlm_threshold_mapping.csv
    SILVER_INPUT_DIR = f"{S3_ROOT}/Silver/old_Latest/dhms_staging_silver_data"
    # Outputs — all in the gold folder, each table saved as
    #   <GOLD_ROOT>/year=YYYY/month=MM/day=DD/<table>.csv  (run date)  +  <GOLD_ROOT>/Latest/<table>.csv
    # (common/storage.py); the newest saved copy is read back on the next run
    GOLD_ROOT = f"{S3_ROOT}/Gold/DIC_Staging"
    SILVER_STATUS_ROOT = GOLD_ROOT       # silver tables + status columns (golden: silver_data_with_status)
    INTERMEDIATE_ROOT = GOLD_ROOT        # final_intermediate
    KPI_OUTPUT_ROOT = GOLD_ROOT          # ris_view_data, ris_trends_data
    LATEST_FOLDER = "Latest"
elif STORAGE == "local":
    # src/ris_kpi/config.py -> repository root (RIS_KPI_ROOT overrides it, e.g. for a non-editable install)
    ROOT_DIR = Path(os.getenv("RIS_KPI_ROOT") or Path(__file__).resolve().parents[2])
    INPUT_DATA_DIR = ROOT_DIR / "input_data"
    OUTPUT_DATA_DIR = ROOT_DIR / "output_data"
    # Inputs
    # <table>.csv, or the raw_db_tables layout <table>/<YYYY>/<MM>/<DD>/<table>.csv (newest date used)
    DB_TABLES_DIR = INPUT_DATA_DIR / "raw_db_tables"
    SILVER_INPUT_DIR = INPUT_DATA_DIR / "old_silver_data"
    # Outputs (same layout as on S3: <root>/year=YYYY/month=MM/day=DD/ + <root>/latest/)
    SILVER_STATUS_ROOT = OUTPUT_DATA_DIR / "silver_data_with_status"
    INTERMEDIATE_ROOT = OUTPUT_DATA_DIR / "output_kpi_data"
    KPI_OUTPUT_ROOT = OUTPUT_DATA_DIR / "output_kpi_data"
    LATEST_FOLDER = "latest"             # as in RIS_KPI_golden/output_data, so the two compare directly
else:
    raise ValueError(f"RIS_KPI_STORAGE must be 's3' or 'local', not {STORAGE!r}")


class RisView:
    """RIS view (ris_kpi.ris_view): silver status -> intermediate -> ris_view_dw (in memory) + DB ids
    -> ris_view_data. ris_view/pipeline.py picks the scenario from the surveys in latest/ris_view_data.csv:
    scenario 1 (some silver surveys are new): INSERT for the new surveys, then UPDATE for all;
    scenario 2 (no new survey): UPDATE for all (ris_view/incremental.py)."""
    # UPCs in '/' form (e.g. ["N/02015/03002/GJ", "N/07024/01001/GJ"]). Empty = all UPCs.
    KEEP_UPCS = []

    # --- Input ---
    SILVER_INPUT_DIR = SILVER_INPUT_DIR
    # DB tables, read from DB_TABLES_DIR on every run (kept up to date there by the DB export job):
    # STATUS_TABLES (silver-status step, UPDATE only) and master_upc_project / master_survey_system /
    # survey_data_map / master_defect_type (view step: UPC filter, UPC lengths, DB id columns)
    DB_TABLES_DIR = DB_TABLES_DIR
    STATUS_TABLES = ["dlm_defect_case", "udm_defect_reference"]
    # True = keep only UPCs present in master_upc_project | False = no UPC filter (view step).
    # UPC Length(in km) / UPC Lane Length(in km) always come from master_upc_project (ris_view/project_master.py).
    FILTER_BY_PROJECT_MASTER = True

    # --- Sources ---
    # Reporting intervals: file name without extension, read from SILVER_INPUT_DIR as .csv / .parquet / .xlsx
    # (replaces the Databricks table nhai_dhms.ris_kpi_output.upc_reporting_intervals)
    REPORTING_INTERVALS_TABLE = "upc_reporting_intervals"
    # Interval (m) used where a reporting interval is empty or a standard (CA/IRC) has no row
    DEFAULT_INTERVAL = 100
    # Cracking source (file name without extension). The notebooks used the 10m table master_cracking.
    CRACKING_TABLE = "master_cracking"

    # --- Output (file names without .csv) ---
    # Every output table is saved twice (common/storage.py):
    #   <root>/year=YYYY/month=MM/day=DD/<name>.csv  one folder per run date (a second run on the same day overwrites)
    #   <root>/latest/<name>.csv                     the newest run, overwritten by every run
    # Each step reads the newest dated copy of its table, inserts / updates rows (ris_view/incremental.py) and saves
    # it under the run date.
    # Silver tables with defect_reference_id / current_status / final_status (input of the next two steps)
    SILVER_STATUS_ROOT = SILVER_STATUS_ROOT
    # Intermediate step output, input of the view step (Databricks: <OUTPUT_SCHEMA>.final_intermediate)
    INTERMEDIATE_ROOT = INTERMEDIATE_ROOT
    INTERMEDIATE_NAME = "final_intermediate"
    # ris_view_dw (built in memory, not saved) + DB id columns (ris_view/db_ids.py) -> ris_view_data.
    # The surveys (upc + phase + cycle + detected_on) in KPI_OUTPUT_ROOT/latest/ris_view_data.csv are existing; any
    # other silver survey is new. Delete the saved copies (dated folders + latest) to rebuild it from scratch.
    KPI_OUTPUT_ROOT = KPI_OUTPUT_ROOT
    RIS_VIEW_DATA_NAME = "ris_view_data"


class RisTrends:
    """RIS trends (ris_kpi.ris_trends): ris_trends_dw (in memory) + DB ids -> ris_trends_data, new surveys only.
    Does not use the status tables."""
    # UPCs in '/' form. Empty = all UPCs.
    KEEP_UPCS = []
    # "Cement concrete" is read as "Cement Concrete" before filtering (PAVEMENT_TYPE_ALIASES in ris_trends/filters.py)
    KEEP_PAVEMENT_TYPES = ["Asphalt", "Cement Concrete", "Hybrid"]
    # True = Roughness_Average / Average_Rutting_Depth only from breach_flag == 1 rows
    USE_BREACH_FLAG = False

    # --- Input ---
    SILVER_INPUT_DIR = SILVER_INPUT_DIR
    # Threshold mapping (Databricks: dlm_threshold_mapping_display): file name without extension, read from
    # SILVER_INPUT_DIR as .parquet / .csv / .xlsx. Required.
    THRESHOLD_MAPPING_TABLE = "dlm_threshold_mapping"
    # True = keep only UPCs present in master_upc_project | False = no UPC filter
    FILTER_BY_PROJECT_MASTER = True
    # DB tables: master_upc_project (UPC filter + UPC_ID), master_survey_system / survey_data_map (DB id columns)
    DB_TABLES_DIR = DB_TABLES_DIR

    # --- Output ---
    # ris_trends_dw (built in memory, not saved) + DB id columns (ris_trends/db_ids.py) -> ris_trends_data, saved as
    # OUTPUT_ROOT/year=YYYY/month=MM/day=DD/ris_trends_data.csv (run date) + OUTPUT_ROOT/latest/ (common/storage.py).
    # Also an input: only silver surveys (upc + phase + cycle + detected_on) not in the newest saved copy are
    # calculated and appended (ris_trends/append.py). Delete the saved copies (dated folders + latest) to rebuild it.
    OUTPUT_ROOT = KPI_OUTPUT_ROOT
    RIS_TRENDS_OUTPUT_NAME = "ris_trends_data"
