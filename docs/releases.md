# リリースの配布と検証

## 現在の範囲

タグから配布用アーカイブを作り、GitHub Releaseへ公開する。現在の公開版は `v0.1.0-alpha.4`。次の候補は `v0.1.0-rc.1` で、RC・stable `v0.1.0` は未公開。公開済みの版とアセットは [Releases](https://github.com/ta-dadadada/zigurui/releases) で確認する。現インストーラの対応対象は `v0.1.0-alpha.3` 以降。以前の版は各Releaseのアセットを手動で取得する。

対象は次の2種類。ターゲットの正式な定義、アーカイブの内容、タグの検査は [scripts/package_release.py](../scripts/package_release.py) に置く。

| 配布名 | Zigターゲット | 実行環境 | ローカルでの確認（2026-11-14） |
| --- | --- | --- | --- |
| `macos-arm64` | `aarch64-macos` | Apple SiliconのmacOS | ReleaseFast構築・形式検査・展開後のCLI結合テスト11件が成功 |
| `linux-x86_64` | `x86_64-linux-musl` | x86-64のLinux。muslで静的リンク | クロスビルド・静的ELF検査と実行互換環境でのCLI11件が成功。公開時はGitHubのLinux runnerでも実行検証する |

対応OSの最低バージョンは未確定。macOSでの実行確認と、Linux向けにクロスビルドできることは別の検証として扱う。公開前に最終アーカイブをLinux x86_64（ubuntu-24.04）とmacOS arm64（macos-15）のネイティブrunnerで導入・実行する。macOSのDeveloper ID署名・公証は含めない。

ローカルでは両対象の `build-target` を別プロセスで並列実行し、集約・梱包・チェックサム・内容検査と、梱包処理の単体テスト8件が成功した。文書中のmacOS用チェックサム確認コマンドも、生成したアーカイブに対して確認済み。ここでは `v0.1.0-alpha.1` を配布名の検証用文字列として使用し、Gitタグは作成していない。

## 正規化検索追加時のローカル検証（2026-11-14）

未コミットのv2・正規化検索差分を含め、既存の2ターゲットをReleaseFastで構築し、アーカイブ構造・静的Linux ELF・チェックサムを確認した。両アーカイブの `LICENSE-utf8proc.md` はvendorの正本とバイト単位で一致した。macOSアーカイブから取り出した実行ファイルでCLI結合テスト5件、梱包処理の単体テスト10件が成功した。Linuxでの実行とGitHub上の実行は未検証。`v0.1.0-alpha.2` はローカルの梱包検証用の名前であり、タグの作成・push・公開はしていない。

## frontmatter追加時のローカル検証（2026-11-14）

正規化の局所改善・AND・frontmatterを含む最終ソースから、macOS arm64とLinux x86_64-muslをReleaseFast・stripで構築した。両アーカイブの構造・チェックサム・Linuxの静的ELFと、utf8proc/libyamlのライセンスが正本と一致することを確認した。通常設定の統合チェックに成功し、その後のfilter早期拒否と補足テストを含めReleaseSafeの全49単体テストも成功した。CLI・梱包・独立検索評価の詳細は[frontmatterの評価](frontmatter-evaluation.md)を参照。Linux上の実行とC内部mallocへの確保失敗注入は未実施。タグ作成・push・公開はしていない。

## インストール・XDG・設定対応のローカル検証（2026-11-14）

AND・frontmatter対応と配布・設定管理を含む両対象を `v0.1.0-alpha.1` でReleaseFast構築・梱包し、チェックサム・アーカイブ構造・実行形式の検査に成功した。このタグはローカル検証用の文字列で、タグ作成・push・Release公開は行っていない。

macOS arm64では、実アーカイブを取得スタブ経由でインストーラへ渡し、実バイナリの新規導入・版表示・同版再導入、設定・索引・原本の保持とCLI結合テスト11件に成功した。設定は `config.json` のmodeと相対index_pathを実際に適用し、正規化した検索ができることも確認した。Linux x86_64版も同じ試験に成功した。Linuxはarm64のDocker環境上でx86_64実行互換機能を使用し、インストーラのunameは対象値のスタブを使った。Linux x86_64のネイティブ環境での導入確認とは区別する。

`mise run check`、CLI結合テスト11件、梱包テスト11件に成功した。インストーラ試験7件はmacOSとLinuxのシェルで成功した。取得スタブを使い、再実行・更新・ダウングレード、取得・チェックサム・アーカイブ・権限の失敗、管理外の既存ファイルやリンクの保護、導入記録の保存失敗、バイナリ切替失敗、切替直後の中断からの再実行を確認する。実HTTPS取得とGitHub Actions上の実行は未検証。設定の不正・型・未知キー・重複・サイズ上限・読取権限、CLI上書きと相対パス基準、設定の上書き防止をCLI試験に含む。ベンチマーク処理10件（Linux専用1件はmacOSでskip）・合成文書4件・公開用要約5件の試験も成功した。AND・frontmatter・schemaと設定modeの併用、両依存ライセンスの欠落時保護も確認した。HOMEや配置先にバックスラッシュがある場合もハッシュ検証・導入・再導入ができ、不一致時は既存バイナリを保持することをGNU SHA-256を使うLinux環境でも確認した。検証用スクリプト・アセットは `.local/distribution-validation/` に保持し、最終統合版は `pr-assets/` に置いている。

## バージョンと配布物

タグは64文字以内の `vX.Y.Z`（stable）または `vX.Y.Z-(alpha|beta|rc).N`。数値の先頭ゼロ、build metadata、改行、他のsuffixは拒否する。分類・公開引数の正本は `package_release.py` の `validate_tag` / `release_flags`。stableは通常Releaseとして `--latest=true`、alpha/beta/rcは `--prerelease --latest=false`。stableを公開すると、それがLatestになる（将来の旧系列保守版も同じ扱い）。Latestを自動選択して導入する機能はない。stableタグ対応の実装はstable公開の承認を意味しない。

RC公開後、タグが `v0.1.0-rc.1` の場合、次の3ファイルを配布する。

```text
zigurui-v0.1.0-rc.1-macos-arm64.tar.gz
zigurui-v0.1.0-rc.1-linux-x86_64.tar.gz
SHA256SUMS
```

各アーカイブは同名のディレクトリを1つ含み、その中に実行ファイル `zigurui`、`README.md`、必須の `LICENSE-utf8proc.md` と `LICENSE-libyaml.txt` を配置する。依存ライセンスの正本は [vendor/utf8proc/LICENSE.md](../vendor/utf8proc/LICENSE.md) と [vendor/libyaml/License](../vendor/libyaml/License) で、梱包元での欠落・配布アーカイブ内での欠落を拒否する。utf8procとlibyamlは静的に組み込む。`LICENSE`（`.md` / `.txt` 付きも含む）や `COPYING` がリポジトリに存在する場合は同梱する。実行ファイルはReleaseFastでビルドし、デバッグ情報を除去する。アーカイブには元の所有者名・生成時刻を残さない。開発用キャッシュ、ベンチマークのraw・生成文書・索引、ローカル資料は同梱しない。

## インストーラと版表示

利用者向けの導入・更新・復旧手順は [利用ガイド](usage.md#インストール更新) を正本とする。`scripts/install.sh` は同じ配布アセットとSHA256SUMSを取得し、指定した版とチャンネルの形式を確認してから配置する。stable指定も扱えるが、安定版アセットはまだ公開していない。

`compile_target(target, tag)` が `-Dversion=<tag>` を渡し、配布バイナリの `version` / `--version` はタグを表示する。通常のソースビルドは `zigurui dev`。インストーラは現行の梱包契約に合わせ、utf8procとlibyamlの両ライセンスを必要とする。版表示・XDG保存先・JSON設定はalpha.4でも使える。新しいrelevance・config項目はRC候補の仕様を確認する。

正式URLは `https://raw.githubusercontent.com/ta-dadadada/zigurui/main/scripts/install.sh`。mainの更新を追う入口として運用する。スクリプト自体を固定する場合は `https://raw.githubusercontent.com/ta-dadadada/zigurui/v0.1.0-rc.1/scripts/install.sh` を使う。版固定URLは対象タグの公開後に利用できる。インストーラの取得元と、引数で指定するバイナリの版はそれぞれ確認する。

ローカルでは `python3 scripts/test-install.py` で取得スタブと隔離したHOMEを用い、導入・再実行・更新・ダウングレード・失敗時保護を検証する。これはHTTPS経由の実取得やLinuxバイナリ実行の証拠ではない。CIとReleaseの検証でも同じテストを実行する。

## updateコマンドの実装と配布

`update` は `scripts/install.sh` をビルド時に埋め込み、実行中の実体の場所を `--bin-dir`、既存導入必須の条件を `--update` として渡す。CLIは利用できる引数名と個数だけを確認し、版・チャンネル・導入記録・SHA-256・アーカイブ・ライセンス・原子的な置換はインストーラを正本とする。更新時にmainのスクリプトを取得・実行することはない。

`build.zig` の資源登録と `build.zig.zon` の `scripts/install.sh` の同梱を維持する。配布アーカイブにスクリプトを別ファイルで含める必要はない。`--update` は既存ファイルがない場合も取得前に拒否し、配置直前にも管理状態を確認する。通常の初回インストールは従来どおり許可する。

隔離検証は `python3 scripts/test-update.py [実行ファイル]` で行う。実ユーザーの導入先は更新しない。CIとReleaseの両OS配布物受入からも実行する。更新成功後に実行ファイルの版を確認し、失敗時は標準エラーの理由と外部インストーラへの案内を確認する。alpha.3にはこのコマンドは含まれず、alpha.4以降は対応する。

## ダウンロード後の確認

公開後は対象タグのReleaseページから、自分のOS・CPUに対応するアーカイブと `SHA256SUMS` を同じディレクトリへ保存する。次はmacOS arm64、タグ `v0.1.0-rc.1` の例。タグとファイル名は入手した版に合わせる。

```sh
awk '$2 == "zigurui-v0.1.0-rc.1-macos-arm64.tar.gz"' SHA256SUMS \
  | shasum -a 256 -c -
tar -xzf zigurui-v0.1.0-rc.1-macos-arm64.tar.gz
export PATH="$PWD/zigurui-v0.1.0-rc.1-macos-arm64:$PATH"
zigurui --version
zigurui help
```

Linuxでは `linux-x86_64` のファイルを選び、同じ確認を `sha256sum -c -` で行う。チェックサムが一致した後に解凍し、展開先を `PATH` に追加して起動する。上記の `export` は現在のシェルだけに適用される。SHA-256はダウンロードしたファイルと公開された配布物の一致を確認するもので、配布者の署名ではない。

文書の索引作成・検索コマンドは[READMEの使い方](../README.md#使い方)を参照する。RC公開後は `./zigurui-v0.1.0-rc.1-macos-arm64/zigurui --version` でタグを確認する。

## ローカルでの配布準備

Python 3とmiseで固定したZigが必要。リポジトリのルートから、未使用の出力先を指定する。

```sh
python3 scripts/package_release.py validate-tag v0.1.0-rc.1
python3 scripts/package_release.py build \
  --tag v0.1.0-rc.1 --output .local/release-assets
python3 scripts/package_release.py verify \
  --tag v0.1.0-rc.1 --directory .local/release-assets
```

この操作はローカルのビルドと梱包・検証だけを行い、GitタグやReleaseを作成しない。クロスビルドやアーカイブの構造検査だけでは、対象OSでの実行成功までは確認できない。

## 共通の配布物受入

[distribution.yml](../.github/workflows/distribution.yml) を通常CIとReleaseから共用する。CIでは `v0.1.0-rc.1` を版埋込み・梱包の試験用文字列として使い、タグもReleaseも作成しない。

1. タグを検証し、正式target定義からbuild matrixを作る。
2. Ubuntuの各ジョブでstripした配布binaryを構築し、有限のmetadataとbinaryだけを渡す。
3. 全targetの欠落・重複・未知target・余分なファイル・metadata・hashを検査して梱包し、最終archiveとSHA256SUMSだけをartifactへ保存する。
4. macOS arm64 / Linux x86_64へ同じ最終archiveを渡す。`test-release-acceptance.py` は実OS/CPU、archive構造・SHA256、版表示、匿名fixtureによるCLI・正規化・frontmatter・差分更新・heading/relevance/explain・独立oracleを検証する。
5. 隔離HOME/XDG/導入先で新規導入と、公開済みalpha.4の実archiveから候補版への組込みupdateを確認する。取得・checksum失敗時の既存binary/config/index/原本の保持も検証する。既存installer/update試験がarchive・管理外導入・置換失敗などを補う。

alpha.4はGitHub Releaseから取得し、両archiveとSHA256SUMSを検証して実行する。旧配布の取得が失敗しても試験を省略せず失敗する。stable更新は既存fixtureのshell binaryを使う **モック** で、未公開stableの実配布試験ではない。候補binaryをstable版と偽って検証しない。候補版そのもののstable形式梱包と版埋込みは、別途stableタグ文字列でローカル検証できる。

ローカルでは両archiveとSHA256SUMSを用意し、対象OSで次を実行する（未使用outputを指定）。

```sh
python3 scripts/test-release-acceptance.py --tag v0.1.0-rc.1 \
  --target macos-arm64 --directory .local/release-assets \
  --previous .local/previous-release --output .local/release-acceptance
```

Linuxではtargetを `linux-x86_64` に変える。受入の出力や生ログ・索引はartifactへ保存しない。配布artifactは3ファイル、target中間artifactはbinary/有限metadataだけで、私的KBや実検索語を使わない。SHA256は一致確認であり配布者の署名ではない。

## 公開手順と承認

公開の定義は [release.yml](../.github/workflows/release.yml)。`v*` タグpushで起動し、不正タグの拒否、共通source検査・梱包テスト・installerテスト、共通の配布受入を経て、全対象が成功した場合だけpublishする。Securityも同じタグで実行し、publishは両OS受入とSecurityの成功を待つ。イベントごとの実行条件は[CIの実行条件](ci.md)を参照。書込権限はpublishだけが持つ。既存Releaseは更新・上書きせず、`gh release create --verify-tag` が失敗したら停止する。

通常CIのcheckではheading試験に加えてrelevance CLIと独立区間oracleを実行する。性能ベンチマークは配布受入から分離する。既知の広いnormalized relevanceの費用や差分更新が遅くなる条件は[利用ガイド](usage.md#版と互換性移行表)と評価資料を参照。性能改善は今回の対象外。

公開前にレビュー・マージを完了し、mainの固定SHAとCI/Security成功を確認する。未マージ枝から公開しない。タグpushは公開workflowを開始する操作なので、対象タグと公開範囲の承認を確認してから実行する。RCの承認からstable公開の承認を推定しない。

公開前に対象コミット・タグ・既存tag/releaseの有無・配布対象・検証結果を確認し、個人パス・秘密情報・索引・生ログが公開差分や成果物に含まれないことを確かめる。既存tag/releaseは削除・上書きしない。公開後はタグの固定SHA、Releaseのprerelease/Latest属性、3アセット、配布SHA256、公開URLからの隔離導入・更新と対象OSでの起動を確認する。モック取得成功を実HTTPS配布確認の代わりにしない。


## タグ保護（設定済み）

2026-11-15に利用者がタグ保護を設定し、その後のAPI読み取りでactiveな `release-tag-create` / `release-tag-immutable` と、既存の[main](https://github.com/ta-dadadada/zigurui/rules/27216606)保護が維持されていることを確認した。mainにはPR・履歴・削除/更新制限があり、required checksは `check` / `Scan (secret)`（GitHub Actions由来）、最新base必須はfalse、管理者roleのalways bypassがある。接続ユーザーはrepository admin、workflow既定権限はread、fork初回contributorには実行承認が必要。これは確認時点の状態であり、設定を変更していない。画面の未取得の選択状態は推測しない。

公開タグには次の2つのactive tag rulesetを重ねている。対象は名前パターン `v*`（API表現は `refs/tags/v*`）、除外なし。タグ名のSemVer検証は引き続き梱包処理が担当する。

| ruleset | ルール | bypass |
| --- | --- | --- |
| release-tag-create | Restrict creationsのみ | 現在の個人repositoryでは公開担当のRepository adminだけをAlways allow。write/maintain全員、Dependabot、一般のActions/Appには与えない。管理者が増えたら対象者を再確認する |
| release-tag-immutable | Restrict updates、Restrict deletions、Block force pushes | 空。作成を許した担当者にも通常のタグ移動・削除を許さない |

1つのrulesetに3制限と同じbypassをまとめると、作成者が更新・削除もbypassできる。2つに分けることで「指定担当者は新規作成だけ可、既存タグは全員不変」を表す。タグにはPRによる作成経路がないため、作成bypassにFor pull requests onlyを選ばない。組織へ移行して専用release team/Appを使うなら、その管理主体だけへ絞る案を別途判断する。既存タグも保護対象となるが作り直しは不要で、誤った公開は新しい版で訂正する。規則自体を編集できるadminの権限までは不可逆に封じない。

タグ作成者は承認済み固定SHAへタグをpushする人/認証主体。Releaseの `GITHUB_TOKEN` は既存タグを `--verify-tag` で確認し、検証済みartifactをReleaseへ添付するための別主体で、publishだけが `contents: write` を持つ。タグを作成・移動するbypassは不要。`GITHUB_TOKEN` 自身でタグをpushしても通常のpush workflowは連鎖発火しないため、公開手順の人によるタグpushと同一視しない。

タグ保護は、作成者・更新・削除を制限する。次の2つの確認を代替しない。

1. **タグを作る前**：対象SHAがマージ済みmain上にあること、その同じSHAのmain CI/Securityが成功したこと、公開承認と既存tag/releaseを確認する。PRの合成mergeの成功だけでは不十分。現在のworkflowはmainへの所属・事前成功を自動照合していないため、これは公開担当の必須手順として残る。mainのbypassがあることも考慮し、単にmain上という理由で合格扱いしない。
2. **タグpush後**：そのタグの版を埋め込んだ両OSの最終アーカイブが、同じrunでSHA256・導入・移行・検索等の受入とSecurityに成功すること。publishはその成功済みartifactだけを使う。タグ作成前のstatus checkを仮に追加できても、まだ存在しないタグ対象archiveの合格は証明できない。bypass actorを許すだけでもCI合格の証明にはならない。

rc.1はマージ確認・固定SHAのmain検証成功・別の続行指示後に、新規タグとして作る計画と両立する。stable公開の承認は別に必要。rulesetの設定は利用者が実施した。エージェントは読み取り確認のみで、bypass・権限を変更していない。今後の設定変更も別途判断する。

根拠： [タグの作成/更新/削除制限](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)、[bypassと対象パターン](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository)、[GITHUB_TOKENによるイベント発火](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)。
