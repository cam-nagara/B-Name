# Outliner 中心の実 Object レイヤー管理 移行計画

作成日: 2026-04-30  
対象 Blender: 5.1.1 (`b70da489d7f4`)

## 1. 目的

B-Name の N パネル内 `UIList` で Photoshop / CLIP STUDIO PAINT 風の D&D レイヤー操作を再現する方針を見直し、Blender 標準 Outliner の D&D を利用できる構造へ移行する。

方針は次の通り。

- B-Name の論理レイヤーを、Blender の実 `Object` として表現する。
- ページ、コマ、汎用フォルダを、Blender の `Collection` として表現する。
- Outliner 上の Object/Collection D&D を、B-Name の親子付け変更 UI として使う。
- B-Name パネル上のレイヤーリストは廃止または補助表示へ縮小し、作成、削除、前面/背面移動、詳細編集、整合性修復のボタンを配置する。
- 重なり順は B-Name の `z_index` を正とし、`Object.location.z` と Object 名 prefix を自動同期する。

## 2. 調査結果

### 2.1 Outliner / Collection

Blender 5.1.1 実機で以下を確認した。

- `bpy.ops.outliner.collection_drop`
- `bpy.ops.outliner.parent_drop`
- `bpy.ops.outliner.item_drag_drop`
- `bpy.types.Collection.objects`
- `bpy.types.Collection.children`

Outliner は Object / Collection の所属変更や親子付け D&D を持つ。ただし Python アドオンが Outliner 内へ独自項目を差し込む API ではないため、B-Name 独自 `PropertyGroup` のままでは利用できない。

また、`CollectionObjects` / `CollectionChildren` に `move()` は無い。Outliner 上での表示順を任意レイヤー順として保存先にするのは不安定である。

### 2.2 現在の B-Name データ

現在の主な状態は次の通り。

| 種別 | 現状 | Outliner への露出 |
|------|------|------------------|
| ページ | `BNamePageEntry` + `page_pNNNN` Collection | 一部あり |
| コマ | `BNameComaEntry` | Collection ではない |
| 汎用レイヤーフォルダ | `BNameLayerFolder` | なし |
| GP | 単一 `bname_master_sketch` Object 内の GP レイヤー | 個別レイヤーとしては出ない |
| ラスター | `BNameRasterLayer` + `raster_plane_*` Object | あり |
| 画像 | `BNameImageLayer` + GPU overlay 描画 | なし |
| フキダシ | `BNameBalloonEntry` + GPU overlay 描画 | なし |
| テキスト | `BNameTextEntry` + GPU overlay / typography 描画 | なし |
| 効果線 | GP object/layer ベース | 要整理 |

このままでは Outliner D&D の対象になるレイヤーとならないレイヤーが混在する。

## 3. 基本設計

### 3.1 Outliner 階層

最終的な Outliner 構造案。

```text
B-Name
  0000__outside
    L0010__image__参照画像
    L0020__gp__全体メモ
  P0001__p0001__1ページ
    C0010__c01__コマ1
      L0010__image__ラフ
      L0020__raster__ペン入れ
      F0030__folder__人物
        L0031__gp__髪
        L0032__gp__顔
      L0040__balloon__セリフ
      L0050__text__セリフ本文
    C0020__c02__コマ2
      L0010__gp__下描き
```

| B-Name論理要素 | Blender実体 |
|----------------|-------------|
| 作品ルート | Collection `B-Name` |
| ページ外 | Collection `0000__outside` |
| ページ | Collection |
| コマ | Collection |
| 汎用フォルダ | Collection |
| GPレイヤー | Grease Pencil Object |
| ラスター | Mesh plane Object |
| 画像 | Mesh plane Object |
| フキダシ | Mesh / Curve Object |
| テキスト | 生成画像 plane Object または Mesh Text Object |
| 効果線 | Grease Pencil Object または Mesh Object |

### 3.2 安定 ID と表示名

Object/Collection 名はユーザーが変更できるため、B-Name の安定 ID は custom property に保存する。

```text
object["bname_kind"] = "image" | "raster" | "gp" | "balloon" | "text" | "effect"
object["bname_id"] = "image_0001"
object["bname_parent_key"] = "p0001:c01"
object["bname_folder_id"] = "folder_xxx"
object["bname_z_index"] = 40
object["bname_title"] = "セリフ本文"
object["bname_managed"] = True
```

#### 既存 PropertyGroup の参照ルール

既存実装に沿って、各エントリの真の安定 ID は次のとおり。計画書全体の `bname_id` / `bname_parent_key` はこれを写し取った派生値である。

| エントリ | 真の安定 ID プロパティ | 値の例 | 補足 |
|---------|----------------------|-------|------|
| `BNamePageEntry` | `id` | `p0001`, `p0001-0002` (見開き) | `core/page.py:51`。属性名は `page_id` ではなく `id` |
| `BNameComaEntry` | `id` | `c01` (`cNN`) | `core/coma.py:87`。`id` を一次キーとする |
| `BNameComaEntry.coma_id` | (補助) | `c01` (ファイル stem) | `core/coma.py:96`。export 用ファイル名生成に使う**派生値**であり、安定 ID として使ってはならない |
| `BNameLayerFolder` | `id` | `folder_xxxxxx` | `core/layer_folder.py:16` |
| `BNameRasterLayer` / `BNameImageLayer` | `id` | `raster_0001` 等 | `core/raster_layer.py` / `core/image_layer.py` |
| `BNameBalloonEntry` / `BNameTextEntry` | `id` | `balloon_0001` 等 | `core/balloon.py` / `core/text_entry.py` |

`object["bname_id"]` には上表「真の安定 ID」をそのまま格納する。`object["bname_parent_key"]` は既存 `parent_key` 形式 `"<page_id>:<coma_id>"` をそのまま採用し、新フィールド名を作らない（§7 参照）。

#### Object/Collection 名 prefix 規則

Object/Collection 名は派生値として自動更新する。kind prefix は次の 1 文字で統一する。

| 種別 | prefix 文字 | 例 |
|------|------------|------|
| ページ Collection | `P` | `P0001__p0001__1ページ` |
| 見開きページ Collection | `P` | `P0001-0002__p0001-0002__見開き` (ページ ID にハイフンを含むときは prefix の数値部もハイフン形式) |
| コマ Collection | `C` | `C0010__c01__コマ1` |
| 汎用フォルダ Collection | `F` | `F0030__folder_xxxxxx__人物` |
| レイヤー Object | `L` | `L0040__text__セリフ本文` |
| ページ外 Collection | `P` (固定 `P0000`) | `P0000__outside__ページ外` (outside も `P` 系列で統一し、Outliner alpha ソートで `P0000` が最前に来るようにする) |

prefix の数値 4 桁は z_index を 0 詰め 4 桁で表記する（`L0040` 等）。同一階層に同 z_index が衝突した場合は、`bname_id` の昇順で内部優先度を決め、prefix の生成は重複しないように `_a` `_b` などのサフィックスで衝突回避する。

```text
L0040__text__セリフ本文
F0030__folder__人物
C0010__c01__コマ1
P0001__p0001__1ページ
```

ユーザーが名前を編集した場合は、prefix 以降をタイトルとして解釈し、prefix は B-Name が再生成する。

#### Object 名長と多バイト文字の扱い (Blender 5.1.1 実機検証)

Blender 5.1.1 の ID name 上限は **255 バイト** (UTF-8)。Object/Collection 名はこの上限で内部的に黙って切り詰められる。

- 半角 ASCII のみなら 255 字まで通る。
- 日本語タイトルは 1 字 = 3 バイト換算なので、`P0001__p0001__` (14 ASCII バイト) を消費した残りで **約 80 字** が上限。実機テストでは `"コマ" * 50` (150 字) は 85 字で切り詰めた。
- 安全に扱うため、Object 名生成側で次を実装する。
  1. `bname_title` は **常にフルテキストを custom property に保持** し、Object 名側を真とみなさない。
  2. Object 名は `prefix_len + UTF-8 で `MAX_OBJECT_NAME_BYTES - prefix_len - safety_margin (8B)` 以下に切詰めたタイトル` で生成する。文字境界で安全に切るユーティリティを `utils/object_naming.py` (新設) に置く。
  3. 切詰め発生時は Object に `bname_title_truncated = True` を立て、修復ボタンから検知できるようにする。
- Blender は同名衝突時に `.001` `.002` を自動付加するが、`bname_id` を真の安定 ID とするため、検出 scan は **Object 名ではなく `bname_id` で B-Name エントリと突合**する (`.001` 付き Object も同 `bname_id` であれば同一エントリとして扱う)。Object 名 prefix の再生成は必要に応じて B-Name 側から `obj.name = canonical` で上書きする。

### 3.3 GP レイヤー

GP は Blender の Grease Pencil 内部レイヤーではなく、**1 つの GP Object を B-Name の 1 レイヤーとして扱う**。

推奨構造。

```text
L0031__gp__髪  (Grease Pencil Object)
  内部 GP layer: content
  内部 GP layer: __bname_mask  (必要時のみ、通常は非表示)
```

Outliner 上では Object 単位で D&D できる。ユーザーに GP 内部レイヤーを直接管理させない。

利点:

- Outliner の Object D&D と相性が良い。
- 1 レイヤー = 1 Object で、表示/ロック/選択/削除が直感的。
- GP レイヤー単位の親子付け同期を廃止できる。

注意点:

- GP Object 数が増えるため、描画・モード切替・マテリアル管理の性能検証が必要。
- 既存 `bname_master_sketch` からの移行処理が必要。
- 既存 GP レイヤーのフレーム、マテリアル、親キーを Object 単位へ移す必要がある。

## 4. 重なり順

### 4.1 正とする値

重なり順の正は `bname_z_index` とする。

- 値が大きいほど前面。
- `Object.location.z` は `z_index` から自動計算する。
- Object 名 prefix も `z_index` から自動計算する。
- export / render / hit test は `z_index` でソートする。

### 4.2 Z 座標

ビューを正投影、用紙面を XY、奥行きを Z とする。

```text
z = z_index * BNAME_Z_STEP_M
```

初期案:

```text
BNAME_Z_STEP_M = 0.0001  # 0.1mm
```

透明素材や GP の表示順が Z だけで安定しない場合は、Viewport 用 Z と export 用ソートを分ける。最終成果物は必ず `z_index` ソートを正とする。

### 4.3 Outliner 表示順

Outliner 上で自由な表示順保存は期待しない。代わりに、Object/Collection 名 prefix を自動更新する。

```text
L0010__image__ラフ
L0020__raster__ペン入れ
L0030__balloon__セリフ
L0040__text__本文
```

B-Name パネルに以下を置く。

- 最前面へ
- 前面へ
- 背面へ
- 最背面へ
- z_index を整列
- Outliner 名 prefix を再生成

Outliner のアルファベット順ソートを使うと、prefix によってレイヤー順と表示順を概ね一致させられる。B-Name 用 Outliner を開く operator で `display_mode="VIEW_LAYER"`、`use_sort_alpha=True` などを設定する。

## 5. Outliner D&D 同期

### 5.1 同期対象

Outliner で以下を許可する。

| 操作 | B-Name側の意味 |
|------|----------------|
| Layer Object を Page Collection へ移動 | ページ直下へ reparent |
| Layer Object を Coma Collection へ移動 | コマ内へ reparent |
| Layer Object を Folder Collection へ移動 | 汎用フォルダへ格納 |
| Folder Collection を Page / Coma / Folder へ移動 | フォルダ親変更 |
| Coma Collection を Page Collection へ移動 | **既定では拒否** (下記参照) |
| Object を outside Collection へ移動 | ページ外へ移動 |

#### コマのページ間移動の取り扱い

`BNameComaEntry.rect_x_mm/rect_y_mm/vertices` は **ページローカル座標 (mm)**。コマを別ページへ D&D した場合、移動先の用紙サイズ・grid offset が異なれば座標が破綻するため、**Phase 0-2 では Coma → Page 跨ぎ移動は拒否**し、`§5.3 正規化` で元のページ Collection へ戻す。

将来 Phase 4 以降で許可する場合は、次のいずれかを必須とする。

1. **明示 operator**: B-Name パネルから「コマを別ページへ移動」を選択させ、座標再計算 (`new_rect = old_rect - old_page_offset + new_page_offset`) と vertices 再射影を伴うフローのみ許可する。
2. **D&D 検出時の確認ダイアログ**: Outliner D&D を検出したとき modal popup で「座標を維持／用紙基準で再射影／キャンセル」を選ばせる。

どちらも未実装の段階では、`§5.3 正規化` で「Coma Collection が別 Page Collection に link されたら、元の Page Collection へ unlink/relink で戻し、警告ログを出す」を既定動作とする。

### 5.2 監視方式

Outliner D&D 専用の Python callback は期待しない。以下の組み合わせで検出する。

- `depsgraph_update_post`
- `bpy.msgbus`
- 低頻度 timer scan
- B-Name 管理 Collection/Object の custom property 差分比較

検出後に、Object の所属 Collection から B-Name の `parent_key` / `folder_key` / `z_index` を更新する。

### 5.3 正規化

Outliner 操作後は必ず正規化を行う。

- B-Name 管理 Object (`bname_managed = True`) は B-Name 管理 Collection のどれか 1 つだけに所属させる。
- 複数 Collection に link された場合は、**`bname_managed = True` のもののみ自動正規化対象**とし、最後に検出した B-Name Collection を採用してそれ以外から unlink する。`bname_managed` が無い (ユーザーが手動で Ctrl-D&D で意図的に多重 link した非管理 Object) は触らない。
- ユーザーが B-Name 管理 Object を **意図的に多重 link したい** ときの逃げ道として、Object に `bname_no_normalize = True` を立てる退避フラグを用意し、検出 scan で skip する。
- 無効な場所へ移動された場合は、直前の有効親へ戻す。Coma Collection の Page 跨ぎは §5.1 のルールで戻す。
- B-Name 管理 Object/Collection が削除された場合は、B-Name パネルに修復ボタンを出す。自動復元はユーザー操作との競合があるため、初期段階では警告優先にする。

#### depsgraph_update_post の再帰抑止

`§5.2` で `depsgraph_update_post` を導入する際、コールバック内で `obj.name` 書換 / Collection link/unlink / custom property 更新を行うと再 fire が発生する。次の 3 段で抑止する。

1. **モジュールスコープ guard**: `_BNAME_SYNC_IN_PROGRESS = False` をフラグとし、コールバック先頭で True なら早期 return、自身の処理範囲では set/unset を `try/finally` で囲う。
2. **差分検出キャッシュ**: 前回 scan 時の `(object_name, parent_collection_name, location_z, custom_props_hash)` を `bpy.app.driver_namespace` に保持し、変化が無ければ早期 return する。
3. **B-Name 操作中の suppress**: B-Name の operator (前面/背面、reparent 等) は実行ブロック全体を guard で囲い、operator 内変更による再 fire を完全に抑止する。

`bpy.msgbus` は `RNA path` 単位の subscribe で `depsgraph_update_post` より粒度が細かいので、Object → Collection 関係の変化監視は msgbus を主、`depsgraph_update_post` を保険として併用する。timer scan は **5 秒以上の低頻度** で整合性チェック専用とする (1 秒以下にすると Undo 中に再帰する事例が報告されている)。

## 6. マスク設計

### 6.1 結論

**Collection 自体に「子 Object をこのページ/コマ領域でクリップする」汎用マスク機能は無い。**

そのため、ページやコマを Collection で再現しても、Collection へ入れただけでページ外・コマ外が非表示になるわけではない。

マスクを実現する場合は、B-Name が以下を生成・同期する必要がある。

- ページ/コマごとの mask object
- 各レイヤー Object 側の material / modifier / GP mask 設定
- export 時のクリップ処理

### 6.2 ページマスク

ページマスクは比較的低リスク。

ページは矩形なので、画像/ラスター/テキスト画像 plane は次のどちらかで対応できる。

- plane geometry をページ矩形内にクリップする。
- material shader でページ矩形外の alpha を 0 にする。

GP Object は、Object 内に `content` layer と hidden mask layer を持ち、Grease Pencil の layer mask を使う案を検証する。Blender 5.1.1 実機では `GreasePencilLayer.use_masks` と `mask_layers` が存在する (5.1 では `bpy.data.grease_pencils` に統一、4.3-4.x の `bpy.data.grease_pencils_v3` と両対応する `_gp_data_blocks()` ヘルパが既に `utils/gpencil.py` に存在する)。

mask layer の命名と動的生成範囲を次のように定義する。

- ページマスク用と コママスク用で **別レイヤーに分離する**。
  - `__bname_page_mask`: ページ矩形マスク。GP Object がページ Collection 直下にある場合のみ有効。
  - `__bname_coma_mask`: コマ形状マスク。GP Object がコマ Collection 内にある場合のみ有効。両方が成立するときは `__bname_coma_mask` が `__bname_page_mask` を上書きする (内側が必ずページ内のため)。
- 名前 prefix `__bname_` の layer は B-Name 管理であり、UI には出さない。マスク生成 / 削除 / 形状更新は親 Collection 変化や `BNameComaEntry.vertices` 更新に追従させる。
- ページ・コマの形状が変わる頻度は低いため、再生成は msgbus + 明示的修復ボタンで十分とする (毎フレーム再計算は不要)。

### 6.3 コママスク

コママスクは中から高リスク。

コマは矩形だけでなく、多角形、曲線、フリーフォームを持つため、単純な Z や Collection ではクリップできない。

候補は次の通り。

| 方式 | 対象 | 長所 | リスク |
|------|------|------|--------|
| material alpha mask | image / raster / text image | Viewport と render で同じ見た目にしやすい | 多角形/曲線マスク生成が必要 |
| mesh geometry clip | image / raster plane | 表示が確実 | 画像変形・回転時の再計算が複雑 |
| GP layer mask | GP Object | Blender GP の機能に乗れる | Object ごとの hidden mask layer 生成が必要。挙動検証必須 |
| export 時のみ clip | 全種 | 最終出力の正確性を確保しやすい | Viewport 表示とズレる |
| compositor / stencil 相当 | 全種 | 理論上は柔軟 | リアルタイム編集との相性が悪い |

初期方針:

1. export は既存ロジックを活かし、`z_index` 順にコマ領域でクリップする。
2. Viewport はまずページマスクを安定させる。
3. コママスクは raster/image/text plane から対応する。
4. GP Object のコママスクは別フェーズで、実機挙動を検証してから採用する。

### 6.4 マスクの正とするデータ

マスク形状の正は B-Name のページ/コマデータとする。

- ページ: `paper.canvas_width_mm` / `paper.canvas_height_mm`
- コマ: `BNameComaEntry.shape_type` / `vertices` / `rect_*`

Blender 側の mask object は派生物として再生成可能にする。

## 7. 保存形式

初期移行では、既存 `work.json` / `pages.json` / `page.json` を維持する。

追加する概念。**フィールド名は既存スキーマと整合させる**ため、新規に `outlinerParentKey` のような別名を作らず、既存 `parentKind` / `parentKey` をそのまま流用する。

```json
{
  "objectName": "L0040__text__セリフ本文",
  "zIndex": 40,
  "layerObjectMode": "object",
  "parentKind": "coma",
  "parentKey": "p0001:c01"
}
```

- `parentKey` の値ドメインは既存 [utils/layer_reparent.py:142-147](utils/layer_reparent.py:142) と完全一致 (`"<page_id>:<coma_id>"` / `"<page_id>"` / `""`)。フォルダを許容する場合は `parentKind = "folder"` + `parentKey = "<folder_id>"` を追加する。
- `objectName` は派生表示。次回読込時に `bname_id` から prefix を再生成し、`objectName` フィールドは検証用情報としてのみ使う (.001 自動付加検出用)。
- `layerObjectMode` の取り得る値: `"object"` (Phase 1 以降の Object/Collection mirror モード) / `"legacy"` (Phase 0 互換モード、Object を生成しない)。

最終的には Object custom property と JSON の二重管理になるため、同期責務を明確にする。

初期段階:

- JSON / PropertyGroup を正とする。
- Object/Collection は mirror。
- Outliner D&D 検出時だけ Object 側から JSON / PropertyGroup へ反映する。

安定後:

- 親子付けと z_index は Object/Collection 側を正に寄せる。
- レイヤー固有設定は引き続き JSON / PropertyGroup に保持する。

## 8. 移行フェーズ

### Phase 0: 実機 POC

目的: Outliner を B-Name レイヤー管理 UI として使えるかを最小検証する。

作業:

1. B-Name root / outside / page / coma / folder Collection を生成する試作。
2. 画像、ラスター、GP の 3 種だけを実 Object として生成する試作。
3. Outliner D&D 後の Collection 所属変更を scanner で検出する。
4. `parent_key` / `folder_key` へ反映する。
5. Object 名 prefix と z_index 同期を検証する。
6. B-Name 用 Outliner 表示の取り扱いを確定する。**既存 Outliner エディタの設定を上書きしない方針**を採用し、次のいずれかを選ぶ:
   - **(a)** 専用 workspace `B-Name` を提供する。`B-Name` workspace の Outliner だけ `display_mode="VIEW_LAYER"`, `use_sort_alpha=True` 既定にし、ユーザーの既存 workspace 内 Outliner には触らない。
   - **(b)** N パネルの「Outliner を B-Name 表示に切替」ボタンで明示的に既存 area の設定を変更する。直前設定を `area["bname_outliner_backup"]` に退避し、復元ボタンも提供する。

   POC 段階では (a) を採用し、Phase 6 で (b) を追加する想定。
7. depsgraph 再帰抑止 guard (`§5.3` のフラグ + 差分キャッシュ) を実装し、scan の実測 fire 回数をログ化して負荷を確認する。
8. GP Object 数の負荷試験。100 ページ × 30 GP Object 相当のシード (合計 3000 GP Object) を作成し、Viewport 描画 / mode 切替 / Undo の応答性を測定する。

完了条件:

- 画像/ラスター/GP Object を Outliner でページ/コマ/フォルダへ D&D できる。
- D&D 後に B-Name の親子情報が更新される。
- 前面/背面ボタンで Z と名前 prefix が更新される。
- 保存/読込後に同じ Outliner 構造が復元される。
- 3000 GP Object シードでも Viewport / Undo が実用範囲。
- depsgraph_update_post の再帰が起きない (guard により早期 return される実測ログがある)。

### Phase 1: Outliner mirror 導入

目的: 既存機能を壊さず、Outliner 管理構造を常時生成する。

作業:

1. `utils/outliner_model.py` 新設。
2. `utils/layer_object_sync.py` 新設。
3. load_post / work open / page add / coma add / layer add 後に mirror 同期。
4. 既存 `bname_layer_stack` は残しつつ、D&D は非推奨表示にする。
5. Outliner D&D scan を導入。
6. 無効構造の復元/警告処理を追加。

完了条件:

- 既存 UIList を使わなくても、Outliner で親子付け変更できる。
- 既存テストが通る。
- 既存ファイルを開いても破壊的変換が発生しない。

### Phase 2: GP Object per layer

目的: GP 内部レイヤー管理から、1 GP Object = 1 B-Name レイヤーへ移行する。

作業:

1. 新規 GP レイヤー作成時に新 GP Object を作る。
2. 既存 `bname_master_sketch` 内 GP レイヤーを GP Object 群へ移行する operator を作る。
3. 選択中 GP Object を draw/edit 対象にする。
4. GP Object の z_index / name prefix / parent Collection を同期する。
5. GP Object の material / frame / brush 初期化を整備する。

完了条件:

- Outliner で GP Object をページ/コマ/フォルダへ移動できる。
- GP 描画ツールが、選択中 GP Object へ描画する。
- 既存 GP レイヤーの移行が可逆的に検証できる。

### Phase 3: 画像/ラスター Object 正規化

目的: 画像とラスターを Object 表示へ統一する。

作業:

1. 画像レイヤーを image plane Object 化する。
2. 既存 raster plane を B-Name outliner model に正式登録する。
3. material で opacity / blend / tint / brightness / contrast / binarize を再現する。
4. z_index と Object Z を同期する。
5. ページマスクを画像/ラスター plane に適用する。

完了条件:

- 画像/ラスターが overlay ではなく Object として見える。
- Outliner D&D と Z ボタンで親子/重なり順を管理できる。
- 既存 export と見た目が大きくズレない。

### Phase 4: フキダシ/テキスト Object 化

目的: overlay 依存のフキダシ/テキストを Outliner 管理対象にする。

方針:

- フキダシは Mesh / Curve Object として生成する。
- テキストは Blender Text Object だけでは縦書き、ルビ、縦中横、独自組版の再現が難しいため、当面は B-Name typography から生成した画像 plane Object とする。

作業:

1. フキダシ形状 generator を Object mesh/curve 出力へ対応。
2. テキスト renderer から透過画像を生成し、plane material に貼る。
3. テキスト編集時は画像 plane を更新する。
4. parent_balloon_id によるフキダシ/テキスト連動を Object transform でも維持する。
5. コマ/ページ移動時に Object 座標を維持/変換する。

完了条件:

- フキダシ/テキストが Outliner に出る。
- Outliner D&D でページ/コマ/フォルダ移動できる。
- 編集結果が Viewport と export に反映される。

### Phase 5: マスク統合

目的: ページ/コマ領域外を非表示にする。

作業:

1. ページ mask object を生成。
2. コマ mask object を生成。
3. image/raster/text plane の material clip を実装。
4. GP Object の hidden mask layer 方式を検証、採用可否を決める。
5. export pipeline の z_index + mask クリップを統一。

完了条件:

- ページ外表示が Viewport で抑制される。
- コマ外表示が主要レイヤー種別で抑制される。
- export と Viewport のクリップ結果が一致する。

### Phase 5b: 効果線の Object 化

目的: 現状 GP / Mesh ハイブリッドで管理されている効果線を Outliner レイヤーモデルへ統合する。

現状認識:

- `core/effect_line.py` と `operators/effect_line_op.py` `operators/effect_line_gen.py` は GP Object と Mesh ベースを併用しており、`§2.2` 表でも「要整理」とされていた。
- 効果線は通常コマ単位で作られ、parent_balloon_id 相当の対象指定 (`effect_line_link_op.py`) を持つ。

作業:

1. 効果線データブロックの kind を 1 つに統一する (推奨: GP Object とし、`bname_kind = "effect"` を立てる)。Mesh 効果線は段階的に GP Object 化する operator を提供する。
2. 既存ファイル読込時、GP/Mesh 混在を検出して `bname_kind = "effect"` の正規化済み Object として登録する。
3. Outliner 上で `L00NN__effect__放射線` のように prefix 化し、コマ Collection 直下に配置する。
4. 効果線の参照対象 (フキダシ/コマ) との連動 (`effect_line_link_op.py`) を Object Custom Property で表現する (`bname_effect_target = "<balloon_id>" | "<coma_id>"`)。

完了条件:

- 効果線が Outliner に `effect` kind の Object として現れ、ページ/コマ/フォルダへ D&D できる。
- 既存ファイル読込で効果線が消失しない。
- export PSD / PNG での効果線描画が Phase 5b 前後で同一。

### Phase 6: UIList 廃止

目的: N パネルのレイヤーリストを廃止または補助化する。

作業:

1. B-Name パネルに作成/削除/前面/背面/詳細/修復ボタンを配置。
2. 現在の `BNAME_UL_layer_stack` を非表示または読み取り専用へ変更。
3. 右クリックメニューやショートカットを Object 選択ベースへ移行。
4. ドキュメントとチュートリアルを更新。

完了条件:

- 通常のレイヤー親子管理は Outliner で完結する。
- B-Name パネルはレイヤー作成・詳細編集・順序操作に集中する。

## 9. 影響ファイル

主な影響範囲。

| ファイル/領域 | 影響 |
|---------------|------|
| `core/work.py` | object model 移行用設定、状態保存 |
| `core/page.py` / `core/coma.py` | Collection と安定 ID の同期。`BNamePageEntry.id` (`pNNNN`/`pNNNN-NNNN`) と `BNameComaEntry.id` (`cNN`) を真の安定 ID として固定。`coma_id` (ファイル stem) は派生のまま |
| `core/image_layer.py` | image plane Object との対応。GPU overlay (`ui/overlay_image.py`) → material node tree への移植が伴う |
| `core/raster_layer.py` | raster plane を正規レイヤー Object 化。既存 `RASTER_Z_LIFT_M = 0.0005` 固定値を `z_index * BNAME_Z_STEP_M` 由来に置換するマイグレーション |
| `core/balloon.py` | balloon Object 対応。GPU overlay → Mesh / Curve 移植が伴う |
| `core/text_entry.py` | text image plane 対応。typography 生成画像 plane への移植 |
| `core/effect_line.py` | 効果線の Object kind 統一 (Phase 5b)。GP/Mesh 混在の正規化 |
| `utils/gpencil.py` | GP Object per layer。既存「ページごとに 1 GP」モデルから「レイヤーごとに 1 GP」モデルへ転換 |
| `utils/page_grid.py` | page Collection / Object 座標同期 |
| `utils/layer_stack.py` | 段階的縮小、z_index 変換 |
| `utils/layer_reparent.py` | Object/Collection reparent へ移行。既存 `parent_key_for_target()` の値ドメインを流用 |
| `utils/layer_folder.py` | folder Collection 化。`parent_key` の値ドメインを「ページ:コマ」だけでなく「フォルダ ID」も含むよう拡張 |
| `utils/object_naming.py` (新設) | Object 名 prefix 生成 / UTF-8 安全切詰め / `bname_id` 突合 |
| `utils/outliner_model.py` (新設, Phase 1) | Object/Collection mirror モデル |
| `utils/layer_object_sync.py` (新設, Phase 1) | depsgraph guard 含む同期実装 |
| `operators/layer_stack_op.py` | UIList 操作から Object 操作へ移行 |
| `operators/effect_line_op.py` / `operators/effect_line_gen.py` / `operators/effect_line_link_op.py` | 効果線生成/連動を Object Custom Property 経由に変更 (Phase 5b) |
| `operators/*_op.py` | 新規作成時に実 Object を生成 |
| `ui/overlay*.py` | Object 化後に縮小、移行中は併用 |
| `io/schema.py` | zIndex / objectName / layerObjectMode 追加。`parentKind` / `parentKey` は既存を流用し、`outlinerParentKey` のような別名は作らない |
| `io/export_pipeline.py` | z_index / Object material / mask 統合 |
| `panels/gpencil_panel.py` | レイヤーリスト (`BNAME_UL_layer_stack`) は当該ファイルにあるが、Phase 6 で `panels/layer_stack_panel.py` への分離も同時に検討 |
| `utils/handlers.py` | load/save/Outliner sync。`depsgraph_update_post` 新設 + 再帰抑止 guard |

## 10. テスト方針

### 10.1 自動テスト

- Blender 5.1.1 background import/register。
- 既存作品の load 後に Outliner mirror が生成される。
- Object custom property と JSON / PropertyGroup が一致する。
- Collection link/unlink をスクリプトで模擬し、Outliner D&D と同等の親変更を検証する。
- z_index 変更後に Object Z と name prefix が一致する。
- 保存/再読込後に Object/Collection 構造が復元される。
- 無効な Collection へ移動された Object が復元または警告される。

### 10.2 実機 UI テスト

- Outliner で Object を Page / Coma / Folder Collection へ D&D。
- Outliner で Folder Collection を別 Collection へ D&D。
- B-Name パネルの前面/背面ボタンで Outliner 名 prefix が変わる。
- GP Object 選択から描画ツールへ入り、選択 Object に描画される。
- 画像/ラスター/フキダシ/テキストの可視性、ロック、削除、Undo/Redo。

### 10.3 視覚テスト

- Orthographic view で z_index 順に表示されるか。
- 半透明素材の表示順が破綻しないか。
- ページマスク/コママスクの境界で欠けやはみ出しがないか。
- export PNG/PSD と Viewport の重なり順が一致するか。

## 11. リスク

| リスク | 内容 | 対策 |
|--------|------|------|
| 大規模移行 | UIList ではなくデータモデル変更になる | Phase 0/1 は mirror として導入 |
| Outliner 操作検出 | 専用 callback がない | msgbus 主, depsgraph_update_post 保険, 5 秒以上の低頻度 timer scan で差分検出。再帰抑止 guard 必須 |
| Object が複数 Collection に所属 | 親が曖昧になる | `bname_managed=True` の Object のみ自動正規化。`bname_no_normalize=True` で退避可能 |
| Object 名 UTF-8 切詰め | Blender 5.1.1 の Object/Collection 名は 255 バイトで黙って切詰め。日本語タイトルは prefix 込で 80 字相当が上限 | `bname_title` を custom property に保持し Object 名は派生のみ。生成時に UTF-8 安全切詰め (`utils/object_naming.py`) を通す |
| 同名衝突 (.001 自動付加) | Blender が自動的に `.001` を付ける | 検出 scan は Object 名でなく `bname_id` で突合。prefix は B-Name 側から `obj.name = canonical` で随時上書き |
| コマのページ間 D&D | コマ座標はページローカル mm。別ページ移動で破綻 | Phase 0-2 は拒否し元 Page Collection に戻す。Phase 4 以降は明示 operator + 座標再計算で許可 |
| ユーザー削除/リネーム | 管理 Object が消える/ID prefix が壊れる | custom property を正にし、修復 operator を用意 |
| depsgraph 再帰 | コールバック内変更で再 fire | guard フラグ + 差分キャッシュ + B-Name operator 全体 suppress |
| Z と透明表示 | Viewport の透明ソートが不安定な可能性 | export は z_index ソートを正、Viewport は実機検証 |
| 既存 Z 値との不整合 | `RASTER_Z_LIFT_M = 0.0005` 固定値が `z_index * 0.0001` 由来と衝突 | Phase 3 で読込時マイグレーションを実装 |
| GP Object 増加 | パフォーマンスと描画モード切替 | Phase 0 完了条件に「3000 GP Object 負荷試験」を組込み |
| テキスト Object 化 | Blender Text では B-Name 組版を再現しづらい | typography 生成画像 plane を採用 |
| コママスク | 任意形状クリップが難しい | ページマスクから段階導入、コマは種別ごとに対応 |
| Outliner 設定上書き | ユーザーの既存 Outliner 設定を勝手に変えると混乱 | 専用 workspace `B-Name` を提供 (Phase 0)。既存 area 上書きは Phase 6 で復元機能付きで導入 |
| GP データブロック名のバージョン差 | Blender 5.x は `bpy.data.grease_pencils`, 4.3-4.x は `grease_pencils_v3` | 既存 `_gp_data_blocks()` ヘルパで両対応済 |

## 12. 採用判断

この方針は、N パネルの `UIList` で疑似 D&D を続けるよりも Blender の設計に合っている。

採用する場合の推奨順は次の通り。

1. Phase 0 の POC を実施する。
2. 画像/ラスター/GP だけで Outliner D&D と z_index 同期を確認する。
3. 問題なければ Phase 1 の mirror 導入へ進む。
4. GP Object per layer を先に確定する。
5. フキダシ/テキスト Object 化とコママスクは後段に回す。

特にマスクについては、Collection で自然に解決する問題ではない。ページ/コマを Collection にすることと、領域外を非表示にすることは別問題として扱う必要がある。

## 13. 参考

- Blender 5.1.1 実機確認: `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe`
- Blender API: Collection  
  https://docs.blender.org/api/current/bpy.types.Collection.html
- Blender API: Outliner operators  
  https://docs.blender.org/api/current/bpy.ops.outliner.html
- Blender Manual: Grease Pencil Layers / Masks  
  https://docs.blender.org/manual/en/latest/grease_pencil/properties/layers.html
- Blender Developer Documentation: Outliner  
  https://developer.blender.org/docs/features/interface/outliner/
