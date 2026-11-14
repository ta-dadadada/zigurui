# frontmatterの独立評価

この記録は配列内object・多重配列への対応前の実装を対象とする。以下の拒否構文と件数は当時の結果であり、現行仕様は [frontmatter](frontmatter.md)、拡張後の検証は [配列対応の評価](frontmatter-objects-evaluation.md) を参照。過去の測定値は再計測していない。

仕様は[frontmatterの集計と本文検索](frontmatter.md)、全数値は[匿名要約JSON](benchmark-results/frontmatter-evaluation-01.json)を参照。rawはローカルの `.local/benchmarks/frontmatter-evaluation-01/results.json` に保持した。公開要約に端末情報・絶対パスを含めていない。

## schema・型付き検索・境界

YAMLを別のライブラリで再解釈せず、手書きの文書と型付きmetadata対応表から期待schema・集合・原文出力を求めた。12文書中10文書にfrontmatterがあり、事前の固定スキーマなしに27フィールドを観測した。例えば `/count` はfloat・integer・stringが混在し、present=4 / missing=8。`/nullable` はpresent=3 / missing=9 / null_count=2となった。

- 検索124条件で、literal/normalizedとnormalized索引上のliteral経路、search/scan、計測有無の全出力が期待値に一致した。
- scalarと配列要素でinteger・float・booleanを混同せず、missingをnullとして扱わないことを確認。quoted/block/空文字列、日付・yes・on・True・01のstring扱い、i64境界、floatの範囲・ゼロ指数も含めた。
- nested map、空配列・空map、空キー、`=`、`/`・`~`のescape、複数whereのAND、本文AND、metadataの大小文字を正規化しないことを確認した。
- headerだけの語は本文検索から除かれ、BOM/CRLFや半角濁点・互換文字によって長さが変わっても原文の行・抜粋が一致した。CLI非公開のbyte spanはZig単体テストの担当範囲である。
- 不正文書18種について、候補外・filter不一致でも検索144実行でエラーとなり、schemaでも拒否した。重複・壊れた構文・非map root・anchor/alias/tag/directive/merge・配列内のmap/配列・数値範囲外を含む。失敗時の標準出力は空だった。
- 不正filter44件を拒否し、where64個の成功と65個の拒否を確認した。header 256 KiB、深さ16、nodes4096、encoded pointer4096バイト、schema union4096 pathsについて、上限ちょうどと1超過の計10ケースが期待どおりだった。長いpointerはexplicit YAML keyを使い、implicit keyの構文上限と分離した。

schema unionの上限はschemaだけに適用し、文書ごとに解析するsearch/scanはunion超過でも成功した。JSON期待値256 KiB境界はOSのargv制限を避け、実装側のAPI単体テストで検証した。評価scriptの単体テスト7件も成功した。

## 性能・容量・互換性

固定seedの合成1,000文書、原文1,184,304バイト、本文1,108,414バイトで、ReleaseFast・構築3回・予熱2回・各検索10回を逐次実行した。以下はnormalizedの無計測CLI中央値。

| 条件 | raw候補 / 本文検証 / 一致 | search ms | scan ms | 本文正規化回数 search / scan |
| --- | ---: | ---: | ---: | ---: |
| raw全体の検索 | 1,000 / 1,000 / 1,000 | 45.03 | 54.79 | 1,000 / 1,000 |
| 本文のみ・filterなし | 1,000 / 1,000 / 1,000 | 44.08 | 53.75 | 1,000 / 1,000 |
| 半数適合 | 1,000 / 500 / 500 | 26.65 | 36.66 | 500 / 500 |
| 1文書適合 | 1,000 / 1 / 1 | 9.10 | 18.53 | 1 / 1 |
| 適合なし | 1,000 / 0 / 0 | 9.18 | 18.15 | 0 / 0 |
| 3語AND・半数適合 | 1,000 / 500 / 500 | 27.91 | 37.16 | 500 / 500 |
| 全1文字AND・半数適合 | 全走査 / 500 / 1 | 26.60 | 35.86 | 500 / 500 |
| headerだけに存在する語 | 1,000 / 1,000 / 0 | 41.85 | 50.77 | 1,000 / 1,000 |

全54測定条件でraw posting候補、filter通過数、一致数、本文変換数・入力バイト数が独立期待値と一致した。半数適合の変換入力は本文553,948バイト、1文書適合は183バイト、適合なしは0だった。metadataにしかないgramは候補に残るが、本文照合で結果から除かれた。

frontmatter有効時の計測版other区間は約2.0〜2.1msで、全header解析・本文view/maskの準備を含む。解析だけの独立区間ではなく、該当なしでもこの処理は残る。schema CLIは中央値9.10ms、peak RSS中央値8.11 MiB。本文のみsearchのpeak RSS中央値は8.83 MiB、normalized索引は3,420,385バイト、構築中央値71.15msだった（1 MiB=1,048,576バイト）。

保存形式はv2。計測有無の索引SHA-256が一致し、旧ANDバイナリが同じ原文から作った索引も同じ内容だった。旧・新双方によるraw phrase/ANDの相互読み込み6条件が一致した。metadataやschemaは保存していない。別runとは原文内容・文書パスが異なるため、索引サイズを直接比較しない。

チーム内の重い処理を止めたが、ホスト全体の負荷は未統制。時間・RSSは合成スモークの観測であり、性能保証ではない。実KB、別環境、任意のYAML文書全般は未評価。配列はscalar要素に限定し、配列内object/arrayや添字検索には対応しない。

## 再実行

通常版と`-Dprofile=true`版のビルド・テスト完了後、他の重い処理と時間帯を調整して実行する。新規run-idだけを使い、生成物・診断を削除しない。

```sh
python3 -B scripts/test-evaluate-frontmatter.py
python3 -B scripts/evaluate_frontmatter.py --run-id frontmatter-reproduction-01 \
  --plain .local/benchmarks/frontmatter-plain-v2/bin/zigurui \
  --profile .local/benchmarks/frontmatter-profile/bin/zigurui \
  --baseline .local/benchmarks/and-plain-v2/bin/zigurui \
  --provenance .local/benchmarks/frontmatter-provenance.json \
  --size 1000 --index-repeats 3 --warmups 2 --repeats 10
```
