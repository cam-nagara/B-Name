"""Blender 実機用: レイヤーリストD&D/親子付け/移動追従の挙動確認."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(os.environ.get("BNAME_LAYER_STACK_VISUAL_OUT", "") or tempfile.mkdtemp(prefix="bname_layer_stack_visual_"))


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


def _assert_close(actual: float, expected: float, label: str, eps: float = 1.0e-4) -> None:
    if abs(float(actual) - float(expected)) > eps:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")


def _first_gp_point(layer):
    from bname_dev.utils import gp_layer_parenting as gp_parent

    for point in gp_parent.iter_points(layer):
        return point
    raise AssertionError(f"GP point not found: {getattr(layer, 'name', '')}")


def _gp_point_world_mm(obj, point) -> tuple[float, float]:
    from bname_dev.utils.geom import m_to_mm

    return (
        m_to_mm(float(obj.location.x) + float(point.position.x)),
        m_to_mm(float(obj.location.y) + float(point.position.y)),
    )


def _add_test_gp_layer(context, parent_key: str):
    from bname_dev.utils import gp_layer_parenting as gp_parent
    from bname_dev.utils import gpencil as gp_utils
    from bname_dev.utils.geom import mm_to_m

    obj = gp_utils.ensure_master_gpencil(context.scene)
    layer = obj.data.layers.new("dnd_gp")
    gp_parent.set_parent_key(layer, parent_key)
    frame = gp_utils.ensure_active_frame(layer)
    assert frame is not None and getattr(frame, "drawing", None) is not None
    ok = gp_utils.add_stroke_to_drawing(
        frame.drawing,
        [
            (mm_to_m(25.0), mm_to_m(25.0), 0.0),
            (mm_to_m(35.0), mm_to_m(30.0), 0.0),
        ],
    )
    assert ok
    return obj, layer


def _add_test_raster_layer(context, parent_key: str):
    from bname_dev.operators import raster_layer_op

    result = bpy.ops.bname.raster_layer_add("EXEC_DEFAULT", dpi=30, bit_depth="gray8")
    assert "FINISHED" in result, result
    coll = context.scene.bname_raster_layers
    entry = coll[context.scene.bname_active_raster_layer_index]
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key
    image = raster_layer_op.ensure_raster_image(context, entry, create_missing=False)
    assert image is not None
    width, height = int(image.size[0]), int(image.size[1])
    px, py = min(20, width - 2), min(20, height - 2)
    index = (py * width + px) * 4
    image.pixels[index:index + 4] = (0.0, 0.0, 0.0, 1.0)
    image.update()
    raster_layer_op.mark_raster_dirty(entry)
    return entry, image, (px, py)


def _add_test_text(page, text_id: str, parent_key: str):
    entry = page.texts.add()
    entry.id = text_id
    entry.body = text_id
    entry.x_mm = 22.0
    entry.y_mm = 22.0
    entry.width_mm = 18.0
    entry.height_mm = 14.0
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key
    return entry


def _add_test_balloon(page, balloon_id: str, parent_key: str):
    entry = page.balloons.add()
    entry.id = balloon_id
    entry.shape = "rect"
    entry.x_mm = 48.0
    entry.y_mm = 24.0
    entry.width_mm = 22.0
    entry.height_mm = 15.0
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key
    return entry


def _stack(context):
    from bname_dev.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    assert stack is not None
    layer_stack_utils.remember_layer_stack_signature(context)
    return stack


def _find_stack_item(context, uid: str):
    for index, item in enumerate(_stack(context)):
        from bname_dev.utils import layer_stack as layer_stack_utils

        if layer_stack_utils.stack_item_uid(item) == uid:
            return index, item
    raise AssertionError(f"stack item not found: {uid}")


def _move_uid_below_parent(context, uid: str, parent_uid: str) -> None:
    from bname_dev.utils import layer_stack as layer_stack_utils

    stack = _stack(context)
    from_index = next(i for i, item in enumerate(stack) if layer_stack_utils.stack_item_uid(item) == uid)
    parent_index = next(i for i, item in enumerate(stack) if layer_stack_utils.stack_item_uid(item) == parent_uid)
    target_index = min(len(stack) - 1, parent_index + 1)
    stack.move(from_index, target_index)
    layer_stack_utils.apply_stack_order_if_ui_changed(context, moved_uid=uid)
    layer_stack_utils.apply_stack_drop_hint(context, uid, nesting_delta=1)
    layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)


def _move_uid_before(context, uid: str, before_uid: str) -> None:
    from bname_dev.utils import layer_stack as layer_stack_utils

    stack = _stack(context)
    from_index = next(i for i, item in enumerate(stack) if layer_stack_utils.stack_item_uid(item) == uid)
    before_index = next(i for i, item in enumerate(stack) if layer_stack_utils.stack_item_uid(item) == before_uid)
    stack.move(from_index, before_index)
    layer_stack_utils.apply_stack_order_if_ui_changed(context, moved_uid=uid)
    layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)


def _assert_parent(context, uid: str, parent_key: str) -> None:
    _index, item = _find_stack_item(context, uid)
    actual = str(getattr(item, "parent_key", "") or "")
    if actual != parent_key:
        raise AssertionError(f"{uid} parent: expected {parent_key}, got {actual}")


def _assert_pixel_alpha(image, x: int, y: int, expected: float, label: str) -> None:
    width = int(image.size[0])
    index = (y * width + x) * 4 + 3
    _assert_close(float(image.pixels[index]), expected, label, eps=1.0e-4)


def _alpha_hits_near(image, center_x: int, center_y: int, radius: int = 12) -> list[tuple[int, int, float]]:
    width, height = int(image.size[0]), int(image.size[1])
    hits: list[tuple[int, int, float]] = []
    for y in range(max(0, center_y - radius), min(height, center_y + radius + 1)):
        for x in range(max(0, center_x - radius), min(width, center_x + radius + 1)):
            alpha = float(image.pixels[(y * width + x) * 4 + 3])
            if alpha > 0.5:
                hits.append((x, y, alpha))
    return hits


def _alpha_hits_all(image, limit: int = 20) -> list[tuple[int, int, float]]:
    width, height = int(image.size[0]), int(image.size[1])
    hits: list[tuple[int, int, float]] = []
    for y in range(height):
        for x in range(width):
            alpha = float(image.pixels[(y * width + x) * 4 + 3])
            if alpha > 0.5:
                hits.append((x, y, alpha))
                if len(hits) >= limit:
                    return hits
    return hits


def _simulate_coma_move(context, page, panel, dx_mm: float, dy_mm: float) -> None:
    from bname_dev.utils import layer_stack as layer_stack_utils
    from bname_dev.utils.layer_hierarchy import COMA_KIND, coma_stack_key
    from bname_dev.operators import layer_move_op

    uid = layer_stack_utils.target_uid(COMA_KIND, coma_stack_key(page, panel))
    _index, item = _find_stack_item(context, uid)
    resolved = layer_stack_utils.resolve_stack_item(context, item)
    assert resolved is not None
    resolved_panel = resolved.get("target")
    assert str(getattr(resolved_panel, "coma_id", "") or "") == str(getattr(panel, "coma_id", "") or "")
    mover = SimpleNamespace(_target=resolved, _snapshots=[])
    layer_move_op.BNAME_OT_layer_move_tool._capture_snapshot(mover, context, "coma", resolved)
    assert layer_move_op.BNAME_OT_layer_move_tool._apply_delta(mover, context, dx_mm, dy_mm)


def _simulate_page_move(context, page, dx_mm: float, dy_mm: float) -> None:
    from bname_dev.core.work import get_work
    from bname_dev.utils import layer_stack as layer_stack_utils
    from bname_dev.utils import page_grid
    from bname_dev.utils.layer_hierarchy import PAGE_KIND, page_stack_key
    from bname_dev.operators import layer_move_op

    uid = layer_stack_utils.target_uid(PAGE_KIND, page_stack_key(page))
    _index, item = _find_stack_item(context, uid)
    resolved = layer_stack_utils.resolve_stack_item(context, item)
    assert resolved is not None
    resolved_page = resolved.get("target")
    assert str(getattr(resolved_page, "id", "") or "") == str(getattr(page, "id", "") or "")
    mover = SimpleNamespace(_target=resolved, _snapshots=[])
    layer_move_op.BNAME_OT_layer_move_tool._capture_snapshot(mover, context, "page", resolved)
    assert layer_move_op.BNAME_OT_layer_move_tool._apply_delta(mover, context, dx_mm, dy_mm)
    page_grid.apply_page_collection_transforms(context, get_work(context))


def _write_visual_report(state: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return
    image = Image.new("RGB", (920, 560), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.text((24, 18), "B-Name Layer Stack UI Behavior Check", fill=(0, 0, 0), font=font)
    y = 58
    for row in state.get("stack_rows", []):
        depth = int(row.get("depth", 0))
        color = (245, 250, 255) if depth else (235, 235, 235)
        draw.rectangle((24 + depth * 22, y, 440, y + 24), fill=color, outline=(90, 90, 90))
        draw.text((34 + depth * 22, y + 6), f"{row['kind']} {row['label']}", fill=(0, 0, 0), font=font)
        y += 28
    draw.rectangle((500, 72, 860, 472), outline=(0, 0, 0), width=2)
    before = state["before_coma_rect"]
    after = state["after_coma_rect"]
    scale = 1.25
    def rect_xy(rect):
        x, yy, w, h = rect
        return (500 + x * scale, 472 - (yy + h) * scale, 500 + (x + w) * scale, 472 - yy * scale)
    draw.rectangle(rect_xy(before), outline=(180, 180, 180), width=2)
    draw.rectangle(rect_xy(after), outline=(255, 0, 170), width=3)
    draw.text((500, 492), "gray=before / magenta=after; nested layers moved with coma", fill=(0, 0, 0), font=font)
    image.save(OUT_DIR / "layer_stack_ui_behavior.png")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_layer_stack_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "LayerStack.bname"))
        assert "FINISHED" in result, result

        from bname_dev.operators import effect_line_op
        from bname_dev.operators import raster_layer_op
        from bname_dev.utils import gp_layer_parenting as gp_parent
        from bname_dev.utils import layer_stack as layer_stack_utils
        from bname_dev.utils.geom import m_to_mm, mm_to_px
        from bname_dev.utils.layer_hierarchy import COMA_KIND, PAGE_KIND, coma_stack_key, page_stack_key

        context = bpy.context
        work = context.scene.bname_work
        page = work.pages[0]
        panel = page.comas[0]
        page_key = page_stack_key(page)
        coma_key = coma_stack_key(page, panel)
        page_uid = layer_stack_utils.target_uid(PAGE_KIND, page_key)
        coma_uid = layer_stack_utils.target_uid(COMA_KIND, coma_key)

        _gp_obj, gp_layer = _add_test_gp_layer(context, page_key)
        _eff_obj, effect_layer = effect_line_op._create_effect_layer(
            context,
            (74.0, 88.0, 30.0, 24.0),
            parent_key=page_key,
        )
        raster, raster_image, raster_pixel = _add_test_raster_layer(context, page_key)
        balloon = _add_test_balloon(page, "dnd_balloon", page_key)
        text_a = _add_test_text(page, "dnd_text_a", page_key)
        text_b = _add_test_text(page, "dnd_text_b", page_key)

        gp_uid = layer_stack_utils.target_uid("gp", layer_stack_utils._node_stack_key(gp_layer))
        effect_uid = layer_stack_utils.target_uid("effect", layer_stack_utils._node_stack_key(effect_layer))
        raster_uid = layer_stack_utils.target_uid("raster", raster.id)
        balloon_uid = layer_stack_utils.target_uid("balloon", f"{page_key}:{balloon.id}")
        text_a_uid = layer_stack_utils.target_uid("text", f"{page_key}:{text_a.id}")
        text_b_uid = layer_stack_utils.target_uid("text", f"{page_key}:{text_b.id}")

        _stack(context)
        _move_uid_before(context, text_a_uid, text_b_uid)
        stack_uids = [layer_stack_utils.stack_item_uid(item) for item in _stack(context)]
        if stack_uids.index(text_a_uid) > stack_uids.index(text_b_uid):
            raise AssertionError("D&D order failed: text_a was not moved before text_b")

        for uid in (gp_uid, effect_uid, raster_uid, balloon_uid, text_a_uid, text_b_uid):
            _move_uid_below_parent(context, uid, coma_uid)
            _assert_parent(context, uid, coma_key)

        if gp_parent.parent_key(gp_layer) != coma_key:
            raise AssertionError("GP layer parent was not applied")
        if gp_parent.parent_key(effect_layer) != coma_key:
            raise AssertionError("Effect layer parent was not applied")
        assert raster.parent_kind == "coma" and raster.parent_key == coma_key
        assert balloon.parent_kind == "coma" and balloon.parent_key == coma_key
        assert text_a.parent_kind == "coma" and text_a.parent_key == coma_key

        before_coma_rect = (
            float(panel.rect_x_mm),
            float(panel.rect_y_mm),
            float(panel.rect_width_mm),
            float(panel.rect_height_mm),
        )
        before_balloon = (float(balloon.x_mm), float(balloon.y_mm))
        before_text = (float(text_a.x_mm), float(text_a.y_mm))
        before_gp = tuple(float(v) for v in _first_gp_point(gp_layer).position)
        before_effect = tuple(float(v) for v in _first_gp_point(effect_layer).position)

        _assert_pixel_alpha(raster_image, raster_pixel[0], raster_pixel[1], 1.0, "raster source alpha before")
        raster_slice_index = (raster_pixel[1] * int(raster_image.size[0]) + raster_pixel[0]) * 4 + 3
        _assert_close(float(raster_image.pixels[:][raster_slice_index]), 1.0, "raster source alpha before slice")
        dx_mm, dy_mm = 4.0, 3.0
        dx_px = int(round(mm_to_px(dx_mm, int(raster.dpi))))
        dy_px = int(round(mm_to_px(dy_mm, int(raster.dpi))))
        _simulate_coma_move(context, page, panel, dx_mm, dy_mm)

        _assert_close(panel.rect_x_mm, before_coma_rect[0] + dx_mm, "coma x")
        _assert_close(panel.rect_y_mm, before_coma_rect[1] + dy_mm, "coma y")
        _assert_close(balloon.x_mm, before_balloon[0] + dx_mm, "balloon x")
        _assert_close(balloon.y_mm, before_balloon[1] + dy_mm, "balloon y")
        _assert_close(text_a.x_mm, before_text[0] + dx_mm, "text x")
        _assert_close(text_a.y_mm, before_text[1] + dy_mm, "text y")
        after_gp = tuple(float(v) for v in _first_gp_point(gp_layer).position)
        after_effect = tuple(float(v) for v in _first_gp_point(effect_layer).position)
        _assert_close(m_to_mm(after_gp[0] - before_gp[0]), dx_mm, "gp dx")
        _assert_close(m_to_mm(after_gp[1] - before_gp[1]), dy_mm, "gp dy")
        _assert_close(m_to_mm(after_effect[0] - before_effect[0]), dx_mm, "effect dx")
        _assert_close(m_to_mm(after_effect[1] - before_effect[1]), dy_mm, "effect dy")
        _assert_pixel_alpha(raster_image, raster_pixel[0], raster_pixel[1], 0.0, "raster source alpha after")
        try:
            _assert_pixel_alpha(
                raster_image,
                raster_pixel[0] + dx_px,
                raster_pixel[1] + dy_px,
                1.0,
                "raster shifted alpha after",
            )
        except AssertionError as exc:
            hits = _alpha_hits_near(raster_image, raster_pixel[0] + dx_px, raster_pixel[1] + dy_px)
            all_hits = _alpha_hits_all(raster_image)
            raise AssertionError(f"{exc}; nearby alpha hits={hits[:12]}; all alpha hits={all_hits}") from exc

        before_page_offset = (float(page.offset_x_mm), float(page.offset_y_mm))
        before_page_gp_world = _gp_point_world_mm(_gp_obj, _first_gp_point(gp_layer))
        before_page_effect_world = _gp_point_world_mm(_eff_obj, _first_gp_point(effect_layer))
        raster_obj = bpy.data.objects.get(raster_layer_op.raster_plane_name(raster.id))
        assert raster_obj is not None
        before_raster_object = tuple(float(v) for v in raster_obj.location)
        before_page_balloon = (float(balloon.x_mm), float(balloon.y_mm))
        before_page_text = (float(text_a.x_mm), float(text_a.y_mm))

        page_dx_mm, page_dy_mm = 7.0, -2.0
        _simulate_page_move(context, page, page_dx_mm, page_dy_mm)

        _assert_close(page.offset_x_mm, before_page_offset[0] + page_dx_mm, "page x")
        _assert_close(page.offset_y_mm, before_page_offset[1] + page_dy_mm, "page y")
        after_page_gp_world = _gp_point_world_mm(_gp_obj, _first_gp_point(gp_layer))
        after_page_effect_world = _gp_point_world_mm(_eff_obj, _first_gp_point(effect_layer))
        _assert_close(after_page_gp_world[0] - before_page_gp_world[0], page_dx_mm, "page move gp world dx")
        _assert_close(after_page_gp_world[1] - before_page_gp_world[1], page_dy_mm, "page move gp world dy")
        _assert_close(after_page_effect_world[0] - before_page_effect_world[0], page_dx_mm, "page move effect world dx")
        _assert_close(after_page_effect_world[1] - before_page_effect_world[1], page_dy_mm, "page move effect world dy")
        _assert_close(m_to_mm(raster_obj.location.x - before_raster_object[0]), page_dx_mm, "page move raster object dx")
        _assert_close(m_to_mm(raster_obj.location.y - before_raster_object[1]), page_dy_mm, "page move raster object dy")
        _assert_close(balloon.x_mm, before_page_balloon[0], "page move balloon local x")
        _assert_close(balloon.y_mm, before_page_balloon[1], "page move balloon local y")
        _assert_close(text_a.x_mm, before_page_text[0], "page move text local x")
        _assert_close(text_a.y_mm, before_page_text[1], "page move text local y")
        _assert_pixel_alpha(
            raster_image,
            raster_pixel[0] + dx_px,
            raster_pixel[1] + dy_px,
            1.0,
            "page move raster pixel not shifted",
        )

        stack_rows = [
            {
                "kind": str(getattr(item, "kind", "")),
                "label": str(getattr(item, "label", "") or getattr(item, "name", "")),
                "depth": int(getattr(item, "depth", 0)),
                "parent_key": str(getattr(item, "parent_key", "") or ""),
            }
            for item in _stack(context)
        ]
        state = {
            "page_uid": page_uid,
            "coma_uid": coma_uid,
            "before_coma_rect": before_coma_rect,
            "after_coma_rect": (
                float(panel.rect_x_mm),
                float(panel.rect_y_mm),
                float(panel.rect_width_mm),
                float(panel.rect_height_mm),
            ),
            "raster_shift_px": [dx_px, dy_px],
            "stack_rows": stack_rows,
        }
        _write_visual_report(state)
        print(f"BNAME_LAYER_STACK_UI_BEHAVIOR_OK visual={OUT_DIR}")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
