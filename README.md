# zigurui

ローカルのMarkdownを対象に、索引の構築・検索・保存をZigで自作する検索エンジン。日本語・英語の部分一致検索に加え、表記ゆれを揃えた検索、複数語句のAND、frontmatterやファイル情報による絞り込みができる。

## インストール

### ソースからビルドする

Gitと [mise](https://mise.jdx.dev/getting-started.html) を用意し、次を実行する。利用するだけならGit hookの導入や開発用テストは不要。

```sh
git clone https://github.com/ta-dadadada/zigurui.git
cd zigurui
mise trust
mise install zig
mise run build
./zig-out/bin/zigurui help
```

実行ファイルは `zig-out/bin/zigurui` に生成される。固定版utf8procとlibyamlを静的に組み込むため、実行環境への追加ライブラリのインストールは不要。シェルにmiseを有効化していなくても、上記コマンドでビルドできる。

配布アーカイブを使う場合は [対応環境](docs/releases.md#現在の範囲) と [ダウンロード後の確認](docs/releases.md#ダウンロード後の確認) を参照し、以下の `./zig-out/bin/zigurui` を展開した実行ファイルのパスに置き換える。対応OSの最低バージョンは未確定。

## 使い方

```sh
# 索引の保存先を先に用意する
mkdir -p .local

# 文書ディレクトリを再帰的に読み、索引を作成する
./zig-out/bin/zigurui index /path/to/notes .local/notes.idx

# 保存済み索引から検索する（原文ディレクトリへのアクセスは不要）
./zig-out/bin/zigurui search .local/notes.idx '検索エンジン'

# 同じ文書群を索引なしで全文走査する
./zig-out/bin/zigurui scan /path/to/notes '検索エンジン'

# 表記ゆれを揃える索引と検索（既存索引とは別の保存先を使う）
./zig-out/bin/zigurui index --mode normalized /path/to/notes .local/normalized.idx
./zig-out/bin/zigurui search --mode normalized .local/normalized.idx 'ガラス'
./zig-out/bin/zigurui scan --mode normalized /path/to/notes 'ガラス'

# 複数の語句をすべて含む文書を検索する
./zig-out/bin/zigurui search --mode normalized --all .local/normalized.idx 'ガラス' '断熱'

# 保存済みfrontmatterのキーと型を調べ、本文をタグ条件で絞り込む
./zig-out/bin/zigurui schema .local/normalized.idx
./zig-out/bin/zigurui search --where file:/name '"notes.md"' .local/normalized.idx '検索'
./zig-out/bin/zigurui search --mode normalized --frontmatter --where /tags '"住宅"' .local/normalized.idx 'ガラス'

./zig-out/bin/zigurui help
```

## 現在の機能と制約

検索結果は `絶対パス:行番号: 抜粋` の形式で、1文書につき1件表示する。単語検索は検索表現で最初の一致、AND検索は各語句の最初の一致を原文へ戻した開始位置が最小の区間を使う。パス順で並び、一致がなければ何も表示せず正常終了する。抜粋はUTF-8の文字境界を保ち、一致開始の前32・開始から96コードポイント以内、改行までを表示する。normalizedでも行番号・抜粋は原文を使う。展開した文字の途中への一致は元の文字全体を指す。

- 既定ではUTF-8の `.md` / `.markdown` をプレーンテキストとして扱う。`--frontmatter` を明示すると先頭YAMLを解析して本文だけを検索する。拡張子の大文字・小文字は区別しない。
- 既定の `literal` は大文字・小文字、全角・半角、Unicodeの表記差を区別する。`normalized` はNFKC→default full case folding→NFKCで照合する。両モードとも各引数の空白は連続文字列の一部として扱う。`--all` で複数引数を指定すると全語句のAND検索になる。
- シンボリックリンクの文書やサブディレクトリは追跡しない。隠しファイル・隠しディレクトリは除外しない。
- 空の検索語、不正なUTF-8、読み取りや保存の失敗は、標準エラーにエラー名を表示して終了コード1を返す。
- normalizedでは非ASCIIを含む入力・各中間表現を4,194,304コードポイントまでに制限し、超過は `NormalizationTooLarge` になる。ASCIIだけの場合は由来表を省略し、この追加制限はない。
- 1文書は64 MiB未満、索引の読み込みは1 GiB未満に制限する。対象の一部を黙って省略せず、読み取りに失敗した場合はコマンド全体を失敗させる。
- 文書を読み取り専用で扱う。索引による文書の上書きと索引自身の取り込みを防ぐため、索引の保存先にはMarkdown以外の拡張子を指定する。保存先の親ディレクトリはあらかじめ作成する。

normalized検索はnormalizedで構築した索引を必要とする。normalized索引のliteral検索は保存原文を全文走査する。`--mode` を省略すると、索引の種類にかかわらずliteralになる。処理・位置対応・対象外の表記差は [検索の正規化](docs/search-normalization.md)、複数語句と代表位置の規則は [AND検索](docs/search-and.md) を参照。

frontmatterの事前スキーマ定義は不要。`schema` は保存済み文書からキー・混在型・有無と、組み込みのファイル名・保存パスの型を表示する。`--where` は型付きの等値条件で、複数指定はAND、scalar配列は要素のどれかへの一致となる。`file:/name` と `file:/path` の条件だけならYAMLを解析せず原文全体を検索できる。対象構文・型・制限は [frontmatter](docs/frontmatter.md) を参照。

索引には文書のパス・原文と転置インデックスを保存する。検索では保存済みの転置インデックスを読み込み、N-gramを再構築しない。原文の変更・追加・削除は、同じ `index` コマンドを再実行して全体を再構築すると反映される。再構築するまでは保存時点の本文・抜粋を表示する。

索引は再構築可能なローカル派生データで、独自バイナリ形式v2にバージョンとチェックサム、検索modeと正規化プロファイルを持つ。v1索引や異なる正規化プロファイルは読み込まず再構築を案内する。非互換のバージョンや破損した索引を読み込んだ場合は、`index` を再実行する。チェックサムは偶発的な破損の検出用で、認証には使わない。保存は一時ファイルへの書き込み・同期後に置き換えるため、構築や書き込みの途中で失敗しても既存索引を維持する。

ランキング・見出しの重み付け・差分更新・ファイル監視は未実装。ひらがなとカタカナ、同義語などは同一視しない。frontmatterを明示した検索と `schema` では、不正・未対応のYAMLを黙って除外せず、問題のパスと理由を標準エラーへ示し、部分結果を出さず終了コード1で停止する。

## 詳細仕様と開発者向け文書

- [検索の正規化](docs/search-normalization.md): 表記ゆれの範囲、原文位置、上限、索引の互換性。
- [複数語句のAND検索](docs/search-and.md): 語句の指定、代表位置、表示規則。
- [frontmatterとファイル情報](docs/frontmatter.md): 型、条件、スキーマ、対応YAML、失敗時の扱い。
- [開発環境の運用](docs/development.md): 開発用セットアップ、テスト・評価、構成、保守・配布の案内。
