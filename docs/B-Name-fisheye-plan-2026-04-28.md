# B-Name 魚眼レンダリング機能 実装計画

作成日: 2026-04-28
対象範囲: コマファイル（`<Work>.bname/p<NNNN>/c<NN>/c<NN>.blend`）のみ
方針: A案（eeVR を参考に独自実装、コードは流用しない）

**前提**: [B-Name-restructure-plan-2026-04-28.md](B-Name-restructure-plan-2026-04-28.md) の構造改修完了後に着手。本計画書は新構造（`p0001/c01/passes/`）を前提に記述。

## 1. 背景と目的

B-Name は「ネーム作画用」と銘打っているが、実際の運用では:
- キャラ作画は CLIP STUDIO PAINT で行う
- **背景・小物・効果線・フキダシ・配置済み 3D は B-Name 側で本番原稿として Cycles 魚眼レンダリングする**

このため魚眼レンズの「正確さ」が必須で、ネーム用簡易表示では足りない。

現状 B-Name には魚眼用 PropertyGroup・apply 関数の下地があるが、Cycles `FISHEYE_EQUISOLID` を使うのみ。
ユーザーの既存ワークフローには以下が含まれており、これらを B-Name 側に独自実装する:

- EEVEE 高速プレビュー用魚眼（cube map + GLSL 再投影）
- 180° 出力切替
- 縮小モードでの Pencil+4 線幅自動調整
- 下絵スケール連動レンダーボーダー（コマ枠.png に基づく出力範囲）
- アングルプリセット（保存済み・流用可）

参考実装の eeVR は GPL v3。**本実装ではコードを取り込まず、公知の数式（r = 2f·sin(θ/2) ほか）と公知の手法（cube map 6 面 + 再投影）を独自に再実装する**。これによりライセンス汚染を回避し、B-Name 既存の SPDX:GPL-2.0-or-later を維持する。

## 2. 投影方式の確定（重要）

### 採用する投影は 2 種：Equidistant と Equisolid（Cycles 自動連動）

ユーザーは作品／コマごとに Cycles の `panorama_type` を **`FISHEYE_EQUIDISTANT`（魚眼・等距離射影）** と **`FISHEYE_EQUISOLID`（魚眼・等立体角射影）** で使い分ける運用。B-Name の EEVEE 魚眼プレビューはこれを **手動切替させずカメラ設定に自動追従** させる。

選択ロジック（疑似コード）:

```
panorama = cam.data.panorama_type  # Cycles の現在値
if panorama == 'FISHEYE_EQUIDISTANT':
    use_equidistant_shader()
elif panorama == 'FISHEYE_EQUISOLID':
    use_equisolid_shader()
else:
    # 想定外（POLYNOMIAL / EQUIRECTANGULAR / MIRRORBALL 等）
    fallback_to_equisolid_with_warning()
```

参考: ユーザーの自作アドオン（カメラ操作＆出力プリセット.py）は eeVR に対し `domeMethodEnum = '1'`（Orthographic、`r = f·sin(θ)`）を固定で渡していた。これは Cycles のどちらの魚眼方式とも一致しないため、長年わずかに不一致な出力が出ていたと考えられる。本計画ではこの取り違えを修正する。

### 投影式（両方の数式）

両投影とも cube map 6 面のサンプリングは共通。違いは出力 UV → 3D 単位ベクトルへの逆投影だけ。

```
シェーダ内で（共通部分）:
  d = vTexCoord.xy           // 出力画像座標 [-1, 1]
  r = length(d)
  if (r > 1.0) discard
  dunit = normalize(d)
  phi = FOVFRAC * PI * r     // 視野角を画素半径に対応付け

[Equidistant のとき]
  pt.xy = dunit * sin(phi)
  pt.z  = cos(phi)
  // 等距離射影: r = f·θ → θ = phi、3D 単位ベクトルは (sin θ·dunit, cos θ)

[Equisolid のとき]
  // 入力 r は 2·sin(θ/2) を表す → θ = 2·asin(r/2)、ただし計算簡略化のため
  // phi を「θ/2 ではなく θ」として扱い、Equisolid 逆写像は以下:
  pt.xy = 2.0 * dunit * sin(phi * 0.5) * cos(phi * 0.5)  // = sin(phi) * dunit
  pt.z  = 1.0 - 2.0 * sin(phi * 0.5) * sin(phi * 0.5)    // = cos(phi)
```

実装ノート: 上式の通り **両投影は最終的に同じ `pt.xy = sin(phi)·dunit, pt.z = cos(phi)` に帰着する**。違いは「`phi` を画素半径 `r` からどう求めるか」にある:
- Equidistant: `phi = (FOVFRAC * PI) * r`
- Equisolid: `phi = 2 * asin(min(1, r * sin(FOVFRAC * PI / 2)))`（半径 1 が視野角端に対応するよう正規化）

そのため shader は **`phi` 計算行のみ分岐**、3D 復元以降は共通でよい。実装は分岐 if 1 つ、もしくはユニフォーム `int u_projection_mode` で switch する。

`FOVFRAC` の意味:
- 180° 出力: `FOVFRAC = 0.5`（半天球）
- それ以上: `cam.data.fisheye_fov` を `2π` で割った値

### Cycles 本番レンダリングとの整合

- Cycles 側は `cam.data.type = 'PANO'`, `cam.data.panorama_type ∈ {FISHEYE_EQUIDISTANT, FISHEYE_EQUISOLID}`, `cam.data.fisheye_fov = θ_rad`
- B-Name の EEVEE プレビューは:
  1. `cam.data.panorama_type` を読んで投影モードを決定
  2. `cam.data.fisheye_fov` を `FOVFRAC = θ_rad / (2π)` に換算
  3. cube map レンダ → 該当投影で再投影
- 結果として **EEVEE プレビューと Cycles 本番出力が同一画角・同一歪みで一致する**

### 想定外パノラマタイプの扱い

`cam.data.panorama_type` が `FISHEYE_LENS_POLYNOMIAL` / `EQUIRECTANGULAR` / `MIRRORBALL` 等だった場合:
- B-Name の EEVEE プレビューは対応せず、警告ログ + Equisolid フォールバック表示
- UI 側に「現在のカメラパノラマタイプは EEVEE プレビュー未対応です。Cycles で本番レンダしてください」を出す
- Cycles 本番レンダ自体は B-Name が触らないため正常動作する

## 3. 確定方針

1. **A案（独自実装）採用** — eeVR コードは取り込まない。数式・手法のみ参考にゼロから書く
2. **対象はコマファイル（`c<NN>.blend`）のみ** — `work.blend`（オーバービュー）には影響させない
3. **Pencil+4 連動必須** — 縮小モード時に Pencil+4 ノードグループの `Brush Settings` の `size` を縮小率に合わせて自動調整、解除時に復元
4. **ライセンス**: 既存の `SPDX:GPL-2.0-or-later` を維持
5. **計画書出力先**: 本ファイル（`docs/B-Name-fisheye-plan-2026-04-28.md`）
6. **構造改修依存**: 本計画は [B-Name-restructure-plan-2026-04-28.md](B-Name-restructure-plan-2026-04-28.md) の完了を前提とする。構造改修前に着手しない

## 4. アーキテクチャ

### 4.1 レンダーパス

魚眼モード ON 時の挙動:

```
[本番レンダ]
  Render エンジン = CYCLES
  cam.data.type = PANO
  cam.data.panorama_type = ユーザー設定値を尊重
                           （未設定時のみ FISHEYE_EQUISOLID をセット）
  cam.data.fisheye_fov = θ
  → Cycles が直接魚眼でレンダ。中間処理なし

[プレビュー（EEVEE 高速）]
  Render エンジン = BLENDER_EEVEE_NEXT
  cam.data.type = PERSP（一時切替、6 面 cube map レンダのため）
  cam.data.lens = 計算値
  for face in [front, back, left, right, top, bottom]:
      cam を face 方向に向ける
      bpy.ops.render.render() → 各面を画像に保存
  GPU shader で 6 枚の画像を Equisolid 再投影 → 出力
  cam を元に戻す
```

### 4.2 モジュール構成

新規ファイル:
- `core/fisheye/` (パッケージ)
  - `__init__.py`
  - `projection.py` — Equidistant / Equisolid 両投影の数式（CPU 計算用、テスト用）
  - `cube_capture.py` — 6 面 cube map レンダの制御（カメラ向き計算、render() 呼び出し、画像収集）
  - `reproject_shader.py` — GLSL fragment shader 文字列（両投影を `u_projection_mode` で分岐）と GPU 描画ロジック
  - `pencil4_link.py` — Pencil+4 ノードの線幅保存／縮小／復元
  - `panorama_sync.py` — `cam.data.panorama_type` を読んで shader モードを決定するヘルパ
- `operators/fisheye_op.py` — オペレータ
  - `BNAME_OT_fisheye_render_image` — 1 枚レンダ（cube → 再投影 → 保存）
  - `BNAME_OT_fisheye_render_faces` — 6 面のみレンダ（再投影なし）
  - `BNAME_OT_fisheye_assemble` — 既存の 6 面画像から再投影のみ実行
  - `BNAME_OT_fisheye_save_pencil4_widths` — Pencil+4 線幅をスナップショット
- `panels/fisheye_panel.py` — N パネル UI（既存 panel_camera_panel に統合する案も）

既存ファイル拡張（**ファイル名は構造改修後の新名**を記載）:
- `core/coma_camera.py`（旧 `core/panel_camera.py`） — 既存の `BNameComaCameraSettings`（旧 `BNamePanelCameraSettings`）に魚眼レンダ用プロパティ追加
  - `fisheye_fov_mode: EnumProperty` — `'180'` / `'360'` （初期 `'180'`、Blender カメラの `fisheye_fov` は最大 2π まで対応するため 180° 以上のレンダも可能）
  - `fisheye_render_engine: EnumProperty` — `'CYCLES'` / `'EEVEE'`（初期 `'CYCLES'`、`EEVEE` は cube+再投影プレビュー用）
  - `fisheye_output_dir: StringProperty` — 出力先（既定 `//passes/`、コマファイルは `c01.blend` なので `//` は `c01/` を指し、最終的に `<Work>.bname/p<NNNN>/c<NN>/passes/` になる）
  - `fisheye_cube_keep: BoolProperty` — cube map 6 面中間画像を残すか（初期 True、`passes/cube/` に保存）
  - **投影方式は持たない**: `cam.data.panorama_type` を真とするため B-Name 側にプロパティを置かない（二重管理防止）
- `utils/coma_camera.py`（旧 `utils/panel_camera.py`） — `apply_fisheye_mode` を拡張:
  - 魚眼モード ON 時に `cam.data.panorama_type` が `FISHEYE_EQUIDISTANT` / `FISHEYE_EQUISOLID` 以外なら、既定で `FISHEYE_EQUISOLID` をセット（ユーザーが触っていない初期状態の救済）
  - ユーザーが手動で Equidistant に変更した場合は尊重（上書きしない）

### 4.3 cube map のカメラ向き

各面のカメラ Matrix（Z 軸が前向き、Y 軸が上向きの右手系を仮定）:

```
front:  identity
back:   180° rotation around Y
right:   90° rotation around Y
left:   -90° rotation around Y
top:    -90° rotation around X
bottom:  90° rotation around X
```

カメラの元の transform を保存し、6 面分レンダ後に必ず復元する。

`fisheye_fov` の値で必要な面数が変わる:
- 180° 以下: front + 上下左右 = **5 面で十分**（背面 back は不要）
- 180° 超〜360°: **全 6 面**必要

実装方針: まず 6 面固定で実装し、性能不足が判明した場合のみ 180° 限定の 5 面化を後追いで導入する。

中間画像の保存先: `<Work>.bname/p<NNNN>/c<NN>/passes/cube/` に
`cube_front.png` / `cube_back.png` / `cube_left.png` / `cube_right.png` / `cube_top.png` / `cube_bottom.png`
の名前で保存。`fisheye_cube_keep = False` のときは temp dir に出してレンダ完了後に削除。

### 4.4 魚眼再投影シェーダ（Equidistant / Equisolid 両対応、B-Name オリジナル実装）

eeVR の `commdef`/`dome`/`fetch_*` は同梱しない。以下のように **B-Name 専用に書き直す**。

ベースとなる数式は公知だが、**変数名・構造・コメント・ヘルパ関数の切り方を別物にする**こと。コードレビュー時にも「eeVR とは別実装」と判別可能であることを目視確認する。

ファイル: `core/fisheye/reproject_shader.py`

シェーダ概要:
- vertex: フルスクリーンクワッド（NDC = clip space 直接出力）
- fragment:
  1. NDC → 出力ピクセルの方向 `d`
  2. `phi = FOVFRAC * PI * length(d)`、超過は discard
  3. Equisolid 逆投影で 3D 単位ベクトル `pt` を計算
  4. `pt` がどの cube 面に属するか判定（最大成分）
  5. 該当面のテクスチャから sampling → 出力色

GPU module は Blender 標準の `gpu` モジュール（`gpu.shader.from_builtin` ではなく `GPUShader` カスタム）を使う。

### 4.5 Pencil+4 連動

`core/fisheye/pencil4_link.py` の責務:
- ノードグループ名が `"Pencil+ 4 Line Node Tree"` で始まるものを全列挙
- 各ノードグループ内の `"Brush Settings"` で始まるノードに対し:
  - `save_widths()`: `node["original_size"] = node.size` を全件に設定
  - `apply_scale(scale)`: `node.size = node["original_size"] * scale`（`original_size` 未保存の場合はスキップ）
  - `restore()`: `node.size = node["original_size"]`

呼び出しタイミング:
- 縮小モード ON への遷移時 → `apply_scale(percentage / 100.0)`
- 縮小モード OFF への遷移時 → `restore()`
- 縮小率変更時 → `apply_scale(new_percentage / 100.0)`
- ユーザー手動「Pencil+4 線幅を保存」ボタン → `save_widths()`

### Pencil+4 が無い環境

Pencil+4 ノードグループが 1 つも見つからない場合:
- **黙ってスキップ**（警告ダイアログを出さない）
- ログには `INFO` レベルで「Pencil+4 ノードグループ未検出。線幅連動をスキップ」を出す
- 縮小モード UI 自体は通常通り表示し、解像度縮小機能のみ動作させる
- このため `pencil4_link.py` の各関数は冒頭で **早期 return** する設計とする

既存 `utils/coma_camera.py`（旧 `utils/panel_camera.py`）の `_adjust_pencil4_line_width` / `_restore_pencil4_line_widths` は Pencil+4 連動の雛形が既にある可能性が高いため、**先にこれを精査して既存実装の状態確認 → 不足分のみ追加** とする。

### 4.6 レンダーボーダー（コマ枠.png 連動）

既存実装あり（構造改修後 `utils/coma_camera.py` 内 `update_render_border_*`）。本計画では追加せず、魚眼モード ON 時にも同じ計算が動くことを確認するのみ。

### 4.7 コレクション操作（魚眼モード ON/OFF 連動）

ユーザーのコマファイル（例: `コマファイルver02.blend`）には、以下の前提構成があることが多い:
- `コマ枠` コレクション — コマ枠表示用 EMPTY/MESH 等
- ビューレイヤー: `キャラ` / `背景` / `効果` 等の出し分け
- ノードツリー: `出力_キャラ` / `出力_背景` 等のグループ

魚眼モード切替時の挙動（自作 .py を踏襲）:
- 魚眼モード **ON** に遷移時:
  - 全ビューレイヤーから `コマ枠` コレクションを `exclude = True`（魚眼レンダに枠を入れたくないため）
  - 3D ビューポートのシェーディング種別を `RENDERED` に切替
- 魚眼モード **OFF** に遷移時:
  - `コマ枠` コレクションの `exclude = False` を全ビューレイヤーで復元
  - 3D ビューポートのシェーディング種別を元に戻す（記憶した値）

実装ファイル: `core/fisheye/coma_collection_sync.py`（新規）

注意点:
- ユーザー側の `.blend` は古い Blender 形式で作成されているケースあり（互換性問題の可能性）
- コレクション名 `コマ枠` は **B-Name 既存規約**として存在することを前提にする
- 該当コレクションが見つからない場合は黙ってスキップ（警告のみ）
- ビューレイヤー単位の exclude 操作は既存 `utils/coma_camera.py`（旧 `utils/panel_camera.py`）に類似実装がある可能性。Phase F1 で要精査

## 5. UI 構成

N パネル「カメラ操作」配下に魚眼セクションを追加:

```
[v] 魚眼モード
    視野角 (°):    [スライダー   180.0]
    投影方式:      [Cycles と連動: 等立体角射影]   ← 表示のみ、編集は Cycles 側で
    出力方式:      ( ) Cycles 本番  (•) EEVEE プレビュー
    FOV モード:    (•) 180°  ( ) 360°
    出力先:        [//passes/         ]
    [v] cube 中間画像を残す
    ───────────────────────────────────
    [魚眼レンダリング]
    [6 面のみレンダ（中間ファイル）]
    [既存 6 面から組立]
[v] 縮小モード
    縮小率 (%):    [スライダー   12.5]
    クイック:      [12.5%] [25%] [50%] [100%]
    [Pencil+4 線幅を保存]
```

仕様詳細:

- **視野角 (°)**: B-Name UI ラベルは度（°）固定。内部値はラジアン（Blender カメラ標準）で保持し、UI のみ度表記に変換する。
- **投影方式**: 読取専用のラベル表示で、`cam.data.panorama_type` を読んで:
  - `FISHEYE_EQUIDISTANT` → `Cycles と連動: 等距離射影`
  - `FISHEYE_EQUISOLID` → `Cycles と連動: 等立体角射影`
  - それ以外 → `Cycles と連動: 未対応 (XXX) — Equisolid で代替`（赤字警告）

  クリックでカメラプロパティへジャンプするオペレータがあると親切（任意）。
- **FOV モード**: 180° / 360° の 2 択。Blender カメラの `fisheye_fov` は最大 2π まで設定可能のため、360° モードでも 180° 超〜 360° の範囲でレンダ可能。視野角スライダーの上限が連動して切替わる:
  - 180° モード時: スライダー上限 180°
  - 360° モード時: スライダー上限 360°
- **クイックボタン (12.5/25/50/100%)**: 押すと縮小率プロパティに該当値を設定し、Pencil+4 線幅とレンダ解像度の更新を即時実行。
- **cube 中間画像を残す**: `fisheye_cube_keep` プロパティと連動。OFF にすると temp dir 利用 + レンダ後削除。

## 6. Phase 分割

**前提**: [B-Name-restructure-plan-2026-04-28.md](B-Name-restructure-plan-2026-04-28.md) の Phase R1〜R5 が完了していること。本魚眼 Phase は構造改修後の `coma_camera.py` 等を対象にする。

### Phase F1+F2 — 既存実装棚卸し + Pencil+4 連動確定（一括）
範囲:
- `_adjust_pencil4_line_width` / `_restore_pencil4_line_widths` の現状把握（構造改修後 `utils/coma_camera.py` 内）
- `apply_fisheye_mode` の panorama_type 自動連動部分（Equidistant/Equisolid 両対応で動くか）の確認
- 既存テストがあれば実機（Blender 5.1）で「魚眼モード ON → Cycles 本番レンダ → 期待通り」が出ることを確認
- `core/fisheye/pencil4_link.py` 実装（既存があれば移設・整理）
  - Pencil+4 が無い環境では早期 return する設計
- 縮小モード ON/OFF/縮小率変更との結線
- 「Pencil+4 線幅を保存」UI ボタン
- クイックボタン（12.5% / 25% / 50% / 100%）の実装

### Phase F3 — 180°/360° UI と Cycles 連動
範囲:
- `fisheye_fov_mode` プロパティ追加
- UI ラジオボタン追加
- 180° 選択時に `cam.data.fisheye_fov = π`、360° 選択時に `2π` を設定

### Phase F4 — EEVEE 魚眼プレビュー（独自実装、両投影対応）
範囲:
- `core/fisheye/cube_capture.py` 実装
- `core/fisheye/reproject_shader.py` 実装（GLSL、Equidistant / Equisolid を `u_projection_mode` で切替）
- `core/fisheye/panorama_sync.py` 実装（`cam.data.panorama_type` → shader モード決定）
- `BNAME_OT_fisheye_render_image` 実装（実行時に panorama_type を読んで自動分岐）
- 中間ファイル経路（temp dir → 結果 PNG）
- 投影方式の単体テストを **両投影とも** 通す

### Phase F5 — 6 面分割／組立オペレータ
範囲:
- `BNAME_OT_fisheye_render_faces`（6 面のみ書き出し）
- `BNAME_OT_fisheye_assemble`（既存 6 面から再投影）
- 主用途: 部分再レンダ（特定面のみ高品質再描画）

### Phase F6 — レンダーボーダー連動の検証
範囲:
- 魚眼モードでもコマ枠.png 基準の border 計算が動くか実機確認
- 不整合があれば既存 `update_render_border_*` を魚眼対応に拡張

## 7. ライセンスと再実装ポリシー

### 7.1 触れていいもの
- 数式そのもの（Equisolid: `r = 2f·sin(θ/2)`）— 公知、著作権の対象外
- cube map 6 面再投影という手法 — 公知、特許もない
- B-Name 既存コード

### 7.2 触れてはいけないもの
- eeVR の GLSL 文字列（`commdef`, `dome`, `equi`, `fetch_*`, `domemodes` 配列）
- eeVR の Python オペレータ構造（`Renderer` クラスの実装）
- eeVR のファイル名（`renderer.py` のような汎称はかぶっていいが、内容構造を模倣しない）

### 7.3 確認手順（実装後）
- 新規 GLSL コードが eeVR の `renderer.py` の対応箇所と diff 上「同じ」と判定されないこと（変数名・関数分割・コメントが別物）
- 実装ファイルのモジュール docstring に **以下のような 1 行表記を入れる**（法的義務はないが、第三者監査時の証跡として推奨）:
  ```python
  """B-Name fisheye reprojection (Equidistant / Equisolid).

  独自実装。eeVR (https://github.com/EternalTrail/eeVR, GPL-3.0) を
  手法面で参考にしたが、コードは流用していない。
  """
  ```
- AGENTS.md には記載不要（モジュール docstring で十分）
- `blender_manifest.toml` のライセンス表記は変更不要（GPL-2.0-or-later のまま）

## 8. リスクと対処

| リスク | 影響 | 対処 |
|---|---|---|
| EEVEE cube レンダが遅い | プレビュー実用性 | Phase F4 完了後に実測。1 秒/面 × 6 = 6 秒なら OK、それ以上なら 5 面化／低解像度化検討 |
| GPU shader が Blender バージョン差で動かない | クラッシュ | Blender 5.1 を最低保証、4.3 以降では `gpu` API 互換性を都度確認 |
| Pencil+4 ノードがリンクライブラリ参照で書込不可 | 線幅変更が無視される | `node_group.override_library` をチェック、警告ログを出して続行 |
| Cycles 本番出力と EEVEE プレビューの色が違う | 確認用途として使えない | EEVEE プレビューは「歪みのみ」確認用と割り切り、最終確認は常に Cycles |
| 既存自作 .py との設定衝突 | 二重制御 | B-Name 内では完結。自作 .py は同梱せず、ユーザーが必要なら別途同居 |

## 9. テスト方針

### 9.1 自動テスト（CLI）
- `blender --background --python tests/test_fisheye_projection.py`
- 両投影式の単体テスト:
  - Equidistant: 複数の θ について `r = θ`（正規化済み）を検証
  - Equisolid: 複数の θ について `r = 2·sin(θ/2)` を検証
- Cycles 側 panorama_type を切替えたとき shader モードが追従することを `panorama_sync` 単体で検証

### 9.2 実機テスト（要手動立会い）
- Blender 5.1 起動 → 任意の `c<NN>.blend` を開く → 魚眼モード ON → Cycles レンダ → 出力 PNG が `<Work>.bname/p<NNNN>/c<NN>/passes/` に期待通り出力
- EEVEE プレビュー → 出力 PNG が Cycles と同一の歪みを示すことを目視確認
- **Cycles 側 panorama_type を Equisolid → Equidistant に切替 → EEVEE プレビューがそれに追従**して別歪みになることを目視確認
- Equidistant に切替後 Cycles でもレンダ → EEVEE プレビューと一致確認
- 縮小モード ON → Pencil+4 線幅が縮小率に追随
- 縮小モード OFF → Pencil+4 線幅が元に戻る
- 縮小率を 12.5% → 25% → 50% と変更して各値で線幅追随

### 9.3 全体チェック
各 Phase 完了時にグローバル CLAUDE.md の「徹底チェック」を実施（直前変更箇所の行単位監査）。

## 10. 参考

実装上の参考になる既存ファイル（**構造改修後の新ファイル名で記載**。改修前の旧名は括弧書き）:
- `core/coma_camera.py`（旧 `core/panel_camera.py`） — PropertyGroup の雛形
- `utils/coma_camera.py`（旧 `utils/panel_camera.py`） — `apply_fisheye_mode`、`_apply_fisheye_layout`、Pencil+4 連動の現状実装
- `operators/coma_camera_op.py`（旧 `operators/panel_camera_op.py`） — オペレータの命名規則
- `panels/coma_camera_panel.py`（旧 `panels/panel_camera_panel.py`） — N パネル UI 構成

eeVR から「参考にする」項目（コードはコピーしない）:
- 6 面 cube map をレンダしてから Equisolid に再投影するという発想
- フルスクリーンクワッド + fragment shader で再投影する実装方針
- 360° 時のシーム処理アイデア（必要になれば）

公知ドキュメント:
- Wikipedia "Fisheye lens" — 4 種投影式の標準解説
- Paul Bourke "Classification of fisheye lenses" — 投影式と用途の対応
- Blender Manual "Cameras" — Cycles の panorama_type 仕様

---

**以上**
