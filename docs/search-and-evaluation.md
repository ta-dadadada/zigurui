# 複数語ANDの評価

仕様は[複数語AND検索](search-and.md)、全条件の数値は[匿名要約JSON](benchmark-results/and-evaluation-01.json)を参照。固定seedの合成1,000文書（本文1,108,414バイト）、ReleaseFastで構築3回、予熱2回、検索10回を逐次実行した。rawはローカルの `.local/benchmarks/and-evaluation-01/results.json` に保持し、端末情報・絶対パスを公開要約へ含めていない。

## 正しさと検索条件

- 手書き18ケースをliteral索引/literal、normalized索引/normalized、normalized索引/literalの3経路、計54条件で検証。各条件でsearch/scanと計測有無の全出力が一致した。
- 「検索 Zig」の連続語句では1文書、`--all ... 検索 Zig`では別行・逆順の2文書も含む3文書を取得した。これは合成例の到達範囲であり、実ノートの品質評価ではない。
- 空白を含む語を分割しないこと、全角空白の通常の正規化、重複語、正規化後の重複・1文字、重なり、改行を含む語句、gram偽陽性、展開・結合文字の原文抜粋を確認した。表示は語の入力順によらず最小の原文開始位置を使った。
- 空配列・空語・先行語が不一致でも後続の空語や不正UTF-8を拒否するなど60件のエラー検証と、`./--all`という索引名、input後の`--all`を通常語として扱うことを確認した。
- 既存14検索語×3経路の42条件で、旧phraseバイナリとの全出力一致と独立Python oracleとの一致を確認した。新機能で従来の連続語句検索を置き換えていない。

性能用コーパスの期待集合・原文行番号は、各語のNFKC→casefold→NFKCと原文各行の照合から求めた。候補件数は全語のgram集合から独立に計算した。正規化後のバイト位置を原文へ流用していない。CLI非公開のoffset/endや同開始位置での入力順の選択は、実装側のZig単体テストの担当範囲である。評価scriptの単体テスト5件も成功した。

## 時間・メモリ・変換回数

以下はnormalizedの無計測CLI中央値。候補欄の「全走査」はgramを持つ語がなく、本文全件を調べる経路を示す。

| 条件 | 本文検証 / 一致 | search ms | scan ms | 本文正規化回数 search / scan |
| --- | ---: | ---: | ---: | ---: |
| 単語「ガラス」 | 1,000 / 1,000 | 41.42 | 50.49 | 1,000 / 1,000 |
| AND「ガラス」「東京」「駅前」 | 1,000 / 1,000 | 44.81 | 53.31 | 1,000 / 1,000 |
| AND「ガラス」「希少」 | 1 / 1 | 6.07 | 48.91 | 1 / 1,000 |
| AND「ガ」「希」（全走査） | 1,000 / 1 | 41.94 | 51.49 | 1,000 / 1,000 |
| AND「東京駅前」「ガラス」（偽陽性） | 1,000 / 0 | 39.87 | 49.30 | 1,000 / 1,000 |
| AND「ガラス」「ｶﾞﾗｽ」（正規化後重複） | 1,000 / 1,000 | 41.63 | 51.53 | 1,000 / 1,000 |

計測版で、全条件の本文変換数と入力バイト総数が独立期待値と一致した。3語ANDも本文変換は3,000回に増えず、1候補1回だった。語の連続一致・原文位置照会の追加コストは残るため、単語とANDの時間差を同じ検索条件の最適化率とは扱わない。

3語ANDのpeak RSS中央値はsearch 7.77 MiB、scan 4.23 MiB（1 MiB=1,048,576バイト）。normalized索引は3,003,125バイト、構築中央値71.83ms・peak RSS中央値12.89 MiB。保存形式はv2で、計測有無の索引SHA-256は一致し、旧phraseバイナリも新たに作った索引を読めた。索引には文書パスを含むので、別runのサイズと直接比較しない。

チーム内の重い処理を停止したが、ホスト全体の負荷は未統制。時間・RSSは合成スモークの観測であり、性能保証や実ノート・別環境への一般化はしない。ランキング、同義語、送り仮名吸収は評価対象外。

## 再実行

評価runnerは新規run内の空の設定保存先を `XDG_CONFIG_HOME` に指定し、正解照合・予熱・計測のすべてを同じ環境で実行する。利用者のnormalized設定や不正な設定を読み込まず、評価ケースで指定したmodeを使う。

事前に通常版と`-Dprofile=true`版をビルドし、ビルド・テストを終えてから実行する。新規run-idだけを受け付け、生成物や診断を削除しない。

```sh
python3 -B scripts/test-evaluate-and.py
python3 -B scripts/evaluate_and.py --run-id and-reproduction-01 \
  --plain .local/benchmarks/and-plain-v2/bin/zigurui \
  --profile .local/benchmarks/and-profile/bin/zigurui \
  --phrase-baseline .local/benchmarks/candidate-plain/bin/zigurui \
  --provenance .local/benchmarks/and-provenance.json \
  --size 1000 --index-repeats 3 --warmups 2 --repeats 10
```
