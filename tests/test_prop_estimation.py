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


def test_regression_param_rows_write_type_first():
    """Live V15: without TYPE first, NRTL and the component IDs are rejected."""
    rows = pe.plan_regression_params([{"param": "nrtl", "element": 2, "i": "etoac", "j": "ethanol"},
                                      {"param": "TC", "i": "ETOAC", "initial": 523.3}])
    assert [w[0] for w in rows[0]] == ["TYPE", "PARAMNAME", "ELEMNO", "CIDI", "CIDJ", "USAGE"]
    assert rows[0][0] == ("TYPE", "BI-PARAMETER") and ("ELEMNO", 2) in rows[0]
    assert rows[1][0] == ("TYPE", "PARAMETER") and ("PINIT", 523.3) in rows[1]
    assert not any(w[0] == "CIDJ" for w in rows[1])


def test_regression_validation():
    import pytest
    from aspen_mcp.tools.flowsheeting import SpecError
    with pytest.raises(SpecError):
        pe.plan_regression_params([{"param": "NRTL", "type": "BI-PARAMETER", "i": "A"}])
    with pytest.raises(SpecError):
        pe.check_points([[40.0, 136.6, 0.006]], 6)
    pe.check_points([[40.0, 136.6, 0.006, 0.994, 0.022, 0.978]], 6)
