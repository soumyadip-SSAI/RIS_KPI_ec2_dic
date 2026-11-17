"""DB id columns of ris_view_data / ris_trends_data, and the UPC lengths from master_upc_project."""
import uuid
from datetime import datetime

import pandas as pd

from ris_kpi.ris_trends.db_ids import build_ris_trends_data
from ris_kpi.ris_view.db_ids import build_ris_view_data, defect_type_keys
from ris_kpi.ris_view.project_master import load_project_master

DB = {
    "master_upc_project": pd.DataFrame({"id": ["11", "12"], "upc": ["N/1", "N/2"],
                                        "length_km": ["10.5", "x"], "lane_length_km": ["42", None]}),
    "master_survey_system": pd.DataFrame({"id": ["9"], "system_code": ["NSV"]}),
    "survey_data_map": pd.DataFrame({"survey_schedule_id": ["s1", "s1-dup"], "upc": ["N/1", "N/1"],
                                     "phase": ["1", "1"], "cycle": ["2.0", "2"], "survey_date": ["2026-01-05"] * 2}),
    "master_defect_type": pd.DataFrame({
        "id": ["d-pot", "d-crack-rigid", "d-rut-flex"],
        "survey_system": ["NSV"] * 3,
        "asset_type": ["Flexible Pavement", "Rigid Pavements", "Flexible Pavement"],
        "defect_name": ["Pothole", "Crack on Rigid Pavement", "Rutting"]}),
}


def test_defect_type_keys():
    asset, name = defect_type_keys(pd.Series(["Cracking", "Cracking", "Pothole", "Rutting", "Total"]),
                                   pd.Series(["Cement Concrete", "", None, "", ""]))
    assert asset.tolist()[:4] == ["Rigid Pavements", "Flexible Pavement", "Flexible Pavement", "PAVEMENT"]
    assert name.tolist()[:4] == ["Crack on Rigid Pavement", "Cracking", "Pothole", "Rutting"]
    assert pd.isna(asset.iloc[4]) and pd.isna(name.iloc[4])


def view_dw(*rows):
    return pd.DataFrame([{"Source_System": "NSV", "UPC": u, "Phase": 1, "Cycle": 2, "Defect_Detected_On": "2026-01-05",
                          "Defect_Type": d, "Pavement_Type": p, "Status": "open", "Zone": "Z1",
                          "Total_Defects_CA": 1, "populated_on": datetime(2026, 11, 17)} for u, d, p in rows])


def test_build_ris_view_data_ids_fallback_and_dropped_rows():
    out = build_ris_view_data(view_dw(("N/1", "Pothole", None), ("N/1", "Cracking", "Cement Concrete"),
                                      ("N/1", "Rutting", None), ("N/1", "Total", None),
                                      ("N/2", "Pothole", None)), DB)
    # N/2 has no survey_data_map row -> dropped (REQUIRED_IDS)
    assert out["upc"].tolist() == ["N/1"] * 4
    assert out["upc_id"].unique().tolist() == ["11"]
    assert out["survey_source_id"].unique().tolist() == ["9"]
    assert out["survey_id"].unique().tolist() == ["s1"]                    # duplicate key: first kept
    assert out["defect_type_id"].tolist()[:3] == ["d-pot", "d-crack-rigid", "d-rut-flex"]  # Rutting via fallback
    assert pd.isna(out["defect_type_id"].iloc[3])                          # Total: no defect type id
    assert out["id"].is_unique
    assert list(out.columns[:4]) == ["id", "survey_source_id", "upc", "upc_id"]  # each id after its source column
    assert "Source_System" not in out.columns and "Zone" not in out.columns
    assert {"created_at", "updated_at", "pavement_type", "defect_detected_on"} <= set(out.columns)


def test_ris_view_data_id_is_a_new_uuid_per_row():
    rows = view_dw(("N/1", "Pothole", None))
    first, second = build_ris_view_data(rows, DB)["id"].item(), build_ris_view_data(rows, DB)["id"].item()
    assert str(uuid.UUID(first)) == first and first != second              # random uuid4, as ris_trends_data


def test_build_ris_trends_data():
    dw = pd.DataFrame({"UPC": ["N/1", "N/2"], "phase": ["1", "1"], "cycle": ["2", "2"], "zone": ["Z", "Z"],
                       "pavement_type": ["Asphalt"] * 2, "Survey_Source": ["NSV"] * 2,
                       "Defect_Detected_On": ["2026-01-05"] * 2, "PCI_Value": [3.1, 2.0],
                       "Roughness_Average": [1.0, 2.0], "Average_Rutting_Depth": [4.0, 5.0], "row_rank": [1, 1],
                       "populated_on": [datetime(2026, 11, 17)] * 2})
    out = build_ris_trends_data(dw, DB)
    assert out["upc"].tolist() == ["N/1"]                                  # N/2: no survey id -> dropped
    assert out[["upc_id", "survey_id", "survey_source_id"]].iloc[0].tolist() == ["11", "s1", "9"]
    assert list(out.columns[:4]) == ["id", "upc", "upc_id", "phase"]
    assert {"roughness_average_mm_km", "average_rutting_depth_mm", "created_at", "updated_at"} <= set(out.columns)
    assert not {"zone", "Survey_Source", "row_rank"} & set(out.columns)


def test_upc_lengths_from_master_upc_project():
    info = load_project_master(DB["master_upc_project"]).set_index("upc")
    assert info.loc["N/1", "UPC Length(in km)"] == 10.5
    assert info.loc["N/1", "UPC Lane Length(in km)"] == 42
    assert pd.isna(info.loc["N/2", "UPC Length(in km)"])                  # invalid -> NaN
