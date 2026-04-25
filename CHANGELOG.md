# CHANGELOG

このファイルは B-Name の主要な変更履歴を記録します。
Blender 5.1.1 を対象としています。

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
