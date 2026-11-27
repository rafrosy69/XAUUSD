"""Analysis engine: trading-day aggregation, levels, confluence zones, bias, scenarios,
mechanical evaluation and descriptive statistics.

Conventions
-----------
* Trading day = New York close convention (18:00 -> 17:00 America/New_York), the same used
  by most FX/CFD platforms for the daily candle of spot gold. DST is handled via zoneinfo.
* The engine never uses data after `asof` (no look-ahead): it can be replayed on past dates.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import ta

NY, ROME, LON = "America/New_York", "Europe/Rome", "Europe/London"
RULES_VERSION = 6      # v6: a breakout whose confirming close is already beyond T1 is cancelled, not traded
H = pd.Timedelta(hours=1)

CFG = {
    "w_tf": {"D1": 0.25, "H4": 0.30, "H1": 0.30, "M15": 0.15},   # timeframe weights (intraday)
    "bias_strong": 0.60, "bias_mild": 0.25,
    "window_atr": 1.6,          # ladder shows levels within +/- 1.6 daily ATR
    "tol_atr": 0.07,            # clustering tolerance (fraction of daily ATR)
    "tol_min": 3.0, "tol_max": 9.0,
    "zone_min_score": 3.5,      # minimum confluence to qualify as an entry zone
    "min_dist_atr": 0.12,       # pullback zone must be at least this far from price
    "max_dist_atr": 1.10,
    "buf_atr_d": 0.15,          # stop buffer beyond the zone = 15% of the daily ATR (about one hourly ATR)
    "minw_atr_d": 0.05,         # minimum width of an entry zone
    "brk_fill_atr_d": 0.025,    # typical slippage beyond the level on a confirmed M15 close
    "entry_deadline_rome": 21,  # no new intraday entries after 21:00 Rome time
}


# ----------------------------------------------------------------------------- time helpers
def tday_of(ts):
    """Trading date (naive midnight Timestamp) of a UTC timestamp / Series."""
    if isinstance(ts, pd.Timestamp):
        return (ts.tz_convert(NY).tz_localize(None) + 7 * H).normalize()
    return (ts.dt.tz_convert(NY).dt.tz_localize(None) + 7 * H).dt.normalize()


def next_weekday(d: pd.Timestamp) -> pd.Timestamp:
    while d.day_of_week >= 5:
        d = d + pd.Timedelta(days=1)
    return d


def session_bounds(tday: pd.Timestamp) -> dict:
    """UTC boundaries of the trading day `tday` and of its three sessions."""
    p = tday - pd.Timedelta(days=1)
    mk = lambda d, h, tz: pd.Timestamp(year=d.year, month=d.month, day=d.day, hour=h, tz=tz).tz_convert("UTC")
    return {"open": mk(p, 18, NY), "london": mk(tday, 8, LON), "ny": mk(tday, 8, NY),
            "close": mk(tday, 17, NY)}


def resample(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    key = df["time"].dt.floor(f"{minutes}min")
    out = (df.groupby(key)
             .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                  close=("close", "last"), volume=("volume", "sum"))
             .reset_index())
    return out


def to_h4(h1: pd.DataFrame) -> pd.DataFrame:
    loc = h1["time"].dt.tz_convert(NY).dt.tz_localize(None) + 7 * H
    out = (h1.groupby(loc.dt.floor("4h"))
             .agg(time=("time", "first"), open=("open", "first"), high=("high", "max"),
                  low=("low", "min"), close=("close", "last"))
             .reset_index(drop=True))
    return out


def to_daily(h1: pd.DataFrame) -> pd.DataFrame:
    out = (h1.assign(tday=tday_of(h1["time"])).groupby("tday")
             .agg(time=("time", "first"), open=("open", "first"), high=("high", "max"),
                  low=("low", "min"), close=("close", "last"), n=("close", "size"))
             .reset_index())
    return out[out["tday"].dt.day_of_week < 5].reset_index(drop=True)


def _sig(x, eps=0.0):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return 0
    return 1 if x > eps else (-1 if x < -eps else 0)


def _rsi_sig(r):
    if r is None or math.isnan(r):
        return 0
    return 1 if r > 55 else (-1 if r < 45 else 0)


def _last(s: pd.Series):
    s = s.dropna()
    return float(s.iloc[-1]) if len(s) else float("nan")


# ----------------------------------------------------------------------------- preparation
def prepare(h1_raw: pd.DataFrame, m5_raw: pd.DataFrame, asof: pd.Timestamp) -> dict:
    """Cut every series at `asof` and rebuild the bars in progress from 5-minute data."""
    m5 = m5_raw[m5_raw["time"] < asof].reset_index(drop=True)
    h1c = h1_raw[h1_raw["time"] + H <= asof].reset_index(drop=True)          # completed hours
    if len(m5) == 0 or len(h1c) < 300:
        raise ValueError("Dati insufficienti per l'analisi")
    tail = m5[m5["time"] >= h1c["time"].iloc[-1] + H]
    h1 = h1c
    if len(tail):
        part = resample(tail, 60)
        h1 = pd.concat([h1c, part[["time", "open", "high", "low", "close", "volume"]]], ignore_index=True)
    m15 = resample(m5, 15)
    return {"h1": h1, "h1c": h1c, "m5": m5, "m15": m15, "price": float(m5["close"].iloc[-1]),
            "asof": asof, "last_bar": m5["time"].iloc[-1]}


# ----------------------------------------------------------------------------- levels
def pivots_classic(h, l, c) -> dict:
    p = (h + l + c) / 3.0
    out = {"P": p, "R1": 2 * p - l, "S1": 2 * p - h, "R2": p + (h - l), "S2": p - (h - l),
           "R3": h + 2 * (p - l), "S3": l - 2 * (h - p)}
    return {k: float(v) for k, v in out.items()}


def _fmt_day(ts: pd.Timestamp) -> str:
    return ts.strftime("%d/%m")


def collect_levels(P: dict, d1: pd.DataFrame, h4: pd.DataFrame, ind: dict, cfg=CFG) -> list[dict]:
    """All reference prices that intraday traders watch, each with a weight."""
    price, asof = P["price"], P["asof"]
    h1, m5 = P["h1"], P["m5"]
    today = P["tday"]
    dcomp = d1[d1["tday"] < today]
    prev = dcomp.iloc[-1]
    L: list[dict] = []

    def add(price_, name, w, fam, dyn=False, fast=False):
        """dyn  = moving level: it can add weight to a zone but never defines its edges;
        fast = moves within the session (hourly averages, today's extremes, ADR projections):
               shown on the map, ignored by the scenario rules so that the plan does not flip
               from one refresh to the next."""
        if price_ is None or not np.isfinite(price_):
            return
        L.append({"price": float(price_), "name": name, "w": float(w), "fam": fam, "dyn": dyn or fast, "fast": fast})

    # previous day
    add(prev["high"], "Massimo di ieri (PDH)", 3.0, "pd")
    add(prev["low"], "Minimo di ieri (PDL)", 3.0, "pd")
    add(prev["close"], "Chiusura di ieri", 2.0, "pd")
    # pivots
    for k, v in P["pivots"].items():
        w = {"P": 2.5, "R1": 2.0, "S1": 2.0, "R2": 1.5, "S2": 1.5, "R3": 1.0, "S3": 1.0}[k]
        add(v, "Pivot P" if k == "P" else f"Pivot {k}", w, "pivot")
    # today (developing)
    td = P["today"]
    if td:
        add(td["open"], "Apertura di oggi", 1.5, "today")
        add(td["high"], "Massimo di oggi", 1.5, "today", fast=True)
        add(td["low"], "Minimo di oggi", 1.5, "today", fast=True)
        if td.get("asia_done"):
            add(td["asia_high"], "Max sessione Asia", 2.0, "asia")
            add(td["asia_low"], "Min sessione Asia", 2.0, "asia")
    # weekly
    wk = dcomp.assign(wk=dcomp["tday"] - pd.to_timedelta(dcomp["tday"].dt.day_of_week, unit="D"))
    this_wk = today - pd.Timedelta(days=int(today.day_of_week))
    pw = wk[wk["wk"] < this_wk]
    if len(pw):
        lastwk = pw[pw["wk"] == pw["wk"].iloc[-1]]
        add(lastwk["high"].max(), "Max sett. scorsa (PWH)", 3.0, "week")
        add(lastwk["low"].min(), "Min sett. scorsa (PWL)", 3.0, "week")
        add(lastwk["close"].iloc[-1], "Chiusura sett. scorsa", 1.5, "week")
    cw = wk[wk["wk"] == this_wk]
    if len(cw):
        add(cw["high"].max(), "Max settimana in corso", 2.0, "week")
        add(cw["low"].min(), "Min settimana in corso", 2.0, "week")
        add(cw["open"].iloc[0], "Apertura settimana", 1.0, "week")
    # monthly
    mon = dcomp.assign(m=dcomp["tday"].dt.to_period("M"))
    this_m = today.to_period("M")
    pm = mon[mon["m"] < this_m]
    if len(pm):
        lastm = pm[pm["m"] == pm["m"].iloc[-1]]
        add(lastm["high"].max(), "Max mese scorso", 2.5, "month")
        add(lastm["low"].min(), "Min mese scorso", 2.5, "month")
    cm = mon[mon["m"] == this_m]
    if len(cm):
        add(cm["open"].iloc[0], "Apertura mese", 1.5, "month")
    # round numbers
    atr_d = ind["atr_d"]
    lo, hi = price - cfg["window_atr"] * atr_d, price + cfg["window_atr"] * atr_d
    k = math.floor(lo / 50.0)
    while k * 50 <= hi:
        v = k * 50
        if v >= lo:
            if v % 100 == 0:
                add(v, f"Soglia psicologica {v:.0f}", 2.5, "round")
            else:
                add(v, f"Cifra tonda {v:.0f}", 1.5, "round")
        k += 1
    # moving averages (dynamic)
    add(ind["H1"]["ema20"], "EMA20 H1", 1.0, "ma", fast=True)
    add(ind["H1"]["ema50"], "EMA50 H1", 1.5, "ma", fast=True)
    add(ind["H1"]["ema200"], "EMA200 H1", 1.5, "ma", fast=True)
    add(ind["H4"]["ema50"], "EMA50 H4", 1.5, "ma", True)
    add(ind["D1"]["sma20"], "SMA20 D1", 2.0, "ma", True)
    add(ind["D1"]["sma50"], "SMA50 D1", 2.0, "ma", True)
    add(ind["D1"]["sma200"], "SMA200 D1", 2.5, "ma", True)
    # swing points (confirmed only)
    seen: list[float] = []

    def add_swings(df, left, right, w, tag, fam, since):
        for s in ta.swing_points(df, left, right):
            if s["time"] < since:
                continue
            if any(abs(s["price"] - x) < 2.0 for x in seen):
                continue
            seen.append(s["price"])
            kind = "Max" if s["type"] == "H" else "Min"
            add(s["price"], f"{kind} {tag} del {_fmt_day(s['time'].tz_convert(ROME))}", w, fam)

    add_swings(dcomp.tail(130).reset_index(drop=True), 2, 2, 2.5, "D1", "swD", asof - pd.Timedelta(days=180))
    add_swings(h4.tail(260).reset_index(drop=True), 3, 3, 2.0, "H4", "swH4", asof - pd.Timedelta(days=45))
    add_swings(P["h1c"].tail(260).reset_index(drop=True), 4, 4, 1.2, "H1", "swH1", asof - pd.Timedelta(days=12))
    # ADR projections (move with today's extremes)
    if td:
        add(td["low"] + ind["adr20"], "Proiezione ADR (max)", 1.5, "adr", fast=True)
        add(td["high"] - ind["adr20"], "Proiezione ADR (min)", 1.5, "adr", fast=True)
    L = [x for x in L if lo <= x["price"] <= hi]
    # merge levels that are the very same price (e.g. yesterday's low = week's low)
    L.sort(key=lambda x: (x["price"], -x["w"]))
    merged: list[dict] = []
    for x in L:
        if merged and abs(x["price"] - merged[-1]["price"]) < 0.05:
            m = merged[-1]
            if x["w"] > m["w"]:
                m["name"], x["name"] = x["name"], m["name"]
                m["fam"] = x["fam"]
            m["dyn"], m["fast"] = m["dyn"] and x["dyn"], m["fast"] and x["fast"]
            m["name"] = f"{m['name']} = {x['name']}"
            m["w"] = max(m["w"], x["w"]) + 0.5
        else:
            merged.append(dict(x))
    return merged


def _rank(m: dict) -> int:
    """Display priority of a level inside a zone (most recognisable first)."""
    fam, name = m["fam"], m["name"]
    if fam == "pd":
        return 0
    if fam == "pivot":
        return 1 if name.split(" = ")[0] in ("Pivot P", "Pivot R1", "Pivot S1") else 5
    if fam == "round":
        return 4 if name.startswith("Soglia") else 8
    return {"week": 2, "month": 3, "asia": 6, "today": 7, "swD": 9, "swH4": 10, "adr": 11,
            "ma": 12, "swH1": 13}.get(fam, 99)


def cluster_levels(levels: list[dict], price: float, atr_d: float, cfg=CFG) -> list[dict]:
    """Merge nearby levels into confluence zones and score them.
    Zone boundaries come from static levels only; moving levels (averages, ADR projections)
    add weight to a zone they fall into but never move its edges, so the map does not jitter
    from one refresh to the next."""
    tol = min(max(cfg["tol_atr"] * atr_d, cfg["tol_min"]), cfg["tol_max"])
    zs: list[dict] = []
    for lv in sorted([x for x in levels if not x["dyn"]], key=lambda x: x["price"]):
        if zs and lv["price"] - zs[-1]["m"][-1]["price"] <= tol and lv["price"] - zs[-1]["lo"] <= 2.2 * tol:
            zs[-1]["m"].append(lv)
            zs[-1]["hi"] = lv["price"]
        else:
            zs.append({"lo": lv["price"], "hi": lv["price"], "m": [lv], "dyn_only": False})
    for lv in sorted([x for x in levels if x["dyn"]], key=lambda x: x["price"]):
        best, bd = None, None
        for z in zs:
            if z["dyn_only"]:
                continue
            dist = 0.0 if z["lo"] <= lv["price"] <= z["hi"] else min(abs(lv["price"] - z["lo"]), abs(lv["price"] - z["hi"]))
            if bd is None or dist < bd:
                best, bd = z, dist
        if best is not None and bd <= 0.5 * tol:
            best["m"].append(lv)
        else:
            zs.append({"lo": lv["price"], "hi": lv["price"], "m": [lv], "dyn_only": True})
    out = []
    for z in zs:
        members, lo, hi = z["m"], z["lo"], z["hi"]
        fam_tot: dict[str, float] = {}
        for m in sorted([m for m in members if not m["fast"]], key=lambda x: -x["w"]):
            # diminishing weight for several levels of the same family inside one zone
            n = sum(1 for k in fam_tot if k.startswith(m["fam"] + "#"))
            fam_tot[f"{m['fam']}#{n}"] = m["w"] * (1.0, 0.5, 0.25, 0.0)[min(n, 3)]
        score = sum(fam_tot.values())           # intraday-moving levels are listed but do not count
        st = [m for m in members if not m["dyn"]] or members
        mid = sum(m["price"] * m["w"] for m in st) / sum(m["w"] for m in st)
        ordered = sorted(members, key=lambda x: (_rank(x), -x["w"]))
        names = [m["name"] for m in ordered]
        label = names[0].split(" = ")[0] + (f" +{len(names) - 1}" if len(names) > 1 else "")
        side = "R" if lo > price else ("S" if hi < price else "X")
        stars = 3 if score >= 6 else (2 if score >= cfg["zone_min_score"] else 1)
        out.append({"lo": lo, "hi": hi, "mid": mid, "score": round(score, 2), "stars": stars,
                    "names": names, "label": label, "side": side, "n": len(members),
                    "members": [{"price": m["price"], "name": m["name"], "dyn": m["dyn"]} for m in
                                sorted(members, key=lambda x: -x["price"])],
                    "dyn_only": z["dyn_only"], "dist": (mid - price)})
    return sorted(out, key=lambda z: -z["mid"])


# ----------------------------------------------------------------------------- indicators & bias
def compute_indicators(P: dict, d1: pd.DataFrame, h4: pd.DataFrame) -> dict:
    today = P["tday"]
    dcomp = d1[d1["tday"] < today]
    h1, h1c, m15 = P["h1"], P["h1c"], P["m15"]
    cD = d1["close"]
    ind = {
        "atr_d": _last(ta.atr(dcomp, 14)),
        "adr5": float((dcomp["high"] - dcomp["low"]).tail(5).mean()),
        "adr20": float((dcomp["high"] - dcomp["low"]).tail(20).mean()),
        "atr_h1": _last(ta.atr(h1c, 14)),
        "atr_m15": _last(ta.atr(m15.iloc[:-1] if len(m15) > 20 else m15, 14)),
        "D1": {"sma20": _last(ta.sma(cD, 20)), "sma50": _last(ta.sma(cD, 50)), "sma200": _last(ta.sma(cD, 200)),
               "sma20_prev5": float(ta.sma(cD, 20).iloc[-6]) if len(cD) > 26 else float("nan"),
               "rsi": _last(ta.rsi(cD, 14)), "adx": _last(ta.adx(dcomp, 14))},
        "H4": {"ema20": _last(ta.ema(h4["close"], 20)), "ema50": _last(ta.ema(h4["close"], 50)),
               "rsi": _last(ta.rsi(h4["close"], 14))},
        "H1": {"ema20": _last(ta.ema(h1["close"], 20)), "ema50": _last(ta.ema(h1["close"], 50)),
               "ema200": _last(ta.ema(h1["close"], 200)), "rsi": _last(ta.rsi(h1["close"], 14)),
               "adx": _last(ta.adx(h1c, 14))},
        "M15": {"ema20": _last(ta.ema(m15["close"], 20)), "ema50": _last(ta.ema(m15["close"], 50)),
                "rsi": _last(ta.rsi(m15["close"], 14))},
    }
    return ind


def h1_structure(P: dict) -> dict:
    sw = ta.swing_points(P["h1c"].tail(160).reset_index(drop=True), 4, 4)
    hs = [s for s in sw if s["type"] == "H"][-2:]
    ls = [s for s in sw if s["type"] == "L"][-2:]
    price = P["price"]
    res = {"sig": 0, "text": "struttura mista", "last_high": hs[-1]["price"] if hs else None,
           "last_low": ls[-1]["price"] if ls else None}
    if len(hs) == 2 and len(ls) == 2:
        lh, ll = hs[1]["price"] < hs[0]["price"], ls[1]["price"] < ls[0]["price"]
        hh, hl = hs[1]["price"] > hs[0]["price"], ls[1]["price"] > ls[0]["price"]
        if price < ls[1]["price"]:
            res.update(sig=-1, text="rottura dell'ultimo minimo H1 (massimi decrescenti)" if lh else "rottura dell'ultimo minimo H1")
        elif price > hs[1]["price"]:
            res.update(sig=1, text="rottura dell'ultimo massimo H1 (minimi crescenti)" if hl else "rottura dell'ultimo massimo H1")
        elif lh and ll:
            res.update(sig=-1, text="massimi e minimi decrescenti")
        elif hh and hl:
            res.update(sig=1, text="massimi e minimi crescenti")
    return res


def compute_bias(P: dict, ind: dict, cfg=CFG) -> dict:
    p = P["price"]
    D, H4, H1, M = ind["D1"], ind["H4"], ind["H1"], ind["M15"]
    st = h1_structure(P)
    parts = {
        "D1": [_sig(p - D["sma20"]), _sig(p - D["sma50"]), _sig(p - D["sma200"]),
               _sig(D["sma20"] - D["sma20_prev5"]), _rsi_sig(D["rsi"])],
        "H4": [_sig(p - H4["ema20"]), _sig(p - H4["ema50"]), _sig(H4["ema20"] - H4["ema50"]), _rsi_sig(H4["rsi"])],
        "H1": [_sig(p - H1["ema20"]), _sig(p - H1["ema50"]), _sig(p - H1["ema200"]),
               _sig(H1["ema20"] - H1["ema50"]), st["sig"], _rsi_sig(H1["rsi"])],
        "M15": [_sig(p - M["ema20"]), _sig(p - M["ema50"]), _sig(M["ema20"] - M["ema50"])],
    }
    tf = {k: float(np.mean(v)) for k, v in parts.items()}
    score = sum(cfg["w_tf"][k] * tf[k] for k in tf)
    if score >= cfg["bias_strong"]:
        label, direction = "Rialzista forte", 1
    elif score >= cfg["bias_mild"]:
        label, direction = "Rialzista", 1
    elif score <= -cfg["bias_strong"]:
        label, direction = "Ribassista forte", -1
    elif score <= -cfg["bias_mild"]:
        label, direction = "Ribassista", -1
    else:
        label, direction = "Neutrale / laterale", 0
    atr_h1 = ind["atr_h1"]
    ext = (p - H1["ema20"]) / atr_h1 if atr_h1 else 0.0
    td = P["today"]
    flags = {
        "oversold_h1": H1["rsi"] < 30, "overbought_h1": H1["rsi"] > 70,
        "oversold_m15": M["rsi"] < 25, "overbought_m15": M["rsi"] > 75,
        "ext_atr": ext, "stretched_down": ext < -2.0, "stretched_up": ext > 2.0,
        "adr_used": (td["range"] / ind["adr20"]) if td else 0.0,
        "trend_regime": "trend" if H1["adx"] >= 25 else ("laterale" if H1["adx"] < 18 else "intermedio"),
    }
    return {"score": round(score, 3), "label": label, "dir": direction, "tf": tf,
            "structure": st, "flags": flags}


# ----------------------------------------------------------------------------- scenarios
def _r1(x):
    return round(float(x), 1)


def _buf(ind: dict, cfg=CFG) -> float:
    return cfg["buf_atr_d"] * ind["atr_d"]


def _targets(direction: int, entry: float, risk: float, zones: list[dict], atr_d: float, cfg=CFG):
    """T1 = first meaningful zone at least ~1R away; T2/T3 = next solid zones further out.
    Returns (targets, obstacle, hurdles):
      obstacle = a *strong* zone standing in the way before 1R (the setup is then discarded);
      hurdles  = weaker zones crossed before T1 (shown to the trader, they do not block the setup)."""
    if direction < 0:
        cands = sorted([z for z in zones if z["hi"] < entry], key=lambda z: -z["hi"])
        edge = lambda z: z["hi"]
    else:
        cands = sorted([z for z in zones if z["lo"] > entry], key=lambda z: z["lo"])
        edge = lambda z: z["lo"]
    out, obstacle, hurdles = [], None, []
    for z in cands:
        px = edge(z)
        dist = abs(entry - px)
        if not out:
            if dist < 0.95 * risk:
                if z["score"] >= 6 and obstacle is None:
                    obstacle = {"price": _r1(px), "label": z["label"]}
                elif z["score"] >= 2.0 and dist >= 0.25 * risk:
                    hurdles.append({"lo": _r1(z["lo"]), "hi": _r1(z["hi"]), "rr": round(dist / risk, 2), "stars": z["stars"]})
                continue
            if z["score"] < 2.0:
                continue
        else:
            if z["score"] < cfg["zone_min_score"] or abs(px - out[-1]["price"]) < 0.12 * atr_d:
                continue
        out.append({"price": _r1(px), "rr": round(dist / risk, 2), "label": z["label"], "proj": False})
        if len(out) >= 3:
            break
    if not out:   # nothing left on the ladder: fall back to R-multiples
        out = [{"price": _r1(entry + direction * m * risk), "rr": m, "label": f"proiezione {m:g}R", "proj": True}
               for m in (1.5, 2.5)]
    return out, obstacle, hurdles


def build_scenarios(P: dict, ind: dict, bias: dict, zones: list[dict], cfg=CFG) -> dict:
    price, atr_d = P["price"], ind["atr_d"]
    buf = _buf(ind, cfg)
    min_d, max_d = cfg["min_dist_atr"] * atr_d, cfg["max_dist_atr"] * atr_d
    minw = max(cfg["minw_atr_d"] * atr_d, 3.0)
    above = sorted([z for z in zones if z["lo"] > price], key=lambda z: z["lo"])
    below = sorted([z for z in zones if z["hi"] < price], key=lambda z: -z["hi"])
    inside = [z for z in zones if z["lo"] <= price <= z["hi"]]

    def zb(z):
        lo, hi = z["lo"], z["hi"]
        if hi - lo < minw:
            lo, hi = z["mid"] - minw / 2, z["mid"] + minw / 2
        return lo, hi

    def stop_beyond(z, direction):
        """Stop on the far side of the zone plus one buffer (15% of the daily ATR)."""
        lo, hi = zb(z)
        return hi + buf if direction < 0 else lo - buf

    def finish(s, z):
        tg, obstacle, hurdles = _targets(s["dir"], s["entry"], s["risk"], zones, atr_d, cfg)
        rr1 = tg[0]["rr"]
        q = "sfavorevole" if (obstacle or rr1 < 0.95) else "ok"
        s.update(targets=tg, obstacle=obstacle, hurdles=hurdles, rr1=rr1, quality=q, confluence=z["names"][:5],
                 zone_label=z["label"], score=z["score"], stars=z["stars"])
        for k in ("zone_lo", "zone_hi", "entry", "stop", "risk", "trigger"):
            if k in s:
                s[k] = _r1(s[k])
        return s

    def fade(direction, z, role):
        lo, hi = zb(z)
        entry = (lo + hi) / 2
        stop = stop_beyond(z, direction)
        return finish({"type": "fade", "dir": direction, "role": role, "zone_lo": lo, "zone_hi": hi,
                       "entry": entry, "stop": stop, "risk": abs(stop - entry)}, z)

    def brk(direction, z, role):
        lo, hi = zb(z)
        trig = lo if direction < 0 else hi
        entry = trig + direction * cfg["brk_fill_atr_d"] * atr_d   # typical fill after a confirmed close
        stop = trig - direction * buf                     # back inside the broken level = failed break
        return finish({"type": "break", "dir": direction, "role": role, "zone_lo": lo, "zone_hi": hi,
                       "trigger": trig, "entry": entry, "stop": stop, "risk": abs(stop - entry)}, z)

    def qualifying(cands, min_score, dmin, dmax=max_d):
        return [z for z in cands if not z["dyn_only"] and z["score"] >= min_score
                and dmin <= abs(z["mid"] - price) <= dmax]

    S: list[dict] = []
    d = bias["dir"]
    f = bias["flags"]

    def with_trend(direction):
        """One pull-back entry and one breakout, both in the direction of the trend.
        A second, deeper pull-back zone and counter-trend entries were tested and left out: they
        showed no benefit in the replay (small samples) and can conflict with an open trade."""
        pull_side = above if direction < 0 else below
        brk_side = below if direction < 0 else above
        q = qualifying(pull_side, cfg["zone_min_score"], min_d)
        first = None
        for z in q:                                    # nearest zone with a workable R:R
            s = fade(direction, z, "con il trend")
            if s["quality"] != "sfavorevole":
                first = s
                S.append(s)
                break
        # breakout: nearest level whose break leaves room (no strong zone before 1R)
        for z in inside + [z for z in brk_side if not z["dyn_only"] and z["score"] >= 2.0
                           and abs(z["mid"] - price) <= max_d]:
            sb = brk(direction, z, "con il trend · rottura")
            if sb["quality"] != "sfavorevole":
                S.append(sb)
                break
    if d != 0:
        with_trend(d)
    else:
        for direction, side in ((-1, above), (1, below)):
            for z in qualifying(side, cfg["zone_min_score"], min_d):
                s = fade(direction, z, "range")
                if s["quality"] != "sfavorevole":
                    S.append(s)
                    break
    letters = iter("ABCDEF")
    for s in S:
        s["id"] = next(letters)
        c = []
        if s["type"] == "break":
            if (s["dir"] < 0 and (f["oversold_h1"] or f["stretched_down"])) or \
               (s["dir"] > 0 and (f["overbought_h1"] or f["stretched_up"])):
                c.append("stretched")
            if f["adr_used"] >= 0.9:
                c.append("adr")
        s["cautions"] = c
    # level whose loss invalidates the directional view
    inval = None
    fades = [s for s in S if s["type"] == "fade" and s["dir"] == d]
    if d != 0 and fades:
        inval = max(s["stop"] for s in fades) if d < 0 else min(s["stop"] for s in fades)
    beyond = []
    if inval is not None:
        side = [z for z in (above if d < 0 else below) if not z["dyn_only"] and z["score"] >= 2.0
                and ((z["lo"] > inval) if d < 0 else (z["hi"] < inval))]
        beyond = [{"lo": _r1(z["lo"]), "hi": _r1(z["hi"]), "label": z["label"]} for z in side[:2]]
    return {"setups": S, "invalidation": inval, "beyond": beyond, "buffer": round(buf, 1)}


# ----------------------------------------------------------------------------- evaluation
def evaluate_setup(s: dict, m5: pd.DataFrame, start: pd.Timestamp, deadline: pd.Timestamp,
                   close: pd.Timestamp) -> dict:
    """Mechanical replay on 5-minute bars after `start`. Conservative conventions:
    * fade   : limit order at `entry` (zone mid);
    * break  : entry at the first 15-min close beyond the trigger; if that close is already beyond the
               first target the setup is cancelled ("annullato": no trade, it does not count in the statistics);
    * reclaim: needs an H1 close beyond the trigger, then a limit order at `entry`;
    * if stop and target fall inside the same 5-min bar the stop is assumed first;
    * no new entries after `deadline`; open trades are closed at the session close."""
    d = s["dir"]
    fut = m5[(m5["time"] >= start) & (m5["time"] < close)].reset_index(drop=True)
    res = {"id": s["id"], "status": "non attivato", "R": 0.0, "entry_time": None, "entry_px": None,
           "exit_time": None, "exit_px": None, "t2": False}
    if not len(fut) or not s["targets"]:
        return res
    stop, t1 = s["stop"], s["targets"][0]["price"]
    t2 = s["targets"][1]["price"] if len(s["targets"]) > 1 else None
    i_entry, entry_px = None, None
    if s["type"] == "fade":
        for i, b in fut.iterrows():
            if b["time"] >= deadline:
                break
            hit = b["high"] >= s["entry"] if d < 0 else b["low"] <= s["entry"]
            if hit:
                gap = (b["open"] > s["entry"]) if d < 0 else (b["open"] < s["entry"])
                entry_px, i_entry = (b["open"] if gap else s["entry"]), i
                break
    elif s["type"] == "break":
        m15 = resample(fut, 15)
        for _, b in m15.iterrows():
            end = b["time"] + pd.Timedelta(minutes=15)
            if end > deadline:
                break
            ok = b["close"] < s["trigger"] if d < 0 else b["close"] > s["trigger"]
            if ok:
                nxt = fut.index[fut["time"] >= end]
                if len(nxt):
                    if (b["close"] - t1) * d >= 0:      # the confirming close is already beyond the first target:
                        res["status"] = "annullato"     # the move is done, there is no trade left to take
                        return res
                    i_entry, entry_px = int(nxt[0]) - 1, float(b["close"])
                break
    else:  # reclaim
        h1 = resample(fut, 60)
        t_ok = None
        for _, b in h1.iterrows():
            end = b["time"] + H
            if end > deadline:
                break
            ok = b["close"] > s["trigger"] if d > 0 else b["close"] < s["trigger"]
            if ok:
                t_ok = end
                break
        if t_ok is not None:
            for i, b in fut[fut["time"] >= t_ok].iterrows():
                if b["time"] >= deadline:
                    break
                hit = b["low"] <= s["entry"] if d > 0 else b["high"] >= s["entry"]
                if hit:
                    entry_px, i_entry = s["entry"], i
                    break
    if i_entry is None:
        return res
    risk = abs(stop - entry_px)
    if risk <= 0:
        return res
    res.update(status="aperto", entry_time=fut["time"].iloc[min(i_entry + (1 if s["type"] == "break" else 0), len(fut) - 1)],
               entry_px=float(entry_px))
    # the entry bar of a limit order can also hit the stop (not the target: unknown order)
    first = i_entry if s["type"] != "break" else i_entry + 1
    done = False
    for i in range(first, len(fut)):
        b = fut.iloc[i]
        stop_hit = b["high"] >= stop if d < 0 else b["low"] <= stop
        tgt_hit = (b["low"] <= t1 if d < 0 else b["high"] >= t1) and not (i == i_entry and s["type"] != "break")
        if stop_hit:
            res.update(status="stop", exit_time=b["time"], exit_px=stop, R=-1.0)
            done = True
            break
        if tgt_hit:
            res.update(status="target 1", exit_time=b["time"], exit_px=t1, R=round(abs(t1 - entry_px) / risk, 2))
            done = True
            if t2 is not None:      # informational: was T2 reached later without first returning to entry?
                for j in range(i, len(fut)):
                    bb = fut.iloc[j]
                    back = bb["high"] >= entry_px if d < 0 else bb["low"] <= entry_px
                    if j > i and back:
                        break
                    if (bb["low"] <= t2 if d < 0 else bb["high"] >= t2):
                        res["t2"] = True
                        break
            break
    if not done:
        last = fut.iloc[-1]
        pnl = (entry_px - last["close"]) if d < 0 else (last["close"] - entry_px)
        res.update(status="chiuso a fine sessione", exit_time=last["time"], exit_px=float(last["close"]),
                   R=round(pnl / risk, 2))
    return res


# ----------------------------------------------------------------------------- statistics
def pivot_stats(d1c: pd.DataFrame, n: int = 60) -> dict:
    d = d1c.tail(n + 1).reset_index(drop=True)
    cnt = {k: 0 for k in ("P", "R1", "S1", "R2", "S2", "R3", "S3")}
    tot = 0
    for i in range(1, len(d)):
        pv = pivots_classic(d.loc[i - 1, "high"], d.loc[i - 1, "low"], d.loc[i - 1, "close"])
        lo, hi = d.loc[i, "low"], d.loc[i, "high"]
        tot += 1
        for k, v in pv.items():
            if lo <= v <= hi:
                cnt[k] += 1
    return {"n": tot, "rate": {k: (v / tot if tot else 0.0) for k, v in cnt.items()}}


def hourly_profile(h1c: pd.DataFrame, n_days: int = 20) -> list[dict]:
    d = h1c.assign(tday=tday_of(h1c["time"]))
    days = sorted(d["tday"].unique())[-n_days:]
    d = d[d["tday"].isin(days)]
    d = d.assign(hr=d["time"].dt.tz_convert(ROME).dt.hour, rng=d["high"] - d["low"])
    g = d.groupby("hr")["rng"].agg(["mean", "median", "count"])
    return [{"hour": int(h), "mean": float(g.loc[h, "mean"]) if h in g.index else 0.0,
             "median": float(g.loc[h, "median"]) if h in g.index else 0.0} for h in range(24)]


def session_stats(h1c: pd.DataFrame, n_days: int = 20) -> dict:
    d = h1c.assign(tday=tday_of(h1c["time"]))
    days = sorted(d["tday"].unique())[-n_days:]
    rng = {"asia": [], "london": [], "ny": []}
    hi_in = {"asia": 0, "london": 0, "ny": 0}
    lo_in = {"asia": 0, "london": 0, "ny": 0}
    n = 0
    for day in days:
        b = session_bounds(pd.Timestamp(day))
        x = d[d["tday"] == day]
        if len(x) < 15:
            continue
        n += 1
        parts = {"asia": x[x["time"] < b["london"]],
                 "london": x[(x["time"] >= b["london"]) & (x["time"] < b["ny"])],
                 "ny": x[x["time"] >= b["ny"]]}
        dh, dl = x["high"].max(), x["low"].min()
        for k, v in parts.items():
            if len(v):
                rng[k].append(v["high"].max() - v["low"].min())
                if v["high"].max() >= dh:
                    hi_in[k] += 1
                if v["low"].min() <= dl:
                    lo_in[k] += 1
    return {"n": n, "avg_range": {k: float(np.mean(v)) if v else 0.0 for k, v in rng.items()},
            "high_in": {k: v / n if n else 0 for k, v in hi_in.items()},
            "low_in": {k: v / n if n else 0 for k, v in lo_in.items()}}


# ----------------------------------------------------------------------------- main entry
def analyze(h1_raw: pd.DataFrame, m5_raw: pd.DataFrame, asof: pd.Timestamp, cfg=CFG,
            with_stats: bool = True) -> dict:
    P = prepare(h1_raw, m5_raw, asof)
    d1 = to_daily(P["h1"])
    h4 = to_h4(P["h1"])
    today = next_weekday(tday_of(asof))
    P["tday"] = today
    dcomp = d1[d1["tday"] < today]
    prev, prev2 = dcomp.iloc[-1], dcomp.iloc[-2]
    b = session_bounds(today)
    # today's developing session
    tm5 = P["m5"][P["m5"]["time"] >= b["open"]]
    today_d = None
    if len(tm5):
        today_d = {"open": float(tm5["open"].iloc[0]), "high": float(tm5["high"].max()),
                   "low": float(tm5["low"].min()), "range": float(tm5["high"].max() - tm5["low"].min()),
                   "asia_done": bool(asof >= b["london"])}
        a = tm5[tm5["time"] < b["london"]]
        if len(a):
            today_d.update(asia_high=float(a["high"].max()), asia_low=float(a["low"].min()))
        else:
            today_d["asia_done"] = False
    P["today"] = today_d
    P["pivots"] = pivots_classic(prev["high"], prev["low"], prev["close"])
    ind = compute_indicators(P, d1, h4)
    bias = compute_bias(P, ind, cfg)
    levels = collect_levels(P, d1, h4, ind, cfg)
    zones = cluster_levels(levels, P["price"], ind["atr_d"], cfg)
    plan = build_scenarios(P, ind, bias, zones, cfg)
    A = {
        "asof": asof, "last_bar": P["last_bar"], "tday": today, "bounds": b, "price": P["price"],
        "prev": {"tday": prev["tday"], "open": float(prev["open"]), "high": float(prev["high"]),
                 "low": float(prev["low"]), "close": float(prev["close"]),
                 "chg": float(prev["close"] / prev2["close"] - 1), "range": float(prev["high"] - prev["low"])},
        "today": today_d, "pivots": P["pivots"], "ind": ind, "bias": bias, "levels": levels,
        "zones": zones, "scenarios": plan["setups"], "invalidation": plan["invalidation"],
        "beyond": plan["beyond"],
        "buffer": plan["buffer"],
        "frames": {"d1": d1, "h4": h4, "h1": P["h1"], "m15": P["m15"], "m5": P["m5"]},
        "market_open": bool(len(tm5) > 0 and (asof - P["last_bar"]) < pd.Timedelta(minutes=20)),
    }
    for z in zones:      # historical probability that price reaches the zone before the close
        z["prob"] = None
    if with_stats:
        model = excursion_model(P["h1c"], asof)
        A["excursion"] = {"n": model["n"], "offset_h": model["offset_h"],
                          "up_med": float(np.median(model["up"])) * ind["atr_d"] if model["n"] else None,
                          "dn_med": float(np.median(model["dn"])) * ind["atr_d"] if model["n"] else None,
                          "up_p80": float(np.quantile(model["up"], 0.8)) * ind["atr_d"] if model["n"] else None,
                          "dn_p80": float(np.quantile(model["dn"], 0.8)) * ind["atr_d"] if model["n"] else None}
        for z in zones:
            if z["side"] == "R":
                z["prob"] = reach_prob(model, z["lo"] - P["price"], ind["atr_d"])
            elif z["side"] == "S":
                z["prob"] = reach_prob(model, z["hi"] - P["price"], ind["atr_d"])
        for s in plan["setups"]:
            ref = s.get("trigger", s["zone_lo"] if s["dir"] < 0 else s["zone_hi"]) if s["type"] != "fade" \
                else (s["zone_lo"] if s["dir"] < 0 else s["zone_hi"])
            s["prob_touch"] = reach_prob(model, ref - P["price"], ind["atr_d"])
        A["stats"] = {"pivot": pivot_stats(dcomp, 60), "hourly": hourly_profile(P["h1c"], 20),
                      "sessions": session_stats(P["h1c"][P["h1c"]["time"] < b["open"]], 20)}
        rec = dcomp.tail(7).copy()
        rec["chg"] = rec["close"].pct_change()
        rec["range"] = rec["high"] - rec["low"]
        A["stats"]["recent"] = rec.tail(6)[["tday", "open", "high", "low", "close", "chg", "range"]].to_dict("records")
    return A


def walk_forward(h1_raw: pd.DataFrame, m5_raw: pd.DataFrame, n_days: int = 40, hour_rome: int = 8,
                 before: pd.Timestamp | None = None, cfg=CFG) -> list[dict]:
    """Replay the engine at `hour_rome` (Europe/Rome) on each of the last `n_days` completed
    trading days and evaluate every setup mechanically on the rest of that session.
    Rules are fixed (no optimisation); only data available at that time is used."""
    td = tday_of(m5_raw["time"])
    days = sorted(pd.unique(td))
    first_ok = m5_raw["time"].iloc[0] + pd.Timedelta(days=4)
    out = []
    for day in days:
        day = pd.Timestamp(day)
        b = session_bounds(day)
        if b["open"] < first_ok or (before is not None and b["close"] > before) or day.day_of_week >= 5:
            continue
        asof = pd.Timestamp(year=day.year, month=day.month, day=day.day, hour=hour_rome, tz=ROME).tz_convert("UTC")
        deadline = pd.Timestamp(year=day.year, month=day.month, day=day.day,
                                hour=cfg["entry_deadline_rome"], tz=ROME).tz_convert("UTC")
        sess = m5_raw[(m5_raw["time"] >= b["open"]) & (m5_raw["time"] < b["close"])]
        if len(sess) < 200:          # holiday / truncated session
            continue
        try:
            A = analyze(h1_raw, m5_raw, asof, cfg, with_stats=False)
        except Exception:
            continue
        res = [dict(evaluate_setup(s, m5_raw, asof, deadline, b["close"]), type=s["type"], dir=s["dir"],
                    role=s["role"], quality=s["quality"], rr1=s["rr1"]) for s in A["scenarios"]]
        out.append({"tday": day, "bias": A["bias"]["label"], "score": A["bias"]["score"],
                    "price": A["price"], "results": res,
                    "day_chg": float(sess["close"].iloc[-1] - A["price"])})
    return out[-n_days:]


# ----------------------------------------------------------------------------- reach probabilities
def excursion_model(h1c: pd.DataFrame, asof: pd.Timestamp, n_days: int = 250) -> dict:
    """How far did price travel (up / down) from this time-of-session to the session close on
    past days?  Distances are normalised by the daily ATR(14) known at that day's open, so the
    distribution adapts to the current volatility.  Purely descriptive (no direction forecast)."""
    d1 = to_daily(h1c)
    atr = ta.atr(d1, 14).shift(1)
    atr.index = d1["tday"]
    today = next_weekday(tday_of(asof))
    b0 = session_bounds(today)
    off = max(pd.Timedelta(0), (asof - b0["open"]).floor("h"))
    off = min(off, pd.Timedelta(hours=22))
    x = h1c.assign(tday=tday_of(h1c["time"]))
    days = [d for d in sorted(x["tday"].unique()) if d < today][-n_days:]
    ups, dns = [], []
    for day, g in x[x["tday"].isin(days)].groupby("tday"):
        a = atr.get(day, np.nan)
        if not np.isfinite(a) or len(g) < 15:
            continue
        t_ref = session_bounds(pd.Timestamp(day))["open"] + off
        rest = g[g["time"] >= t_ref]
        if not len(rest):
            continue
        p = float(rest["open"].iloc[0])
        ups.append((rest["high"].max() - p) / a)
        dns.append((p - rest["low"].min()) / a)
    return {"up": np.sort(np.array(ups)), "dn": np.sort(np.array(dns)), "n": len(ups),
            "offset_h": off.total_seconds() / 3600.0}


def reach_prob(model: dict, dist: float, atr_d: float) -> float:
    """Share of past sessions in which price moved at least `dist` (signed, in $) from here."""
    if model["n"] == 0 or atr_d <= 0:
        return float("nan")
    arr = model["up"] if dist > 0 else model["dn"]
    return float((arr >= abs(dist) / atr_d).mean())
