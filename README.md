# zigurui

ローカルのMarkdownを対象に、索引の構築・検索・保存をZigで自作する検索エンジン。

日本語・英語を含む文書をCLIから部分一致で検索できる。Unicodeコードポイント単位の2-gram転置インデックスで候補を絞り、原文で連続した一致を確認する。1文字の検索語は全文走査に切り替える。全文走査を独立したコマンドでも実行し、索引検索と比較できる。

## セットアップ

[mise](https://mise.jdx.dev/getting-started.html) をインストールしてから、リポジトリのルートで実行する。

```sh
mise trust
mise install
mise exec -- lefthook install
mise exec -- zig version
mise run check
```

開発ツールのバージョンは `mise.toml` の `[tools]` を正本として固定している。実行ファイルに外部ライブラリへの依存はない。
シェルにmiseを有効化していなくても、`mise run` または `mise exec --` 経由で利用できる。

Codexの役割分担、linked worktreeのセットアップ、キャッシュの分離、測定時の協調は [開発環境の運用](docs/development.md) を参照する。

macOS arm64・Linux x86-64向けに、タグからプレリリースのバイナリを作る仕組みを用意している。配布物の確認・起動と公開前の手順は[プレリリースの配布と検証](docs/releases.md)を参照する。現時点ではタグのpush・Release公開は行っていない。

## 使い方

```sh
mise run build

# 文書ディレクトリを再帰的に読み、索引を作成する
./zig-out/bin/zigurui index /path/to/notes .local/notes.idx

# 保存済み索引から検索する（原文ディレクトリへのアクセスは不要）
./zig-out/bin/zigurui search .local/notes.idx '検索エンジン'

# 同じ文書群を索引なしで全文走査する
./zig-out/bin/zigurui scan /path/to/notes '検索エンジン'

./zig-out/bin/zigurui help
```

検索結果は `絶対パス:行番号: 抜粋` の形式で、1文書につき最初の一致を1件表示する。パス順で並び、一致がなければ何も表示せず正常終了する。抜粋はUTF-8の文字境界を保ち、最大で一致位置の前32文字・後96文字を表示する。

- UTF-8の `.md` / `.markdown` をプレーンテキストとして扱う。拡張子の大文字・小文字は区別しない。
- 大文字・小文字、全角・半角、Unicodeの表記差を区別する。複数語も空白を含む1つの連続文字列として検索する。
- シンボリックリンクの文書やサブディレクトリは追跡しない。隠しファイル・隠しディレクトリは除外しない。
- 空の検索語、不正なUTF-8、読み取りや保存の失敗は、標準エラーにエラー名を表示して終了コード1を返す。
- 1文書は64 MiB未満、索引の読み込みは1 GiB未満に制限する。対象の一部を黙って省略せず、読み取りに失敗した場合はコマンド全体を失敗させる。
- 文書を読み取り専用で扱う。索引による文書の上書きと索引自身の取り込みを防ぐため、索引の保存先にはMarkdown以外の拡張子を指定する。保存先の親ディレクトリはあらかじめ作成する。

索引には文書のパス・本文と転置インデックスを保存する。検索では保存済みの転置インデックスを読み込み、N-gramを再構築しない。原文の変更・追加・削除は、同じ `index` コマンドを再実行して全体を再構築すると反映される。再構築するまでは保存時点の本文・抜粋を表示する。

索引は再構築可能なローカル派生データで、独自バイナリ形式にバージョンとチェックサムを持つ。非互換のバージョンや破損した索引を読み込んだ場合は、`index` を再実行する。チェックサムは偶発的な破損の検出用で、認証には使わない。保存は一時ファイルへの書き込み・同期後に置き換えるため、構築や書き込みの途中で失敗しても既存索引を維持する。

## 開発と検証

| コマンド | 内容 |
| --- | --- |
| `mise run build` | ビルド。成果物は `zig-out/bin/zigurui` |
| `mise run run` | ビルドしてヘルプを表示 |
| `mise run test` | 検索・保存形式・抜粋の単体テスト |
| `mise run fmt` | Zigファイルを整形 |
| `mise run fmt:check` | 整形の検査 |
| `mise run lint:workflows` | actionlintで全GitHub Actionsワークフローを検査 |
| `mise run check` | 整形・ワークフローの検査・ビルド・単体テスト |
| `mise exec -- lefthook validate` | Git hookの設定を検証 |
| `mise exec -- lefthook run pre-commit --all-files` | コミット前の検査を作業ツリー全体に手動実行 |
| `python3 scripts/test-cli.py` | ビルド済み実行ファイルの結合テスト（Python 3が必要） |
| `python3 scripts/test-benchmark-corpus.py` | 合成文書・独立した正解判定の単体テスト |
| `python3 scripts/test-benchmark.py` | 時間・RSSの計測処理の単体テスト |
| `python3 scripts/test-benchmark-report.py` | 公開用要約の生成処理の単体テスト |

コミット前のGit hookは `lefthook.yml` を正本とし、整形の検査とactionlintだけを実行する。自動整形や自動stageは行わない。ビルド・単体テストは `mise run check`、結合テスト・ベンチマークまで含む検証はCIで行う。

hookは作業ツリー全体を検査するため、部分stageした場合にコミット対象だけが正しいことを保証するものではない。stageした差分も確認してからコミットする。

結合テストは一時文書を作り、索引検索と全文走査の一致、文書を変更しないこと、保存時点の本文での検索、再構築による追加・削除の反映、シンボリックリンクの除外、エラー時の既存索引維持を確認する。

Zigのオプションを直接指定する場合は、例えば `mise exec -- zig build -Doptimize=ReleaseFast` を使う。

固定seedの合成文書を使う検索の正しさ・速度・容量の比較は、[ベンチマークの条件と結果](docs/benchmarks.md)を参照する。

[GitHub ActionsのCI](.github/workflows/ci.yml)はUbuntu上で検査・テストの `check` とReleaseFastの `benchmark` を、依存関係のない別ジョブで実行する。CIの測定条件は100・1,000文書、索引構築3回、各検索方式5回、予熱1回とし、GitHubの実行画面で概要と公開用の要約JSONを確認する。生の測定データや個人パスを含むmetadataはartifactへ保存しない。性能の閾値や過去結果との自動比較は設けない。

独立した処理の並列化は待ち時間の短縮を狙うものだが、セットアップの重複でrunnerの総使用時間が増える場合がある。GitHub上での所要時間や使用量は未計測。

CI相当のコマンドはmacOSで確認済み。GitHub上のLinux実行は未確認。

## 構成と次の段階

- `src/search.zig`: コードポイント2-gram、転置インデックス、索引検索、基準となる全文走査
- `src/storage.zig`: バージョン付き索引形式の符号化・復号・検査
- `src/main.zig`: CLI、文書の収集、原子的な保存、検索結果の表示
- `scripts/test-cli.py`: 実行ファイルの結合テスト
- `mise.toml` / `build.zig` / `build.zig.zon`: ツール・タスク・ビルドの定義
- `.local/`: コンセプト設計書などのローカル資料・一時ファイル（Git管理対象外）

コンセプト設計書の段階1「正しく見つける」と段階2「索引を道具にする」を実装し、合成文書で速度・容量を比較している。段階3の複数語検索・ランキング・見出しの重み付け、段階4の差分更新・監視は今後の開発対象とする。現時点の実行確認はmacOSで行っている。

## 依存更新とセキュリティ検査

RenovateでmiseのツールとGitHub Actionsの更新を管理し、Trivyでsecretと対応する設定不備を検査する。対象・判定基準・ローカル確認・外部App有効化の手順と未確認事項は [依存更新とセキュリティ検査](docs/security-checks.md) を参照。
