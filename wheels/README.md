# wheels/

Extensions Platform で同梱する Python wheel 置き場。
`blender_manifest.toml` の `wheels = [...]` にここのファイル名を列挙する。

## 同梱中

| ライブラリ | ライセンス | 追加フェーズ | 用途 |
|---|---|---|---|
| Pillow 12.2.0 (`cp311`/`cp312`/`cp313` Windows x64) | HPND | Phase 3 / Phase 6 | 書き出し時の画像合成・カラーモード変換 |

## 同梱予定

| ライブラリ | ライセンス | 追加フェーズ | 用途 |
|---|---|---|---|
| fontTools | MIT | Phase 3 | OpenType 字形切替・メトリクス計算 (縦書き用) |
| pypdf | BSD | Phase 6b | PDF 結合 |
| psd-tools または pytoshop | MIT | Phase 6c | PSD 書き出し |
| littleCMS | MIT | Phase 6d | CMYK ICC プロファイル変換 |

## プラットフォーム

現時点では Windows x64 に対応し、以下を同梱する:

- `cp311-win_amd64`
- `cp312-win_amd64`
- `cp313-win_amd64`

ピュア Python wheel (例: `fontTools`, `pypdf`) は `py3-none-any.whl` 1 本で
全プラットフォームをカバーできる。C 拡張持ち (例: `Pillow`) は
プラットフォーム別 wheel が必要。

## 取得方法 (メモ)

```bash
# 例: Pillow の cp313-win_amd64 wheel を取得
pip download --only-binary=:all: --platform win_amd64 --python-version 3.13 \
    --implementation cp --abi cp313 \
    --no-deps -d ./wheels Pillow
```
