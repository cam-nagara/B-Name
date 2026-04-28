# B-Name オーバービュー編集への再設計 計画書

## 1. 背景と目的

現行実装は「1 ページ = 1 `page.blend`」の per-page .blend 方式。ユーザーはページ切替のたびに .blend を swap しており、全ページを俯瞰した編集ができない。

要望: **「全ページ一覧」を編集ホームにし、その上で** コマ割り / コマ形状変更 / GP ラフ描画 / 縦書きテキスト / フキダシ配置 **ができるようにする**。コマの 3D データはダブルクリックで `cNN.blend` を開いて編集する方式に変更する。

## 2. 確定アーキテクチャ

### ファイル構成 (新)

```
<Work>.bname/
├── work.json                          # 作品メタ
├── pages.json                         # ページ一覧メタ
├── work.blend                         # ★新: マスター .blend (全ページの 2D データ)
└── pNNNN/
    ├── page.json                      # ページメタ (comas リストを含む)
    └── cNN/
        ├── cNN.json                   # コマメタ
        ├── cNN.blend                  # コマ 3D シーン (ダブルクリックで開く)
        ├── cNN_thumb.png              # サムネ
        └── passes/                    # 魚眼レンダ等の副産物
```

**`page.blend` は廃止**。役割を `work.blend` が吸収する。

### Blender セッションの状態モデル

| モード | 開いている mainfile | 主なデータ |
|---|---|---|
| **overview モード** (既定・編集ホーム) | `work.blend` | 全ページの Collection / GP / テキスト / フキダシ / 画像 |
| **コマ編集モード** | `cNN.blend` | そのコマの 3D シーン (アセットリンク等) |

モード切替は **`wm.open_mainfile`** で mainfile 自体を差替える。

### Scene プロパティ

- `Scene.bname_work` (PointerProperty) — work.blend 内で永続。`work.pages[i].comas[j]` に全ページ・全コマのメタを保持
- ページ/コマメタは常に JSON が真で、`load_post` で Scene に再構築 (既存 `_reload_all_pages_panels` を流用)
- `Scene.bname_overview_mode` — overview 表示モード。本計画後は既定 True、ユーザーは単ページ拡大表示へトグル可能

### ページごとの Collection 構造 (Phase 2 で導入)

```
work.blend scene
└── Collection "B-Name"
    ├── Collection "page_0001"    ← transform: (0, 0, 0)
    │   ├── GREASEPENCIL  "page_0001_sketch"
    │   ├── TEXT          "page_0001_text_001"
    │   └── EMPTY         "page_0001_balloon_001" etc.
    ├── Collection "page_0002"    ← transform: (-(cw+gap), 0, 0)  (左方向)
    │   └── ...
    └── ...
```

Collection の transform でページを grid 位置に配置。GP/テキスト/フキダシは子として相対座標で持つ。

### モード遷移

```
[起動]
  └─ work.blend を open → overview モード
       ├─ コマダブルクリック → work.blend save → cNN.blend open → コマ編集モード
       │     └─ Exit → cNN.blend save → work.blend open → overview モード
       └─ ページ追加/削除/移動 → work.blend 内で Collection 操作
```

## 3. Phase 1 — 基盤切替 + コマ操作 overview 対応

**目的**: `page.blend` 廃止、`work.blend` 一本化、コマ 4 操作 (cut / knife / vertex edit / shape 変換) が overview 全ページに対して動く状態にする。

### 範囲内
- `io/blend_io.py`: `save_work_blend` / `open_work_blend` を追加。`save_page_blend` / `open_page_blend` は削除。coma 系は維持
- `operators/work_op.py`:
  - `work_new`: work.blend を `<work>.bname/work.blend` に保存。page.blend の保存は行わない。最初のページ 0001 と基本枠コマは従来通り作成 (Scene に乗る)
  - `work_open`: work.json + pages.json 読込 → work.blend を open
  - `work_save`: 現在 mainfile が work.blend なら save_mainfile、cNN.blend なら save_as_mainfile に委譲
- `operators/page_op.py`:
  - `page_add`: work.blend 内に新規ページ Collection を作成 (中身は空、Phase 2 で GP 生成)、Collection transform を grid 位置に設定。mainfile の swap なし
  - `page_remove`: ページ Collection を削除、page.json/cNN.json を削除。mainfile 差替えロジック廃止
  - `page_select` / `_switch_to_page`: page.blend swap を削除。active_page_index の更新と viewport フィットのみ
- `operators/mode_op.py`:
  - `enter_coma_mode`: save work.blend (save_mainfile) → open cNN.blend。未作成なら現シーンを save_as
  - `exit_coma_mode`: save cNN.blend → open work.blend
- `utils/handlers.py`:
  - `_on_load_post`: `work.blend` ロード時は work_dir/pages.json から全メタを Scene に復元。`cNN.blend` ロード時は mode=COMA + 該当 coma_id を設定
  - `_sync_active_from_blend_path` を `work.blend` / `cNN.blend` の 2 系統に対応 (page.blend 分岐を削除)
- `ui/overlay.py`:
  - Scene.bname_overview_mode の default を True にする
  - コマ編集モード中は overview 描画をスキップし従来通りアクティブコマの 2D 表示のみ
- `operators/view_op.py`:
  - `bname.view_fit_all` / `bname.view_overview_toggle` は維持。`bname.view_fit_page` は単一ページ表示トグル用途に残す
- コマ操作 3 種を **カーソル位置から対象ページ/コマを逆引き** して動作させる:
  - `coma_edit_vertices`, `coma_knife_cut`, `coma_to_polygon/to_rect`
  - 新規ヘルパ `find_coma_at_world_mm(work, wx_mm, wy_mm) -> (page_entry, coma_entry)`: 全ページの grid 位置を考慮してヒット判定
  - 各 operator の invoke で mouse → world_mm → (page, coma) に解決し、`self._work/_page/_entry` にセット。active_page_index も更新
- **コマ一覧 UIList にダブルクリック → `bname.enter_coma_mode`** をハンドラで結線:
  - UIList.draw_item 内で行クリック判定は難しいため、代替として以下どちらかで実装:
    - UIList 行に小さな「コマ編集へ」アイコンボタンを追加 (確実)
    - または N パネル側に「選択中コマを編集」ボタン (既存の「コマ編集へ」を流用)
  - ビューポート上のコマダブルクリックは keymap で `LEFTMOUSE` `DOUBLE_CLICK` を `bname.enter_coma_mode` に結線、invoke 時に world 座標→対象コマ逆引き
- ショートカット keymap に `LEFTMOUSE` `DOUBLE_CLICK` を `bname.enter_coma_mode` 割当 (「B-Name Viewport」keymap、既存 watcher 制御に従う)

### 範囲外 (Phase 2 以降)
- 各ページの GP オブジェクト自動生成と Collection 配置
- overview 上での GP 描画
- 縦書きテキスト / フキダシ / 画像レイヤーの overview 対応
- per-page .blend で作成された旧作品の自動マイグレーション (既存 `作品テスト.bname` は破棄)

### 検索ヘルパ `find_coma_at_world_mm` の仕様

```
入力: work, x_mm, y_mm (world 空間の mm 座標)
出力: (page_index, coma_index) or None

処理:
  1. overview_mode が False なら active_page のみ対象、offset=(0,0)
  2. 各ページについて grid offset (col*(-(cw+gap)), -row*(ch+gap)) を計算
  3. 対象座標 (x_mm - ox, y_mm - oy) が各 coma の rect_* に含まれるかチェック
     (polygon は外接矩形で近似、Phase 2.5 以降で正確な判定)
  4. Z 順最大のヒット (最前面) を返す。なければ None
```

### 受入条件 (acceptance criteria)

- [ ] `作品を新規作成` で `.bname/work.blend` が作成される (page.blend は作成されない)
- [ ] ページ追加 2 回で overview に 3 ページ並ぶ (右→左の既存グリッド配置)
- [ ] overview 上で任意ページのコマに「ナイフツール」「頂点/辺ドラッグ」が効く
- [ ] active_page_index が操作したページに自動で追随する
- [ ] コマ一覧のダブルクリック or 「コマ編集へ」で `cNN.blend` に遷移できる
- [ ] コマ編集モードの「戻る」で `work.blend` に戻り、コマ編集内容が保存されている
- [ ] Blender 再起動後、同じ状態 (ページ構成・コマ割) が復元される
- [ ] 徹底チェック済み (直前の変更箇所を行単位で監査、致命バグなし)

## 4. Phase 2 — GP ラフ描画 overview 対応

**目的**: 各ページに GP オブジェクトを持たせ、overview 上で直接 Blender 標準のドロー/消しゴム/ブラシが効くようにする。

### 主な変更
- `page_add` で `page_NNNN_sketch` GP v3 オブジェクト + `page_NNNN` Collection を生成、Collection transform を grid 位置にセット
- `page_remove` で GP / Collection も削除
- `page_move` / ページ順変更時に Collection transform を再計算
- **マウスホバー中ページの GP を active に自動切替**: `bpy.app.timers` で 0.1〜0.2 秒毎にカーソル位置→対象ページを判定し `view_layer.objects.active` を切替
- `BNAME_OT_gpencil_setup` の位置づけを再検討 (ページ単位に変える / 廃止)
- GP パネル (`panels/gpencil_panel.py`) を複数ページ対応にリファイン
- `load_post` ハンドラで GP オブジェクト ↔ ページ対応の整合確認

### 新 operator
- `bname.gpencil_page_ensure`: アクティブページに GP がなければ生成
- `bname.gpencil_follow_cursor`: 自動切替 ON/OFF トグル (preferences に連動可)

### 受入条件
- [ ] 3 ページ overview 上でマウス位置に応じて active GP が切替わる
- [ ] ドローモードで各ページに別々のストロークが描ける
- [ ] work.blend 保存 → 再起動で全 GP ストロークが保持される
- [ ] Phase 1 の コマ操作 / コマ編集遷移は壊れていない

## 5. Phase 3 — 縦書きテキスト + フキダシ overview 対応

**目的**: テキストオブジェクト / フキダシをページ単位で生成・編集できる。

### 主な変更
- `operators/balloon_op.py` / `text_entry` 系を複数ページ対応に
- ページ Collection に配置、マウス位置でページ判定
- N パネルのテキスト入力 UI からドロップダウンでページ選択 or カーソル自動判定
- フォント・縦書き設定 (既存の `typography/` 配下を流用)

### 受入条件
- [ ] 任意ページにテキストブロックを追加できる (位置=マウスカーソル)
- [ ] 任意ページにフキダシを追加できる
- [ ] フキダシとテキストは親子関係で連動移動する

## 6. リスクと対処

| リスク | 影響 | 対処 |
|---|---|---|
| work.blend が 100 ページで重くなる | 起動/保存時間 | 圧縮保存 (既定 True) + 画像は link 参照のみ + GP は sparse 保存。100 ページで 100MB 前後を目安 |
| マウスホバー active GP 切替のラグ | 描画体験の違和感 | タイマー 0.15 秒、切替閾値にデッドゾーン (パネル境界近くで頻繁切替しない) |
| コマ 4 操作のヒット判定が重複する | 意図しないコマが編集される | Z 順最大ヒットのみ採用、複数マッチ時は最前面 1 つ |
| ダブルクリック keymap が他アドオンと衝突 | 誤作動 | 既存の `watcher` による B-Name タブ active 時のみ有効化に乗せる |
| 旧作品の破棄リスク | データ喪失 | ユーザー承諾済 (作品テスト.bname 破棄 OK の明示あり) |

## 7. テスト方針

- 各 Phase 完了時点でグローバル CLAUDE.md の「徹底チェック」を実施 (直前の変更箇所を行単位で監査)
- Blender 起動 → 新規作品 → ページ追加 → コマ割り → 保存 → 再起動 → 復元 の E2E を手動実行
- Phase 2 以降は GP ストロークの描画/保存/復元まで含める

## 8. 参考

- 既存の関連実装:
  - `io/blend_io.py`: open/save_as_mainfile ラッパ (save_work_blend を追加すればよい)
  - `utils/handlers.py`: `_reload_all_pages_panels` を再利用
  - `ui/overlay.py`: overview 描画 (`_draw_page_overlay` は Phase 1 でそのまま使える)
  - `operators/view_op.py`: overview モードのフィット計算 (bbox 計算は左方向展開に対応済)

---

**以上**
