"""Flowsheet layout: read and move block positions.

Block positions are not in the COM tree. They are stored only in the .bkp text,
in the PFSVData section that follows GRAPHICS_BACKUP (probed on V15, 2026-11-21):

    BLOCK
    ID: B1
    ICON: "FRACT1"
    Section GLOBAL
    At 1.800000 0.000000          <- block position (x y)
    Label At 0.004954 0.377386    <- label offset relative to At
    STREAM
    ID: 1
    TYPE 0 TERMINAL 1             <- 1 = feed end free, 2 = product end free
    At -1.300000 0.000000         <- the stream's free (terminal) end
    Label At 1.091444 0.000000    <- label offset relative to At
    ROUTE 1 0
    r r -1.300000 0.000000 0      <- route points: <code> <code> x y 0
    x x 1.374038 0.000000 0
    t l 1.574038 0.000000 0       <- "t" point = the block port
    $ D 0.000000 -0.000000        <- "$" lines close the route
    $ C 0.000000 0.0
    $ $ 0.0 0.0

Aspen keeps an edited "At" through InitFromArchive2 + SaveAs, but does not
re-route streams. So moving a block here also moves the ends of the streams
attached to it:

* a feed or product stream (one end free) moves rigidly with the block, unless
  move_terminals=False, in which case only its block end moves;
* a stream between two blocks has its block-side end point moved, and the
  neighbouring point adjusted so the last segment stays horizontal/vertical.

The text functions are pure and tested offline. The COM tool saves the working
copy as .bkp, closes it, edits the text, and reopens it.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from typing import Any

_NUM = r"-?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?"
_AT_RE = re.compile(rf"^At ({_NUM}) ({_NUM})\s*$")
_POINT_RE = re.compile(rf"^(\S+) (\S+) ({_NUM}) ({_NUM}) (\S+)\s*$")
_SIZE_RE = re.compile(rf"^SIZE ({_NUM}) ({_NUM}) ({_NUM}) ({_NUM})\s*$")
_OBJ_HEADERS = ("BLOCK", "STREAM", "LEGEND")


class LayoutError(ValueError):
    pass


def _fmt(v: float) -> str:
    return f"{v:.6f}"


@dataclass
class RoutePoint:
    line: int
    codes: tuple[str, str]
    x: float
    y: float
    tail: str


@dataclass
class Stream:
    id: str
    terminal: int | None = None
    at_line: int | None = None
    at: tuple[float, float] | None = None
    routes: list[list[RoutePoint]] = field(default_factory=list)


@dataclass
class Block:
    id: str
    icon: str | None = None
    section: str | None = None
    at_line: int | None = None
    at: tuple[float, float] | None = None


@dataclass
class Layout:
    lines: list[str]          # lines without line endings
    newline: str
    trailing_newline: bool
    blocks: dict[str, Block]
    streams: dict[str, Stream]
    size_line: int | None

    def text(self) -> str:
        out = self.newline.join(self.lines)
        return out + self.newline if self.trailing_newline else out


def parse_layout(text: str) -> Layout:
    """Parse the PFSVData section of a .bkp file's text."""
    newline = "\r\n" if "\r\n" in text else "\n"
    trailing = text.endswith(newline)
    lines = text.split(newline)
    if trailing:
        lines = lines[:-1]
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == "PFSVData")
    except StopIteration:
        raise LayoutError("No PFSVData graphics section in this file (is it a .bkp saved by Aspen Plus?)")

    blocks: dict[str, Block] = {}
    streams: dict[str, Stream] = {}
    size_line = None
    kind = None
    cur: Any = None
    route: list[RoutePoint] | None = None

    for i in range(start + 1, len(lines)):
        ln = lines[i].strip()
        if ln.startswith("$_") and ln.endswith("FILE"):   # $_SUMMARY_FILE, $_ADS_FILE
            break
        if ln == "LAYOUT" or ln.startswith("VIEWPORT"):
            kind, cur, route = None, None, None
            continue
        if ln in _OBJ_HEADERS:
            kind, cur, route = ln, None, None
            continue
        if size_line is None and _SIZE_RE.match(ln):
            size_line = i
            continue
        if kind == "BLOCK":
            if ln.startswith("ID:"):
                cur = Block(ln[3:].strip())
                blocks[cur.id] = cur
            elif cur is None:
                continue
            elif ln.startswith("ICON:"):
                cur.icon = ln[5:].strip().strip('"')
            elif ln.startswith("Section "):
                cur.section = ln[8:].strip()
            elif cur.at_line is None and (m := _AT_RE.match(ln)):
                cur.at_line, cur.at = i, (float(m[1]), float(m[2]))
        elif kind == "STREAM":
            if ln.startswith("ID:"):
                cur = Stream(ln[3:].strip())
                streams[cur.id] = cur
            elif cur is None:
                continue
            elif ln.startswith("TYPE ") and "TERMINAL" in ln:
                try:
                    cur.terminal = int(ln.split("TERMINAL")[1].split()[0])
                except (IndexError, ValueError):
                    cur.terminal = None
            elif cur.at_line is None and (m := _AT_RE.match(ln)):
                cur.at_line, cur.at = i, (float(m[1]), float(m[2]))
            elif ln.startswith("ROUTE "):
                route = []
                cur.routes.append(route)
            elif route is not None and not ln.startswith("$") and (m := _POINT_RE.match(ln)):
                route.append(RoutePoint(i, (m[1], m[2]), float(m[3]), float(m[4]), m[5]))
            elif ln.startswith("$"):
                route = None
    for s in streams.values():
        s.routes = [r for r in s.routes if r]
    return Layout(lines, newline, trailing, blocks, streams, size_line)


def layout_summary(layout: Layout) -> dict[str, Any]:
    """Plain dict of block and stream positions."""
    return {
        "blocks": {b.id: {"x": b.at[0], "y": b.at[1], "icon": b.icon}
                   for b in layout.blocks.values() if b.at},
        "streams": {s.id: {"end_x": s.at[0], "end_y": s.at[1],
                           "free_end": {1: "feed", 2: "product"}.get(s.terminal, "none"),
                           "route_points": [[p.x, p.y] for r in s.routes for p in r]}
                    for s in layout.streams.values() if s.at},
        "units": "Aspen flowsheet drawing units (x to the right, y up)",
    }


# ── editing ──────────────────────────────────────────────────────────────
def _write_at(layout: Layout, line: int, x: float, y: float) -> None:
    indent = layout.lines[line][: len(layout.lines[line]) - len(layout.lines[line].lstrip())]
    layout.lines[line] = f"{indent}At {_fmt(x)} {_fmt(y)}"


def _write_point(layout: Layout, p: RoutePoint) -> None:
    raw = layout.lines[p.line]
    indent = raw[: len(raw) - len(raw.lstrip())]
    layout.lines[p.line] = f"{indent}{p.codes[0]} {p.codes[1]} {_fmt(p.x)} {_fmt(p.y)} {p.tail}"


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _shift_stream(layout: Layout, s: Stream, dx: float, dy: float) -> None:
    for r in s.routes:
        for p in r:
            p.x += dx
            p.y += dy
            _write_point(layout, p)
    if s.at and s.at_line is not None:
        s.at = (s.at[0] + dx, s.at[1] + dy)
        _write_at(layout, s.at_line, *s.at)


def _shift_block_end(layout: Layout, s: Stream, old_block_at: tuple[float, float],
                     dx: float, dy: float, other_end: tuple[float, float] | None) -> bool:
    """Move the route end that touches the block. Returns False if none found."""
    best = None   # (distance, route, index)
    for r in s.routes:
        for idx in {0, len(r) - 1}:
            p = r[idx]
            d = _dist((p.x, p.y), old_block_at)
            if other_end is not None and _dist((p.x, p.y), other_end) < d:
                continue      # this end belongs to the other block / terminal
            if best is None or d < best[0]:
                best = (d, r, idx)
    if best is None:
        return False
    _, r, idx = best
    end = r[idx]
    if len(r) >= 3:
        nb = r[1] if idx == 0 else r[-2]
        eps = 1e-6
        if abs(nb.y - end.y) < eps:      # horizontal end segment: keep it horizontal
            nb.y += dy
        elif abs(nb.x - end.x) < eps:    # vertical end segment: keep it vertical
            nb.x += dx
        _write_point(layout, nb)
    end.x += dx
    end.y += dy
    _write_point(layout, end)
    return True


def _expand_size(layout: Layout) -> None:
    if layout.size_line is None:
        return
    m = _SIZE_RE.match(layout.lines[layout.size_line].strip())
    if not m:
        return
    xmin, xmax, ymin, ymax = (float(v) for v in m.groups())
    pts = [b.at for b in layout.blocks.values() if b.at]
    pts += [s.at for s in layout.streams.values() if s.at]
    if not pts:
        return
    nx = min([xmin] + [p[0] for p in pts])
    xx = max([xmax] + [p[0] for p in pts])
    ny = min([ymin] + [p[1] for p in pts])
    xy = max([ymax] + [p[1] for p in pts])
    if (nx, xx, ny, xy) != (xmin, xmax, ymin, ymax):
        layout.lines[layout.size_line] = f"SIZE {nx:g} {xx:g} {ny:g} {xy:g}"


def move_blocks(layout: Layout, moves: dict[str, tuple[float, float]],
                connections: dict[str, dict[str, Any]], relative: bool = False,
                move_terminals: bool = True) -> list[str]:
    """Apply block moves to *layout* in place and return notes.

    moves: {block: (x, y)} absolute, or (dx, dy) when relative=True.
    connections: {stream: {"source": block|None, "destination": block|None}},
      from the COM ports (see connectivity.stream_connections).
    """
    notes: list[str] = []
    upper = {k.upper(): k for k in layout.blocks}
    for name, (vx, vy) in moves.items():
        key = upper.get(str(name).upper())
        if key is None:
            raise LayoutError(f"Block '{name}' is not on the flowsheet drawing. "
                              f"Blocks: {', '.join(sorted(layout.blocks)) or 'none'}")
        b = layout.blocks[key]
        if b.at is None or b.at_line is None:
            raise LayoutError(f"Block '{key}' has no position line in the file.")
        old = b.at
        dx, dy = (vx, vy) if relative else (vx - old[0], vy - old[1])
        b.at = (old[0] + dx, old[1] + dy)
        _write_at(layout, b.at_line, *b.at)
        notes.append(f"{key}: ({old[0]:g}, {old[1]:g}) -> ({b.at[0]:g}, {b.at[1]:g})")

        for sid, conn in connections.items():
            src, dst = conn.get("source"), conn.get("destination")
            if key not in (src, dst):
                continue
            s = layout.streams.get(sid)
            if s is None or not s.routes:
                notes.append(f"  stream {sid}: no drawn route, left as is")
                continue
            other = dst if src == key else src
            if other is None or other == key:
                if move_terminals:
                    _shift_stream(layout, s, dx, dy)
                    notes.append(f"  stream {sid}: moved with the block")
                    continue
                other_end = s.at
            else:
                ob = layout.blocks.get(other)
                other_end = ob.at if ob else None
            if _shift_block_end(layout, s, old, dx, dy, other_end):
                notes.append(f"  stream {sid}: end at {key} moved")
            else:
                notes.append(f"  stream {sid}: could not find its end at {key}; re-route it in the GUI")
    _expand_size(layout)
    return notes


# ── COM tools ────────────────────────────────────────────────────────────
def _bkp_path(working: str) -> str:
    root, ext = os.path.splitext(working)
    return working if ext.lower() == ".bkp" else root + ".bkp"


def _read(path: str) -> str:
    with open(path, encoding="ascii", errors="surrogateescape", newline="") as f:
        return f.read()


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="ascii", errors="surrogateescape", newline="") as f:
        f.write(text)


def _save_working_bkp() -> str:
    """SaveAs the working copy to .bkp so its text reflects the current state."""
    from ..com_bridge import aspen
    from .. import safety
    st = safety.state
    if not st.working:
        raise LayoutError("No simulation is open. Use open_file first.")
    target = _bkp_path(st.working)
    err = st.check_save(target)
    if err:
        raise LayoutError(err)
    aspen.save(target)
    st.working = target
    return target


def _connections() -> dict[str, dict[str, Any]]:
    from ..com_bridge import aspen
    from .streams import _get_block_ports_on_com
    from .connectivity import stream_connections
    ports = aspen.call(_get_block_ports_on_com) or []
    names = {s for p in ports for s in p.get("streams", [])}
    out = {}
    for s in names:
        c = stream_connections(ports, s)
        out[s] = {"source": (c["source"] or {}).get("block"),
                  "destination": (c["destination"] or {}).get("block")}
    return out


def tool_get_layout() -> dict:
    """Return block and stream positions from the flowsheet drawing."""
    from .. import safety
    st = safety.state
    try:
        if st.attached:
            if st.working and st.working.lower().endswith(".bkp") and os.path.isfile(st.working):
                out = layout_summary(parse_layout(_read(st.working)))
                out["note"] = "Read from the file on disk (positions as of its last save)."
                return out
            return {"error": "This is your own GUI document; positions can only be read from a .bkp "
                             "file. Save it as .bkp in the GUI, or open_file it to work on a copy."}
        path = _save_working_bkp()
        out = layout_summary(parse_layout(_read(path)))
        out["file"] = path
        return out
    except Exception as exc:
        return {"error": str(exc)}


def tool_move_blocks(moves: dict[str, list[float]], relative: bool = False,
                     move_terminals: bool = True) -> str:
    """Move blocks on the flowsheet drawing (saves, edits the .bkp, reopens)."""
    from ..com_bridge import aspen
    from .. import safety
    st = safety.state
    if st.attached:
        return ("Refused: this is your own document open in the Aspen GUI. Moving blocks needs a "
                "close and reopen; open_file the file to work on a copy instead.")
    try:
        parsed = {}
        for k, v in (moves or {}).items():
            if not isinstance(v, (list, tuple)) or len(v) != 2:
                raise LayoutError(f"Move for '{k}' must be [x, y]{' offsets' if relative else ''}.")
            parsed[k] = (float(v[0]), float(v[1]))
        if not parsed:
            raise LayoutError("No moves given. Example: {\"B1\": [3.0, 0.5]}")
        conns = _connections()
        path = _save_working_bkp()
        layout = parse_layout(_read(path))
        notes = move_blocks(layout, parsed, conns, relative, move_terminals)   # validate before closing
    except Exception as exc:
        return "Error: " + str(exc)

    aspen.close_file()
    try:
        _write(path, layout.text())
    finally:
        aspen.open_file(path)
        st.working = path
    return ("Moved on the flowsheet drawing (working copy reopened: " + path + ").\n"
            + "\n".join(notes)
            + "\nResults were reloaded from the file; run again if you need fresh results.")
