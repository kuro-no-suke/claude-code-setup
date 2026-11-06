#!/usr/bin/env python3
"""TDnet の適時開示から TOB・MBO・再編の「予兆サイン」を拾い、日次レポートを作る。

使い方:
  python3 tdnet_watch.py                 # 今日（日本時間）の開示
  python3 tdnet_watch.py 20261106        # 日付を指定
  python3 tdnet_watch.py 20261001 20261106  # 期間をまとめて処理（TDnet は約1か月分のみ公開）

出力:
  signals.csv            サインの累積ログ（同じ開示は重複登録しない）
  reports/YYYY-MM-DD.md  日次レポート（ウォッチリストのヒット、TOB発表、スコア上位）
"""
import csv
import datetime as dt
import html
import os
import re
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
WATCHLIST = os.path.join(BASE, "watchlist.tsv")
SIGNALS = os.path.join(BASE, "signals.csv")
REPORTS = os.path.join(BASE, "reports")
JST = dt.timezone(dt.timedelta(hours=9))
SCORE_WINDOW_DAYS = 90
HALF_LIFE_DAYS = 30  # この日数がたったサインは点数が半分になる

# (種類, 点数, 正規表現)。上から順に判定し、1つの開示に複数の種類が付くことがある。
# 点数は「TOB・非上場化の予兆としての強さ」。0 はイベントそのもの・参考情報。
RULES = [
    ("TOB・MBO発表", 0, r"公開買付け?の開始|公開買付けに関する賛同|応募(の)?推奨|ＭＢＯ|MBO|マネジメント・バイアウト"),
    ("TOB結果", 0, r"公開買付けの結果"),
    ("完全子会社化・統合", 0, r"完全子会社化|株式交換|経営統合|株式移転|吸収合併"),
    ("特別委員会", 4, r"特別委員会"),
    ("親会社・主要株主の異動", 3, r"親会社.{0,6}異動|主要株主.{0,8}異動|その他の関係会社.{0,4}異動"),
    ("株主優待の廃止", 3, r"株主優待.{0,10}(廃止|終了)"),
    ("配当の未定化・無配", 3, r"無配|配当予想.{0,10}未定"),
    ("中計の取り下げ・延期", 3, r"中期経営計画.{0,12}(取り下げ|取下げ|撤回|延期|見直し|取りやめ)"),
    ("上場維持基準", 3, r"上場維持基準"),
    # 株式併合・上場廃止は TOB 後の手続きであることが多いので、予兆ではなく発表済みとして扱う
    ("非上場化の手続き", 0, r"上場廃止|株式併合|株式等売渡請求"),
    ("資本業務提携", 2, r"資本業務提携|資本提携"),
    ("業績の下方修正", 1, r"下方修正"),
    ("自己株式の公開買付", 1, r"自己株式の公開買付"),
    ("業績の上方修正", 0, r"上方修正"),
    ("増配", 0, r"増配"),
    ("リスク", 0, r"取立不能|取立遅延|債務超過|継続企業の前提|不適切な会計|特別調査委員会"),
]
RULES = [(n, p, re.compile(r)) for n, p, r in RULES]


def fetch_day(date):
    """指定日（YYYYMMDD）の開示一覧を返す。[(時刻, コード4桁, 社名, 表題, PDFのURL)]"""
    out = []
    for page in range(1, 40):
        url = f"https://www.release.tdnet.info/inbs/I_list_{page:03d}_{date}.html"
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                s = r.read().decode("utf-8", "ignore")
        except Exception:
            break
        got = 0
        for row in re.findall(r"<tr>(.*?)</tr>", s, re.S):
            tds = [html.unescape(re.sub(r"<[^>]+>", "", t)).strip()
                   for t in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if len(tds) >= 4 and re.match(r"\d\d:\d\d", tds[0]):
                got += 1
                m = re.search(r'href="([^"]+\.pdf)"', row)
                pdf = "https://www.release.tdnet.info/inbs/" + m.group(1) if m else ""
                out.append((tds[0], tds[1][:4], tds[2], re.sub(r"\s+", " ", tds[3]), pdf))
        if got == 0:
            break
        time.sleep(0.3)
    return out


def load_watchlist():
    wl = {}
    with open(WATCHLIST, encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            wl[r["code"]] = r
    return wl


def load_signals():
    if not os.path.exists(SIGNALS):
        return []
    with open(SIGNALS, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_signals(rows):
    fields = ["date", "time", "code", "name", "signal", "points", "title", "pdf"]
    with open(SIGNALS, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["date"], r["time"], r["code"], r["signal"])))


def classify(title):
    return [(n, p) for n, p, rx in RULES if rx.search(title)]


def process_day(date, wl, signals):
    seen = {(r["date"], r["code"], r["title"], r["signal"]) for r in signals}
    iso = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    items = fetch_day(date)
    new = []
    for t, code, name, title, pdf in items:
        for sig, pts in classify(title):
            key = (iso, code, title, sig)
            if key in seen:
                continue
            seen.add(key)
            row = dict(date=iso, time=t, code=code, name=name, signal=sig,
                       points=str(pts), title=title, pdf=pdf)
            signals.append(row)
            new.append(row)
    return iso, items, new


def scores(signals, wl, today):
    """ウォッチリスト銘柄の予兆スコア。TOB・MBOがすでに発表された銘柄は除く。"""
    since = (today - dt.timedelta(days=SCORE_WINDOW_DAYS)).isoformat()
    done = announced(signals, since)
    # 1) 1件の開示（同じ日・銘柄・表題）に複数のサインが付いたら、一番高い点数だけを採る
    per_doc = {}
    for r in signals:
        if r["date"] >= since and r["code"] in wl and r["code"] not in done and int(r["points"]) > 0:
            k = (r["code"], r["date"], r["title"])
            if k not in per_doc or int(r["points"]) > int(per_doc[k]["points"]):
                per_doc[k] = r
    # 2) 同じ日・同じ銘柄・同じ種類のサインは1回だけ数える
    per_day = {}
    for r in per_doc.values():
        per_day.setdefault((r["code"], r["date"], r["signal"]), r)
    # 3) 古いサインほど軽くする（HALF_LIFE_DAYS で半分）
    sc = {}
    for r in sorted(per_day.values(), key=lambda r: r["date"]):
        age = (today - dt.date.fromisoformat(r["date"])).days
        s = sc.setdefault(r["code"], {"score": 0.0, "signals": []})
        s["score"] += int(r["points"]) * 0.5 ** (max(age, 0) / HALF_LIFE_DAYS)
        s["signals"].append(f'{r["date"][5:]} {r["signal"]}')
    for s in sc.values():
        s["score"] = round(s["score"], 1)
    return sorted(sc.items(), key=lambda kv: -kv[1]["score"])


def announced(signals, since):
    """期間内に TOB・MBO の発表または結果が出た銘柄 {code: 最初の日付}。"""
    out = {}
    for r in signals:
        if r["date"] >= since and r["signal"] in ("TOB・MBO発表", "TOB結果", "非上場化の手続き"):
            out.setdefault(r["code"], r["date"])
    return out


def fmt(r, wl):
    w = wl.get(r["code"])
    tag = ""
    if w:
        tag = f' **[{w["group"]}'
        if w.get("holder"):
            tag += f' / {w["holder"][:20]} {w["holder_pct"]}%'
        if w.get("pbr"):
            tag += f' / PBR {w["pbr"]}'
        tag += "]**"
    link = f' ([PDF]({r["pdf"]}))' if r["pdf"] else ""
    return f'- {r["time"]} {r["code"]} {r["name"]}{tag}: {r["title"]}{link}'


def write_report(iso, items, new, signals, wl):
    today = dt.date.fromisoformat(iso)
    by = lambda pred: [r for r in new if pred(r)]
    hits = by(lambda r: r["code"] in wl)
    events = by(lambda r: r["signal"] in ("TOB・MBO発表", "TOB結果", "完全子会社化・統合", "非上場化の手続き"))
    omens = by(lambda r: int(r["points"]) > 0 and r["code"] not in wl)
    risks = by(lambda r: r["signal"] == "リスク")
    lines = [f"# TOB予兆ウォッチ {iso}", "",
             f"TDnet開示 {len(items)} 件 → サイン {len(new)} 件"
             f"（ウォッチリスト該当 {len({r['code'] for r in hits})} 社）", ""]

    def section(title, rows, empty="なし"):
        lines.append(f"## {title}")
        if rows:
            done = set()
            for r in rows:
                k = (r["code"], r["title"])
                if k in done:
                    continue
                done.add(k)
                sigs = sorted({x["signal"] for x in rows if (x["code"], x["title"]) == k})
                lines.append(fmt(r, wl) + f'  `{" / ".join(sigs)}`')
        else:
            lines.append(empty)
        lines.append("")

    section("ウォッチリスト銘柄の開示", hits)
    section("TOB・MBO・完全子会社化（市場全体）", events)
    section("予兆サイン（ウォッチリスト外）", omens)
    section("リスク開示", risks)

    lines.append(f"## 予兆スコア上位（ウォッチリスト、直近{SCORE_WINDOW_DAYS}日）")
    top = scores(signals, wl, today)[:20]
    if top:
        lines.append("| コード | 銘柄 | 区分 | スコア | サイン |")
        lines.append("|---|---|---|---|---|")
        for code, s in top:
            w = wl[code]
            lines.append(f'| {code} | {w["name"]} | {w["group"]} | {s["score"]} | {"、".join(s["signals"][-4:])} |')
    else:
        lines.append("なし")
    lines.append("")
    since = (today - dt.timedelta(days=SCORE_WINDOW_DAYS)).isoformat()
    hit = sorted((d, c) for c, d in announced(signals, since).items() if c in wl)
    lines.append(f"## ウォッチリストでTOB・MBO・非上場化の手続きが出た銘柄（直近{SCORE_WINDOW_DAYS}日、スコア対象外）")
    lines.extend([f'- {d} {c} {wl[c]["name"]}（{wl[c]["group"]}）' for d, c in hit] or ["なし"])
    lines.append("")
    lines.append("※公表情報の機械的な分類です。投資判断はご自身で行ってください。")
    os.makedirs(REPORTS, exist_ok=True)
    path = os.path.join(REPORTS, f"{iso}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def daterange(a, b):
    d = dt.date(int(a[:4]), int(a[4:6]), int(a[6:]))
    e = dt.date(int(b[:4]), int(b[4:6]), int(b[6:]))
    while d <= e:
        yield d.strftime("%Y%m%d")
        d += dt.timedelta(days=1)


def main():
    args = sys.argv[1:]
    today = dt.datetime.now(JST).strftime("%Y%m%d")
    dates = [today] if not args else (list(daterange(args[0], args[1])) if len(args) > 1 else [args[0]])
    wl = load_watchlist()
    signals = load_signals()
    for d in dates:
        iso, items, new = process_day(d, wl, signals)
        if not items:
            print(f"{iso}: 開示なし（休日または公開期間外）")
            continue
        path = write_report(iso, items, new, signals, wl)
        print(f"{iso}: 開示 {len(items)} 件、新しいサイン {len(new)} 件 → {os.path.relpath(path, BASE)}")
    save_signals(signals)


if __name__ == "__main__":
    main()
