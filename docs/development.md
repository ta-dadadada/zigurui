# 開発環境の運用

利用者向けの導入と検索例は [README](../README.md) を参照。この文書は開発環境・検証・保守の入口とする。コマンドはリポジトリのルートで実行する。

## 開発環境の構築

[mise](https://mise.jdx.dev/getting-started.html) とPython 3を用意する。ツールの固定バージョンは [mise.toml](../mise.toml) を正本とする。

```sh
mise trust
mise install
mise exec -- zig version
```

Git hookを未導入の場合は、既存hookと共有範囲を確認してから `mise exec -- lefthook install` を実行する。既存hookを無条件に上書きしない。続いて `mise run check` を実行する。linked worktreeでは [worktreeの準備](#worktreeの準備) を使う。

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
| `python3 scripts/test-install.py` | インストーラの隔離環境テスト（取得はスタブ） |
| `python3 scripts/test-cli.py` | ビルド済み実行ファイルの結合テスト（Python 3が必要） |
| `python3 scripts/test-benchmark-corpus.py` | 合成文書・独立した正解判定の単体テスト |
| `python3 scripts/test-benchmark.py` | 時間・RSSの計測処理の単体テスト |
| `python3 scripts/test-benchmark-report.py` | 公開用要約の生成処理の単体テスト |

コミット前のGit hookは `lefthook.yml` を正本とし、整形の検査とactionlintだけを実行する。自動整形や自動stageは行わない。ビルド・単体テストは `mise run check`、結合テスト・ベンチマークまで含む検証はCIで行う。

hookは作業ツリー全体を検査するため、部分stageした場合にコミット対象だけが正しいことを保証するものではない。stageした差分も確認してからコミットする。

単体テストは公式Unicode適合データ、独立した位置期待値、索引/scanの一致、確保失敗を検証する。

結合テストは一時文書を作り、索引検索と全文走査の一致、文書を変更しないこと、保存時点の本文での検索、再構築による追加・削除の反映、シンボリックリンクの除外、エラー時の既存索引維持を確認する。

Zigのオプションを直接指定する場合は、例えば `mise exec -- zig build -Doptimize=ReleaseFast` を使う。

固定seedの合成文書を使う検索の正しさ・速度・容量の比較は、[ベンチマークの条件と結果](benchmarks.md)を参照する。

[GitHub ActionsのCI](../.github/workflows/ci.yml)はUbuntu上で検査・テストの `check` とReleaseFastの `benchmark` を、依存関係のない別ジョブで実行する。CIの測定条件は100・1,000文書、索引構築3回、各検索方式5回、予熱1回とし、GitHubの実行画面で概要と公開用の要約JSONを確認する。生の測定データや個人パスを含むmetadataはartifactへ保存しない。性能の閾値や過去結果との自動比較は設けない。

独立した処理の並列化は待ち時間の短縮を狙うものだが、セットアップの重複でrunnerの総使用時間が増える場合がある。GitHub上での所要時間や使用量は未計測。

CI相当のコマンドをmacOSで確認した記録がある。個別の実行状況はGitHub Actionsの実行結果で確認する。

### 機能別の独立評価

追加機能の評価ツールと再実行コマンドは次の記録に集約する。通常のCIがこれらをすべて実行するわけではないため、変更した機能に応じて確認する。

- [正規化検索の性能・正しさ](normalization-performance.md)
- [AND検索](search-and-evaluation.md)
- [frontmatter](frontmatter-evaluation.md)
- [ファイル名・パス](builtin-metadata-evaluation.md)
- [frontmatterの失敗診断](frontmatter-diagnostics-evaluation.md)

## 実装の構成と未実装の機能

- `src/normalize.zig` / `src/unicode_bridge.c`: Unicode正規化と原文バイト区間の由来追跡
- `src/search.zig`: コードポイント2-gram、転置インデックス、索引検索、基準となる全文走査
- `src/frontmatter.zig` / `src/yaml_bridge.c`: 型付きメタデータ、スキーマ集計、YAML解析
- `src/profile.zig`: 専用ビルドの時間・件数計測
- `src/storage.zig`: バージョン付き索引形式の符号化・復号・検査
- `src/config.zig`: JSON設定の読み込み・検証と設定内の相対索引パス解決
- `src/paths.zig`: XDG保存先の解決
- `scripts/install.sh`: Releaseの検証とユーザー権限での導入・更新
- `src/main.zig`: CLI、文書の収集、原子的な保存、検索結果の表示
- `scripts/test-cli.py`: 実行ファイルの結合テスト
- `mise.toml` / `build.zig` / `build.zig.zon`: ツール・タスク・ビルドの定義
- `.local/`: コンセプト設計書などのローカル資料・一時ファイル（Git管理対象外）

索引の構築・検索・保存を実装し、合成文書で正しさ・速度・容量を比較している。複数語AND・frontmatter・ファイル情報による絞り込みも実装済み。ランキング・見出しの重み付け・差分更新・監視は未実装。現時点の実行確認はmacOSで行っている。

## 保守・配布

- [依存更新とセキュリティ検査](security-checks.md): Renovate・Trivyの対象、判定基準、ローカル確認、外部App設定。
- [プレリリースの配布と検証](releases.md): 配布対象、梱包、公開前の確認と承認。
- [Unicode依存](../vendor/utf8proc/SOURCE.md) / [YAML依存](../vendor/libyaml/SOURCE.md): 固定版、取得元、ライセンス、更新手順。

## Codexの役割

共通ルールは [AGENTS.md](../AGENTS.md)、役割固有の指示とモデル・推論量は [.codex/agents/](../.codex/agents/) を正本とする。advisorは設計判断の前提検討、Zigレビューは所有権と失敗経路の追跡を重視し、推論量を高める。検索評価は既存の評価手順と独立した期待結果の照合を中心に、標準的な推論量から始める。実案件での品質・所要時間を見て設定を見直す。

| 名前 | 相談する場面 | 返すもの |
| --- | --- | --- |
| `advisor` | 計画確定前、設計の分岐、行き詰まり、測定と仮説の不一致、完了判断の迷い | 推奨案・根拠・主なリスク・次の確認 |
| `zig_reviewer` | 所有権、エラー経路、文字位置、索引・保存形式に関わる変更 | 重要度・根拠箇所・影響・必要な検証 |
| `search_evaluator` | 検索仕様や品質の確認、性能仮説の評価 | 独立した期待結果・測定条件・結果・限界 |

親は目的、対象worktreeと差分、相談したい判断、既存の検証・レビュー結果を渡す。必要な役だけを呼び、既存レビューで確認済みの範囲を繰り返さない。例: 「advisorを起動して、この計画の前提と最小の検証方法を確認し、助言を待ってから計画を固めてください」。

advisorとZigレビューは変更を行わない。読み取りだけなら専用worktreeは不要。ただし同時編集中のファイルを確定した差分と扱わず、対象コミットまたは変更を止めた差分を指定する。評価担当の出力先と測定時間帯は親が調整する。

[Codex公式仕様](https://learn.chatgpt.com/docs/agent-configuration/subagents)に従い、プロジェクト単位の独立したTOML定義を使う（2026-11-14確認）。権限は親を継承し、役割の「変更しない」という指示は独立した読み取り専用権限を作らない。厳密な読み取り専用が必要なら親セッションもその権限で開始する。

[Claude Code Advisor](https://code.claude.com/docs/en/advisor)の「判断の節目で別の視点に相談する」考え方を参考にした。Claudeの専用サーバーツールや会話全体の自動受け渡しを再現するものではない。Codexでは通常のカスタムサブエージェントとして必要な情報を渡す。より強いモデルの自動選択、常時監視、承認の代行は行わない。

## worktreeの準備

```sh
# この環境整備を含むコミットから作成する。未コミットの設定は自動で移らない。
git worktree add -b feature/example ../zigurui-example HEAD
cd ../zigurui-example
sh scripts/setup-worktree.sh
mise run check
```

miseを事前にインストールする。スクリプトはそのworktreeのmise設定を信頼登録し、固定バージョンのツールを導入し、Lefthookの設定と既存hookを確認する。Pythonの評価スクリプトにはPython 3も必要。

Gitの管理パスは `git rev-parse --git-path` で取得する。linked worktreeの `.git` はファイルなので、`.git/hooks` や `.git/info` を直接組み立てない。通常のhookはworktree間で共有される。セットアップは既存hookを上書きしない。未導入・別のhook・`core.hooksPath` の独自設定では、対象と影響を確認し、必要な承認を得てから `mise exec -- lefthook install` を実行する。hookの検査内容は [lefthook.yml](../lefthook.yml) に集約する。

[Codexのlocal environments](https://learn.chatgpt.com/docs/environments/local-environment)用に `.codex/environments/environment.toml` を用意した。アプリの環境設定で `zigurui` を選択し、新しいworktreeでセットアップが完了したことを確認する。自動実行されないクライアントでは、上記の手動手順を使う。セットアップではビルド・テスト・測定を自動起動しない。

## 出力と資料の境界

| 対象 | 保存先・扱い |
| --- | --- |
| 通常のビルド | 各worktreeの `zig-out/` |
| Zigのローカルキャッシュ | 各worktreeの `.zig-cache/` |
| Zigのグローバルキャッシュ | 各worktreeの `.local/zig-global-cache/` |
| ベンチ用ビルド・記録 | [ベンチマーク手順](benchmarks.md#再現手順)のworktree内パス |
| Pythonの一時ファイル | 既存の `tempfile` による実行ごとの一意なパス。固定名を共有しない |
| miseのツール本体 | ユーザー共通の固定バージョンを利用。エージェントごとに複製しない |

キャッシュの環境変数はmise.tomlに集約する。Zigは `mise run` / `mise exec --` で起動し、別worktreeを指す `--cache-dir` / `--global-cache-dir` / `--prefix` は渡さない。各worktree内ではエージェントごとのキャッシュを追加せず、同じ出力への同時ビルドを避ける。

新worktreeには `.local/` 内の設計書、過去の測定結果、実データはない。まず追跡済みREADME・docsと合成文書で作業し、不足資料は名前・必要な部分・用途を明示して親へ伝える。`.local/` の丸ごとのコピーやシンボリックリンク共有は禁止する。実文書由来の索引にも本文とパスが含まれるため、実データと同様に扱う。

## 性能測定の協調

worktreeはCPU・メモリ・ディスクを分離しない。測定担当は開始前に同じホストで作業する担当者と時間帯を調整し、ビルド・テスト・別のベンチが完了してから測定する。測定中はどのworktreeでもこれらを起動しない。読み取りレビューも大規模走査は避ける。強制停止はせず、完了を待つ。

自動のホスト全体ロックは設けていない。他プロジェクトを含む負荷を統制できない場合はスモーク確認に限定し、性能改善の根拠にはしない。条件・制約・未コミット差分を結果とともに残す。測定手順と個人情報の扱いは [docs/benchmarks.md](benchmarks.md) に従う。

作業終了時はworktreeと不要キャッシュを整理候補として報告する。測定結果や設計書を保護し、worktree削除を含め、対象・理由・記録への影響を示して承認を得てから削除する。

## 初回検証（2026-11-14）

- HEAD `7e289ba` から検証用linked worktreeを作成し、この環境整備の差分だけを反映した。`.local/` の資料や実データはコピーしていない。
- `.git` がファイルの状態でセットアップ、`mise run check`、Lefthookの全ファイル検査に成功。CLI結合テスト3件も成功した。共有pre-commit hookのSHA-256は前後で一致した。
- `zig env` と出力の存在確認により、通常ビルド、ベンチ用ビルド、両キャッシュが検証用worktree内にあることを確認した。Zigソースとbuild.zigは変更していない。
- ReleaseFast・合成100文書・14検索語・構築1回・検索各2回・予熱1回で `worktree-smoke-01` が完了した。既存runnerが索引/scanと独立した正解を照合した。ビルド完了後に測定し、別のビルドプロセスは検出しなかったが、ホスト全体の負荷は統制していない。速度の比較・保証には使わない。
- TOMLの構文と必須項目を確認し、Codex CLI 0.169.0の `debug prompt-input` に3役が列挙されることを確認した。通常の読み取り専用CLIセッションでは3役をそれぞれ指定し、親履歴を引き継がず起動して返答を受領した。役割の応答確認であり、実案件でのレビュー品質の評価ではない。
- `--ephemeral` での起動は `failed to load model context` / `no rollout found for thread id` で失敗した。通常セッションでの再確認は成功したが、一時セッションでの利用は未解決。
- アプリのlocal environment定義は、導入済みアプリの形式と照合した。セットアップスクリプトは手動実行で検証済み。アプリ画面からの環境選択とworktree作成時の自動起動は未確認。

ローカルの記録は `.local/worktree-validation/`、検証用worktreeはその下の `checkout/` に保持した。raw結果は `checkout/.local/benchmarks/worktree-smoke-01/results.json` にある。検証用キャッシュ・バイナリ・プロンプト診断出力は整理候補だが、まだ削除していない。worktree全体の削除は測定記録も失うため、先に残す記録を選ぶ必要がある。
