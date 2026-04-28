# B-Name ラスター描画レイヤー導入計画 (2026-04-28)

## 0. 背景と目的

- 60 ページ規模の作品で、Grease Pencil v3 (ベクター) のみだと描画ストローク数が膨大になり編集が重くなる。
- ラスター (ビットマップ) ペイントレイヤーを追加し、**実体ピクセル数を 300dpi / 150dpi に縮小して保持**することで、見た目は 600dpi の用紙にフィット表示しつつメモリと描画負荷を抑える。
- 既存の `image` kind (配置画像 / スキャンラフ取り込み) とは別概念。新 kind = `raster` として設計する。

## 1. 用語定義 (本書内)

| 語 | 意味 |
| -- | -- |
| ラスター描画レイヤー / raster | 本計画で追加する新レイヤー種別。Texture Paint で描画。 |
| 配置画像 / image | 既存の `BNameImageLayer`。スキャンラフや写真の配置用。 |
| 600dpi 相当 | `paper.dpi` の値。出力時の最終解像度。 |
| レイヤー DPI | ラスターレイヤーごとに持つピクセル密度設定 (300 / 150 / 任意)。 |
| 所属 (scope) | レイヤーがページ所属 (page) か作品マスター所属 (master) か。 |
| マスク親 | レイヤーが page / panel フォルダ配下に入っているとき、その親要素。出力時にこの形でクリップする。 |

## 2. データモデル

### 2.1 PropertyGroup `BNameRasterLayer`

`core/raster_layer.py` (新規) に定義。

| プロパティ | 型 | 説明 |
| -- | -- | -- |
| `id` | StringProperty | UUID (uuid4 hex 12 文字)。レイヤー識別子。リネーム不可。 |
| `title` | StringProperty | 表示名 (ユーザー編集可)。既定: `ラスター 1` 連番。 |
| `image_name` | StringProperty | `bpy.data.images` 内の Image 名 (= `raster_<uuid12>`)。 |
| `filepath_rel` | StringProperty | 作品ルートからの相対パス (`raster/<uuid12>.png`)。 |
| `dpi` | IntProperty | レイヤー DPI。既定 300、min 30、soft_max 1200。 |
| `bit_depth` | EnumProperty | `gray8` (256階調+α) / `gray1` (1bit+α)。既定 `gray8`。 |
| `line_color` | FloatVectorProperty(COLOR, size=4) | 線色。出力時に階調値と乗算して着色。既定 (0,0,0,1)。 |
| `opacity` | FloatProperty | 不透明度 0-1。既定 1.0。 |
| `visible` | BoolProperty | 表示。既定 True。 |
| `locked` | BoolProperty | ロック。既定 False。 |
| `scope` | EnumProperty | `page` / `master`。所属の種別。 |
| `parent_kind` | EnumProperty | `none` / `page` / `panel`。マスク親の種別。 |
| `parent_key` | StringProperty | マスク親の参照キー (page id / panel id)。 |

### 2.2 保持場所 (集約)

- 作品ルート直下の **`<work>/raster/<uuid12>.png`** にフラット集約。
- ページ・コマ間の移動およびマスター↔ページ付け替えで **PNG ファイルは動かさない** (メタの `scope` / `parent_*` を書き換えるだけ)。
- `bpy.data.images` には `raster_<uuid12>` という名前でロードし、`filepath` は外部 PNG を指す (Pack しない)。

### 2.3 シーン側コレクション

`Scene.bname_raster_layers : CollectionProperty(type=BNameRasterLayer)` として作品全体の単一コレクションで持つ。所属 (`scope` / `parent_*`) はこのコレクション内のメタで判定。

> 補足: GP の master 思想と揃え、所属はメタのみで表現する。Image データ本体は集約場所に置きフラットに管理する。

### 2.4 統合レイヤースタックへの組み込み

- `core/layer_stack.py` の `LAYER_KIND_ITEMS` / `ACTIVE_LAYER_KIND_ITEMS` に `("raster", "ラスター", "")` を追加。
- `utils/layer_stack.py` の `resolve_stack_item` に kind=`raster` の解決枝を追加。`item.key` を UUID とし、`bname_raster_layers` から該当エントリを引いて返す。
- `sync_layer_stack` で各ページ / マスター配下のラスターレイヤーを所属に応じて挿入する。

## 3. 階層モデル (マスク化フォルダ)

### 3.1 既存階層

```
work
├ master (作品全体の集約レイヤー)
│   ├ raster (master scope)
│   ├ gp (master の GP レイヤー)
│   └ image
└ page (ページ)
    ├ raster (page scope, parent_kind=page)
    ├ gp / image / balloon / text / effect
    └ panel (コマ)
        ├ raster (page scope, parent_kind=panel)
        ├ gp / balloon / text / effect
        └ ...
```

### 3.2 マスク規則

- `parent_kind=page`: そのページの紙面領域 (`paper` の bleed まで) でクリップ。
- `parent_kind=panel`: その panel の枠形状 (角丸・多角形含む) でクリップ。
- `parent_kind=none`: クリップなし (master scope の既定)。
- ビューポート上では Phase 1 では **クリップ表示しない** (素通し)。出力時に Pillow でマスクを生成して適用。

### 3.3 マスター所属の表示順 (Q9 案前者で確定)

- レイヤースタック上に **「マスター」セクションを最上位に固定**。各ページのスタックとは別に重ね順を持つ。
- ビューポート / 出力では「マスター = ページ群より前面」で合成 (= 最上位の意味と一致)。
- 後で「ページごとに重ね順を変えたい」要件が出たら拡張する (Phase 2 以降)。

## 4. UI 統合

### 4.1 追加メニュー

現状の `BNAME_MT_layer_stack_add` は単一 Menu で `_ADD_KIND_ITEMS` をフラットに列挙する構造。raster は DPI / bit 深度のバリエーションがあるため **入れ子サブメニューを新設**する。

- `_ADD_KIND_ITEMS` に `"raster"` を追加するが、raster だけは `layout.operator` で直接出さず、`layout.menu("BNAME_MT_layer_stack_add_raster")` でサブメニューを開く分岐を `BNAME_MT_layer_stack_add.draw` 内に追加する。
- `_ADD_KIND_ICONS` に `"raster": "BRUSH_DATA"` を追加。
- 新規 Menu クラス `BNAME_MT_layer_stack_add_raster` (operators/layer_stack_op.py に同居) を追加し、以下のプリセットを並べる:
  - 300dpi / グレー 8bit (既定)
  - 150dpi / グレー 8bit
  - 300dpi / 1bit
  - 150dpi / 1bit
  - カスタム... (DPI / bit 深度をダイアログで指定)
- 各プリセットは `bname.raster_layer_add` を呼び、`dpi` / `bit_depth` プロパティを設定。
- ラベル: 既存 `image` は「画像 (配置)」、新規 `raster` は「ラスター (描画用)」と **UI 名を区別**。
- アイコン: 既存 `image` = `IMAGE_DATA`、新規 `raster` = `BRUSH_DATA`。

### 4.2 レイヤー詳細パネル

`panels/gpencil_panel.py` の `draw_stack_item_detail` に `kind=="raster"` 分岐を追加 (関数 `_draw_raster_selected_settings`)。表示項目:

- title (リネーム)
- visible / locked
- opacity (slider)
- dpi (**読み取り専用ラベル + 「リサンプル...」ボタン**。`layout.prop` 直書きはしない。値変更はオペレータ `bname.raster_layer_resample` 経由でのみ行い、ピクセルデータの Lanczos 再サンプリングと同期させる)
- bit_depth (gray8 / gray1。変更時は `bname.raster_layer_set_bit_depth` 経由)
- line_color
- scope (page / master 切替: 移動ツールから操作するのが基本だが、ここからも切替可)
- parent_kind / parent_key (現在の所属表示, 読み取り専用)
- 「Texture Paint へ入る」ボタン

### 4.3 ＋ボタン以外のエントリポイント

- `bname.raster_layer_add` オペレータを直接呼ぶショートカットも準備 (デフォルトキーマップは未割り当て、ユーザーが設定可能)。

## 5. オペレータ群

すべて `operators/raster_layer_op.py` (新規) に集約。

| bl_idname | 機能 |
| -- | -- |
| `bname.raster_layer_add` | UUID 採番 / Image 生成 / PNG 即時書き出し / Plane 生成 / レイヤースタック挿入 |
| `bname.raster_layer_remove` | アクティブラスターを削除 (Image・Plane・PNG・メタの一斉削除、Undo 対応) |
| `bname.raster_layer_select` | アクティブ化。kind=`raster` への切替 |
| `bname.raster_layer_paint_enter` | 該当 Plane を active 化、Texture Paint モードへ |
| `bname.raster_layer_paint_exit` | OBJECT モードへ戻す。AGENTS.md「ツール継続」原則に従い、別ツールへ切替時のみ呼ぶ |
| `bname.raster_layer_resample` | DPI 変更時に既存画素を Lanczos でリサンプル |
| `bname.raster_layer_set_bit_depth` | gray1 / gray8 切替 (gray1 化時はしきい値ダイアログ) |
| `bname.raster_layer_save_png` | 編集中の Image を `<work>/raster/<uuid>.png` に書き出し (gray8 + α の PNG。Ctrl+S フックから呼ぶ) |
| `bname.raster_layer_move_scope` | scope を page ↔ master 付け替え |
| `bname.raster_layer_move_parent` | parent_kind/parent_key を変更 (page → panel、別ページへ移動など) |

### 5.1 Plane 生成仕様

- 名前 (page scope): `raster_plane_<uuid12>` (1枚)
- 名前 (master scope): `raster_plane_<uuid12>__<page_id>` (各ページ位置に 1 枚ずつ配置。Image Texture は **同じ `bpy.data.images['raster_<uuid12>']` を参照**)
- メッシュ: 4 頂点 Plane、サイズは紙の `canvas_width_mm` × `canvas_height_mm` (Blender unit 系の換算は paper の現行ルールに合わせる)。
- 位置:
  - page scope: 該当ページの `page_grid` 上の位置 (`utils/page_grid.apply_page_collection_transforms` が決める)。
  - master scope: 全ページ位置に複製配置。ページ追加 / 削除時にハンドラで Plane 数を増減。
- マテリアル: `raster_mat_<uuid12>` (master でも 1 つを共有)。Image Texture ノード → **Emission シェーダー (シェードレス)** で接続。Principled は使わない (master GP の表示と整合させるため)。
- B-Name 用フラグ: `obj["bname_raster_id"] = <uuid12>`、master scope の場合は `obj["bname_raster_master_page"] = <page_id>` も付与して識別。
- レイヤー削除時は全 Plane (master scope は複数枚) / メッシュ / マテリアル / Image / PNG をすべて purge。
- master scope でページが増減した時の Plane 同期は `utils/handlers.py` の page change ハンドラで行う。

### 5.2 PNG 書き出し仕様

- 形式: PNG 16bit RGBA は使わず、**8bit + α**。グレー値は R=G=B でグレースケール固定、線色は出力時に適用。
- 描画中のグレースケール強制:
  - Texture Paint モード入りと同時に、現在のブラシの `color` を強制的にグレー (R=G=B) にロックする。
  - `bname.raster_layer_paint_enter` 内で `tool_settings.image_paint.brush.color` 監視ハンドラを設定し、変更されたら R=G=B の平均値で上書き。退出時にハンドラ解除。
  - これにより「描画中の見た目 (グレー濃淡)」と「出力時の線色着色」が両立する。
- 1bit モード: PNG は 8bit のまま保存し、メタの `bit_depth=gray1` を記録。書き出し合成時にしきい値量子化を適用 (描画中の自由度を保つため)。
- パス: `<work>/raster/<uuid12>.png`。ディレクトリがなければ作成。
- `bpy.data.images` 内の Image の `filepath` は `//raster/<uuid12>.png` (Blender 相対パス、work.blend 同一ディレクトリ前提) とし、別環境で開いても解決可能にする。
- 書き出しタイミング (二重に網を張ってデータ消失を防ぐ):
  - レイヤー追加時 (空 PNG)
  - Texture Paint モード退出時 (`bname.raster_layer_paint_exit` 内)
  - レイヤー切替・ページ切替で対象 Image がアンロード対象になる時
  - Blender 保存 (Ctrl+S) フック (`bpy.app.handlers.save_pre`) 時に **dirty フラグが立っている全ラスター**を一括書き出し
  - 明示的な「PNG として保存」操作 (`bname.raster_layer_save_png`)
- `Image.is_dirty` を必ず確認してから書き出す。dirty でない時はスキップ (I/O 削減)。

## 6. Texture Paint モード遷移制御

### 6.1 入る (raster_layer_paint_enter)

1. 既存の panel modal や GP モードを `panel_modal_state.finish_all` で終了。
2. 対象 Plane を `view_layer.objects.active` に設定し `select_set(True)`。
3. アクティブマテリアルとアクティブ Image Texture ノードを設定。
4. `bpy.ops.object.mode_set(mode="TEXTURE_PAINT")`。
5. `tool_settings.image_paint.canvas` に Image を設定。

### 6.2 抜ける

- 別ツールに切り替わる時、または `bname.gpencil_master_mode_set` 系が呼ばれた時のみ OBJECT モードへ戻す。
- AGENTS.md の「明示的な終了操作または別ツールへの切り替えがあるまで継続」原則に従う。

### 6.3 ロックされたレイヤー / 非表示レイヤー

- `locked=True` または `visible=False` のときは Texture Paint 入りを拒否し、`self.report({"WARNING"}, ...)` で通知。

## 7. 出力パイプライン統合

`io/export_pipeline.py` の `build_page_layers` に raster の合成枝を追加する。

### 7.0 合成順序の決定方針 (Phase 切り分け)

現状の `build_page_layers` は **固定位置挿入** (image kind) と **レイヤースタック並び順** (gp kind) のハイブリッドで動作している。raster をどちらに従わせるかは以下の段階的方針で進める:

- **Phase 1**: raster は **固定位置挿入** で実装 (image kind と同じ思想)。レイヤースタック上の前後順序は出力に反映しない。実装コストを最小化し早期に動作確認。
- **Phase 2**: panel scope のラスターを **コマフォルダの一員として** スタック順に従わせる (ユーザー要件「コマフォルダ配下にラスター/GP を入れる」の実現)。これに合わせて `build_page_layers` の panel 内合成ロジックをスタック並び順走査に置き換える。
- **Phase 3**: 全 kind を統一的にスタック並び順で合成する大規模リファクタ (任意)。

### 7.1 合成順序 (Phase 1 = 固定位置挿入の場合)

現状の合成順序に raster を以下の位置で挿入:

1. paper (用紙色)
2. **raster (master scope, parent_kind=none) — 背面群**
3. image_layers (配置画像)
4. **raster (page scope, parent_kind=page)** ← ページ全面ラスター
5. panel white_margin
6. panel background
7. **raster (page scope, parent_kind=panel)** ← コマ内ラスター (border の手前に配置することでコマ枠線がラスターを覆い隠さないようにする)
8. panel preview
9. panel border (コマ枠線、ラスターより前面)
10. gp_layers
11. balloons
12. texts
13. **raster (master scope) — 前面群**
14. tombo / work_info / nombre

> master scope レイヤーの「背面群 / 前面群」の振り分けは、レイヤースタック上の master セクション内で「ベースレイヤー以下 / 以上」のフラグで判定する。レイヤー単位に `master_z = back / front` プロパティを持たせる (Phase 1 では `front` 固定でも可)。

### 7.2 各ラスターの合成手順

```
load PNG (gray8 RGBA、Pillow 経由)
  ↓ bit_depth==gray1 ならしきい値量子化 (alpha は維持、グレー値を 0 or 255 に二値化)
  ↓ 線色を Blender COLOR 値 (linear) から取得し、utils/color_space.linear_to_srgb で sRGB に変換
  ↓ 線色で着色 (RGBA を (line_color_srgb.rgb × (gray/255), alpha × line_color.a × layer.opacity))
  ↓ ピクセル拡大 (Lanczos): scale = paper.dpi / layer.dpi
       例: layer.dpi=300, paper.dpi=600 → 2 倍拡大
       例: layer.dpi=150, paper.dpi=600 → 4 倍拡大
       拡大後の px 数 = (canvas_width_mm * paper.dpi / 25.4, canvas_height_mm * paper.dpi / 25.4)
  ↓ 配置 (page scope: 左上を紙面原点に揃える / master scope: 各ページ位置に複製合成)
  ↓ マスク適用 (parent_kind に応じて page / panel のマスクを生成して乗算)
  ↓ alpha_composite で土台へ合成
```

### 7.3 マスク生成

- page マスク: 紙面 (canvas + bleed) の矩形マスク。
- panel マスク: 既存の `_panel_mask_*` ヘルパ (export_pipeline 内) を流用。コマ形状 (多角形・角丸) を Pillow の `ImageDraw` で生成。
- マスター scope: マスクなし。

## 8. ページ間移動 / scope 付け替え

### 8.1 移動ツール

- 既存 [`operators/layer_move_op.py`](D:\Develop\Blender\B-Name\operators\layer_move_op.py) を拡張。
- 操作:
  - `bname.raster_layer_move_scope` : scope を page ↔ master に切替。
  - `bname.raster_layer_move_parent` : parent_kind / parent_key を切替 (page → panel、別ページ、別コマ)。
- データ移動: PNG ファイルは動かさない。メタの `scope` / `parent_*` のみ更新し、レイヤースタックを再同期。

### 8.2 マスター↔ページ付け替えの挙動

- マスター → 単一ページ: scope=`page`、parent_kind=`page`、parent_key=対象ページ id。
- ページ → マスター: scope=`master`、parent_kind=`none`、parent_key=空。
- ピクセルデータ・DPI・bit_depth は引き継ぐ。

### 8.3 衝突 / 整合性

- 削除されたページに所属するラスターは **デフォルトで `scope=master` に自動格上げ**して安全側に倒す。ただし以下のユーザーフィードバックを必ず行う:
  - 削除実行時に確認ダイアログ「このページに N 個のラスターレイヤーがあります。削除しますか / マスターに移動しますか / キャンセル」を表示。
  - 「マスターに移動」を選ぶと scope=master へ昇格し、`title` に「(元: <page_id>)」のサフィックスを付加。
  - 「削除」を選ぶと PNG を `<work>/raster/.trash/` に退避してメタを除去 (R6 と統合)。
- ラスター本体を即時物理削除する操作は行わず、必ず .trash/ 経由とする。クリーンアップは別途明示的なオペレータで実行。

## 9. 遅延ロード / メモリ最適化

- `bpy.data.images` にロードするのは「アクティブなラスター」と「現在ビューポートで表示中のページに属するラスター」のみ。
- アンロード手順 (データ消失防止のため必ず以下の順):
  1. 対象 Image の `is_dirty` を確認。dirty なら `bname.raster_layer_save_png` 相当のロジックで PNG に書き出す。
  2. 該当 Plane (master scope なら全複製 Plane) のマテリアルから Image Texture 参照を一旦切る (またはマテリアルだけ残して Image を unlink)。
  3. `bpy.data.images.remove(image, do_unlink=False)` で破棄。
- 再ロード時は `bpy.data.images.load(filepath, check_existing=True)` でファイルから復元し、マテリアルに再接続。
- ページ切替フックは `bname_active_page_index` の update コールバック、または `utils/handlers.py` の既存 page change ハンドラに同居。
- 出力パイプラインは合成時に Pillow で PNG を直接読み込み (Blender Image を経由しない)、合成後すぐ解放。
- Phase 1 では遅延ロードは未実装 (全ラスターを常時ロード)。Phase 3 で実装。Phase 1 のメモリ消費は要件 (60 ページ × 1 ラスター程度) で運用想定する。

## 10. 既存システムとの整合

### 10.1 既存 `image` kind との混同防止

- UI 上のラベル: 既存 = 「画像」、新規 = 「ラスター」。
- アイコン: 既存 = `IMAGE_DATA`、新規 = `BRUSH_DATA` (Texture Paint と連想)。
- メニュー上での順序: 「画像 (配置)」「ラスター (描画)」と並べる。

### 10.2 `_creation_violates_layer_scope` との関係

- ラスターは「ページ全体キャンバス」または「コマ全体キャンバス」を持つ概念で、中心点による配置可否判定とは性質が異なる。
- 既存の `_creation_violates_layer_scope` は流用しない。代わりにモード判定:
  - MODE_PANEL: ラスター追加時、所属を強制的に panel = アクティブ panel に設定。
  - MODE_PAGE: ラスター追加時、所属を強制的に page = アクティブ page に設定 (or master をユーザー選択)。

### 10.3 PropertyGroup 登録順

- `core/__init__.py`:
  - import リストに `raster_layer` を追加 (位置は `image_layer` の直後)。
  - `_MODULES` タプルでも `image_layer` の直後・`layer_stack` の直前に `raster_layer` を配置。
  - 他 PropertyGroup への前方参照は文字列キー (`parent_key`) のみで PropertyGroup 型参照はないため、登録順制約は弱い。
- `operators/__init__.py`:
  - import リストに `raster_layer_op` を追加 (位置は `image_layer_op` の直後)。
  - `_MODULES` タプルでも `image_layer_op` と `layer_stack_op` の間に配置。

### 10.4 mode_set wrapper

- 既存の `bname.gpencil_master_mode_set` (panels/gpencil_panel.py) は GP 専用の wrapper オペレータ。raster 用に **同等の wrapper `bname.raster_layer_mode_set`** を新設する。
  - `mode` プロパティで TEXTURE_PAINT / OBJECT を切り替え。
  - 内部で `panel_modal_state.finish_all` を呼んでから対象 Plane を active 化、その後 `bpy.ops.object.mode_set` を実行。
- 既存ツール切替ボタン (アプリバー等) からモードを切り替える時、現在のレイヤーが raster なら raster wrapper、gp なら gp wrapper を呼ぶ分岐を `BNAME_OT_gpencil_master_mode_set.execute` 周辺に追加 (または新たに `bname.layer_aware_mode_set` 統合 wrapper を作る)。
- AGENTS.md の「ツール継続原則」に従い、raster mode 中は他レイヤー選択や別ツールへの切替が起きるまで自動で抜けない。

### 10.5 work.json への追記

- 作品メタ `work.json` に `raster_layers` セクションを追加。各ラスターの `id` / `title` / `dpi` / `bit_depth` / `line_color` / `opacity` / `scope` / `parent_kind` / `parent_key` / `filepath_rel` を保持。
- `io/schema.py` の `WORK_SCHEMA_VERSION` を bump し、`work_to_dict` / `work_from_dict` に raster_layers の serialize / deserialize を追加。
- 既存ファイル (raster_layers キーなし) を読み込んだ場合は空コレクションとして初期化 (前方互換)。
- 読み込み時: `raster/` 配下の PNG を Phase 1 では一括ロード、Phase 3 で遅延ロードに切替。
- ファイルが見つからない場合: `title` に「(欠落)」プレフィックスを付け、空 Image でレイヤー行を残す (誤って消さないため)。

## 11. .blend / 作品ディレクトリ構造への影響

新規ディレクトリ `<work>/raster/` を作る。`pages/` 配下や `panels/` 配下には何も追加しない (集約方式)。`utils/paths.py` に `RASTER_DIR_NAME = "raster"` と `raster_dir(work_dir)` / `raster_png_path(work_dir, uuid)` ヘルパを追加し、ディレクトリ生成は `create_bname_skeleton` で他のサブディレクトリ (assets/scenario/exports) と並列で行う。

```
MyWork.bname/
├ work.blend
├ work.json                 ← raster_layers セクション追記 (schema bump)
├ pages.json
├ pages/...
├ raster/                   ← 新設
│   ├ 550e8400e29b.png
│   ├ a3f2c1d4e5b6.png
│   └ .trash/               ← 削除待避用 (R6)
├ assets/
├ scenario/
└ exports/
```

## 12. リスクと未確定事項

| # | 項目 | 内容 | 暫定方針 |
| -- | -- | -- | -- |
| R1 | Texture Paint と GP モードの行き来 | モード切替コストが高い、ストロークの最中に切ると失う可能性 | 切替前に必ずストローク確定 (Texture Paint 標準動作) |
| R2 | Image の保存タイミング漏れ | save_pre フック失敗時にデータ消失 | 退出時 + ページ切替時 + save_pre の三重書き出し、`is_dirty` を必ず確認 |
| R3 | Plane の Z 順 | Blender ビューポート上でレイヤースタックの前後と Z 順を一致させる必要 | `utils/handlers.py` に `_bname_on_layer_stack_reorder` ハンドラを追加、`bname.layer_stack_move` 等のオペレータ完了時に呼ぶ |
| R4 | 1bit 表現 | しきい値固定では意図しない出力になる可能性 | PropertyGroup に `gray1_threshold: FloatProperty(default=0.5)` を追加 (Phase 2 で UI 露出) |
| R5 | 線色の sRGB / linear 変換 | Blender の COLOR プロパティは linear 保持、PNG 出力は sRGB | `utils/color_space.linear_to_srgb` を 7.2 の合成手順内で適用 (CLAUDE.md のルール準拠) |
| R6 | ページ削除時の orphan PNG | 物理削除を伴う操作を即時実行するか保留にするか | 「ゴミ箱」フォルダ `<work>/raster/.trash/` に移動して保留、明示的なクリーンアップ操作で完全削除 |
| R7 | UUID 衝突 | 12 文字 hex で実用上ゼロだが念のため | 採番時に既存集合と照合してリトライ |
| R8 | master scope での Plane 数増加 | 60 ページなら 60 枚 Plane が並ぶ | Plane は Mesh 共有・Material 共有で軽量。Image Texture も 1 枚を共有するためメモリ増は軽微 |
| R9 | Texture Paint 中のブラシ色グレースケール強制 | ハンドラ追加が必要、ブラシ変更で取りこぼす可能性 | `bpy.app.handlers` にカスタムフックは追加せず、`tool_settings.image_paint.brush.color` を msgbus で監視する (Blender 4.x 標準) |
| R10 | 出力時メモリ ピーク | 600dpi 6071×8598×4 = 約 200MB / レイヤー、Lanczos 拡大時さらに増加 | 出力は 1 ページずつシリアル処理、Pillow オブジェクトは即時 close() |

## 13. 実装フェーズ

### Phase 1 (MVP: 描いて出せる)

- データモデル / コレクション / レイヤースタック統合
- 追加メニュー (300dpi gray8 / 150dpi gray8 のみ)
- Texture Paint 入退出
- 外部 PNG 保存 (退出時 + save_pre)
- 出力パイプラインへの単純合成 (page scope のみ、マスクなし)
- 線色 + opacity 適用

### Phase 2 (マスク・1bit・移動・master)

- panel マスク / page マスクの出力時適用
- 1bit 表現 (描画は 8bit、出力時量子化、`gray1_threshold` UI 露出)
- scope / parent 移動ツール (`bname.raster_layer_move_scope` / `bname.raster_layer_move_parent`)
- master scope 完全対応 (レイヤースタックのマスターセクション、複数 Plane 同期、master_z 振り分け)
- panel scope ラスターをコマフォルダのスタック順で合成するロジック (7.0 の Phase 2)

### Phase 3 (品質向上)

- DPI リサンプル
- 遅延ロード
- ビューポートでのマスク疑似表示 (gpu オーバーレイ)
- カスタム DPI 入力ダイアログ

## 14. テスト項目 (Blender 5.1 実機)

メモリ概算の根拠 (300dpi RGBA, A4 257×364mm):
- 1 レイヤー = 3036×4299×4 ≒ **49.5MB**
- 60 ページ × 1 レイヤー = 約 **2.97GB** (Phase 1 の常時ロード上限)
- 60 ページ × 5 レイヤー = 約 **14.85GB** (Phase 3 の遅延ロード前提)

| ID | フェーズ | 内容 |
| -- | -- | -- |
| T1 | 1 | 300dpi ラスター追加 → Texture Paint で描画 → 退出 → `<work>/raster/<uuid>.png` に PNG 生成 |
| T2 | 1 | 150dpi ラスター追加 → Image px サイズが期待値 (1518×2150 など) になる |
| T3 | 1 | レイヤー削除 → Plane / マテリアル / Image / PNG が削除される (PNG は .trash/ に退避) |
| T4 | 1 | 線色を赤に設定 → 出力 PNG で赤い線になる、線色変更で再着色される |
| T5 | 1 | Texture Paint 中にブラシ色を赤にしようとしても自動的にグレー (R=G=B) に補正される |
| T6 | 2 | 1bit モード → 出力時に 0/1 量子化されている、threshold が反映される |
| T7 | 2 | page → master 移動 → 全ページに表示される、各ページに Plane が複製配置される |
| T8 | 2 | master → page 移動 → 該当ページのみに表示される、他ページの Plane が purge される |
| T9 | 2 | panel parent → 出力時にコマ枠でクリップされる、コマ枠線はラスターの上に乗る |
| T10 | 2 | コマを移動 → panel parent のラスターも追従する (panel content group 連動) |
| T11 | 1 | 60 ページ × 1 ラスター (300dpi) でメモリ消費が約 3GB ± 範囲 |
| T12 | 1 | Ctrl+S → save_pre フックで dirty なラスターのみ書き出される (clean は I/O スキップ) |
| T13 | 1 | work.blend を閉じて再オープン → ラスターが `//raster/<uuid>.png` 相対パスで復元される |
| T14 | 3 | DPI リサンプル → 既存ピクセルが Lanczos で拡大縮小される |
| T15 | 1 | ロックレイヤーを Texture Paint 入りしようとすると WARNING で拒否される |
| T16 | 2 | 削除ダイアログ「マスター昇格 / 削除 / キャンセル」が機能する |

## 15. 関連ファイル (実装時の作業対象)

| 区分 | パス | 操作 |
| -- | -- | -- |
| 新規 | `core/raster_layer.py` | PropertyGroup `BNameRasterLayer` |
| 新規 | `operators/raster_layer_op.py` | 全オペレータ (add/remove/select/paint_enter/paint_exit/resample/set_bit_depth/save_png/move_scope/move_parent/mode_set wrapper) |
| 新規 | `panels/raster_panel.py` (任意) | 詳細設定 UI を切り出す場合。Phase 1 では `panels/gpencil_panel.py` 内に同居でも可 |
| 編集 | `core/__init__.py` | import に `raster_layer` 追加、`_MODULES` で `image_layer` の直後に挿入 |
| 編集 | `operators/__init__.py` | import に `raster_layer_op` 追加、`_MODULES` で `image_layer_op` の直後に挿入 |
| 編集 | `core/layer_stack.py` | `LAYER_KIND_ITEMS` / `ACTIVE_LAYER_KIND_ITEMS` に raster 追加 |
| 編集 | `utils/layer_stack.py` | `resolve_stack_item` に kind=`raster` 分岐、`sync_layer_stack` で raster collection を取り込み、`collect_targets` 系も対応 |
| 編集 | `operators/layer_stack_op.py` | `_ADD_KIND_ITEMS` に raster 追加、`_ADD_KIND_ICONS` 追加、`BNAME_MT_layer_stack_add.draw` で raster は `layout.menu()` 分岐、新規 Menu `BNAME_MT_layer_stack_add_raster`、`_add_by_kind` に raster 分岐 |
| 編集 | `panels/gpencil_panel.py` | `draw_stack_item_detail` に raster 分岐 (`_draw_raster_selected_settings`)、`_kind_icon` に raster 追加、`_draw_stack_data_row` に raster 分岐 |
| 編集 | `io/export_pipeline.py` | `build_page_layers` に raster 合成枝 (master back / page-page / page-panel / master front)、`_render_raster_layer` 関数追加 |
| 編集 | `io/work_io.py` / `io/schema.py` | `WORK_SCHEMA_VERSION` bump、`work_to_dict` / `work_from_dict` に raster_layers serialize / deserialize |
| 編集 | `operators/layer_move_op.py` | scope / parent 移動オペレータ追加 |
| 編集 | `utils/handlers.py` | `_bname_on_save_pre` 内で raster の dirty PNG 書き出し追加、page change ハンドラに master scope Plane 同期と遅延ロード追加、layer stack reorder ハンドラ |
| 編集 | `utils/paths.py` | `RASTER_DIR_NAME` / `raster_dir` / `raster_png_path` / `raster_trash_dir` 追加 |
| 編集 | `io/work_io.py` (`create_bname_skeleton`) | `raster/` ディレクトリ作成を追加 |
| 編集 | `utils/color_space.py` | `linear_to_srgb` が未実装なら追加 (既存確認の上) |
| 編集 | `panels/gpencil_panel.py` の `BNAME_OT_gpencil_master_mode_set` | 現在のレイヤーが raster なら raster mode_set wrapper を呼ぶ分岐を追加 (または上位で振り分ける別オペレータを新設) |

## 16. 開発セッションへの引き継ぎ

実装は本書を計画書として開発セッションで進める。Phase 1 から順に着手し、各 Phase 完了時に T1〜T13 のうち該当項目をパスさせる。
