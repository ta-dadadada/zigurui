# frontmatterの集計と本文検索

## 使い方

```sh
zigurui index --mode normalized /path/to/notes .local/notes.idx
zigurui schema .local/notes.idx
zigurui search --where file:/name '"notes.md"' .local/notes.idx '検索'
zigurui search --mode normalized --frontmatter --where /status '"ready"' .local/notes.idx '検索'
zigurui scan --mode normalized --all --frontmatter --where /tags '"技術"' --where /published true /path/to/notes '検索' '設計'
```

`schema` は保存済み索引の全文書を解析し、事前にキーや型を定義せずスキーマをJSONで表示する。ファイル名・保存パスの組み込み項目も別の一覧に含む。`search` の `--frontmatter` は保存時点の本文だけを検索する。`scan` は現在の文書を読む。通常の検索は引き続きfrontmatterを含む原文全体が対象で、YAMLを解析しない。

`--mode` は従来どおりサブコマンド直後に置く。その後、入力パスより前に `--all`、`--frontmatter`、`--where` を指定する。これら3種類の順序は自由で、`--where` だけは繰り返せる。入力パス以降の引数は検索語句であり、オプションとして解釈しない。入力名が予約したオプション名なら `./--frontmatter` のように指定する。`index` に新しいオプションは不要で、`schema` の引数は索引パス1つだけ。

## 型と絞り込み

`--where <selector> <JSON-scalar>` は2引数を取る。YAMLキーは従来どおり `/status` などのキー経路で指定し、`--frontmatter` との併用が必要。ファイル情報は別の名前空間で指定する。

| selector | 値 |
| --- | --- |
| `file:/name` | 保存パスのbasename。拡張子を含む |
| `file:/path` | 索引に保存されたパス文字列そのまま |

ファイル条件だけなら `--frontmatter` は不要で、YAMLを解析せず原文全体を検索する。YAML条件を混在させる場合や本文だけを検索する場合は `--frontmatter` を付ける。明示した場合は、ファイル条件に合わない文書のheaderも検証する。

ファイル情報は常にstringで、文字列の完全一致だけを行う。大小文字・Unicode・全角半角を変換せず、本文の `--mode normalized` にも影響されない。非string条件は型不一致となり0件になる。未知の `file:` selectorは `InvalidFilter`。同じファイル名が複数あればすべてが候補となる。部分一致やglobは扱わない。

`search` は保存snapshotのパスを使い、現在のファイルシステムを再確認しない。通常のCLI索引は絶対パスを保存する。v2には収集rootがないため相対パスを推測せず、既存索引に相対パスが入っていてもその文字列を使う。`scan` は今回収集した文書のパスを使う。YAMLの `/file/name` や `/file:~1name` は組み込み項目と衝突しない。

キー名とmetadata値には本文検索のUnicode正規化を適用しない。

| YAMLの値 | 型 |
| --- | --- |
| 引用符付き・literal/folded block scalar | string |
| 空値、`null`、`~` | null |
| 小文字の `true` / `false` | boolean |
| JSON数値文法の整数 | integer（i64） |
| JSON数値文法で小数点・指数を含む数値 | float（有限f64） |
| `yes`、`on`、`01`、`True`、日付、その他のplain scalar | string |
| scalar・object・arrayを含む配列 | array |
| マッピング | object |

数値が範囲外の場合は `FrontmatterNumberOutOfRange`。非ゼロ値が0へunderflowするfloatも拒否する。文字列として扱う場合は引用する。`1` と `1.0` は別の型なので一致しない。同じ型の数値は値で比較するため、floatの `1.0` と `1e0` は一致する。

条件の期待値は有効なJSONのnull・boolean・数値・stringだけで、objectとarrayは拒否する。複数条件はすべて満たす必要がある。対象がscalarなら型と値の一致、配列なら直下のscalar要素のいずれかへの一致で判定する。末端の配列を再帰的に平坦化しないため、`items: [[1]]` は `/items` の `1` に一致しない。未設定はnullと一致しない。objectにscalar条件を指定した文書は型不一致となる。

キー経路はJSON Pointerのescapeを使う `/status` や `/author/name` の形式。キー中の `/` は `~1`、`~` は `~0` と表す。`=` を含むキーもそのまま指定できる。空pointer（root全体）は拒否し、`/` は空文字列のキーを指す。配列の添字による絞り込みは行わず、数値tokenもobjectのキーとして扱う。

経路の途中にある配列は再帰的に透過する。`items: [[{name: a}], {name: b}]` なら `/items/name` は `"a"` と `"b"` のどちらにも一致する。scalar・object・arrayの混在、空配列、配列直下の配列も許可する。複数の `--where` はそれぞれ文書内のいずれかの値で満たせばよく、別要素が各条件を満たしても一致する。同一要素に条件をまとめる機能はない。

## スキーマJSON

ルートは `document_count`、`frontmatter_count`、`fields`、`builtin_fields` を持つ。各fieldは次の情報を持ち、pointerのUTF-8バイト辞書順で並ぶ。

| 属性 | 意味 |
| --- | --- |
| pointer | 配列を透過するキー経路。JSON Pointerのescapeを使う |
| types | 観測した型のunion。型名の辞書順 |
| present | その経路に値が1つ以上ある文書数。同一文書の複数要素は1件 |
| missing | 全文書数からpresentを引いた数。frontmatterなしも含む |
| null_count | その経路の値にnullが1つ以上ある文書数。親配列のnull要素は加算しない |
| element_types | その経路にある配列の直下要素の型のunion。object・arrayも含む。空配列だけなら空リスト |

`builtin_fields` は `file:/name`、`file:/path` の順で、`selector`、`types`、`present`、`missing`、`null_count`、`element_types` を返す。両方ともtypesは `["string"]`、presentは全文書数、missingとnull_countは0、element_typesは空リスト。空の索引でも2つの定義を返し、presentも0となる。YAMLの `fields` へは混ぜない。

root・配列添字・匿名の配列要素はfieldとして出さない。object自体はfieldとして数え、その下のキーも別fieldとして数える。型が文書間や同一文書の要素間で異なっても拒否せずunionにする。空配列は親経路のpresentに含めるが、子経路の存在を作らない。例えば `items: [{name: a}, {name: null}, {name: null}, null]` では `/items/name` のpresentとnull_countはともに1、`/items` のnull_countは0となる。schema出力にはキーが含まれるため、公開する前に内容を確認する。

## YAMLの範囲と上限

文書先頭（UTF-8 BOMは許可）の完全な `---` 行から開始し、完全な `---` または `...` 行で終了する。改行はLF/CRLFに対応する。前後の空白や余分なCRを含む行は区切りではない。閉じ区切り後の改行までを除いた位置から本文となる。開始区切りがない文書はmetadataなし・本文全体、開始があって閉じがない文書はエラー。

空・コメントだけのheaderは空map。rootはmapのみ、nested mapとscalar・map・配列を含む配列を扱う。anchors、aliases、明示tags、YAML directives、mergeキー `<<` は `UnsupportedFrontmatter`。キーは復号後のscalar文字列として扱い、同じmapの重複キーや壊れた構文は `InvalidFrontmatter`。

| 制限 | 値と数え方 |
| --- | --- |
| header | 区切り行を除くUTF-8 payload 256 KiB |
| 深さ | rootmapを1、scalar/array要素を含む値ノードで16 |
| nodes | map・sequence・scalar・キーを各1として4096 |
| field pointer | escape後のUTF-8で4096バイト |
| schemaのunique paths | 全文書のunionで4096 |
| where条件 | 64個。期待値のJSONは256 KiB以下 |

header/深さ/nodes/pointer/schemaの上限超過は `FrontmatterTooLarge`。filterの形式・個数・長さ違反は `InvalidFilter`。YAML条件を指定して `--frontmatter` を省いた場合はCLIの `InvalidArguments`。`--frontmatter` を明示した検索では候補外・filter不一致の文書も含め全headerを検証し、失敗時は検索結果やschemaを部分出力しない。

## 解析に失敗した場合

`--frontmatter` の検索・走査と `schema` は、1文書でも解析できなければ終了コード1で停止し、標準出力へ結果を出さない。壊れた文書を黙って除外しない。標準エラーには原因のエラー名と対象パスを示す。

```text
zigurui: frontmatter: InvalidFrontmatter: file="/notes/broken.md"
```

パスの引用符・バックスラッシュ・改行・タブ・制御文字はescapeする。header、本文、metadata値は診断へ含めない。`search` と `schema` のパスは索引保存時点、`scan` は現在の収集パス。最初に失敗した文書を修正して再実行する。schema全体のunique path上限では `schema_aggregation: FrontmatterTooLarge` と、unionが上限を超えた時点の追加文書を示す。この場合はその文書単独の違反を意味しない。

通常検索とファイル条件だけの検索ではYAMLを解析しないため、壊れたheaderがあっても従来どおり原文を検索できる。診断の書き込み自体に失敗した場合は通常のエラー表示を試みる。結果の出力開始後に発生するOSの書き込み失敗について、標準出力全体の原子性は保証しない。

## 索引・位置・実装

保存形式v2には原文全体と従来のpostingsを保持し、metadataや派生スキーマは保存しない。`--frontmatter` を明示した検索では全headerを解析し、文書ごとのmetadataを解放した後、本文の借用slice・原文開始位置・filter適合maskだけを持つ。毎回のheader解析と文書数分の作業領域が必要になる。ファイル条件だけの場合は原文sliceと開始位置0を使い、YAML解析を省く。どちらも文書IDを変更せずmaskで絞り込む。

元のpostingsは本文検索より広い候補を返す。metadataだけのgramも候補になるが、filter適合文書の本文で連続一致を再確認するため結果には残らない。短語の全文走査でも同じmaskと本文を使う。原文開始位置を一致区間の両端へ足し、表示には元の文書を使うため、行番号・抜粋・ANDの代表位置は原文に対応する。headerと本文の間の改行は正規化で消さない。

計測版の最短posting数・候補数は原文postingsによる件数、本文検証数・本文変換数はfilter適合後の件数。変換バイト数は本文sliceだけを数える。metadata解析は既存のother区間に含める。

構文解析は固定libyaml 0.2.5のイベントAPIを使い、[上流のdepth制限だけをbackport](../vendor/libyaml/SOURCE.md)する。内部の32段制限とは別に、Zig側で製品仕様の16段を検査する。内部制限はglobal変数で、bridgeは単一スレッドCLIを前提とする。Cのイベント・parserは成功/失敗の双方で解放し、parser・filter・schema・projectionの合成fixtureで各確保位置に失敗を注入し、Zig側の所有メモリが解放されることを検証する。C内部mallocへの失敗注入はこの検証に含まれない。

独立した型・条件・原文位置の期待結果と性能スモークは[frontmatterの評価](frontmatter-evaluation.md)を参照。
