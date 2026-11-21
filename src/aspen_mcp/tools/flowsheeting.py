"""Flowsheeting objects: Design Specs and Calculator blocks.

Layout in the Aspen Plus V15 COM tree (probed on a live install):

  \\Data\\Flowsheeting Options\\Design-Spec\\<name>\\Input
      EXPR1 (spec expression), EXPR2 (target), TOL
      FVN_<FIELD>\\<var>        define variables (parallel vectors keyed by name)
      VARY_VARTYPE, VARYBLOCK, VARYSENTENCE, VARYVARIABLE, ...   the one vary
      LOWER, UPPER, STEP_SIZE, MAX_STEP_SIZ
  \\Data\\Flowsheeting Options\\Calculator\\<name>\\Input
      FVN_<FIELD>\\<var>        define variables
      READ_VAR\\#i / WRITE_VAR\\#i  import / export variable names
      FORTRAN_EXEC\\#i / FORTRAN_DEC\\#i  one Fortran line per row

Objects are created with Elements.Add(name) on the folder. A define row is
created with InsertRow(0, i) on FVN_VARTYPE, which adds the row to every
FVN_* sibling, then SetLabel(0, i, False, name). Fields must be written in
order (VARTYPE, then BLOCK/STREAM, SENTENCE, VARIABLE ...) because Aspen
validates each against the previous ones.

The plan_* functions are pure and build an ordered list of writes, so they
are unit-tested without Aspen; the tool_* functions apply them on COM.
"""

from __future__ import annotations

from typing import Any

from ..com_bridge import aspen
from .units import iter_children, node_unit

FOLDERS = {
    "design-spec": r"\Data\Flowsheeting Options\Design-Spec",
    "calculator": r"\Data\Flowsheeting Options\Calculator",
    "transfer": r"\Data\Flowsheeting Options\Transfer",
    "sensitivity": r"\Data\Model Analysis Tools\Sensitivity",
    "optimization": r"\Data\Model Analysis Tools\Optimization",
    "constraint": r"\Data\Model Analysis Tools\Constraint",
}

# Friendly key -> Aspen field suffix, in the order Aspen needs them written.
VAR_FIELDS = [
    ("vartype", "VARTYPE"),
    ("block", "BLOCK"),
    ("stream", "STREAM"),
    ("substream", "SUBS"),
    ("component", "COMPONEN"),
    ("sentence", "SENTENCE"),
    ("variable", "VARIABLE"),
    ("id1", "ID1"),
    ("id2", "ID2"),
    ("id3", "ID3"),
    ("element", "ELEM"),
    ("propset", "PROPSET"),
    ("paramno", "PARAMNO"),
    ("units", "UOM"),
    ("init_value", "INIT_VAL"),
]
_VAR_KEYS = {k for k, _ in VAR_FIELDS}

# Design-Spec vary scalars: friendly key -> node name.
_DS_VARY_NODES = {
    "vartype": "VARY_VARTYPE", "block": "VARYBLOCK", "stream": "VARYSTREAM",
    "substream": "VARYSUBS", "component": "VARYCOMPONEN", "sentence": "VARYSENTENCE",
    "variable": "VARYVARIABLE", "id1": "VARYID1", "id2": "VARYID2", "id3": "VARYID3",
    "element": "VARYELEM", "paramno": "VARYPARAMNO", "units": "VARYUOM",
    "init_value": "VARYINIT_VAL",
}

FORTRAN_PREFIX = "F        "


class SpecError(ValueError):
    """Bad input for a flowsheeting object (raised before touching Aspen)."""


def kind_key(kind: str) -> str:
    k = kind.strip().lower().replace("_", "-").replace(" ", "-")
    k = {"designspec": "design-spec", "spec": "design-spec", "calc": "calculator",
         "sens": "sensitivity", "opt": "optimization"}.get(k.replace("-", ""), k)
    if k not in FOLDERS:
        raise SpecError(f"Unknown object kind '{kind}'. Use one of: {', '.join(FOLDERS)}")
    return k


def check_variable(var: dict[str, Any], *, need_name: bool = True) -> dict[str, Any]:
    """Validate a variable description and return it with normalised keys."""
    if not isinstance(var, dict):
        raise SpecError("Each variable must be an object like "
                        '{"name": "XMCH", "vartype": "MOLE-FRAC", "stream": "3", "component": "MCH"}')
    v = {str(k).lower(): val for k, val in var.items() if val is not None}
    unknown = set(v) - _VAR_KEYS - {"name", "direction"}
    if unknown:
        raise SpecError(f"Unknown variable field(s) {sorted(unknown)}. Allowed: name, direction, "
                        + ", ".join(k for k, _ in VAR_FIELDS))
    if need_name:
        name = str(v.get("name", "")).strip()
        if not name:
            raise SpecError("Variable is missing 'name'")
        if len(name) > 8 or not name[0].isalpha() or not name.replace("_", "").isalnum():
            raise SpecError(f"Variable name '{name}' must be a Fortran name: starts with a letter, "
                            "letters/digits/_ only, at most 8 characters")
        v["name"] = name.upper()
    if not v.get("vartype"):
        raise SpecError(f"Variable {v.get('name', '')!s} is missing 'vartype' "
                        "(e.g. MOLE-FRAC, STREAM-VAR, BLOCK-VAR, MASS-FLOW, PARAMETER)")
    v["vartype"] = str(v["vartype"]).upper()
    if "substream" not in v and (v.get("stream") is not None):
        v["substream"] = "MIXED"
    return v


def plan_define_rows(defines: list[dict[str, Any]]) -> list[tuple[str, list[tuple[str, Any]]]]:
    """[(var_name, [(FVN_FIELD, value), ...])] in write order."""
    seen: set[str] = set()
    plan = []
    for raw in defines:
        v = check_variable(raw)
        if v["name"] in seen:
            raise SpecError(f"Variable '{v['name']}' is defined twice")
        seen.add(v["name"])
        writes = [(f"FVN_{field}", v[key]) for key, field in VAR_FIELDS if key in v]
        plan.append((v["name"], writes))
    return plan


def plan_design_spec(defines, spec, target, tolerance, vary, lower, upper,
                     step_size=None, max_step_size=None) -> dict[str, Any]:
    if not defines:
        raise SpecError("A design spec needs at least one define variable")
    rows = plan_define_rows(defines)
    if not str(spec).strip():
        raise SpecError("'spec' (the expression to drive, e.g. XMCH) is empty")
    vary_v = check_variable(vary, need_name=False)
    unknown_vary = set(vary_v) - set(_DS_VARY_NODES)
    if unknown_vary:
        raise SpecError(f"Vary field(s) {sorted(unknown_vary)} are not supported for a design spec")
    try:
        lo, hi = float(lower), float(upper)
    except (TypeError, ValueError):
        raise SpecError("'lower' and 'upper' must be numbers")
    if lo >= hi:
        raise SpecError(f"lower ({lower}) must be below upper ({upper})")
    scalars: list[tuple[str, Any]] = [("EXPR1", str(spec)), ("EXPR2", _num_str(target)),
                                      ("TOL", _num_str(tolerance))]
    vary_writes = [(_DS_VARY_NODES[k], vary_v[k]) for k, _ in VAR_FIELDS if k in vary_v]
    limits = [("LOWER", _num_str(lower)), ("UPPER", _num_str(upper))]
    if step_size is not None:
        limits.append(("STEP_SIZE", _num_str(step_size)))
    if max_step_size is not None:
        limits.append(("MAX_STEP_SIZ", _num_str(max_step_size)))
    return {"rows": rows, "scalars": scalars, "vary": vary_writes, "limits": limits}


def fortran_line(line: str) -> str:
    """Return a Fortran statement in the form Aspen stores (F + 8 spaces + code)."""
    s = line.rstrip("\n")
    if s.startswith("F ") or s.startswith("F\t") or s.startswith("C ") or s == "F":
        return s
    return FORTRAN_PREFIX + s.strip()


def plan_calculator(defines, fortran, declarations=None) -> dict[str, Any]:
    if not defines:
        raise SpecError("A calculator needs at least one define variable")
    rows = plan_define_rows(defines)
    imports, exports = [], []
    for raw, (name, _) in zip(defines, rows):
        direction = str({k.lower(): v for k, v in raw.items()}.get("direction", "import")).lower()
        if direction in ("import", "read", "in"):
            imports.append(name)
        elif direction in ("export", "write", "out"):
            exports.append(name)
        else:
            raise SpecError(f"Variable {name}: direction must be 'import' or 'export'")
    if not exports:
        raise SpecError("A calculator needs at least one 'export' variable (one it writes)")
    if isinstance(fortran, str):
        fortran = fortran.splitlines()
    lines = [fortran_line(l) for l in (fortran or []) if l.strip()]
    if not lines:
        raise SpecError("'fortran' is empty")
    if isinstance(declarations, str):
        declarations = declarations.splitlines()
    decs = [fortran_line(l) for l in (declarations or []) if l.strip()]
    return {"rows": rows, "imports": imports, "exports": exports, "exec": lines, "decl": decs}


def _num_str(x: Any) -> str:
    if isinstance(x, bool):
        raise SpecError("numbers expected, got a boolean")
    if isinstance(x, (int, float)):
        return repr(float(x)) if isinstance(x, float) else str(x)
    return str(x)


# ── COM side ─────────────────────────────────────────────────────────────

def _child(node: Any, key: Any) -> Any:
    try:
        return node.Elements(key)
    except Exception:
        return None


def _set(input_node: Any, path: list[Any], value: Any) -> None:
    node = input_node
    for p in path:
        node = _child(node, p)
        if node is None:
            raise SpecError(f"Aspen has no input node {'/'.join(map(str, path))}")
    try:
        setter = node.SetValue
    except AttributeError:
        setter = None
    try:
        if setter is not None:
            setter(0, value)
        else:
            node.Value = value
    except Exception as exc:
        raise SpecError(f"Aspen rejected {'/'.join(map(str, path))} = {value!r}: {exc}") from None
    try:
        got = node.Value
    except Exception:
        got = value
    if got in (None, "") and value not in (None, ""):
        raise SpecError(f"Aspen rejected {'/'.join(map(str, path))} = {value!r}")


def _insert_row(input_node: Any, vector: str, label: str | None = None) -> int:
    vec = _child(input_node, vector)
    if vec is None:
        raise SpecError(f"Aspen has no table '{vector}' here")
    i = vec.Elements.Count
    vec.Elements.InsertRow(0, i)
    if label is not None:
        vec.Elements.SetLabel(0, i, False, label)
    return i


def _apply_rows(input_node: Any, rows) -> None:
    for name, writes in rows:
        _insert_row(input_node, "FVN_VARTYPE", name)
        for field, value in writes:
            try:
                _set(input_node, [field, name], value)
            except SpecError as exc:
                raise SpecError(f"Variable {name}: {exc}") from None


def _apply_vector(input_node: Any, vector: str, values: list[Any]) -> None:
    for value in values:
        i = _insert_row(input_node, vector)
        _set(input_node, [vector, i], value)


def _new_object(root: Any, kind: str, name: str):
    folder = root.FindNode(FOLDERS[kind])
    if folder is None:
        raise SpecError(f"This simulation has no {FOLDERS[kind]} folder")
    if _child(folder, name) is not None:
        raise SpecError(f"{kind} '{name}' already exists; remove it first or pick another name")
    folder.Elements.Add(name)
    obj = _child(folder, name)
    inp = _child(obj, "Input") if obj is not None else None
    if inp is None:
        raise SpecError(f"Aspen did not create {kind} '{name}'")
    return folder, inp


def _remove_on_fail(folder: Any, name: str) -> None:
    try:
        folder.Elements.Remove(name)
    except Exception:
        pass


def tool_add_design_spec(name: str, defines: list[dict], spec: str, target: float | str,
                         tolerance: float | str, vary: dict, lower: float, upper: float,
                         step_size: float | None = None, max_step_size: float | None = None) -> str:
    try:
        plan = plan_design_spec(defines, spec, target, tolerance, vary, lower, upper,
                                step_size, max_step_size)
    except SpecError as exc:
        return f"Error: {exc}"
    name = name.strip().upper()

    def impl():
        root = aspen._app.RootModel("")
        folder, inp = _new_object(root, "design-spec", name)
        try:
            _apply_rows(inp, plan["rows"])
            for node, value in plan["scalars"] + plan["vary"] + plan["limits"]:
                _set(inp, [node], value)
        except Exception:
            _remove_on_fail(folder, name)
            raise
        return (f"Design spec '{name}' created: {spec} = {target} ± {tolerance}, varying "
                f"{_describe(plan['vary'])} between {lower} and {upper}. "
                f"Defines: {', '.join(n for n, _ in plan['rows'])}. Run the simulation to solve it.")
    try:
        return aspen.call(impl)
    except SpecError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error: {exc}"


def tool_add_calculator(name: str, defines: list[dict], fortran: list[str] | str,
                        declarations: list[str] | str | None = None) -> str:
    try:
        plan = plan_calculator(defines, fortran, declarations)
    except SpecError as exc:
        return f"Error: {exc}"
    name = name.strip().upper()

    def impl():
        root = aspen._app.RootModel("")
        folder, inp = _new_object(root, "calculator", name)
        try:
            _apply_rows(inp, plan["rows"])
            _apply_vector(inp, "READ_VAR", plan["imports"])
            _apply_vector(inp, "WRITE_VAR", plan["exports"])
            _apply_vector(inp, "FORTRAN_EXEC", plan["exec"])
            if plan["decl"]:
                _apply_vector(inp, "FORTRAN_DEC", plan["decl"])
        except Exception:
            _remove_on_fail(folder, name)
            raise
        return (f"Calculator '{name}' created with {len(plan['exec'])} Fortran line(s). "
                f"Imports: {', '.join(plan['imports']) or '-'}; exports: {', '.join(plan['exports'])}. "
                "Aspen sequences it automatically from its imports and exports.")
    try:
        return aspen.call(impl)
    except SpecError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error: {exc}"


def _describe(writes: list[tuple[str, Any]]) -> str:
    d = dict(writes)
    parts = [str(d[k]) for k in ("VARYBLOCK", "VARYSTREAM", "VARYVARIABLE", "VARYCOMPONEN") if d.get(k)]
    return " ".join(parts) or str(d.get("VARY_VARTYPE", "?"))


def _dump(node: Any, depth: int = 3) -> Any:
    """Plain dict of a subtree: scalars as values (with units), containers as dicts."""
    if node is None:
        return None
    try:
        if node.ValueType != 0:
            u = node_unit(node)
            return {"value": node.Value, "unit": u} if u else node.Value
    except Exception:
        return None
    if depth <= 0:
        return "…"
    out: dict[str, Any] = {}
    for c in iter_children(node, 2000):
        try:
            name = c.Name
        except Exception:
            continue
        if name is None:
            continue
        val = _dump(c, depth - 1)
        if val in (None, {}, ""):
            continue
        out[name] = val
    return out


def tool_list_flowsheeting_objects() -> dict[str, list[str]]:
    def impl():
        root = aspen._app.RootModel("")
        result: dict[str, list[str]] = {}
        for kind, path in FOLDERS.items():
            folder = root.FindNode(path)
            if folder is None:
                continue
            result[kind] = [c.Name for c in iter_children(folder) if c.Name]
        return result
    try:
        return aspen.call(impl)
    except Exception as exc:
        return {"error": [str(exc)]}


def tool_get_flowsheeting_object(kind: str, name: str) -> dict[str, Any]:
    try:
        k = kind_key(kind)
    except SpecError as exc:
        return {"error": str(exc)}

    def impl():
        root = aspen._app.RootModel("")
        obj = root.FindNode(FOLDERS[k] + "\\" + name)
        if obj is None:
            return {"error": f"{k} '{name}' not found"}
        return {"kind": k, "name": name,
                "input": _dump(_child(obj, "Input"), 2),
                "output": _dump(_child(obj, "Output"), 2)}
    try:
        return aspen.call(impl)
    except Exception as exc:
        return {"error": str(exc)}


def tool_remove_flowsheeting_object(kind: str, name: str) -> str:
    try:
        k = kind_key(kind)
    except SpecError as exc:
        return f"Error: {exc}"

    def impl():
        folder = aspen._app.RootModel("").FindNode(FOLDERS[k])
        if folder is None or _child(folder, name) is None:
            return f"Error: {k} '{name}' not found"
        folder.Elements.Remove(name)
        return f"Removed {k} '{name}'"
    try:
        return aspen.call(impl)
    except Exception as exc:
        return f"Error: {exc}"
