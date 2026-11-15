# 利用ガイド

導入・更新・保存先・設定・検索動作の詳細をまとめる。概要と最初の実行例は [README](../README.md) を参照。

## インストール・更新

macOS arm64・Linux x86_64向けのプレリリースを使用する。Zig・mise・sudoは不要で、curl、tar、SHA-256用のshasumまたはsha256sumが必要。

正式なインストーラURLは [install.sh](https://raw.githubusercontent.com/ta-dadadada/zigurui/main/scripts/install.sh)。現インストーラの対応対象は **v0.1.0-alpha.3以降**。公開状況と利用する版は [Releases](https://github.com/ta-dadadada/zigurui/releases) で確認する。以下は公開済み `v0.1.0-alpha.4` の導入例。RC・stableの公開状況と互換性は[移行表](#版と互換性移行表)を参照。

```sh
curl -fsSL https://raw.githubusercontent.com/ta-dadadada/zigurui/main/scripts/install.sh \
  | sh -s -- --version v0.1.0-alpha.4 --channel prerelease
```

保存して内容を確認してから実行する場合:

```sh
curl -fsSL https://raw.githubusercontent.com/ta-dadadada/zigurui/main/scripts/install.sh -o install.sh
less install.sh
sh install.sh --version v0.1.0-alpha.4 --channel prerelease
~/.local/bin/zigurui --version
```

通常のURLはmainの更新を追う。スクリプト自体も版固定で取得したい場合は、[alpha.4固定URL](https://raw.githubusercontent.com/ta-dadadada/zigurui/v0.1.0-alpha.4/scripts/install.sh) を使い、同じ版・チャンネルの引数を指定する。

既定の配置先は `~/.local/bin`。`--bin-dir /absolute/path` で変更できる。PATHに含まれない場合は案内を表示するので、自分のシェルに合わせて設定する。シェル設定は自動変更しない。別の管理方法で入れたziguruiが先に選ばれる場合は、表示された導入先の絶対パスで確認する。

更新・旧版への戻し方は、希望する版を指定して同じインストーラを再実行する。同じ版の再実行も可能。版の指定は必須で、自動的に最新版を選ばない。`--channel` の既定は `stable` で `vX.Y.Z` だけを受け付ける。RCまではプレリリースなので、`--channel prerelease` と `vX.Y.Z-alpha.N` / `beta.N` / `rc.N` のタグを明示する。版とチャンネルが一致しなければ終了する。

取得・SHA-256検証・展開に失敗した場合は既存バイナリを残す。導入記録のない既存ファイル、シンボリックリンク、前回導入後に他の方法で変更されたバイナリは上書きしない。miseなどで管理している場合はその方法で更新するか、別の `--bin-dir` を選ぶ。強制上書きは提供しない。

導入先の `.zigurui-install/` に管理記録と配布物のREADME・ライセンスを保持する。ここはインストーラの管理領域で、利用者の索引・設定を置かない。SHA-256は破損や取り違えの検出用であり、バイナリとチェックサムの配布元が同時に改ざんされた場合は防げない。署名・attestation・macOS公証は未対応。

取得や検証のエラーでは、版・チャンネル・公開アセットを確認して再実行する。書き込みに失敗したら、書き込める `--bin-dir` を選ぶ。ロック取得のエラーでは他の導入処理の終了を待つ。中断で `.zigurui-install.lock` が残った場合だけ、他の導入処理がないことを確認してその空ディレクトリを `rmdir` し、再実行する。導入記録と異なる場合は、管理元を確認するか新しい配置先を使う。

アンインストールは、このインストーラで導入した配置先の `zigurui` ファイルだけを削除する。例: `rm -- "$HOME/.local/bin/zigurui"`。設定・索引・原本は削除しない。管理記録とライセンスも自動削除しない。

## CLIからの更新

`update` を含む版では、実行中のziguruiを指定した版へ更新できる。次は構文の例で、`vX.Y.Z-alpha.N` は実在する公開タグへ置き換える。

```sh
zigurui update --version vX.Y.Z-alpha.N --channel prerelease
zigurui update --help
```

版の指定は必須。`--channel` の既定は `stable` で、プレリリースには `prerelease` を明示する。同版の再導入と、明示した旧版への切替も外部インストーラと同じ扱いになる。最新版の自動選択・バックグラウンド更新は行わず、更新コマンドを明示したときだけ通信する。

更新先は実行中の実体バイナリの場所に固定する。インストーラの導入記録に一致する場合だけ更新でき、`update` で `--bin-dir` は指定できない。miseなど別の方法で管理している場合や、改名・導入記録不一致の場合は、元の管理方法か外部インストーラを使う。シンボリックリンク経由で起動した場合も、実体の管理状態で判定する。

更新処理は実行ファイルに組み込んだインストーラを使用する。設定ファイルは読まず、不正な設定があっても更新できる。取得・検証・展開に失敗すれば既存バイナリを保持し、設定・索引・原本も変更しない。ライセンスと導入記録の保存・置換中断時の扱いは外部インストーラと共通。

公開済みの `v0.1.0-alpha.3` には `update` は含まれない。この機能を含む版への初回切替、旧版へ戻してコマンドが使えなくなった場合、組み込みインストーラが将来の配布形式を扱えない場合は、外部インストーラを使う。`zigurui update --help` は管理状態や設定にかかわらず表示できる。

## 版と互換性・移行表

この文書は `v0.1.0-rc.1` 候補の仕様。現在の公開版はalpha.4で、RCと安定版 `v0.1.0` は未公開。RCは配布・移行の確認用、stableはその確認後に別途判断する。未公開タグを指定しても導入できない。

| 対象 | alpha.4からRC候補への移行 | 旧版へ戻すとき・互換範囲 |
| --- | --- | --- |
| 実行ファイル / CLI | 管理済みなら `zigurui update --version v0.1.0-rc.1 --channel prerelease`（公開後）。alpha.3以前は外部インストーラ。同じ版の再導入も可能 | 更新は明示タグだけ。管理外の導入は元の管理方法、または別の `--bin-dir` で導入する。自動移行・強制上書きはしない |
| 検索 / 順位 | 既存のliteral/normalized、AND、絞り込み、通常のテキスト出力を維持。未設定の順位はpath、heading/relevanceはopt-in | 新しいオプションを旧版で使える保証はない。利用版のhelpを確認する。検索候補集合は順位設定で増えない |
| config | 従来の `index_path` / `mode` は引き続き使用可能。`rank` / `relevance` を追加しても索引の再構築は不要 | 旧版は未知キーを拒否する。戻す前に設定を別に保持し、旧版が理解する項目だけの設定を用意する。バイナリ更新はconfigを変更しない |
| JSON / explain | relevance専用のschema_version=1 / algorithm=relevance-v1。通常のテキスト出力とは別契約 | alpha.4以前はexplain非対応。将来のJSON変更はschema/algorithmを確認する。schemaコマンドのJSONとは別の契約 |
| 保存索引 | alpha.4は既にv3対応。v2を検索・schemaで読める。通常構築はv3を保存。v2索引から差分更新を始めるには、原本から **明示的な全再構築** でroot付きv3を作る。既存のroot付きv3は再構築不要 | alpha.3以前はv3を読めない。旧バイナリで原本から **別の保存先** へ再構築する。保持したいv2を新版のindexで上書きしない |
| 正規化 / index mode | normalized索引ではliteral検索も可能。literal索引をnormalized検索するには別途再構築 | 非対応の索引版・正規化profile・破損は拒否し、自動変換しない。索引・config・原本は更新失敗時も保持する |

stable公開後の更新構文は `zigurui update --version v0.1.0`（channel省略時はstable）。stable公開を意味する例ではない。配布対象はmacOS arm64とLinux x86_64（musl静的リンク）で、OS最低版は未確定、署名・公証・attestationは未対応。

新設定・新索引を旧版でも使えるという互換保証はしない。CLI/config/JSON/indexはそれぞれ上表の範囲で扱い、版とhelpを確認する。索引だけで原本のバックアップを代用しない。

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

毎回同じ索引・検索モード・順位を使う場合は、`zigurui paths` の `config_file` に表示される場所へUTF-8の `config.json` を作成する。既定は `~/.config/zigurui/config.json`。ファイルと親ディレクトリは自動作成しない。例:

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
| `rank` | `search` / `scan` の順位。`path` / `heading` / `relevance` | `path` |
| `relevance` | field・重み・近接信号の[設定](#relevanceの順位と説明を見る)。この項目だけでは順位を有効化しない | 組み込みのfieldと重み |

各項目は省略でき、`{}` も有効。`--all` と `schema` は索引パスの明示を維持し、設定の `index_path` は使用しない。文書ディレクトリと検索語は、その都度CLIの引数で指定する。

優先順位は **CLIの明示指定 → 設定ファイル → 未設定時の既定値**。たとえば上の設定では、`zigurui index /path/to/notes` と `zigurui search 'ガラス'` に同じ索引・normalizedが適用される。`zigurui search --mode literal /other/notes.idx '検索'` は両方をCLI指定で上書きする。索引の再構築や変換は自動では行わないので、モードを変更した場合は対応する索引を作成する。

設定の `index_path` に相対パスを書く場合は、**config.jsonのあるディレクトリ**を基準にする。CLIの明示パスは従来どおり実行ディレクトリ基準。どちらも `~` や環境変数をアプリ側で展開しない（CLIではシェルの展開が先に行われる）。`paths` は設定を反映した既定索引とmodeを表示する。

設定ファイルがなければ従来の既定値で動作する。HOMEも有効なXDG_CONFIG_HOMEもなく設定先を決められない場合は設定なしとして扱い、明示パスでの操作を維持する。既定索引が必要な場合は、有効なXDG_DATA_HOMEかHOMEを設定する。

存在する設定は、CLIで上書きする項目も含めて全体を検証する。JSONオブジェクト以外、未知キー、重複キー、mode/index_path/rankの文字列以外（nullを含む）、不正なmode/rank、空・NULを含むindex_path、不正UTF-8、64 KiBを超えるファイルはエラーになる。空ファイルも不正。構文不正や読取権限不足では設定ファイルの場所と理由を表示し、索引・設定を変更せず停止する。`help` と `version` / `--version` は設定を読まずに使用できる。設定ファイル自身を索引で上書きする指定は拒否する。

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

検索結果は `絶対パス:行番号: 抜粋` の形式で、1文書につき1件表示する。単語検索は検索表現で最初の一致、AND検索は各語句の最初の一致を原文へ戻した開始位置が最小の区間を使う。既定ではパス順で並び、一致がなければ何も表示せず正常終了する。抜粋はUTF-8の文字境界を保ち、一致開始の前32・開始から96コードポイント以内、改行までを表示する。normalizedでも行番号・抜粋は原文を使う。展開した文字の途中への一致は元の文字全体を指す。


- 既定ではUTF-8の `.md` / `.markdown` をプレーンテキストとして扱う。`--frontmatter` を明示すると先頭YAMLを解析して本文だけを検索する。拡張子の大文字・小文字は区別しない。
- 既定の `literal` は大文字・小文字、全角・半角、Unicodeの表記差を区別する。`normalized` はNFKC→default full case folding→NFKCで照合する。両モードとも各引数の空白は連続文字列の一部として扱う。`--all` で複数引数を指定すると全語句のAND検索になる。
- シンボリックリンクの文書やサブディレクトリは追跡しない。隠しファイル・隠しディレクトリは除外しない。
- 空の検索語、不正なUTF-8、読み取りや保存の失敗は、標準エラーにエラー名を表示して終了コード1を返す。
- normalizedでは非ASCIIを含む入力・各中間表現を4,194,304コードポイントまでに制限し、超過は `NormalizationTooLarge` になる。ASCIIだけの場合は由来表を省略し、この追加制限はない。
- 1文書は64 MiB未満、索引の読み込みは1 GiB未満に制限する。復元する文書・posting・文書ID配列の合計も1 GiBまでとし、保存前にも同じ制限を検査する。v2読取の配列予算は互換性維持のため2 GiBとする。大きなv2を読み込めても、新規v3が上限を超える場合は保存前に停止して旧索引を残す。対象の一部を黙って省略せず、読み取りに失敗した場合はコマンド全体を失敗させる。
- 文書を読み取り専用で扱う。索引による文書の上書きと索引自身の取り込みを防ぐため、索引の保存先にはMarkdown以外の拡張子を指定する。CLIで明示した保存先の親ディレクトリはあらかじめ作成する。

normalized検索はnormalizedで構築した索引を必要とする。normalized索引のliteral検索は保存原文を全文走査する。`--mode` を省略すると設定の `mode` を使い、それも未設定なら索引の種類にかかわらずliteralになる。処理・位置対応・対象外の表記差は [検索の正規化](search-normalization.md)、複数語句と代表位置の規則は [AND検索](search-and.md) を参照。

frontmatterの事前スキーマ定義は不要。`schema` は保存済み文書からキー・混在型・有無と、組み込みのファイル名・保存パスの型を表示する。`--where` は型付きの等値条件で、複数指定はAND、scalar配列は要素のどれかへの一致となる。`file:/name` と `file:/path` の条件だけならYAMLを解析せず原文全体を検索できる。対象構文・型・制限は [frontmatter](frontmatter.md) を参照。

索引には文書のパス・原文と転置インデックスを保存する。検索では保存済みの転置インデックスを読み込み、N-gramを再構築しない。原文の変更・追加・削除は、通常の `index` による全再構築、または[明示的な差分更新](#差分更新)で反映する。再構築するまでは保存時点の本文・抜粋を表示する。

索引は再構築可能なローカル派生データで、独自バイナリ形式v3にバージョンとチェックサム、検索modeと正規化プロファイル、収集rootを持つ。文書IDの一覧は差分＋可変長整数で保存する。v2は検索・schemaの読取互換を維持する。v1索引や異なる正規化プロファイルは読み込まず再構築を案内する。非互換のバージョンや破損した索引を読み込んだ場合は、`index` を再実行する。チェックサムは偶発的な破損の検出用で、認証には使わない。保存は一時ファイルへの書き込み・同期後に置き換えるため、構築や書き込みの途中で失敗しても既存索引を維持する。

BM25などの多段階ランキング・ファイル監視は未実装。ひらがなとカタカナ、同義語などは同一視しない。frontmatterを明示した検索と `schema` では、不正・未対応のYAMLを黙って除外せず、問題のパスと理由を標準エラーへ示し、部分結果を出さず終了コード1で停止する。

## 差分更新

RC候補はv3索引と差分更新に対応する。alpha.4からv3・差分更新に対応し、alpha.3以前の配布版は非対応。バイナリを更新する`zigurui update`とは別の操作。

```sh
# 最初は通常構築でroot付きv3索引を作る
zigurui index --mode normalized /path/to/notes /path/to/notes.idx
# 文書の追加・変更・削除後、同じディレクトリとmodeを明示する
zigurui index --mode normalized --incremental /path/to/notes /path/to/notes.idx
```

未変更文書のpostingsを再利用し、追加・変更した文書のN-gramだけを作り直す。全ファイルの読み取りと本文のバイト比較は行う。文書数・パス・本文・文書順がすべて一致する場合だけ、新しいpostingsの構築と索引の再保存を省き、索引のバイト列・mtimeを維持する。それ以外は索引全体を保存する。mtimeだけでは判定しない。移動・改名は削除＋追加として扱う。IDは新しいパス順へ付け直す。一般的な速度改善率は未確定。合成データでの条件付き比較だけで、実文書や別環境での効果は保証しない。

通常indexと同じく索引パスを省略でき、設定ファイルのmodeとindex_pathも適用される。索引の不在・破損、mode不一致、収集root不一致では失敗し、既存索引を保持する。暗黙の全再構築へは切り替えない。rootのないv2索引は検索できるが、差分更新を導入するには通常indexで明示的な全再構築が必要。

収集rootは実体の絶対ディレクトリで照合する。同じ実体への別名は許可し、別ディレクトリへの更新は拒否する。検索・schemaではrootや原本の実在を要求せず、保存時点の原文を検索する。収集rootも原文・文書パスと同じ私的データとして索引に含まれる。索引を公開・添付しない。新たなroot文字列を診断や評価レポートには出さない。

旧版はv3を読めない。旧版へ戻す場合は、そのバイナリで原本から別の保存先へ再構築する。旧索引を残す必要があれば、新版でindexを実行する際に別の保存先を選ぶ。既存索引の自動変換は行わない。

同じ索引への構築・差分更新は同時に実行しない。writer間のロックは設けておらず、原子的置換は並行writerの更新順まで保証しない。文書集合全体のファイルシステムsnapshotを取得する機能もない。

変更なしの場合も旧索引のchecksum・varint・件数等の完全検証とroot/mode照合を行う。完了表示と成功時の終了コードは従来と同じで、表示に失敗すれば非0で終了するが索引は変更しない。この経路では保存先への書き込み権限を必要としない。mtime・ファイルサイズだけで変更なしとは判断しない。

## 見出し一致を優先する

alpha.4以降の機能。alpha.3以前の配布版には含まれない。

`search` / `scan` の入力パス前に `--rank heading` を指定すると、一致済み文書のうち、検索語のいずれかが見出しにも一致する文書を先に表示する。順位を設定していなければ指定なし、または明示 `--rank path` で従来のパス順を維持する。同点はパス順。見出しの階層、個数、出現回数、一致語数では加点しない。AND検索は検索対象範囲で全語句が一致する条件を維持し、その一部が見出しにあれば優先する。

```sh
zigurui search --mode normalized --rank heading notes.idx 'ガラス'
zigurui scan --mode normalized --all --rank heading notes 'ガラス' '断熱'
```

初版が認識する見出しは、字下げなしで行頭から始まる1〜6個の `#` と空白またはタブに続く文字列。末尾の空白で区切られた `#` 列は除く。Setext形式、字下げ、引用・リスト内の見出しは対象外。インラインのリンク・強調・HTMLは解析せず、その文字列を検索対象にする。CommonMark全体の構文解析ではない。

文書先頭のfrontmatterと、バッククォートまたはチルダ3個以上で開始するコードフェンス内は加点対象外。閉じていないフェンスは残りを除外する。frontmatter境界が不正・上限超過の場合はその文書を加点しない。ランキングのためにYAMLの値を解析せず、通常検索にfrontmatter検証エラーを追加しない。`--frontmatter` 指定時の既存検証は維持する。

見出しの照合にも指定したliteral/normalizedを使う。検索結果の文書集合・行番号・抜粋は変えず、抜粋を見出しへ移動しない。`search` は索引に保存された原文、`scan` は現在の原文で判定する。見出し優先のための追加の索引形式変更や再構築は不要。順位の有用性と性能は実KBでは未評価。


## relevanceの順位と説明を見る

`--rank relevance` は一致済み文書をfieldの語句coverageと近接で採点する。通常表示の形式・一致位置・抜粋はそのままで、同点はパス順。既定はpath。設定の `rank` でも明示的に選べ、CLI指定 > config > pathの順に優先する。再構築は不要でv2/v3の保存原文を使う。

```sh
zigurui search --mode normalized --all --rank relevance notes.idx 'ガラス' '断熱'
zigurui search --mode normalized --all --rank relevance --explain notes.idx 'ガラス' '断熱'
```

`--explain` はrelevance専用のJSON契約（schema_version=1、algorithm=relevance-v1）。1件でもtotalとfield別内訳を返し、通常表示と順位・集合を共有する。offset/endは原文UTF-8バイト、window/ordered_windowは検索表現のUnicodeコードポイント半開区間で、bodyでは本文先頭、他fieldでは値先頭が0。valueは文字列値の0始まり番号。matched_term_indicesは検索modeで重複排除した入力順の0始まり語句番号。語句やfield値は出さないが、パスや設定の経路は含むため、説明JSONをそのまま公開しない。

まず基本機能で対象と条件を決める。表記差には `--mode normalized`、複数の語句には `--all`、本文限定・型付き条件には `--frontmatter` / `--where` を使う。見出し一致を優先するだけなら `--rank heading` で足りる。これらで足りない順位の調整に `relevance` を選ぶ。fieldを追加しても、そのfieldだけに一致する文書が新たな候補になるわけではない。

非公開の比較環境、架空の設定例、一要素ずつの比較、採用と復元の手順は[検索の調整ガイド](search-tuning.md)を参照。

fieldsを省略するとbody=1、heading=2、file_name=1。明示配列は全置換で1〜32件。metadataは任意の配列透過キー経路（既存 `--where` のescape規則）を指定する。title/tags等の固定項目はない。未設定・数値/bool/null・objectは採点せず、配列の各文字列は独立した値として最良の値だけを使う。重みは有限な0〜100で、未知項目・重複field・無効な経路を拒否する。relevance設定だけでは順位を有効にしない。CLIには重み指定を追加せずconfigへ集約する。

調整は一度に一項目ずつ変え、同じ語句・mode・絞り込みで通常表示と `--explain` の `settings`・field別寄与・totalを確認する。例えば `proximity_weight` と `ordered_weight` を両方0にするとcoverageだけになり、片方だけ0ならその信号だけを止められる。既定値が自分の文書群で最適とは限らない。

fieldの `weight: 0` は寄与を0にするが、抽出・正規化・資源上限の検査は残る。処理そのものを避けるには、そのfieldを `fields` 配列から除く（配列は全置換なので残すfieldも列挙する）。全fieldの重み0も有効で同点path順になるが、fieldは1件以上必要。近接重みが両方0の場合は出現探索と出現数上限の検査を省く一方、値のコードポイント上限は残る。

metadataを設定した場合は重み0でも一致済み文書のYAMLを検証する。不正なら診断名と対象パスを返して停止し、部分結果は出さない。非候補は追加解析しない（`--frontmatter` 自体の全体検証は従来どおり）。metadata未設定のpath/heading/relevanceには新しいYAML検証を追加しない。`--where` の型付き完全一致は正規化しない。

coverageは異なる入力語句の一致割合で、反復回数を加算しない。bodyは本文全体、headingは一つのATX見出し、metadataは一つの文字列値で数える。近接は同じfield値に限定し、bodyは同じ物理行に限定する。unorderedは全語句を覆う最短区間、orderedは入力順に開始位置が非減少となる最短区間。別配列要素や別見出しを結合しない。単一引数は従来どおり連続部分一致、複数引数の空白結合を完全語句とは扱わない。

rank・field・重み・絞り込み条件の変更に索引の再構築は不要。保存済み原文へ検索時に反映する。modeをnormalizedへ変える場合はnormalized索引が必要で、literal索引を自動変換しない。`index_path` の変更も索引を移動・変換しない。旧設定は引き続き使えるが、新項目を知らない旧版へ戻す場合は設定も確認する。

不正なrelevance設定は、`relevance.fields[0].weight` のような0始まりの箇所と理由で診断する。入力した値やmetadata経路・未知キーの文字列は診断に含めない。JSON構文や重複キーはJSON解析段階の診断となる。CLIでrankを上書きしても、不正設定を無視しない。

広い一致集合でのnormalized relevanceは全文変換と採点の費用が大きい。差分更新も旧索引の読込・検証・再利用処理があり、共有gramの多い文書群などで全再構築より遅くなる場合がある。速度向上の保証ではない。[差分更新の評価](index-storage-design.md#literal少数更新の追加調査2026-11-15)と[relevanceの検証](relevance-ranking.md)を参照。

採点式、W-Lが重なりにより隙間を相殺する性質、資源上限と超過時エラー、独立評価と限界は[relevanceの設計・検証](relevance-ranking.md)を参照。
