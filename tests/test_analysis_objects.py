import pytest

import test_flowsheeting as tf
from aspen_mcp.tools import analysis_objects as ao
from aspen_mcp.tools import flowsheeting as fs
from aspen_mcp.tools.flowsheeting import SpecError
from test_flowsheeting import aspen_tree  # noqa: F401  (fixture)

VARY_VECS = list(ao.VARY_ROW_NODES.values()) + ["LOWER", "UPPER", "INCR", "NPOINT", "OPT_LIST",
                                                "OPT_RANGE", "STEP_SIZE"]


def _rich_object(name):
    """Object whose Input has FVN_* rows, a shared VARY* row group, and scalars."""
    obj = tf._new_object(name)
    inp = obj.Elements("Input")
    inp._kids = [k for k in inp._kids if k.Name not in VARY_VECS]   # drop DS scalars
    vary = [tf._vec(n) for n in VARY_VECS]
    for v in vary:
        v._group = vary
    vary[VARY_VECS.index("VARYSENTENCE")]._valid = {"PARAM", "COL-SPECS", "RESULTS"}
    extra = [tf.Node(n) for n in ("OPER", "MAX_MIN", "OPT_COPY", *ao.EQ_NODES.values())]
    inp._kids += vary + extra + [tf._vec("EXPR"), tf._vec("CONID")]
    return obj


@pytest.fixture
def tree(aspen_tree, monkeypatch):
    monkeypatch.setattr(tf._Els, "Add", lambda self, n: self.o._kids.append(_rich_object(n)))
    return aspen_tree


XMCH = {"name": "XMCH", "vartype": "MOLE-FRAC", "stream": "3", "component": "MCH"}
FEED2 = {"vartype": "STREAM-VAR", "stream": "2", "variable": "MOLE-FLOW",
         "lower": 1200, "upper": 2000, "points": 5}


def test_plan_sensitivity_checks():
    with pytest.raises(SpecError, match="exactly one"):
        ao.plan_sensitivity([{**FEED2, "increment": 100}], ["XMCH"], [XMCH])
    with pytest.raises(SpecError, match="at most 5"):
        ao.plan_sensitivity([FEED2] * 6, ["XMCH"], [XMCH])
    with pytest.raises(SpecError, match="tabulate"):
        ao.plan_sensitivity([FEED2], [], [XMCH])
    p = ao.plan_sensitivity([FEED2], "XMCH", [XMCH])
    row = dict(p["varies"][0])
    assert row["VARY_VARTYPE"] == "STREAM-VAR" and row["VARYSUBS"] == "MIXED"
    assert row["OPT_RANGE"] == "NPOINT" and row["NPOINT"] == 5 and row["LOWER"] == "1200"


def test_add_sensitivity_writes_rows(tree):
    msg = ao.tool_add_sensitivity("s1", [FEED2], ["XMCH"], [XMCH])
    assert msg.startswith("Sensitivity 'S1' created"), msg
    inp = tree.FindNode(r"\Data\Model Analysis Tools\Sensitivity\S1\Input")
    assert inp.FindNode(r"VARYVARIABLE\#0").Value == "MOLE-FLOW"
    assert inp.FindNode(r"NPOINT\#0").Value == 5
    assert inp.FindNode(r"EXPR\1").Value == "XMCH"
    assert inp.FindNode(r"FVN_COMPONEN\XMCH").Value == "MCH"


def test_sensitivity_reuses_premade_blank_row(tree, monkeypatch):
    """Aspen pre-creates vary row #0 on a new Sensitivity; it must be reused."""
    def add(self, n):
        obj = _rich_object(n)
        vary = obj.Elements("Input").Elements("VARY_VARTYPE")
        vary.Elements.InsertRow(0, 0)          # the pre-made, blank row #0
        self.o._kids.append(obj)
    monkeypatch.setattr(tf._Els, "Add", add)
    ao.tool_add_sensitivity("S3", [FEED2], ["XMCH", "XMCH*100"], [XMCH])
    inp = tree.FindNode(r"\Data\Model Analysis Tools\Sensitivity\S3\Input")
    assert inp.FindNode("VARY_VARTYPE").Elements.Count == 1
    assert inp.FindNode(r"VARYSTREAM\#0").Value == "2"
    assert [c.Name for c in inp.FindNode("EXPR")._kids] == ["1", "2"]


def test_add_optimization_with_constraint(tree):
    assert ao.tool_add_constraint("c1", [XMCH], "XMCH", ">=", 0.97, 1e-4).startswith("Constraint 'C1'")
    rr = {"vartype": "BLOCK-VAR", "block": "B1", "sentence": "COL-SPECS", "variable": "MOLE-RR",
          "lower": 1, "upper": 10}
    q = {"name": "QREB", "vartype": "BLOCK-VAR", "block": "B1", "sentence": "RESULTS",
         "variable": "REB-DUTY"}
    msg = ao.tool_add_optimization("o1", [q], "QREB", "min", [rr], ["c1"])
    assert msg.startswith("Optimization 'O1' created"), msg
    inp = tree.FindNode(r"\Data\Model Analysis Tools\Optimization\O1\Input")
    assert inp.FindNode("MAX_MIN").Value == "MINIMIZE"
    assert inp.FindNode(r"CONID\#0").Value == "C1"
    assert inp.FindNode(r"UPPER\#0").Value == "10"
    con = tree.FindNode(r"\Data\Model Analysis Tools\Constraint\C1\Input")
    assert con.FindNode("OPER").Value == "GE"


def test_bad_vary_rolls_back(tree):
    bad = {**FEED2, "vartype": "BLOCK-VAR", "block": "B1", "sentence": "NOPE", "variable": "X"}
    msg = ao.tool_add_sensitivity("S2", [bad], ["XMCH"], [XMCH])
    assert msg.startswith("Error") and "vary 1" in msg
    assert fs.tool_list_flowsheeting_objects()["sensitivity"] == []


def test_add_transfer(tree):
    src = {"vartype": "BLOCK-VAR", "block": "SEG-1", "sentence": "PARAM", "variable": "IN-DIAM"}
    dests = [{**src, "block": b} for b in ("SEG-2", "SEG-3")]
    msg = ao.tool_add_transfer("t1", src, dests)
    assert msg.startswith("Transfer 'T1' created"), msg
    inp = tree.FindNode(r"\Data\Flowsheeting Options\Transfer\T1\Input")
    assert inp.FindNode("EQBLOCK").Value == "SEG-1"
    assert inp.FindNode(r"VARYBLOCK\#1").Value == "SEG-3"
    assert inp.FindNode("OPT_COPY").Value == "OBLOCK"


def test_operator_and_sense_validation():
    with pytest.raises(SpecError):
        ao.plan_constraint([XMCH], "XMCH", "!=", 1, 0.1)
    with pytest.raises(SpecError):
        ao.plan_optimization([XMCH], "XMCH", "best", [FEED2])


def test_registered():
    pytest.importorskip("fastmcp")
    import asyncio
    from aspen_mcp.server import create_server
    names = {t.name for t in asyncio.run(create_server().list_tools())}
    assert {"add_sensitivity", "add_optimization", "add_constraint", "add_transfer"} <= names
