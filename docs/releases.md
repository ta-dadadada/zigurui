# プレリリースの配布と検証

## 現在の範囲

タグから配布用アーカイブを作り、GitHub Releaseへプレリリースとして公開するワークフローを用意している。現時点ではタグの作成・push、GitHub上の配布ワークフロー実行、Releaseの公開は行っていない。

対象は次の2種類。ターゲットの正式な定義、アーカイブの内容、タグの検査は [scripts/package_release.py](../scripts/package_release.py) に置く。

| 配布名 | Zigターゲット | 実行環境 | ローカルでの確認（2026-11-14） |
| --- | --- | --- | --- |
| `macos-arm64` | `aarch64-macos` | Apple SiliconのmacOS | ReleaseFastで構築、Mach-O arm64の形式検査、アーカイブ展開後のCLI結合テスト3件が成功 |
| `linux-x86_64` | `x86_64-linux-musl` | x86-64のLinux。muslで静的リンク | クロスビルドと静的リンクのELF x86-64形式検査が成功。実行は未検証 |

対応OSの最低バージョンは未確定。macOSでの実行確認と、Linux向けにクロスビルドできることは別の検証として扱う。Linuxでの実行テストはワークフローで行う構成だが、GitHub上ではまだ実行していない。macOSのDeveloper ID署名・公証は含めない。

ローカルでは両対象の `build-target` を別プロセスで並列実行し、集約・梱包・チェックサム・内容検査と、梱包処理の単体テスト8件が成功した。文書中のmacOS用チェックサム確認コマンドも、生成したアーカイブに対して確認済み。ここでは `v0.1.0-alpha.1` を配布名の検証用文字列として使用し、Gitタグは作成していない。

## 正規化検索追加時のローカル検証（2026-11-14）

未コミットのv2・正規化検索差分を含め、既存の2ターゲットをReleaseFastで構築し、アーカイブ構造・静的Linux ELF・チェックサムを確認した。両アーカイブの `LICENSE-utf8proc.md` はvendorの正本とバイト単位で一致した。macOSアーカイブから取り出した実行ファイルでCLI結合テスト5件、梱包処理の単体テスト10件が成功した。Linuxでの実行とGitHub上の実行は未検証。`v0.1.0-alpha.2` はローカルの梱包検証用の名前であり、タグの作成・push・公開はしていない。

## frontmatter追加時のローカル検証（2026-11-14）

正規化の局所改善・AND・frontmatterを含む最終ソースから、macOS arm64とLinux x86_64-muslをReleaseFast・stripで構築した。両アーカイブの構造・チェックサム・Linuxの静的ELFと、utf8proc/libyamlのライセンスが正本と一致することを確認した。通常設定の統合チェックに成功し、その後のfilter早期拒否と補足テストを含めReleaseSafeの全49単体テストも成功した。CLI・梱包・独立検索評価の詳細は[frontmatterの評価](frontmatter-evaluation.md)を参照。Linux上の実行とC内部mallocへの確保失敗注入は未実施。タグ作成・push・公開はしていない。

## バージョンと配布物

タグは64文字以内で、`v0.1.0-alpha.1` のような `vX.Y.Z-(alpha|beta|rc).N` 形式に限定する。数値の先頭ゼロは認めず、安定版形式の `v1.0.0` は受け付けない。当面はすべてGitHubのプレリリースとし、Latestには設定しない。

タグが `v0.1.0-alpha.1` の場合、次の3ファイルを配布する。

```text
zigurui-v0.1.0-alpha.1-macos-arm64.tar.gz
zigurui-v0.1.0-alpha.1-linux-x86_64.tar.gz
SHA256SUMS
```

各アーカイブは同名のディレクトリを1つ含み、その中に実行ファイル `zigurui`、`README.md`、必須の `LICENSE-utf8proc.md` と `LICENSE-libyaml.txt` を配置する。依存ライセンスの正本は [vendor/utf8proc/LICENSE.md](../vendor/utf8proc/LICENSE.md) と [vendor/libyaml/License](../vendor/libyaml/License) で、梱包元での欠落・配布アーカイブ内での欠落を拒否する。utf8procとlibyamlは静的に組み込む。`LICENSE`（`.md` / `.txt` 付きも含む）や `COPYING` がリポジトリに存在する場合は同梱する。実行ファイルはReleaseFastでビルドし、デバッグ情報を除去する。アーカイブには元の所有者名・生成時刻を残さない。開発用キャッシュ、ベンチマークのraw・生成文書・索引、ローカル資料は同梱しない。

## ダウンロード後の確認

公開後は対象タグのReleaseページから、自分のOS・CPUに対応するアーカイブと `SHA256SUMS` を同じディレクトリへ保存する。次はmacOS arm64、タグ `v0.1.0-alpha.1` の例。タグとファイル名は入手した版に合わせる。

```sh
awk '$2 == "zigurui-v0.1.0-alpha.1-macos-arm64.tar.gz"' SHA256SUMS \
  | shasum -a 256 -c -
tar -xzf zigurui-v0.1.0-alpha.1-macos-arm64.tar.gz
export PATH="$PWD/zigurui-v0.1.0-alpha.1-macos-arm64:$PATH"
zigurui help
```

Linuxでは `linux-x86_64` のファイルを選び、同じ確認を `sha256sum -c -` で行う。チェックサムが一致した後に解凍し、展開先を `PATH` に追加して起動する。上記の `export` は現在のシェルだけに適用される。SHA-256はダウンロードしたファイルと公開された配布物の一致を確認するもので、配布者の署名ではない。

文書の索引作成・検索コマンドは[READMEの使い方](../README.md#使い方)を参照する。CLIには版番号の表示コマンドがないため、入手したタグとアーカイブ名を記録しておく。

## ローカルでの配布準備

Python 3とmiseで固定したZigが必要。リポジトリのルートから、未使用の出力先を指定する。

```sh
python3 scripts/package_release.py validate-tag v0.1.0-alpha.1
python3 scripts/package_release.py build \
  --tag v0.1.0-alpha.1 --output .local/release-assets
python3 scripts/package_release.py verify \
  --tag v0.1.0-alpha.1 --directory .local/release-assets
```

この操作はローカルのビルドと梱包・検証だけを行い、GitタグやReleaseを作成しない。クロスビルドやアーカイブの構造検査だけでは、対象OSでの実行成功までは確認できない。

## 公開手順と承認

公開の定義は [.github/workflows/release.yml](../.github/workflows/release.yml) に置く。`v*` タグのpushで起動し、次の順序で処理する。

1. `validate` でタグと共通の検査・テストを確認し、`scripts/package_release.py` のターゲット定義からmatrixを生成する。
2. 各ターゲットを別のUbuntuジョブでクロスビルドし、実行ファイルと対象・タグ・ハッシュのmetadataをartifactとして渡す。一方が失敗しても他方を打ち切らない。
3. 集約ジョブが全ターゲットのartifactを受け取り、metadataと実行ファイルを照合してから、梱包・チェックサム・内容検査とLinux版のCLI結合テストを行う。
4. すべて成功した場合に、`publish` がプレリリースを作成する。

ターゲットの欠落・重複・未知のターゲット、許可外のファイルやmetadata、ハッシュの不一致を集約時に拒否し、一部のターゲットだけでは公開しない。リポジトリへの書き込み権限は `publish` だけが持つ。通常のCIとSecurity workflowは独立しており、それらの成功を新たなRelease公開条件には追加していない。macOS版もUbuntuからのクロスビルドであり、GitHub上のmacOSネイティブ実行は確認していない。

公開前に対象コミット、タグ、配布対象、検証結果をレビューする。ベンチマーク結果は公開用に投影した要約だけを追跡し、個人パス・機微情報・ローカル生成物がpush対象に入っていないことを確認する。ローカルのrawを変更したり、履歴を変更したりする必要はない。

タグの作成・pushとRelease公開には、その時点で別途承認を得る。タグpushは公開ワークフローを開始する操作なので、準備の一環として先に実行しない。承認後にレビュー済みコミットへタグを作成し、そのタグをpushする。ワークフロー完了後はタグが指すコミット、プレリリース表示、3配布物、チェックサム、対象OSでの起動を確認する。
