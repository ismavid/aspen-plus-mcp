"""Design spec / calculator tools against a fake tree that mimics Aspen's
parallel FVN_* vectors (InsertRow on one adds the row to all siblings)."""

import pytest

from aspen_mcp.tools import flowsheeting as fs
from aspen_mcp.tools.flowsheeting import SpecError, fortran_line, plan_calculator, plan_design_spec

FVN = ["FVN_" + f for _, f in fs.VAR_FIELDS]


class Node:
    def __init__(self, name, value=None, children=None, group=None, valid=None):
        self.Name = name
        self.Value = value
        self.UnitString = ""
        self._kids = list(children or [])
        self.ValueType = 0 if children is not None else 1
        self._group = group          # sibling vectors that share rows
        self._valid = valid
        self.Elements = _Els(self)

    def SetValue(self, _flag, value):
        if self._valid and value not in self._valid:
            raise RuntimeError(f"{value} is not a valid value")
        self.Value = value

    def FindNode(self, path):
        node = self
        for part in path.strip("\\").split("\\"):
            node = node.Elements._get(part)
            if node is None:
                return None
        return node


class _Els:
    def __init__(self, owner):
        self.o = owner

    def _get(self, key):
        kids = self.o._kids
        if isinstance(key, int):
            return kids[key] if key < len(kids) else None
        return next((c for c in kids if c.Name == key), None)

    def __call__(self, key):
        n = self._get(key)
        if n is None:
            raise KeyError(key)
        return n

    @property
    def Count(self):
        return len(self.o._kids)

    def InsertRow(self, _dim, i):
        for vec in (self.o._group or [self.o]):
            vec._kids.insert(i, Node(f"#{i}"))
            vec.ValueType = 0

    def SetLabel(self, _dim, i, _flag, label):
        for vec in (self.o._group or [self.o]):
            vec._kids[i].Name = label

    def Add(self, name):
        self.o._kids.append(_new_object(name))

    def Remove(self, name):
        self.o._kids = [c for c in self.o._kids if c.Name != name]


def _vec(name):
    return Node(name, children=[])


def _new_object(name):
    fvn = [_vec(n) for n in FVN]
    for v in fvn:
        v._group = fvn
    vary = [Node(n) for n in fs._DS_VARY_NODES.values()]
    vary[list(fs._DS_VARY_NODES).index("sentence")]._valid = {"COL-SPECS", "PARAM", "RESULTS"}
    scal = [Node(n) for n in ("EXPR1", "EXPR2", "TOL", "LOWER", "UPPER", "STEP_SIZE", "MAX_STEP_SIZ")]
    vecs = [_vec(n) for n in ("READ_VAR", "WRITE_VAR", "FORTRAN_EXEC", "FORTRAN_DEC")]
    return Node(name, children=[Node("Input", children=fvn + vary + scal + vecs),
                                Node("Output", children=[])])


def _tree():
    folders = Node("Flowsheeting Options", children=[Node("Design-Spec", children=[]),
                                                     Node("Calculator", children=[]),
                                                     Node("Transfer", children=[])])
    mat = Node("Model Analysis Tools", children=[Node("Sensitivity", children=[]),
                                                Node("Optimization", children=[]),
                                                Node("Constraint", children=[])])
    return Node("root", children=[Node("Data", children=[folders, mat])])


@pytest.fixture
def aspen_tree(monkeypatch):
    from aspen_mcp.com_bridge import aspen
    tree = _tree()

    class App:
        def RootModel(self, _=""):
            return tree
    monkeypatch.setattr(aspen, "_app", App(), raising=False)
    monkeypatch.setattr(aspen, "call", lambda fn: fn())
    return tree


XMCH = {"name": "xmch", "vartype": "mole-frac", "stream": "3", "component": "MCH"}
RR = {"vartype": "BLOCK-VAR", "block": "B1", "sentence": "COL-SPECS", "variable": "MOLE-RR"}


def test_plan_design_spec_orders_fields_and_defaults_substream():
    p = plan_design_spec([XMCH], "XMCH", 0.98, 1e-4, RR, 1, 20)
    name, writes = p["rows"][0]
    assert name == "XMCH"
    assert writes == [("FVN_VARTYPE", "MOLE-FRAC"), ("FVN_STREAM", "3"),
                      ("FVN_SUBS", "MIXED"), ("FVN_COMPONEN", "MCH")]
    assert [n for n, _ in p["vary"]] == ["VARY_VARTYPE", "VARYBLOCK", "VARYSENTENCE", "VARYVARIABLE"]
    assert ("EXPR2", "0.98") in p["scalars"] and ("LOWER", "1") in p["limits"]


@pytest.mark.parametrize("bad, msg", [
    (dict(defines=[]), "at least one"),
    (dict(defines=[{"name": "TOOLONGNAME", "vartype": "X"}]), "8 characters"),
    (dict(defines=[{"name": "A"}]), "vartype"),
    (dict(defines=[XMCH, XMCH]), "twice"),
    (dict(lower=5, upper=1), "below"),
    (dict(defines=[{**XMCH, "colour": 1}]), "Unknown variable field"),
])
def test_plan_design_spec_rejects_bad_input(bad, msg):
    args = dict(defines=[XMCH], spec="XMCH", target=0.98, tolerance=1e-4, vary=RR, lower=1, upper=20)
    args.update(bad)
    with pytest.raises(SpecError, match=msg):
        plan_design_spec(**args)


def test_fortran_line_prefix():
    assert fortran_line("VFLOW=FLOW/DENS") == "F        VFLOW=FLOW/DENS"
    assert fortran_line("F        X=1") == "F        X=1"
    assert fortran_line("C comment") == "C comment"


def test_plan_calculator_needs_an_export():
    d = [{"name": "TF", "vartype": "STREAM-VAR", "stream": "1", "variable": "TEMP"}]
    with pytest.raises(SpecError, match="export"):
        plan_calculator(d, ["X=1"])
    d2 = d + [{"name": "P", "vartype": "BLOCK-VAR", "block": "B1", "sentence": "PARAM",
               "variable": "PRES", "direction": "export"}]
    p = plan_calculator(d2, "P = TF/10\nIF (P .GT. 30) P = 30")
    assert p["imports"] == ["TF"] and p["exports"] == ["P"]
    assert p["exec"] == ["F        P = TF/10", "F        IF (P .GT. 30) P = 30"]


def test_add_design_spec_builds_tree(aspen_tree):
    msg = fs.tool_add_design_spec("ds1", [XMCH], "XMCH", 0.98, 0.0001, RR, 1, 20)
    assert msg.startswith("Design spec 'DS1' created"), msg
    inp = aspen_tree.FindNode(r"\Data\Flowsheeting Options\Design-Spec\DS1\Input")
    assert inp.FindNode(r"FVN_COMPONEN\XMCH").Value == "MCH"
    assert inp.FindNode(r"FVN_SUBS\XMCH").Value == "MIXED"
    assert inp.FindNode("EXPR1").Value == "XMCH"
    assert inp.FindNode("VARYVARIABLE").Value == "MOLE-RR"
    assert inp.FindNode("UPPER").Value == "20"


def test_add_design_spec_removes_half_built_object_on_aspen_rejection(aspen_tree):
    bad = {**RR, "sentence": "NOPE"}
    msg = fs.tool_add_design_spec("DS2", [XMCH], "XMCH", 0.98, 1e-4, bad, 1, 20)
    assert msg.startswith("Error") and "not a valid value" in msg
    assert aspen_tree.FindNode(r"\Data\Flowsheeting Options\Design-Spec\DS2") is None


def test_duplicate_name_is_refused(aspen_tree):
    fs.tool_add_design_spec("DS1", [XMCH], "XMCH", 0.98, 1e-4, RR, 1, 20)
    assert "already exists" in fs.tool_add_design_spec("DS1", [XMCH], "XMCH", 0.9, 1e-4, RR, 1, 20)


def test_add_calculator_and_list_get_remove(aspen_tree):
    defines = [{"name": "TF", "vartype": "STREAM-VAR", "stream": "1", "variable": "TEMP"},
               {"name": "P", "vartype": "BLOCK-VAR", "block": "B1", "sentence": "PARAM",
                "variable": "PRES", "direction": "export"}]
    msg = fs.tool_add_calculator("c1", defines, ["P = TF/10"], ["REAL*8 K"])
    assert msg.startswith("Calculator 'C1' created"), msg
    inp = aspen_tree.FindNode(r"\Data\Flowsheeting Options\Calculator\C1\Input")
    assert [c.Value for c in inp.FindNode("WRITE_VAR")._kids] == ["P"]
    assert [c.Value for c in inp.FindNode("READ_VAR")._kids] == ["TF"]
    assert inp.FindNode(r"FORTRAN_EXEC\#0").Value == "F        P = TF/10"
    assert inp.FindNode(r"FORTRAN_DEC\#0").Value == "F        REAL*8 K"

    listing = fs.tool_list_flowsheeting_objects()
    assert listing["calculator"] == ["C1"] and listing["design-spec"] == []
    got = fs.tool_get_flowsheeting_object("calc", "C1")
    assert got["input"]["FVN_VARIABLE"]["P"] == "PRES"
    assert fs.tool_remove_flowsheeting_object("calculator", "C1") == "Removed calculator 'C1'"
    assert fs.tool_list_flowsheeting_objects()["calculator"] == []


def test_new_tools_are_registered_and_logged():
    pytest.importorskip("fastmcp")
    import asyncio
    from aspen_mcp import safety
    from aspen_mcp.server import create_server
    tools = {t.name for t in asyncio.run(create_server().list_tools())}
    assert {"add_design_spec", "add_calculator", "list_flowsheeting_objects",
            "get_flowsheeting_object", "remove_flowsheeting_object"} <= tools
    assert {"add_design_spec", "add_calculator", "remove_flowsheeting_object"} <= safety.WRITE_TOOLS
