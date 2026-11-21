"""Offline tests for estimation/regression result shaping (layout from V15 probe)."""

from aspen_mcp.tools import prop_estimation as pe


def test_group_constants():
    rows = pe.group_estimates({1: 0.218, 2: 676.2}, {1: "ZC", 2: "TC"},
                              {1: "DEFINITI", 2: "JOBACK"}, {2: "K"}, coefficients=False)
    assert rows == [{"param": "ZC", "method": "DEFINITI", "unit": None, "value": 0.218},
                    {"param": "TC", "method": "JOBACK", "unit": "K", "value": 676.2}]


def test_group_t_dependent_coefficients():
    """PARANAME2 names only the first index of each parameter (2-EHN: 1 CPIG, 12 PLXANT)."""
    values = {i: float(i) for i in range(1, 15)}
    names = {1: "CPIG", 12: "PLXANT"}
    rows = pe.group_estimates(values, names, {1: "JOBACK", 12: "RIEDEL"}, {}, coefficients=True)
    assert [r["param"] for r in rows] == ["CPIG", "PLXANT"]
    assert rows[0]["values"] == [float(i) for i in range(1, 12)]
    assert rows[1]["values"] == [12.0, 13.0, 14.0] and rows[1]["method"] == "RIEDEL"


def test_estimation_and_regression_tools_registered():
    from aspen_mcp import safety
    assert {"set_estimation_method", "set_run_class"} <= safety.WRITE_TOOLS
