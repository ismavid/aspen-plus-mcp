"""Aspen's own Sensitivity, Optimization, Constraint and Transfer objects.

Layout in the Aspen Plus V15 COM tree (probed live):

  \\Data\\Model Analysis Tools\\Sensitivity\\<S>\\Input
      FVN_*\\<var>              define variables for tabulation
      VARY_VARTYPE\\#i, VARYBLOCK\\#i, VARYSENTENCE\\#i, VARYVARIABLE\\#i, ...
      LOWER\\#i, UPPER\\#i, INCR\\#i, NPOINT\\#i, OPT_LIST\\#i (RANGE/LIST),
      OPT_RANGE\\#i (INCR/NPOINT)   one row per varied input
      EXPR\\#i                    tabulated expressions
  \\Data\\Model Analysis Tools\\Optimization\\<O>\\Input
      FVN_*, EXPR1 (objective), MAX_MIN (MAXIMIZE/MINIMIZE), CONID\\#i,
      vary rows with LOWER\\#i, UPPER\\#i, STEP_SIZE\\#i
  \\Data\\Model Analysis Tools\\Constraint\\<C>\\Input
      FVN_*, EXPR1, OPER (GE/LE/EQ), EXPR2, TOL
  \\Data\\Flowsheeting Options\\Transfer\\<T>\\Input
      EQVARTYPE, EQBLOCK, EQSTREAM, EQSUBS, EQCOMPONENT, EQSENTENCE, EQVARIABLE, ...
      (the source), and VARY*\\#i rows (the destinations)

One InsertRow(0, i) on VARY_VARTYPE adds row #i to every VARY* sibling
(including LOWER/UPPER/...). As elsewhere, plan_* functions are pure.
"""

from __future__ import annotations

from typing import Any

from ..com_bridge import aspen
from .flowsheeting import (
    VAR_FIELDS,
    SpecError,
    _apply_rows,
    _apply_vector,
    _child,
    _insert_row,
    _new_object,
    _num_str,
    _remove_on_fail,
    _set,
    check_variable,
    fortran_line,
    plan_define_rows,
)

# Friendly key -> vary-row node name (same names as the Design-Spec scalars).
VARY_ROW_NODES = {
    "vartype": "VARY_VARTYPE", "block": "VARYBLOCK", "stream": "VARYSTREAM",
    "substream": "VARYSUBS", "component": "VARYCOMPONEN", "sentence": "VARYSENTENCE",
    "variable": "VARYVARIABLE", "id1": "VARYID1", "id2": "VARYID2", "id3": "VARYID3",
    "element": "VARYELEM", "paramno": "VARYPARAMNO", "units": "VARYUOM",
}

# Transfer source: friendly key -> node (note EQCOMPONENT is spelled in full).
EQ_NODES = {
    "vartype": "EQVARTYPE", "block": "EQBLOCK", "stream": "EQSTREAM",
    "substream": "EQSUBS", "component": "EQCOMPONENT", "sentence": "EQSENTENCE",
    "variable": "EQVARIABLE", "id1": "EQID1", "id2": "EQID2", "id3": "EQID3",
    "element": "EQELEM", "paramno": "EQPARAMNO", "units": "EQUOM",
}

_ORDER = [k for k, _ in VAR_FIELDS]


def _vary_writes(var: dict[str, Any], nodes: dict[str, str], what: str) -> list[tuple[str, Any]]:
    unknown = {k for k in var if k not in nodes and k not in ("name", "direction")}
    unknown -= {"lower", "upper", "points", "increment", "step_size", "init_value"}
    if unknown:
        raise SpecError(f"{what}: field(s) {sorted(unknown)} not supported here")
    return [(nodes[k], var[k]) for k in _ORDER if k in var and k in nodes]


def _check_vary(raw: dict[str, Any], what: str) -> dict[str, Any]:
    v = check_variable({k: x for k, x in raw.items()
                        if k.lower() not in ("lower", "upper", "points", "increment", "step_size")},
                       need_name=False)
    for k in ("lower", "upper", "points", "increment", "step_size"):
        if k in {kk.lower() for kk in raw}:
            v[k] = {kk.lower(): x for kk, x in raw.items()}[k]
    return v


def plan_sensitivity(varies, tabulate, defines=None) -> dict[str, Any]:
    if not varies:
        raise SpecError("A sensitivity needs at least one vary")
    if len(varies) > 5:
        raise SpecError("Aspen allows at most 5 varied inputs in one sensitivity")
    rows = []
    for i, raw in enumerate(varies):
        v = _check_vary(raw, f"vary {i + 1}")
        try:
            lo, hi = float(v["lower"]), float(v["upper"])
        except (KeyError, TypeError, ValueError):
            raise SpecError(f"vary {i + 1}: 'lower' and 'upper' numbers are required") from None
        if lo >= hi:
            raise SpecError(f"vary {i + 1}: lower ({lo}) must be below upper ({hi})")
        if ("points" in v) == ("increment" in v):
            raise SpecError(f"vary {i + 1}: give exactly one of 'points' or 'increment'")
        writes = _vary_writes(v, VARY_ROW_NODES, f"vary {i + 1}")
        extra = [("OPT_LIST", "RANGE"), ("LOWER", _num_str(v["lower"])),
                 ("UPPER", _num_str(v["upper"]))]
        if "points" in v:
            n = int(v["points"])
            if n < 2:
                raise SpecError(f"vary {i + 1}: 'points' must be at least 2")
            extra += [("OPT_RANGE", "NPOINT"), ("NPOINT", n)]
        else:
            inc = float(v["increment"])
            if inc <= 0:
                raise SpecError(f"vary {i + 1}: 'increment' must be positive")
            extra += [("OPT_RANGE", "INCR"), ("INCR", _num_str(v["increment"]))]
        rows.append(writes + extra)
    if isinstance(tabulate, str):
        tabulate = [tabulate]
    exprs = [str(e).strip() for e in (tabulate or []) if str(e).strip()]
    if not exprs:
        raise SpecError("'tabulate' needs at least one expression (usually a define name)")
    return {"rows": plan_define_rows(defines or []), "varies": rows, "tabulate": exprs}


def plan_constraint(defines, expression, operator, bound, tolerance) -> dict[str, Any]:
    op = str(operator).strip().upper()
    op = {">=": "GE", "<=": "LE", "=": "EQ", "==": "EQ"}.get(op, op)
    if op not in ("GE", "LE", "EQ"):
        raise SpecError("operator must be one of GE (>=), LE (<=), EQ (=)")
    if not defines:
        raise SpecError("A constraint needs at least one define variable")
    if not str(expression).strip():
        raise SpecError("'expression' is empty")
    return {"rows": plan_define_rows(defines),
            "scalars": [("EXPR1", str(expression)), ("OPER", op), ("EXPR2", _num_str(bound)),
                        ("TOL", _num_str(tolerance))]}


def plan_optimization(defines, objective, sense, varies, constraints=None) -> dict[str, Any]:
    s = str(sense).strip().upper()
    s = {"MAX": "MAXIMIZE", "MIN": "MINIMIZE"}.get(s, s)
    if s not in ("MAXIMIZE", "MINIMIZE"):
        raise SpecError("sense must be 'maximize' or 'minimize'")
    if not defines:
        raise SpecError("An optimization needs at least one define variable")
    if not varies:
        raise SpecError("An optimization needs at least one vary")
    rows = []
    for i, raw in enumerate(varies):
        v = _check_vary(raw, f"vary {i + 1}")
        try:
            lo, hi = float(v["lower"]), float(v["upper"])
        except (KeyError, TypeError, ValueError):
            raise SpecError(f"vary {i + 1}: 'lower' and 'upper' numbers are required") from None
        if lo >= hi:
            raise SpecError(f"vary {i + 1}: lower ({lo}) must be below upper ({hi})")
        extra = [("LOWER", _num_str(v["lower"])), ("UPPER", _num_str(v["upper"]))]
        if "step_size" in v:
            extra.append(("STEP_SIZE", _num_str(v["step_size"])))
        rows.append(_vary_writes(v, VARY_ROW_NODES, f"vary {i + 1}") + extra)
    if not str(objective).strip():
        raise SpecError("'objective' is empty")
    cons = [str(c).strip().upper() for c in (constraints or []) if str(c).strip()]
    return {"rows": plan_define_rows(defines), "scalars": [("EXPR1", str(objective)), ("MAX_MIN", s)],
            "varies": rows, "constraints": cons}


def plan_transfer(source, destinations) -> dict[str, Any]:
    src = check_variable(source, need_name=False)
    if not destinations:
        raise SpecError("A transfer needs at least one destination")
    return {"source": _vary_writes(src, EQ_NODES, "source"),
            "dests": [_vary_writes(check_variable(d, need_name=False), VARY_ROW_NODES,
                                   f"destination {i + 1}")
                      for i, d in enumerate(destinations)]}


# ── COM side ─────────────────────────────────────────────────────────────

def _apply_vary_rows(inp: Any, rows: list[list[tuple[str, Any]]]) -> None:
    for n, writes in enumerate(rows):
        i = _insert_row(inp, "VARY_VARTYPE")
        for node, value in writes:
            try:
                _set(inp, [node, i], value)
            except SpecError as exc:
                raise SpecError(f"vary {n + 1}: {exc}") from None


def _build(kind: str, name: str, fill) -> str:
    name = name.strip().upper()

    def impl():
        root = aspen._app.RootModel("")
        folder, inp = _new_object(root, kind, name)
        try:
            return fill(inp, name)
        except Exception:
            _remove_on_fail(folder, name)
            raise
    try:
        return aspen.call(impl)
    except Exception as exc:
        return f"Error: {exc}"


def tool_add_sensitivity(name: str, varies: list[dict], tabulate: list[str] | str,
                         defines: list[dict] | None = None) -> str:
    try:
        plan = plan_sensitivity(varies, tabulate, defines)
    except SpecError as exc:
        return f"Error: {exc}"

    def fill(inp, nm):
        _apply_rows(inp, plan["rows"])
        _apply_vary_rows(inp, plan["varies"])
        _apply_vector(inp, "EXPR", plan["tabulate"])
        return (f"Sensitivity '{nm}' created: {len(plan['varies'])} varied input(s), tabulating "
                f"{', '.join(plan['tabulate'])}. Run the simulation, then read the table with "
                f"get_flowsheeting_object('sensitivity', '{nm}').")
    return _build("sensitivity", name, fill)


def tool_add_constraint(name: str, defines: list[dict], expression: str, operator: str,
                        bound: float | str, tolerance: float | str) -> str:
    try:
        plan = plan_constraint(defines, expression, operator, bound, tolerance)
    except SpecError as exc:
        return f"Error: {exc}"

    def fill(inp, nm):
        _apply_rows(inp, plan["rows"])
        for node, value in plan["scalars"]:
            _set(inp, [node], value)
        d = dict(plan["scalars"])
        return f"Constraint '{nm}' created: {d['EXPR1']} {d['OPER']} {d['EXPR2']} (tol {d['TOL']})."
    return _build("constraint", name, fill)


def tool_add_optimization(name: str, defines: list[dict], objective: str, sense: str,
                          varies: list[dict], constraints: list[str] | None = None,
                          fortran: list[str] | str | None = None) -> str:
    try:
        plan = plan_optimization(defines, objective, sense, varies, constraints)
    except SpecError as exc:
        return f"Error: {exc}"
    if isinstance(fortran, str):
        fortran = fortran.splitlines()
    lines = [fortran_line(l) for l in (fortran or []) if l.strip()]

    def fill(inp, nm):
        _apply_rows(inp, plan["rows"])
        for node, value in plan["scalars"]:
            _set(inp, [node], value)
        _apply_vary_rows(inp, plan["varies"])
        if plan["constraints"]:
            _apply_vector(inp, "CONID", plan["constraints"])
        if lines:
            _apply_vector(inp, "FORTRAN_EXEC", lines)
        return (f"Optimization '{nm}' created: {plan['scalars'][1][1].lower()} {objective} over "
                f"{len(plan['varies'])} variable(s)"
                + (f", subject to {', '.join(plan['constraints'])}" if plan["constraints"] else "")
                + ". Run the simulation to solve it.")
    return _build("optimization", name, fill)


def tool_add_transfer(name: str, source: dict, destinations: list[dict]) -> str:
    try:
        plan = plan_transfer(source, destinations)
    except SpecError as exc:
        return f"Error: {exc}"

    def fill(inp, nm):
        for node, value in plan["source"]:
            _set(inp, [node], value)
        _apply_vary_rows(inp, plan["dests"])
        return f"Transfer '{nm}' created: copies the source value to {len(plan['dests'])} destination(s)."
    return _build("transfer", name, fill)
