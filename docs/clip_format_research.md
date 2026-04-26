# CLIP STUDIO PAINT (.clip) 出力 調査計画書

調査日: 2026-04-26
セッション種別: 調査セッション（コード変更なし）
サンプル: `D:\TM Dropbox\Miura Tadahiro\『ゆらぎ荘の幽奈さん』_2026読切 - コピー (2)\page0017.clip`（34.0 MB / 600dpi / B5判 230×340mm / 16ページ目）

---

## 1. ゴール

B-Name から CLIP STUDIO PAINT が読み込める `.clip` ファイルを書き出す。最低目標は次の2点:

- **B-Name のコマを CSP の「コマフォルダ」として出力**（コマ枠線つき・マスクつきの折りたたみ可能フォルダ）
- **B-Name の Grease Pencil（GP）を CSP の「ベクターレイヤー」として出力**

副次目標として、ページ寸法・トンボ・綴じ方向・タイトル/著者などの製本メタを CSP に渡す。

---

## 2. CLIP ファイル全体構造

`.clip` はユーザーの想定どおり SQLite を内包しているが、**ファイル全体が SQLite なのではなく、独自のチャンクラッパー（CSFCHUNK）の中の1チャンクとして SQLite が入っている**。先頭マジックは `SQLite format 3` ではなく `CSFCHUNK`。

### 2.1 ファイル全体レイアウト（実測）

```
+-------------------- CSFCHUNK ファイルヘッダ (24B) --------------------+
| 0x00 (8B)  ASCII "CSFCHUNK"                                         |
| 0x08 (8B)  ファイル全体サイズ (BE u64)                                |
| 0x10 (8B)  予約? = 24 (BE u64) ← 最初のチャンクへのオフセット相当     |
+----------------------------------------------------------------------+
| 0x18  CHNKHead         …… 40B固定。ファイルメタ                      |
| 0x50  CHNKExta × 220   …… 大BLOB（3Dデータ/ラスタタイル/ベクター/サム |
|                              ネ等）。各々 extrnlid+UUID で参照される  |
| 0x...  CHNKSQLi        …… SQLite3 データベース本体（2.4MB）          |
| 0x...  CHNKFoot        …… サイズ0の終端                               |
+----------------------------------------------------------------------+
```

サンプル実測:
- 全220個の `CHNKExta`、合計 ~31MB（ファイルの 91%）
- `CHNKSQLi` は 2,457,600 B ちょうど（SQLite ページサイズ × N で揃っている可能性）
- 整数はすべて **ビッグエンディアン**

### 2.2 各チャンクの共通レイアウト

```
+--- チャンクヘッダ (16B) ---+
| name (8B ASCII)            |   "CHNK" + 4文字 (Head/Exta/SQLi/Foot)
| chunk_data_size (BE u64)   |   16Bヘッダを除いたデータ部のバイト数
+----------------------------+
| chunk_data (chunk_data_size B) |
+--------------------------------+
```

### 2.3 CHNKExta のサブレイアウト

```
+ 0x00 "CHNKExta" + 0x08 size(BE u64) +
+ 0x10 sub_size(BE u64) = 0x28(=40)   +  ← 直後40Bが ID/参照ヘッダ
+ 0x18 "extrnlid" (8B)                +
+ 0x20 UUID (32B ASCII hex 大文字)    +
+ 0x40 payload (size - 0x30 B)        +  ← 種別ごとにフォーマット異なる
+--------------------------------------+
```

payload の中身は種別依存:
- 先頭が `CLIP_STUDIO_3D_DATA2` … 3D デッサン人形/3D素材
- 先頭が zlib magic (`78 9c` / `78 da`) … ラスタタイルなど zlib圧縮データ
- それ以外 … ベクターデータ等の独自バイナリ（**仕様非公開**）

> 公開情報: [Inochi2D/clip-d SPEC.md](https://github.com/Inochi2D/clip-d/blob/main/SPEC.md) によれば「CHNKExta は zlib 圧縮」と要約されているが、実機では **3Dや独自バイナリも混在**しており不正確。

---

## 3. SQLite 側のスキーマ（実測37テーブル）

主要テーブルのみ抜粋。

| テーブル | 行数(実測) | 役割 |
|---|---:|---|
| `Project` | 1 | プロジェクトのルート |
| `Canvas` | 1 | キャンバス寸法・解像度・綴じ方向・トンボ・ページ番号・ノンブル設定 |
| `Layer` | 122 | **レイヤー実体（ツリーは連結リストで構成）** |
| `LayerObject` | 36 | レイヤーに付く 3D/カメラ等のサブオブジェクト |
| `VectorObjectList` | 17 | **ベクターレイヤーの線データ（VectorData は外部参照）** |
| `Mipmap` / `MipmapInfo` / `Offscreen` | 144 / 1000 / 1156 | **ラスタ画像実体（タイル化＋Mipmap階層）** |
| `LayerThumbnail` | 136 | レイヤーサムネ |
| `ExternalChunk` | 220 | **`extrnlid`+UUID → CHNKExta オフセット の参照表** |
| `CanvasItem` / `CanvasItemBank` | 1 / 1 | 3Dモデル等のアセット参照 |
| `Manager3DOd` / `DessinDollInfo` / etc | … | 3Dデッサン人形 |
| `RulerPerspective` / `RulerVanishPoint` / `SpecialRulerManager` | … | 定規 |

### 3.1 BLOB の「外部参照」メカニズム（重要発見）

`VectorObjectList.VectorData`、`Offscreen.BlockData`、各種 Mipmap データなど、サイズが大きいフィールドは **SQLite に直接 BLOB を入れず、40Bの参照だけ入れる**:

```
extrnlid + 32文字hex UUID
例: b'extrnlid908304F8A4274AC7B970C065AB0841DD'  (40B)
```

これを `ExternalChunk.ExternalID` で逆引きすると、対応する `CHNKExta` チャンクのファイル先頭からの絶対オフセット (`Offset`) が得られる。**実測で 220 件すべての参照リンクが解決した**。

```
Layer.LayerRenderMipmap → Mipmap.row → MipmapInfo.row × N
   → Offscreen.row → BlockData(40B extrnlid)
       → ExternalChunk.Offset → CHNKExta@offset
           → payload (zlibラスタタイル)
```

---

## 4. 「コマフォルダ」の正体（実機ネームから判明）

実サンプル（少年ジャンプ系のネーム原稿）のコマ枠フォルダ階層を再構成すると:

```
ルートフォルダ (LayerType=256, LayerFolder=1)
├ "用紙" (type=1584)
├ "コマ枠" (folder=1)               ← 普通のフォルダ
│  ├ "基本枠スナップ用" (folder=17, ComicFrameLineMipmap≠0)   ← 基本枠の見えない参照
│  ├ "枠なしコマ"        (folder=1,  ComicFrameLineMipmap≠0)
│  └ "枠ありコマ"        (folder=1)
│     ├ "c04" (folder=1, ComicFrameLineMipmap≠0)             ← ★コマフォルダ
│     │  ├ "白背景"                                            ← 中身ラスタ
│     │  ├ "3Dデッサン人形_Ver5.30"                             ← 3D
│     │  └ "レイヤー 1"
│     ├ "c03" (folder=1, ComicFrameLineMipmap≠0)
│     ├ "c02" (folder=1, ComicFrameLineMipmap≠0)
│     └ "c01" (folder=1, ComicFrameLineMipmap≠0)
├ "ネーム" (folder=1)
│  ├ "主線" (VectorNormalStrokeIndex≠0) ← ★ベクターレイヤー
│  ├ "効果" (VectorNormalStrokeIndex≠0) ← ★ベクターレイヤー
│  └ ...
└ ...
```

### 4.1 LayerType / LayerFolder の値マッピング（実測サンプルからの推定）

| LayerType | LayerFolder | 意味 |
|---:|---:|---|
| 0 | 0 | 通常レイヤー（ラスタ or ベクター） |
| 0 | 1 | 通常フォルダ |
| 0 | 17 | **特殊フォルダ**（キャラ/陰影/メイン等の構造化フォルダ。実測で n=15） |
| 1 | 0 | ラスタ（線画/ベタ系） |
| 2 | 0 | トーンレイヤー |
| 256 | 1 | ルートフォルダ |
| 800 | 0 | コミックストーリー情報（`ComicStoryInfoType=1` と一致） |
| 1584 | 0 | 用紙レイヤー |

### 4.2 コマフォルダの判定式

> `LayerFolder = 1` AND `ComicFrameLineMipmap` ≠ 0 → **CSPの「コマフォルダ」**

`ComicFrameLineMipmap` が指す `Mipmap` 行 → `Offscreen` → `extrnlid` → `CHNKExta` に **コマ枠線のラスタ画像（タイル化、zlib圧縮）** が入っている。**コマ枠そのものはベクター枠ではなくラスタ枠**として保存されているのが CSP の実装。

### 4.3 階層リンク

`Layer` テーブルは「親へのポインタ」を持たず、**連結リスト + 子参照**でツリーを表現:

- `LayerFirstChildIndex` … 子の先頭の MainId（フォルダのみ意味あり）
- `LayerNextIndex` … 同階層の次レイヤーの MainId
- ルートは `Canvas.CanvasRootFolder` が指す Layer.MainId

---

## 5. ベクターレイヤーの位置づけ

- **判定**: `LayerType=0` AND `VectorNormalStrokeIndex` ≠ 0
- **データ実体**: `VectorObjectList.VectorData` (40B `extrnlid`参照) → `CHNKExta` 内の独自バイナリ
- **形式**: 制御点列 + 筆圧 + ブラシ参照 + 中心線 + 太さプロファイル等。**公式仕様は非公開**で、公開リバース成果でも未解読。

---

## 6. 実現可能性の段階評価

| フェーズ | 内容 | 難度 | 工数感 | 判定 |
|---|---|---|---|---|
| **P0** | CSFCHUNK ラッパー生成（ヘッダ/Foot/サイズ更新） | 低 | 0.5d | ✅ 確実 |
| **P1** | 最小 SQLite を作って空キャンバスを CSP で開かせる | 中 | 2〜3d | ✅ 着実に可能 |
| **P2** | レイヤーツリー（フォルダ＋空ラスタ）の生成 | 中 | 2〜3d | ✅ 可能 |
| **P3** | コマフォルダ生成（`LayerFolder=1`+`ComicFrameLineMipmap`） | 中〜高 | 3〜5d | ⚠️ Offscreen タイル形式の解読が必要 |
| **P4** | コマ枠線をラスタ化して `Mipmap`/`Offscreen` に格納 | **高** | 5〜10d | ⚠️ タイル分割＋zlib＋Mipmap階層生成 |
| **P5** | GP→ラスタとして「コマ内ラスタレイヤー」へ書き出し | 高 | 既存解析の延長で可能 | ⚠️ P4と同基盤 |
| **P6** | **GP→CSPベクターレイヤー** 直接書き出し | **非常に高** | 不明（数週〜数ヶ月） | ❌ 仕様非公開で破損リスク高 |

### 6.1 推奨実装ロードマップ

1. **P0+P1**：空 .clip 生成スパイクを `tools/clip_probe/` 配下に置き、CSP で開いて落ちないことを確認。これだけで「CLIP出力の足場」が立ち上がる。
2. **P2+P3**：B-Name のコマを **コマフォルダの「枠＋空ラスタ中身」**として並べる。コマ枠線は最初は「黒1px線をラスタライズしただけのダミー」でもCSPで開く（Offscreenタイル形式が成功した時点でゴール）。
3. **P5**：GPストロークをBlender側でラスタライズ（既存PSDパスと同じ仕組み）してコマ内ラスタとして埋める。**この段階で実用最低限が達成できる**。
4. **P6**：**当面は実装しない**。CSP上で「ラスタからベクター変換」をユーザー操作で行うか、フェーズ最後の挑戦として残す。

### 6.2 P6 を諦めるべき理由

- `VectorObjectList.VectorData` の独自バイナリは公開リバース成果が **読み取り側ですら未完**（`clip-d` も "Don't know yet" と明記）。
- 誤った BLOB を入れると CSP がクラッシュ → ユーザーの作業ファイルを破損させる致命リスク。
- そもそも GP のベジェ曲線と CSP の「ベクター中心線＋太さプロファイル」のデータモデルが一対一対応しないため、無損失変換は理論上も困難。

---

## 7. 既知のリスクと回避策

| リスク | 内容 | 回避策 |
|---|---|---|
| Offscreen タイル形式の壁 | ラスタは32×32 or 256×256のタイルにzlib圧縮で並ぶ。CSP独自のタイル並び順 | 既存 `.clip` を1ピクセルずつ書き換えてバイナリ差分を観察するスパイクで解読 |
| Mipmap 階層 | レンダリング高速化のため複数解像度を持つ。最低でも基本解像度1階層は必要 | サンプルでは MipmapCount=1 のシンプルな例も多い。最小構成から |
| 文字エンコード | TEXT カラムは UTF-8 ではなく **Shift_JIS** で格納されている例あり（実機サンプルで確認済み） | レイヤー名は SJIS でエンコードして格納 |
| バージョン依存 | `ProjectInternalVersion` で CSP バージョンを記録。新しすぎると古いCSPで開けない | サンプルと同じバージョン文字列を流用 |
| 3D/特殊データ | サンプルにはデッサン人形(`CLIP_STUDIO_3D_DATA2`)が含まれる | B-Name 出力では 3D 関連テーブルは空のまま参照しない |

---

## 8. 代替案：PSD のコマフォルダ階層化

**最短の現実解**として、既存の PSD 出力を「コマ＝レイヤーフォルダ、GP＝ラスタライズ済みラスター」の階層で書き出す方法がある。

- メリット: 仕様が公開済み、既存 PSD 出力資産を活用、CSPはPSDを完全に読めるしフォルダ階層も保つ
- デメリット: ベクター性は失われ、CSPの「コマフォルダ（コマ枠つき）」ではなく「ただのレイヤーフォルダ」になる
- 工数: 1〜3 日（既存 PSD 出力の階層改修のみ）

**.clip 直書き P3 着手前に PSD 階層化を先に実装するのが投資対効果が高い**。

---

## 9. 次アクション選択肢（ユーザー判断待ち）

| 案 | 内容 | 推奨度 |
|---|---|---|
| **A** | PSD のコマフォルダ階層化を先に実装（B-Name のコマ→PSDレイヤーフォルダ） | ★★★ 最短で実用効果大 |
| **B** | `tools/clip_probe/` でP0+P1スパイク（空 .clip 生成→CSPで開く検証）を作る | ★★ 学習価値高 |
| **C** | A→Bの順で両方やる（PSD実用化→.clip直書き挑戦） | ★★★ 推奨ルート |
| **D** | いきなりP3コマフォルダ生成に着手 | ★ Offscreen 形式の追加調査が先に必要 |
| **E** | P6（GP→CSPベクター）に挑戦 | ❌ 破損リスク高、現時点では非推奨 |

---

## 10. 参考情報

- [Inochi2D/clip-d SPEC.md](https://github.com/Inochi2D/clip-d/blob/main/SPEC.md) — 公開リバース仕様（要約のみで詳細は未完）
- 実機サンプル解析スクリプト: `d:/tmp/clip_probe/` 配下にダンプ（調査セッション中の作業領域。コミット対象外）
- 抽出した SQLite: `d:/tmp/clip_probe/page0017.sqlite`（2.4MB）
