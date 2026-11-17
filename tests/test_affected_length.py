"""Affected-length rules added in the 16-11-2026 logic change."""
import pandas as pd
import pytest

from ris_kpi.ris_view.affected_length import LENGTH_SOURCE, _pick_sources, subtract_closed_from_open
from ris_kpi.ris_view.spine_flags import JOIN_KEYS, covered_metres, majority_covered

KEYS = {k: "x" for k in JOIN_KEYS}


def segments(*bounds):
    return pd.DataFrame([{**KEYS, "_row": i, "_smin": a, "_smax": b} for i, (a, b) in enumerate(bounds)])


def intervals(*bounds):
    return pd.DataFrame([{**KEYS, "_min": a, "_max": b} for a, b in bounds])


def test_majority_covered_docstring_example():
    # interval 254537-254607: 13 of 20 m -> 1, 50 of 50 m -> 1, 7 of 50 m -> 0
    seg = segments((254530, 254550), (254550, 254600), (254600, 254650))
    assert majority_covered(seg, intervals((254537, 254607))).tolist() == [1, 1, 0]


def test_exactly_half_is_flagged_and_zero_is_not():
    seg = segments((0, 10), (10, 20))
    assert majority_covered(seg, intervals((5, 10))).tolist() == [1, 0]


def test_overlapping_intervals_counted_once():
    seg = segments((0, 100))
    assert covered_metres(seg, intervals((0, 30), (20, 40))).tolist() == [40]
    assert majority_covered(seg, intervals((0, 30), (20, 40))).tolist() == [0]
    assert majority_covered(seg, intervals((0, 30), (30, 60))).tolist() == [1]  # adjacent windows joined


def test_intervals_of_other_keys_do_not_count():
    seg = segments((0, 10))
    other = intervals((0, 10)).assign(lane="other")
    assert majority_covered(seg, other).tolist() == [0]


def test_no_intervals():
    assert majority_covered(segments((0, 10)), intervals()).tolist() == [0]


GRAIN = {"upc": "U", "source_system": "S", "detected_on": "d", "phase": "1", "cycle": "1"}


def rows(*items):
    """(defect_type, status, ca_m, irc_m[, pavement_type]) per row; pavement_type defaults to Asphalt for Cracking /
    Pothole and '' for the other defects (pavement_group in defect_counts.py)."""
    out = []
    for d, s, ca, irc, *pav in items:
        pavement = pav[0] if pav else ("Asphalt" if d in ("Cracking", "Pothole") else "")
        out.append({**GRAIN, "defect_type": d, "pavement_type": pavement, "status": s, "affected_length_ca_m": ca,
                    "affected_length_irc_m": irc, "affected_length_ca_km": ca / 1000,
                    "affected_length_irc_km": irc / 1000})
    return pd.DataFrame(out)


def test_cracking_open_minus_closed_other_defects_untouched():
    out = subtract_closed_from_open(rows(("Cracking", "Open", 500, 300), ("Cracking", "Closed", 200, 100),
                                         ("Rutting", "Open", 400, 400), ("Rutting", "Closed", 150, 150)))
    got = out.set_index(["defect_type", "status"])
    assert got.loc[("Cracking", "Open"), ["affected_length_ca_m", "affected_length_irc_m"]].tolist() == [300, 200]
    assert got.loc[("Cracking", "Open"), "affected_length_ca_km"] == pytest.approx(0.3)
    assert got.loc[("Cracking", "Closed"), "affected_length_ca_m"] == 200
    assert got.loc[("Rutting", "Open"), "affected_length_ca_m"] == 400


def test_cracking_open_clipped_at_zero():
    out = subtract_closed_from_open(rows(("Cracking", "Open", 50, 0), ("Cracking", "Closed", 80, 0)))
    assert out.loc[out["status"] == "Open", "affected_length_ca_m"].item() == 0


def test_cracking_open_minus_closed_per_pavement_type():
    out = subtract_closed_from_open(rows(("Cracking", "Open", 500, 0, "Asphalt"),
                                         ("Cracking", "Closed", 200, 0, "Asphalt"),
                                         ("Cracking", "Open", 100, 0, "Cement Concrete"),
                                         ("Cracking", "Closed", 30, 0, "Cement Concrete")))
    got = out.set_index(["pavement_type", "status"])["affected_length_ca_m"]
    assert got[("Asphalt", "Open")] == 300
    assert got[("Cement Concrete", "Open")] == 70


def test_pick_sources_follows_length_source():
    lengths = pd.DataFrame([
        {"defect_type": d, "status": s, "source": src}
        for d in LENGTH_SOURCE for s in ("Open", "Closed") for src in ("intermediate", "silver")])
    kept = _pick_sources(lengths)
    expected = {(d, s, LENGTH_SOURCE[d][s]) for d in LENGTH_SOURCE for s in ("Open", "Closed") if LENGTH_SOURCE[d][s]}
    assert set(map(tuple, kept[["defect_type", "status", "source"]].to_numpy())) == expected


def test_unknown_defect_type_rejected():
    with pytest.raises(ValueError, match="LENGTH_SOURCE"):
        _pick_sources(pd.DataFrame([{"defect_type": "Bleeding", "status": "Open", "source": "silver"}]))
