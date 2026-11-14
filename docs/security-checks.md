# 依存更新とセキュリティ検査

## Renovateによる依存更新

`renovate.json` を依存更新の設定元とする。対象は標準の `mise` と `github-actions` managerで、月曜0時から9時まで（Asia/Tokyo）の更新枠を設ける。実際の実行時刻はRenovate側の巡回にも依存する。自動マージは行わない。

- Zig、actionlint、Lefthookのバージョンは `mise.toml` の `[tools]` を唯一の設定元とする。workflow側には転記しない。
- GitHub ActionsはコミットSHAとバージョン注釈を更新し、SHA固定を維持する。
- `aquasecurity/trivy-action` の `with.version` も標準managerの対象となる。Trivy本体の固定バージョンは `.github/workflows/security.yml` を設定元とする。
- Pythonスクリプトは標準ライブラリだけを使うため、pip managerは追加しない。Unicode処理は固定版utf8procをvendorとして同梱する。現行Renovateの対象外であり、[Unicode依存の更新手順](../vendor/utf8proc/SOURCE.md)に従い手動で更新・適合検証する。

標準managerが対象を扱えるので、独自の正規表現managerは追加していない。Dependabotの設定も併置しない。対応範囲は公式の [mise manager](https://docs.renovatebot.com/modules/manager/mise/) と [GitHub Actions manager](https://docs.renovatebot.com/modules/manager/github-actions/) を参照する。

設定ファイルだけでは外部Appの導入は完了しない。有効化する場合は、管理者が[公式の導入手順](https://docs.renovatebot.com/getting-started/installing-onboarding/)に従ってGitHub Appの権限を確認し、このリポジトリを対象として選択する。その後、初回の設定・検出対象・更新PRを確認する。この作業ではAppのインストール、認証設定、更新PRの作成は行っていない。

## Trivyの対象と判定

`.github/workflows/security.yml` は、mainへのpush、pull request、手動実行でリポジトリのfilesystemを検査する。権限は `contents: read` とし、結果はconsoleへ表示する。SARIFの送信やSecurity設定の変更は行わない。ActionはコミットSHA、本体は固定バージョンを指定する。

共通の除外、キャッシュ、表示形式と終了コードは `trivy.yaml` を正本とする。scannerとseverityの組み合わせは `security.yml` の `matrix.include` の2profileを正本とし、別々のジョブで並列実行する。Trivy Actionと本体バージョンの指定は共通の1stepに置く。`fail-fast: false` により、一方が失敗しても他方を打ち切らず、両方の結果を確認する。

| 項目 | 方針 |
| --- | --- |
| secretの判定 | UNKNOWN / LOW / MEDIUM / HIGH / CRITICALの全severityで、検出時は終了コード1 |
| misconfigの判定 | HIGH / CRITICALを表示し、検出時は終了コード1 |
| 閾値の理由 | 秘密情報はMEDIUMでも公開を避ける必要があるため、severityで除外しない。設定不備は影響が大きいHIGH / CRITICALを初期の判定対象とする |
| 除外 | `.git`、`.local`、`.zig-cache`、`zig-out`、`__pycache__`、Pythonバイトコード |
| 文書 | README、docs、公開用のベンチマーク要約もsecret検査の対象 |
| キャッシュ | リポジトリ内の `.local/trivy-cache` を使用 |

Trivyがエラー終了した場合もworkflowは失敗する。個別の指摘を無条件に抑制するignore設定は設けていない。

misconfigはTrivyが対応するDockerfileやIaC設定を対象とする。現時点では該当ファイルがほぼなく、すべてのYAMLやGitHub Actionsの危険な動作を網羅する検査ではない。`vuln` scannerは有効にしておらず、Zigや標準ライブラリのCVEを網羅する構成でもない。対応対象は [filesystem](https://trivy.dev/docs/latest/target/filesystem/) と [misconfiguration](https://trivy.dev/docs/latest/scanner/misconfiguration/) の公式資料を参照する。

secret scannerは既知の秘密情報パターンを探す。ユーザー名、端末名、個人パスなどのマスキングの代わりにはならない。公開用データの抽出と確認はpush前にローカルで行う。CIはpush済みの内容を扱うため、公開前の持ち出しを防ぐ保証には使わない。[secret scannerの仕様](https://trivy.dev/docs/latest/scanner/secret/)も参照する。

## ローカルでの確認

CIと同じバージョンのTrivyを用意して、リポジトリのルートから実行する。

```sh
trivy --version
trivy fs --config trivy.yaml --scanners secret --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL .
trivy fs --config trivy.yaml --scanners misconfig --severity HIGH,CRITICAL .
```

Renovateの設定は[公式validator](https://docs.renovatebot.com/config-validation/)で確認できる。取得するパッケージのバージョンは明示し、キャッシュは `.local/` に置く。

```sh
npm exec --yes --ignore-scripts --cache .local/npm-cache --package renovate@44.238.2 -- renovate-config-validator --strict --no-global renovate.json
```

## 今回の確認範囲

- `actionlint 1.7.12` でworkflowを検査した。
- Renovate 44.238.2のvalidatorはstrictなリポジトリ設定として成功した。インストールスクリプトを無効化したためRE2未ビルドの注意が出ており、validatorはJavaScriptのRegExpへフォールバックした。独自の正規表現設定は使用していない。
- 同じRenovateの標準抽出処理へ実際の `mise.toml` とSecurity workflowを渡し、Zig・actionlint・Lefthook、ActionのSHAと注釈、Trivy本体 `with.version` の抽出を確認した。外部へのPR作成やバージョン照会を行うdry-runは実施していない。
- CIと同版のTrivy 0.76.2（macOS ARM64）を[公式release](https://github.com/aquasecurity/trivy/releases/tag/v0.76.2)から取得し、公式checksumsとGitHub release assetのdigestを照合した。archiveのSHA256は `ae7ea358326960406b703c77b2ac490190797d80b306b5b50c2a27221da0a5b8`。
- ユーザーのDocker認証設定を変更せず、認証情報を持たない検証用設定で公開checks bundleを取得した。取得済みbundleのdigestは `sha256:b591e9190cff4aa4e385b1a25bccfd9ecf178d3c77fd194e1410b7ce7e1df058`（2026-11-14取得）。以下の検証は同版の本体とこのbundleによる結果で、組み込みchecksへのフォールバックではない。

| 検証対象 | profile / severity | 検出 | 終了コード |
| --- | --- | --- | ---: |
| 通常のリポジトリ | secret / ALL | 0件 | 0 |
| 通常のリポジトリ | misconfig / HIGH,CRITICAL | 0件（対応する設定ファイルも0件） | 0 |
| 正常な合成fixture | secret / ALL、misconfig / HIGH,CRITICAL | 各0件 | 各0 |
| 同じ安全なダミーsecret | 旧条件のsecret / HIGH,CRITICAL | severityで除外され0件 | 0 |
| 同じ安全なダミーsecret | 新条件のsecret / ALL | `openai-realtime-client-secret`、MEDIUM、1件 | 1 |
| rootユーザーの合成Dockerfile | misconfig / HIGH,CRITICAL | `DS-0002`、HIGH、1件 | 1 |

secret fixtureは実際に発行された認証情報を使わず、固定の合成文字列で[本体の組み込みルール](https://github.com/aquasecurity/trivy/blob/v0.76.2/pkg/fanal/secret/builtin-rules.go)への一致を検証した。ダミー値、検証用ファイル、rawログ、取得した実行ファイルは `.local/` にのみ保持し、公開対象に含めない。通常のリポジトリ検査ではこのfixtureを除外する。

GitHub上でのSecurity workflow実行とRenovate App導入は未実施で、リモート設定も変更していない。
