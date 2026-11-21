"""Property analysis (Properties > Analysis): binary Txy/Pxy and pure-component tables.

COM layout, probed on V15 (2026-11-21):

* Create: \\Data\\Properties\\Analysis.Elements.Add("NAME!BINARY") (also !PURE,
  !GENERIC, !RESIDUE). A name without "!TYPE" fails.
* BINARY Input, written in this order: ANALTYPE (TXY, PXY, GIBBS-ENERGY, ...),
  COMP1, COMP2, CVARNAME (MOLEFRAC/MASSFRAC), CCOMP, VARVALUE=RANGE,
  OPT_CPTINCR=POINT, CLOWER, CUPPER, CNPOINT, then the fixed variable:
  PVARVALUE=LIST + PUNITLABEL + PLIST\\#i rows (Txy), or TVARVALUE/TUNITLABEL/
  TLIST (Pxy). Optional OPSETNAME (property method).
* PURE Input: PUREPROP (CP, CPIG, DHVL, PL, ...), COMPS\\#i, TVARVALUE=RANGE with
  TLOWER/TUPPER/TNPOINT, PVARVALUE=LIST with PLIST.
* After a run: Output\\PROP_DATA\\<PROP>\\<phase or comp>\\...\\<row>, rows "1".."n".
"""

from __future__ import annotations

from typing import Any

from .flowsheeting import SpecError, _child, _set, _insert_row, _remove_on_fail

FOLDER = r"\Data\Properties\Analysis"
BINARY_TYPES = ("TXY", "PXY", "GIBBS-ENERGY", "TXXY", "PXXY", "TXX")


def _clean(name: str) -> str:
    n = str(name or "").strip().upper()
    if not n or len(n) > 8 or not n.replace("-", "").replace("_", "").isalnum():
        raise SpecError("Analysis name must be 1-8 letters/digits (e.g. TXY1).")
    return n


def _values(values: Any, what: str) -> list[float]:
    if isinstance(values, (int, float)):
        values = [values]
    try:
        vals = [float(v) for v in values]
    except (TypeError, ValueError):
        raise SpecError(f"{what} must be a number or a list of numbers.")
    if not vals:
        raise SpecError(f"Give at least one {what}.")
    return vals


def plan_binary(comp1: str, comp2: str, kind: str = "TXY", pressures: Any = None,
                temperatures: Any = None, units: str | None = None, points: int = 11,
                basis: str = "MOLEFRAC", property_method: str | None = None
                ) -> dict[str, Any]:
    """Ordered writes for a binary analysis (pure; no Aspen)."""
    kind = str(kind).upper()
    if kind not in ("TXY", "PXY"):
        raise SpecError("kind must be TXY or PXY.")
    c1, c2 = str(comp1).strip().upper(), str(comp2).strip().upper()
    if not c1 or not c2 or c1 == c2:
        raise SpecError("Give two different components.")
    basis = str(basis).upper()
    if basis not in ("MOLEFRAC", "MASSFRAC"):
        raise SpecError("basis must be MOLEFRAC or MASSFRAC.")
    if not 2 <= int(points) <= 200:
        raise SpecError("points must be between 2 and 200.")
    writes: list[tuple[str, Any]] = [("ANALTYPE", kind), ("COMP1", c1), ("COMP2", c2),
                                     ("CVARNAME", basis), ("CCOMP", c1), ("VARVALUE", "RANGE"),
                                     ("OPT_CPTINCR", "POINT"), ("CLOWER", 0.0), ("CUPPER", 1.0),
                                     ("CNPOINT", int(points))]
    if kind == "TXY":
        fixed = _values(pressures if pressures is not None else 1.0, "pressure")
        writes += [("PVARVALUE", "LIST"), ("PUNITLABEL", units or "atm")]
        rows = ("PLIST", fixed)
    else:
        if temperatures is None:
            raise SpecError("A Pxy analysis needs temperatures.")
        fixed = _values(temperatures, "temperature")
        writes += [("TVARVALUE", "LIST"), ("TUNITLABEL", units or "C")]
        rows = ("TLIST", fixed)
    if property_method:
        writes.append(("OPSETNAME", str(property_method).upper()))
    return {"type": "BINARY", "writes": writes, "rows": [rows]}


def plan_pure(prop: str, components: list[str], t_lower: float, t_upper: float,
              points: int = 11, t_units: str | None = None, pressure: float = 1.0,
              p_units: str | None = None, property_method: str | None = None) -> dict[str, Any]:
    comps = [str(c).strip().upper() for c in (components or []) if str(c).strip()]
    if not comps:
        raise SpecError("Give at least one component.")
    if float(t_lower) >= float(t_upper):
        raise SpecError("t_lower must be below t_upper.")
    writes: list[tuple[str, Any]] = [("PUREPROP", str(prop).strip().upper()),
                                     ("TVARVALUE", "RANGE"), ("TLOWER", float(t_lower)),
                                     ("TUPPER", float(t_upper)), ("TNPOINT", int(points))]
    if t_units:
        writes.append(("TUNITLABEL", t_units))
    writes += [("PVARVALUE", "LIST"), ("PUNITLABEL", p_units or "atm")]
    if property_method:
        writes.append(("OPSETNAME", str(property_method).upper()))
    return {"type": "PURE", "writes": writes, "rows": [("COMPS", comps), ("PLIST", [float(pressure)])]}


# ── results ──────────────────────────────────────────────────────────────
def _row_key(name: str) -> int | None:
    try:
        return int(str(name))
    except ValueError:
        return None


def flatten_table(prop_data: Any) -> dict[str, list]:
    """PROP_DATA subtree -> {"TEMP TOTAL": [...], "KVL TOTAL TOLUENE": [...], ...}.

    A node whose children are all numbered rows is a column; its path is the name.
    """
    cols: dict[str, dict[int, Any]] = {}

    def kids(n):
        out = []
        for i in range(10000):
            try:
                c = n.Elements(i)
            except Exception:
                break
            if c is None:
                break
            out.append(c)
        return out

    def walk(n, path):
        ch = kids(n)
        if not ch:
            return
        if all(_row_key(c.Name) is not None for c in ch) and not any(kids(c) for c in ch[:2]):
            cols[" ".join(path)] = {_row_key(c.Name): c.Value for c in ch}
            return
        for c in ch:
            walk(c, path + [c.Name])

    for c in kids(prop_data):
        walk(c, [c.Name])
    rows = sorted({r for col in cols.values() for r in col})
    return {name: [col.get(r) for r in rows] for name, col in cols.items()}


def add_vapor_fractions(table: dict[str, list], comps: list[str]) -> None:
    """y_i = K_i * x_i when the table has KVL and liquid mole fractions."""
    for comp in comps:
        k = next((v for n, v in table.items() if n.startswith("KVL") and n.endswith(comp)), None)
        x = next((v for n, v in table.items()
                  if n.startswith("MOLEFRAC") and n.endswith(comp) and "VAPOR" not in n), None)
        if k and x and f"y {comp}" not in table:
            table[f"y {comp} (K*x)"] = [None if a is None or b is None else a * b for a, b in zip(k, x)]


# ── COM tools ────────────────────────────────────────────────────────────
def _create(name: str, plan: dict[str, Any]) -> str:
    from ..com_bridge import aspen

    def impl():
        root = aspen._app.RootModel("")
        folder = root.FindNode(FOLDER)
        if folder is None:
            raise SpecError("This simulation has no Properties\\Analysis folder.")
        if _child(folder, name) is not None:
            raise SpecError(f"Analysis '{name}' already exists.")
        folder.Elements.Add(f"{name}!{plan['type']}")
        inp = _child(_child(folder, name), "Input")
        if inp is None:
            raise SpecError(f"Aspen did not create analysis '{name}'.")
        try:
            for node, value in plan["writes"]:
                _set(inp, [node], value)
            for vector, values in plan["rows"]:
                for v in values:
                    i = _insert_row(inp, vector)
                    _set(inp, [vector, i], v)
        except Exception:
            _remove_on_fail(folder, name)
            raise
        return f"{plan['type'].title()} analysis '{name}' created. Run the simulation, then get_property_analysis('{name}')."
    return aspen.call(impl)


def tool_add_binary_analysis(name: str, comp1: str, comp2: str, kind: str = "TXY",
                             pressures: Any = None, temperatures: Any = None,
                             units: str | None = None, points: int = 11,
                             basis: str = "MOLEFRAC", property_method: str | None = None) -> str:
    try:
        return _create(_clean(name), plan_binary(comp1, comp2, kind, pressures, temperatures,
                                                 units, points, basis, property_method))
    except Exception as exc:
        return f"Error: {exc}"


def tool_add_pure_analysis(name: str, prop: str, components: list[str], t_lower: float,
                           t_upper: float, points: int = 11, t_units: str | None = None,
                           pressure: float = 1.0, p_units: str | None = None,
                           property_method: str | None = None) -> str:
    try:
        return _create(_clean(name), plan_pure(prop, components, t_lower, t_upper, points,
                                               t_units, pressure, p_units, property_method))
    except Exception as exc:
        return f"Error: {exc}"


def tool_get_property_analysis(name: str) -> dict:
    from ..com_bridge import aspen
    from .units import node_unit

    def impl():
        root = aspen._app.RootModel("")
        obj = root.FindNode(rf"{FOLDER}\{name.upper()}")
        if obj is None:
            return {"error": f"No analysis '{name}'."}
        inp = _child(obj, "Input")
        info = {}
        for k in ("ANALTYPE", "COMP1", "COMP2", "PUREPROP", "OPSETNAME"):
            n = _child(inp, k) if inp is not None else None
            if n is not None and n.Value is not None:
                info[k] = n.Value
        pd = obj.FindNode(r"Output\PROP_DATA")
        if pd is None:
            return {**info, "error": "No results yet. Run the simulation first."}
        table = flatten_table(pd)
        comps = [info[k] for k in ("COMP1", "COMP2") if k in info]
        add_vapor_fractions(table, comps)
        units = {}
        for col in list(table)[:40]:
            try:
                node = pd.FindNode(col.split(" (")[0].replace(" ", "\\") + "\\1")
                u = node_unit(node) if node is not None else None
                if u:
                    units[col] = u
            except Exception:
                pass
        return {**info, "rows": len(next(iter(table.values()), [])), "columns": table, "units": units}
    try:
        return aspen.call(impl)
    except Exception as exc:
        return {"error": str(exc)}
