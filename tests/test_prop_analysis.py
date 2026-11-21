"""Offline tests for property analysis planning and result tables (layout from V15 probe)."""

import pytest

from aspen_mcp.tools import prop_analysis as pa
from aspen_mcp.tools.flowsheeting import SpecError


class N:
    def __init__(self, name, value=None, kids=()):
        self.Name, self.Value, self._k = name, value, list(kids)

    def Elements(self, i):
        if isinstance(i, int):
            return self._k[i]
        return next(k for k in self._k if k.Name == i)


def rows(vals):
    return [N(str(i + 1), v) for i, v in enumerate(vals)]


def test_plan_txy_write_order_matches_live_probe():
    p = pa.plan_binary("toluene", "mch", pressures=1)
    names = [w[0] for w in p["writes"]]
    assert names == ["ANALTYPE", "COMP1", "COMP2", "CVARNAME", "CCOMP", "VARVALUE",
                     "OPT_CPTINCR", "CLOWER", "CUPPER", "CNPOINT", "PVARVALUE", "PUNITLABEL"]
    assert p["type"] == "BINARY" and p["rows"] == [("PLIST", [1.0])]
    assert dict(p["writes"])["COMP1"] == "TOLUENE"


def test_plan_pxy_and_validation():
    p = pa.plan_binary("WATER", "ETOH", kind="pxy", temperatures=[60, 80], units="C")
    assert ("TVARVALUE", "LIST") in p["writes"] and p["rows"] == [("TLIST", [60.0, 80.0])]
    with pytest.raises(SpecError):
        pa.plan_binary("WATER", "WATER")
    with pytest.raises(SpecError):
        pa.plan_binary("WATER", "ETOH", kind="PXY")
    with pytest.raises(SpecError):
        pa._clean("TOO-LONG-NAME")


def test_plan_pure():
    p = pa.plan_pure("cpig", ["water"], 25, 200, points=8, t_units="C")
    assert ("PUREPROP", "CPIG") in p["writes"] and ("COMPS", ["WATER"]) in p["rows"]
    with pytest.raises(SpecError):
        pa.plan_pure("CP", ["WATER"], 200, 25)


def test_flatten_table_and_vapour_fraction():
    pd = N("PROP_DATA", kids=[
        N("TEMP", kids=[N("TOTAL", kids=rows([110.6, 105.0, 100.9]))]),
        N("MOLEFRAC(C,N)", kids=[N("TOLUENE", kids=rows([0.0, 0.5, 1.0]))]),
        N("KVL", kids=[N("TOTAL", kids=[N("TOLUENE", kids=rows([1.2, 1.1, 1.0])),
                                        N("MCH", kids=rows([1.0, 0.9, 0.8]))])]),
    ])
    t = pa.flatten_table(pd)
    assert t["TEMP TOTAL"] == [110.6, 105.0, 100.9]
    assert t["KVL TOTAL MCH"] == [1.0, 0.9, 0.8]
    pa.add_vapor_fractions(t, ["TOLUENE", "MCH"])
    assert t["y TOLUENE (K*x)"] == pytest.approx([0.0, 0.55, 1.0])
    assert "y MCH (K*x)" not in t          # no x column for MCH


def test_analysis_tools_registered_as_writes():
    from aspen_mcp import safety
    assert {"add_binary_analysis", "add_pure_analysis"} <= safety.WRITE_TOOLS
