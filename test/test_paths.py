from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_paths():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("bname_paths", root / "utils" / "paths.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_page_and_coma_ids():
    paths = _load_paths()
    assert paths.format_page_id(1) == "p0001"
    assert paths.format_spread_id(20, 21) == "p0020-0021"
    assert paths.validate_page_id("p9999") == "p9999"
    assert paths.format_coma_id(1) == "c01"
    assert paths.format_coma_id(99) == "c99"
    assert paths.validate_coma_id("c01") == "c01"
    assert not paths.is_valid_coma_id("c00")
    assert not paths.is_valid_coma_id("c100")


def test_invalid_ids_raise():
    paths = _load_paths()
    for value in (0, 10000):
        try:
            paths.format_page_id(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"format_page_id({value}) did not raise")
    for value in (0, 100):
        try:
            paths.format_coma_id(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"format_coma_id({value}) did not raise")


def test_new_layout_paths():
    paths = _load_paths()
    root = Path("D:/work/Test.bname")
    assert paths.page_dir(root, "p0001") == root / "p0001"
    assert paths.coma_dir(root, "p0001", "c01") == root / "p0001" / "c01"
    assert paths.coma_blend_path(root, "p0001", "c01") == root / "p0001" / "c01" / "c01.blend"
    assert paths.coma_json_path(root, "p0001", "c01") == root / "p0001" / "c01" / "c01.json"
    assert paths.coma_thumb_path(root, "p0001", "c01") == root / "p0001" / "c01" / "c01_thumb.png"
    assert paths.coma_preview_path(root, "p0001", "c01") == root / "p0001" / "c01" / "c01_preview.png"
    assert paths.coma_passes_dir(root, "p0001", "c01") == root / "p0001" / "c01" / "passes"
    assert paths.coma_passes_cube_dir(root, "p0001", "c01") == root / "p0001" / "c01" / "passes" / "cube"


if __name__ == "__main__":
    test_page_and_coma_ids()
    test_invalid_ids_raise()
    test_new_layout_paths()
