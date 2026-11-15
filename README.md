# zigurui

[![CI](https://github.com/ta-dadadada/zigurui/actions/workflows/ci.yml/badge.svg)](https://github.com/ta-dadadada/zigurui/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/ta-dadadada/zigurui/blob/main/LICENSE)

**ローカルのMarkdownを、日本語・英語で検索するZig製CLI。**

検索エンジンの中核を自作して学ぶプロジェクト。コードポイント2-gramと転置インデックスを使い、索引の構築・検索・保存をZigで実装している。

- **部分一致検索**：日本語・英語の語句を検索し、ファイルのパス・行番号・原文の抜粋を表示する。
- **表記ゆれへの対応**：全角・半角や英字の大文字・小文字などを揃える正規化検索。
- **絞り込み**：複数語句のAND、YAML frontmatter、ファイル名・パスによる条件指定。
- **ローカルで完結**：文書を読み取り専用で扱い、保存した索引から検索する。

現在は**プレリリース**。公開済みは `v0.1.0-alpha.4`、次の公開候補は `v0.1.0-rc.1`。RC・安定版 `v0.1.0` はまだ公開していない。以下の検索仕様はRC候補のソースを対象とし、公開版の機能差と更新時の注意は[移行表](docs/usage.md#版と互換性移行表)を参照する。ファイル監視は未実装。索引には原文とパスが含まれるため、元の文書と同様に扱う。

## インストール・更新

配布バイナリは **macOS arm64 / Linux x86_64** 向け。Zigのインストールは不要。curl、tar、shasumまたはsha256sumを用意する。OSの最低版は未確定。署名・macOS公証には未対応。

```sh
curl -fsSL https://raw.githubusercontent.com/ta-dadadada/zigurui/main/scripts/install.sh \
  | sh -s -- --version v0.1.0-alpha.4 --channel prerelease
~/.local/bin/zigurui --version
```

既定の配置先は `~/.local/bin`。PATHに追加して以下の例を実行する。更新時も希望する版を指定して再実行する。版の指定は必須で、自動的に最新版を選ばない。

alpha.4以降では `zigurui update --version <tag> --channel prerelease` でも同じ導入先を更新できる。インストーラの管理対象だけを更新し、設定・索引は保持する。RC公開後は `<tag>` に `v0.1.0-rc.1` を指定する。stableの公開後は `zigurui update --version v0.1.0`（既定channelはstable）。管理外の導入・旧版への切替は[利用ガイド](docs/usage.md#cliからの更新)を参照。

公開版は [Releases](https://github.com/ta-dadadada/zigurui/releases)、スクリプトを保存して確認する方法・更新・復旧・アンインストールは [利用ガイド](https://github.com/ta-dadadada/zigurui/blob/main/docs/usage.md#インストール更新) を参照。ソースから使う場合は [ビルド手順](https://github.com/ta-dadadada/zigurui/blob/main/docs/usage.md#ソースからビルドする) を参照。

## 使い方

以下は架空の文書を作って正規化検索を試す例。新しい作業用ディレクトリで実行する。

```sh
mkdir notes
printf '# 住宅メモ\nガラスの断熱性能を調べる。\n' > notes/housing.md

# 索引を構築する
zigurui index --mode normalized notes notes.idx

# 半角カタカナでも原文に一致する
zigurui search --mode normalized notes.idx 'ｶﾞﾗｽ'

# 複数語句をすべて含む文書を検索する
zigurui search --mode normalized --all notes.idx 'ガラス' '断熱'
```

結果は `絶対パス:行番号: 抜粋` の形式で、文書ごとに1件、既定ではパス順に表示する。relevanceは明示指定または設定で有効にする。見出し一致を優先する場合は [利用手順](docs/usage.md#見出し一致を優先する) の `--rank heading` を使う。fieldと近接による明示順位 `--rank relevance` と説明JSONは[利用手順](docs/usage.md#relevanceの順位と説明を見る)を参照。上の例では `housing.md` の2行目が見つかる。

自分の文書を検索するには `notes` を対象ディレクトリに置き換える。`.md` / `.markdown` を再帰的に読み込む。シンボリックリンクは追跡せず、隠しファイル・ディレクトリは対象に含む。原文の変更後は同じ `index` コマンドを再実行する。

`--mode` を省略すると設定ファイルの `mode` を使い、未設定なら `literal`（文字をそのまま照合）になる。normalized検索には同じモードで作った索引を使う。追加の検索例・条件指定・上限は [利用ガイド](https://github.com/ta-dadadada/zigurui/blob/main/docs/usage.md#使い方) を参照。

## 保存先・設定ファイル

索引パスを省略すると、既定では `~/.local/share/zigurui/default.idx` を使う。`zigurui paths` で保存先を確認できる。macOS・Linux共通でXDG方式を使い、`config.json` で既定索引・検索モード・順位を指定できる。

順位を自分の文書群に合わせる手順は[検索の調整ガイド](docs/search-tuning.md)を参照。人が読む場合と[利用者Skill](skills/zigurui/SKILL.md)を使うAIエージェントの両方に対応し、設定例・非公開の比較・説明の確認・復元を案内する。

詳細は [保存先](https://github.com/ta-dadadada/zigurui/blob/main/docs/usage.md#保存先) と [設定・CLIの優先順位](https://github.com/ta-dadadada/zigurui/blob/main/docs/usage.md#設定ファイル) を参照。

## 詳細仕様・開発資料

| 読みたいこと | 文書 |
| --- | --- |
| 導入・設定・検索動作の詳細 | [利用ガイド](https://github.com/ta-dadadada/zigurui/blob/main/docs/usage.md) |
| Unicode正規化と原文位置の扱い | [検索の正規化](https://github.com/ta-dadadada/zigurui/blob/main/docs/search-normalization.md) |
| AND検索の規則 | [複数語句のAND検索](https://github.com/ta-dadadada/zigurui/blob/main/docs/search-and.md) |
| メタデータによる絞り込み | [frontmatterとファイル情報](https://github.com/ta-dadadada/zigurui/blob/main/docs/frontmatter.md) |
| 正しさ・速度・容量の測定条件と結果 | [ベンチマーク](https://github.com/ta-dadadada/zigurui/blob/main/docs/benchmarks.md) |
| ビルド・テスト・実装の構成 | [開発環境の運用](https://github.com/ta-dadadada/zigurui/blob/main/docs/development.md) |

## ライセンス

zigurui本体は [MIT License](https://github.com/ta-dadadada/zigurui/blob/main/LICENSE) で公開する。
同梱する依存コードとテストデータにはそれぞれのライセンスが適用される。
[utf8proc](https://github.com/ta-dadadada/zigurui/blob/main/vendor/utf8proc/LICENSE.md)・[libyaml](https://github.com/ta-dadadada/zigurui/blob/main/vendor/libyaml/License)・[Unicodeテストデータ](https://github.com/ta-dadadada/zigurui/blob/main/testdata/unicode/LICENSE.txt) を参照。
