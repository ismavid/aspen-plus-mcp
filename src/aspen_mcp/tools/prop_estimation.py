"""Property estimation (PCES) and data regression (DRS): read results, set up methods.

COM layout, probed on V15 (2026-11-21):

Estimation, \\Data\\Properties\\Estimation\\Estimate:
* Input switches: UNCHOICE (pure constants), TCHOICE (T-dependent), BINCHOICE,
  GRPCHOICE (YES/NO), ALLONLY (ALL/ONLY). With UNCHOICE=NO, METHODU rows are
  ignored silently.
* Pure-constant methods: METHODU\\<PARAM>\\<COMP>\\#0 (row added with
  InsertRow(0, n) + SetLabel(0, n, False, COMP) on METHODU\\<PARAM>).
* Output, keyed \\<COMP>\\<n>: constants ESTVALUE / PARANAME / ESTMETHOD2 /
  VAR_UNIT; T-dependent ESTVALUE2 / PARANAME2 / ESTMETHOD3 / VAR_UNIT2, where
  PARANAME2 names the first index of each parameter and the following indices
  are its coefficients.

Regression, needs \\Data\\Setup\\Main\\Input\\RUN_CLASS = DRS:
* Cases: \\Data\\Properties\\Regression\\<case>, Elements.Add("NAME").
* Input: REGRESSION, OPSETNAME, ALGORITHM, OBJECT_FUNC, MAXIT, TOL; parameter
  rows PARAMNAME/ELEMNO/CIDI/CIDJ/PINIT/LOWER/UPPER (#i); WEIGHT\\<dataset>.
* Output: ESTVALUE2\\<i> (regressed values), OBJ_FUNC, SIGMA, AAD/AARD/RMS_DEV/
  MAX_DEV\\#i\\<dataset>.
"""

from __future__ import annotations

from typing import Any

from .flowsheeting import SpecError, _child, _set

EST = r"\Data\Properties\Estimation\Estimate"
REG = r"\Data\Properties\Regression"
RUN_CLASS = r"\Data\Setup\Main\Input\RUN_CLASS"
RUN_CLASSES = ("FLOWSHEET", "PROP", "PCES", "DRS", "ADA", "TGS")


def _kids(n: Any) -> list:
    out = []
    if n is None:
        return out
    for i in range(10000):
        try:
            c = n.Elements(i)
        except Exception:
            break
        if c is None:
            break
        out.append(c)
    return out


def _by_index(node: Any) -> dict[str, dict[int, Any]]:
    """node\\<COMP>\\<n> -> {comp: {n: value}}."""
    out: dict[str, dict[int, Any]] = {}
    for comp in _kids(node):
        vals = {}
        for c in _kids(comp):
            try:
                vals[int(c.Name)] = c.Value
            except (TypeError, ValueError):
                continue
        out[comp.Name] = vals
    return out


def group_estimates(values: dict[int, Any], names: dict[int, Any], methods: dict[int, Any],
                    units: dict[int, Any], coefficients: bool) -> list[dict[str, Any]]:
    """Turn index-keyed output vectors into rows of {param, value(s), method, unit}."""
    rows: list[dict[str, Any]] = []
    for i in sorted(values):
        name = names.get(i)
        if name not in (None, ""):
            row = {"param": name, "method": methods.get(i), "unit": units.get(i)}
            if coefficients:
                row["values"] = [values[i]]
            else:
                row["value"] = values[i]
            rows.append(row)
        elif coefficients and rows:
            rows[-1]["values"].append(values[i])
    return rows


def tool_get_estimation_results(component: str | None = None) -> dict:
    """Estimated pure constants and T-dependent parameters per component."""
    from ..com_bridge import aspen

    def impl():
        root = aspen._app.RootModel("")
        est = root.FindNode(EST)
        if est is None:
            return {"error": "This simulation has no property estimation."}
        inp, out = _child(est, "Input"), _child(est, "Output")
        switches = {}
        for k in ("UNCHOICE", "TCHOICE", "BINCHOICE", "GRPCHOICE", "ALLONLY"):
            n = _child(inp, k) if inp is not None else None
            switches[k] = None if n is None else n.Value
        if out is None:
            return {"switches": switches, "error": "No estimation results; run first."}
        get = lambda k: _by_index(_child(out, k))
        v1, n1, m1, u1 = get("ESTVALUE"), get("PARANAME"), get("ESTMETHOD2"), get("VAR_UNIT")
        v2, n2, m2, u2 = get("ESTVALUE2"), get("PARANAME2"), get("ESTMETHOD3"), get("VAR_UNIT2")
        comps = sorted(set(v1) | set(v2))
        if component:
            comps = [c for c in comps if c.upper() == component.upper()]
        res = {}
        for c in comps:
            res[c] = {"constants": group_estimates(v1.get(c, {}), n1.get(c, {}), m1.get(c, {}),
                                                   u1.get(c, {}), False),
                      "t_dependent": group_estimates(v2.get(c, {}), n2.get(c, {}), m2.get(c, {}),
                                                     u2.get(c, {}), True)}
        note = None
        if str(switches.get("UNCHOICE", "")).upper() == "NO":
            note = "UNCHOICE=NO: pure-component constants are NOT estimated (methods set in METHODU are ignored)."
        return {"switches": switches, "components": res, **({"note": note} if note else {})}
    try:
        return aspen.call(impl)
    except Exception as exc:
        return {"error": str(exc)}


def tool_set_estimation_method(component: str, param: str, method: str = "DEFINITI") -> str:
    """Estimate one pure constant (e.g. ZC of DES-OXAC by DEFINITI) and turn UNCHOICE on."""
    from ..com_bridge import aspen
    comp, prm, meth = (str(x).strip().upper() for x in (component, param, method))

    def impl():
        root = aspen._app.RootModel("")
        inp = root.FindNode(EST + r"\Input")
        if inp is None:
            raise SpecError("This simulation has no property estimation input.")
        vec = inp.FindNode(rf"METHODU\{prm}")
        if vec is None:
            raise SpecError(f"Aspen has no estimation method table for {prm}.")
        if _child(vec, comp) is None:
            n = vec.Elements.Count
            vec.Elements.InsertRow(0, n)
            vec.Elements.SetLabel(0, n, False, comp)
        _set(inp, ["METHODU", prm, comp, "#0"], meth)
        notes = []
        un = _child(inp, "UNCHOICE")
        if un is not None and str(un.Value or "").upper() != "YES":
            _set(inp, ["UNCHOICE"], "YES")
            notes.append("UNCHOICE switched to YES (pure constants were not being estimated)")
        return (f"{prm} of {comp} will be estimated by {meth}."
                + ("\n" + "; ".join(notes) if notes else "") + "\nRun, then get_estimation_results.")
    try:
        return aspen.call(impl)
    except Exception as exc:
        return f"Error: {exc}"


def tool_set_run_class(run_class: str) -> str:
    """Switch the run type (FLOWSHEET, PROP, PCES, DRS = data regression, ...)."""
    from ..com_bridge import aspen
    rc = str(run_class).strip().upper()
    rc = {"REGRESSION": "DRS", "ESTIMATION": "PCES", "ANALYSIS": "PROP"}.get(rc, rc)
    if rc not in RUN_CLASSES:
        return f"Error: run_class must be one of {', '.join(RUN_CLASSES)} (or REGRESSION)."

    def impl():
        inp = aspen._app.RootModel("").FindNode(RUN_CLASS.rsplit("\\", 1)[0])
        node = _child(inp, "RUN_CLASS") if inp is not None else None
        if node is None:
            raise SpecError("No Setup\\Main\\RUN_CLASS node.")
        old = node.Value
        _set(inp, ["RUN_CLASS"], rc)
        return f"Run type {old} -> {rc}."
    try:
        return aspen.call(impl)
    except Exception as exc:
        return f"Error: {exc}"


PARAM_FIELDS = ("PARAMNAME", "ELEMNO", "CIDI", "CIDJ", "PINIT", "LOWER", "UPPER", "USAGE")
DEV_FIELDS = ("AAD", "AARD", "AVG_DEV", "RMS_DEV", "MAX_DEV")


def _vector(node: Any) -> list:
    return [c.Value for c in _kids(node)]


def tool_get_regression(case: str | None = None) -> dict:
    """List regression cases, or one case's parameters, regressed values and deviations."""
    from ..com_bridge import aspen

    def impl():
        root = aspen._app.RootModel("")
        run_class = getattr(root.FindNode(RUN_CLASS), "Value", None)
        reg = root.FindNode(REG)
        if reg is None:
            return {"run_class": run_class,
                    "error": "No regression folder. Set the run type with set_run_class('DRS')."}
        if not case:
            return {"run_class": run_class, "cases": [c.Name for c in _kids(reg)]}
        obj = _child(reg, case.upper()) or _child(reg, case)
        if obj is None:
            return {"error": f"No regression case '{case}'."}
        inp, out = _child(obj, "Input"), _child(obj, "Output")
        settings = {k: getattr(_child(inp, k), "Value", None)
                    for k in ("REGRESSION", "OPSETNAME", "ALGORITHM", "OBJECT_FUNC", "MAXIT", "TOL")}
        cols = {f: _vector(_child(inp, f)) for f in PARAM_FIELDS}
        n = max((len(v) for v in cols.values()), default=0)
        params = [{f: (cols[f][i] if i < len(cols[f]) else None) for f in PARAM_FIELDS} for i in range(n)]
        weights = {c.Name: c.Value for c in _kids(_child(inp, "WEIGHT"))}
        result: dict[str, Any] = {"run_class": run_class, "case": obj.Name, "settings": settings,
                                  "parameters": params, "data_sets": weights}
        if out is not None:
            est = _vector(_child(out, "ESTVALUE2"))
            for i, v in enumerate(est):
                if i < len(params):
                    params[i]["regressed"] = v
            for k in ("OBJ_FUNC", "SIGMA"):
                node = _child(out, k)
                if node is not None:
                    result[k.lower()] = node.Value
            devs = {}
            for f in DEV_FIELDS:
                node = _child(out, f)
                for row in _kids(node):
                    for ds in _kids(row):
                        devs.setdefault(ds.Name, {}).setdefault(f, []).append(ds.Value)
            if devs:
                result["deviations"] = devs
        return result
    try:
        return aspen.call(impl)
    except Exception as exc:
        return {"error": str(exc)}
