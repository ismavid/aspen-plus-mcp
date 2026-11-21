"""Offline test setup: stub the Windows COM modules and provide a fake Aspen tree.

Nothing here needs Aspen Plus or Windows. The fake tree mimics the IHNode
attributes the tools use: Elements(i | name), Name, Value, ValueType, UnitString.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Stub pywin32 so aspen_mcp.com_bridge imports on any OS.
if "pythoncom" not in sys.modules:
    pythoncom = types.ModuleType("pythoncom")
    pythoncom.CoInitialize = lambda: None
    pythoncom.CoUninitialize = lambda: None
    pythoncom.CoInitializeEx = lambda *a, **k: None
    pythoncom.COINIT_APARTMENTTHREADED = 2
    sys.modules["pythoncom"] = pythoncom
if "win32com" not in sys.modules:
    win32com = types.ModuleType("win32com")
    client = types.ModuleType("win32com.client")
    client.Dispatch = lambda *a, **k: None
    client.GetObject = lambda *a, **k: None
    win32com.client = client
    sys.modules["win32com"] = win32com
    sys.modules["win32com.client"] = client


class _Elements:
    def __init__(self, children):
        self._children = children

    def __call__(self, key):
        if isinstance(key, int):
            if key >= len(self._children):
                raise IndexError(key)
            return self._children[key]
        for c in self._children:
            if c.Name == key:
                return c
        raise KeyError(key)

    @property
    def Count(self):
        return len(self._children)

    def Item(self, i):
        return self._children[i]


class FakeNode:
    def __init__(self, name, value=None, unit=None, children=None):
        self.Name = name
        self.Value = value
        self.UnitString = unit or ""
        self._children = list(children or [])
        self.ValueType = 0 if children is not None else 1
        self.Elements = _Elements(self._children)


def N(name, value=None, unit=None):
    """Scalar leaf node."""
    return FakeNode(name, value, unit)


def G(name, *children, value=None):
    """Container node."""
    return FakeNode(name, value, None, children)


def mch_tree():
    """A small RadFrac flowsheet like Aspen's mch.bkp example, in ENG units."""
    def stream(name, temp, pres, moleflow, massflow):
        return G(name, G("Output",
                         N("RES_TEMP", temp, "F"),
                         N("RES_PRES", pres, "psia"),
                         N("RES_VFRAC", 0.0),
                         N("RES_MOLEFLOW", moleflow, "lbmol/hr"),
                         N("RES_MASSFLOW", massflow, "lb/hr"),
                         N("MW", 98.02),
                         N("Unit Set", "ENG"),
                         G("X", N("MCH", 0.9726), N("TOLUENE", 0.0261), N("PHENOL", 0.0014))))

    b1 = G("B1",
           G("Input", N("NSTAGE", 22), N("BASIS_RR", 8.0)),
           G("Output", N("COND_DUTY", -1.2e7, "Btu/hr"), N("REB_DUTY", 1.5e7, "Btu/hr")),
           G("Ports",
             G("F(IN)", G("1"), G("2")),
             G("LD(OUT)", G("3")),
             G("B(OUT)", G("4"))),
           value="RADFRAC")
    return G("root", G("Data",
                       G("Setup", G("Global", G("Input", N("INSET", "ENG"), N("OUTSET", "ENG")))),
                       G("Streams",
                         stream("1", 220.0, 20.0, 400.0, 36000.0),
                         stream("2", 220.0, 20.0, 1200.0, 110000.0),
                         stream("3", 218.83, 16.0, 200.0, 19605.0),
                         stream("4", 325.17, 20.2, 1400.0, 127000.0)),
                       G("Blocks", b1)))


class _FakeApp:
    def __init__(self, tree):
        self._tree = tree

    def RootModel(self, _=""):
        return self._tree


@pytest.fixture
def fake_aspen(monkeypatch):
    """Point the shared aspen bridge at the fake mch tree; run calls inline."""
    from aspen_mcp.com_bridge import aspen
    monkeypatch.setattr(aspen, "_app", _FakeApp(mch_tree()), raising=False)
    monkeypatch.setattr(aspen, "call", lambda fn: fn())
    return aspen
