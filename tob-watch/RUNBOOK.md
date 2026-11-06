# 毎日の自動実行手順（スケジュール実行のセッション向け）

平日の夜に自動で起動するセッションは、この手順どおりに作業する。

## 0. 準備
```bash
git fetch origin claude/kind-euler-959ht1
git checkout claude/kind-euler-959ht1
git pull origin claude/kind-euler-959ht1
cd tob-watch
```

## 1. TDnet の取り込み（回数制限なし）
```bash
python3 -I tdnet_watch.py
```
- `reports/YYYY-MM-DD.md` と `signals.csv` が更新される。
- 「開示なし」と出たら休日なので、3 以降は飛ばしてよい（2 は実行する）。
- TDnet に接続できない（403 など）ときは、そのことをレポート冒頭に書いて続ける。

## 2. EDINET DB（1日100回の上限。合計60回以内に収める）
### 2-1. 大量保有報告とアクティビスト（2〜3回）
`get_events` を `event_type=large_holding_report,large_holding_change,activist_filing`、
`since=前営業日`、`until=今日`、`limit=200` で呼ぶ。
結果のうち `watchlist.tsv` の `code` か `edinet_code` に一致するものを、
当日のレポート末尾に「## 大量保有・アクティビスト（EDINET）」として追記する
（提出者、保有割合、前回からの増減、保有目的）。
保有目的に「重要提案行為」「経営参加」などが含まれるものは先頭に置く。
一致がなくても、保有割合が10%を超える新規の大量保有報告は「ウォッチリスト外」として最大10件書く。

### 2-2. 親子関係の確認（最大50回）
`edinet_queue.txt` の先頭から50件まで、`get_parent_companies` を呼ぶ。判定基準:
- `parent`: relationType が「親会社」、他社の有報で「連結子会社」と記載、または法人の議決権が40%以上
- `affiliate`: 「その他の関係会社」などで議決権20%以上

一致したら `watchlist.tsv` の該当行の `group`、`holder`、`holder_pct` を更新する。
確認が終わったコードは、一致の有無にかかわらず `edinet_queue.txt` から消す。
上限エラーが出たらその時点で止め、確認済みの分だけ反映する。
新しく見つかった parent / affiliate は、レポートに「## 新しく見つかった親子・関係会社」として追記する。

## 3. 保存
```bash
git add -A tob-watch
git commit -m "TOB予兆ウォッチ: YYYY-MM-DD の日次更新"
git push -u origin claude/kind-euler-959ht1
```
push がネットワークエラーで失敗したら、2秒・4秒・8秒・16秒の間隔で最大4回やり直す。

## 4. 最後の返答（通知に使われる）
次の内容を5〜10行の日本語でまとめる:
- ウォッチリスト銘柄の開示（特に TOB・MBO の発表と、点数のあるサイン）
- 予兆スコア上位3銘柄
- 大量保有・アクティビストの注目点
- 新しく見つかった親子・関係会社の件数と、`edinet_queue.txt` の残り件数

特定の銘柄の売買を勧める書き方はしない。
