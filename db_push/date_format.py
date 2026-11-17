"""Date columns -> yyyy-mm-dd before the push (e.g. a CSV saved by Excel: 25-01-2025 -> 2025-01-25).

Dates are read DAY FIRST (25-01-2025, 25/01/2025, 05-01-2025 = 5 January). A time part is dropped
(2025-01-25 00:00:00 -> 2025-01-25). A value that is not recognised is left as it is, so the dry run reports it
(the DB rejects it).
"""
from datetime import datetime

DATE_COLUMNS = ["defect_detected_on"]     # converted in both tables when present

FORMATS = [
    "%Y-%m-%d", "%Y/%m/%d",               # 2025-01-25, 2025/01/25
    "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y",   # 25-01-2025, 25/01/2025, 25.01.2025
    "%d-%b-%Y", "%d %b %Y", "%d-%B-%Y",   # 25-Jan-2025, 25 Jan 2025, 25-January-2025
    "%d-%m-%y", "%d/%m/%y",               # 25-01-25, 25/01/25
]


def to_yyyy_mm_dd(value):
    """yyyy-mm-dd text of a date value; None stays None; an unrecognised value is returned unchanged."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    # whole value first (25 Jan 2025), then without a time part (2025-01-25 00:00:00, 2025-01-25T00:00:00)
    for candidate in dict.fromkeys([text, text.split()[0], text.split("T")[0]]):
        for fmt in FORMATS:
            try:
                return datetime.strptime(candidate, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return value


def fix_date_columns(df, columns=DATE_COLUMNS):
    """df with every column of `columns` it has converted to yyyy-mm-dd; prints what changed."""
    df = df.copy()
    for col in columns:
        if col not in df.columns:
            continue
        before = df[col]
        after = before.map(to_yyyy_mm_dd)
        changed = before.notna() & (before != after)
        bad = sorted({v for v in after if isinstance(v, str) and not _is_iso(v)})
        df[col] = after
        if changed.any():
            examples = before[changed].drop_duplicates().head(3)
            print(f"{col}: {int(changed.sum()):,} values changed to yyyy-mm-dd, e.g. "
                  + ", ".join(f"'{b}' -> '{to_yyyy_mm_dd(b)}'" for b in examples))
        else:
            print(f"{col}: already yyyy-mm-dd")
        if bad:
            print(f"!! {col}: {len(bad)} value(s) not recognised as a date (left as they are): {bad[:10]}")
    return df


def _is_iso(text):
    try:
        datetime.strptime(text, "%Y-%m-%d")
        return True
    except ValueError:
        return False
