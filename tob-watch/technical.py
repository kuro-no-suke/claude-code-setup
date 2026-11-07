#!/usr/bin/env python3
"""日足の株価からテクニカル指標を計算する。

使い方（単体）:
  python3 technical.py 5951 2489      # 銘柄コードを並べる

データ元: Yahoo Finance のチャートAPI（query1.finance.yahoo.com、約1年分の日足）
"""
import datetime as dt
import json
import sys
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (tob-watch)"}


def fetch_daily(code, rng="1y"):
    """[(date, open, high, low, close, volume)] を古い順で返す。取れなければ []。"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}.T?range={rng}&interval=1d"
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.load(r)
        res = d["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        out = []
        for i, ts in enumerate(res.get("timestamp") or []):
            c = q["close"][i]
            if c is None:
                continue
            day = dt.datetime.fromtimestamp(ts, dt.timezone(dt.timedelta(hours=9))).date()
            out.append((day, q["open"][i], q["high"][i], q["low"][i], c, q["volume"][i] or 0))
        return out
    except Exception:
        return []


def sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def ema_series(xs, n):
    k = 2 / (n + 1)
    out, e = [], None
    for x in xs:
        e = x if e is None else x * k + e * (1 - k)
        out.append(e)
    return out


def rsi(closes, n=14):
    if len(closes) <= n:
        return None
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        ag, al = (ag * (n - 1) + g) / n, (al * (n - 1) + l) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def pct(a, b):
    return (a / b - 1) * 100 if a is not None and b else None


def analyze(rows):
    """テクニカル指標と判定を dict で返す。データ不足なら None。"""
    if len(rows) < 30:
        return None
    closes = [r[4] for r in rows]
    vols = [r[5] for r in rows]
    highs = [r[2] or r[4] for r in rows]
    lows = [r[3] or r[4] for r in rows]
    c = closes[-1]
    ma25, ma75, ma200 = sma(closes, 25), sma(closes, 75), sma(closes, 200)
    ma25_prev = sum(closes[-30:-5]) / 25 if len(closes) >= 30 else None
    r14 = rsi(closes)
    hi52, lo52 = max(highs[-245:]), min(lows[-245:])
    v5, v25 = sma(vols, 5), sma(vols, 25)
    macd = [a - b for a, b in zip(ema_series(closes, 12), ema_series(closes, 26))]
    sig = ema_series(macd, 9)
    hist = [a - b for a, b in zip(macd, sig)]
    macd_cross = None
    for i in range(1, min(6, len(hist))):
        if hist[-i] > 0 >= hist[-i - 1]:
            macd_cross = "MACD好転"
            break
        if hist[-i] < 0 <= hist[-i - 1]:
            macd_cross = "MACD悪化"
            break
    gc = None
    if ma75 and len(closes) >= 85:
        for i in range(1, 11):
            a_now, b_now = sum(closes[-25 - i + 1:len(closes) - i + 1]) / 25, sum(closes[-75 - i + 1:len(closes) - i + 1]) / 75
            a_pre, b_pre = sum(closes[-25 - i:len(closes) - i]) / 25, sum(closes[-75 - i:len(closes) - i]) / 75
            if a_now > b_now and a_pre <= b_pre:
                gc = "ゴールデンクロス"
                break
            if a_now < b_now and a_pre >= b_pre:
                gc = "デッドクロス"
                break

    up = ma25_prev is not None and ma25 > ma25_prev
    if ma75 and c > ma25 > ma75 and up:
        trend = "上昇"
    elif ma75 and c < ma25 < ma75 and not up:
        trend = "下降"
    else:
        trend = "もみ合い"

    dev25 = pct(c, ma25)
    vol_ratio = v5 / v25 if v25 else None
    flags = []
    if (r14 is not None and r14 >= 75) or (dev25 is not None and dev25 >= 20):
        flags.append("過熱")
    if (r14 is not None and r14 <= 30) or (dev25 is not None and dev25 <= -15):
        flags.append("売られすぎ")
    if trend == "上昇" and r14 is not None and 40 <= r14 <= 58 and dev25 is not None and abs(dev25) <= 4:
        flags.append("押し目")
    if c >= hi52 * 0.99 and (vol_ratio or 0) >= 1.5:
        flags.append("高値ブレイク")
    elif c >= hi52 * 0.97:
        flags.append("高値圏")
    if (vol_ratio or 0) >= 2:
        flags.append("出来高急増")
    for x in (gc, macd_cross):
        if x:
            flags.append(x)

    return {
        "date": rows[-1][0].isoformat(),
        "close": c,
        "chg1d": pct(c, closes[-2]),
        "chg1m": pct(c, closes[-22]) if len(closes) >= 22 else None,
        "chg3m": pct(c, closes[-64]) if len(closes) >= 64 else None,
        "ma25": ma25, "ma75": ma75, "ma200": ma200,
        "dev25": dev25,
        "rsi": r14,
        "hi52": hi52, "lo52": lo52,
        "pos52": (c - lo52) / (hi52 - lo52) * 100 if hi52 > lo52 else None,
        "vol_ratio": vol_ratio,
        "trend": trend,
        "flags": flags,
    }


def tech_points(t):
    """上昇候補の点数に使うテクニカル側の点数。"""
    if not t:
        return 0
    p = {"上昇": 1, "もみ合い": 0, "下降": -1}[t["trend"]]
    p += sum({"押し目": 1, "高値ブレイク": 1, "ゴールデンクロス": 1, "MACD好転": 0.5,
              "過熱": -2, "デッドクロス": -1, "MACD悪化": -0.5}.get(f, 0) for f in t["flags"])
    return p


def fmt_num(x, f="{:.0f}"):
    return "-" if x is None else f.format(x)


def summary_cells(t):
    """レポートの表に入れるセル: 終値 | 1か月 | 3か月 | トレンド | RSI | 25日線乖離 | 52週位置 | 出来高 | サイン"""
    if not t:
        return ["-"] * 9
    return [fmt_num(t["close"], "{:,.0f}"), fmt_num(t["chg1m"], "{:+.0f}%"), fmt_num(t["chg3m"], "{:+.0f}%"),
            t["trend"], fmt_num(t["rsi"]), fmt_num(t["dev25"], "{:+.1f}%"), fmt_num(t["pos52"], "{:.0f}%"),
            fmt_num(t["vol_ratio"], "{:.1f}倍"), "、".join(t["flags"]) or "-"]


TABLE_HEAD = ("| 終値 | 1か月 | 3か月 | トレンド | RSI | 25日線乖離 | 52週位置 | 出来高(5日/25日) | サイン |",
              "|---|---|---|---|---|---|---|---|---|")


class Cache:
    """同じ実行中に同じ銘柄を何度も取りに行かないためのキャッシュ。"""

    def __init__(self, delay=0.4):
        self.d, self.delay = {}, delay

    def get(self, code):
        if code not in self.d:
            self.d[code] = analyze(fetch_daily(code))
            time.sleep(self.delay)
        return self.d[code]


if __name__ == "__main__":
    for code in sys.argv[1:]:
        t = analyze(fetch_daily(code))
        if not t:
            print(code, "データなし")
            continue
        print(code, t["date"], "| " + " | ".join(summary_cells(t)) + " |", "点数", tech_points(t))
