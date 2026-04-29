# ビューポート reparent 操作 (Alt 系) 実装計画

作成日: 2026-04-29
対象 Blender: 5.1.1

## 1. 操作仕様 (確定)

| 操作 | 親変更 | 位置変更 | 視覚フィードバック |
|------|-------|--------|-----|
| **Alt + ドラッグ** | ドロップ先のコンテナへ | カーソル位置に追従 | ドロップ先コマ/ページ枠ハイライト + 半透明レイヤープレビュー |
| **Alt + クリック** | クリック地点の **1 段深い** コンテナへ | 据え置き | 候補コンテナを 0.3 秒パルス点滅 |
| **Alt + Shift + クリック** | クリック地点の **1 段浅い** コンテナへ | 据え置き | 同上 |

### 階層の定義 (上 → 下)

```
work
 └ (page 外領域 / orphan layer 置き場)   ← 最上階層
    └ page (p0001, p0002, ...)
       └ coma (c01, c02, ...)
          └ 末端レイヤー (gp, balloon, text, image, raster, effect)
```

### 「ページ外」の意味 (確定)

ユーザー回答により、balloon / text / コマ も「ページ外」(= work レベル直下) に置けるようにする。

- ラスター/GP は既存 `scope=master` を流用
- balloon / text / coma / image については **新たに work 直下のコレクション** を新設
- レイヤーリスト上は最上位に「(ページ外)」グループを表示

### Lateral 付け替え

- 同階層の別コンテナ上で Alt+クリック (例: コマ A 子の balloon を コマ B の上で Alt+クリック) → 親をコマ B に切り替える (lateral 移動を許可)

### 起動条件

- どのツール中でも **Alt + LEFTMOUSE PRESS** で発火 (K ツールに依存しない)
- 既存の Ctrl+Alt+LEFTMOUSE (ブラシサイズ) や Ctrl+Shift+LEFTMOUSE (view_layer_pick) と被らないよう、純粋な Alt+LEFTMOUSE / Alt+Shift+LEFTMOUSE のみ採用

### 重なり優先

- クリック地点に複数コマが重なる場合、`z_order` が手前 (大きい) のコマを採用 (既存 `coma_containing_point` ルール踏襲)

### マルチセレクト

- Ctrl/Shift マルチセレクト中の全レイヤーを一括 reparent。各レイヤーの位置はそれぞれ維持

## 2. データ構造変更

### 新規追加プロパティ

#### `BNameWorkData` (work)

```python
shared_balloons: CollectionProperty(type=BNameBalloonEntry)
shared_texts: CollectionProperty(type=BNameTextEntry)
shared_comas: CollectionProperty(type=BNameComaEntry)
```

(image / raster は既存で `scope` プロパティで master 化可能)

#### 各エントリの `parent_kind`

既存値: `"page"` / `"coma"`
新値追加: `"none"` (= ページ外 / shared)

### 影響を受ける既存ロジック

- `core/page.py`: `BNamePageEntry.balloons` / `.texts` / `.comas` 走査ロジック → `(page or shared)` 両方を見る共通イテレータが必要
- `io/schema.py` / `io/page_io.py` / `io/work_io.py`: 保存/読込スキーマに `shared_*` を追加
- `utils/layer_stack.py`: collect_targets で shared コレクションを最上位グループとして集約
- `panels/gpencil_panel.py`: レイヤーリストに「(ページ外)」グループ表示
- 描画 (overlay, viewport): page 座標系を持たないため、shared レイヤーは **work 共通の世界座標 mm** で描画 (page grid の offset を加算しない)

## 3. 実装フェーズ案

### **フェーズ A** (この PR で完了させる範囲)

ページ内の reparent 操作だけを実装。ページ外昇格は未対応 (Alt+Shift+クリックでコマ→page までは動くが、page→外は無効)。

**実装内容**
1. `utils/layer_reparent.py` 新設: 1 つのレイヤー (or マルチセレクト集合) を指定コンテナへ reparent する純関数
2. `operators/alt_reparent_op.py` 新設: 3 つのオペレーター
   - `BNAME_OT_alt_reparent_drag` (modal): Alt+ドラッグ
   - `BNAME_OT_alt_reparent_into` (oneshot): Alt+クリック → 1 段深い
   - `BNAME_OT_alt_reparent_out` (oneshot): Alt+Shift+クリック → 1 段浅い
3. `ui/reparent_overlay.py` 新設: GPU draw_handler でドロップインジケーター描画
4. `keymap/keymap.py`: Alt+LEFTMOUSE / Alt+Shift+LEFTMOUSE 追加
5. CHANGELOG / コミット / プッシュ

**フェーズ A の Alt+Shift+クリック 仕様 (限定版)**
- レイヤーがコマ子のとき: コマから出して page 直下に
- レイヤーが page 直下のとき: 何もしない (将来 phase B で page 外昇格に拡張)
- コマ自体への適用: コマは既に page 子なので、Alt+Shift+クリック で page 外へ昇格させたい — phase B で対応
- 不可な操作はカーソル下に赤の点滅 + ステータスバー文言

### **フェーズ B** (別 PR、後続)

「ページ外」コレクションの新設と、コマ / balloon / text / image / raster の page 外昇格対応。

**実装内容**
1. `BNameWorkData.shared_balloons` / `shared_texts` / `shared_comas` を追加
2. `parent_kind="none"` を許可
3. shared 系の保存/読込 (io/schema, page_io, work_io)
4. レイヤーリストに「(ページ外)」最上位グループ
5. shared レイヤーの描画 (世界座標系)
6. Alt+Shift+クリックで page 外昇格をサポート

## 4. ドロップインジケーターのデザイン

- **対象コンテナ枠**: 太い水色実線 (3px) + 内側半透明シアン塗り (alpha 0.1)
- **ドラッグ中レイヤープレビュー**: 既存のレイヤーリスト D&D の `_draw_overlay` (panels/gpencil_panel から流用) を viewport 用に再利用
- **クリック型確定演出**: 候補コンテナを 0.3 秒パルス点滅 (alpha 0.4 → 0.0)
- **不可な操作**: 対象を赤で 0.3 秒点滅 + `self.report({"INFO"}, "...")` で理由表示

## 5. 影響範囲チェックリスト (フェーズ A)

- [x] 既存の Ctrl+Alt+LEFTMOUSE (ブラシサイズ) と被らない (純粋な Alt のみ)
- [x] 既存の Ctrl+Shift+LEFTMOUSE (view_layer_pick) と被らない
- [ ] balloon_op / text_op / object_tool_op の modal 中も Alt+ドラッグが効くか (modal の PASS_THROUGH 経路を確認)
- [ ] レイヤーリスト D&D との両立 (どちらも Alt 不要なので競合なし)
- [ ] coma 内の親子付け (現状: gp_layer_parenting.py) と同一 API を流用 or 並列
- [ ] マルチセレクトの一括処理時に collect_targets のキャッシュが正しく無効化されるか

## 6. テスト方針

### 自動テスト (Blender CLI)

- 単体: `utils/layer_reparent` の reparent 関数 (page→coma, coma→page, coma→coma lateral)
- 統合: テストシーンで Alt+ドラッグをシミュレート → 親キーが期待通り変わるか
- 回帰: マルチセレクトでの一括 reparent
- 既存テスト: `test/blender_layer_stack_ui_behavior_check.py` が pass し続けることを確認

### AI 視認テスト

- 実機 Blender でドロップインジケーターが正しく出るか
- 不可な操作で赤点滅が出るか
- マルチセレクト一括 reparent の挙動

## 7. リスクと未解決点

- **Alt+ドラッグの起動条件競合**: balloon/text/object ツール中の modal は LEFTMOUSE PRESS を握っているため、Alt 修飾でも tool の modal が先に取る可能性がある。tool 側で Alt+LEFTMOUSE を passthrough する分岐を追加する必要があるかも。実装中に検証。
- **page 跨ぎ移動**: 別ページのコマ上に Alt+ドラッグした場合、現状の page 内の D&D ロジックは「同一ページ内のみ」を強制している。Alt+ドラッグでは別ページ移動を許可する仕様にしてよいか? → 既定 OK (案 1 採用) 。コマも別ページへ送れる
- **コマの reparent**: コマ自体を別ページに送るとき、 内包するレイヤー (parent_key=このコマ) も一緒に動くか? → CSP/PS の「フォルダごと移動」相当で連れて行くべき (子も一緒に)
