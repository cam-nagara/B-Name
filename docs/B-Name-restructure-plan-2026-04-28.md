# B-Name ディレクトリ構造改修 計画

作成日: 2026-04-28
位置づけ: 魚眼レンダリング実装（[B-Name-fisheye-plan-2026-04-28.md](B-Name-fisheye-plan-2026-04-28.md)）の前提改修。本改修完了後に魚眼実装に着手する。

## 1. 背景と目的

現状のディレクトリ構造はネストが深く、コマ単位で副産物（魚眼出力 PNG、cube map 中間画像など）を置く場所が決まっていない。

```
旧:
  <Work>.bname/
  ├── work.json
  ├── pages.json
  ├── work.blend
  └── pages/
      └── 0001/
          ├── page.json
          └── panels/
              ├── panel_001.blend
              ├── panel_001.json
              ├── panel_001_thumb.png
              └── panel_001_preview.png
```

問題点:
- `pages/` 階層が冗長
- コマファイルが `panels/` 直下に並ぶため、コマ単位の副産物（魚眼 passes、cube 中間ファイル）の置き場が無い
- `panel_001` という命名が長い

## 2. 新構造（確定）

```
<Work>.bname/
├── work.json
├── pages.json
├── work.blend
└── p0001/                          ← 旧 pages/0001/
    ├── page.json
    ├── c01/                        ← 旧 panels/panel_001/ (新規ディレクトリ)
    │   ├── c01.blend               ← 旧 panel_001.blend
    │   ├── c01.json                ← 旧 panel_001.json
    │   ├── c01_thumb.png           ← 旧 panel_001_thumb.png
    │   ├── c01_preview.png         ← 旧 panel_001_preview.png
    │   └── passes/                 ← 新規。魚眼レンダ出力先
    │       └── cube/               ← 新規。cube map 中間 6 面画像
    ├── c02/
    │   └── ...
    └── ...
```

見開きページの場合:
```
<Work>.bname/
└── p0020-0021/                     ← 旧 pages/0020-0021/
    ├── page.json
    ├── c01/
    └── ...
```

## 3. 命名規約

### 3.1 ページ
- ディレクトリ名: `p<NNNN>` （4 桁ゼロパディング、例 `p0001`）
- 桁数は CLIP STUDIO PAINT の運用に合わせる（最大 9999 ページ）
- 見開き: `p<NNNN>-<NNNN>` （例 `p0020-0021`）
- 内部 ID（JSON のキー、Scene プロパティ等）: ディレクトリ名と同じ `p0001` / `p0020-0021`

### 3.2 コマ
- ディレクトリ名: `c<NN>` （2 桁ゼロパディング、例 `c01`、最大 `c99`）
- ファイル stem: ディレクトリ名と同じ（`c01.blend`, `c01.json`）
- 99 を超えた場合: **エラーで作成拒否**（拡張せず、明示的に例外を投げる）
- 内部 ID（JSON のキー、Scene プロパティ等）: ディレクトリ名と同じ `c01`
- 用語統一: コード内では `coma` で統一（既存 `panel_*` 命名は本改修で `coma_*` に置換）

### 3.3 副産物
- コマ単位の魚眼出力: `<Work>.bname/p0001/c01/passes/`
- cube map 中間画像: `<Work>.bname/p0001/c01/passes/cube/`
- それ以外（ユーザー指定の自由出力）: 既定で同じ `passes/` 配下

## 4. 識別子の正規表現

```python
PAGE_ID_RE  = r"^p\d{4}(-\d{4})?$"     # p0001, p0020-0021
COMA_ID_RE  = r"^c\d{2}$"               # c01〜c99
```

JSON や Scene プロパティに保存される ID は `p` / `c` プレフィックス付きで統一。プレフィックス無しで数値のみを保存している箇所があれば全て改修対象。

## 5. 影響ファイル

直接書換え必須（grep で検出済み）:

| ファイル | 改修内容 |
|---|---|
| [utils/paths.py](D:/Develop/Blender/B-Name/utils/paths.py) | `PAGES_DIR_NAME` / `PANELS_DIR_NAME` 削除、`page_dir` / `panels_dir` を `coma_dir` 中心に再設計、`format_panel_stem` を `format_coma_id` に改名し 2 桁化、正規表現更新 |
| [io/work_io.py](D:/Develop/Blender/B-Name/io/work_io.py) | パス生成箇所更新 |
| [io/page_io.py](D:/Develop/Blender/B-Name/io/page_io.py) | 同上 |
| [io/panel_io.py](D:/Develop/Blender/B-Name/io/panel_io.py) | ファイル名 `panel_io.py` → `coma_io.py` に改名検討、内部もコマ命名に |
| [io/meldex_receiver.py](D:/Develop/Blender/B-Name/io/meldex_receiver.py) | パス処理更新 |
| [operators/work_op.py](D:/Develop/Blender/B-Name/operators/work_op.py) | 新規作成・保存・読込処理 |
| [operators/page_op.py](D:/Develop/Blender/B-Name/operators/page_op.py) | ページ追加削除 |
| [operators/spread_op.py](D:/Develop/Blender/B-Name/operators/spread_op.py) | 見開き処理 |
| [operators/layer_stack_op.py](D:/Develop/Blender/B-Name/operators/layer_stack_op.py) | 中身確認の上で更新 |
| [utils/handlers.py](D:/Develop/Blender/B-Name/utils/handlers.py) | load_post の path resolve |
| [utils/page_range.py](D:/Develop/Blender/B-Name/utils/page_range.py) | ページ範囲処理 |
| [utils/panel_camera_refs.py](D:/Develop/Blender/B-Name/utils/panel_camera_refs.py) | 下絵参照のパス |

間接波及（用語統一のため要検討）:
- `core/panel.py` → `core/coma.py` 改名
- `core/panel_camera.py` → `core/coma_camera.py` 改名
- `operators/panel_*` → `operators/coma_*` 改名（多数）
- `panels/panel_camera_panel.py` → `panels/coma_camera_panel.py` 改名
- ※ Blender UI の `Panel` クラス（bpy.types.Panel）と「コマ panel」が混同しやすかった問題が解消する副次効果あり

ドキュメント:
- [docs/B-Name-overview-plan.md](D:/Develop/Blender/B-Name/docs/B-Name-overview-plan.md) — アーキ図と本文を新構造に更新
- [docs/B-Name-plan.md](D:/Develop/Blender/B-Name/docs/B-Name-plan.md) — 同様に更新
- [docs/B-Name_設計意図.md](D:/Develop/Blender/B-Name/docs/B-Name_設計意図.md) — 命名規約を新規追記

既存データ:
- 旧構造の `作品テスト.bname` 等は破棄（ユーザー承諾済）
- マイグレーションスクリプトは作らない

## 6. Phase 分割

### Phase R1 — paths.py 全面改修
新規 API（命名は `coma_*` で統一、ID と stem を兼用）:
- `format_coma_id(index: int) -> str` — `1` → `"c01"`、99 超で `ValueError`
- `validate_coma_id(coma_id: str) -> str` — `^c\d{2}$` 検証
- `validate_page_id(page_id: str) -> str` — `^p\d{4}(-\d{4})?$` 検証
- `format_page_id(index: int) -> str` — `1` → `"p0001"`
- `format_spread_id(left: int, right: int) -> str` — `(20, 21)` → `"p0020-0021"`
- `page_dir(work_dir, page_id) -> Path` — `<work>/<page_id>/`（旧 `<work>/pages/<page_id>/` から `pages/` 除去）
- `coma_dir(work_dir, page_id, coma_id) -> Path` — `<work>/<page_id>/<coma_id>/`
- `coma_blend_path(work_dir, page_id, coma_id) -> Path`
- `coma_json_path(work_dir, page_id, coma_id) -> Path`
- `coma_thumb_path(work_dir, page_id, coma_id) -> Path`
- `coma_preview_path(work_dir, page_id, coma_id) -> Path`
- `coma_passes_dir(work_dir, page_id, coma_id) -> Path` — `<work>/<page_id>/<coma_id>/passes/`
- `coma_passes_cube_dir(...) -> Path` — `<work>/<page_id>/<coma_id>/passes/cube/`

旧 API 削除:
- `PAGES_DIR_NAME`, `PANELS_DIR_NAME` 定数削除
- `panels_dir`, `panel_blend_path`, `panel_json_path`, `panel_thumb_path`, `panel_preview_path`, `format_panel_stem`, `validate_panel_stem`, `is_valid_panel_stem` 削除
- 単体テスト追加（正規表現、ID 整形、99 超 ValueError）

### Phase R2 — io 層書換え
- `panel_io.py` → `coma_io.py` 改名
- `work_io.py` / `page_io.py` の path 参照を全置換
- 新規ディレクトリ作成ロジック（コマ追加時に `c01/` ディレクトリ + `c01.blend` を生成）

### Phase R3 — operators / utils 書換え
- `panel_*` を `coma_*` に置換（ファイル名・クラス名・関数名・Scene プロパティ名）
- Scene プロパティ名 `bname_panel_camera_*` 等も `bname_coma_camera_*` に
- core 層も同時改名

### Phase R4 — UI 層書換え
- N パネル UI のラベル「パネル」を「コマ」に統一
- bl_idname の prefix 更新（`BNAME_OT_panel_*` → `BNAME_OT_coma_*`）
- keymap も追従

### Phase R5 — ドキュメント更新 + 全体チェック
- overview-plan / plan / 設計意図.md を新構造に
- ユーザー手動による新規作品作成 → ページ追加 → コマ追加 → コマ編集モード遷移 → 戻る、の E2E 確認

## 7. リスクと対処

| リスク | 影響 | 対処 |
|---|---|---|
| `panel` → `coma` 改名漏れ | クラッシュ・参照エラー | grep で全件洗い、Phase R3 / R4 完了時に「徹底チェック」実施 |
| Blender 側 `bpy.types.Panel`（UI 部品）との混同が残る | 命名衝突 | UI クラスは Blender 慣習通り `BNAME_PT_*` で命名（既存通り）、コマ概念は `BNameComa*` プレフィックスに統一。両者を別名前空間に分離 |
| Scene プロパティ rename で .blend ファイルとの後方互換が壊れる | 旧 .blend 開けない | 旧データは破棄前提（承諾済）。新規 .blend で動作確認 |
| 既存 `c100` 以上のコマが必要なケース | エラーで作業ブロック | 99 上限はマンガ運用上十分。超過時はエラーメッセージで「ページ分割」を促す |
| meldex_receiver の互換 | 外部連携壊れる | 連携相手 (Meldex) 側のパス組立も新構造に追従させる必要あり、Phase R2 着手前に Meldex 側の改修要否を確認。Meldex が古いパス前提なら `meldex_receiver.py` を一時的に旧→新パス変換する役割で残す |

## 8. 受入条件（テスト）

- 新規作品作成で `<Work>.bname/work.blend` のみ生成、`pages/` ディレクトリは作られない
- ページ追加で `<Work>.bname/p0001/page.json` が作成される
- コマ追加で `<Work>.bname/p0001/c01/c01.blend` + `c01.json` が作成される
- コマ追加 99 個まで成功、100 個目で明確なエラー
- 見開きページ追加で `<Work>.bname/p0020-0021/` が作成される
- コマ編集モード遷移で `c01.blend` が main file として開く
- Blender 再起動後、同じ状態（ページ・コマ・GP・テキスト等）が復元される
- grep -r "panel_" で残存があれば全て妥当性確認（Blender API の Panel クラス以外は coma に置換済）
- 徹底チェック完了

## 9. 完了後の連携

本改修完了後、[B-Name-fisheye-plan-2026-04-28.md](B-Name-fisheye-plan-2026-04-28.md) の Phase F1+F2 に進む。魚眼計画書側も新パス記法（`c01/passes/`）と新ファイル名（`coma_camera.py` 等）に追従済み。

## 10. 着手前の追加確認事項

- 本計画書には記述していないが Phase R2 着手前に確認したいこと:
  - Meldex 連携が現在使われているか（[io/meldex_receiver.py](D:/Develop/Blender/B-Name/io/meldex_receiver.py) の呼出元と使用状況）
  - 連携あり → 旧パス互換レイヤを残すか、Meldex 側も同時改修するか
  - 連携なし → `meldex_receiver.py` も新パス前提で書換え

---

**以上**
