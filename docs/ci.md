# CIの実行条件

通常検証は [ci.yml](../.github/workflows/ci.yml)、セキュリティは [security.yml](../.github/workflows/security.yml)、タグ公開は [release.yml](../.github/workflows/release.yml) を入口とする。公開前のアーカイブ受入は [distribution.yml](../.github/workflows/distribution.yml) を共用する。

## 実行マトリクス

| イベント・変更対象 | 通常check（relevanceを含む） | secret / misconfig | 両OS配布物受入 | benchmark |
| --- | --- | --- | --- | --- |
| PR作成・更新・再開・base等の編集・ready for review：非配布Markdownだけ | 実行 | 実行 | 省略 | 省略 |
| 同PR：同梱README・ルートライセンスだけ、または非配布Markdownとの混在 | 実行 | 実行 | 実行 | 省略 |
| 同PR：その他の変更が1件でもある・判定不明 | 実行 | 実行 | 実行 | 実行 |
| mainへのpush（直接push・マージ・bot等による更新） | 実行 | 実行 | 実行 | 実行 |
| CIのworkflow_dispatch（選択refの全検証） | 実行 | 実行 | 実行 | 実行 |
| Releaseのvタグpush | source検査・条件/梱包/installer/oracleテスト＋配布実行試験 | 実行、公開必須ゲート | 実行、公開必須ゲート | 実行しない |
| Securityのworkflow_dispatch | 対象外 | 実行 | 対象外 | 対象外 |

PRの対象actionは `opened, synchronize, reopened, edited, ready_for_review`。Draftにも同じ規則を適用する。PR closeは、マージ時のmain pushで検証する。ラベル・レビュー・Draftへの切替だけではコードが変わらないため追加起動しない。fork由来PRと依存更新botにも同じ条件を使う。forkや自動作成PRの実行承認がGitHub側で必要な場合は承認待ちとなり、省略成功として扱わない。

mainはPRの合格結果から省略を推定しない。PRの合成mergeと実マージ後のSHAは異なり、直接push・bypass・外部更新もあるため、各pushの実SHAを全検証する。タグは対象版を埋め込んだ最終archive自体を試験する別の検証であり、mainの成功で代用しない。性能計測は公開の必須条件にせず、main/手動のbenchmarkで扱う。

通常checkは全対象イベントで省略せず、整形・actionlint・ビルド・Zig単体テスト、CLI、heading、relevance CLI/独立oracle、差分更新、更新、梱包・installer・評価スクリプトのテストを行う。非配布文書だけのPRでも、required checkの安定性と検証手順の整合を優先する。条件テスト自身も通常checkとタグ公開前に実行する。

## PRの変更判定

追加ジョブの分類は [scripts/ci_conditions.py](../scripts/ci_conditions.py) を正本とする。省略するのは次の既知の文書だけ。それ以外はすべて配布確認・benchmarkの対象にする。

- `docs/` または `skills/` 配下の `.md`：アーカイブに含まず、ビルドやテストとして実行もしない。配布確認・benchmarkを省略できる。
- `README.md` と梱包処理が認めるルートライセンス名：アーカイブに含むため配布確認が必要。これらだけならbenchmarkは省略できる。
- `src/`、`vendor/`、`testdata/`、`scripts/`、workflow、build/Zig/mise設定、セキュリティ・hook設定、未知のファイルは全実行。文書らしい拡張子だけで全ディレクトリを除外しない。vendorのライセンスもこの全実行に含む。

checkoutは既定のPR合成mergeを使い、変更判定だけは `fetch-depth: 0` で履歴を取得する。PRイベントのbase/head SHAを検証し、headがbaseを含み、checkoutの2親がそのbase/headと一致する場合だけ差分を採用する。headがbaseより古い、履歴不足、SHA/merge不一致、空差分、解析失敗、git失敗・timeout、未知イベントは全実行。省略のためにPR API権限やトークンを追加しない。

差分は `--no-renames --name-only -z` で取得し、renameを旧パス削除＋新パス追加として両方分類する。srcからdocsへの移動で実装の削除を見落とさず、削除ファイル、日本語・改行・タブを含む名前も扱う。PR全変更をローカルgitから取得するため、APIのページ上限やworkflowのpath判定件数に依存しない。

判定結果は有限のtrue/falseだけ。パス・PR本文・git生stderrはログへ出さない。判定ジョブのcheckout/setup/スクリプト自体が失敗した場合も、後続のdistribution/benchmarkは `!cancelled()` と「判定成功かつ出力が明示falseのときだけ省略」で実行する。省略は配布確認・benchmarkだけに許し、判定不能を省略成功へ変えない。判定ジョブの失敗自体は失敗として残る。

## required checks・権限・実行の分離

mainの現在のrequired checksは `check` と `Scan (secret)`。両ジョブは条件判定への依存やjob-level ifを持たず、docs-onlyでも実検査を行う。workflow全体の `paths` / `paths-ignore` で起動を省略しない。required jobのskipが成功扱いになる問題と、workflow未起動でPendingが残る問題を避ける。rulesetの設定はこの変更では変更しない。

権限は `contents: read`。forkのコードを実行するのは `pull_request` の通常runnerで、`pull_request_target`、独自secret、write token、認証情報の永続化を使わない。forkが自分のworkflowを改変できることまで条件判定で防ぐ仕組みではなく、公開はPRイベントでは起動しない。workflow/script変更はreviewとmain保護で確認する。

concurrencyはCI/Security/Releaseで固定prefixを分ける。PRだけ番号とイベント単位で新しい実行が古い実行をcancelする。main push・手動・タグはrun_idを含め、他イベントだけでなく同refの別実行もcancelやpending置換をしない。再利用Securityでもcallerの `github.workflow` にgroupを依存させない。

SecurityはPR/mainに独立起動し、required check名を維持する。CI手動とReleaseからも再利用でき、CI手動は全検証、Releaseのpublishは両OS受入とSecurity両方の成功を待つ。失敗・skip・cancelを公開成功に変える `always()` はpublishに使わない。公開するのはそのrunで検証したarchiveだけ。

baseブランチが進んだだけでは既存PRの再実行は保証されない。現在のmain rulesetは最新base必須ではないため、合流前の組合せ保証にはPR更新/再実行や保護設定の別検討が必要。実マージ後はmainの全検証が走り、公開前はその固定SHAの成功も確認する。merge queueは現在使用しておらず、導入時には `merge_group` イベントも追加する。

## ローカル検証と限界

```sh
python3 scripts/test-ci-conditions.py
mise exec -- actionlint
```

条件テストはイベント別の期待実行、関連/無関係パスの混在、fork action、同梱文書、実Git履歴でのrenameとNUL区切り、base/merge不一致、履歴/比較/JSON失敗、有限output、workflowのrequired/gate/concurrency契約を確認する。actionlintはYAMLと式の構文を検査する。ローカル試験はGitHubのイベント配送・fork承認・job skip/cancelの実動作を再現しないため、変更push後のPR/main/手動/タグの結果も確認する。公開タグを試験目的で作成しない。

GitHub仕様の根拠： [required checkとskip](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)、[イベント](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)、[concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)、[再利用workflow](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations)。
