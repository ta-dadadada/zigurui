# 利用ガイド

導入・更新・保存先・設定・検索動作の詳細をまとめる。概要と最初の実行例は [README](../README.md) を参照。

## インストール・更新

macOS arm64・Linux x86_64向けのプレリリースを使用する。Zig・mise・sudoは不要で、curl、tar、SHA-256用のshasumまたはsha256sumが必要。

正式なインストーラURLは [install.sh](https://raw.githubusercontent.com/ta-dadadada/zigurui/main/scripts/install.sh)。現インストーラの対応対象は **v0.1.0-alpha.3以降**。公開状況と利用する版は [Releases](https://github.com/ta-dadadada/zigurui/releases) で確認する。以下は `v0.1.0-alpha.3` の導入例。

```sh
curl -fsSL https://raw.githubusercontent.com/ta-dadadada/zigurui/main/scripts/install.sh \
  | sh -s -- --version v0.1.0-alpha.3 --channel prerelease
```

保存して内容を確認してから実行する場合:

```sh
curl -fsSL https://raw.githubusercontent.com/ta-dadadada/zigurui/main/scripts/install.sh -o install.sh
less install.sh
sh install.sh --version v0.1.0-alpha.3 --channel prerelease
~/.local/bin/zigurui --version
```

通常のURLはmainの更新を追う。スクリプト自体も版固定で取得したい場合は、[alpha.3固定URL](https://raw.githubusercontent.com/ta-dadadada/zigurui/v0.1.0-alpha.3/scripts/install.sh) を使い、同じ版・チャンネルの引数を指定する。

既定の配置先は `~/.local/bin`。`--bin-dir /absolute/path` で変更できる。PATHに含まれない場合は案内を表示するので、自分のシェルに合わせて設定する。シェル設定は自動変更しない。別の管理方法で入れたziguruiが先に選ばれる場合は、表示された導入先の絶対パスで確認する。

更新・旧版への戻し方は、希望する版を指定して同じインストーラを再実行する。同じ版の再実行も可能。版の指定は必須で、自動的に最新版を選ばない。`--channel` の既定は `stable` で `vX.Y.Z` だけを受け付ける。当面のReleaseはプレリリースのみなので、`--channel prerelease` と `vX.Y.Z-alpha.N` / `beta.N` / `rc.N` のタグを明示する。版とチャンネルが一致しなければ終了する。

取得・SHA-256検証・展開に失敗した場合は既存バイナリを残す。導入記録のない既存ファイル、シンボリックリンク、前回導入後に他の方法で変更されたバイナリは上書きしない。miseなどで管理している場合はその方法で更新するか、別の `--bin-dir` を選ぶ。強制上書きは提供しない。

導入先の `.zigurui-install/` に管理記録と配布物のREADME・ライセンスを保持する。ここはインストーラの管理領域で、利用者の索引・設定を置かない。SHA-256は破損や取り違えの検出用であり、バイナリとチェックサムの配布元が同時に改ざんされた場合は防げない。署名・attestation・macOS公証は未対応。

取得や検証のエラーでは、版・チャンネル・公開アセットを確認して再実行する。書き込みに失敗したら、書き込める `--bin-dir` を選ぶ。ロック取得のエラーでは他の導入処理の終了を待つ。中断で `.zigurui-install.lock` が残った場合だけ、他の導入処理がないことを確認してその空ディレクトリを `rmdir` し、再実行する。導入記録と異なる場合は、管理元を確認するか新しい配置先を使う。

アンインストールは、このインストーラで導入した配置先の `zigurui` ファイルだけを削除する。例: `rm -- "$HOME/.local/bin/zigurui"`。設定・索引・原本は削除しない。管理記録とライセンスも自動削除しない。

## 保存先

macOS・Linux共通でXDG方式を使う。macOSでも `~/Library` 配下は使わない。

| 用途 | 既定 | 環境変数 |
| --- | --- | --- |
| 設定 | `~/.config/zigurui/` | `XDG_CONFIG_HOME` |
| 保持する索引 | `~/.local/share/zigurui/` | `XDG_DATA_HOME` |
| 再生成可能なキャッシュ（現在未使用） | `~/.cache/zigurui/` | `XDG_CACHE_HOME` |
| ログなどの状態（現在未使用） | `~/.local/state/zigurui/` | `XDG_STATE_HOME` |

各環境変数の絶対パスの下に `zigurui/` を付ける。空・相対パスの場合はHOMEから既定を解決する。既定索引はdata配下の `default.idx`。`zigurui paths` で現在の保存先を確認でき、このコマンド自体は何も作成しない。設定ファイルは同ディレクトリの `config.json` を使う。文書登録機能はまだない。

```sh
zigurui paths
zigurui index /path/to/notes
zigurui search '検索エンジン'
# 正規化用に別の索引を保持する場合は明示パスを使用する
zigurui index --mode normalized /path/to/notes /path/to/normalized.idx
zigurui search --mode normalized /path/to/normalized.idx 'ガラス'
```

パス省略時は単一の既定索引を使い、`index` の再実行で置き換える。複数の文書群・検索モードを保持する場合は索引パスを明示する。索引パスをCLIで省略した場合は、設定で選んだ索引も含めて親を構築時に作成する。明示パスの親は従来どおり事前に作成する。既存の索引を自動移動することはない。

索引は利用者が保持するデータで、キャッシュ削除の対象ではない。バイナリの更新と索引形式の互換性は別に扱う。読めない版・正規化プロファイル・検索モードでは理由と再構築の案内を表示し、自動削除・変換はしない。元の文書から、同じ保存先と必要な `--mode` を指定して `index` を再実行する。旧版で作った索引を残す必要がある場合は別の保存先へ再構築する。

## 設定ファイル

毎回同じ索引・検索モードを使う場合は、`zigurui paths` の `config_file` に表示される場所へUTF-8の `config.json` を作成する。既定は `~/.config/zigurui/config.json`。ファイルと親ディレクトリは自動作成しない。例:

```json
{
  "index_path": "/absolute/path/to/notes.idx",
  "mode": "normalized"
}
```

| 項目 | 設定時の動作 | 未設定時 |
| --- | --- | --- |
| `index_path` | CLIで索引パスを省略した `index` / `search` の保存先・読込先 | XDGのdata配下の `default.idx` |
| `mode` | `index` / `search` / `scan` の検索モード。`literal` または `normalized` | `literal` |

どちらの項目も省略でき、`{}` も有効。`--all` と `schema` は索引パスの明示を維持し、設定の `index_path` は使用しない。文書ディレクトリと検索語は、その都度CLIの引数で指定する。

優先順位は **CLIの明示指定 → 設定ファイル → 未設定時の既定値**。たとえば上の設定では、`zigurui index /path/to/notes` と `zigurui search 'ガラス'` に同じ索引・normalizedが適用される。`zigurui search --mode literal /other/notes.idx '検索'` は両方をCLI指定で上書きする。索引の再構築や変換は自動では行わないので、モードを変更した場合は対応する索引を作成する。

設定の `index_path` に相対パスを書く場合は、**config.jsonのあるディレクトリ**を基準にする。CLIの明示パスは従来どおり実行ディレクトリ基準。どちらも `~` や環境変数をアプリ側で展開しない（CLIではシェルの展開が先に行われる）。`paths` は設定を反映した既定索引とmodeを表示する。

設定ファイルがなければ従来の既定値で動作する。HOMEも有効なXDG_CONFIG_HOMEもなく設定先を決められない場合は設定なしとして扱い、明示パスでの操作を維持する。既定索引が必要な場合は、有効なXDG_DATA_HOMEかHOMEを設定する。

存在する設定は、CLIで上書きする項目も含めて全体を検証する。JSONオブジェクト以外、未知キー、重複キー、文字列以外（nullを含む）、不正なmode、空・NULを含むindex_path、不正UTF-8、64 KiBを超えるファイルはエラーになる。空ファイルも不正。構文不正や読取権限不足では設定ファイルの場所と理由を表示し、索引・設定を変更せず停止する。`help` と `version` / `--version` は設定を読まずに使用できる。設定ファイル自身を索引で上書きする指定は拒否する。

バイナリを更新・ダウングレードしても設定内容は保持する。旧版が新しい設定項目を理解できる保証はないため、未対応の版へ戻す場合は設定項目も確認する。

## ソースからビルドする

Gitと [mise](https://mise.jdx.dev/getting-started.html) を用意し、次を実行する。利用するだけならGit hookの導入や開発用テストは不要。

```sh
git clone https://github.com/ta-dadadada/zigurui.git
cd zigurui
mise trust
mise install zig
mise run build
export PATH="$PWD/zig-out/bin:$PATH"
zigurui help
```

実行ファイルは `zig-out/bin/zigurui` に生成される。上記の `export` は現在のシェルで `zigurui` を呼び出せるようにする。固定版utf8procとlibyamlを静的に組み込むため、実行環境への追加ライブラリのインストールは不要。シェルにmiseを有効化していなくても、上記コマンドでビルドできる。

配布アーカイブを使う場合は [対応環境](releases.md#現在の範囲) と [ダウンロード後の確認](releases.md#ダウンロード後の確認) を参照し、展開先のディレクトリを `PATH` に追加する。対応OSの最低バージョンは未確定。

## 使い方

以下は `zigurui` が `PATH` から実行できる状態の例。ソースからのビルドと配布バイナリで共通に使える。`/path/to/notes` は検索したい文書ディレクトリへ置き換える。

```sh
# 索引の保存先を先に用意する
mkdir -p .local

# 文書ディレクトリを再帰的に読み、索引を作成する
zigurui index /path/to/notes .local/notes.idx

# 保存済み索引から検索する（原文ディレクトリへのアクセスは不要）
zigurui search .local/notes.idx '検索エンジン'

# 同じ文書群を索引なしで全文走査する
zigurui scan /path/to/notes '検索エンジン'

# 表記ゆれを揃える索引と検索（既存索引とは別の保存先を使う）
zigurui index --mode normalized /path/to/notes .local/normalized.idx
zigurui search --mode normalized .local/normalized.idx 'ガラス'
zigurui scan --mode normalized /path/to/notes 'ガラス'

# 複数の語句をすべて含む文書を検索する
zigurui search --mode normalized --all .local/normalized.idx 'ガラス' '断熱'

# 保存済みfrontmatterのキーと型を調べ、本文をタグ条件で絞り込む
zigurui schema .local/normalized.idx
zigurui search --where file:/name '"notes.md"' .local/normalized.idx '検索'
zigurui search --mode normalized --frontmatter --where /tags '"住宅"' .local/normalized.idx 'ガラス'

zigurui help
```

## 現在の機能と制約

検索結果は `絶対パス:行番号: 抜粋` の形式で、1文書につき1件表示する。単語検索は検索表現で最初の一致、AND検索は各語句の最初の一致を原文へ戻した開始位置が最小の区間を使う。パス順で並び、一致がなければ何も表示せず正常終了する。抜粋はUTF-8の文字境界を保ち、一致開始の前32・開始から96コードポイント以内、改行までを表示する。normalizedでも行番号・抜粋は原文を使う。展開した文字の途中への一致は元の文字全体を指す。

- 既定ではUTF-8の `.md` / `.markdown` をプレーンテキストとして扱う。`--frontmatter` を明示すると先頭YAMLを解析して本文だけを検索する。拡張子の大文字・小文字は区別しない。
- 既定の `literal` は大文字・小文字、全角・半角、Unicodeの表記差を区別する。`normalized` はNFKC→default full case folding→NFKCで照合する。両モードとも各引数の空白は連続文字列の一部として扱う。`--all` で複数引数を指定すると全語句のAND検索になる。
- シンボリックリンクの文書やサブディレクトリは追跡しない。隠しファイル・隠しディレクトリは除外しない。
- 空の検索語、不正なUTF-8、読み取りや保存の失敗は、標準エラーにエラー名を表示して終了コード1を返す。
- normalizedでは非ASCIIを含む入力・各中間表現を4,194,304コードポイントまでに制限し、超過は `NormalizationTooLarge` になる。ASCIIだけの場合は由来表を省略し、この追加制限はない。
- 1文書は64 MiB未満、索引の読み込みは1 GiB未満に制限する。対象の一部を黙って省略せず、読み取りに失敗した場合はコマンド全体を失敗させる。
- 文書を読み取り専用で扱う。索引による文書の上書きと索引自身の取り込みを防ぐため、索引の保存先にはMarkdown以外の拡張子を指定する。CLIで明示した保存先の親ディレクトリはあらかじめ作成する。

normalized検索はnormalizedで構築した索引を必要とする。normalized索引のliteral検索は保存原文を全文走査する。`--mode` を省略すると設定の `mode` を使い、それも未設定なら索引の種類にかかわらずliteralになる。処理・位置対応・対象外の表記差は [検索の正規化](search-normalization.md)、複数語句と代表位置の規則は [AND検索](search-and.md) を参照。

frontmatterの事前スキーマ定義は不要。`schema` は保存済み文書からキー・混在型・有無と、組み込みのファイル名・保存パスの型を表示する。`--where` は型付きの等値条件で、複数指定はAND、scalar配列は要素のどれかへの一致となる。`file:/name` と `file:/path` の条件だけならYAMLを解析せず原文全体を検索できる。対象構文・型・制限は [frontmatter](frontmatter.md) を参照。

索引には文書のパス・原文と転置インデックスを保存する。検索では保存済みの転置インデックスを読み込み、N-gramを再構築しない。原文の変更・追加・削除は、同じ `index` コマンドを再実行して全体を再構築すると反映される。再構築するまでは保存時点の本文・抜粋を表示する。

索引は再構築可能なローカル派生データで、独自バイナリ形式v2にバージョンとチェックサム、検索modeと正規化プロファイルを持つ。v1索引や異なる正規化プロファイルは読み込まず再構築を案内する。非互換のバージョンや破損した索引を読み込んだ場合は、`index` を再実行する。チェックサムは偶発的な破損の検出用で、認証には使わない。保存は一時ファイルへの書き込み・同期後に置き換えるため、構築や書き込みの途中で失敗しても既存索引を維持する。

ランキング・見出しの重み付け・差分更新・ファイル監視は未実装。ひらがなとカタカナ、同義語などは同一視しない。frontmatterを明示した検索と `schema` では、不正・未対応のYAMLを黙って除外せず、問題のパスと理由を標準エラーへ示し、部分結果を出さず終了コード1で停止する。
