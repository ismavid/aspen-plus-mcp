"""Unit helpers: read each value together with the unit Aspen reports for it.

These helpers only use duck-typed IHNode attributes (Elements, Name, Value,
ValueType, UnitString), so they run on the COM thread in production and
against simple fakes in the offline tests. They do not import win32com.
"""

from __future__ import annotations

from typing import Any

# Keys that are metadata, not physical values.
SKIP_KEYS = ("Unit Set", "User Table", "User Tree")


def node_unit(node: Any) -> str | None:
    """Return the unit string Aspen shows for *node*, or None if it has none."""
    try:
        unit = node.UnitString
    except Exception:
        return None
    if unit is None:
        return None
    unit = str(unit).strip()
    return unit or None


def iter_children(node: Any, limit: int = 10_000):
    """Yield the children of an IHNode by index until the list ends."""
    if node is None:
        return
    for i in range(limit):
        try:
            child = node.Elements(i)
        except Exception:
            return
        if child is None:
            return
        yield child


def collect_values_and_units(node: Any, max_keys: int = 80) -> tuple[dict[str, Any], dict[str, str]]:
    """Return ({Name: Value}, {Name: unit}) for every scalar child of *node*.

    Children with ValueType == 0 are containers and are skipped. A value
    with no unit (counts, flags, mole fractions) is left out of the unit map.
    """
    values: dict[str, Any] = {}
    units: dict[str, str] = {}
    for child in iter_children(node):
        try:
            if child.ValueType == 0:
                continue
            name = child.Name
            values[name] = child.Value
        except Exception:
            continue
        unit = node_unit(child)
        if unit:
            units[name] = unit
        if len(values) >= max_keys:
            break
    return values, units


def read_unit_set(tree: Any) -> dict[str, str]:
    """Best-effort read of the global input/output unit sets (e.g. ENG, SI, METCBAR)."""
    found: dict[str, str] = {}
    for key, out in (("INSET", "input"), ("OUTSET", "output")):
        node = tree
        for part in ("Data", "Setup", "Global", "Input", key):
            try:
                node = node.Elements(part)
            except Exception:
                node = None
            if node is None:
                break
        if node is None:
            continue
        try:
            value = node.Value
        except Exception:
            continue
        if value:
            found[out] = str(value)
    return found
