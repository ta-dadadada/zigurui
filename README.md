# zigurui

ローカルのMarkdownを対象に、索引の構築・検索・ランキング・保存・更新をZigで自作する検索エンジン。

現在は開発環境の雛形のみ。検索機能は未実装で、実行ファイルは何も出力せずに終了する。

## セットアップ

[mise](https://mise.jdx.dev/getting-started.html) をインストールしてから、リポジトリのルートで実行する。

```sh
mise trust
mise install
mise exec -- zig version
mise run check
```

Zigのバージョンは `mise.toml` で 0.17.1 に固定している。外部ライブラリへの依存はない。
シェルにmiseを有効化していなくても、`mise run` または `mise exec --` 経由で利用できる。

## 開発コマンド

| コマンド | 内容 |
| --- | --- |
| `mise run build` | ビルド。成果物は `zig-out/bin/zigurui` |
| `mise run run` | ビルドして起動 |
| `mise run test` | テスト実行。現時点のテストケースは0件 |
| `mise run fmt` | Zigファイルを整形 |
| `mise run fmt:check` | 整形の検査 |
| `mise run check` | 整形の検査・ビルド・テスト |

Zigのオプションを直接指定する場合は、例えば `mise exec -- zig build -Doptimize=ReleaseFast` を使う。

## 構成

- `mise.toml`: ツールのバージョンと開発タスク
- `build.zig`: ビルド・実行・テストの定義
- `build.zig.zon`: Zigパッケージ情報
- `src/main.zig`: 空のエントリーポイント
- `.local/`: コンセプト設計書などのローカル資料・一時ファイル（Git管理対象外）

## 開発方針

- 元の文書は読み取り専用とし、索引は再構築可能な派生データとする。
- 検索の中核は自作し、Zig標準ライブラリや周辺用途のライブラリは必要に応じて利用する。
- まず全文走査を正解の基準とし、その後にUnicodeコードポイント単位のN-gramと転置インデックスを実装する。
- 正しさを確認してから、永続化、検索品質、更新・性能の改善へ進む。

詳細なコマンド体系、索引形式、対応OSの優先順位は今後決める。
