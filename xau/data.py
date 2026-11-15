"""Data layer: public market-data endpoints (no API key needed).

Primary candles : Dukascopy "freeserv" chart feed (spot XAU/USD, BID side)
Cross-checks    : Swissquote public quote, gold-api.com, TradingView scanner
Macro calendar  : ForexFactory weekly JSON feed

All timestamps are returned as tz-aware UTC pandas Timestamps.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from typing import Optional

import pandas as pd
import requests

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
DUKA_URL = "https://freeserv.dukascopy.com/2.0/index.php"
DUKA_HEADERS = {"User-Agent": UA,
                "Referer": "https://freeserv.dukascopy.com/2.0/?path=chart/index"}
INTERVAL_MIN = {"1MIN": 1, "5MIN": 5, "15MIN": 15, "1HOUR": 60, "4HOUR": 240, "1DAY": 1440}


class DataError(RuntimeError):
    pass


def _get(url, *, params=None, headers=None, timeout=25, retries=3, method="GET", data=None):
    last = None
    for i in range(retries):
        try:
            r = requests.request(method, url, params=params, headers=headers or {"User-Agent": UA},
                                 timeout=timeout, data=data)
            if r.status_code == 200 and r.text:
                return r
            last = f"HTTP {r.status_code}"
        except Exception as e:  # network error -> retry
            last = repr(e)
        time.sleep(0.8 * (i + 1))
    raise DataError(f"{url}: {last}")


def _to_ms(t) -> int:
    if t is None:
        return int(time.time() * 1000)
    if isinstance(t, (int, float)):
        return int(t)
    ts = pd.Timestamp(t)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return int(ts.timestamp() * 1000)


def duka_candles(instrument: str = "XAU/USD", interval: str = "1HOUR", bars: int = 5000,
                 end=None, side: str = "B", max_pages: int = 12) -> pd.DataFrame:
    """Download the last `bars` candles ending before `end` (UTC). Paginates backwards."""
    end_ms = _to_ms(end)
    rows: dict[int, list] = {}
    cursor = end_ms
    for _ in range(max_pages):
        need = bars - len(rows)
        if need <= 0:
            break
        params = {"path": "chart/json3", "instrument": instrument, "offer_side": side,
                  "interval": interval, "splits": "true", "stocks": "true",
                  "limit": min(max(need, 10), 5000), "time_direction": "P",
                  "timestamp": cursor, "jsonp": "cb"}
        r = _get(DUKA_URL, params=params, headers=DUKA_HEADERS)
        m = re.match(r"^\s*cb\((.*)\)\s*;?\s*$", r.text, re.S)
        if not m:
            raise DataError(f"Dukascopy: risposta inattesa ({r.text[:80]!r})")
        chunk = json.loads(m.group(1))
        if not chunk:
            break
        before = len(rows)
        for row in chunk:
            rows[int(row[0])] = row
        earliest = min(int(row[0]) for row in chunk)
        if len(rows) == before or earliest >= cursor:
            break
        cursor = earliest
    if not rows:
        raise DataError(f"Dukascopy: nessun dato per {instrument} {interval}")
    df = pd.DataFrame(sorted(rows.values()), columns=["ms", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["ms"], unit="ms", utc=True)
    df = df.drop(columns="ms")[["time", "open", "high", "low", "close", "volume"]]
    df = df[df["time"] < pd.Timestamp(end_ms, unit="ms", tz="UTC")]
    return df.tail(bars).reset_index(drop=True)


def swissquote_quote(pair: str = "XAU/USD") -> Optional[dict]:
    try:
        r = _get(f"https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/{pair}",
                 retries=2, timeout=12)
        js = r.json()
        best = None
        for venue in js:
            for p in venue.get("spreadProfilePrices", []):
                if p.get("spreadProfile") in ("prime", "premium", "standard"):
                    best = {"bid": float(p["bid"]), "ask": float(p["ask"]),
                            "time": pd.Timestamp(venue["ts"], unit="ms", tz="UTC"),
                            "source": "Swissquote"}
                    break
            if best:
                break
        return best
    except Exception:
        return None


def goldapi_quote() -> Optional[dict]:
    try:
        js = _get("https://api.gold-api.com/price/XAU", retries=2, timeout=12).json()
        return {"price": float(js["price"]), "time": pd.Timestamp(js["updatedAt"]),
                "source": "gold-api.com"}
    except Exception:
        return None


TV_COLUMNS = ["close", "RSI", "SMA20", "SMA50", "SMA200", "ATR", "ADX",
              "RSI|240", "EMA20|240", "EMA50|240",
              "RSI|60", "EMA20|60", "EMA50|60", "EMA200|60",
              "RSI|15", "EMA20|15", "EMA50|15"]


def tradingview_snapshot(tickers=("OANDA:XAUUSD",), columns=TV_COLUMNS) -> dict:
    """Indicator values computed by TradingView - used only as an independent cross-check."""
    try:
        payload = json.dumps({"symbols": {"tickers": list(tickers), "query": {"types": []}},
                              "columns": list(columns)})
        r = _get("https://scanner.tradingview.com/global/scan", method="POST", data=payload,
                 headers={"User-Agent": UA, "Content-Type": "application/json"}, retries=2, timeout=15)
        out = {}
        for item in r.json().get("data", []):
            out[item["s"]] = dict(zip(columns, item["d"]))
        return out
    except Exception:
        return {}


def ff_calendar() -> list[dict]:
    """ForexFactory weekly calendar (this week). Returns [] if unavailable."""
    try:
        r = _get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", retries=1, timeout=15)   # rate-limited feed: no retry storm
        out = []
        for e in r.json():
            try:
                t = pd.Timestamp(dt.datetime.fromisoformat(e["date"])).tz_convert("UTC")
            except Exception:
                continue
            out.append({"time": t, "currency": e.get("country", ""), "title": e.get("title", ""),
                        "impact": e.get("impact", ""), "forecast": e.get("forecast", "") or "",
                        "previous": e.get("previous", "") or ""})
        return sorted(out, key=lambda x: x["time"])
    except Exception:
        return []
