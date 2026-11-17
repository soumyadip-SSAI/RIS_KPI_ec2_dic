"""Before/after check for a code change: every CSV under two output folders must match cell for cell.

    ris-kpi compare output_data_before output_data

Cells are compared as the text written to the CSV, so any change in a value, its formatting, a column or the row
order is reported. Columns that change on every run are left out by default (DEFAULT_IGNORE): the run times
populated_on / created_at / updated_at, and id (a random uuid in ris_trends_data and ris_view_data). Run both versions with the same input_data, the same starting output_data (e.g. none:
ris_view_data / ris_trends_data are read back and only updated) and the same pandas version, otherwise differences
that are not caused by the code can show up.
"""
import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

MAX_REPORTED_ROWS = 5
DEFAULT_IGNORE = ("populated_on", "created_at", "updated_at", "id")


def _read(path, ignore_columns):
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_filter=False, encoding="utf-8-sig")
    return df.drop(columns=[c for c in ignore_columns if c in df.columns])


def compare_frames(old, new, sort_rows=False):
    """List of differences between two text DataFrames (empty = identical)."""
    problems = []
    if list(old.columns) != list(new.columns):
        missing = [c for c in old.columns if c not in new.columns]
        extra = [c for c in new.columns if c not in old.columns]
        if missing or extra:
            problems.append(f"columns differ: missing {missing}, extra {extra}")
            return problems
        problems.append("column order differs")
        new = new[old.columns]
    if len(old) != len(new):
        problems.append(f"row count {len(old):,} -> {len(new):,}")
        return problems
    if sort_rows:
        old = old.sort_values(list(old.columns)).reset_index(drop=True)
        new = new.sort_values(list(new.columns)).reset_index(drop=True)
    diff = old.ne(new)
    rows = diff.any(axis=1)
    if rows.any():
        problems.append(f"{int(rows.sum()):,} rows differ in columns {list(diff.columns[diff.any()])}")
        for i in rows[rows].index[:MAX_REPORTED_ROWS]:
            cols = list(diff.columns[diff.loc[i]])
            problems.append(f"  row {i}: " + "; ".join(f"{c}: {old.at[i, c]!r} -> {new.at[i, c]!r}" for c in cols))
    return problems


def compare_dirs(old_dir, new_dir, ignore_columns=DEFAULT_IGNORE, sort_rows=False):
    """True when both folders hold the same CSV files with the same content."""
    old_dir, new_dir = Path(old_dir), Path(new_dir)
    old_files = {p.relative_to(old_dir) for p in old_dir.rglob("*.csv")}
    new_files = {p.relative_to(new_dir) for p in new_dir.rglob("*.csv")}
    if not old_files:
        raise FileNotFoundError(f"No CSV files under {old_dir}")

    ok = True
    for rel in sorted(old_files ^ new_files):
        log.error("%s: only in %s", rel, old_dir if rel in old_files else new_dir)
        ok = False
    for rel in sorted(old_files & new_files):
        problems = compare_frames(_read(old_dir / rel, ignore_columns), _read(new_dir / rel, ignore_columns),
                                  sort_rows)
        if problems:
            ok = False
            log.error("%s: DIFFERENT", rel)
            for p in problems:
                log.error("  %s", p)
        else:
            log.info("%s: identical", rel)
    log.info("Result: %s", "outputs identical" if ok else "OUTPUTS DIFFER")
    return ok
