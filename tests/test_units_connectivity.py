from conftest import G, N
from aspen_mcp.tools.connectivity import block_connections, port_direction, stream_connections
from aspen_mcp.tools.units import collect_values_and_units, node_unit

PORTS = [
    {"block": "B1", "name": "F(IN)", "streams": ["1", "2"]},
    {"block": "B1", "name": "LD(OUT)", "streams": ["3"]},
    {"block": "B1", "name": "B(OUT)", "streams": ["4"]},
    {"block": "B2", "name": "F(IN)", "streams": ["4"]},
    {"block": "B2", "name": "P(OUT)", "streams": ["5"]},
]


def test_port_direction():
    assert port_direction("F(IN)") == "in"
    assert port_direction("ld(out)") == "out"
    assert port_direction("WEIRD") is None


def test_stream_roles():
    assert stream_connections(PORTS, "1") == {
        "source": None, "destination": {"block": "B1", "port": "F(IN)"}, "role": "feed"}
    assert stream_connections(PORTS, "3")["role"] == "product"
    s4 = stream_connections(PORTS, "4")
    assert s4["role"] == "internal"
    assert s4["source"]["block"] == "B1" and s4["destination"]["block"] == "B2"
    assert stream_connections(PORTS, "99")["role"] == "unconnected"


def test_block_connections():
    c = block_connections(PORTS, "B1")
    assert [i["stream"] for i in c["inlets"]] == ["1", "2"]
    assert {o["stream"] for o in c["outlets"]} == {"3", "4"}


def test_units_collected_and_blank_units_skipped():
    node = G("Output", N("RES_TEMP", 100.0, "C"), N("MW", 18.0), N("RES_PRES", 1.0, " bar "), G("X"))
    values, units = collect_values_and_units(node)
    assert values == {"RES_TEMP": 100.0, "MW": 18.0, "RES_PRES": 1.0}
    assert units == {"RES_TEMP": "C", "RES_PRES": "bar"}


def test_node_unit_handles_missing_attribute():
    class NoUnit:
        pass
    assert node_unit(NoUnit()) is None


def test_get_stream_reports_units_and_connections(fake_aspen):
    from aspen_mcp.tools.streams import tool_get_stream
    r = tool_get_stream("3")
    assert r["res_temp"] == 218.83
    assert r["units"]["res_temp"] == "F"
    assert r["units"]["res_pres"] == "psia"
    assert r["units"]["res_moleflow"] == "lbmol/hr"
    assert "mw" not in r["units"]
    assert r["unit_set"] == {"input": "ENG", "output": "ENG"}
    assert r["stream_unit_set"] == "ENG"
    assert r["connections"] == {"source": {"block": "B1", "port": "LD(OUT)"},
                                "destination": None, "role": "product"}
    assert r["mole_frac_liq"]["MCH"] == 0.9726


def test_get_block_reports_units_and_connections(fake_aspen):
    from aspen_mcp.tools.blocks import tool_get_block
    r = tool_get_block("B1")
    assert r["type"] == "RADFRAC"
    assert r["units"]["output"] == {"COND_DUTY": "Btu/hr", "REB_DUTY": "Btu/hr"}
    assert [i["stream"] for i in r["connections"]["inlets"]] == ["1", "2"]
    assert {o["stream"] for o in r["connections"]["outlets"]} == {"3", "4"}
