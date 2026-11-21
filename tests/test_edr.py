"""Offline tests for EDR file editing and the feed pre-run check.

tests/data/edr_tasc_min.edr.txt is a trimmed copy of the structure of a V15
Shell&Tube (.edr) file: input/results scalars, a plain array and a per-stream array.
"""

from pathlib import Path

import pytest

from aspen_mcp.tools import edr
from aspen_mcp.tools.simulation import missing_state_vars

DATA = Path(__file__).parent / "data" / "edr_tasc_min.edr.txt"


def _text():
    return DATA.read_bytes().decode("utf-8")


def test_parse_and_summary():
    p = edr.parse_edr(_text())
    assert p["application"] == "Tasc"
    assert p["input"]["TubeLng"]["value"] == 47.2441 and p["input"]["TubeLng"]["uom"] == "in"
    assert p["results"]["TubeLng"]["value"] == 47.2440948          # same name in results
    assert p["input_arrays"]["NozActualIDSS"]["values"] == [0.622, 0.622]
    assert p["input_arrays"]["Comp01LiqMoleFrc"]["values"]["1"][:3] == [None, None, 1]
    s = edr.edr_summary(p)
    assert s["results"]["RV2Violation"]["meaning"] == "violation"
    assert s["results"]["VibIndicationTasc"]["meaning"] == "possible vibration"
    assert s["geometry"]["TEMAType"]["value"] == "BEM"
    assert s["nozzle_id_in_out"]["NozActualIDSS"] == [0.622, 0.622]


def test_set_inputs_edits_only_input_section():
    new, notes = edr.set_edr_inputs(_text(), {"TubeLng": 53.63},
                                    {"NozActualIDSS": {0: 1.049}, "NozActualODSS": {"0": 1.315}})
    assert new.startswith("﻿<?xml")
    p = edr.parse_edr(new)
    assert p["input"]["TubeLng"]["value"] == 53.63 and p["input"]["TubeLng"]["source"] == "User"
    assert p["results"]["TubeLng"]["value"] == 47.2440948            # results untouched
    assert p["input_arrays"]["NozActualIDSS"]["values"] == [1.049, 0.622]
    assert p["input_arrays"]["NozActualODSS"]["values"] == [1.315, 0.84]
    assert any("TubeLng: 47.2441 -> 53.63 in" in n for n in notes)


def test_set_inputs_rejects_unknown_and_per_stream():
    with pytest.raises(edr.EdrError, match="not an input scalar"):
        edr.set_edr_inputs(_text(), {"AreaRatioDirty": 2})              # a result, not an input
    with pytest.raises(edr.EdrError, match="per-stream"):
        edr.set_edr_inputs(_text(), arrays={"Comp01LiqMoleFrc": {0: 1}})


def test_find_edr_file(tmp_path):
    work = tmp_path / "aspen_mcp_work" / "PM__work_1"
    work.mkdir(parents=True)
    f = work / "IC-403_1.EDR"
    f.write_text("x")
    assert edr.find_edr_file("IC-403_1.EDR", "IC-403", [str(work.parent)]) == str(f)
    assert edr.find_edr_file(None, "IC-999", [str(tmp_path)]) is None
    assert edr._inside(str(f), str(tmp_path / "aspen_mcp_work"))


@pytest.mark.parametrize("t,p,v,missing", [
    (25, 1, None, []), (125, None, 1.0, []), (None, 2.3, 0.0, []),
    (25, None, None, ["PRES"]), (None, None, None, ["TEMP", "PRES"]),
    (None, None, 1.0, ["TEMP or PRES (VFRAC is set)"]),
])
def test_feed_state_spec(t, p, v, missing):
    """Steam feeds given as T + vapour fraction were wrongly refused (live V15 run)."""
    assert missing_state_vars(t, p, v) == missing


def test_edr_tools_registered():
    from aspen_mcp import safety
    assert "set_edr_inputs" in safety.WRITE_TOOLS


def test_air_cooled_summary_uses_acol_names():
    """Live V15: the air-cooled IC-406 (application "Acol") returned no results."""
    text = _text().replace('application name="Tasc"', 'application name="Acol"')
    s = edr.edr_summary(edr.parse_edr(text))
    assert s["results"]["AreaRatioDirty"]["value"] == pytest.approx(9.769727)
    text = _text().replace('application name="Tasc"', 'application name="Aple"')
    assert "AreaRatioDirty" in edr.edr_summary(edr.parse_edr(text))["results"]


def test_vector_nodes_report_children_not_counts():
    """Live V15: per-utility UTL_COST etc. are vectors; Value is the row count."""
    class N:
        def __init__(self, name, value=None, kids=(), unit=None):
            self.Name, self.Value, self._k, self.UnitString = name, value, list(kids), unit
        def Elements(self, i):
            return self._k[i]
    vec = N("UTL_COST", 1, [N("IC-301", 0.064, unit="$/hr"), N("V-401", 0.678, unit="$/hr")])
    assert edr._node_values(vec) == {"IC-301": {"value": 0.064, "unit": "$/hr"},
                                     "V-401": {"value": 0.678, "unit": "$/hr"}}
    assert edr._node_values(N("TOTUTCOST", 0.744, unit="$/hr")) == {"value": 0.744, "unit": "$/hr"}
