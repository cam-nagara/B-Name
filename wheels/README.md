# wheels/

Extensions Platform で同梱する Python wheel 置き場。
`blender_manifest.toml` の `wheels = [...]` にここのファイル名を列挙する。

## 同梱予定 (Phase 3 以降)

| ライブラリ | ライセンス | 追加フェーズ | 用途 |
|---|---|---|---|
| fontTools | MIT | Phase 3 | OpenType 字形切替・メトリクス計算 (縦書き用) |
| Pillow | HPND | Phase 3 / Phase 6 | 書き出し時の画像合成・カラーモード変換 |
| pypdf | BSD | Phase 6b | PDF 結合 |
| psd-tools または pytoshop | MIT | Phase 6c | PSD 書き出し |
| littleCMS | MIT | Phase 6d | CMYK ICC プロファイル変換 |

## プラットフォーム

Blender 5.x のバンドル Python は 3.11 / 3.12 想定。以下の組合せを同梱する:

- `cp311-win_amd64` / `cp312-win_amd64`
- `cp311-macosx_arm64` / `cp312-macosx_arm64`
- `cp311-linux_x86_64` / `cp312-linux_x86_64`

ピュア Python wheel (例: `fontTools`, `pypdf`) は `py3-none-any.whl` 1 本で
全プラットフォームをカバーできる。C 拡張持ち (例: `Pillow`) は
プラットフォーム別 wheel が必要。

## 取得方法 (メモ)

```bash
# 例: Pillow の cp311-win_amd64 wheel を取得
pip download --only-binary=:all: --platform win_amd64 --python-version 3.11 \
    --no-deps -d ./wheels Pillow
```

Phase 0 時点では wheel は同梱しない (宣言リストも空)。
