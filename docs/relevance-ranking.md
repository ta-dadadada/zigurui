# 説明可能な relevance 順位

目的は、思いついた語で日本語ノートを探しやすくし、自作エンジンで順位の理由を学べること。rgとの速度競争、既製エンジン、同義語、リンクグラフは対象外。

## 契約と責任

検索集合・原文のUTF-8バイト位置・抜粋・v2/v3読取・v3保存は維持する。既定path、既存heading（二値、同点path）を維持し、新しい `--rank relevance` だけが採点する。`--explain` はrelevanceでのみ使用する別のJSON出力契約。通常表示と同じ一度の採点結果を使い、1件でも採点する。合計降順、同点は保存パスのUTF-8バイト順。

順位規則と採点内訳はrelevance、既存rankingはheading、frontmatterは型と文字列抽出、configはJSON境界を所有する。queryはCLI非依存の検索条件を受け取り、投影・候補検索・原文位置の復元・順位を組み立て、matchesと任意の採点内訳を一つのResultとして所有する。mainはCLIと入出力・表示を担当する。Resultは文書・設定を借用し、借用元より先にdeinitする。searchの準備済み語句共有APIのみ追加し、候補探索アルゴリズム/storageは変更しない方針。既存の一致済み再順位付けと診断経路はFollow、設定と表示をAdapt、候補拡大・保存変更は採用しない。変更軸は利用者のfield設定と採点信号であり、前者はデータで表し、プラグイン等の汎用抽象化は追加しない。

## 採点 v1

入力引数は空白分割しない。検索modeで一度準備・重複排除した異なる語句を使う。各fieldはbody、heading、file_name、または任意のfrontmatterの配列透過キー経路（JSON Pointerのescape、配列添字指定ではない）。title等を固定しない。既定fieldはbody（重み1）、heading（2）、file_name（1）。metadataは明示設定時だけ解析する。

各fieldの各値について `coverage = 一致する異なる語句数 / 全異なる語句数`。fieldのcoverageは最良値のcoverage。反復や配列要素数で加算しない。body coverageはfrontmatter境界を除いた本文全体のdistinct語句を数え、近接だけ物理行を区画とする。境界が壊れたbodyは空扱いにし、YAMLを検証しない。見出し行もbodyには含む。headingは既存headingと同じATX認識を共有、file_nameはbasenameを一値とする。metadataは指定経路の文字列のみ、配列は各文字列を独立値とする。未設定・非文字列・混在型の非文字列要素は0、objectを文字列化しない。配列のobject内に指定経路がある場合も各文字列を独立値として取り出す。

近接は同一値・同一区画の全出現（重なり可）を対象とする。距離は検索modeの表現のUnicodeコードポイント。全語句を覆う最小区間の長さをW（全出現区間は半開[start,end)、W=max(end)-min(start)）、語句長の合計をLとして、unordered信号は `1/(1+max(0,W-L))`。入力順に開始位置が非減少となる全語句の最小区間も別に求め同式のordered信号とする（重なり・包含・同じ開始も可）。同じ最小幅は値番号、開始位置の昇順を使う。W-Lは隙間そのものではなく、重なりが隙間を相殺する（abcdef___xと[abcdef,abc,x]では信号1）。1語句では近接信号は0。単一引数は連続部分一致であり、複数引数の空白連結を完全語句とは呼ばない。各信号はfield内の最良値を採用する。合計はfieldごとの `weight * coverage + proximity_weight * weight * unordered + ordered_weight * weight * ordered` の和。coverageと各近接の最良値が異なる場合も説明で各値の番号を明記する。

設定 `relevance` は `fields`（1〜32件、各kind/pointer/weight）、`proximity_weight`、`ordered_weight` を受ける。重みは有限非負0〜100、未知・重複キー、重複field、無効pointerを拒否。fields省略は既定値、明示配列は全置換。既定の近接重みは各1。CLI明示rank > config rank > path。relevance設定のみでは有効化しない。CLIはrankを選び、field/重みはconfigを唯一の設定源とする。

資源上限はdistinct語句64、全語句を合わせた1値の出現100,000、1値の検索表現コードポイント1,000,000。近接重みが両方0または1語句の場合は出現探索と出現上限の検査を省略する。コードポイント上限は常に検査する。正規化の作業メモリは変換後の上限確認より先に必要であり、この上限で保護されない。全候補の総仕事量に追加上限は設けず、逐次処理で保持メモリを抑える。超過時は明示エラー、近似・切捨て・部分出力なし。採点を全部終えてからmatchesを並べ替え、出力する。metadata設定時は一致済み候補の全YAMLを重み0でもfail-closedで解析し、既存診断名・対象パスを保持し値を出さない。path/headingには追加解析しない。

説明JSONはschema_version、algorithm、mode、設定、各結果のdistinct term数、tie_break、各結果のpath・原文offset/end・field別coverage件数/値番号・最小区間/ordered区間と信号・寄与・totalを含む。生のfield値は含めない。正常終了時のみ一つのJSONを出す。

## 検証の要約

Zig 0.17.1、macOS/arm64で以下を確認した。fixtureはすべて匿名の合成文書で、実KBは取得・複製・評価していない。

- Zig単体83テスト成功。Unicode公式適合データ、原文位置、保存互換、確保失敗時の解放と結果保全を含む。
- relevanceのCLI168コマンドと短文oracle80例成功。通常/説明の順位・集合、search/scan、v2/v3、snapshot、差分更新/再構築、設定の拒否と優先順位、型/配列/任意経路、YAML診断と部分出力抑止、資源上限を検証。
- 独立oracle・除去比較の手計算4テスト、既存Python検証12スクリプト93テスト、既存heading順位30コマンド成功。
- fmt:check、actionlint、通常ビルド、単体テスト、diffの空白検査が成功。Zigの所有権・失敗経路と検索評価の独立レビューを行い、指摘の修正後に限定再レビューを完了した。

oracleは短文の全出現の直積を列挙し、Zigの効率化した最短区間探索から独立させた。最短区間、包含、同じ開始、逆順、重なり、後方の最短、Unicode正規化、結合文字、全半角、field/配列境界を照合する。性能fixtureへ全組合せ列挙は適用しない。

## 品質評価

ラベルは合成ノートの利用場面から決め、採点式から転記していない。調整用1場面と固定評価用2場面を分離し、重みを最適化していない。固定評価用も新しく用意した極小の人工セットで、外部の未見データや実KBを代表しない。

全条件で同じfield・重み・候補を保ち、説明内訳からcoverage寄与のみ、近接+順序寄与のみ、全寄与を個別集計して並べ替える。併用条件の順はCLIのrelevance順と一致することを検査する。

| split/場面 | path nDCG@10 | fieldのみ | 近接のみ | 併用 |
| --- | ---: | ---: | ---: | ---: |
| tuning/materials | 0.2189 | 0.8542 | 0.9229 | 0.9229 |
| fixed/journey | 0.2189 | 0.8542 | 0.3733 | 0.7252 |
| fixed/retrieval | 0.2189 | 0.8542 | 0.9074 | 0.9074 |

MRRは全順位を対象とし、pathで0.5、field/近接/併用は1。Recall@10は全条件0.6で、非候補の関連文書も分母に含む。候補の再順位付けだけでは検索集合から漏れた正解を取り戻せない。

近接の併用は、別見出しや配列要素へ語が分散する固定例でfieldのみより悪化した。一般的品質改善や推奨重みの根拠にはしない。

評価器の初期の「近接のみ」条件にはbody coverageが混入していたため、比較根拠から除外した。上表は、説明済み寄与の分離へ修正し再測定した結果だけを示す。coverage差とpath順を逆にし近接を共に0にする手計算例、後続fieldの近接寄与が効く例で除去比較を検証する。

## 性能スモーク

ReleaseFast通常版と計装版を逐次ビルドし、他の担当内ビルド・テストを完了させてから予熱1回、通常版3回、計装版1回を逐次実行した。両版の検索結果一致も確認した。ホスト全体の負荷は未統制であり、性能比較や改善の採用根拠にはしない。

通常版の中央値は以下。CPUは子プロセスのuser+system、RSSはプロセス生存期間のpeakであり、検索処理だけのメモリ増分ではない。

| 条件 | 文書数 | wall ms | CPU ms | RSS bytes |
| --- | ---: | ---: | ---: | ---: |
| 広い語 | 100 | 9.49 | 7.64 | 2883584 |
| 1文字 | 100 | 5.20 | 4.37 | 2867200 |
| 長文 | 1 | 12.78 | 12.00 | 7749632 |
| 多数配列 | 4 | 6.76 | 5.76 | 3670016 |

通常版のwall/CPU/RSSと、計装版のfield_extract/field_normalize/scoring/proximity/rank_sortを分離して記録する。計装schemaは3で、既存readerは1/2も引き続き読む。計装の区間集計は排他的で内容・検索語・パスを含まない。query導入後は説明内訳の解放もResultのcleanupに含め、reportより前に完了させる。導入前の上表はその解放費用を含まないため、内訳を直接比較しない。

## 再実行

実行・テスト規約は[開発環境の運用](development.md)を参照。未使用の出力先を指定し、各コマンドを逐次実行する。出力先の匿名fixture・索引・説明の生データ・測定ログはローカルだけに保持する。

```sh
python3 scripts/test-relevance-oracle.py
python3 scripts/test-relevance.py --output .local/relevance-validation/run-NEW
python3 scripts/evaluate-relevance.py --output .local/relevance-evaluation/run-NEW
mise exec -- zig build -j1 -Doptimize=ReleaseFast --prefix .local/benchmarks/relevance-plain
mise exec -- zig build -j1 -Doptimize=ReleaseFast -Dprofile=true --prefix .local/benchmarks/relevance-profile
# ビルドと他の重い検証がすべて終わってから開始する。
python3 scripts/benchmark-relevance.py --plain .local/benchmarks/relevance-plain/bin/zigurui --profile .local/benchmarks/relevance-profile/bin/zigurui --output .local/benchmarks/relevance-smoke-NEW
```

## 適用範囲と残る判断

説明JSON、任意field設定、coverage、unordered/ordered最小区間を明示利用に限定する。既定path・heading・候補集合・原文位置・保存形式を維持し、ランキング導入だけの再構築は要求しない。次のfield設定はconfigだけで追加でき、新しい採点信号はrelevanceの採点・結果型と契約/oracleへ局所化する。

実KBでの有用性、Linux上の機能別実行、負荷を統制した精密性能、長時間運用、全候補/文書合計の仕事量制限は未検証。重みの調整や既定順位の変更、候補集合拡大はこの結果だけから決めない。既存kb向け評価セットの偏りも、この合成評価で解消されたとは扱わない。
