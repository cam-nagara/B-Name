"""Blender 実機用: 新 pNNNN/cNN 構造の最小 E2E チェック."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _mainfile() -> Path:
    return Path(bpy.data.filepath).resolve()


def main() -> None:
    mod = _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bname_restructure_"))
    work_dir = temp_root / "R5_Test.bname"
    try:
        result = bpy.ops.bname.work_new(filepath=str(work_dir))
        assert result == {"FINISHED"}, result
        assert (work_dir / "work.blend").exists()
        assert not (work_dir / "pages").exists()
        assert (work_dir / "p0001" / "page.json").exists()
        assert (work_dir / "p0001" / "c01" / "c01.json").exists()

        result = bpy.ops.bname.page_add()
        assert result == {"FINISHED"}, result
        assert (work_dir / "p0002" / "page.json").exists()
        assert (work_dir / "p0002" / "c01" / "c01.json").exists()

        result = bpy.ops.bname.coma_add()
        assert result == {"FINISHED"}, result
        assert (work_dir / "p0002" / "c02" / "c02.json").exists()

        work = bpy.context.scene.bname_work
        work.active_page_index = 1
        work.pages[1].active_coma_index = 1
        result = bpy.ops.bname.coma_move_to_page(target_page_id="p0001")
        assert result == {"FINISHED"}, result
        moved_meta = json.loads((work_dir / "p0001" / "c02" / "c02.json").read_text(encoding="utf-8"))
        assert moved_meta["id"] == "c02"
        assert moved_meta["comaId"] == "c02"

        result = bpy.ops.bname.pages_merge_spread(left_index=0)
        assert result == {"FINISHED"}, result
        assert (work_dir / "p0001-0002" / "page.json").exists()
        assert not (work_dir / "p0001").exists()
        assert not (work_dir / "p0002").exists()

        result = bpy.ops.bname.pages_split_spread(spread_index=0)
        assert result == {"FINISHED"}, result
        assert (work_dir / "p0001" / "page.json").exists()
        assert (work_dir / "p0002" / "page.json").exists()

        work = bpy.context.scene.bname_work
        work.active_page_index = 0
        work.pages[0].active_coma_index = 0
        result = bpy.ops.bname.enter_coma_mode()
        assert result == {"FINISHED"}, result
        assert _mainfile() == (work_dir / "p0001" / "c01" / "c01.blend").resolve()

        result = bpy.ops.bname.exit_coma_mode()
        assert result == {"FINISHED"}, result
        assert _mainfile() == (work_dir / "work.blend").resolve()
        print("BNAME_RESTRUCTURE_E2E_OK")
    finally:
        try:
            mod.unregister()
        finally:
            bpy.ops.wm.read_factory_settings(use_empty=True)
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
