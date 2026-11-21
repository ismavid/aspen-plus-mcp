"""Offline tests for flowsheet block positions (.bkp PFSVData editing).

tests/data/mch_pfs.bkp.txt reproduces the graphics section of mch.bkp as saved by
Aspen Plus V15 (one RadFrac B1, feeds 1 and 2, products 3 and 4), CRLF endings.
"""

from pathlib import Path

import pytest

from aspen_mcp.tools import layout as lo

DATA = Path(__file__).parent / "data" / "mch_pfs.bkp.txt"   # .txt because *.bkp is git-ignored
CONN = {"1": {"source": None, "destination": "B1"}, "2": {"source": None, "destination": "B1"},
        "3": {"source": "B1", "destination": None}, "4": {"source": "B1", "destination": None}}


def _text():
    return DATA.read_bytes().decode("ascii")


def test_parse_real_v15_section():
    lay = lo.parse_layout(_text())
    assert set(lay.blocks) == {"B1"}
    assert lay.blocks["B1"].at == (1.8, 0.0) and lay.blocks["B1"].icon == "FRACT1"
    assert set(lay.streams) == {"1", "2", "3", "4"}
    s4 = lay.streams["4"]
    assert s4.terminal == 2 and s4.at == (5.2, -0.825)
    assert len(s4.routes) == 1 and len(s4.routes[0]) == 5     # empty ROUTE groups dropped
    summary = lo.layout_summary(lay)
    assert summary["streams"]["1"]["free_end"] == "feed"


def test_round_trip_is_byte_identical():
    text = _text()
    assert lo.parse_layout(text).text() == text


def test_move_block_moves_terminal_streams_rigidly():
    lay = lo.parse_layout(_text())
    notes = lo.move_blocks(lay, {"b1": (3.8, 0.5)}, CONN)
    out = lay.text()
    assert "At 3.800000 0.500000" in out
    assert "t l 3.574038 0.500000 0" in out          # stream 1 port end follows
    assert "r r 0.700000 0.500000 0" in out          # and its free end moves too
    assert "Label At 0.004954 0.377386" in out       # relative offsets untouched
    assert "\r\n" in out and "\n" not in out.replace("\r\n", "")
    assert "At 9.000000 9.000000" in out             # nothing after PFSVData touched
    assert any("B1: (1.8, 0) -> (3.8, 0.5)" in n for n in notes)
    assert out.split("\r\n")[8] == "SIZE -1.3 7.2 -1 1.02615"   # box grows to fit


def test_move_terminals_false_keeps_free_ends():
    lay = lo.parse_layout(_text())
    lo.move_blocks(lay, {"B1": (1.0, 0.0)}, CONN, relative=True, move_terminals=False)
    s1 = lay.streams["1"]
    pts = [(p.x, p.y) for p in s1.routes[0]]
    assert pts[0] == (-1.3, 0.0)                       # feed end stays
    assert pts[-1] == pytest.approx((2.574038, 0.0))   # port end follows
    assert s1.at == (-1.3, 0.0)


def test_vertical_move_keeps_end_segment_orthogonal():
    lay = lo.parse_layout(_text())
    lo.move_blocks(lay, {"B1": (0.0, 1.0)}, CONN, relative=True, move_terminals=False)
    r = lay.streams["4"].routes[0]                      # product end -> port at the end
    assert r[-1].y == pytest.approx(r[-2].y)            # last segment still horizontal
    assert r[0].y == pytest.approx(-0.825)              # free end stays


def test_internal_stream_moves_only_the_moved_blocks_end():
    text = _text().replace("SIZE -1.3 5.2 -1 1", "SIZE -1.3 9 -1 1")
    extra = "\r\n".join(["BLOCK", "ID: B2", "Section GLOBAL", "At 7.000000 0.526154",
                         "Label At 0 0.3", "STREAM", "ID: 5", "TYPE 0 TERMINAL 0",
                         "At 0.000000 0.000000", "Label At 0 0", "ROUTE 0 0",
                         "t r 2.247885 0.526154 0", "x 0 4.000000 0.526154 0",
                         "t l 6.800000 0.526154 0", "$ $ 0.0 0.0", "LEGEND"])
    text = text.replace("LEGEND", extra, 1)
    lay = lo.parse_layout(text)
    conn = {"5": {"source": "B1", "destination": "B2"}}
    lo.move_blocks(lay, {"B2": (1.0, 0.0)}, conn, relative=True)
    pts = [(p.x, p.y) for p in lay.streams["5"].routes[0]]
    assert pts[0] == pytest.approx((2.247885, 0.526154))   # B1 end unchanged
    assert pts[-1] == pytest.approx((7.8, 0.526154))       # B2 end moved


def test_unknown_block_and_missing_section():
    with pytest.raises(lo.LayoutError, match="not on the flowsheet"):
        lo.move_blocks(lo.parse_layout(_text()), {"B9": (0, 0)}, CONN)
    with pytest.raises(lo.LayoutError, match="No PFSVData"):
        lo.parse_layout("MM \"41.0\"\r\n")


def test_move_blocks_registered_as_write_tool():
    from aspen_mcp import safety
    assert "move_blocks" in safety.WRITE_TOOLS


def _segments_orthogonal(route):
    for a, b in zip(route, route[1:]):
        if abs(a.x - b.x) > 1e-6 and abs(a.y - b.y) > 1e-6:
            return False
    return True


def test_fixed_free_ends_keep_routes_orthogonal():
    """Live V15 run showed diagonals when move_terminals=False; routes must stay x/y."""
    lay = lo.parse_layout(_text())
    lo.move_blocks(lay, {"B1": (0.0, -1.0)}, CONN, relative=True, move_terminals=False)
    for sid in "1234":
        r = lay.streams[sid].routes[0]
        assert _segments_orthogonal(r), (sid, [(p.x, p.y, p.codes) for p in r])
    s1 = lay.streams["1"].routes[0]                     # 3 points -> jog inserted
    assert [p.codes for p in s1] == [("r", "r"), ("x", "y"), ("y", "x"), ("t", "l")]
    assert (s1[0].x, s1[0].y) == (-1.3, 0.0) and s1[-1].y == pytest.approx(-1.0)
    s4 = lay.streams["4"].routes[0]                     # existing vertical leg extended
    assert len(s4) == 5 and s4[2].y == pytest.approx(-1.826154)
    s3 = lay.streams["3"].routes[0]                     # product route listed from its free end
    assert (s3[0].x, s3[0].y) == (5.1, 0.526154) and s3[-1].y == pytest.approx(-0.473846)
    assert lo.parse_layout(lay.text()).streams["1"].routes[0][1].codes == ("x", "y")
