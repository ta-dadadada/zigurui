# Unicode依存の固定版

utf8proc v2.12.0（Unicode 18.0.0）を使用する。上流のタグが指すコミットは `79cdd5ab40c79afa559f689ffc13b76812dee1ac`。

取得元: <https://github.com/JuliaStrings/utf8proc/tree/79cdd5ab40c79afa559f689ffc13b76812dee1ac>

2026-11-14にタグのrawファイルを取得した。以下の4ファイルは上流から無変更。`src/unicode_bridge.c` はアプリケーション側のC ABIであり、上流コードの改変ではない。

| ファイル | SHA-256 |
| --- | --- |
| utf8proc.c | f38438eb986ba7eedad0f2e820a70fae1aaf05197364ba0381a5e84775099ef8 |
| utf8proc.h | 08da85e820efaa3613500946c26b2dc427a07f76001ed7c16bc90f298b814c35 |
| utf8proc_data.c | b4911d4162d216afa612317a9b981851e15d672f4d93b25f3a1eb986f8c0aedb |
| LICENSE.md | 3b510150d34f248a221bb88e1d811238d6c6c18b51231822c42974c39bb07256 |

コードはMIT、同梱Unicodeデータは [LICENSE.md](LICENSE.md) 内のUnicodeライセンスに従う。配布アーカイブにはこのファイルを `LICENSE-utf8proc.md` として必ず同梱する。ライセンス内容は複製して管理せず、梱包時に正本から取得する。

## 適合テストデータ

`testdata/unicode/` は [Unicode 18.0.0 UCD](https://www.unicode.org/Public/18.0.0/ucd/) から取得した無変更の公開データ。ライセンスは同ディレクトリの `LICENSE.txt`（Unicode License V3）。実行ファイルにはテストデータを含めない。

| ファイル | SHA-256 |
| --- | --- |
| NormalizationTest.txt | 25a50d816764b04abfb4a646d3eb2b2a803284c3873d9a06757b94fe4513dde3 |
| CaseFolding.txt | a004797658a457bec4dc11683e39f69249ea3b595b752dbea6721c4c9f587b0d |
| LICENSE.txt（https://www.unicode.org/license.txt） | e7a93b009565cfce55919a381437ac4db883e9da2126fa28b91d12732bc53d96 |

## 更新

Renovateの現行managerはvendorを更新しない。更新時はライブラリとUnicodeデータの版・ライセンス・チェックサムを確認し、同じUnicode版の適合テストも更新する。`normalize.zig` のUnicode版・変換手順版と索引互換性を判断し、全体変換・位置復元・allocation failure・両配布対象のビルド・ライセンス梱包を検証する。システムのUnicode版へ暗黙に追随しない。
