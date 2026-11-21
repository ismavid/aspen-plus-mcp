"""Safety layer: work on copies, protect originals, and log every change.

What it does
------------
* **Copy on open.** ``open_file`` opens a timestamped working copy of the
  file instead of the original. The copy goes in an ``aspen_mcp_work`` folder
  next to the original, or in ``~/aspen_mcp_work`` when that folder is not
  writable (for example Aspen's own examples under Program Files).
* **Protected saves.** ``save`` without a path saves the working copy. Saving
  over the original file, or anywhere under a protected folder, is refused.
  A document attached from the GUI *is* the user's original, so a plain
  ``save`` on it is refused unless ``allow_overwrite=True``.
* **Change log.** Every write tool (set_*, add_*, remove_*, connect, run,
  save ...) appends one JSON line to ``<working copy>.changes.jsonl`` with the
  time, tool, arguments, result and, for value edits, the old value and unit.

Settings (environment variables)
--------------------------------
ASPEN_MCP_COPY_ON_OPEN   "0" turns copy-on-open off (default on).
ASPEN_MCP_WORKDIR        folder for working copies (overrides the default).
ASPEN_MCP_PROTECTED      extra protected folders, separated by os.pathsep.

This module does not import win32com, so it is tested offline.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

WORK_DIR_NAME = "aspen_mcp_work"
_COMPANION_EXTS = (".appdf", ".ads", ".his", ".def", ".for")

# Tools that change the simulation (or its files) and so get logged.
WRITE_TOOLS = frozenset({
    # lifecycle
    "open_file", "attach", "close_file", "save", "new_simulation",
    "run", "run_async", "reinit", "reinit_and_run", "run_script",
    # streams
    "set_stream_param", "set_stream_composition", "set_stream_composition_batch",
    "add_stream", "remove_stream", "connect", "connect_port", "disconnect",
    # blocks
    "set_param", "add_block", "remove_block", "configure_fsplit", "set_label",
    "fill_trivial_params",
    # components, properties, reactions
    "add_component", "remove_component", "set_property_method",
    "add_reaction_set", "remove_reaction_set", "add_reaction", "remove_reaction",
    "setup_block_reaction",
    # generic tree writes
    "set_value", "insert_row", "set_tear_estimate",
    # columns and internals
    "add_side_duty", "remove_side_duty", "set_column_stages", "set_condenser_type",
    "set_reboiler_type", "set_feed_stage", "set_product_stage", "set_column_pressure",
    "add_column_internals", "add_internals_section", "remove_internals_section",
    "configure_tray_section", "configure_packed_section", "set_internals_param",
    "resize_column_stages",
    # flowsheeting objects
    "add_design_spec", "add_calculator", "remove_flowsheeting_object",
    # utilities
    "add_utility", "batch_add_utilities", "remove_utility",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def copy_on_open_enabled() -> bool:
    return os.environ.get("ASPEN_MCP_COPY_ON_OPEN", "1").strip().lower() not in ("0", "false", "no", "off")


def protected_dirs() -> list[str]:
    dirs = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        val = os.environ.get(var)
        if val:
            dirs.append(val)
    extra = os.environ.get("ASPEN_MCP_PROTECTED", "")
    dirs.extend(p for p in extra.split(os.pathsep) if p.strip())
    return [_norm(d) for d in dirs]


def is_protected(path: str | os.PathLike) -> bool:
    target = _norm(path)
    for d in protected_dirs():
        if target == d or target.startswith(d.rstrip("\\/") + os.sep):
            return True
    return False


def _writable_dir(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".aspen_mcp_write_test"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


def work_dir_for(original: str | os.PathLike) -> Path:
    """Pick the folder that holds working copies for *original*."""
    env = os.environ.get("ASPEN_MCP_WORKDIR")
    if env:
        d = Path(env)
        d.mkdir(parents=True, exist_ok=True)
        return d
    sibling = Path(original).resolve().parent / WORK_DIR_NAME
    if not is_protected(sibling) and _writable_dir(sibling):
        return sibling
    home = Path.home() / WORK_DIR_NAME
    home.mkdir(parents=True, exist_ok=True)
    return home


def is_working_copy(path: str | os.PathLike) -> bool:
    p = Path(path)
    return p.parent.name == WORK_DIR_NAME and "__work_" in p.stem


def make_working_copy(original: str | os.PathLike, stamp: str | None = None) -> Path:
    """Copy *original* (and same-stem companion files) into the work folder."""
    src = Path(original).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"File not found: {src}")
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    dest_dir = work_dir_for(src)
    dest = dest_dir / f"{src.stem}__work_{stamp}{src.suffix}"
    shutil.copy2(src, dest)
    for ext in _COMPANION_EXTS:
        comp = src.with_suffix(ext)
        if comp.is_file():
            shutil.copy2(comp, dest.with_suffix(ext))
    return dest


@dataclass
class SafetyState:
    original: str | None = None      # file the user pointed at
    working: str | None = None       # file actually open in Aspen
    attached: bool = False           # bound to the user's own GUI document
    log_path: str | None = None
    history: list[dict] = field(default_factory=list)

    def reset(self) -> None:
        self.original = self.working = self.log_path = None
        self.attached = False

    # ── open / attach ────────────────────────────────────────────────
    def prepare_open(self, path: str) -> str:
        """Return the path Aspen should open (a working copy when enabled)."""
        path = os.path.abspath(path)
        if copy_on_open_enabled() and not is_working_copy(path):
            working = str(make_working_copy(path))
        else:
            working = path
        self.original, self.working, self.attached = path, working, False
        self.log_path = working + ".changes.jsonl"
        return working

    def mark_attached(self, path: str | None) -> None:
        self.original = self.working = os.path.abspath(path) if path else None
        self.attached = True
        self.log_path = (self.working + ".changes.jsonl") if self.working else None

    # ── save checks ──────────────────────────────────────────────────
    def check_save(self, target: str | None, allow_overwrite: bool = False) -> str | None:
        """Return an error message if saving to *target* is not allowed, else None."""
        dest = _norm(target) if target else (_norm(self.working) if self.working else None)
        if dest is None:
            return None
        if is_protected(dest):
            return (f"Refused: {dest} is inside a protected folder. "
                    "Save to a path outside Program Files / ASPEN_MCP_PROTECTED.")
        if allow_overwrite:
            return None
        if self.attached and not target:
            return ("Refused: this document is your own file open in the Aspen GUI. "
                    "Use save(file_path=...) to save a copy, or save(allow_overwrite=True) "
                    "to overwrite your original.")
        if target and self.original and dest == _norm(self.original) and self.original != self.working:
            return ("Refused: that path is the original file. Saving goes to the working copy "
                    f"({self.working}); pass allow_overwrite=True to overwrite the original.")
        return None

    # ── change log ───────────────────────────────────────────────────
    def log(self, tool: str, args: dict[str, Any], result: Any,
            old: dict[str, Any] | None = None, error: str | None = None) -> dict:
        entry: dict[str, Any] = {"ts": _now(), "tool": tool, "args": args}
        if old:
            entry["old"] = old
        if error:
            entry["error"] = error
        entry["result"] = result if isinstance(result, (str, int, float, bool, type(None))) else str(result)
        if isinstance(entry["result"], str) and len(entry["result"]) > 500:
            entry["result"] = entry["result"][:500] + "…"
        if self.original:
            entry["original"] = self.original
        if self.working:
            entry["working"] = self.working
        self.history.append(entry)
        if self.log_path:
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            except OSError:
                pass
        return entry

    def read_log(self, limit: int = 50) -> list[dict]:
        if self.log_path and os.path.isfile(self.log_path):
            with open(self.log_path, encoding="utf-8") as f:
                lines = [json.loads(line) for line in f if line.strip()]
            return lines[-limit:]
        return self.history[-limit:]

    def status(self) -> dict[str, Any]:
        return {
            "copy_on_open": copy_on_open_enabled(),
            "original": self.original,
            "working": self.working,
            "attached_to_gui": self.attached,
            "change_log": self.log_path,
            "protected_dirs": protected_dirs(),
        }


state = SafetyState()


def logged(fn: Callable, old_reader: Callable[[str, dict], dict | None] | None = None,
           st: SafetyState | None = None) -> Callable:
    """Wrap an MCP tool so each call is written to the change log.

    *old_reader(tool_name, kwargs)* may return {"value": ..., "unit": ...}
    read before the call, so the log shows old → new.
    """
    name = fn.__name__

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        s = st or state
        import inspect
        try:
            bound = inspect.signature(fn).bind_partial(*args, **kwargs)
            call_args = {k: v for k, v in bound.arguments.items()}
        except TypeError:
            call_args = {"args": list(args), **kwargs}
        old = None
        if old_reader is not None:
            try:
                old = old_reader(name, call_args)
            except Exception:
                old = None
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            s.log(name, call_args, None, old, error=str(exc))
            raise
        s.log(name, call_args, result, old)
        return result

    return wrapper
