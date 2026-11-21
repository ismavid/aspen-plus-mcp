import json
import os

import pytest

from aspen_mcp import safety
from aspen_mcp.safety import SafetyState, is_working_copy, logged, make_working_copy


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("ASPEN_MCP_WORKDIR", raising=False)
    monkeypatch.delenv("ASPEN_MCP_COPY_ON_OPEN", raising=False)
    monkeypatch.setenv("ASPEN_MCP_PROTECTED", str(tmp_path / "locked"))
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    case = tmp_path / "cases"
    case.mkdir()
    bkp = case / "mch.bkp"
    bkp.write_text("ORIGINAL")
    return tmp_path, bkp


def test_working_copy_goes_next_to_original(env):
    _, bkp = env
    copy = make_working_copy(bkp, stamp="20261121-010000")
    assert copy == bkp.parent / "aspen_mcp_work" / "mch__work_20261121-010000.bkp"
    assert copy.read_text() == "ORIGINAL"
    assert is_working_copy(copy) and not is_working_copy(bkp)


def test_protected_original_copies_to_home(env):
    tmp, _ = env
    locked = tmp / "locked" / "examples"
    locked.mkdir(parents=True)
    src = locked / "flash.bkp"
    src.write_text("X")
    copy = make_working_copy(src)
    assert copy.parent == tmp / "home" / "aspen_mcp_work"
    assert not (locked / "aspen_mcp_work").exists()


def test_apw_companions_are_copied(env):
    _, bkp = env
    apw = bkp.with_suffix(".apw")
    apw.write_text("APW")
    apw.with_suffix(".appdf").write_text("PDF")
    copy = make_working_copy(apw, stamp="s")
    assert copy.with_suffix(".appdf").read_text() == "PDF"


def test_prepare_open_and_save_rules(env):
    tmp, bkp = env
    st = SafetyState()
    working = st.prepare_open(str(bkp))
    assert working != str(bkp) and os.path.isfile(working)
    assert st.check_save(None) is None                         # save working copy: ok
    assert "original" in st.check_save(str(bkp))               # over original: refused
    assert st.check_save(str(bkp), allow_overwrite=True) is None
    assert "protected" in st.check_save(str(tmp / "locked" / "x.bkp"), allow_overwrite=True)
    assert st.check_save(str(tmp / "elsewhere.bkp")) is None   # save-as elsewhere: ok


def test_reopening_a_working_copy_does_not_copy_again(env):
    _, bkp = env
    st = SafetyState()
    w1 = st.prepare_open(str(bkp))
    w2 = st.prepare_open(w1)
    assert w2 == w1


def test_copy_on_open_can_be_disabled(env, monkeypatch):
    _, bkp = env
    monkeypatch.setenv("ASPEN_MCP_COPY_ON_OPEN", "0")
    st = SafetyState()
    assert st.prepare_open(str(bkp)) == str(bkp)
    assert "original" not in (st.check_save(None) or "")


def test_attached_document_needs_explicit_overwrite(env):
    _, bkp = env
    st = SafetyState()
    st.mark_attached(str(bkp))
    assert "GUI" in st.check_save(None)
    assert st.check_save(None, allow_overwrite=True) is None
    assert st.check_save(str(bkp.parent / "copy.bkp")) is None


def test_logged_writes_jsonl_with_old_value(env):
    _, bkp = env
    st = SafetyState()
    st.prepare_open(str(bkp))

    def set_stream_param(stream_name: str, param: str, value: float, basis: str | None = None) -> str:
        return f"Stream '{stream_name}' {param} = {value} [F]"

    reader = lambda tool, args: {"value": 220.0, "unit": "F"}
    wrapped = logged(set_stream_param, reader, st=st)
    assert wrapped("1", "TEMP", 238.0) == "Stream '1' TEMP = 238.0 [F]"
    assert wrapped.__name__ == "set_stream_param"

    lines = [json.loads(l) for l in open(st.log_path, encoding="utf-8")]
    assert len(lines) == 1
    e = lines[0]
    assert e["tool"] == "set_stream_param"
    assert e["args"] == {"stream_name": "1", "param": "TEMP", "value": 238.0}
    assert e["old"] == {"value": 220.0, "unit": "F"}
    assert e["original"] == str(bkp)
    assert st.read_log(10)[0]["tool"] == "set_stream_param"


def test_logged_records_errors(env):
    st = SafetyState()

    def remove_block(name: str) -> str:
        raise RuntimeError("COM died")

    with pytest.raises(RuntimeError):
        logged(remove_block, st=st)("B1")
    assert st.history[-1]["error"] == "COM died"


def test_server_registers_safety_tools_and_keeps_signatures():
    pytest.importorskip("fastmcp")
    import asyncio
    from aspen_mcp.server import create_server
    mcp = create_server()

    async def list_tools():
        return await mcp.list_tools()

    tools = {t.name: t for t in asyncio.run(list_tools())}
    assert {"safety_status", "get_change_log", "set_stream_param", "save"} <= set(tools)
    params = tools["set_stream_param"].parameters["properties"]
    assert set(params) >= {"stream_name", "param", "value", "basis"}
    assert "allow_overwrite" in tools["save"].parameters["properties"]


def test_open_file_tool_opens_copy_and_save_refuses_original(env, monkeypatch):
    _, bkp = env
    from aspen_mcp.com_bridge import aspen
    from aspen_mcp.tools import simulation
    opened, saved = [], []
    monkeypatch.setattr(aspen, "open_file", lambda p: opened.append(p) or p)
    monkeypatch.setattr(aspen, "save", lambda p=None: saved.append(p))
    monkeypatch.setattr(safety, "state", SafetyState())

    msg = simulation.tool_open_file(str(bkp))
    assert "working copy" in msg and opened[0] != str(bkp)
    assert bkp.read_text() == "ORIGINAL"
    assert "Refused" in simulation.tool_save(str(bkp))
    assert saved == []
    assert simulation.tool_save().startswith("File saved")
    assert saved == [None]
