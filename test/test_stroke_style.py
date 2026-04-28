from __future__ import annotations

import sys
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("stroke_style", ROOT / "utils" / "stroke_style.py")
stroke_style = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
spec.loader.exec_module(stroke_style)


def test_dashed_segments_leave_gaps():
    parts = stroke_style.styled_segments_for_line((0.0, 0.0), (20.0, 0.0), 1.0, "dashed")
    assert len(parts) >= 3
    assert parts[0][0] == (0.0, 0.0)
    assert parts[0][1][0] < parts[1][0][0]


def test_dotted_segments_are_shorter_than_spacing():
    parts = stroke_style.styled_segments_for_line((0.0, 0.0), (10.0, 0.0), 1.0, "dotted")
    assert len(parts) >= 5
    first_length = parts[0][1][0] - parts[0][0][0]
    assert 0.0 < first_length <= 1.0
    assert parts[1][0][0] - parts[0][0][0] >= 1.0


def test_double_segments_offset_and_use_inner_width():
    parts = stroke_style.styled_segments_for_line((0.0, 0.0), (10.0, 0.0), 2.0, "double")
    assert len(parts) == 2
    assert parts[0][0][1] != parts[1][0][1]
    assert parts[0][2] < 2.0


def test_closed_path_includes_last_to_first_segment():
    parts = stroke_style.styled_segments_for_path(
        [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)],
        1.0,
        "solid",
        closed=True,
    )
    assert len(parts) == 3
    assert parts[-1][1] == (0.0, 0.0)


if __name__ == "__main__":
    test_dashed_segments_leave_gaps()
    test_dotted_segments_are_shorter_than_spacing()
    test_double_segments_offset_and_use_inner_width()
    test_closed_path_includes_last_to_first_segment()
