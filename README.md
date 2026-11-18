# RIS KPI (DIC)

Builds the RIS KPI tables `ris_view_data` and `ris_trends_data` from the silver defect tables and the DHMS database
tables (defect statuses, UPC / survey / defect type masters). Every input is read from S3 and every output is written
to S3; nothing is kept on the machine that runs it.

DIC copy of `ris_kpi_golden_modular`: same code, other S3 folders (`src/ris_kpi/config.py`): silver input
`Silver/old_Latest/dic_staging_silver_data/`, DB tables `DHMS/DIC_Staging/Latest/`, outputs `Gold/DIC_Staging/`.

| Command | Use it when | Writes |
|---|---|---|
| `ris-kpi run` | Full KPI creation (new survey data / silver tables) | everything below |
| `ris-kpi status-update` | Only defect statuses changed in the DB (cases closed, assigned, ...) | silver with status, `final_intermediate`, `ris_view_data` |
| `ris-kpi sync-db-ids` | Manually, after every run: take `id` / `created_at` of rows already in the DB | `Latest/ris_view_data.csv`, `Latest/ris_trends_data.csv` |

`ris-kpi status-update` is the EC2 version of golden's `defect_status_updation` flow: the DB export job has already
refreshed the status tables on S3, so it only re-runs the RIS view (same as `ris-kpi ris-view`).

The tables are **updated, not rebuilt**: each run reads the newest saved copy of every table first and inserts /
updates rows in it (see [New and existing surveys](#new-and-existing-surveys-scenario-1--2)). Every table is saved per
run date and as a `Latest/` copy (see [Output folders](#output-folders)).

## Where the data lives (`s3://dhms-new-stgae/NSV/...`)

| | S3 location | Contents |
|---|---|---|
| in  | `DHMS/DIC_Staging/Latest/` | DB tables written by the separate DB export job: `dlm_defect_case.csv`, `udm_defect_reference.csv` (statuses), `master_upc_project.csv`, `master_survey_system.csv`, `survey_data_map.csv`, `master_defect_type.csv` (UPC filter, UPC lengths, DB id columns) |
| in  | `Silver/old_Latest/dic_staging_silver_data/` | silver tables (`master_*.csv`, `master_chainage.csv`, `upc_reporting_intervals.csv`, `dlm_threshold_mapping.csv`) |
| in + out | `Gold/DIC_Staging/` (`Latest/` and `year=YYYY/month=MM/day=DD/`) | every output table: the 5 silver defect tables + `defect_reference_id`, `current_status`, `final_status` (golden's `silver_data_with_status`), `final_intermediate.csv`, `ris_view_data.csv`, `ris_trends_data.csv` |

The gold folder is also an input: each step reads the newest saved copy of its table and saves the changed table
again. The silver tables with status are saved there too (not in the silver folder), under the same names as the
silver inputs (`master_cracking.csv`, ...); the silver input folder is never written to.

The locations are set at the top of `src/ris_kpi/config.py` (`DB_TABLES_DIR`, `SILVER_INPUT_DIR`, `GOLD_ROOT`,
`LATEST_FOLDER`). AWS access comes from boto3's usual chain: the EC2 instance role, or `aws configure` locally. No
credentials are stored in the repo. The role needs:

- `s3:GetObject` on the inputs **and on `Gold/DIC_Staging/`**, `s3:PutObject` on `Gold/DIC_Staging/`;
- `s3:ListBucket` on `Gold/DIC_Staging/`, to find the newest `year=/month=/day=` copy. Without it the `Latest/` copy is
  used (log line `No s3:ListBucket on ...`), which every run writes together with the dated copy, so it is the same
  table.

> If the role cannot read `Gold/DIC_Staging/`, S3 answers as if nothing was saved: the run then treats every survey as
> new and starts every table from scratch (rows of surveys no longer in the silver tables and the old `created_at`
> values are lost from the new copies; the older dated copies stay on S3). Check the log line
> `Surveys: ... in silver, ... in ris_view_data, ... new` after the first run on a new machine or role.

### Output folders

Every output table (the 5 silver status tables, `final_intermediate`, `ris_view_data`, `ris_trends_data`) is saved
twice (`common/storage.py`):

```
s3://dhms-new-stgae/NSV/Gold/DIC_Staging/
├── year=2026/month=11/day=17/<table>.csv   # one folder per run date (a second run on the same day overwrites it)
└── Latest/<table>.csv                       # copy of the newest run, overwritten by every run
```

A step reads the **newest saved** copy of its table: the latest `year=/month=/day=` folder directly under
`Gold/DIC_Staging/` that has the file (`Latest/` only when there is no dated copy), changes it and saves it under the
current run date + `Latest/`. Other folders inside `Gold/DIC_Staging/` are ignored. A table
a run does not change (e.g. `ris_trends_data` without a new survey) gets no copy in that run's folder. One run time is
used for the whole run (run-date folder, `created_at`, `updated_at`).

## Running on the EC2 instance (SSM console)

Needs Python 3.10 or newer (`python3 --version`); on Ubuntu, `apt install python3-venv` if `venv` is missing.

First time:

```bash
sudo su
cd ~
git clone <repo-url> ris_kpi_dic
cd ris_kpi_dic
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
ris-kpi run
```

Every later run (after a new push):

```bash
sudo su
cd ~/ris_kpi_dic
git pull
source .venv/bin/activate
pip install -e .                 # only needed when pyproject.toml changed; harmless otherwise
ris-kpi run                      # or: ris-kpi status-update   when only statuses changed
```

A full run takes several minutes. If the SSM session may time out, run it in the background with a log file:

```bash
nohup ris-kpi run > run_$(date +%F_%H%M).log 2>&1 &
tail -f run_*.log                # Ctrl+C stops watching, not the run
```

The run ends with a summary (`OK` / `FAILED` / `SKIPPED` per step); exit code 1 means something failed.

## Commands

```bash
ris-kpi run                      # ris_view -> ris_trends
ris-kpi run --keep-going         # still run ris_trends when ris_view fails

ris-kpi status-update            # only defect statuses changed: the RIS view (scenario 2, or 1 when a survey is new)
ris-kpi ris-view                 # the same RIS view run
ris-kpi ris-view --step view     # single steps (silver-status, intermediate, view), UPDATE mode for all surveys
ris-kpi ris-trends

ris-kpi sync-db-ids --dry-run     # after a run: report which rows get the DB id / created_at
ris-kpi sync-db-ids               # copy them and save (see "DB ids of existing rows")
```

`python -m ris_kpi ...` works the same way. The same steps can be called from Python (for example from an Airflow
task):

```python
from ris_kpi.pipelines import run_full
from ris_kpi.ris_view import run_ris_view
```

## Steps

| Step | Reads | Writes |
|---|---|---|
| `ris_view` 1. `silver-status` | silver defect tables, newest saved silver status, `dlm_defect_case` / `udm_defect_reference` (UPDATE only) | `Gold/DIC_Staging/`: the 5 silver defect tables (+ `defect_reference_id`, `current_status`, `final_status`) |
| `ris_view` 2. `intermediate` | silver parent / child tables, `master_chainage`, `upc_reporting_intervals`, newest saved silver status, newest saved `final_intermediate` | `Gold/DIC_Staging/`: `final_intermediate.csv` |
| `ris_view` 3. `view` | newest saved silver status (defect counts, Closed lengths), child 10m tables (IRC counts), newest saved `final_intermediate` (Open lengths, `Total` rows), `master_upc_project` (UPC filter, UPC lengths), `master_survey_system` / `survey_data_map` / `master_defect_type` (ids), newest saved `ris_view_data` | `Gold/DIC_Staging/`: `ris_view_data.csv` |
| `ris_trends` | `master_{roughness,rutting}_{parent,child_10m}`, `master_chainage`, `dlm_threshold_mapping`, `master_upc_project` (UPC filter) / `master_survey_system` / `survey_data_map` (ids), newest saved `ris_trends_data` (surveys already done) | `Gold/DIC_Staging/`: `ris_trends_data.csv` (rows of new surveys appended) |

`ris_trends` does not use the status tables, so after a status-only change `ris-kpi status-update` is enough.
`ris_view_dw` / `ris_trends_dw` are built in memory only; the saved tables are the same rows with the DB id columns
added (see [DB id columns](#db-id-columns)).

Affected lengths in brief: Open comes from `final_intermediate` (roughness, rutting, cracking; ravelling and
pothole 0), Closed from the silver tables (breached rows), and cracking Open = Open - Closed. Cracking and pothole are
split by pavement type (`Cement Concrete` / `Asphalt`; Hybrid, other or empty count as `Asphalt`), since their
defect type id depends on it; the other defect types and `Total` rows have an empty `pavement_type`. An intermediate
row is flagged when at least half of it lies inside breached windows. The detailed rules are in the docstrings of
`ris_view/status.py`, `defect_counts.py` and `affected_length.py`.

## New and existing surveys (scenario 1 / 2)

A survey is one `upc` + `phase` + `cycle` + `defect_detected_on`. The RIS view (`ris_view/pipeline.py`,
`ris_view/incremental.py`) compares the surveys of the silver defect tables with the surveys in
`Gold/DIC_Staging/Latest/ris_view_data.csv`:

**Scenario 1: some silver surveys are new** (not in `ris_view_data`; all of them when there is none).

a) INSERT, for the new surveys only. Each step adds their rows to the newest saved table and saves it under the run
   date; the next step reads that copy, filtered to the new surveys:

   1. silver status: `final_status` = `Open` for every defect, no DB lookup (`defect_reference_id` / `current_status`
      empty)
   2. `final_intermediate` of the new surveys
   3. `ris_view_data` of the new surveys: every defect Open, so the Closed rows are 0; `created_at` = `updated_at` =
      run time

b) then UPDATE for all surveys, as in scenario 2 (the newest saved tables are now the ones written in a).

**Scenario 2: no new survey.** UPDATE for all surveys. Every step is calculated for every survey and only updates
rows of the newest saved table (no row added or removed):

| Table | Row key | Updated columns |
|---|---|---|
| silver status | `id` + `upc` + `phase` + `cycle` + `detected_on` (silver ids restart per survey) | `defect_reference_id`, `current_status`, `final_status`, only on rows with a DB case (`current_status` found in `dlm_defect_case`); rows without one keep their saved status |
| `final_intermediate` | `upc` + `source_system` + `defect_detected_on` + `phase` + `cycle` + `zone` + `lane` + `start_chainage` + `end_chainage` + `pavement_type` | the defect ids and `roughness_status` / `rutting_status` / `cracking_status` |
| `ris_view_data` | `survey_id` + `survey_source_id` + `upc_id` + `defect_type` + `pavement_type` + `status` | `total_defects_irc`, `total_defects_ca`, `affected_length_ca_m`, `affected_length_irc_m`, `affected_length_ca_km`, `affected_length_irc_km`; `updated_at` = run time on the rows whose values changed. Every other column (`id`, `created_at`, ids, UPC lengths, ...) stays as saved |

- Rows inserted in a) and updated in b) keep `created_at` = `updated_at` (one run time per run).
- Rows of surveys no longer in the silver tables (or filtered out) stay as saved. A calculated row with no saved row
  is logged and not inserted (update only).
- A survey whose rows are dropped for a missing id (e.g. no `survey_data_map` row yet) never reaches `ris_view_data`,
  so it is new (scenario 1) on every run until the id is found; its silver status / intermediate rows are replaced,
  never doubled.
- No saved table yet (first run) -> scenario 1 for every survey: everything is inserted Open, then updated with the
  DB statuses in b).
- To rebuild everything from scratch, delete the saved copies (dated folders + `Latest/`) of the output tables.

**`ris_trends_data`** (`ris_trends/append.py`) is only ever appended to; its rows are never updated (defect
statuses do not change the trends):

- The newest saved `ris_trends_data` is read first. The silver tables are filtered to the surveys that are **not** in
  it yet, the trends are calculated for those only, their rows are appended with `created_at` = `updated_at` = run
  time, and the table is saved under the run date + `Latest/`. Every trends calculation is per survey, so this gives
  the same rows as a full run.
- Nothing saved yet -> every silver survey is calculated. No new survey (or every new survey's rows are dropped for a
  missing id) -> nothing is written.
- A saved table without `phase` / `cycle` (written by an older version) stops the run: delete it to rebuild it.

## DB id columns

Lookups use the DB tables in `DHMS/DIC_Staging/Latest/`. Each lookup is de-duplicated on its key (keep the first row), so
no row is added; a row whose id is not found is kept with an empty id and its key is logged.

**`ris_view_data`** (`ris_view/db_ids.py`): all `ris_view_dw` columns and values, plus:

| Column | DB table | Join | Value |
|---|---|---|---|
| `upc_id` | `master_upc_project` | `UPC` = `upc` | `id` |
| `survey_source_id` | `master_survey_system` | `Source_System` = `system_code` | `id` |
| `survey_id` | `survey_data_map` | `UPC` + `Phase` + `Cycle` + `Defect_Detected_On` = `upc` + `phase` + `cycle` + `survey_date` | `survey_schedule_id` |
| `defect_type_id` | `master_defect_type` | `Source_System` + asset_type + defect_name = `survey_system` + `asset_type` + `defect_name` | `id` |
| `id` | — | — | random uuid4 per row (as `ris_trends_data`), set when the survey's rows are inserted; UPDATE runs keep it |

asset_type / defect_name: Pothole -> `Flexible Pavement` / `Pothole`; Cracking -> `Flexible Pavement` / `Cracking`
(on Cement Concrete: `Rigid Pavements` / `Pothole` and `Rigid Pavements` / `Crack on Rigid Pavement`); Roughness /
Ravelling -> `Pavement (NSV)`; Rutting -> `PAVEMENT` / `Rutting`, and when that has no id for the survey system,
`Flexible Pavement` / `Rutting` (`DEFECT_TYPE_FALLBACK`). `Total` rows have no defect type id. Rows with an empty
`survey_id`, `survey_source_id` or `upc_id` are **dropped** (logged with their keys); `Source_System` and `Zone` are
dropped and the columns are renamed to snake_case (`UPC Length(in km)` -> `upc_length_km`, ...).
`populated_on` -> `created_at`, `updated_at` added.

**`ris_trends_data`** (`ris_trends/db_ids.py`): `upc_id`, `survey_source_id` (`Survey_Source` = `system_code`) and
`survey_id` as above, `id` = random uuid4 per row. `Roughness_Average` -> `roughness_average_mm_km`,
`Average_Rutting_Depth` -> `average_rutting_depth_mm`, `populated_on` -> `created_at`, `updated_at` added; `zone`,
`Survey_Source`, `row_rank` dropped (`phase` / `cycle` kept). Rows with an empty `upc_id`, `survey_id` or
`survey_source_id` are **dropped**.

UPC lengths and UPC filter (`ris_view/project_master.py`): `UPC Length(in km)` = `master_upc_project.length_km`,
`UPC Lane Length(in km)` = `lane_length_km`. `FILTER_BY_PROJECT_MASTER = True` keeps only UPCs in `master_upc_project`
(ris_view and ris_trends).

## DB ids of existing rows (`ris-kpi sync-db-ids`)

Run by hand after `ris-kpi run`. It reads the newest saved `ris_view_data` / `ris_trends_data` (run_data) and the
DB export `ris_view_kpi` / `ris_trends_kpi` in `DHMS/DIC_Staging/Latest/` (kpi_data). A run_data row whose key matches a
kpi_data row takes that row's `id` and `created_at`, so the DB push UPDATEs the existing DB row instead of adding a
second one. Rows without a match keep their own `id` / `created_at` (new to the DB).

| Table | Key |
|---|---|
| `ris_view_data` | `survey_source_id`, `upc_id`, `defect_detected_on`, `survey_id`, `defect_type_id`, `status` |
| `ris_trends_data` | `upc_id`, `survey_id`, `survey_source_id`, `defect_detected_on`, `pavement_type`, `roughness_irc_category` |

`status` / `roughness_irc_category` make the key unique: ris_view has an open and a close row per defect type,
ris_trends a Good / Fair / Poor row per pavement type. Keys are compared trimmed and case-insensitive, dates as
`yyyy-mm-dd`. A key shared by several rows is left unchanged (logged). It reads and overwrites only
`Gold/DIC_Staging/Latest/ris_view_data.csv` / `ris_trends_data.csv` (same names); the dated copies are not touched.
The next `ris-kpi run` writes `Latest/` again from the newest dated copy, so run this after every `ris-kpi run`
(`tools/db_ids_sync.py`).

## Settings

All in `src/ris_kpi/config.py`: the S3 locations at the top, then one class per step: `RisView` (`KEEP_UPCS`,
`master_upc_project` filter, reporting intervals, output names), `RisTrends` (`KEEP_UPCS`, pavement types,
`USE_BREACH_FLAG`).

## Local runs (development, before / after checks)

Set `RIS_KPI_STORAGE=local` to use folders in the repository instead of S3 (no AWS access needed):

- inputs: `input_data/raw_db_tables/` (DB tables, flat `<table>.csv` or the dated
  `<table>/<YYYY>/<MM>/<DD>/<table>.csv` snapshots of the DB pull, newest used), `input_data/old_silver_data/`
- outputs: `output_data/silver_data_with_status/`, `output_data/output_kpi_data/` (`final_intermediate`,
  `ris_view_data`, `ris_trends_data`), each with the `year=/month=/day=` + `latest/` layout of
  `RIS_KPI_golden/output_data` (lower-case `latest` locally), so the two can be compared directly.

Outputs must not change unless the KPI logic is meant to change. Because the saved tables are read back, start both
runs from the same `output_data` (simplest: none) on the same day. Before merging:

```bash
export RIS_KPI_STORAGE=local                       # PowerShell: $env:RIS_KPI_STORAGE = "local"
rm -rf output_data && ris-kpi run && mv output_data output_data_before    # on the old code
git checkout <your-branch>
ris-kpi run                                                               # same input_data, no output_data
ris-kpi compare output_data_before output_data                            # exit 0 = identical
```

The compare checks every CSV under both folders (dated copies and `latest/`) and leaves out `populated_on`,
`created_at`, `updated_at` (run times) and `id` (a random uuid in `ris_trends_data` and
`ris_view_data`). Unit tests (`pytest`) use an in-memory S3 stand-in, no AWS access.

## Layout

```
ris-kpi/
├── pyproject.toml              # dependencies, `ris-kpi` command, pytest settings
├── src/ris_kpi/
│   ├── cli.py                  # ris-kpi command
│   ├── config.py               # all settings (S3 locations at the top)
│   ├── pipelines.py            # run_full
│   ├── common/                 # io.py (read / write tables on S3 or disk), s3.py (boto3), storage.py (dated /
│   │                           # latest output folders), db_tables.py (DB tables), db_ids.py (id lookups, survey
│   │                           # key), keys.py (UPC / id / date normalisation), logging.py
│   ├── ris_view/
│   │   ├── pipeline.py         # run_ris_view: scenario 1 / 2, runs the three steps below (INSERT / UPDATE)
│   │   ├── incremental.py      # new / existing surveys, INSERT / UPDATE of a saved table
│   │   ├── silver_status.py    # step 1      <- status.py
│   │   ├── intermediate.py     # step 2      <- reporting_intervals.py, defect_report.py, measure_windows.py,
│   │   │                       #                interval_spine.py, spine_flags.py, defect_ids.py
│   │   ├── view.py             # step 3      <- project_master.py, defect_counts.py, affected_length.py, output.py,
│   │   │                       #                db_ids.py
│   │   └── loaders.py          # step-specific table loaders
│   ├── ris_trends/
│   │   ├── pipeline.py         # run_ris_trends
│   │   ├── loaders.py, filters.py, kpi_metrics.py, pci.py, categories.py, output.py
│   │   ├── db_ids.py, append.py
│   │   └── spark.py            # Spark rounding / null semantics
│   └── tools/compare_outputs.py
├── tests/
├── input_data/                 # local runs only, not committed
└── output_data/                # local runs only, not committed
```
