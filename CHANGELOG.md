# CHANGELOG

このファイルは B-Name の主要な変更履歴を記録します。
Blender 5.1.1 を対象としています。

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
