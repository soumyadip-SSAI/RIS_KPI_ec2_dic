import pandas as pd
import pytest

from ris_kpi.cli import main
from ris_kpi.tools.compare_outputs import compare_dirs


def write(root, rel, df):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


@pytest.fixture
def dirs(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    df = pd.DataFrame({"UPC": ["A", "B"], "Status": ["open", "close"], "populated_on": ["t1", "t1"]})
    for root in (old, new):
        write(root, "output_kpi_data/ris_view_dw.csv", df)
    return old, new, df


def test_identical_ignoring_populated_on(dirs):
    old, new, df = dirs
    write(new, "output_kpi_data/ris_view_dw.csv", df.assign(populated_on="t2"))
    assert compare_dirs(old, new)


def test_value_change_is_reported(dirs):
    old, new, df = dirs
    write(new, "output_kpi_data/ris_view_dw.csv", df.assign(Status=["open", "open"]))
    assert not compare_dirs(old, new)


def test_row_order_only_with_sort_rows(dirs):
    old, new, df = dirs
    write(new, "output_kpi_data/ris_view_dw.csv", df.iloc[::-1])
    assert not compare_dirs(old, new)
    assert compare_dirs(old, new, sort_rows=True)


def test_missing_file_is_reported(dirs):
    old, new, df = dirs
    write(old, "output_kpi_data/ris_trends_dw.csv", df)
    assert not compare_dirs(old, new)


def test_cli_exit_codes(dirs):
    old, new, df = dirs
    with pytest.raises(SystemExit) as ok:
        main(["compare", str(old), str(new)])
    assert ok.value.code == 0
    write(new, "output_kpi_data/ris_view_dw.csv", df.assign(UPC=["A", "X"]))
    with pytest.raises(SystemExit) as bad:
        main(["compare", str(old), str(new)])
    assert bad.value.code == 1
