# CHANGELOG

このファイルは B-Name の主要な変更履歴を記録します。
Blender 5.1.1 を対象としています。

## 2026-04-30 — レイヤー詳細設定ダイアログを Object 選択ベースに刷新

### 追加
- `operators/layer_detail_op.py` 新設:
    - `bname.layer_detail_open`: 選択中 (active_object) の B-Name 管理レイヤー
      Object の `bname_kind` / `bname_id` から対応 entry を逆引きし、kind ごと
      のフィールド (image / raster / balloon / text / gp / effect) を
      `invoke_props_dialog` で編集可能に表示。`bname_managed=True` の Object
      を選択しているときのみ poll が通る。

### 変更 (ui/context_menu.py 全面刷新)
- 旧 UIList ベース (`active_stack_item`) の参照を **Object 選択ベース**
  (`active_object` の `bname_kind`) に切替え。UIList を廃止した整合性を確保。
- `BNAME_MT_layer_context` (新規): Outliner / 3D ビュー / 各ツール (フキダシ/
  テキスト/効果線/Object/枠線) の右クリックポップアップから直接呼び出せる
  サブメニュー。「詳細設定」「リンク複製 (effect のみ)」「親変更は Outliner
  で D&D」の案内を表示。
- `BNAME_MT_object_context`: 3D ビュー Object 右クリック (`VIEW3D_MT_object_
  context_menu`) **と Outliner Object 右クリック (`OUTLINER_MT_object` /
  `OUTLINER_MT_context_menu`)** の両方に `_draw_in_object_context` /
  `_draw_in_outliner_context` を append。アクティブ Object が B-Name 管理対象
  のときのみ B-Name サブメニューを差し込む。
- 旧 `BNAME_MT_selection_context` は `_draw_layer_commands` を呼ぶ薄いラッパ
  として残置。既存ツール (`balloon_op` / `text_op` / `effect_line_op` /
  `coma_edge_move_op` / `object_tool_op`) の `bpy.ops.wm.call_menu(name=
  "BNAME_MT_selection_context")` 呼出をそのまま動作させる。

### UI
- `panels/outliner_layer_panel.py` に「アクティブレイヤー > 詳細設定を開く」
  ボックスを追加。N パネルからもダイアログを呼び出せる。

E2E 確認:
- `bname.layer_detail_open` 登録、image Empty を active にして poll=True、
  active 無しで poll=False
- `BNAME_MT_layer_context` / `BNAME_MT_object_context` /
  `BNAME_MT_selection_context` 全 menu 登録
- VIEW3D_MT_object_context_menu / OUTLINER_MT_object のクラス参照取得 OK

## 2026-04-30 — Empty 化の徹底チェック修正 (即時同期 / 表示サイズ / 旧データ掃除)

### 修正
- `_EMPTY_DISPLAY_SIZE` を 1mm から 5mm に拡大 (3D ビューで点として
  選択可能なサイズへ)。
- `outliner_watch._on_depsgraph_update_post` を追加: Empty (image/text)
  を 3D ビューで G で動かしたとき、5 秒間隔の timer scan を待たずに
  `entry.x_mm/y_mm` に即時書戻し、オーバーレイ描画位置に連動する。
  再帰抑止は `los.suppress_sync()` ガード + entry 同値チェックで実施。
- `utils/empty_layer_object.cleanup_legacy_plane_objects` を追加:
  旧 Plane 方式 (text_plane_*, image_plane_*, balloon_plane_*) の Object
  と関連 Mesh / Material / placeholder Image データブロックを自動掃除。
  `_mirror_image_text_empties` 冒頭で呼び出して Empty 化移行直後の
  ゴミデータを除去する。

### Blender 5.1 ハンドラ登録
- `bpy.app.handlers.depsgraph_update_post` に `_on_depsgraph_update_post`
  を登録/解除。

E2E 確認:
- 旧 `text_plane_t01` Object を残置した状態から mirror 実行 → 削除確認
- depsgraph_update_post 経由で Empty.location 変更が即時 entry に反映
  (timer scan を待たない)

## 2026-04-30 — 画像 / テキストを Empty Object 化 (オーバーレイ描画方式)

### 変更
画像レイヤーとテキストを Outliner 上 **Empty Object** として登録する方式に
変更。実際の絵柄/文字描画は既存の B-Name 独自オーバーレイが担当する。
画像生成や Pillow 転写を回避してメモリ消費と編集応答性を改善する。

### 設計上の整合
- **export pipeline (`io/export_pipeline.py`)** は元々 PropertyGroup
  (BNameImageLayer / BNameTextEntry) を直接読んで Pillow で合成しているため、
  Empty 化しても **PNG / PSD レンダリング結果は不変**。全ページ出力時にも
  画像/テキストは正しく描画される。
- Outliner 上の D&D / 親子関係 / 表示 ON/OFF / マスク Modifier 対象としての
  機能は Empty で得られる。

### 追加
- `utils/empty_layer_object.py` 新設:
    - `ensure_image_empty_object` / `ensure_text_empty_object`: `bname_kind`
      stamp + Outliner mirror へ link。`empty_display_type='PLAIN_AXES'` +
      小さい display_size で原点マーカー表示。
    - `sync_entry_position_from_object`: Empty.location が変わったら
      対応 entry.x_mm/y_mm に書戻し。オーバーレイ描画位置に連動する。
- `operators/balloon_text_curve_op.py` に operator を追加:
    - `bname.images_to_empty_all`: 全画像レイヤーを Empty として登録
    - `bname.texts_to_empty_all`: 全テキストを Empty として登録
- watch (`utils/outliner_watch.py`) に `_writeback_empty_layer_parent` と
  Empty.location → entry 位置同期処理を追加。Outliner D&D で image/text
  Empty が別コマ等へ移ると entry の parent_kind / parent_key / folder_key
  が書戻る。
- `utils/layer_object_sync._mirror_image_text_empties`: mirror 実行時に
  全 image / text entry に対応する Empty を ensure (load_post / 保存時 /
  reparent 完了時に自動追従)。

### 削除
- `utils/text_plane_object.py` 削除 (Plane + Image Texture 方式は廃止)。
- 旧 `bname.texts_to_plane_all` operator は `bname.texts_to_empty_all` への
  エイリアスとして残置 (panel 経由の旧呼出を壊さないため)。

### Phase 3c との関係
オーバーレイ表示切替 (`bname_overlay_enabled`) を OFF にすると Empty の
原点マーカーだけが残り「データ構造の確認モード」として機能する。
ON のときは画像 / テキストはオーバーレイ経由で従来どおり高速描画される。

## 2026-04-30 — Phase 3c / 4c / 5d: 残発展課題を実装

### 追加 (Phase 5d: GP コマ/ページマスク)
- `utils/mask_apply._ensure_gp_internal_mask`: グリースペンシル v3 の
  `GreasePencilLayer.use_masks` + `mask_layers` 機構を使い、**同じ GP Object
  内に `__bname_mask` という名前のマスクレイヤーを自動生成**して、
  コマ/ページマスク Mesh の各 Face を閉じストロークとして描き写す。
  全コンテンツレイヤーで `use_masks=True` を立て、`mask_layers` に
  `__bname_mask` を登録する。マスクレイヤー自体は `hide=True` で見えない。
- 親 Collection 変更時にマスクストロークも追従して再生成。

### 追加 (Phase 4c: フキダシ Curve / テキスト Plane)
- `utils/balloon_curve_object.py` 新設: `BNameBalloonEntry` から
  `outline_for_entry` で得た輪郭点列を Bezier Curve として生成。
  `dimensions="2D"` + `fill_mode="BOTH"` + `bevel_depth` で線幅を再現。
  rect/ellipse/cloud/fluffy/thorn 等の Meldex 共通形状に対応。
- `utils/text_plane_object.py` 新設: `BNameTextEntry` の本文を typography
  (`typography.export_renderer.render_to_image` + Pillow) で透過 PNG に
  描画し、Plane Object の Image Texture material に貼り付ける。Pillow 不在
  環境では placeholder (1×1 透明) にフォールバック。
- `operators/balloon_text_curve_op.py` 新設:
    - `bname.balloons_to_curve_all`
    - `bname.texts_to_plane_all`

### 追加 (Phase 3c: オーバーレイ表示切替)
- `bpy.types.Scene.bname_overlay_enabled` (BoolProperty, default True) を
  追加。`ui/overlay.py._draw_callback` / `_draw_callback_pixel` の冒頭で
  この値を見て早期 return し、B-Name 独自 GPU オーバーレイ全体を ON/OFF。
- `operators/overlay_toggle_op.py` 新設 (`bname.overlay_toggle`)。
- `panels/outliner_layer_panel.py` に「オーバーレイ表示」ボックスと
  ON/OFF トグルボタンを追加。OFF にすると raster Mesh / balloon Curve /
  text Plane などの Blender 標準 Object 描画のみが見える状態になる。

### 残課題 (実装後)
- 大規模負荷試験 (例: 100 ページ × 30 GP Object 規模での Outliner 応答 /
  描画モード切替 / Undo/Redo の実測): すべての必要機能が整ってから実施。

## 2026-04-30 — マスク Object の viewport 非表示 + apply ロジック整理

### 修正
- マスク Mesh Object (`page_mask_*` / `coma_mask_*`) と `__masks__`
  Collection を viewport から非表示に。`hide_viewport=True` +
  `display_type="BOUNDS"` を ensure 時に設定し、`__masks__` Collection
  自体も LayerCollection 経由で `hide_viewport=True`。マスク Mesh が
  3D ビューに黒い面として描画されてレイヤーが見えなくなる問題を解消。
  Modifier の target 参照は hidden でも有効なのでクリッピング機能には
  影響しない。
- `apply_mask_to_layer_object`: parent_key 形式を先に判定 ("コマ配下"
  かつ mask 未生成のときページマスクへフォールバックしない)、論理を
  整理。GP 系は `_ensure_gp_mask_modifier` で modifier クリーンアップ
  のみ実行 (Phase 5d まで no-op)。

## 2026-04-30 — 旧 page Collection 廃止 / コマカット trigger / マスク視覚化

### 変更
- 旧 `page_p0001` 形式 Collection を `p0001` (新 mirror Collection) に統一。
  `ensure_page_collection` (gpencil 側) が呼ばれた時点で旧 Collection の
  Object/子 Collection を新側に移送し、旧 Collection を削除する自動移行を
  実装。`page_collection_name` の戻り値も `page_id` 直接へ変更。
- 枠線カット (`bname.coma_knife_cut`) 完了時に Outliner mirror と
  全マスク再生成を自動実行する trigger を追加。コマ追加直後に新コマ
  Collection (例: `c02`) が即時生成される。
- watch timer scan に「ページ/コマ/フォルダ件数の変化検出」を追加。
  外部 op で entry が増減したときに自動で mirror を再走させる
  (5 秒以内に反映)。

### 追加
- `utils/mask_apply.py`: コマ/ページマスク Mesh をレイヤー Object に適用。
    - Mesh 系 (raster / image plane / balloon plane / text plane):
      Boolean Modifier (Intersect, FAST solver) でマスク形状クリップ。
    - GP 系: Blender 5.1 GP v3 では外部 Object をマスク source に取る一般
      Modifier が無いため、現状は modifier 削除のみで no-op。
      Phase 5d で `__bname_mask` 内蔵 layer 方式で実装予定。
- raster の `ensure_raster_plane`、GP の `create_layer_gp_object`、
  effect の `create_effect_line_object` で、Object 生成完了後に
  `mask_apply.apply_mask_to_layer_object` を呼ぶよう統合。
- watch の raster / effect writeback 完了後にもマスクを再適用し、親変更に
  追従する。
- `bname.repair_hierarchy` / `bname.mask_regenerate_all` で全レイヤーへ
  マスクを再適用するよう統合。

## 2026-04-30 — Outliner 中心レイヤー管理へ全面移行

### 追加
- Outliner Object/Collection ベースのレイヤー管理基盤
  (`utils/object_naming.py`, `utils/outliner_model.py`,
  `utils/layer_object_sync.py`, `utils/outliner_watch.py`):
    - B-Name 安定 ID を Object/Collection の custom property
      (`bname_kind` / `bname_id` / `bname_managed` / `bname_parent_key`/
      `bname_folder_id` / `bname_z_index` / `bname_title`) に保持。
    - ルート Collection `B-Name`、`outside`、ページ Collection、
      コマ Collection、汎用フォルダ Collection を mirror で生成。
    - 5 秒間隔 timer scan + 再帰抑止 guard + 差分キャッシュで Outliner
      D&D を低負荷で検出し、raster / 効果線 / GP の Object 親を実 entry
      へ書戻し。
- ページ / コマ Collection のシンプル名命名 (`p0001` / `c01`) と
  カラータグ設定 (ページ=紫 COLOR_06、コマ=水色 COLOR_05)。`outside` も
  シンプル名へ変更。
- `bname.coma_renumber_active_page` operator: アクティブページのコマ ID を
  順番通り c01, c02, ... に再採番。Outliner Collection の bname_id も追従。
- `bname.gp_layer_create_per_object` operator: 1 GP Object = 1 B-Name
  レイヤー モデルで新規 GP Object をアクティブコマ直下に生成。
- `bname.effect_line_create_object` operator: 効果線 GP Object を生成。
- `bname.outliner_apply_view` / `bname.outliner_restore_view` operator:
  Outliner エディタを VIEW_LAYER + alpha sort 表示に切替/復元。B-Name 管理
  マーカー (`bname_outliner_managed`) を立てた area のみ復元対象。
- `bname.mask_regenerate_all` / `bname.mask_remove_orphans` operator:
  ページ / コマ mask Mesh Object を `__masks__` Collection に集約生成・
  孤立掃除。
- `bname.repair_hierarchy` operator: B-Name 階層整合性の自動修復
  (mirror 再走 + 空 bname_id への uuid 割当 + 重複 ID の managed=False
  降格 + マスク再生成 + snapshot 再収集)。
- `BNAME_PT_outliner_layers` (N パネル「Outliner レイヤー」): Outliner
  表示切替 / 新規レイヤー作成 / マスク管理 / 整合性修復 / コマ ID 再採番
  ボタンを集約。

### 変更
- `utils/handlers.py` の load_post / save_pre で
  `mirror_work_to_outliner` を呼び出し、Collection 階層を最新化。コマ blend
  (cNN/cNN.blend) では `prepare_coma_blend_scene` 前に mirror が走らない
  ようにスキップ。
- `utils/layer_reparent.reparent_selected` の完了時に mirror を再走させて
  Outliner 階層を即時反映。
- `operators/raster_layer_op.ensure_raster_plane`: raster plane に B-Name
  安定 ID を stamp し、Outliner mirror 配下に取り込み。`parent_key` に
  `pNNNN:cNN` 形式が来ても page を正しく解決。

### 削除
- `BNAME_PT_layer_stack` / `BNAME_UL_layer_stack` (UIList ベースの
  レイヤーリスト)。Outliner 中心管理に一本化。
- 後方互換用の Object 化 operator 群 (`bname.image_layer_to_object` /
  `image_layers_all_to_object` / `balloon_to_object` / `balloons_all_to_object` /
  `text_to_object` / `texts_all_to_object`) と関連ヘルパ
  (`utils/image_plane_object.py`, `utils/balloon_text_plane.py`)。
- master GP / 効果線 master の移行 operator 群
  (`bname.gp_layer_migrate_master_dryrun` / `gp_layer_migrate_master` /
  `effect_line_migrate_master_dryrun` / `effect_line_migrate_master`) と
  関連ヘルパ (migrate 関数、可逆性メタ `bname_migrated_from_layer` /
  `bname_migrated_from_effect_layer`、`register_master_effect_object`)。
- watch の image / balloon / text write-back (overlay 描画専用で Object
  化対象外のため不要)。

### Blender 5.1.1 実機検証で確認した実装上の制約
- Object/Collection 名は 255 バイト上限。日本語タイトルでは prefix を
  含めて約 80 字で内部切詰め。`utils/object_naming._truncate_utf8` で
  UTF-8 安全に切詰め、`bname_title_truncated` フラグを立てる。
- `CollectionObjects` / `CollectionChildren` に `move()` メソッドが無い
  ため、Collection 内の表示順は名前 prefix と alpha sort で表現する。
- `Material.shadow_method` は EEVEE Next で削除済み。設定しない。
- Library override / linked Object は名前変更不可なので `assign_canonical_name`
  で早期 return。

### テスト (Blender 5.1.1 background)
- アドオン register/unregister 成功
- mock work + 実 work.bname での Outliner 階層生成、kind 別 z_index
  分離、coma renumber、カラータグ反映を検証
- 全 6 バッチの全行精密監査 (高 22 / 中 37 / 低 9 件) を 24 件の修正へ
  集約済み

## 2026-04-30 — 汎用レイヤーフォルダを追加

### 追加
- 画像・ラスター・フキダシ・テキストをまとめられる `BNameLayerFolder` を追加。
- 各レイヤーに `folder_key` を追加し、ページ/コマ/ページ外の実所属
  (`parent_kind` / `parent_key`) とフォルダ表示所属を分離。
- レイヤーリストで汎用フォルダを作成・選択・開閉し、D&Dで対象レイヤーを
  フォルダへ格納/フォルダ外へ取り出せるようにした。
- ページごとに同じIDのフキダシ/テキストがある場合でも、D&D対象外のページへ
  `folder_key` が波及しないようにした。
- 汎用フォルダ自体を移動するとき、同じフォルダ内のフキダシに紐づくテキストを
  二重移送しないようにした。

### テスト
- `test/blender_layer_folder_check.py` を追加。Blender 5.1.1 実機で
  ページ内/コマ内/入れ子フォルダへの所属、フォルダ外への取り出し、
  ページ間で同じIDがある場合の所属分離、フォルダ自体の移動/削除、
  保存スキーマを検証。

## 2026-04-30 — レイヤーリストD&Dの親変更を再実装

### 追加
- レイヤーリスト上で行をページ・コマ・ページ外グループ直下へD&Dしたとき、
  既存のリペアレント処理に委譲して実データの所属も移送するようにした。
- テキスト/フキダシ/コマのページまたぎD&Dに対応し、フキダシの子テキストも
  追従するようにした。
- GP/効果線/画像/ラスターのページ・コマ・ページ外へのD&D親変更を確認し、
  コマを別ページへD&Dしたときも直下の主要レイヤーが新しいコマへ追従するようにした。

### テスト
- `test/blender_layer_stack_dnd_reparent_check.py` を追加。Blender 5.1.1 実機で
  page→page、page→coma、page→outside→page、coma→別page、主要レイヤー種別の
  D&D親変更を検証。

## 2026-04-30 — ビューポート Alt reparent フェーズBを実装

### 追加
- レイヤーリスト最上位に「(ページ外)」グループを追加し、shared コマ/フキダシ/
  テキスト、master ラスター、ページ外画像、root GP/効果線を集約表示。
- Alt+Shift+クリック / Alt+ドラッグ / レイヤースタック同期で、コマ・フキダシ・
  テキスト・画像・ラスターをページ外へ昇格できるようにした。
- ページ外 shared レイヤーを world mm 座標としてビューポート描画。

### 修正
- フキダシをページ外または別ページへ移すとき、子テキストも世界座標を維持して移送。
- コマをページ外へ出すとき、直下のフキダシ/テキスト/画像/ラスター/GP/効果線を
  不整合な親キーのまま残さないようにした。

### テスト
- `test/blender_alt_reparent_phase_b_outside_check.py` を追加。Blender 5.1.1 実機で
  page→outside→coma、コマ外出し、画像/ラスターの outside 化を検証。

## 2026-04-30 — ページ外レイヤーの保存スキーマを追加

### 追加
- `BNameWorkData` に `shared_balloons` / `shared_texts` / `shared_comas` を追加。
- work.json schemaVersion を 3 に更新し、`shared_balloons` / `shared_texts` /
  `shared_comas` / `image_layers` を保存・読込できるようにした。
- `BNameImageLayer` に `parent_kind` / `parent_key` を追加し、画像レイヤーも
  ページ外・ページ・コマ所属を保持できるようにした。

### テスト
- `test/blender_shared_layer_schema_check.py` を追加。Blender 5.1.1 実機で
  shared コレクションと画像レイヤーの work.json round-trip を検証。

## 2026-04-30 — ビューポート Alt reparent フェーズA検証修正

### 修正
- `BNAME_OT_alt_reparent_drag` に残っていた診断用 `print` を削除し、通常操作時に
  コンソールへ大量ログが出続けないようにした。
- Blender 5.1.1 の `KeyMapItems.new(..., head=True)` を使い、Alt+LEFTMOUSE /
  Alt+Shift+LEFTMOUSE の addon keymap を同一キーマップ内の先頭へ追加するようにした。

### テスト
- `test/blender_alt_reparent_phase_a_check.py` を追加。Blender 5.1.1 実機で
  page→coma、別ページ移送、子テキスト連動、マルチセレクト一括 reparent を検証。

## 2026-04-30 — ビューポート Alt 系 reparent の徹底チェック修正

### 修正
- **クリティカル**: cross-page balloon/text 移送時の **id 衝突によるデータ重複**を修正。
  移送先ページに同じ id がある場合、`_allocate_balloon_id` / `_allocate_text_id` で
  新規 id を採番する。子テキストも同様に衝突回避し、balloon の new_id への
  parent_balloon_id 付け替えを実装
- **クリティカル**: `_reparent_balloon` / `_reparent_text` / `_move_child_texts_across_page`
  の元エントリ削除を `if e is src_entry` (Blender wrapper の `is` 比較は不安定) から
  `id` での文字列マッチに置換。これで src ページに古いエントリが残ったまま
  新ページにも複製される重複バグを根本対策
- `flash_error("page", page_id="")` の no-target 呼び出しは状態を残さず早期リターン

### 既知の制限事項 (Phase A)
- balloon_tool / text_tool / object_tool 等の **modal 中の Alt+LEFTMOUSE は当該ツールが
  先取り** する (balloon_tool は Alt+クリックを「テールドラッグ」に使用)。Alt+ドラッグの
  reparent はデフォルトモード (オブジェクトモード/ページモード) でのみ機能。ツール中
  の Alt 系 reparent は別タスクで対応予定
- Cross-page reparent 後、移動したレイヤーの stack uid が変わるため、マルチセレクト
  状態 / アクティブ行の追従が失われる場合がある
- `BNAME_OT_coma_move_to_page` 経由のコマ別ページ移送は work.active_page_index を
  変更するため、ビューが意図せず切り替わる場合がある

## 2026-04-30 — ビューポート Alt 系 reparent (フェーズ A)

ビューポート上の Alt+ドラッグ / Alt+クリック / Alt+Shift+クリックで、選択中の
レイヤーを別のコマ・ページに送れるようにする操作を新設。

### 追加
- **`utils/layer_reparent.py`** を新設。`ClickTarget` データクラスと、ターゲット
  解決 / マルチセレクト一括 reparent / balloon・text・image・raster・gp/effect・
  coma それぞれの個別 reparent を提供。同一ページ内なら parent_kind/parent_key
  を直接書き換え、別ページなら entry をコレクション間で移送 (子テキストも連動)。
- **`ui/reparent_overlay.py`** を新設。`SpaceView3D.draw_handler_add` (POST_VIEW)
  にドロップインジケーター描画を登録。状態 (hover / confirm / error / preview
  card) を `set_*` / `flash_*` で更新する。
- **`operators/alt_reparent_op.py`** に 3 つのオペレーターを新設:
  - `BNAME_OT_alt_reparent_drag`: Alt+LEFTMOUSE で発火。modal でドロップ位置の
    コマ/ページを実線シアンでハイライトし、半透明プレビューカードをカーソル
    追従。Release で reparent + 位置追従 (ドラッグなしでクリックすると親変更
    だけで位置維持)。
  - `BNAME_OT_alt_reparent_into`: Alt+クリックでクリック位置の最深コンテナへ
    reparent (位置維持)。
  - `BNAME_OT_alt_reparent_out`: Alt+Shift+LEFTMOUSE で 1 段浅い親へ reparent
    (位置維持)。コマ→ページに対応。
- キーマップに `LEFTMOUSE alt=True` (drag) と `LEFTMOUSE shift=True alt=True`
  (out) を追加。
- 計画書 `docs/viewport_reparent_plan_2026-04-29.md` を作成。

### 仕様
- マルチセレクト中の全レイヤーを一括 reparent (各レイヤーの位置はそれぞれ維持)。
- クリック位置に複数コマが重なる場合は `z_order` が手前のコマを採用。
- balloon は子テキスト (`parent_balloon_id`) を一緒に reparent + ページ移動。
- 別ページへ送るときは、視覚位置 (世界座標) を維持するため entry.x_mm/y_mm を
  ページオフセット差で補正。

### 実装上の注意
- Blender の CollectionProperty 要素は `is` 比較で別オブジェクト扱いになるため、
  `page_stack_key(page)` での文字列比較に統一 (重大バグの修正含む)。
- layer_stack item.parent_key は collect_targets の heuristic で決まるため、
  reparent 時は entry.parent_key だけでなく item.parent_key も同時に更新する。

### フェーズ B (別 PR 予定)
- balloon / text / coma / image / raster の「ページ外」(work 直下) 昇格
- `BNameWorkData.shared_*` コレクション新設 + 保存スキーマ拡張
- レイヤーリスト最上位に「(ページ外)」グループを表示
- Alt+Shift+クリックで page → 外への昇格を有効化

## 2026-04-29 — Ctrl/Shift マルチセレクト (レイヤーリスト + ビューポート)

### 追加
- **レイヤーリスト**: `BNAME_OT_layer_stack_multi_select` を新設。レイヤー左の RADIOBUT_OFF/ON ボタンを以下のように動作させる:
  - 通常クリック: そのレイヤー単独を選択 (他の selected をクリアして active に)
  - Ctrl + クリック: そのレイヤーの選択をトグル (他の selected は維持)
  - Shift + クリック: active 行から押した行までの範囲を一括選択
- **ビューポート (オブジェクトモード)**: `BNAME_OT_page_pick_viewport` に Ctrl / Shift 修飾を追加し、コマ/ページの複数選択を可能化。
  - Ctrl + クリック: そのコマ/ページの選択をトグル。アクティブ・ページ切替は行わない
  - Shift + クリック: そのコマ/ページを選択集合に追加
  - 通常クリック: 従来通り単独選択 + ページ切替 (旧マルチセレクトはクリアされる)
- 全種別の data PG (`BNamePageEntry` / `BNameComaEntry` / `BNameImageLayer` / `BNameRasterLayer` / `BNameTextEntry`) に `selected: BoolProperty(SKIP_SAVE)` を追加 (フキダシは既存)。GP layer / group / 効果線は Blender 既定の `select` を流用
- `utils/layer_stack` に `is_item_selected` / `set_item_selected` / `clear_all_selection` ヘルパーを追加。スタック行 → 実 PG への select 反映を一元化
- `utils/object_selection` に `page_key` / `image_key` / `raster_key` ヘルパーを追加し、key 集合 → 各エントリの `selected` フラグへの sync を全種別 (page / coma / balloon / text / image / raster) に拡張 (旧 `_sync_balloon_flags`)
- キーマップに `bname.page_pick_viewport` を Ctrl+LEFTMOUSE / Shift+LEFTMOUSE で追加バインド

### 変更
- `_draw_selection_slot` を「アクティブ行表示用ラベル」から「multi-select オペレーターを発火する emboss=False ボタン」に変更し、`operator_context = INVOKE_DEFAULT` で event の Ctrl/Shift を見て分岐
- `BNAME_UL_layer_stack.draw_item` の selected 判定を「アクティブ行 OR data 側 selected フラグ」の OR に変更し、複数行に同時に塗りつぶし状態を出せるように
- `BNAME_OT_page_pick_viewport.invoke` の修飾キー reject を緩和し、Ctrl / Shift 修飾を multi_mode へ流用するよう変更 (Ctrl+Shift は既存 `bname.view_layer_pick` 用に PASS_THROUGH)

### 整合性
- ビューポートの object_selection (key 集合) と各エントリの `selected` フラグが双方向に同期するため、ビューポート Ctrl+クリックでの選択は即座にレイヤーリストの RADIOBUT 表示に反映され、その逆も同様

### 既知の制約
- 行のラベル(名前)領域は Blender の `template_list` 既定の選択挙動 (単独 active 移動のみ) のまま。Ctrl/Shift 修飾は左の RADIOBUT トグルから操作する設計
- balloon/text/object ツール (modal) 中の Ctrl/Shift クリックは各ツール側のロジックを既存利用 (本コミットでは page_pick_viewport だけを変更)

## 2026-04-29 — レイヤーリスト D&D の視覚フィードバックと操作性を改善

### 追加
- D&D 中、半透明のレイヤーカードプレビューと水平のドロップインジケーターをカーソル位置に GPU 描画 (`SpaceView3D.draw_handler` の UI region / POST_PIXEL に `_draw_overlay` を登録)
- レイヤー名のラベル領域全体を `bname.layer_stack_drag` のクリックハンドルに拡張 (GRIP アイコンを狙わなくても、行名のどこをクリックしてもドラッグ開始できる)

### 変更
- ドラッグオペレーターのコミット条件を `LEFTMOUSE PRESS / RELEASE` 両対応に変更し、ボタン経由 invoke (RELEASE 時呼び出し) でも 1 回の追加クリックで完了するように改善
- `_normalize_tree_order` のページ subtree 構築をスタック順尊重に変更 (旧実装は「コマ群を全部出してからページ直下子」固定だったため、ページとその第1コマの間に GP/balloon/text 等を挟めなかった)
- `_draw_square_label` を常に `cell.label(text, icon)` 形式で描画するよう変更し、空ラベルとオペレーターボタンの幅差で同じ depth の行が左右にズレる問題を解消

### Blender API 上の制約 (既知)
- 完全な「ドラッグオンプレス」(マウス押した瞬間にドラッグ開始) は、Blender のボタンが PRESS でなく RELEASE で invoke する仕様のため再現不可。現状は「クリック → カーソル移動 → クリックでコミット」の 2 クリック型ドラッグ + GPU プレビューによる視覚フィードバックで近似している

## 2026-04-29 — レイヤー D&D を CSP / Photoshop 風の挙動に修正

### 修正
- フォルダ行を選択した状態で「レイヤーを追加」を実行しても、新しいレイヤーがフォルダの兄弟として追加されてしまう問題を修正（`_parent_key_for_new_item` でフォルダ選択時はフォルダの key を返すよう変更）
- フォルダ配下の GP レイヤーが `_partition_gp_targets` で `parent_key` を strip され、レイヤーリスト sync 後に root へ戻され、続けて `apply_stack_order` がフォルダ外へ追い出してしまう破壊的バグを修正（フォルダ親の場合も `target.parent_key` を保持し、`_normalize_tree_order` でフォルダ配下にネストするよう変更）
- レイヤーリスト D&D の Y-only ドラッグでは「同一ページ内の `depth` 増加」を block していたため、ページ直下のレイヤーをコマ行直下にドロップしてもコマの子に入らない問題を修正（depth-increase guard を撤廃し、CSP / Photoshop と同じく Y ドラッグでもコンテナの子になれるよう変更）
- `BNAME_OT_layer_stack_drag` の `_drag_to_event` で `apply_stack_order_if_ui_changed` を使っていたため、最初の MOUSEMOVE で signature が None になり reparent が反映されない問題を修正（`apply_stack_drop_hint` を毎フレーム直接呼ぶよう変更）

## 2026-04-29 — 新規作品作成/作品クローズ時の page Collection orphan 掃除

### 追加
- `gp_utils.remove_all_page_gpencils()` を追加。`page_pNNNN` Collection と旧仕様 `page_pNNNN_sketch` GP オブジェクト・データブロック・紙メッシュをまとめて掃除する

### 修正
- 同一 Blender セッション内で複数回 `work_new` / `work_close` を行うと、前作品の `page_pNNNN` Collection や旧 GP オブジェクトが `B-Name` ルート Collection 配下に残骸として蓄積する問題を修正（`work_new` と `work_close` の冒頭で `remove_all_page_gpencils()` を呼ぶよう変更）

## 2026-04-29 — レイヤーリストの重複行とクロスページ親変更を修正

### 修正
- フキダシが複数のコマ矩形に空間的に重なる場合、同じフキダシがレイヤーリストに2行表示される問題を修正（`_explicit_entry_parent` を `panels_by_key` 限定からページ全コマ走査に変更し、per-panel 呼び出しでオーソリティ親がスコープ外のエントリを skip するように整理）
- レイヤーリスト上で別ページのコマへ D&D した時に、保存データは元ページに残ったまま親キーだけ別ページを指す矛盾状態が作られる問題を修正（`_apply_stack_drop_hint` でページ間親変更を reject）
- `collect_targets` 末尾に UID 重複排除の防御コードを追加し、上記以外の経路で重複が紛れ込んでもレイヤーリストには 1 行しか出さないように修正

### 検証
- AI 視認による Blender 実機テスト（D&D 並び替え、レイヤーをページ/コマに入れる・出す、レイヤー移動ツールでページ/コマを動かしたときの GP/ラスター/テキスト/フキダシ/効果線の追従）を実施し、修正後に意図通りの挙動になることを確認

## 2026-04-29 — レイヤーリストD&Dと親子追従の修正

### 追加
- レイヤーリストのD&D並べ替え、ページ/コマ配下への移動、ページ/コマ移動時の子レイヤー追従を検証する Blender 実機テストを追加

### 変更
- 統合レイヤーリストの親子付け対象に、GP、ラスター、テキスト、フキダシ、効果線を一貫して扱うように変更
- レイヤーリストから追加するラスター描画レイヤーが、選択中のページ/コマ配下へ作成されるように変更

### 修正
- レイヤーリスト内でレイヤーカードをD&Dしても、ページ内やコマ内へ正しく入らない問題を修正
- コマをレイヤー移動ツールやオブジェクト操作で移動した時に、配下のGP、ラスター、テキスト、フキダシ、効果線が追従しない問題を修正
- ラスター画像の未保存ピクセルが画像パス再設定時に保存済みPNGへ戻り、コマ移動時のラスター追従で画素が消える問題を修正

## 2026-04-29 — ラスター描画レイヤーの初期化と描画モード安定化

### 追加
- ラスターレイヤー追加時の透明 PNG 保存、Texture Paint 入退出、描画モード中の表示設定更新を検証する Blender 実機テストを追加

### 変更
- ラスター描画用マテリアルをバージョン付きノード構成として管理し、通常の不透明度・線色更新ではノードツリーを作り直さないように変更
- ラスター描画モードへ入る前に Object モードでラスター平面とマテリアルを同期し、Texture Paint 中のメッシュ/マテリアル差し替えを避けるように変更

### 修正
- ラスターレイヤー追加時に最初の見開きの存在しない右ページが白く表示され、描画ツール選択後に黒くなる問題を修正
- 空のラスター画像と保存 PNG が不透明黒として初期化され、どの色でストロークしても描画結果が見えない問題を修正
- ラスター描画モードへの切り替え時にマテリアルノードを再構築し、Blender が `node_copy_with_mapping` 周辺でクラッシュし得る問題を修正
- ラスター描画ブラシの初期色が白のまま残り、グレースケール描画時に透明キャンバス上で見えない線になり得る問題を修正

## 2026-04-29 — 日本語IME入力と右クリックメニューの安定化

### 追加
- Windows IME の半角/全角キー相当の入力をテキスト編集中に処理し、Blender のモーダル入力中でも IME のオン/オフを切り替えられるように追加
- 右クリックメニュー項目が空にならないことを検証する Blender 実機テストを追加
- テキストIMEの合成文字列、縦書きカーソル、選択ハイライトの座標を検証するランタイムテストを拡充

### 変更
- オブジェクトツール、テキストツール、フキダシツール、効果線ツール、枠線選択ツールの右クリック対象判定を共通化し、どのツール中でもコマ、フキダシ、テキスト、効果線を同じ選択メニューへ接続するように変更
- 共通右クリックメニューで対象がない場合は「対象が選択されていません」と表示し、効果線以外では「リンク複製」を無効表示に変更

### 修正
- テキストツールで日本語IME入力へ切り替えられない問題を修正
- 縦書きテキストの選択ハイライトとカーソルが文字列より左へずれる問題を修正
- 効果線ツール中など、現在のツール種別と異なるオブジェクト上を右クリックした時に B-Name メニューが空表示になる問題を修正

## 2026-04-29 — 右クリック詳細設定メニュー

### 変更
- ビューポート上のテキスト、フキダシ、効果線、コマ枠線などをクリック選択しただけで詳細設定ダイアログを開く挙動を廃止
- 選択対象の右クリックメニューに「詳細設定」「複製」「リンク複製」「削除」を追加し、詳細設定は右クリックメニューから明示的に開く操作へ変更
- Blender標準のオブジェクト右クリックメニュー内の B-Name メニューにも同じ選択対象コマンドを直接表示するように変更

### 修正
- 対象外の場所を右クリックした時に、前回選択中の要素のコンテキストメニューが誤って開かないように修正
- 右クリックメニューからの削除を、既存の確認ダイアログ付き削除オペレーター経由で実行するように整理

## 2026-04-29 — レイヤー詳細設定の動作反映修正

### 変更
- コマ詳細設定から未実装の「自動くり抜き」チェックと未対応形状の直接選択を外し、表示中の項目だけが実際に反映されるように整理
- ページ/コマの表示名を統合レイヤーリスト行にも表示し、詳細設定で変更した名前がリスト上で確認できるように変更

### 修正
- 画像レイヤー詳細設定の回転、不透明度、色合い、明度、コントラスト、2値化をビューポート表示にも反映するように修正
- 画像レイヤーとページ表示位置の詳細設定変更時にビューポートを即時再描画するように修正
- コマ枠線の線種、辺ごとの表示/線種/線幅/線色、白フチの辺ごとの有効/幅をビューポート表示へ反映するように修正
- フキダシの線種とカスタム形状、テキストの白フチと斜体指定、ラスター描画レイヤーの表示/不透明度/線色が詳細設定変更後すぐ反映されるように修正
- ビューポート用の線種分割ヘルパーと決定的テストを追加

## 2026-04-29 — ビューポートナビゲーションのモーダル干渉修正

### 修正
- フキダシ、効果線、テキスト、オブジェクト、レイヤー移動、枠線カット、枠線選択、コマ頂点編集ツールの待機中に、3Dビューポート右上のナビゲーションギズモ、ズーム、ビュー移動ボタンのマウスイベントを奪わないように修正
- ナビゲーションボタン押下後にカーソルがヒット範囲外へ移動しても、左ボタンを離すまで各モーダルツールがイベントをパススルーし続けるように修正
- 右上ナビゲーションUIの表示設定、ギズモ表示設定、ヒット範囲の判定を共通化し、座標判定テストを追加

## 2026-04-28 — B-Name構造改修 / コマblendテンプレート / 魚眼F1+F2

### 追加
- 作品構造を新 `.bname` 形式へ移行し、ページ直下に `pNNNN/cNN/cNN.blend`、`cNN.json`、参照画像、サムネイル、プレビューをまとめる構造を追加
- 作品ごとに「コマ3Dテンプレート」`.blend` を指定し、新規 `cNN.blend` 作成時だけテンプレートを初回コピーする仕組みを追加
- テンプレート由来のCollection、ViewLayer、マテリアル、NodeGroup、Cameraを保持しつつ、B-NameのコマID、ページID、下絵、カメラ設定を注入する実機テストを追加
- 魚眼レイアウトモード、縮小プレビューモード、Pencil+4線幅保存/縮小連動の基礎機能を追加
- 効果線レイヤー詳細設定に、基準位置、基準位置のずれ、ギザギザ、線の間隔、乱れ、まとまり、最大本数、流線本数上限、入り抜き、線色/塗り色の設定を追加
- 効果線レイヤー詳細設定に始点形状・終点形状、始点をコマ枠へ合わせる設定、始点/終点形状ラインの色分けを追加
- 効果線タイプ「流線」の始点線・終点線を、閉じた形状ではなく開いたベジェ曲線ガイドとして生成する処理を追加

### 変更
- 旧 `panel_*` 命名を `coma_*` / `cNN` へ整理し、コマ移動・複製・見開き統合/解除・コマ編集モード遷移を新構造へ対応
- `work.json` に `comaBlendTemplatePath` を保存し、作品単位でコマテンプレートを切り替えられるように変更
- コマ用 `.blend` を開いた時、B-Name内部のページ一覧用Collectionだけを掃除し、テンプレートに含まれるユーザー素材やfake user付きデータを保持するように変更
- ドキュメントのパス説明と設計意図を `pNNNN/cNN` 構造へ更新
- 効果線の詳細設定をレイヤー単位で保存し、選択時に復元、設定変更時に選択中レイヤーへ即時反映するように変更
- 効果線レイヤーの詳細設定UIを「種類」「始点形状」「終点形状」「線」「描画間隔」「流線」「入り抜き」「色」に整理
- 効果線の描画位置を内側固定へ変更し、「描画位置」「線の長さ」「基準位置をギザギザにする」設定を廃止
- 流線タイプでは閉じた形状設定と全体回転を表示せず、流線角度で始点線・終点線の向きを連動するように変更

### 修正
- コマ移動後の `cNN.json` 内部IDが古いまま残る問題を修正
- コマ移動で移動元IDと移動先IDが同じ場合に、同名リネームで失敗する問題を修正
- 見開き統合直後にアクティブページ index が無効化され、見開き解除できない問題を修正
- 縮小モードON中のPencil+4線幅保存で、縮小後の線幅を元値として保存し得る問題を修正
- 効果線レイヤーの移動・複製・削除で、詳細設定メタデータが汚染または残存する問題を修正
- リンク効果線で流線の始点線・終点線の向きがリンク先へ伝播しない問題を修正

## 2026-04-28 — ラスター描画レイヤー Phase 1

### 追加
- レイヤー種別に「ラスター (描画)」を追加し、300dpi / 150dpi のグレー8bit描画レイヤーをレイヤー追加メニューから作成できるように追加
- ラスターPNGを作品直下の `raster/<uuid>.png` に集約し、Texture Paint用PlaneとEmissionシェードレスマテリアルを自動生成する仕組みを追加
- Texture Paint入退出、ブラシ色のグレースケール補正、ロック/非表示レイヤーの描画拒否、レイヤー削除時のPNG `.trash/` 退避を追加
- Blender保存前のdirtyフックで、変更されたラスターPNGだけを書き出す処理を追加
- ラスターの線色と不透明度を出力時に適用し、元PNGはグレースケール描画データとして保持する出力合成を追加

### 変更
- `work.json` スキーマを更新し、ラスター描画レイヤーのメタデータを保存/復元するように変更
- 作品の新規作成、読み込み、クローズ時にラスターのPlane/Imageランタイムを同期・破棄するように変更

## 2026-04-28 — レイヤー詳細UIの分割

### 変更
- 統合レイヤーリストの選択中レイヤー詳細設定 UI を `panels/layer_stack_detail_ui.py` へ分離
- `panels/gpencil_panel.py` はレイヤー行表示とパネル登録に寄せ、既存の詳細設定ポップアップ呼び出しは互換ラッパー経由で新モジュールへ委譲するように整理

## 2026-04-28 — テキストインライン編集と選択範囲スタイル

### 追加
- テキストツールのインライン編集中に、テキスト上の左ドラッグで文字範囲を選択できるように追加
- 選択範囲確定時に、色、太字、斜体、文字サイズ(Q)、フォントを設定するポップアップを表示するように追加
- テキスト本文内の部分スタイルを保存/読み込みできる `styleSpans` を追加

### 変更
- テキスト本文入力ダイアログを廃止し、本文編集はビューポート上のインライン入力へ統一
- テキストの部分スタイルを、ビューポート表示、組版、画像出力へ反映するように変更

### 修正
- 本文の挿入/削除時に、選択範囲のフォント指定と部分スタイル範囲が本文に追従するように修正
- インライン入力中の IME 切り替えキーを Blender/OS 側へ通し、日本語入力切り替えを阻害しないように修正

## 2026-04-28 — ページ一覧ビューの表示UI整理

### 変更
- ページ一覧ビューとして開いた 3D View では、Blender 標準のオーバーレイ、ツールバー、サイドバー、ツール設定ヘッダー、ヘッダー、ギズモ、HUD、アセットシェルフを非表示にするように変更
- ページ一覧ビューではカメラビューと「カメラをビューに」を解除し、正投影のページ一覧表示を維持するように変更
- ページ一覧ビューを解除またはアドオンを unregister した時、非表示化前の 3D View 表示状態を復元するように変更

### 修正
- コマ編集カメラ更新、B-Name サイドバー自動表示、ビューポートオーバーレイ一括切替がページ一覧ビューの表示設定へ干渉しないように修正
- ページ一覧ビューのフィット計算で範囲外ページ判定を参照する際の import 漏れを修正

## 2026-04-28 — 用紙ガイド線の表示調整

### 変更
- トンボ、裁ち落とし枠、仕上がり枠、基本枠、セーフラインなどの用紙系ガイド線を、ズーム倍率に関係なくビューポート上で常に 1px 幅で表示するように変更
- 用紙系ガイド線の不透明度を 50% に変更

## 2026-04-28 — クリック選択時の詳細設定ポップアップ

### 追加
- 枠線選択ツールで辺・頂点・枠線全体をクリック選択した時、対象コマの詳細設定ダイアログを開けるように追加
- フキダシ、テキスト、効果線を各ツールでクリック選択した時、既存のレイヤー詳細設定ダイアログを開けるように追加

### 修正
- 移動・リサイズなどのドラッグ操作では詳細設定ダイアログを開かないよう、クリック確定時だけ開く判定へ整理
- 枠線の辺や頂点の詳細設定を開く時、ダイアログ側のレイヤー選択処理で選択対象が枠線全体へ上書きされないように修正
- ダブルクリック操作やツール外クリックで、遅延表示される詳細設定ダイアログが誤って開かないようキャンセル条件を追加

## 2026-04-28 — ページ数範囲の非破壊化 / 保存読込確認

### 変更
- 作品情報の開始/終了ページ範囲を縮めた時、範囲外になった既存ページを削除せず、非表示の範囲外ページとして保持するように変更
- ページ数を再度増やした時、保持していた範囲外ページが再び表示対象として復帰するように変更
- 範囲外ページはページ一覧、ビューポート選択、レイヤーリスト、ページ移動ショートカット、枠線編集/カット、テキスト選択、全ページ書き出し/PDF書き出しの対象から外すように整理

### 修正
- 範囲外ページ配下の Grease Pencil レイヤーを一時非表示にし、ページ復帰時に元の表示状態へ戻すように修正
- 旧 `pages.json` にページ範囲フラグが無い場合、既存ページが読み込み時に突然隠れないよう終了番号を既存ページ数へ移行する処理を追加
- テキスト、フキダシ、効果線の保存/読込経路を実機確認し、通常保存時に `page.json` と `work.blend` に保持されることを確認

## 2026-04-28 — ページ一覧専用ビュー試作

### 追加
- **ページ一覧専用ワークスペース** — `B-Name Pages` ワークスペースを作成/再利用し、3D View の一部をページ一覧ビューとして表示できるように追加
- **ページ一覧ビュー位置指定** — ページ一覧ビューを左/右/上/下のどこに表示するかを選択できる設定を追加
- **コマ編集ファイル内のページ一覧ビュー起動** — コマ編集ファイル上部の「ページ一覧に戻る」セクションからも、ページ一覧ビューを開けるように追加

### 変更
- ページ一覧ビューとしてマークされた 3D View では、コマ編集ファイル中でも全ページ一覧オーバーレイを表示するように変更
- ページ一覧ビュー上でページやコマをクリックした時、現在のページ/コマ選択とレイヤーリストの選択状態へ反映するように変更

### 修正
- 既存のページ一覧ビューがある状態で表示位置を変更した時、通常の編集ビューを誤ってページ一覧ビュー扱いにする可能性を修正
- ページ一覧ビューのエリア記録が画面構成変更や複数ウィンドウ時に古い 3D View を参照し続けないよう整理

## 2026-04-27 — コマ編集UI整理 / 通常保存同期 / プレビュー改善

### 変更
- コマ編集ファイルでは B-Name パネルを「ページ一覧に戻る」と「コマ編集: カメラ」だけに整理し、作品/用紙/ツール/ビュー/レイヤー/Grease Pencil/書き出しセクションを非表示化
- 「コマ編集: カメラ」セクションをコマ編集ファイルで既定展開に変更
- B-Name パネル上部の「保存」「閉じる」ボタンを削除し、通常の Blender 保存操作へ導線を統一
- Blender 標準の保存 (`Ctrl+S` など) 時にも B-Name の `work.json` / `pages.json` / 全ページの `page.json` を自動同期するように変更
- B-Name の「作品を保存」も通常保存と同じ JSON 同期経路を使うように整理

### 修正
- コマ編集ファイルからページ一覧へ戻った時に更新されるコマプレビュー画像が低解像度になる問題を修正し、フル解像度で保存するように変更
- 通常保存時に非アクティブページの `page.json` が更新されない可能性を修正

## 2026-04-27 — レイヤーパレット改良 / テキストツール / ブラシサイズ操作

### 追加
- **レイヤー追加メニュー** — レイヤーリスト右側に追加/複製ボタンを追加し、ページ、コマ、グリースペンシル、画像、フキダシ、テキスト、効果線、フォルダを選択中レイヤーの同階層前面へ追加可能化
- **テキストツール** — T キーでテキストツールへ切り替え、クリック地点にテキストレイヤーを作成してインライン入力を開始
- **ブラシサイズドラッグ** — CLIP STUDIO PAINT 互換の Ctrl+Alt+ドラッグでグリースペンシルのブラシサイズを変更可能化

### 変更
- レイヤーリストの列順を、表示/非表示、選択状態、階層、レイヤー種別アイコン、レイヤー名の順に整理
- 選択中レイヤー設定をレイヤーリスト下部へ移動し、グリースペンシルの線色/塗り色を正方形スウォッチで表示
- ページレイヤー表示名を `p000`、コマレイヤー表示名を `c00` 形式に変更
- ページ/コマにも表示切替を追加し、表示状態を保存/読み込み対象化。表示ボタンだけを押した場合はアクティブ選択を変更しないように変更
- Ctrl+Alt ブラシサイズドラッグを、ドラッグ開始点を中心にカーソル位置まで円を広げる挙動へ変更。中心では表示上のブラシサイズを `0px` とし、円の輪郭がカーソル位置に一致するように調整

### 修正
- レイヤーリストでページ追加・GP追加・枠線カット後の新規コマが即時表示されない問題を修正
- ページ折りたたみ時に内包コマが更新操作まで表示され続ける問題を修正。折りたたみで隠れる子が選択中の場合は親ページへ選択を移し、別レイヤーへハイライトがずれないように修正
- 非表示レイヤーのアイコンが閉じた目ではなく別アイコンになる問題を修正
- レイヤーリスト選択からビューポート上の選択状態へ同期しない問題を修正
- 枠線選択ツール起動時の未定義関数エラー、頂点移動時に一部の隣接辺しか連動しない問題、枠線編集ツールのボタンハイライト/カーソル更新漏れを修正
- Ctrl+Alt ブラシサイズドラッグ後に実ブラシサイズが `0` のまま残り、グリースペンシルで描けなくなる問題を修正。プレビュー表示は `0px` を許可しつつ、実ブラシサイズは描画可能な最小値以上に保護

## 2026-04-27 — 統合レイヤーツリー / レイヤー移動ツール / ビュー整理

### 追加
- **統合レイヤーツリー** — ページ一覧とコマ一覧をレイヤーリストへ統合。ページを展開すると配下のコマとコマ内レイヤーをツリー表示し、リスト右ボタンとドラッグ&ドロップで並び替え可能化
- **レイヤー移動ツール** — 選択中のページ、コマ、通常レイヤーをビューポート上のドラッグで移動。コマ移動時は親ページを追従させず、コマ内レイヤーだけを連動
- **ツールセクション** — 用紙セクション直下に、Object/Draw/Edit モード、枠線カット、枠線選択、レイヤー移動をアイコンボタンで集約

### 変更
- ビューの基本状態を「全ページを一覧」に固定し、「ページに合わせる」は一覧状態のまま選択ページへフォーカスする挙動に変更。「一覧モードを終了」は削除
- ページ一覧/コマ一覧からビューセクションと枠線ツールセクションを独立パネル化し、旧コマ一覧の Z 順序セクションを廃止
- Object モードでコマ内部をクリックした時、コマと親ページを同時にアクティブ化し、統合レイヤーツリー上の選択状態へ同期
- ページ移動時は同階層レイヤーと配下コマを追従、コマ移動時はコマ内レイヤーを追従するように変更
- コマファイルを開いた時、B-Name サイドバーが自動で開いた状態になるように変更
- ページ一覧ファイル (`work.blend`) を新規作成/保存/ロードした時、View3D の Blender 標準「オーバーレイ」を自動でオフにするように変更

### 修正
- ページをページ/コマ内へ移動、コマをコマ内へ移動する不正な階層操作を拒否し、ページ外へのコマ移動は許可
- ページ一覧ファイルではコマ内レイヤー作成/移動、コマファイルでは対象コマ外レイヤー作成/移動を拒否
- 選択中ページと同様に、選択中コマもビューポート上でハイライト表示
- コマプレビュー/サムネイルで背景透明化設定がある場合、表示用キャッシュと書き出し結果へ透明化を反映

## 2026-04-27 — ページクリック選択 / UI色変換補正 / リファクタリング

### 追加
- **ページクリック選択** — Object モード時にビューポート上のページを左クリックすると、そのページをアクティブ化。通常の Blender オブジェクト選択を妨げないよう `PASS_THROUGH` で処理
- **Blender UI色値変換ヘルパ** — `utils/color_space.py` を追加し、ユーザー指定の UI 表示値 (sRGB) と Blender 内部の scene-linear 値の相互変換を共通化

### 変更
- セーフライン外塗りつぶし色の初期値を、Blender UI 表示上の明度 0.7 と一致するよう内部値を scene-linear に変換して保存・描画
- 既存作品/旧JSONのセーフライン色 `#808080` / `#B3B3B3` / `#DADADA` などを、UI 表示上の明度 0.7 として読み込む互換処理を追加
- `utils/panel_camera.py` からカメラ参照画像生成/同期処理を `utils/panel_camera_refs.py`、定数を `utils/panel_camera_constants.py` に分離
- `ui/overlay.py` からフキダシオーバーレイ描画を `ui/overlay_balloon.py` に分離
- `io/export_pipeline.py` からフキダシ書き出しを `io/export_balloon.py`、PSD保存処理を `io/export_psd.py` に分離
- `operators/panel_edge_move_op.py` から枠線スタイル編集OperatorとWindowManagerプロパティを `operators/panel_edge_style_op.py` に分離

### 修正
- セーフライン外塗りつぶし色で、コード上の 0.7 が Blender UI 上では約 0.854 と表示されていた sRGB/linear 変換のズレを修正
- 全体リファクタリングで 1500 行超えだった主要ファイルを 1500 行未満に整理

## 2026-04-26 — コマ編集カメラ / 下絵生成 / Pillow 同梱

### 追加
- **コマ編集モード用カメラパネル** — コマ編集用 blend に専用 `Camera` を整備し、焦点距離、クリップ範囲、カメラシフト、魚眼モード、縮小モード、解像度プリセット、カメラアングル保存/適用を操作可能化
- **カメラ下絵同期** — 元ページのネーム画像と現在コマのクロップ画像を生成し、コマ編集 blend 内カメラの background image として設定。全ページ表示/現在ページのみ表示、ネーム/コマ別の不透明度、再読み込み、表示切替に対応
- **Pillow wheel 同梱** — Windows x64 用 Pillow 12.2.0 (`cp311` / `cp312` / `cp313`) を `wheels/` に追加し、`blender_manifest.toml` に登録。開発中の直接ロードでも使えるよう `utils/python_deps.py` で互換 wheel を自動展開・`sys.path` 追加
- 各ページ上部に 3 桁の大きなページ番号表示を追加

### 変更
- コマ編集用 blend を開いた時、`load_post` / 新規 bootstrap / コマ編集遷移後の各経路でカメラと下絵を同期するように変更
- Pillow が使える環境では、コマ編集カメラ下絵のページ画像とコマクロップを自動生成するように変更
- Windows binary wheel の DLL 検索パスを保持し、セッション中に Pillow の C 拡張が安定して読み込めるように変更

### 修正
- 下絵生成に失敗した時、既存のカメラ background image を消してしまう問題を修正
- コマ編集終了時のサムネイル撮影に、カメラ下絵が写り込む問題を修正
- カメラシフトの Shift 微調整で基準点がずれてジャンプする可能性を修正
- wheel 展開失敗時の再試行不能、DLL ディレクトリハンドル破棄、zip 展開パス検証不足を修正

## 2026-04-26 — 枠線編集ツール群 / Q数フォントサイズ / 用紙プリセット改良

### 追加
- **枠線カットツール** (`bname.panel_knife_cut`、F キー) — CLIP STUDIO PAINT 互換の任意角度カット。Shift で軸ロック (水平/垂直)。ドラッグ範囲で対象コマを判定 (アクティブ非依存)、1 コマだけ対象化、連続カット可能、各カットを独立 undo step として記録、カット線方向に応じてコマ間隔 (上下/左右スキマ) を自動適用
- **枠線選択ツール** (`bname.panel_edge_move`、G キー) — 辺/頂点をクリック選択 (シングル=辺、ダブル=枠線全体) してドラッグ移動。隣接コマと連動して gap_mm を維持。N パネルから個別辺の color/width を編集可能 (`edge_styles` CollectionProperty)
- 辺中点に **三角ハンドル** 2 つを表示。クリックでその先の隣接コマ辺/基本枠/裁ち落とし枠まで panel を拡張。隣接コマと既に重なっている時は離れる方向の▲で gap 分のスキマを空け、離れている時は隣接方向の▲で gap=0 でピッタリ重ねる
- **Q 数フォントサイズ** (写植由来、1 Q = 0.25 mm) — `BNameDisplayItem.font_size_q` (default 20.0) を追加。`utils/geom` に `q_to_mm` / `mm_to_q` / `q_to_pt` / `pt_to_q` を追加
- **F キー Esc** で `bname.exit_panel_mode` をパネル編集モード中に発火 (3D View kc のみ)
- 用紙プリセットドロップダウン (`WindowManager.bname_paper_preset_selector`) — 選択で即時適用
- 衝突キー無効化機構 (`disable_conflicting_keys` / `restore_conflicting_keys`) — Fluent 等の他アドオンが addon kc に登録した F/G の単独キー kmi を起動時に検出して `active=False`、unregister 時に復元

### 変更
- ダブルクリックでコマ編集モード遷移は **Object モード時のみ** 発火 (GP 描画モード等では PASS_THROUGH)
- panel border 線幅の既定値: 0.8 → **0.5** mm
- 全 rect outline 描画を `_draw_rect_outline_mm` / `_draw_segments_mm` でズーム連動 (mm 単位太さ) に変更 — canvas 0.30, bleed 0.30, finish 0.50, inner_frame 0.35, safe 0.30, panel border は `border.width_mm`、トンボ 0.40
- 用紙プリセット保存ダイアログのタイトル: 「作品プリセットとして保存」→「**用紙プリセットとして保存**」
- 「見開き表示」(`is_spread_layout`) フラグを削除 — 死んだフラグ
- 作品情報フォントサイズの内部単位を pt → Q に変更、JSON は旧 `fontSizePt` から自動マイグレート
- modal 中の Ctrl+Z / Ctrl+Y を検知して `{"FINISHED", "PASS_THROUGH"}` 返却 — PropertyGroup 参照 stale による C レベル crash を回避
- modal 中の他ショートカット (O / P / G / F / , / .) を割込み検知 → modal 終了して譲る (二重起動防止のため自身のキーは consume)

### 修正
- 枠線選択ツールの辺ドラッグ + ▲拡張で **隣接辺の角度を維持** — `_line_intersect` で「prev/next 辺の line と新 selected line の交点」に共有頂点を補正することで、斜めの辺が重なり/離れずに追従
- `_do_extend` のスナップ仕様: bleed=1mm 外側、隣接コマ=ピッタリ or gap_mm 確保 (重なり状態と▲方向で自動切替)、基本枠=ピッタリ
- panel polygon (cut 後の多角形) の overlay 描画対応 (`_draw_panels` で polygon 分岐)
- knife_cut で同 panel の対辺を拡張先候補から除外 (細い panel での反転バグ回避)

## 2026-04-26 — 作品情報配置 / トンボ / ノド・小口バグ修正

### 追加
- 裁ち落とし枠オーバーレイ (`bleed_rect` = 仕上がり枠 + 裁ち落とし幅) — 用紙パネルの「裁ち落とし幅 (mm)」が 0 より大きい時に枠線を描画
- **トンボ描画** (CLIP STUDIO PAINT 互換仕様) — コーナートンボは裁ち落とし枠の角の外側に二重 L 字 (内側=仕上がり位置の延長線、外側=裁ち落とし位置の延長線)、センタートンボは各辺中央に + 字を配置 (`_draw_trim_marks`)
- 作品情報の各項目に **フォントサイズ (pt) スライダー** を追加 (作品名/話数/サブタイトル/作者名/ページ番号、既定 7.0pt)
- `utils/geom.bleed_rect(paper)` — 裁ち落とし枠の Rect ヘルパー
- `_draw_line_segments` — 独立した線分群を一括描画する gpu ヘルパー

### 変更
- **作品情報テキストの位置基準を「基本枠内側」→「裁ち落とし枠外側」に変更** — 各項目 (作品名/話数/サブタイトル/作者名/ページ番号) を裁ち落とし枠の外側に押し出して配置 (top-* は枠の上、bottom-* は下、left/right は左右端基準)
- 原稿上の表示の position 選択肢を **9 通り → 6 通り** に削減 (top/bottom × left/center/right)。中央段 (middle-*) は枠外配置で自然なアンカーが取りづらいため除外
- 新規作品の表示位置初期値を更新: 作品名=左上、話数=上中央、サブタイトル=右上 (作者名=右下・ページ番号=下中央 は据え置き)
- フォントサイズ既定値を 9.0pt → **7.0pt** に変更 (新規 `BNameDisplayItem` および JSON ロード時のフォールバック)

### 削除
- 「**見開き表示**」(`is_spread_layout`) フラグを削除 — UI に露出するだけで `overlay` / `page_grid` のレンダリング/レイアウトロジックではどこからも参照されていない死んだフラグ。現在の B-Name は常時グリッド配置で見開みは既定の挙動

### 修正
- **ノド/小口の左右反転バグ** ([utils/page_grid.py](utils/page_grid.py) `is_left_half_page`) — `read_direction="left"` (日本マンガ右綴じ) では `page_grid_offset_mm` の col 増加 = 物理左進行のためペア内 (c=0, c=1) の物理左右が反転するが、判定ロジックが論理 slot 偶奇のままで物理位置と逆になっていた。`read_direction` を引数に追加し、RD="left" なら c=1 を物理左、RD="right" なら c=0 を物理左と判定するよう修正。これによりセーフラインの「ノド (中央寄り、広いマージン)」「小口 (外側、狭いマージン)」と基本枠の「ノド方向への変位」が物理位置に対し正しく適用されるようになった

### マイグレーション
- `display.position` の `middle-left` / `middle-center` / `middle-right` は読み込み時に `bottom-left` / `bottom-center` / `bottom-right` へ自動マイグレート ([io/schema.py](io/schema.py) `_DISPLAY_POSITION_MIGRATE`)
- 既存 .bname の `paper.isSpreadLayout` フィールドは無視される (互換性問題なし)

## 2026-04-25 — ビューポート操作 / 紙メッシュ実体化 / ショートカット拡張

### 追加
- 統合ナビゲートモーダル `bname.view_navigate` (Space PRESS で起動、modal 中の `Shift`/`Ctrl` 状態でパン/回転/ズームを動的切替)
- ナビゲート modal 内の LMB クリック (動かさず離す) で **25% ステップズーム** (Alt 押下中はズームアウト方向)
- modal 内 ダブルクリック検出を自前実装 (Blender は modal 中 `DOUBLE_CLICK` イベントを発火しないため)
- ショートカット 4 種を追加 (preferences でキー / 修飾キーをカスタマイズ可能)
  - `O` → オブジェクトモード切替 (`bname.set_mode_object`)
  - `P` → 描画モード切替 (`bname.set_mode_draw`)
  - `,` → 次ページにフォーカス (`bname.page_next`)
  - `.` → 前ページにフォーカス (`bname.page_prev`)
- 描画モード時の `C` キーでブラシ Asset Shelf 表示切替 (`bname.toggle_asset_shelf`)
- 紙メッシュの実体化 (`page_NNNN_paper`) — Plane + 共有白マテリアル `BName_Paper_White`。GP との Z 順を物理レイアウトで制御するため、用紙塗りを GPU overlay からジオメトリパスに移行
- `keymap.rebuild_keymap_from_prefs()` API — preferences の値変更時に自動でキーマップを作り直す (再起動不要)

### 変更
- ビューポート背景を Blender 既定の灰色テーマに戻し、用紙だけ白い実体メッシュで表示
- GP オブジェクトの location.z を `+1mm` に持ち上げて紙メッシュ (z=0) より手前に配置 (`page_grid.GP_Z_LIFT_M`)
- B-Name 専用キーマップを独自名 `"B-Name Viewport"` から Blender 標準名 `"3D View"` (addon kc 層) に変更 — addon kc のキーマップは標準名と一致しないと評価ループに乗らない
- `Window` キーマップにも修飾+ナビゲートキー (Shift+Space, Ctrl+Space) を先取り登録 (`screen.screen_full_area` 等との衝突回避)
- 過去ファイルの白く保存されたビューポート背景を、ロード時にテーマ既定 (灰色) に自動復元

### 削除
- `override_defaults` / `restore_defaults` を no-op 化 — default kc の `KeyMapItem.active` を一括書換する方式は、アドオン無効化中に Blender 内部のキーマップ再構築とレースして `EXCEPTION_ACCESS_VIOLATION` を引き起こすため除去 (キーマップ評価優先順 `addon > user > default` だけで先取りは成立する)
- `apply_paper_background_color` を削除し `reset_viewport_background_to_theme` に置換 — 旧実装は Blender 自体の solid 背景色を全画面で白くしていたため「ビューポート全体が真っ白」状態を招いていた
- 旧 `BNAME_OT_view_pan` / `BNAME_OT_view_rotate` / `BNAME_OT_view_zoom_drag` のキーマップ登録 (クラスは互換のため残置、統合モーダルに機能集約)

### 修正
- ページ削除時 (`remove_page_gpencil`) に新設の紙メッシュ / 紙オブジェクトも併せて削除 (残骸防止)
- ナビゲート回転の方向を反転 (マウスの動きと回転方向が逆だったため)
- ズーム drag のブレ抑制 — 累積差分方式から絶対オフセット方式に変更、3px のデッドゾーン追加、`rv3d.update()` で view_matrix を強制再計算してからピボット補正
