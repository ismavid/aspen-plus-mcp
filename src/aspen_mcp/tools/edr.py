"""EDR (Exchanger Design and Rating) for rigorous HeatX blocks, plus utility costs.

What Aspen Plus V15 exposes (probed on the lab PC, 2026-11-21):

* A HeatX in a rigorous mode (Shell&Tube TASCPLUS, air-cooled ACOLPLUS, plate
  APLEPLUS) points to an .edr file via Input\\INPUT_FILE. Switching a HeatX
  *into* a rigorous mode cannot be done by SetValue (MODE reads back
  SHORTCUT); that needs the GUI's "Convert to rigorous".
* The EDR COM API (BJACCOM2.BJACApp) returns .NET collections whose items are
  None over COM, and GetDocument can blank the file. Not used here.
* The .edr file is plain UTF-8 XML:
    <HTEdata><application name="Tasc">
      <scalars type="input|results"><scalar name="X"><uom/><value/><source/></scalar>
      <arrays type="input|results"><array name="Y"><uom/><values>
          <valueslist list="a,b"/><sourcelist list="User,User"/></values></array>
  Editing input scalars/arrays in the working copy's .edr is picked up by the
  next reinit_and_run, and save() packs the edited file into the .apwz.

Economic Evaluation (APEA) and Energy Analyzer have no usable automation from
a script (the in-process IcarusInfo adapter crashes the caller; the
out-of-process one hangs on a hidden dialog; Energy Analyzer has no ProgID).
What *is* reachable is the utility cost summary, tool_get_utility_costs().
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from typing import Any

# Results worth showing first, by EDR application.
KEY_RESULTS = {
    "Tasc": ["AreaRatioDirty", "ExcessSurfDirty", "PresDropCalcSS", "PresDropCalcTS",
             "DPratioSS", "DPratioTS", "VelSS", "VelTS", "VelInNozSS", "RV2InNoz",
             "RV2LimInNoz", "RV2Violation", "VibIndicationTasc", "TEMAType"],
    "Acol": ["AreaRatioDirty", "AreaReqDirty", "Area", "PresDropCalcTS", "PresDropCalcOT",
             "DPratioTS", "DPratioOT", "VelInTS", "VelFaceAct", "NumFans", "FanDiam",
             "BundleNum", "TubesPerBundle", "TubeLngAct"],
}
# Used for EDR applications without their own list (e.g. plate "Aple"): any of these present.
GENERIC_RESULTS = ["AreaRatioDirty", "ExcessSurfDirty", "AreaReqDirty", "PresDropCalcSS",
                   "PresDropCalcTS", "PresDropCalcOT", "DPratioSS", "DPratioTS", "DPratioOT"]
KEY_INPUTS = {
    "Tasc": ["TEMAType", "ShlID", "TubeNum", "TubeLng", "TubeOD", "TubePitch",
             "BafType", "BafSpcCC", "BafNum", "BafCutPerc"],
    "Acol": ["TubeLng", "TubePerBund", "TubePassPerBund", "TubeRowDeep", "BayPar",
             "FanPerBay", "FanOD", "TypeBundle"],
}
CODES = {
    "RV2Violation": {"1": "violation", "2": "ok"},
    "VibIndicationTasc": {"1": "no vibration", "2": "vibration", "3": "possible vibration"},
}


class EdrError(ValueError):
    pass


def _num(text: str | None) -> Any:
    if text is None or text == "":
        return None
    try:
        f = float(text)
        return int(f) if f.is_integer() and "." not in text and "e" not in text.lower() else f
    except ValueError:
        return text


def _root(text: str) -> ET.Element:
    try:
        return ET.fromstring(text.lstrip("﻿"))
    except ET.ParseError as exc:
        raise EdrError(f"Not an EDR XML file: {exc}")


def _app(root: ET.Element) -> ET.Element:
    app = root.find("application")
    if app is None:
        raise EdrError("No <application> in the EDR file.")
    return app


def _section(app: ET.Element, tag: str, kind: str) -> ET.Element | None:
    for el in app.findall(tag):
        if el.get("type") == kind:
            return el
    return None


def _array_values(arr: ET.Element) -> Any:
    """Plain arrays -> list; per-stream arrays -> {stream_no: list}."""
    def vals(v: ET.Element | None):
        if v is None:
            return []
        vl = v.find("valueslist")
        return [_num(x) for x in (vl.get("list", "") if vl is not None else "").split(",")]
    streams = arr.findall("stream")
    if streams:
        return {s.get("streamnumber"): vals(s.find("values")) for s in streams}
    return vals(arr.find("values"))


def parse_edr(text: str) -> dict[str, Any]:
    app = _app(_root(text))
    out: dict[str, Any] = {"application": app.get("name")}
    for kind in ("input", "results"):
        sc = _section(app, "scalars", kind)
        out[kind] = {s.get("name"): {"value": _num(s.findtext("value")),
                                     "uom": (s.findtext("uom") or None),
                                     "source": s.findtext("source")}
                     for s in (sc.findall("scalar") if sc is not None else [])}
        ar = _section(app, "arrays", kind)
        out[kind + "_arrays"] = {a.get("name"): {"values": _array_values(a),
                                                 "uom": (a.findtext("uom") or None)}
                                 for a in (ar.findall("array") if ar is not None else [])}
    return out


def edr_summary(parsed: dict[str, Any]) -> dict[str, Any]:
    app = parsed.get("application") or ""
    def pick(section: dict, names: list[str]) -> dict:
        res = {}
        for n in names:
            if n in section:
                v = section[n]
                entry = {"value": v["value"], "uom": v["uom"]}
                label = CODES.get(n, {}).get(str(v["value"]))
                if label:
                    entry["meaning"] = label
                res[n] = entry
        return res
    noz = {n: parsed["input_arrays"][n]["values"]
           for n in ("NozActualIDSS", "NozActualIDTS") if n in parsed["input_arrays"]}
    return {"application": app,
            "results": pick(parsed["results"], KEY_RESULTS.get(app, GENERIC_RESULTS)),
            # Geometry as entered, else as calculated (EDR stores TEMA type, tube OD... in results).
            "geometry": {**pick(parsed["results"], KEY_INPUTS.get(app, [])),
                         **pick(parsed["input"], KEY_INPUTS.get(app, []))},
            "nozzle_id_in_out": noz,
            "has_results": bool(parsed["results"])}


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return repr(v) if not v.is_integer() else str(int(v)) if abs(v) < 1e15 else repr(v)
    return str(v)


def set_edr_inputs(text: str, scalars: dict[str, Any] | None = None,
                   arrays: dict[str, dict[int, Any]] | None = None) -> tuple[str, list[str]]:
    """Return (new_text, notes) with input scalars/array entries replaced.

    scalars: {"TubeLng": 53.63}. arrays: {"NozActualIDSS": {0: 1.049}}
    (index 0 = inlet, 1 = outlet for nozzle arrays). Only existing input
    names are accepted; edited entries get source "User".
    """
    root = _root(text)
    app = _app(root)
    notes: list[str] = []
    sc = _section(app, "scalars", "input")
    by_name = {s.get("name"): s for s in (sc.findall("scalar") if sc is not None else [])}
    for name, value in (scalars or {}).items():
        s = by_name.get(name)
        if s is None:
            raise EdrError(f"'{name}' is not an input scalar in this EDR file.")
        v = s.find("value")
        old = v.text if v is not None else None
        if v is None:
            v = ET.SubElement(s, "value")
        v.text = _fmt(value)
        src = s.find("source")
        if src is None:
            src = ET.SubElement(s, "source")
        src.text = "User"
        uom = s.findtext("uom")
        notes.append(f"{name}: {old} -> {v.text}{' ' + uom if uom else ''}")
    ar = _section(app, "arrays", "input")
    arr_by = {a.get("name"): a for a in (ar.findall("array") if ar is not None else [])}
    for name, entries in (arrays or {}).items():
        a = arr_by.get(name)
        if a is None:
            raise EdrError(f"'{name}' is not an input array in this EDR file.")
        if a.find("stream") is not None:
            raise EdrError(f"'{name}' is a per-stream array; editing those is not supported.")
        vl = a.find("values/valueslist")
        sl = a.find("values/sourcelist")
        if vl is None:
            raise EdrError(f"'{name}' has no value list.")
        vals = vl.get("list", "").split(",")
        srcs = sl.get("list", "").split(",") if sl is not None else []
        for idx, value in entries.items():
            i = int(idx)
            if i < 0:
                raise EdrError(f"Negative index for '{name}'.")
            while len(vals) <= i:
                vals.append("")
            while len(srcs) <= i:
                srcs.append("Default")
            old = vals[i]
            vals[i] = _fmt(value)
            srcs[i] = "User"
            notes.append(f"{name}[{i}]: {old or '-'} -> {vals[i]}")
        vl.set("list", ",".join(vals))
        if sl is not None:
            sl.set("list", ",".join(srcs))
    body = ET.tostring(root, encoding="unicode")
    bom = "﻿" if text.startswith("﻿") else ""
    return bom + '<?xml version="1.0" encoding="utf-8"?>\n' + body, notes


# ── files ────────────────────────────────────────────────────────────────
def _read(path: str) -> str:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return "﻿" + f.read()


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def find_edr_file(input_file: str | None, block: str, search_dirs: list[str]) -> str | None:
    """Locate a block's .edr: INPUT_FILE if absolute, else by name near the working copy."""
    names = []
    if input_file:
        if os.path.isabs(input_file) and os.path.isfile(input_file):
            return input_file
        names.append(os.path.basename(input_file).lower())
    names.append(f"{block}.edr".lower())
    for d in search_dirs:
        if not d or not os.path.isdir(d):
            continue
        base_depth = d.rstrip("\\/").count(os.sep)
        for cur, dirs, files in os.walk(d):
            if cur.count(os.sep) - base_depth >= 2:
                dirs[:] = []
            low = {f.lower(): f for f in files}
            for n in names:
                if n in low:
                    return os.path.join(cur, low[n])
    return None


def _inside(path: str, folder: str) -> bool:
    p, f = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    return p == f or p.startswith(f.rstrip("\\/") + os.sep)


# ── COM tools ────────────────────────────────────────────────────────────
def _heatx_info(block: str) -> dict[str, Any]:
    from ..com_bridge import aspen

    def impl():
        tree = aspen._app.RootModel("")
        b = tree.FindNode(rf"\Data\Blocks\{block}")
        if b is None:
            raise EdrError(f"Block '{block}' not found.")
        def val(path):
            try:
                n = b.FindNode(path)
                return None if n is None else n.Value
            except Exception:
                return None
        return {"block": block, "model": val(r"Input\MODE"), "program_mode": val(r"Input\PROGRAM_MODE"),
                "input_file": val(r"Input\INPUT_FILE"), "calc_model": val(r"Output\CALC_MODEL"),
                "edr_file_out": val(r"Output\EDR_FNM"), "blkstat": val(r"Output\BLKSTAT")}
    return aspen.call(impl)


def _heatx_blocks() -> list[str]:
    from ..com_bridge import aspen

    def impl():
        tree = aspen._app.RootModel("")
        blocks = tree.FindNode(r"\Data\Blocks")
        out = []
        if blocks is None:
            return out
        for i in range(blocks.Elements.Count):
            b = blocks.Elements.Item(i)
            try:
                rt = str(b.AttributeValue(6) or "").upper()     # HAP_RECORDTYPE
            except Exception:
                rt = ""
            if rt == "HEATX":
                out.append(b.Name)
                continue
            try:      # fallback: HeatX-only input nodes
                if b.FindNode(r"Input\INPUT_FILE") is not None and b.FindNode(r"Input\MODE") is not None:
                    out.append(b.Name)
            except Exception:
                pass
        return out
    return aspen.call(impl)


def _search_dirs() -> list[str]:
    from .. import safety
    w = safety.state.working
    if not w:
        return []
    d = os.path.dirname(w)
    stem = os.path.splitext(os.path.basename(w))[0]
    return [os.path.join(d, stem), d]


def _resolve(block: str) -> tuple[dict, str]:
    info = _heatx_info(block)
    path = find_edr_file(info.get("input_file") or info.get("edr_file_out"), block, _search_dirs())
    if not path:
        raise EdrError(f"No .edr file found for {block} (INPUT_FILE={info.get('input_file')!r}). "
                       "Is it a rigorous (EDR) HeatX, and is the file open via open_file?")
    return info, path


def tool_list_edr_exchangers() -> dict:
    """List HeatX blocks with their EDR file and key results."""
    try:
        out = []
        for b in _heatx_blocks():
            info = _heatx_info(b)
            path = find_edr_file(info.get("input_file") or info.get("edr_file_out"), b, _search_dirs())
            entry = {**info, "edr_path": path}
            if path:
                try:
                    s = edr_summary(parse_edr(_read(path)))
                    entry["results"] = s["results"]
                except Exception as exc:
                    entry["results_error"] = str(exc)
            out.append(entry)
        return {"exchangers": out}
    except Exception as exc:
        return {"error": str(exc)}


def tool_get_edr(block: str, names: list[str] | None = None) -> dict:
    """Key EDR results/geometry for one HeatX, plus any named scalars/arrays."""
    try:
        info, path = _resolve(block)
        parsed = parse_edr(_read(path))
        out = {**info, "edr_path": path, **edr_summary(parsed)}
        if names:
            extra = {}
            for n in names:
                for sec in ("input", "results", "input_arrays", "results_arrays"):
                    if n in parsed[sec]:
                        extra.setdefault(n, {})[sec] = parsed[sec][n]
                if n not in extra:
                    extra[n] = "not found"
            out["requested"] = extra
        return out
    except Exception as exc:
        return {"error": str(exc)}


def tool_set_edr_inputs(block: str, scalars: dict | None = None, arrays: dict | None = None) -> str:
    """Edit input geometry in a HeatX's EDR file (working copy only)."""
    from .. import safety
    st = safety.state
    if st.attached or not st.working:
        return ("Refused: EDR files are edited only in a working copy. open_file the simulation "
                "first (a GUI-attached document is your original).")
    try:
        _, path = _resolve(block)
        if not _inside(path, os.path.dirname(st.working)) or st.working == st.original:
            return (f"Refused: {path} is not inside the working-copy folder; edit a copy "
                    "opened with open_file (copy-on-open on).")
        arr = {k: {int(i): v for i, v in (d or {}).items()} for k, d in (arrays or {}).items()}
        new_text, notes = set_edr_inputs(_read(path), scalars, arr)
        _write(path, new_text)
        return (f"Edited {os.path.basename(path)} for {block}:\n  " + "\n  ".join(notes)
                + "\nRun reinit_and_run to recalculate; save() packs the edited file into the copy.")
    except Exception as exc:
        return "Error: " + str(exc)


def _node_values(n: Any, depth: int = 3) -> Any:
    """Value + unit of a scalar node; for a vector node, its children (not the row count)."""
    from .units import node_unit, iter_children
    kids = list(iter_children(n, 200)) if depth > 0 else []
    if kids:
        return {c.Name: _node_values(c, depth - 1) for c in kids}
    return {"value": n.Value, "unit": node_unit(n)}


UTILITY_SUM_KEYS = ("TOTUTCOST", "TOT_HEAT_COST", "TOT_COOL_COST", "ELECCOST", "ENERGY_PRICE",
                    "FEEDSTR_COST", "PRODSTR_COST", "NETSTR_COST", "NET_COST", "TCOST",
                    "CO2TAX", "NET_CO2RATE", "FEEDCO2E")


def tool_get_utility_costs() -> dict:
    """Utility and stream cost totals from Results Summary\\Utility-Sum (after a run)."""
    from ..com_bridge import aspen
    from .units import node_unit, iter_children

    def impl():
        tree = aspen._app.RootModel("")
        out_node = tree.FindNode(r"\Data\Results Summary\Utility-Sum\Output")
        if out_node is None:
            return {"error": "No utility summary. Define utilities (add_utility) and run first."}
        res: dict[str, Any] = {}
        for k in UTILITY_SUM_KEYS:
            try:
                n = out_node.Elements(k)
            except Exception:
                continue
            res[k] = _node_values(n)
        utils = tree.FindNode(r"\Data\Utilities")
        per = {}
        for u in iter_children(utils, 200):
            o = u.FindNode("Output") if hasattr(u, "FindNode") else None
            if o is None:
                continue
            row = {}
            for k in ("UTL_USAGE", "UTL_COST", "UTL_DUTY", "CO2RATE"):
                try:
                    n = o.Elements(k)
                except Exception:
                    continue
                row[k] = _node_values(n)
            if row:
                per[u.Name] = row
        res["by_utility"] = per
        res["note"] = ("Capital cost (Economic Evaluation) and Energy Analyzer targets are not "
                       "scriptable through COM on V15; use the GUI dashboards for those.")
        return res
    try:
        return aspen.call(impl)
    except Exception as exc:
        return {"error": str(exc)}
