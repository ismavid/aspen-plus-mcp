"""Flowsheet connectivity: which blocks a stream joins, and a block's inlets/outlets.

Aspen stores connections on the block side: Data\\Blocks\\<B>\\Ports\\<PORT>
holds one child per connected stream. Port names end in "(IN)" or "(OUT)",
for example F(IN), D(OUT), B(OUT), HS(IN). The pure functions here work on
the plain list returned by streams._get_block_ports_on_com(), so they can be
tested without Aspen.
"""

from __future__ import annotations

from typing import Any


def port_direction(port_name: str) -> str | None:
    """Return "in", "out" or None from a port name such as F(IN) or D(OUT)."""
    name = port_name.strip().upper()
    if name.endswith("(IN)"):
        return "in"
    if name.endswith("(OUT)"):
        return "out"
    return None


def stream_connections(ports: list[dict[str, Any]], stream: str) -> dict[str, Any]:
    """Find the source and destination of *stream*.

    Returns {"source": {"block", "port"} | None, "destination": {...} | None,
    "role": "internal" | "feed" | "product" | "unconnected"}.
    A stream with no source block is a flowsheet feed; one with no
    destination block is a product.
    """
    source = None
    destination = None
    for p in ports:
        if stream not in p.get("streams", []):
            continue
        direction = port_direction(p.get("name", ""))
        entry = {"block": p.get("block"), "port": p.get("name")}
        if direction == "out" and source is None:
            source = entry
        elif direction == "in" and destination is None:
            destination = entry
    if source and destination:
        role = "internal"
    elif destination:
        role = "feed"
    elif source:
        role = "product"
    else:
        role = "unconnected"
    return {"source": source, "destination": destination, "role": role}


def block_connections(ports: list[dict[str, Any]], block: str) -> dict[str, list[dict[str, str]]]:
    """List the inlet and outlet streams of *block* as {"stream", "port"} entries."""
    inlets: list[dict[str, str]] = []
    outlets: list[dict[str, str]] = []
    for p in ports:
        if p.get("block") != block:
            continue
        direction = port_direction(p.get("name", ""))
        for s in p.get("streams", []):
            entry = {"stream": s, "port": p.get("name")}
            if direction == "in":
                inlets.append(entry)
            elif direction == "out":
                outlets.append(entry)
    return {"inlets": inlets, "outlets": outlets}
