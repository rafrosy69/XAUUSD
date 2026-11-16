"""Side computations: intermarket table, rule replay summary, journal of published plans,
independent data cross-check."""
from __future__ import annotations

import csv
import glob
import json
import math
import os

import numpy as np
import pandas as pd

from . import data, engine
from .charts import fnum
from .engine import H, ROME

INSTRUMENTS = [("DOLLAR.IDX/USD", "Dollar Index (CFD)", 2), ("EUR/USD", "EUR/USD", 4),
               ("XAG/USD", "Argento (XAG/USD)", 2), ("USTBOND.TR/USD", "T-Bond USA (prezzo: scende se i rendimenti salgono)", 2),
               ("USA500.IDX/USD", "S&P 500 (CFD)", 0)]


def intermarket(h1_gold: pd.DataFrame, asof: pd.Timestamp, gold_price: float, live: bool = True) -> tuple[list[dict], float | None]:
    """Related markets: last price, change today / yesterday / 5 sessions (New York close days) and
    correlation of hourly returns with gold over the last 20 sessions."""
    today = engine.next_weekday(engine.tday_of(asof))
    g = h1_gold[h1_gold["time"] + H <= asof]
    gret = np.log(g.set_index("time")["close"]).diff()
    out, eurusd = [], None
    gd = engine.to_daily(g)
    gdc = gd[gd["tday"] < today]
    out.append({"name": "Oro (XAU/USD)", "dec": 1, "last": float(gold_price),
                "chg_today": float(gold_price / gdc["close"].iloc[-1] - 1),
                "chg_prev": float(gdc["close"].iloc[-1] / gdc["close"].iloc[-2] - 1),
                "chg_5d": float(gdc["close"].iloc[-1] / gdc["close"].iloc[-6] - 1), "corr": None})
    for sym, name, dec in INSTRUMENTS:
        try:
            df = data.duka_candles(sym, "1HOUR", 700, end=asof)
            df = df[df["time"] + H <= asof]
            d = engine.to_daily(df)
            dc = d[d["tday"] < today]
            last, last_t = float(df["close"].iloc[-1]), df["time"].iloc[-1] + H
            if live:                                   # freshest print from the 1-minute feed
                try:
                    m = data.duka_candles(sym, "1MIN", 5, end=asof)
                    if m["time"].iloc[-1] > last_t:
                        last, last_t = float(m["close"].iloc[-1]), m["time"].iloc[-1]
                except Exception:
                    pass
            fresh = (asof - last_t) < pd.Timedelta(hours=2) and engine.tday_of(last_t - pd.Timedelta(minutes=1)) >= today
            r = np.log(df.set_index("time")["close"]).diff()
            j = pd.concat([gret, r], axis=1, join="inner").dropna().tail(460)
            corr = float(j.corr().iloc[0, 1]) if len(j) > 100 else None
            out.append({"name": name, "dec": dec, "last": last,
                        "chg_today": float(last / dc["close"].iloc[-1] - 1) if fresh else None,
                        "chg_prev": float(dc["close"].iloc[-1] / dc["close"].iloc[-2] - 1),
                        "chg_5d": float(dc["close"].iloc[-1] / dc["close"].iloc[-6] - 1), "corr": corr})
            if sym == "EUR/USD":
                eurusd = last
        except Exception:
            continue
    return out, eurusd


def yields_snapshot() -> dict:
    tv = data.tradingview_snapshot(("TVC:US10Y", "TVC:US02Y", "TVC:DXY"), ["close"])
    out = {}
    for k, name in (("TVC:US10Y", "US10Y"), ("TVC:US02Y", "US02Y"), ("TVC:DXY", "DXY")):
        v = tv.get(k, {}).get("close")
        if v:
            out[name] = float(v)
    return out


def quality_check(A: dict, m1_last: float | None) -> dict:
    """Compare our numbers with independent sources (Swissquote, gold-api, TradingView/OANDA)."""
    rows = []
    sq, ga = data.swissquote_quote(), data.goldapi_quote()
    ref = m1_last if m1_last is not None else A["price"]
    if sq:
        rows.append({"what": f"Prezzo bid (Swissquote, {sq['time'].tz_convert(ROME).strftime('%H:%M:%S')})",
                     "ours": fnum(ref, 2), "theirs": fnum(sq["bid"], 2), "diff": fnum(sq["bid"] - ref, 2, True) + " $"})
    if ga:
        rows.append({"what": f"Prezzo (gold-api.com, {ga['time'].tz_convert(ROME).strftime('%H:%M')})",
                     "ours": fnum(ref, 2), "theirs": fnum(ga["price"], 2), "diff": fnum(ga["price"] - ref, 2, True) + " $"})
    tv = data.tradingview_snapshot().get("OANDA:XAUUSD", {})
    ind = A["ind"]
    pairs = [("SMA20 D1", ind["D1"]["sma20"], "SMA20", 1, " $"), ("SMA50 D1", ind["D1"]["sma50"], "SMA50", 1, " $"),
             ("SMA200 D1", ind["D1"]["sma200"], "SMA200", 1, " $"), ("RSI(14) D1", ind["D1"]["rsi"], "RSI", 1, ""),
             ("EMA50 H1", ind["H1"]["ema50"], "EMA50|60", 1, " $"), ("EMA200 H1", ind["H1"]["ema200"], "EMA200|60", 1, " $"),
             ("RSI(14) H1", ind["H1"]["rsi"], "RSI|60", 1, ""), ("EMA50 H4", ind["H4"]["ema50"], "EMA50|240", 1, " $")]
    for label, ours, key, d, unit in pairs:
        v = tv.get(key)
        if v is None or not math.isfinite(ours):
            continue
        rows.append({"what": f"{label} (TradingView · OANDA)", "ours": fnum(ours, d), "theirs": fnum(v, d),
                     "diff": fnum(v - ours, d, True) + unit})
    note = ("Scarti di pochi decimi sono fisiologici: feed diversi (bid Dukascopy contro prezzo medio OANDA) e istanti di "
            "rilevazione non identici, mentre il prezzo si muove.")
    return {"rows": rows, "note": note, "swissquote": sq, "goldapi": ga}


NO_TRADE = ("non attivato", "annullato")          # outcomes without a position: never counted as trades

ROLE_LABEL = {"con il trend": "Con il trend · ingresso su ritracciamento",
              "con il trend · rottura": "Con il trend · rottura",
              "range": "Mercato laterale · vendita su resistenza / acquisto su supporto"}


def _replay_sig(n_days: int) -> str:
    return json.dumps(engine.CFG, sort_keys=True) + f"|{n_days}|v{engine.RULES_VERSION}"


def _replay_path(cache_dir: str, asof: pd.Timestamp) -> str:
    return os.path.join(cache_dir, f"replay_{engine.next_weekday(engine.tday_of(asof)).strftime('%Y-%m-%d')}.json")


def replay_cached(cache_dir: str, asof: pd.Timestamp, n_days: int) -> dict | None:
    """Replay statistics already computed today with the same rules, if any."""
    path = _replay_path(cache_dir, asof)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            c = json.load(fh)
        return c["data"] if c.get("sig") == _replay_sig(n_days) else None
    except Exception:
        return None


def replay_summary(h1: pd.DataFrame, m5: pd.DataFrame, asof: pd.Timestamp, n_days: int = 250,
                   cache_dir: str | None = None) -> dict | None:
    """Rule replay on past sessions (needs a long 5-minute history). The result is cached once per
    trading day, because it only changes when a session ends or the rules change."""
    wf = engine.walk_forward(h1, m5, n_days=n_days, hour_rome=8, before=asof)
    if len(wf) < 20:
        return None
    rows = [dict(day=d["tday"], role=r["role"], status=r["status"], R=r["R"]) for d in wf for r in d["results"]]
    df = pd.DataFrame(rows)

    def agg(g):
        tr = g[~g["status"].isin(NO_TRADE)]
        n = len(tr)
        return {"setups": int(len(g)), "trades": int(n), "win": float(100 * (tr["R"] > 0).mean()) if n else 0.0,
                "stop": float(100 * (tr["status"] == "stop").mean()) if n else 0.0,
                "avg": float(tr["R"].mean()) if n else 0.0,
                "se": float(tr["R"].std(ddof=1) / math.sqrt(n)) if n > 1 else 0.0}
    out_rows = []
    for role in ("con il trend", "con il trend · rottura", "range"):
        g = df[df["role"] == role]
        if len(g):
            out_rows.append(dict(label=ROLE_LABEL[role], **agg(g)))
    b = pd.DataFrame([{"score": d["score"], "chg": d["day_chg"]} for d in wf])
    dd = b[b["score"].abs() >= engine.CFG["bias_mild"]]
    hit = float(100 * (np.sign(dd["chg"]) == np.sign(dd["score"])).mean()) if len(dd) else float("nan")
    f = lambda t: f"{pd.Timestamp(t).day:02d}/{pd.Timestamp(t).month:02d}/{pd.Timestamp(t).year}"
    res = {"days": len(wf), "from": f(wf[0]["tday"]), "to": f(wf[-1]["tday"]), "rows": out_rows, "total": agg(df),
           "bias_hit": hit, "bias_n": int(len(dd))}
    if cache_dir and len(wf) >= 0.9 * n_days:          # never cache a replay cut short by missing history
        os.makedirs(cache_dir, exist_ok=True)
        with open(_replay_path(cache_dir, asof), "w", encoding="utf-8") as fh:
            json.dump({"sig": _replay_sig(n_days), "data": res}, fh)
    return res


# ----------------------------------------------------------------------------- journal of published plans
def _plan_path(outdir: str, tday: pd.Timestamp) -> str:
    return os.path.join(outdir, "plans", f"plan_{tday.strftime('%Y-%m-%d')}.json")


def save_plan(outdir: str, A: dict, titles: dict, keep: bool = False) -> tuple[str, bool]:
    """Store the plan that will be verified on real prices once the session is over.
    Official plan of a trading day = the last one generated before the London open (09:00 Rome
    in winter); if none was generated that early, the first one generated afterwards.
    Later refreshes never replace it, so the register cannot be flattered after the fact.
    `keep=True` (set when `plan_status` found an official plan that stays in force, e.g. because
    one of its setups has already been triggered) forbids the replacement in any case."""
    os.makedirs(os.path.join(outdir, "plans"), exist_ok=True)
    path = _plan_path(outdir, A["tday"])
    plan = {"tday": A["tday"].strftime("%Y-%m-%d"), "asof": A["asof"].isoformat(), "price": A["price"],
            "bias": A["bias"]["label"], "score": A["bias"]["score"], "close": A["bounds"]["close"].isoformat(),
            "invalidation": A.get("invalidation"), "evaluated": False,
            "setups": [{k: s.get(k) for k in ("id", "type", "dir", "role", "zone_lo", "zone_hi", "trigger", "entry", "stop",
                                              "risk", "targets", "rr1")} | {"title": titles[s["id"]]} for s in A["scenarios"]]}
    with open(os.path.join(outdir, "plans", "latest_plan.json"), "w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False, indent=1)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                old = json.load(fh)
            replace = (not old.get("evaluated") and pd.Timestamp(old["asof"]) < A["bounds"]["london"]
                       and A["asof"] < A["bounds"]["london"])
        except Exception:
            replace = True
        if keep or not replace:
            return path, False
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False, indent=1)
    return path, True


def _deadline(tday: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(year=tday.year, month=tday.month, day=tday.day, hour=engine.CFG["entry_deadline_rome"],
                        tz=ROME).tz_convert("UTC")


def track_setup(s: dict, m5: pd.DataFrame, start: pd.Timestamp, deadline: pd.Timestamp, close: pd.Timestamp,
                now: pd.Timestamp) -> dict:
    """Where a published setup stands at `now`, with the same mechanical conventions as the register
    (`engine.evaluate_setup`) plus what matters for a position still open: unrealised result and
    best / worst price since the entry.
    state: in attesa | non attivato | annullato | aperto | stop | target 1 | chiuso a fine sessione"""
    r = engine.evaluate_setup(s, m5, start, deadline, close)
    d, live = s["dir"], now < close
    state = r["status"]
    if state == "non attivato" and live and now < deadline:
        state = "in attesa"
    elif state == "chiuso a fine sessione" and live:
        state = "aperto"
    fut = m5[(m5["time"] >= start) & (m5["time"] < close)]
    last = float(fut["close"].iloc[-1]) if len(fut) else None
    out = {"id": s["id"], "title": s.get("title", ""), "type": s["type"], "dir": d, "state": state,
           "zone_lo": s.get("zone_lo"), "zone_hi": s.get("zone_hi"), "trigger": s.get("trigger"),
           "entry_plan": s.get("entry"), "stop": s["stop"], "t1": s["targets"][0]["price"] if s.get("targets") else None,
           "entry_px": r["entry_px"], "entry_time": r["entry_time"], "exit_px": r["exit_px"], "exit_time": r["exit_time"],
           "R": float(r["R"]), "t2": bool(r.get("t2")), "last": last, "risk": None, "best": None, "worst": None,
           "mfe": None, "mae": None, "seen_hi": float(fut["high"].max()) if len(fut) else None,
           "seen_lo": float(fut["low"].min()) if len(fut) else None}
    if r["entry_px"] is None:
        return out
    risk = abs(s["stop"] - r["entry_px"])
    after = fut[fut["time"] >= r["entry_time"]]
    if state in ("stop", "target 1"):
        after = after[after["time"] <= r["exit_time"]]
    # limit orders: the favourable extreme of the fill bar may precede the fill, so it is not counted
    fav = after if s["type"] == "break" else after[after["time"] > r["entry_time"]]
    best = (float(fav["low"].min()) if d < 0 else float(fav["high"].max())) if len(fav) else float(r["entry_px"])
    worst = (float(after["high"].max()) if d < 0 else float(after["low"].min())) if len(after) else float(r["entry_px"])
    out.update(risk=float(risk), best=best, worst=worst,
               mfe=max(0.0, d * (best - r["entry_px"])) / risk, mae=max(0.0, -d * (worst - r["entry_px"])) / risk)
    return out


PLAN_FIELDS = ("id", "type", "dir", "role", "zone_lo", "zone_hi", "trigger", "entry", "stop", "risk", "targets", "rr1")
LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"


def next_letter(used) -> str:
    """First free scenario id: A…Z (without I and O), then AA, AB, … so that a session refreshed many
    times never runs out of ids. An id is never given twice in the same session."""
    for c in LETTERS:
        if c not in used:
            return c
    for a in LETTERS:
        for b in LETTERS:
            if a + b not in used:
                return a + b
    raise RuntimeError("identificativi degli scenari esauriti")


def _same(a: dict, b: dict, tol: float = 0.25) -> bool:
    """Same trade idea: same kind and side, same entry and stop (targets are not part of the identity)."""
    return (a["type"] == b["type"] and a["dir"] == b["dir"] and abs(a["entry"] - b["entry"]) <= tol
            and abs(a["stop"] - b["stop"]) <= tol)


def _load_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _book_path(outdir: str, tday: pd.Timestamp) -> str:
    return os.path.join(outdir, "plans", f"session_{tday.strftime('%Y-%m-%d')}.json")


def sync_book(outdir: str, A: dict, m5: pd.DataFrame, title_fn) -> dict | None:
    """Session book: every scenario shown during the running session is followed until it is resolved.

    * The first plan of the session is the official one (letters A, B, ...). A refresh before the London
      open replaces it only while none of its setups has been triggered: an open or closed trade can never
      disappear from the register.
    * Later refreshes keep the official plan and add their own scenarios with the next free letters, so a
      letter always means the same trade for the whole session. A refresh scenario still waiting is
      withdrawn when a later refresh no longer proposes it; once triggered it is followed to the end.
    * A scenario equal to a position already open is not proposed again.

    Mutates A["scenarios"] (letters, and the published targets for scenarios equal to an official setup).
    Returns None when the plan generated now is the official one, otherwise the status to display:
    {"asof": time of the official plan, "rows": [tracked setups], "same": plan unchanged, ...}."""
    tday, now, london, close = A["tday"], A["asof"], A["bounds"]["london"], A["bounds"]["close"]
    os.makedirs(os.path.join(outdir, "plans"), exist_ok=True)
    bpath = _book_path(outdir, tday)
    plan, book = _load_json(_plan_path(outdir, tday)), _load_json(bpath)
    if plan is not None and (book is None or book.get("official_asof") != plan.get("asof")):
        book = {"tday": plan["tday"], "official_asof": plan["asof"],          # book rebuilt from the official plan
                "setups": [dict(x, pub=plan["asof"], official=True, withdrawn=None) for x in plan["setups"]]}
    if plan is None:
        book = None

    def entry(x: dict, official: bool) -> dict:
        return dict({k: x.get(k) for k in PLAN_FIELDS}, title=title_fn(x), pub=now.isoformat(), official=official, withdrawn=None)

    def write(bk: dict):
        with open(bpath, "w", encoding="utf-8") as fh:
            json.dump(bk, fh, ensure_ascii=False, indent=1)

    def fresh():
        write({"tday": tday.strftime("%Y-%m-%d"), "official_asof": now.isoformat(),
               "setups": [entry(x, True) for x in A["scenarios"]]})

    if book is None or plan.get("evaluated") or pd.Timestamp(book["official_asof"]) >= now:
        fresh()
        return None
    deadline = _deadline(tday)
    active = [x for x in book["setups"] if not x.get("withdrawn")]
    track = {x["id"]: track_setup(x, m5, pd.Timestamp(x["pub"]), deadline, close, now) for x in active}
    triggered = any(r["entry_px"] is not None for r in track.values())
    if pd.Timestamp(book["official_asof"]) < london and now < london and not triggered:
        fresh()                                   # pre-London refresh, nothing triggered: this is the official plan
        return None
    used = {x["id"] for x in book["setups"]}
    opened = [r for r in track.values() if r["state"] == "aperto"]                          # positions still open

    def relate(sc: dict):
        """How a new scenario relates to the positions still open: it can only be triggered together with
        one of them if its entry lies between that position's stop and first target."""
        ref = sc["trigger"] if sc["type"] != "fade" and sc.get("trigger") is not None else sc["entry"]
        for r in opened:
            lo, hi = sorted((r["stop"], r["t1"] if r["t1"] is not None else r["entry_px"]))
            if lo < ref < hi:
                sc["adds_to" if r["dir"] == sc["dir"] else "against"] = r["id"]
            elif (ref >= r["stop"]) if r["dir"] < 0 else (ref <= r["stop"]):
                sc["after_stop"] = r["id"]
    kept, matched, added = [], set(), []
    for sc in A["scenarios"]:
        m = next((x for x in active if x["id"] not in matched and _same(x, sc)
                  and track[x["id"]]["state"] in ("in attesa", "non attivato", "aperto")), None)
        if m is not None and track[m["id"]]["state"] == "aperto":
            matched.add(m["id"])                  # already in position: shown in the status table only
            continue
        if m is None:
            sc["id"] = next_letter(used)
            used.add(sc["id"])
            added.append(entry(sc, False))
        else:
            matched.add(m["id"])
            sc["id"] = m["id"]
            if m["official"]:                     # the official version stays exactly as published
                sc["targets"], sc["rr1"] = m["targets"], m["rr1"]
            else:                                 # refresh scenario still waiting: the latest publication counts
                m.update({k: sc.get(k) for k in PLAN_FIELDS})
        relate(sc)
        kept.append(sc)
    for x in active:                              # waiting refresh scenarios no longer proposed are withdrawn
        if not x["official"] and x["id"] not in matched and track[x["id"]]["entry_px"] is None:
            x["withdrawn"] = now.isoformat()
    book["setups"] += added
    write(book)
    A["scenarios"] = kept
    rows = []
    for x in active:
        r = track[x["id"]]
        if x["official"] or r["entry_px"] is not None:
            rows.append(dict(r, official=bool(x["official"]), pub=pd.Timestamp(x["pub"])))
    official = [x for x in active if x["official"]]
    same = (not triggered and not added and len(kept) == len(official) and all(x["id"] in matched for x in official))
    return {"asof": pd.Timestamp(book["official_asof"]), "rows": rows, "triggered": triggered, "same": same,
            "deadline": deadline, "close": close}


def previous_session(outdir: str, A: dict, m5: pd.DataFrame) -> dict | None:
    """Final outcome of every scenario published during the last session that has a book: the official
    plan (the only one that enters the register) and the scenarios added by later refreshes. Scenarios
    withdrawn while still waiting are left out."""
    cur = A["tday"].strftime("%Y-%m-%d")
    books = [f for f in sorted(glob.glob(os.path.join(outdir, "plans", "session_*.json")))
             if os.path.basename(f)[len("session_"):-len(".json")] < cur]
    book = _load_json(books[-1]) if books else None
    if not book or not book.get("setups"):
        return None
    try:
        tday = pd.Timestamp(book["tday"])
        b = engine.session_bounds(tday)
        if m5["time"].iloc[0] > b["open"] or m5["time"].iloc[-1] < b["close"] - pd.Timedelta(minutes=10):
            return None                               # the price history at hand does not cover that session
        rows = []
        for x in book["setups"]:
            if x.get("withdrawn"):
                continue
            r = track_setup(x, m5, pd.Timestamp(x["pub"]), _deadline(tday), b["close"], b["close"])
            rows.append(dict(r, official=bool(x["official"]), pub=pd.Timestamp(x["pub"])))
    except Exception:
        return None
    return {"tday": tday, "rows": rows} if rows else None


def update_journal(outdir: str, m5: pd.DataFrame, now: pd.Timestamp) -> dict:
    """Evaluate the official plans of finished sessions on real prices and keep a CSV register."""
    jpath = os.path.join(outdir, "journal.csv")
    for path in sorted(glob.glob(os.path.join(outdir, "plans", "plan_*.json"))):
        with open(path, encoding="utf-8") as fh:
            plan = json.load(fh)
        close = pd.Timestamp(plan["close"])
        if plan.get("evaluated") or close > now or m5["time"].iloc[-1] < close - pd.Timedelta(minutes=10):
            continue
        asof = pd.Timestamp(plan["asof"])
        deadline = _deadline(pd.Timestamp(plan["tday"]))
        res = []
        for s in plan["setups"]:
            r = engine.evaluate_setup(s, m5, asof, deadline, close)
            res.append({"id": s["id"], "title": s["title"], "status": r["status"], "R": r["R"],
                        "entry_px": r["entry_px"], "exit_px": r["exit_px"]})
        plan["results"], plan["evaluated"] = res, True
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, ensure_ascii=False, indent=1)
        new = not os.path.exists(jpath)
        with open(jpath, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["seduta", "piano_delle", "trend", "setup", "titolo", "esito", "R", "ingresso", "uscita"])
            for r in res:
                w.writerow([plan["tday"], asof.tz_convert(ROME).strftime("%H:%M"), plan["bias"], r["id"], r["title"], r["status"],
                            r["R"], r["entry_px"] if r["entry_px"] is not None else "", r["exit_px"] if r["exit_px"] is not None else ""])
    rows = []
    if os.path.exists(jpath):
        with open(jpath, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rows.append({"day": r["seduta"], "id": r["setup"], "title": r["titolo"], "status": r["esito"], "R": float(r["R"])})
    tr = [r for r in rows if r["status"] not in NO_TRADE]
    return {"rows": rows, "n_trades": len(tr), "tot_R": float(sum(r["R"] for r in tr))}
