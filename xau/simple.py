"""Plain-language pages of the site, for readers who are not traders.

Two steps, so that the pages can be redesigned without touching the stored data:

  * view(A, X)        small JSON summary of the running (or next) session: price, direction, the ideas of the
                      day with their state, key appointments, results. Saved next to the technical report as
                      XAUUSD_briefing_<day>.json;
  * body(view, opt)   HTML of the page of one session; archive_body(...) the list of the past days.
                      The site builder (xau/site.py) adds head, navigation and footer.

When a session is over its view is replaced once by a recap computed on the complete prices (`final_view`).

Wording rules: no trading jargon (trend, setup, pivot, ATR, R multiples, long/short ...). An "idea" has an
entry ("entrata"), a target ("obiettivo") and a stop; gains and losses are in dollars per ounce.
"""
from __future__ import annotations

import glob
import html
import json
import math
import os
import re

import pandas as pd

from . import engine, extras, report
from .charts import GIORNI as GIORNI_S, MESI as MESI_S, fnum, nice_step
from .engine import ROME

VERSION = 1
HALT_MIN = 30             # minutes without trades after which a running session is shown as halted (holiday, early close)
NAME_RE = re.compile(r"^XAUUSD_briefing_(\d{4}-\d{2}-\d{2})\.html$")
LIVE_STATES = ("in attesa", "aperto")


def E(s) -> str:
    return html.escape(str(s), quote=True)


# ----------------------------------------------------------------------------- small helpers
def px(x) -> str:
    """Price with one decimal (decimal comma, no thousands separator: as on the trading platforms)."""
    return fnum(x, 1)


def usd(x) -> str:
    """Unsigned amount in dollars: whole dollars from 10 up, one decimal below."""
    x = abs(float(x))
    return fnum(x, 0) if x >= 9.95 else fnum(x, 1)


def susd(x) -> str:
    """Signed amount in dollars ('+39', '−4,5')."""
    x = float(x)
    return fnum(x, 0 if abs(x) >= 9.95 else 1, True)


def spct(x, d=1) -> str:
    return fnum(100 * x, d, True) + "%"


def _ts(iso) -> pd.Timestamp:
    return pd.Timestamp(iso).tz_convert(ROME)


def _iso(ts):
    return None if ts is None else pd.Timestamp(ts).isoformat()


def _f(x, d=1):
    """JSON-safe rounded float (None for missing / NaN)."""
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) or math.isinf(x) else round(x, d)


def cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def weekday(ts: pd.Timestamp) -> str:
    return report.GIORNI_L[ts.day_of_week]


def day_name(ts: pd.Timestamp, year: bool = False) -> str:
    """'venerdì 27 novembre' (optionally with the year)."""
    return f"{weekday(ts)} {ts.day} {report.MESI_L[ts.month - 1]}" + (f" {ts.year}" if year else "")


def day_short(ts: pd.Timestamp) -> str:
    return f"{GIORNI_S[ts.day_of_week]} {ts.day:02d}/{ts.month:02d}"


def trend_level(score: float) -> int:
    """-2 strong fall … +2 strong rise, with the same thresholds as the labels of the engine."""
    s, m = engine.CFG["bias_strong"], engine.CFG["bias_mild"]
    return 2 if score >= s else (1 if score >= m else (-2 if score <= -s else (-1 if score <= -m else 0)))


TREND_WORD = {2: "In forte salita", 1: "In salita", 0: "Incerta", -1: "In discesa", -2: "In forte discesa"}


# ----------------------------------------------------------------------------- plain names of the appointments
FED = "banca centrale americana (Fed)"
EV_PLAIN = [
    (("Federal Funds Rate", "FOMC Statement"), f"La {FED} decide sui tassi d'interesse"),
    (("FOMC Press Conference",), f"Conferenza stampa della {FED}"),
    (("FOMC Meeting Minutes",), f"Resoconto dell'ultima riunione della {FED}"),
    (("FOMC Economic Projections",), f"Previsioni della {FED}"),
    (("Jackson Hole",), "Incontro annuale dei banchieri centrali (Jackson Hole)"),
    (("PCE Price Index",), "Inflazione negli Stati Uniti (il dato più seguito dalla Fed)"),
    (("CPI",), "Inflazione negli Stati Uniti"),
    (("PPI",), "Prezzi all'ingrosso negli Stati Uniti"),
    (("Inflation Expectations",), "Inflazione attesa dalle famiglie americane"),
    (("ADP",), "Nuovi posti di lavoro nelle aziende private americane"),
    (("Non-Farm Employment Change",), "Nuovi posti di lavoro negli Stati Uniti"),
    (("Unemployment Rate",), "Disoccupazione negli Stati Uniti"),
    (("Unemployment Claims",), "Richieste di sussidio di disoccupazione negli Stati Uniti"),
    (("Average Hourly Earnings",), "Stipendi negli Stati Uniti"),
    (("Employment Cost Index", "Unit Labor Costs", "Nonfarm Productivity"), "Costo del lavoro negli Stati Uniti"),
    (("JOLTS",), "Offerte di lavoro negli Stati Uniti"),
    (("Challenger",), "Licenziamenti annunciati negli Stati Uniti"),
    (("Retail Sales",), "Vendite nei negozi americani"),
    (("GDP",), "Crescita dell'economia americana (PIL)"),
    (("ISM Manufacturing",), "Stato di salute delle fabbriche americane"),
    (("ISM Services",), "Stato di salute delle aziende di servizi americane"),
    (("PMI",), "Stato di salute delle aziende americane"),
    (("Consumer Sentiment", "Consumer Confidence"), "Fiducia delle famiglie americane"),
    (("Philly Fed", "Empire State", "Richmond Manufacturing"), "Attività delle fabbriche in una regione degli Stati Uniti"),
    (("Industrial Production", "Capacity Utilization", "Factory Orders"), "Produzione delle fabbriche americane"),
    (("Durable Goods",), "Ordini di beni durevoli negli Stati Uniti"),
    (("HPI",), "Prezzi delle case negli Stati Uniti"),
    (("Home Sales", "Housing Starts", "Building Permits"), "Mercato delle case negli Stati Uniti"),
    (("Bond Auction",), "Asta di titoli di Stato americani"),
    (("Trade Balance",), "Scambi commerciali degli Stati Uniti"),
    (("Bank Holiday",), "Festa negli Stati Uniti: mercati più tranquilli"),
]


def plain_event(raw: str, fallback: str, currency: str = "USD") -> str:
    """Name of a calendar appointment in everyday words. `fallback` (the technical Italian name) is kept
    for appointments that are unknown or not American."""
    if currency != "USD":
        return fallback
    for verb in (" Speaks", " Testifies"):
        if raw.endswith(verb):
            who = raw[:-len(verb)]
            where = " al Congresso" if verb == " Testifies" else ""
            for head in ("Fed Chairman ", "Fed Chair "):
                if who.startswith(head):
                    return f"Parla{where} {who[len(head):]}, presidente della {FED}"
            for head in ("FOMC Member ", "Fed Governor "):
                if who.startswith(head):
                    return f"Parla{where} {who[len(head):]}, dirigente della {FED}"
            if who.startswith("President "):
                return f"Parla{where} {who[len('President '):]}, presidente degli Stati Uniti"
            if who.startswith("Treasury Sec "):
                return f"Parla{where} {who[len('Treasury Sec '):]}, ministro del Tesoro americano"
            return fallback
    for keys, name in EV_PLAIN:
        if any(k in raw for k in keys):
            return name
    return fallback


# ----------------------------------------------------------------------------- view (data)
def _level(kind: str, trigger, entry) -> float:
    """Price the reader has to watch: the level to break, or the entry of a pull-back idea."""
    return float(trigger if kind != "fade" and trigger is not None else entry)


def _idea_from_scenario(s: dict, official: bool, pub) -> dict:
    notes = list(s.get("cautions") or [])
    for k in ("adds_to", "against"):
        if s.get(k):
            notes.append(f"{k}:{s[k]}")
    return {"id": s["id"], "dir": int(s["dir"]), "kind": s["type"], "level": _f(_level(s["type"], s.get("trigger"), s["entry"])),
            "entry": _f(s["entry"]), "stop": _f(s["stop"]), "target": _f(s["targets"][0]["price"]) if s.get("targets") else None,
            "state": "in attesa", "official": bool(official), "now": True, "pub": _iso(pub), "entry_px": None, "entry_time": None,
            "exit_px": None, "exit_time": None, "last": None, "reach": _f(s.get("prob_touch"), 2), "notes": notes}


def _idea_from_row(r: dict, s: dict | None = None) -> dict:
    """Idea from a tracked row of the session book; `s` is the same scenario as proposed right now, if any."""
    out = {"id": r["id"], "dir": int(r["dir"]), "kind": r["type"], "level": _f(_level(r["type"], r.get("trigger"), r["entry_plan"])),
           "entry": _f(r["entry_plan"]), "stop": _f(r["stop"]), "target": _f(r["t1"]), "state": r["state"],
           "official": bool(r.get("official", True)), "now": s is not None, "pub": _iso(r.get("pub")), "entry_px": _f(r["entry_px"]),
           "entry_time": _iso(r["entry_time"]), "exit_px": _f(r["exit_px"]), "exit_time": _iso(r["exit_time"]),
           "last": _f(r.get("last")), "reach": None, "notes": []}
    if s is not None and r["state"] == "in attesa":
        fresh = _idea_from_scenario(s, out["official"], r.get("pub"))
        out["reach"], out["notes"] = fresh["reach"], fresh["notes"]
    return out


def _pts(times, closes) -> list:
    return [[int(pd.Timestamp(t).timestamp()), round(float(c), 1)] for t, c in zip(times, closes)]


def _two_days(m15: pd.DataFrame, days: list, end=None) -> dict | None:
    """15-minute closes of two consecutive sessions (chart «2 giorni»)."""
    start, split = engine.session_bounds(pd.Timestamp(days[0]))["open"], engine.session_bounds(pd.Timestamp(days[1]))["open"]
    g = m15[m15["time"] >= start]
    if end is not None:
        g = g[g["time"] < end]
    if len(g) < 4:
        return None
    return {"days": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in days], "split": int(split.timestamp()),
            "pts": _pts(g["time"], g["close"])}


def _test(replay: dict | None) -> dict | None:
    """Figures of the replay of the rules on past sessions, as far as the plain pages quote them."""
    if not replay or not replay.get("total"):
        return None
    t = replay["total"]
    return {"days": int(replay["days"]), "trades": int(t["trades"]), "win": _f(t["win"]), "avg": _f(t["avg"], 3), "se": _f(t["se"], 3)}


def view(A: dict, X: dict) -> dict:
    """Everything the plain page needs about the running (or next) session, JSON-serialisable."""
    ev = report.prepare_events(A, X.get("calendar") or [], X.get("context") or {})
    tday, asof, b, p, prev, bias = A["tday"], A["asof"], A["bounds"], A["price"], A["prev"], A["bias"]
    st, pv = X.get("plan_status"), X.get("prev_session")
    deadline = extras._deadline(tday)
    scen = {s["id"]: s for s in A["scenarios"]}
    rows = st["rows"] if st else []
    ideas = [_idea_from_row(r, scen.get(r["id"])) for r in rows]
    tracked = {r["id"] for r in rows}
    if (not A["market_open"]) or asof < deadline:                  # after the deadline no new idea can start today
        ideas += [_idea_from_scenario(s, st is None, asof) for s in A["scenarios"] if s["id"] not in tracked]
    d1, m15, h1 = A["frames"]["d1"], A["frames"]["m15"], A["frames"]["h1"]
    done = d1[d1["tday"] < tday]
    two = ([pd.Timestamp(prev["tday"]), tday] if A["today"] else
           [pd.Timestamp(done["tday"].iloc[-2]), pd.Timestamp(done["tday"].iloc[-1])])
    chart = {"g2": _two_days(m15, two)}
    first = pd.Timestamp(d1["tday"].iloc[-10]) if len(d1) >= 10 else pd.Timestamp(d1["tday"].iloc[0])
    hh = h1[h1["time"] >= engine.session_bounds(first)["open"]]
    chart["s2"] = {"pts": _pts(hh["time"], hh["close"])} if len(hh) > 4 else None
    dd = d1.tail(127)
    pts = _pts([pd.Timestamp(t).tz_localize("UTC") + pd.Timedelta(hours=12) for t in dd["tday"]], dd["close"])
    if pts and pd.Timestamp(dd["tday"].iloc[-1]) == tday:
        pts[-1][1] = round(float(p), 1)
    chart["m6"] = {"pts": pts} if len(pts) > 4 else None

    def event(x: dict) -> dict:
        return {"t": _iso(x["utc"]), "name": plain_event(x["raw"], x["title"], x.get("cur", "USD")), "rel": int(x["rel"]),
                "past": bool(x["past"])}

    tf, fl = bias["tf"], bias["flags"]
    halted = bool(A["market_open"] and (X["generated"] - asof) > pd.Timedelta(minutes=HALT_MIN))
    v = {
        "v": VERSION, "final": False, "tday": tday.strftime("%Y-%m-%d"), "asof": _iso(asof), "generated": _iso(X["generated"]),
        "market_open": bool(A["market_open"]), "halted": halted,
        "open": _iso(b["open"]), "close": _iso(b["close"]), "deadline": _iso(deadline),
        "price": _f(p), "prev_close": _f(prev["close"]), "prev_day": pd.Timestamp(prev["tday"]).strftime("%Y-%m-%d"),
        "prev_chg": _f(prev["chg"], 5), "atr": _f(A["ind"]["atr_d"]),
        "day": ({"open": _f(A["today"]["open"]), "high": _f(A["today"]["high"]), "low": _f(A["today"]["low"])} if A["today"] else None),
        "trend": {"score": _f(bias["score"], 3), "level": trend_level(bias["score"]),
                  "short": _f((tf["H1"] + tf["M15"]) / 2, 2), "long": _f(tf["D1"], 2),
                  "ran_up": bool(fl["stretched_up"] or fl["overbought_h1"]), "ran_down": bool(fl["stretched_down"] or fl["oversold_h1"])},
        "ideas": ideas,
        "events": {"available": bool(ev["available"]), "today": [event(x) for x in ev["today"] if x["rel"] >= 2],
                   "next": [event(x) for x in (ev["tomorrow"] + ev["week"]) if x["rel"] >= 2]},
        "prev": ({"day": pv["tday"].strftime("%Y-%m-%d"),
                  "ideas": [_idea_from_row(r) for r in pv["rows"] if r.get("official") or r["entry_px"] is not None]} if pv else None),
        "test": _test(X.get("replay")), "chart": chart,
    }
    v["summary"] = summary_line(v)
    return v


def final_view(out_dir: str, day: str, frames: dict, m5: pd.DataFrame, old: dict | None = None) -> dict | None:
    """Recap of a concluded session on the complete prices: where gold closed and how each idea ended.
    None when the price history at hand does not cover that session."""
    tday = pd.Timestamp(day)
    b = engine.session_bounds(tday)
    d1 = frames["d1"]
    row, before = d1[d1["tday"] == tday], d1[d1["tday"] < tday]
    if not len(row) or not len(before) or m5["time"].iloc[0] > b["open"] or m5["time"].iloc[-1] < b["close"] - pd.Timedelta(minutes=10):
        return None
    row, prev = row.iloc[0], before.iloc[-1]
    plan = extras._load_json(extras._plan_path(out_dir, tday))
    book = extras._load_json(extras._book_path(out_dir, tday))
    setups = []
    if book and book.get("setups"):
        setups = [x for x in book["setups"] if not x.get("withdrawn")]
    elif plan:
        setups = [dict(x, pub=plan["asof"], official=True) for x in plan["setups"]]
    ideas = []
    for x in setups:
        r = extras.track_setup(x, m5, pd.Timestamp(x["pub"]), extras._deadline(tday), b["close"], b["close"])
        if x.get("official") or r["entry_px"] is not None:
            ideas.append(_idea_from_row(dict(r, official=bool(x.get("official")), pub=pd.Timestamp(x["pub"]))))
    events = {"available": False, "today": [], "next": []}
    if old and old.get("events"):
        events = {"available": bool(old["events"].get("available")), "next": [],
                  "today": [dict(x, past=True) for x in old["events"].get("today", [])]}
    score = float(plan["score"]) if plan and plan.get("score") is not None else None
    v = {
        "v": VERSION, "final": True, "tday": tday.strftime("%Y-%m-%d"), "asof": _iso(b["close"]), "generated": _iso(b["close"]),
        "market_open": False, "open": _iso(b["open"]), "close": _iso(b["close"]), "deadline": _iso(extras._deadline(tday)),
        "price": _f(row["close"]), "prev_close": _f(prev["close"]), "prev_day": pd.Timestamp(prev["tday"]).strftime("%Y-%m-%d"),
        "prev_chg": None, "atr": None,
        "day": {"open": _f(row["open"]), "high": _f(row["high"]), "low": _f(row["low"])},
        "trend": ({"score": _f(score, 3), "level": trend_level(score)} if score is not None else None),
        "ideas": ideas, "events": events, "prev": None, "test": None,
        "chart": {"g2": _two_days(frames["m15"], [pd.Timestamp(prev["tday"]), tday], end=b["close"]), "s2": None, "m6": None},
    }
    v["summary"] = summary_line(v)
    return v


def legacy_view(day: str, plan: dict | None) -> dict:
    """Minimal recap of a session for which only the official plan (and maybe its results) is at hand."""
    ideas = []
    if plan:
        res = {r["id"]: r for r in plan.get("results", [])}
        for s in plan["setups"]:
            i = _idea_from_scenario(s, True, plan["asof"])
            r = res.get(s["id"])
            if r is not None:
                i.update(state=r["status"], entry_px=_f(r.get("entry_px")), exit_px=_f(r.get("exit_px")))
            elif plan.get("evaluated"):
                i["state"] = "non attivato"
            i["reach"], i["notes"] = None, []
            ideas.append(i)
    score = float(plan["score"]) if plan and plan.get("score") is not None else None
    v = {"v": 0, "final": True, "legacy": True, "pending": bool(plan) and not plan.get("evaluated"), "tday": day,
         "asof": plan["asof"] if plan else None, "generated": None,
         "market_open": False, "open": None, "close": plan.get("close") if plan else None, "deadline": None,
         "price": _f(plan["price"]) if plan and plan.get("price") is not None else None, "prev_close": None, "prev_day": None,
         "prev_chg": None, "atr": None, "day": None,
         "trend": ({"score": _f(score, 3), "level": trend_level(score)} if score is not None else None),
         "ideas": ideas, "events": {"available": False, "today": [], "next": []}, "prev": None, "test": None,
         "chart": {"g2": None, "s2": None, "m6": None}}
    v["summary"] = summary_line(v)
    return v


def view_path(out_dir: str, day: str) -> str:
    return os.path.join(out_dir, f"XAUUSD_briefing_{day}.json")


def load(out_dir: str, day: str) -> dict | None:
    v = extras._load_json(view_path(out_dir, day))
    ok = (isinstance(v, dict) and v.get("tday") == day and isinstance(v.get("ideas"), list) and v.get("price") is not None
          and isinstance(v.get("summary"), str) and isinstance(v.get("chart"), dict) and isinstance(v.get("events"), dict))
    return v if ok else None


def save(out_dir: str, v: dict) -> str:
    path = view_path(out_dir, v["tday"])
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(v, fh, ensure_ascii=False, separators=(",", ":"))
    return path


def finalize_past(out_dir: str, A: dict, m5: pd.DataFrame) -> int:
    """Write once the recap of every concluded session that has a briefing but no final view yet. Sessions
    older than the price history at hand are left as they are. Returns how many recaps were written."""
    cur, n = A["tday"].strftime("%Y-%m-%d"), 0
    for path in sorted(glob.glob(os.path.join(out_dir, "XAUUSD_briefing_*.html"))):
        m = NAME_RE.match(os.path.basename(path))
        if not m or m.group(1) >= cur:
            continue
        day = m.group(1)
        old = load(out_dir, day)
        if old and old.get("final") and old.get("v") == VERSION:
            continue
        try:
            v = final_view(out_dir, day, A["frames"], m5, old)
        except Exception:
            v = None
        if v is not None:
            save(out_dir, v)
            n += 1
    return n


# ----------------------------------------------------------------------------- wording
def idea_phrase(i: dict, short: bool = False) -> str:
    """'comprare se il prezzo scende a 3650,5' (short: 'comprare a 3650,5')."""
    buy = i["dir"] > 0
    verb = "comprare" if buy else "vendere"
    if i["kind"] == "fade":
        return f"{verb} a {px(i['entry'])}" if short else f"{verb} se il prezzo {'scende' if buy else 'sale'} a {px(i['entry'])}"
    if short:
        return f"{verb} {'sopra' if buy else 'sotto'} {px(i['level'])}"
    return f"{verb} se il prezzo {'sale sopra' if buy else 'scende sotto'} {px(i['level'])}"


FLAT = 0.5                # dollars per ounce: a smaller result counts as break-even


def idea_money(i: dict) -> tuple:
    """(risk, gain, result) in dollars per ounce. Risk and gain refer to the real entry once the idea has
    started; result is None until then (and for an old idea whose prices are not known)."""
    base = i["entry_px"] if i.get("entry_px") is not None else i["entry"]
    risk = abs(i["stop"] - base)
    gain = abs(i["target"] - base) if i.get("target") is not None else None
    res = None
    if i.get("entry_px") is not None:
        ref = i.get("exit_px") if i["state"] != "aperto" and i.get("exit_px") is not None else i.get("last")
        if ref is not None:
            res = i["dir"] * (ref - i["entry_px"])
    return risk, gain, res


def outcome(i: dict, past: bool = False) -> str:
    """'' the idea has not started (yet), 'open' it is in progress, otherwise 'win' | 'loss' | 'flat'.
    `past`: the session is over, so what was still open counts as closed at the end of the day."""
    st = i["state"]
    if i.get("entry_px") is None or st in ("in attesa", "non attivato", "annullato"):
        return ""
    if st == "aperto" and not past:
        return "open"
    if st == "stop":
        return "loss"
    if st == "target 1":
        return "win"
    res = idea_money(i)[2]
    if res is None or abs(res) < FLAT:
        return "flat"
    return "win" if res > 0 else "loss"


def idea_state(i: dict, past: bool = False) -> tuple:
    """(label, css class) of the state in everyday words. `past`: the session is over."""
    st, o = i["state"], outcome(i, past)
    if o == "open":
        return "In corso", "open"
    if o:
        if st == "stop":
            return "Chiusa in perdita", "loss"
        if st == "target 1":
            return "Obiettivo raggiunto", "win"
        label = {"win": "Chiusa in guadagno", "loss": "Chiusa in perdita", "flat": "Chiusa in pari"}[o]
        return label, {"win": "win", "loss": "loss", "flat": "off"}[o]
    if st == "in attesa" and not past:
        return "In attesa", "wait"
    if st == "annullato":
        return "Annullata", "off"
    return "Non scattata", "off"


def _in_order(ideas: list) -> list:
    """Ideas in the order they were published (letters are given in sequence: A, B, ... Z, AA, ...)."""
    return sorted(ideas, key=lambda i: (len(i["id"]), i["id"]))


def tally(ideas: list, past: bool) -> dict:
    """How the ideas that started went: counts, sum of gains and losses in dollars per ounce, and one mark
    per idea in order of entry."""
    started = [i for i in ideas if outcome(i, past)]
    started.sort(key=lambda i: (i.get("entry_time") or "", len(i["id"]), i["id"]))
    t = {"n": len(started), "win": 0, "loss": 0, "flat": 0, "open": 0, "usd": 0.0, "marks": []}
    for i in started:
        o = outcome(i, past)
        t[o] += 1
        t["marks"].append(o)
        res = idea_money(i)[2]
        if o != "open" and res is not None:
            t["usd"] += res
    return t


def tally_words(t: dict) -> str:
    """'6 idee scattate: 2 in guadagno, 4 in perdita'."""
    if not t["n"]:
        return "Nessuna idea è scattata"
    parts = [f"{t[k]} {w}" for k, w in (("win", "in guadagno"), ("loss", "in perdita"), ("flat", "in pari"), ("open", "in corso")) if t[k]]
    return ("1 idea scattata" if t["n"] == 1 else f"{t['n']} idee scattate") + ": " + ", ".join(parts)


def record(views: list) -> dict | None:
    """Scoreboard of every idea that started in the concluded sessions (`views`: recaps of the sessions)."""
    rec = {"since": None, "sessions": 0, "trades": 0, "wins": 0, "losses": 0, "flat": 0, "usd": 0.0}
    for v in sorted((x for x in views if x.get("final")), key=lambda x: x["tday"]):
        t = tally(v["ideas"], True)
        rec["since"] = rec["since"] or v["tday"]
        rec["sessions"] += 1
        rec["trades"] += t["n"]
        rec["wins"] += t["win"]
        rec["losses"] += t["loss"]
        rec["flat"] += t["flat"]
        rec["usd"] += t["usd"]
    return rec if rec["trades"] else None


def reopen_phrase(opens: pd.Timestamp) -> str:
    """When trading starts again, e.g. 'a mezzanotte tra domenica e lunedì'."""
    if opens.hour == 0 and opens.minute == 0:
        return f"a mezzanotte tra {weekday(opens - pd.Timedelta(days=1))} e {weekday(opens)}"
    return f"{weekday(opens)} alle {report.hm(opens)}"


def shown_ideas(v: dict) -> tuple:
    """(cards, waiting, over): ideas in progress or proposed right now; earlier ideas still valid but no
    longer proposed; ideas that are over."""
    order = {"aperto": 0, "in attesa": 1}
    cards = [i for i in v["ideas"] if i["state"] == "aperto" or (i["state"] == "in attesa" and i.get("now", True))]
    cards.sort(key=lambda i: (order[i["state"]], abs(i["level"] - v["price"])))
    waiting = [i for i in v["ideas"] if i["state"] == "in attesa" and not i.get("now", True)]
    over = [i for i in v["ideas"] if i["state"] not in order]
    if v.get("halted"):                 # holiday or early close: without fresh prices nothing can be proposed
        cards, waiting = [i for i in cards if i["state"] == "aperto"], []
    return cards, waiting, over


def summary_line(v: dict) -> str:
    """One plain sentence about the page (description for search engines and link previews)."""
    day = pd.Timestamp(v["tday"])
    tr = v.get("trend")
    trend = f", tendenza {TREND_WORD[tr['level']].lower()}" if tr else ""
    if v.get("final"):
        if v.get("prev_close") is None or v.get("price") is None:
            head = f"Oro (XAU/USD), {day_name(day, True)}{trend}."
        else:
            head = (f"Oro (XAU/USD), {day_name(day, True)}: chiusura a {px(v['price'])} $ "
                    f"({spct(v['price'] / v['prev_close'] - 1, 2)} sul giorno prima).")
        n = len(v["ideas"])
        if not n:
            return head + " Quel giorno nessuna idea."
        return head + (" Com'è andata l'idea del giorno." if n == 1 else f" Com'è andata con le {n} idee del giorno.")
    asof = _ts(v["asof"])
    if v.get("halted"):
        return f"Oro (XAU/USD): mercato fermo, ultimo prezzo {px(v['price'])} $ ({day_short(asof)}, {report.hm(asof)}){trend}."
    if v["market_open"]:
        head = f"Oro (XAU/USD) a {px(v['price'])} $ alle {report.hm(asof)} di {day_short(asof)}{trend}."
        lead = "Idee di oggi"
    else:
        head = f"Oro (XAU/USD): mercato chiuso, ultimo prezzo {px(v['price'])} ${trend}."
        lead = f"Idee per {weekday(day)}"
    cards = shown_ideas(v)[0]
    if not cards:
        return head + " Per ora nessuna idea in attesa."
    said = [f"idea {i['id']} in corso ({'comprato' if i['dir'] > 0 else 'venduto'} a {px(i['entry_px'])})" if i["state"] == "aperto"
            else idea_phrase(i) for i in cards[:3]]
    return f"{head} {lead}: " + "; ".join(said) + "."


# ----------------------------------------------------------------------------- page: style and script
CSS = r"""
:root{color-scheme:light dark;--bg:#f6f3ec;--card:#fff;--ink:#1c222b;--mut:#626d7f;--line:#e7e1d5;--soft:#f3efe7;--gold:#a67710;--goldbg:#fbf2d9;
--up:#0a7f58;--upbg:#e2f5ec;--down:#c62f41;--downbg:#fdeaec;--blue:#2560cf;--bluebg:#e7effd;--sh:0 1px 2px rgba(28,34,43,.05),0 8px 24px rgba(28,34,43,.06)}
@media (prefers-color-scheme:dark){:root{--bg:#0c1015;--card:#141a22;--ink:#e8edf3;--mut:#96a3b4;--line:#242d3a;--soft:#1b222c;--gold:#e3b341;--goldbg:#2a2413;
--up:#2ebd85;--upbg:#11281f;--down:#f6566b;--downbg:#2c171b;--blue:#6aaeff;--bluebg:#14243b;--sh:none}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16.5px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;-webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums}
.wrap{max-width:960px;margin:0 auto;padding:14px 18px 48px}
a{color:var(--blue)}
.up{color:var(--up)}.down{color:var(--down)}.mut{color:var(--mut)}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:20px;box-shadow:var(--sh);min-width:0}
.bar{display:flex;justify-content:space-between;align-items:center;gap:8px 14px;flex-wrap:wrap;margin:0 0 18px}
.brand{display:inline-flex;align-items:center;gap:10px;color:var(--ink);text-decoration:none;font-weight:780;font-size:18px;letter-spacing:-.01em}
.brand svg{width:30px;height:30px;flex:none}
.bar nav{display:flex;gap:2px;background:var(--card);border:1px solid var(--line);border-radius:999px;padding:3px}
.bar nav a{color:var(--mut);text-decoration:none;font-size:14.5px;font-weight:650;padding:5px 14px;border-radius:999px;white-space:nowrap}
.bar nav a.on{background:var(--ink);color:var(--bg)}
h1{font-size:15px;font-weight:600;color:var(--mut);margin:0 0 12px}
h1 b{color:var(--ink);font-weight:750}
h1.big{font-size:29px;line-height:1.2;color:var(--ink);font-weight:780;letter-spacing:-.02em;margin:6px 0 4px}
h2{font-size:21px;line-height:1.25;margin:34px 0 3px;font-weight:760;letter-spacing:-.01em}
.lead{color:var(--mut);margin:0 0 14px;font-size:15px;line-height:1.5}
.eyebrow{font-size:12.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--mut)}
.hero{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(0,1fr);gap:14px;align-items:stretch}
.hero.solo{grid-template-columns:minmax(0,1fr)}
.price{font-size:52px;line-height:1.05;font-weight:780;letter-spacing:-.025em;margin:6px 0 4px}
.price span{white-space:nowrap}
.price small{font-size:.42em;font-weight:650;color:var(--mut);margin-left:6px;letter-spacing:0;white-space:nowrap}
.chg{font-size:17px;font-weight:650}
.chg span+span{color:var(--mut);font-weight:500}
.when{font-size:13.5px;color:var(--mut);margin-top:8px;display:flex;gap:6px 10px;align-items:center;flex-wrap:wrap}
.live{display:inline-flex;align-items:center;gap:6px;padding:2px 10px;border-radius:999px;background:var(--soft);font-size:12.5px;font-weight:650;color:var(--mut);white-space:nowrap}
.live.on{background:var(--upbg);color:var(--up)}
.live.on:before{content:"";width:7px;height:7px;border-radius:50%;background:var(--up)}
.tabs{position:relative;margin-top:16px}
.tabs>input{position:absolute;opacity:0;pointer-events:none}
.seg{display:inline-flex;background:var(--soft);border-radius:999px;padding:3px;gap:2px}
.seg label{font-size:13.5px;font-weight:650;color:var(--mut);padding:5px 13px;border-radius:999px;cursor:pointer;user-select:none;white-space:nowrap}
#rg1:checked~.seg label[for=rg1],#rg2:checked~.seg label[for=rg2],#rg3:checked~.seg label[for=rg3]{background:var(--card);color:var(--ink);box-shadow:0 1px 2px rgba(0,0,0,.14)}
.tabs>input:focus-visible~.seg{outline:2px solid var(--blue);outline-offset:2px}
.pane{display:none}
#rg1:checked~.p1,#rg2:checked~.p2,#rg3:checked~.p3{display:block}
.ch{position:relative;height:230px;margin:18px 58px 28px 0}
.ch svg{position:absolute;left:0;top:0;width:100%;height:100%;overflow:visible}
.ch .ln{fill:none;stroke:var(--gold);stroke-width:2.2;vector-effect:non-scaling-stroke;stroke-linejoin:round;stroke-linecap:round}
.ch .ar{fill:var(--gold);opacity:.11;stroke:none}
.ch .gr{stroke:var(--line);stroke-width:1;vector-effect:non-scaling-stroke}
.ch .sp{stroke:var(--mut);stroke-width:1;stroke-dasharray:3 4;vector-effect:non-scaling-stroke;opacity:.55}
.ch .lv{stroke-width:1.5;stroke-dasharray:6 4;vector-effect:non-scaling-stroke;fill:none}
.ch .lv.buy{stroke:var(--up)}.ch .lv.sell{stroke:var(--down)}
.ch .y,.ch .x,.ch .dayl,.ch .now,.ch .tag{position:absolute;white-space:nowrap;line-height:1.3}
.ch .y{left:100%;margin-left:9px;transform:translateY(-50%);font-size:12px;color:var(--mut)}
.ch .x{top:100%;margin-top:7px;transform:translateX(-50%);font-size:12px;color:var(--mut)}
.ch .dayl{top:100%;margin-top:6px;transform:translateX(-50%);font-size:12px;font-weight:750;color:var(--mut);text-transform:uppercase;letter-spacing:.05em}
.ch .dot{position:absolute;width:11px;height:11px;border-radius:50%;background:var(--gold);border:2px solid var(--card);transform:translate(-50%,-50%);box-shadow:0 0 0 4px rgba(190,140,30,.2)}
.ch .now{left:100%;margin-left:8px;transform:translateY(-50%);font-size:12px;font-weight:750;color:var(--card);background:var(--gold);border-radius:6px;padding:1px 6px}
.ch .tag{left:6px;transform:translateY(-118%);font-size:11.5px;font-weight:750;padding:0 6px;border-radius:5px;background:var(--card)}
.ch .tag.below{transform:translateY(18%)}
.ch .tag.buy{color:var(--up)}.ch .tag.sell{color:var(--down)}
.cap{font-size:14px;color:var(--mut)}
.cap b{font-weight:700}
.mm{display:flex;gap:10px;margin-top:14px;flex-wrap:wrap}
.mm div{flex:1;min-width:118px;background:var(--soft);border-radius:12px;padding:8px 13px}
.mm span{display:block;font-size:12.5px;color:var(--mut);line-height:1.35}
.mm b{font-size:17px;font-weight:720}
.trend .big{display:flex;align-items:center;gap:13px;margin:10px 0 14px}
.trend .ico{width:48px;height:48px;border-radius:15px;display:grid;place-items:center;flex:none;background:var(--goldbg);color:var(--gold)}
.trend .ico svg{width:27px;height:27px}
.trend.t-up .ico{background:var(--upbg);color:var(--up)}.trend.t-down .ico{background:var(--downbg);color:var(--down)}
.trend .lab{font-size:27px;font-weight:780;line-height:1.1;letter-spacing:-.015em}
.meter{display:grid;grid-template-columns:repeat(5,1fr);gap:4px}
.meter i{height:8px;border-radius:4px;background:var(--soft)}
.meter.m-2 i:nth-child(1),.meter.m-1 i:nth-child(2){background:var(--down)}
.meter.m0 i:nth-child(3){background:var(--gold)}
.meter.m1 i:nth-child(4),.meter.m2 i:nth-child(5){background:var(--up)}
.mcap{display:flex;justify-content:space-between;font-size:12px;color:var(--mut);margin-top:5px}
.say{margin:16px 0 0;padding:0;list-style:none;font-size:15.5px;line-height:1.5}
.say li{position:relative;padding-left:19px;margin:0 0 9px}
.say li:last-child{margin:0}
.say li:before{content:"";position:absolute;left:3px;top:.6em;width:6px;height:6px;border-radius:50%;background:var(--gold)}
.ideas{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:14px}
.ideas.one{grid-template-columns:minmax(0,560px)}
.idea{position:relative;overflow:hidden;padding-left:24px}
.idea:before{content:"";position:absolute;left:0;top:0;bottom:0;width:6px;background:var(--line)}
.idea.buy:before{background:var(--up)}.idea.sell:before{background:var(--down)}
.ih{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:10px}
.tagv{display:inline-block;font-size:13px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;padding:4px 12px;border-radius:999px}
.tagv.buy{background:var(--upbg);color:var(--up)}.tagv.sell{background:var(--downbg);color:var(--down)}
.lett{font-size:13px;font-weight:650;color:var(--mut);margin-left:9px;white-space:nowrap}
.st{display:inline-block;font-size:12.5px;font-weight:700;padding:3px 10px;border-radius:999px;white-space:nowrap;background:var(--soft);color:var(--mut)}
.st.open{background:var(--bluebg);color:var(--blue)}.st.win{background:var(--upbg);color:var(--up)}.st.loss{background:var(--downbg);color:var(--down)}
.cond{font-size:19.5px;font-weight:720;line-height:1.3;margin:0 0 4px;letter-spacing:-.005em}
.sub{font-size:14.5px;color:var(--mut);margin:0 0 13px;line-height:1.45}
.sub b{font-weight:700}
.rows{display:grid;gap:6px}
.row{display:grid;grid-template-columns:minmax(84px,auto) 1fr auto;align-items:baseline;gap:10px;padding:8px 13px;border-radius:12px;background:var(--soft)}
.row span{font-size:14.5px;color:var(--mut)}
.row b{font-size:18px;font-weight:740}
.row em{font-style:normal;font-size:14.5px;font-weight:700;white-space:nowrap}
.rrw{position:relative;margin:14px 0 5px}
.rr{display:flex;height:10px;border-radius:5px;overflow:hidden;gap:2px}
.rr i{display:block;background:var(--down)}.rr b{display:block;background:var(--up);flex:1}
.pin{position:absolute;top:-5px;width:4px;height:20px;border-radius:2px;background:var(--ink);transform:translateX(-50%);box-shadow:0 0 0 2px var(--card)}
.rrcap{display:flex;justify-content:space-between;gap:10px;font-size:13.5px;color:var(--mut)}
.rrcap b{font-weight:700}
.note{font-size:14px;line-height:1.45;background:var(--goldbg);border-radius:12px;padding:9px 13px;margin-top:11px}
.note b{font-weight:700}
.empty{color:var(--mut)}
.empty b{color:var(--ink)}
.done{padding:6px 20px}
.drow{display:grid;grid-template-columns:30px minmax(0,1fr) auto;gap:2px 12px;align-items:center;padding:12px 0;border-top:1px solid var(--line)}
.drow:first-child{border-top:0}
.drow .l{width:30px;height:30px;border-radius:9px;background:var(--soft);display:grid;place-items:center;font-weight:800;font-size:13.5px;color:var(--mut)}
.drow .w{font-weight:680;line-height:1.35}
.drow .r{white-space:nowrap}
.drow .r b{margin-left:6px}
.drow .d{grid-column:2/-1;font-size:13.5px;color:var(--mut);line-height:1.45}
.h3{font-size:13px;font-weight:750;letter-spacing:.05em;text-transform:uppercase;color:var(--mut);margin:22px 0 8px}
.howto{margin:14px 0 0;padding:0 0 0 20px;font-size:14.5px;color:var(--mut);line-height:1.5}
.howto li{margin:0 0 5px}
.howto b{color:var(--ink);font-weight:650}
.evs{padding:6px 20px}
.ev{display:grid;grid-template-columns:62px minmax(0,1fr);gap:12px;align-items:start;padding:13px 0;border-top:1px solid var(--line)}
.ev:first-child{border-top:0}
.ev .t{font-size:19px;font-weight:760;line-height:1.3}
.ev .t small{display:block;font-size:12px;font-weight:600;color:var(--mut)}
.ev .n{font-weight:650;line-height:1.4}
.ev .i{font-size:13.5px;color:var(--mut);line-height:1.45;margin-top:2px}
.imp{display:inline-block;font-size:11.5px;font-weight:750;padding:1px 8px;border-radius:999px;margin-right:7px;background:var(--goldbg);color:var(--gold);white-space:nowrap}
.imp.hi{background:var(--downbg);color:var(--down)}
.ev.past{opacity:.55}
.score{display:flex;height:14px;border-radius:7px;overflow:hidden;gap:2px;margin:14px 0 8px;background:var(--soft)}
.score i{display:block;background:var(--up)}.score b{display:block;background:var(--down)}.score u{display:block;background:var(--mut);opacity:.5}
.legend{display:flex;gap:6px 16px;flex-wrap:wrap;font-size:14.5px}
.legend span:before{content:"";display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:7px;background:var(--mut)}
.legend .w:before{background:var(--up)}.legend .l:before{background:var(--down)}
.fine{font-size:14px;color:var(--mut);line-height:1.5;margin:12px 0 0}
.fine b{color:var(--ink);font-weight:650}
details.gl{margin-top:30px}
details.gl summary{cursor:pointer;font-weight:740;font-size:17.5px;list-style:none;display:flex;justify-content:space-between;align-items:center;gap:10px}
details.gl summary::-webkit-details-marker{display:none}
details.gl summary:after{content:"+";font-size:24px;line-height:1;color:var(--mut);font-weight:500}
details.gl[open] summary:after{content:"\2212"}
dl.gls{margin:14px 0 0;display:grid;gap:12px}
dl.gls dt{font-weight:720}
dl.gls dd{margin:0;color:var(--mut);font-size:15px;line-height:1.5}
.warnbox{margin-top:16px;border-radius:18px;padding:16px 20px;background:var(--soft);border:1px solid var(--line);font-size:14.5px;line-height:1.55;color:var(--mut)}
.warnbox b{color:var(--ink)}
.banner{display:none;margin:0 0 14px;padding:11px 16px;border-radius:14px;background:var(--goldbg);border:1px solid var(--gold);font-size:14.5px;line-height:1.45}
.pastnote{display:flex;justify-content:space-between;align-items:center;gap:6px 16px;flex-wrap:wrap;margin:0 0 14px;padding:11px 16px;border-radius:14px;background:var(--bluebg);font-size:14.5px;line-height:1.45}
.pastnote a{font-weight:700;text-decoration:none;white-space:nowrap}
.more{display:inline-block;margin-top:16px;font-weight:700;text-decoration:none}
.stack{display:grid;gap:10px}
.fh{display:inline-flex;align-items:center;gap:6px 12px;flex-wrap:wrap;min-width:0}
.dots{display:inline-flex;gap:4px;align-items:center;flex-wrap:wrap}
.dots i{width:11px;height:11px;border-radius:50%;background:var(--mut);opacity:.9}
.dots i.w{background:var(--up)}.dots i.l{background:var(--down)}.dots i.o{background:var(--blue)}
.dots em{font-style:normal;font-size:12px;color:var(--mut);font-weight:650}
.tot{font-weight:760;white-space:nowrap}
details.fold{padding:0}
details.fold summary{cursor:pointer;list-style:none;display:grid;grid-template-columns:minmax(0,1fr) 22px;gap:3px 12px;align-items:center;padding:15px 20px}
details.fold summary::-webkit-details-marker{display:none}
details.fold summary:after{content:"";grid-column:2;grid-row:1/3;width:9px;height:9px;border-right:2px solid var(--mut);border-bottom:2px solid var(--mut);transform:rotate(45deg);justify-self:center;margin-top:-5px;transition:transform .15s}
details.fold[open] summary:after{transform:rotate(-135deg);margin-top:5px}
details.fold .fh b{font-size:17px;font-weight:740}
details.fold .fs{grid-column:1;display:flex;justify-content:space-between;gap:4px 12px;flex-wrap:wrap;font-size:14.5px;color:var(--mut)}
details.fold .frows{padding:0 20px 6px;border-top:1px solid var(--line)}
.card.sum{display:flex;justify-content:space-between;align-items:flex-start;gap:6px 14px;padding:14px 20px}
.card.sum b{font-size:16.5px}
.big2{font-size:17.5px;margin:8px 0 0;line-height:1.4}
.days{display:grid;gap:10px}
a.dayc{display:block;color:inherit;text-decoration:none;padding:15px 20px}
a.dayc:hover{border-color:var(--gold)}
.dh{display:flex;justify-content:space-between;align-items:baseline;gap:4px 10px;flex-wrap:wrap}
.dh b{display:inline-block;font-size:17px;font-weight:740}
.dh b:first-letter{text-transform:uppercase}
.dh .c{font-size:14.5px;font-weight:700;white-space:nowrap}
.dh .tg{font-size:11.5px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:var(--gold);background:var(--goldbg);border-radius:999px;padding:1px 9px;margin-left:9px;vertical-align:2px;white-space:nowrap}
.di{display:flex;justify-content:space-between;align-items:flex-start;gap:4px 12px;margin-top:7px;font-size:14.5px;color:var(--mut)}
.di .fh,.card.sum .fh{flex:1}
.foot{margin-top:30px;padding-top:18px;border-top:1px solid var(--line);color:var(--mut);font-size:13.5px;line-height:1.55}
.foot p{margin:9px 0 0}
.foot b{color:var(--ink);font-weight:650}
.pager{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px}
.pager a{color:var(--ink);text-decoration:none;border:1px solid var(--line);background:var(--card);border-radius:999px;padding:6px 15px;font-size:14px;font-weight:600;white-space:nowrap}
.pager a:hover{border-color:var(--gold)}
.upd{display:none;position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:60;align-items:center;gap:12px;width:max-content;max-width:calc(100vw - 24px);padding:10px 12px 10px 18px;border-radius:16px;background:var(--ink);color:var(--bg);font-size:14.5px;line-height:1.4;box-shadow:0 10px 34px rgba(0,0,0,.35)}
.upd button{flex:none;background:var(--gold);color:#fff;border:0;border-radius:999px;padding:7px 16px;font:inherit;font-weight:750;font-size:14px;cursor:pointer}
@media (max-width:820px){.hero{grid-template-columns:minmax(0,1fr)}}
@media (max-width:560px){body{font-size:16px}.wrap{padding:12px 12px 40px}.card{padding:16px;border-radius:16px}.idea{padding-left:21px}.price{font-size:44px}
.ch{height:200px;margin-right:52px}.ch .x.opt{display:none}h2{font-size:20px;margin-top:30px}.done,.evs{padding:4px 16px}a.dayc,details.fold summary,.card.sum{padding:14px 16px}details.fold .frows{padding:0 16px 4px}.trend .lab{font-size:25px}
.seg label{padding:5px 11px}.drow{grid-template-columns:30px minmax(0,1fr)}.drow .r{grid-column:2}.brand{font-size:17px}.bar nav a{padding:5px 12px}h1.big{font-size:26px}}
@media print{body{background:#fff}.card{box-shadow:none}.banner,.seg,.upd,.bar nav,.pager{display:none!important}}
"""

JS = r"""
(function(){
  var D=JSON.parse(document.getElementById('oro-data').textContent);
  function f(x,d){ var s=Math.abs(x).toFixed(d).replace('.',','); return (x<0&&parseFloat(Math.abs(x).toFixed(d))!==0?'\u2212':'')+s; }
  function two(n){ return ('0'+n).slice(-2); }
  function hm(t){ try{ return new Intl.DateTimeFormat('it-IT',{timeZone:'Europe/Rome',hour:'2-digit',minute:'2-digit',hour12:false}).format(t); }
    catch(e){ return two(t.getHours())+':'+two(t.getMinutes()); } }                 /* always Italian time, wherever the reader is */
  function $(id){ return document.getElementById(id); }
  if(!D.live) return;
  /* notice when the page is no longer fresh (the automatic update is late, or the session is over) */
  function stale(){ var b=$('banner'), n=Date.now(), msg='', old=n>D.gen_ms+90*60000; if(!b) return;
    if(D.halted&&!old) msg='';                               /* holiday or early close: the page already says that the market is halted */
    else if(n>D.close_ms+45*60000) msg='<b>Questa pagina parla di una giornata già finita.</b> Prova a ricaricarla: se non cambia, il nuovo aggiornamento sta per arrivare.';
    else if(D.market_open&&old) msg='<b>Attenzione: questa pagina è ferma alle '+hm(new Date(D.gen_ms))+'.</b> Le idee potrebbero non valere più. Prova a ricaricarla.';
    else if(!D.market_open&&n>D.open_ms+90*60000) msg='<b>Il mercato ha riaperto.</b> Questa pagina è stata preparata a mercato chiuso: prova a ricaricarla.';
    b.innerHTML=msg; b.style.display=msg?'block':'none'; }
  stale(); setInterval(stale,60000);
  /* live price in the header (needs a normal browser with network access) */
  function setPrice(p){ var a=$('px'), c=$('pxchg'), s=$('pxsince'), l=$('pxlabel'); if(!a) return; a.textContent=f(p,1);
    var d=p-D.prev_close; if(c){ c.textContent=(d>=0?'\u25B2 +':'\u25BC ')+f(d,1)+' $ ('+(d>=0?'+':'')+f(100*d/D.prev_close,2)+'%)'; c.className=d>=0?'up':'down'; }
    if(s) s.textContent=D.since; if(l) l.textContent='Prezzo dell\u2019oro adesso'; }
  function live(){ var n=Date.now(); if(D.halted||n>D.close_ms||n<D.open_ms||!(D.prev_close>0)) return;
    fetch('https://api.gold-api.com/price/XAU',{cache:'no-store'}).then(function(r){return r.json();}).then(function(j){ if(j&&j.price>0){ setPrice(+j.price);
      var b=$('livebadge'); if(b){ b.className='live on'; b.textContent='in diretta · '+hm(new Date()); } } }).catch(function(){}); }
  live(); setInterval(live,60000);
})();
"""


# ----------------------------------------------------------------------------- page: chart
def _chart(pts: list, kind: str, levels=(), split=None, day_labels=None, now=True) -> str:
    """Line chart. The SVG holds only the shapes (stretched to the box); every label is HTML, so the text
    stays readable on any screen. kind: g2 = two sessions (15-minute closes), s2 = hourly, m6 = daily."""
    n = len(pts)
    if n < 4:
        return ""
    cs = [c for _, c in pts]
    lo, hi = min(cs), max(cs)
    for lv in levels:
        lo, hi = min(lo, lv["price"]), max(hi, lv["price"])
    pad = max(hi - lo, 1.0) * 0.11
    lo, hi = lo - pad, hi + pad
    X = lambda i: 100.0 * i / (n - 1)
    Y = lambda c: 100.0 * (hi - c) / (hi - lo)
    line = " L".join(f"{10 * X(i):.1f} {10 * Y(c):.1f}" for i, c in enumerate(cs))
    step = nice_step(hi - lo, 6)
    ticks = [k * step for k in range(math.ceil(lo / step), math.floor(hi / step) + 1)]
    svg = [f"<line class='gr' x1='0' x2='1000' y1='{10 * Y(t):.1f}' y2='{10 * Y(t):.1f}'/>" for t in ticks]
    out = []
    y_now = Y(cs[-1])
    for t in ticks:
        if not (now and abs(Y(t) - y_now) < 8.5) and 3 < Y(t) < 97:
            out.append(f"<span class='y' style='top:{Y(t):.1f}%'>{fnum(t, 0 if step >= 1 else 1)}</span>")
    times = [pd.Timestamp(t, unit="s", tz="UTC").tz_convert(ROME) for t, _ in pts]
    marks = []                                                     # (position 0-100, text, css class)
    if kind == "g2":
        i_split = next((i for i, (t, _) in enumerate(pts) if split is not None and t >= split), None)
        if i_split is not None and 0 < i_split < n - 1:
            svg.append(f"<line class='sp' x1='{10 * X(i_split):.1f}' x2='{10 * X(i_split):.1f}' y1='0' y2='1000'/>")
        if day_labels and i_split:                                 # the name of each day under its half
            marks.append((X(i_split) / 2, day_labels[0], "dayl"))
            if X(i_split) <= 88:
                marks.append(((X(i_split) + 100) / 2, day_labels[1], "dayl"))
        elif day_labels:
            marks.append((50, day_labels[-1], "dayl"))
    elif kind == "s2":
        days = [engine.tday_of(pd.Timestamp(t, unit="s", tz="UTC")) for t, _ in pts]
        k = 0
        for i in range(1, n):
            if days[i] != days[i - 1]:
                marks.append((X(i), f"{GIORNI_S[days[i].day_of_week]} {days[i].day}", "x opt" if k % 2 else "x"))
                k += 1
    else:
        for i in range(1, n):
            if times[i].month != times[i - 1].month:
                marks.append((X(i), MESI_S[times[i].month - 1], "x"))
    last = -100.0
    for x, text, cls in marks:
        if cls != "dayl" and (x < 4 or x > 95 or x - last < 9):
            continue
        last = x
        out.append(f"<span class='{cls}' style='left:{x:.1f}%'>{E(text)}</span>")
    lvs = sorted(levels, key=lambda d: -d["price"])
    for j, lv in enumerate(lvs):
        y = Y(lv["price"])
        svg.append(f"<line class='lv {lv['cls']}' x1='0' x2='1000' y1='{10 * y:.1f}' y2='{10 * y:.1f}'/>")
        below = y < 12 or (j > 0 and y - Y(lvs[j - 1]["price"]) < 13 and y < 86)
        out.append(f"<span class='tag {lv['cls']}{' below' if below else ''}' style='top:{y:.1f}%'>{E(lv['text'])}</span>")
    svg.append(f"<path class='ar' d='M{line} L1000 1000 L0 1000 Z'/><path class='ln' d='M{line}'/>")
    if now:
        out.append(f"<i class='dot' style='left:100%;top:{y_now:.1f}%'></i><span class='now' style='top:{y_now:.1f}%'>{px(cs[-1])}</span>")
    return (f"<div class='ch'><svg viewBox='0 0 1000 1000' preserveAspectRatio='none' aria-hidden='true'>{''.join(svg)}</svg>"
            f"{''.join(out)}</div>")


def _chg_cap(pts: list, text: str) -> str:
    c = pts[-1][1] / pts[0][1] - 1
    return f"<div class='cap'>{text}: <b class='{'up' if c >= 0 else 'down'}'>{spct(c)}</b></div>"


def _arrow(level: int) -> str:
    rot = (55, 30, 0, -30, -55)[level + 2]
    return (f"<svg viewBox='0 0 24 24' aria-hidden='true'><g transform='rotate({rot} 12 12)'><path d='M3.5 12h15M12.5 6l6 6-6 6' "
            f"fill='none' stroke='currentColor' stroke-width='2.7' stroke-linecap='round' stroke-linejoin='round'/></g></svg>")


# ----------------------------------------------------------------------------- page: sections
def _is_today(v: dict, past: bool) -> bool:
    return not past and v["market_open"]


def _day_labels(v: dict, past: bool) -> list:
    g = v["chart"]["g2"]
    a, b = pd.Timestamp(g["days"][0]), pd.Timestamp(g["days"][1])
    if _is_today(v, past) and g["days"][1] == v["tday"]:
        return ["ieri" if (b - a).days == 1 else weekday(a), "oggi"]
    return [weekday(a), weekday(b)]


def _since(v: dict) -> str:
    a, b = pd.Timestamp(v["prev_day"]), pd.Timestamp(v["tday"])
    return "rispetto a ieri" if (b - a).days == 1 else f"rispetto a {weekday(a)}"


def _chart_levels(v: dict) -> list:
    """Entry levels of the ideas shown as cards (at most three, the nearest ones) for the two-day chart."""
    pts = v["chart"]["g2"]["pts"]
    lo, hi = min(c for _, c in pts), max(c for _, c in pts)
    room = 0.6 * max(hi - lo, 1.0)
    cards = shown_ideas(v)[0]
    near = sorted([i for i in cards if lo - room <= i["level"] <= hi + room], key=lambda i: abs(i["level"] - v["price"]))[:3]
    out = []
    for i in near:
        buy = i["dir"] > 0
        if i["state"] == "aperto":
            text, price = f"{i['id']} · {'comprato' if buy else 'venduto'} a {px(i['entry_px'])}", i["entry_px"]
        else:
            where = "a" if i["kind"] == "fade" else ("sopra" if buy else "sotto")
            text, price = f"{i['id']} · {'compra' if buy else 'vendi'} {where} {px(i['level'])}", i["level"]
        out.append({"price": price, "cls": "buy" if buy else "sell", "text": text})
    return out


def _hero(v: dict, past: bool) -> str:
    p = v["price"]
    mm = ""
    if past:
        chg = p - v["prev_close"]
        label = "Chiusura della giornata" if v.get("final") else "Ultimo prezzo della giornata"
        head = (f"<div class='eyebrow'>{label}</div><div class='price'><span>{px(p)}</span><small>$ per oncia</small></div>"
                f"<div class='chg'><span class='{'up' if chg >= 0 else 'down'}'>{'▲' if chg >= 0 else '▼'} {fnum(chg, 1, True)} $ "
                f"({spct(chg / v['prev_close'], 2)})</span> <span>rispetto al giorno prima</span></div>")
        lo_l, hi_l = "Minimo della giornata", "Massimo della giornata"
    else:
        asof = _ts(v["asof"])
        if v["market_open"]:
            chg, base, label, since = p - v["prev_close"], v["prev_close"], "Prezzo dell’oro adesso", _since(v)
            badge = "aggiornato alle " + report.hm(asof)
            when = f"Idee e grafico aggiornati alle {report.hm(asof)} di {day_name(asof)} (ora italiana)"
            if v.get("halted"):
                label, badge = "Ultimo prezzo dell’oro", "mercato fermo"
                when = f"Nessuno scambio dalle {report.hm(asof)} di {day_name(asof)} (ora italiana)"
        else:                           # nothing has traded since the last close: show the change of that session
            base = v["prev_close"] / (1 + v["prev_chg"]) if v.get("prev_chg") is not None else v["prev_close"]
            chg, label, badge = v["prev_close"] - base, "Ultimo prezzo dell’oro", "mercato chiuso"
            since = f"nella giornata di {weekday(pd.Timestamp(v['prev_day']))}"
            when = f"Riapre {reopen_phrase(_ts(v['open']))} (ora italiana)"
        head = (f"<div class='eyebrow' id='pxlabel'>{label}</div><div class='price'><span id='px'>{px(p)}</span><small>$ per oncia</small></div>"
                f"<div class='chg'><span class='{'up' if chg >= 0 else 'down'}' id='pxchg'>{'▲' if chg >= 0 else '▼'} {fnum(chg, 1, True)} $ "
                f"({spct(chg / base, 2)})</span> <span id='pxsince'>{E(since)}</span></div>"
                f"<div class='when'><span class='live' id='livebadge'>{E(badge)}</span><span>{E(when)}</span></div>")
        lo_l, hi_l = "Minimo di oggi", "Massimo di oggi"
    if v.get("day"):
        mm = (f"<div class='mm'><div><span>{lo_l}</span><b>{px(v['day']['low'])}</b></div>"
              f"<div><span>{hi_l}</span><b>{px(v['day']['high'])}</b></div></div>")
    ch = v["chart"]
    panes = ""
    if ch.get("g2"):
        g = ch["g2"]
        tabs = [("2 giorni", _chart(g["pts"], "g2", levels=[] if past else _chart_levels(v), split=g["split"],
                                    day_labels=_day_labels(v, past)) + _chg_cap(g["pts"], "In questi due giorni"))]
        if ch.get("s2"):
            tabs.append(("2 settimane", _chart(ch["s2"]["pts"], "s2") + _chg_cap(ch["s2"]["pts"], "Nelle ultime due settimane")))
        if ch.get("m6"):
            tabs.append(("6 mesi", _chart(ch["m6"]["pts"], "m6") + _chg_cap(ch["m6"]["pts"], "Negli ultimi sei mesi")))
        if len(tabs) == 1:
            panes = f"<div class='tabs'>{tabs[0][1]}</div>"
        else:
            inputs = "".join(f"<input type='radio' name='rg' id='rg{k + 1}'{' checked' if k == 0 else ''}>" for k in range(len(tabs)))
            labels = "".join(f"<label for='rg{k + 1}'>{name}</label>" for k, (name, _) in enumerate(tabs))
            boxes = "".join(f"<div class='pane p{k + 1}'>{box}</div>" for k, (_, box) in enumerate(tabs))
            panes = f"<div class='tabs'>{inputs}<div class='seg' role='group' aria-label='Periodo del grafico'>{labels}</div>{boxes}</div>"
    return f"<div class='card'>{head}{panes}{mm}</div>"


def _trend_sentences(v: dict) -> list:
    out = []
    tr, p = v["trend"], v["price"]
    if v.get("halted"):
        asof = _ts(v["asof"])
        out.append(f"<b>Il mercato è fermo</b>: nessun prezzo nuovo dalle {report.hm(asof)} di {weekday(asof)}. Succede nei giorni di festa.")
    elif v["market_open"]:
        chg = p - v["prev_close"]
        r = abs(chg) / v["atr"] if v.get("atr") else 0.5
        since = _since(v)
        if r < 0.08:
            out.append(f"Oggi l'oro è <b>quasi fermo</b> {since}.")
        else:
            size = " un po'" if r < 0.35 else ("" if r < 0.9 else " molto")
            out.append(f"Oggi l'oro <b>{'sale' if chg > 0 else 'scende'}{size}</b> {since}.")
    else:
        out.append(f"<b>Il mercato è chiuso</b>: riapre {reopen_phrase(_ts(v['open']))}.")
    w = lambda x: 1 if x >= 0.25 else (-1 if x <= -0.25 else 0)
    s, l = w(tr.get("short") or 0), w(tr.get("long") or 0)
    if s == l and s != 0:
        out.append(f"{'Sale' if s > 0 else 'Scende'} sia nelle ultime ore sia negli ultimi giorni.")
    else:
        hours = ("il prezzo non ha una direzione chiara", "il prezzo sale", "il prezzo scende")[s]
        days = ("non ha avuto una direzione chiara", "è salito", "è sceso")[l]
        out.append(f"Nelle ultime ore {hours}{', ma' if s * l < 0 else ';'} negli ultimi giorni {days}.")
    if tr.get("ran_up"):
        out.append("Ha corso molto in poco tempo: dopo salite così rapide può tornare un po' indietro.")
    elif tr.get("ran_down"):
        out.append("È sceso molto in poco tempo: dopo discese così rapide può rimbalzare un po'.")
    if not v["market_open"]:
        out.append("Le idee qui sotto valgono dalla riapertura. Se il prezzo riparte lontano da questi livelli, non valgono più.")
    elif not v.get("halted"):
        nxt = [x for x in v["events"]["today"] if not x["past"]]
        if nxt:
            out.append(f"Alle <b>{report.hm(_ts(nxt[0]['t']))}</b> c'è un appuntamento importante: in quei minuti il prezzo può muoversi di colpo.")
    return out


def _trend(v: dict, past: bool) -> str:
    tr = v.get("trend")
    if not tr:
        return ""
    lvl = tr["level"]
    cls = "t-up" if lvl > 0 else ("t-down" if lvl < 0 else "")
    if past:
        head = "Tendenza di quel mattino"
        said = ["È la direzione che aveva il prezzo quando sono state preparate le prime idee del giorno."]
        chg = day_change(v)
        if chg is not None and v.get("final"):
            if abs(chg) < 0.001:
                said.append("Alla fine l'oro ha chiuso <b>quasi fermo</b>.")
            else:
                way = "salita" if chg > 0 else "discesa"
                lead = "Alla fine" if lvl == 0 else ("La giornata l'ha confermata:" if (lvl > 0) == (chg > 0) else "La giornata è andata al contrario:")
                said.append(f"{lead} l'oro ha chiuso <b>in {way}</b>.")
            if v.get("day"):
                said.append(f"Tra il minimo e il massimo della giornata: <b>{usd(v['day']['high'] - v['day']['low'])} $</b> di differenza.")
        tail = "<ul class='say'>" + "".join(f"<li>{x}</li>" for x in said) + "</ul>"
    else:
        head = "Tendenza"
        tail = "<ul class='say'>" + "".join(f"<li>{x}</li>" for x in _trend_sentences(v)) + "</ul>"
    return (f"<div class='card trend {cls}'><div class='eyebrow'>{head}</div><div class='big'><div class='ico'>{_arrow(lvl)}</div>"
            f"<div class='lab'>{TREND_WORD[lvl]}</div></div><div class='meter m{lvl}' role='img' aria-label='Tendenza: {TREND_WORD[lvl].lower()}'>"
            f"<i></i><i></i><i></i><i></i><i></i></div><div class='mcap'><span>scende</span><span>incerta</span><span>sale</span></div>{tail}</div>")


def _notes(i: dict) -> str:
    out, notes = [], i.get("notes") or []
    buy = i["dir"] > 0
    for n in notes:
        if n == "stretched":
            out.append(f"<b>Attenzione:</b> il prezzo è già {'salito' if buy else 'sceso'} molto. {'Comprare' if buy else 'Vendere'} "
                       f"adesso, «in corsa», è più rischioso del solito.")
        elif n == "adr" and "stretched" not in notes:
            out.append("Oggi il prezzo si è già mosso più del solito: potrebbe restargli poca strada.")
        elif n.startswith("adds_to:"):
            out.append(f"L'idea {E(n[8:])} è già in corso nella stessa direzione: seguirle tutte e due raddoppia il rischio.")
        elif n.startswith("against:"):
            out.append(f"Va nella direzione opposta all'idea {E(n[8:])}, che è ancora in corso.")
    return "".join(f"<div class='note'>{x}</div>" for x in out[:2])


def _rr_bar(risk: float, gain, pin=None) -> str:
    """Red and green bar: what is lost at the stop against what is gained at the target."""
    if not gain or gain <= 0 or risk <= 0:
        return ""
    w = min(max(100 * risk / (risk + gain), 12), 88)
    mark = f"<span class='pin' style='left:{min(max(pin, 0), 1) * 100:.1f}%' title='Il prezzo è qui'></span>" if pin is not None else ""
    return (f"<div class='rrw'><div class='rr'><i style='width:{w:.1f}%'></i><b></b></div>{mark}</div>"
            f"<div class='rrcap'><span>se va male <b class='down'>−{usd(risk)} $</b></span><span>se va bene <b class='up'>+{usd(gain)} $</b></span></div>")


def _idea_card(i: dict, v: dict) -> str:
    buy = i["dir"] > 0
    side = "buy" if buy else "sell"
    label, cls = idea_state(i)
    risk, gain, res = idea_money(i)
    phrase = idea_phrase(i)
    cond = cap(phrase[phrase.index(" ") + 1:])                    # 'Se il prezzo scende a 3650,5'
    p = v["price"]
    pin = None
    if i["state"] == "aperto":
        res = res or 0.0
        verb = "Comprato" if buy else "Venduto"
        cond = f"{verb} a {px(i['entry_px'])}"
        sub = (f"Entrata alle {report.hm(_ts(i['entry_time']))}. In questo momento: <b class='{'up' if res >= 0 else 'down'}'>"
               f"{susd(res)} $</b> per oncia.")
        entry_label, entry_val = "Entrata", px(i["entry_px"])
        pin = (res + risk) / (risk + gain) if gain else None
    else:
        dist = abs(i["level"] - p)
        reach = ""
        if i.get("reach") is not None:
            reach = (": è probabile che oggi ci arrivi" if i["reach"] >= 0.6 else
                     (": oggi può arrivarci" if i["reach"] >= 0.3 else ": è difficile che oggi ci arrivi"))
            if not v["market_open"]:
                reach = ""
        far = f"Mancano {usd(dist)} $ dal prezzo di adesso{reach}."
        if i["kind"] == "fade":
            sub = far
            entry_label, entry_val = "Entrata", px(i["entry"])
        else:
            where = "sopra" if buy else "sotto"
            beyond = (p > i["level"]) if buy else (p < i["level"])
            sub = ((f"Il prezzo è già {where}: manca la conferma." if beyond else far)
                   + f" Non basta che lo tocchi: vale se è ancora {where} alla fine del quarto d'ora.")
            entry_label, entry_val = "Entrata circa", px(i["entry"])
    rows = f"<div class='row'><span>{entry_label}</span><b>{entry_val}</b><em></em></div>"
    if i.get("target") is not None:
        rows += f"<div class='row'><span>Obiettivo</span><b>{px(i['target'])}</b><em class='up'>+{usd(gain)} $</em></div>"
    rows += f"<div class='row'><span>Stop</span><b>{px(i['stop'])}</b><em class='down'>−{usd(risk)} $</em></div>"
    return (f"<div class='card idea {side}'><div class='ih'><div><span class='tagv {side}'>{'Comprare' if buy else 'Vendere'}</span>"
            f"<span class='lett'>idea {E(i['id'])}</span></div><span class='st {cls}'>{label}</span></div>"
            f"<div class='cond'>{E(cond)}</div><p class='sub'>{sub}</p><div class='rows'>{rows}</div>{_rr_bar(risk, gain, pin)}{_notes(i)}</div>")


def _result(i: dict, past: bool) -> str:
    """State of an idea as a pill, with the gain or the loss in dollars once it has been closed."""
    label, cls = idea_state(i, past)
    o, res = outcome(i, past), idea_money(i)[2]
    money = ""
    if o and o != "open" and res is not None:
        money = f"<b class='{'up' if o == 'win' else ('down' if o == 'loss' else 'mut')}'>{susd(res)} $</b>"
    return f"<span class='r'><span class='st {cls}'>{label}</span>{money}</span>"


def _idea_row(i: dict, past: bool) -> str:
    """One line for an idea that is over, that belongs to a past session, or that is waiting far away."""
    st = i["state"]
    if not outcome(i, past):
        if st == "in attesa" and not past:
            detail = f"Obiettivo {px(i['target'])} · stop {px(i['stop'])}." if i.get("target") is not None else f"Stop {px(i['stop'])}."
        elif st == "annullato":
            detail = "Il prezzo ha superato l'obiettivo in un colpo solo: niente operazione."
        else:
            detail = "Il prezzo non è arrivato al livello di entrata: nessuna operazione."
    else:
        when = f" alle {report.hm(_ts(i['entry_time']))}" if i.get("entry_time") else ""
        bits = [f"Entrata{when} a {px(i['entry_px'])}"]
        if st in ("stop", "target 1") and i.get("exit_px") is not None:
            at = f" alle {report.hm(_ts(i['exit_time']))}" if i.get("exit_time") else ""
            bits.append(f"uscita {'allo stop' if st == 'stop' else 'all’obiettivo'}{at} a {px(i['exit_px'])}")
        elif st == "chiuso a fine sessione" and i.get("exit_px") is not None:
            bits.append(f"chiusa a fine giornata a {px(i['exit_px'])}")
        elif st == "aperto" and not past:
            bits.append("ancora in corso")
        detail = ", ".join(bits) + "."
    return (f"<div class='drow'><span class='l'>{E(i['id'])}</span><span class='w'>{E(cap(idea_phrase(i, short=True)))}</span>"
            f"{_result(i, past)}<span class='d'>{detail}</span></div>")


def _dots(t: dict, limit: int = 14) -> str:
    """One small dot per idea that started, in order of entry: green gain, red loss."""
    if not t["n"]:
        return ""
    cls = {"win": "w", "loss": "l", "flat": "f", "open": "o"}
    more = f"<em>+{t['n'] - limit}</em>" if t["n"] > limit else ""
    return "<span class='dots' aria-hidden='true'>" + "".join(f"<i class='{cls[m]}'></i>" for m in t["marks"][:limit]) + more + "</span>"


def _total(t: dict) -> str:
    """Sum of gains and losses of the closed ideas, e.g. '+27 $'."""
    if not (t["win"] + t["loss"] + t["flat"]):
        return ""
    return f"<b class='tot {'up' if t['usd'] >= FLAT else ('down' if t['usd'] <= -FLAT else 'mut')}'>{susd(t['usd'])} $</b>"


def _fold(title: str, ideas: list, past: bool) -> str:
    """Card that tells in one line how a group of ideas went and opens to show them one by one."""
    t = tally(ideas, past)
    rows = "".join(_idea_row(i, past) for i in _in_order(ideas))
    return (f"<details class='card fold'><summary><span class='fh'><b>{title}</b>{_dots(t)}</span>"
            f"<span class='fs'><span>{tally_words(t)}</span>{_total(t)}</span></summary><div class='frows'>{rows}</div></details>")


def _ideas(v: dict, past: bool) -> str:
    day = pd.Timestamp(v["tday"])
    if past:
        over = not v.get("pending")                  # False: only the plan is known and its outcome is still to come
        if not v["ideas"]:
            box = "<div class='card empty'>Quel giorno nessuna idea: il prezzo era lontano dai livelli interessanti.</div>"
        else:
            t = tally(v["ideas"], over)
            head = tally_words(t) if over else "Com'è andata si saprà a fine giornata"
            box = (f"<div class='card sum'><span class='fh'><b>{head}</b>{_dots(t)}</span>{_total(t)}</div>"
                   f"<div class='card done' style='margin-top:10px'>{''.join(_idea_row(i, over) for i in _in_order(v['ideas']))}</div>")
        return (f"<section id='idee'><h2>{'Le idee di quel giorno e com’è andata' if over else 'Le idee del giorno'}</h2>"
                f"<p class='lead'>Guadagni e perdite sono in dollari per ogni oncia, prima dei costi.</p>{box}</section>")
    cards, waiting, over = shown_ideas(v)
    title = "Le idee di oggi" if v["market_open"] else f"Le idee per {weekday(day)}"
    if v.get("halted"):
        title = "Le idee"
    if v.get("halted") and not cards:
        box = ("<div class='card empty'><b>Mercato fermo: per ora nessuna idea.</b> Le idee tornano quando l'oro riprende a "
               "essere scambiato. La pagina si aggiorna da sola.</div>")
    elif cards:
        box = f"<div class='ideas{' one' if len(cards) == 1 else ''}'>" + "".join(_idea_card(i, v) for i in cards) + "</div>"
    elif v["market_open"] and _ts(v["asof"]) >= _ts(v["deadline"]):
        box = ("<div class='card empty'><b>Per oggi basta.</b> Dopo le 21:00 non si aprono nuove operazioni. Le prossime idee "
               "arrivano dopo mezzanotte.</div>")
    else:
        box = ("<div class='card empty'><b>Per ora nessuna idea.</b> Il prezzo è lontano dai livelli interessanti: in questi casi "
               "la cosa migliore è aspettare. La pagina si aggiorna da sola ogni mezz'ora circa.</div>")
    howto = ("<ol class='howto'><li><b>Un'idea vale solo se il prezzo arriva all'entrata.</b> Finché è «in attesa» non si fa nulla.</li>"
             "<li><b>Lo stop si mette sempre</b>, nel momento in cui si entra: è l'uscita che limita la perdita.</li>"
             "<li>Dopo le 21:00 non si aprono nuove operazioni e alle 23:00 si chiude tutto.</li></ol>") if cards else ""
    more = ""
    if waiting:
        more = (f"<div class='h3'>Idee precedenti ancora valide</div>"
                f"<div class='card done'>{''.join(_idea_row(i, False) for i in _in_order(waiting))}</div>")
    return (f"<section id='idee'><h2>{title}</h2><p class='lead'>Spunti calcolati in automatico, uguali per tutti: non sono consigli. "
            f"Guadagni e perdite sono in dollari per ogni oncia.</p>{box}{howto}{more}</section>")


def _group_events(items: list) -> list:
    """Appointments at the same time become one line: names without repetitions, highest importance."""
    out = []
    for x in items:
        g = next((y for y in out if y["t"] == x["t"]), None)
        if g is None:
            g = {"t": x["t"], "names": [], "rel": 0, "past": True}
            out.append(g)
        if x["name"] not in g["names"]:
            if x["rel"] > g["rel"]:
                g["names"].insert(0, x["name"])
            else:
                g["names"].append(x["name"])
        g["rel"], g["past"] = max(g["rel"], x["rel"]), g["past"] and x["past"]
    return out


def _agenda(v: dict) -> str:
    ev = v["events"]
    day = pd.Timestamp(v["tday"])
    title = "Appuntamenti di oggi" if v["market_open"] else f"Appuntamenti di {weekday(day)}"
    if not ev["available"]:
        return (f"<section id='agenda'><h2>{title}</h2><div class='card empty'>Il calendario non è disponibile in questo momento."
                f"</div></section>")

    def row(g, with_day=False):
        t = _ts(g["t"])
        tag = "<span class='imp hi'>molto importante</span>" if g["rel"] >= 3 else "<span class='imp'>importante</span>"
        info = "Già passato." if g["past"] else ("Può far muovere il prezzo di colpo." if g["rel"] >= 3 else "Può muovere un po' il prezzo.")
        small = f"<small>{GIORNI_S[t.day_of_week]} {t.day:02d}/{t.month:02d}</small>" if with_day else ""
        names = g["names"][:3]
        text = " · ".join(E(n) for n in names) + (" e altri dati" if len(g["names"]) > 3 else "")
        return (f"<div class='ev{' past' if g['past'] else ''}'><div class='t'>{report.hm(t)}{small}</div><div><div class='n'>{text}</div>"
                f"<div class='i'>{tag}{info}</div></div></div>")
    today, nxt = _group_events(ev["today"]), _group_events(ev["next"])[:3]
    if today:
        box = (f"<div class='card evs'>{''.join(row(g) for g in today)}</div><p class='fine'>Quando escono queste notizie il prezzo "
               f"dell'oro può fare scatti improvvisi: meglio non aprire operazioni nei minuti subito prima e subito dopo.</p>")
    else:
        box = ("<div class='card empty'><b>Nessun appuntamento importante.</b> Non sono attese notizie che di solito muovono "
               "il prezzo dell'oro.</div>")
    if nxt:
        box += f"<div class='h3'>Nei prossimi giorni</div><div class='card evs'>{''.join(row(g, True) for g in nxt)}</div>"
    return f"<section id='agenda'><h2>{title}</h2><p class='lead'>Orari italiani.</p>{box}</section>"


def record_block(rec: dict | None, test: dict | None) -> str:
    """Scoreboard of the ideas that started in the concluded sessions, with the honest reading of what it is worth."""
    if not rec or not rec.get("trades"):
        return ""
    since = pd.Timestamp(rec["since"])
    n, w, l, flat = rec["trades"], rec["wins"], rec["losses"], rec["flat"]
    bar = (f"<div class='score' role='img' aria-label='{w} in guadagno, {l} in perdita'>"
           + (f"<i style='flex:{w}'></i>" if w else "") + (f"<b style='flex:{l}'></b>" if l else "")
           + (f"<u style='flex:{flat}'></u>" if flat else "") + "</div>")
    legend = (f"<div class='legend'><span class='w'><b>{w}</b> chius{'a' if w == 1 else 'e'} in guadagno</span>"
              f"<span class='l'><b>{l}</b> chius{'a' if l == 1 else 'e'} in perdita</span>"
              + (f"<span><b>{flat}</b> in pari</span>" if flat else "") + "</div>")
    usd_cls = "up" if rec["usd"] >= FLAT else ("down" if rec["usd"] <= -FLAT else "mut")
    days = "1 giornata" if rec["sessions"] == 1 else f"{rec['sessions']} giornate"
    honest = ""
    if test:
        tenth = int(round(test["win"] / 10))
        if test["avg"] <= 2 * test["se"]:
            verdict = "<b>non hanno dimostrato di far guadagnare</b>"
        else:
            verdict = "<b>hanno dato un piccolo guadagno</b>, prima dei costi"
        honest = (f"<p class='fine'>Provate su <b>{test['days']} giornate passate</b>, le stesse regole hanno chiuso in guadagno circa "
                  f"{tenth} volte su 10 e, nel complesso, {verdict}.</p>")
    return (f"<div class='card'><div class='eyebrow'>Il bilancio dal {since.day} {report.MESI_L[since.month - 1]}</div>"
            f"<p class='big2'>In {days} le idee sono scattate <b>{n} volt{'a' if n == 1 else 'e'}</b>.</p>{bar}{legend}"
            f"<p class='fine'>Sommando guadagni e perdite: <b class='{usd_cls}'>{susd(rec['usd'])} $ per oncia</b> in tutto, prima dei costi "
            f"che ogni broker applica a ogni operazione.</p>{honest}</div>")


def _results(v: dict, opt: dict) -> str:
    """How it went: today so far, the previous session, and the scoreboard since the beginning."""
    past_ideas = [i for i in v["ideas"] if i["state"] not in LIVE_STATES]
    pv = v.get("prev")
    block = record_block(opt.get("record"), v.get("test") or opt.get("test"))
    out = ""
    if past_ideas:
        out += _fold("Oggi, finora" if v["market_open"] else "In attesa della riapertura", past_ideas, False)
    if pv and pv.get("ideas"):
        d = pd.Timestamp(pv["day"])
        ieri = v["market_open"] and (pd.Timestamp(v["tday"]) - d).days == 1
        out += _fold(f"{'Ieri' if ieri else cap(weekday(d))}, {d.day} {report.MESI_L[d.month - 1]}", pv["ideas"], True)
    out += block
    if not out:
        return ""
    more = f"<a class='more' href='{E(opt['archive'])}'>Guarda tutti i giorni passati →</a>" if opt.get("archive") else ""
    return (f"<section id='risultati'><h2>Com'è andata</h2><p class='lead'>Ogni idea, una volta scattata, viene seguita fino in fondo "
            f"e contata, che vada bene o male. Guadagni e perdite sono in dollari per ogni oncia, prima dei costi.</p>"
            f"<div class='stack'>{out}</div>{more}</section>")


GLOSSARY = [
    ("Oncia", "L'unità con cui si misura l'oro sui mercati: circa 31 grammi. Il prezzo in alto è in dollari per un'oncia."),
    ("Comprare e vendere", "Si compra quando si pensa che il prezzo salirà. Si vende quando si pensa che scenderà: con i contratti "
                           "dei broker si può vendere anche senza possedere oro. Sulle piattaforme si chiamano Buy e Sell."),
    ("Entrata", "Il prezzo a cui aprire l'operazione."),
    ("Obiettivo", "Il prezzo a cui chiudere incassando il guadagno. Sulle piattaforme: Take Profit."),
    ("Stop", "Il prezzo a cui chiudere se le cose vanno male, per limitare la perdita. Sulle piattaforme: Stop Loss."),
    ("Dollari per oncia", "Guadagni e perdite sono indicati per una sola oncia. Con molti broker l'operazione più piccola "
                          "(0,01 lotti) vale proprio un'oncia: conviene controllare sul proprio."),
    ("Tendenza", "La direzione che il prezzo ha preso nelle ultime ore e negli ultimi giorni. Racconta quello che è successo: "
                 "non garantisce quello che succederà."),
]


def _glossary() -> str:
    items = "".join(f"<dt>{E(a)}</dt><dd>{E(b)}</dd>" for a, b in GLOSSARY)
    return f"<details class='card gl'><summary>Le parole usate in questa pagina</summary><dl class='gls'>{items}</dl></details>"


def warning_box(test: dict | None = None) -> str:
    tested = f" Provate su {test['days']} giornate passate, queste regole non hanno dimostrato di far guadagnare." if test else ""
    return (f"<div class='warnbox'><b>Da sapere prima di usare queste idee.</b> Questa pagina è prodotta in automatico da regole fisse, "
            f"uguali per chiunque la legga: non è una consulenza e non è un invito a comprare o vendere.{tested} Chi opera sull'oro "
            f"con la leva può perdere in fretta il denaro impiegato: conviene usare solo somme che ci si può permettere di perdere.</div>")


def body(v: dict, opt: dict | None = None) -> str:
    """HTML of the page of one session (between the navigation and the footer of the site).
    opt: latest (True for the session in progress), details and archive (links), record and test (figures
    used when the view does not carry its own)."""
    opt = opt or {}
    past = bool(v.get("final")) or not opt.get("latest")
    day = pd.Timestamp(v["tday"])
    test = v.get("test") or opt.get("test")
    if past and (v.get("prev_close") is None or v.get("price") is None):          # legacy recap: only the plan is known
        asof = _ts(v["asof"]) if v.get("asof") else None
        price = ""
        if v.get("price") is not None:
            at = f" (alle {report.hm(asof)})" if asof is not None else ""
            price = (f"<div class='card'><div class='eyebrow'>Prezzo quando sono state preparate le idee{at}</div>"
                     f"<div class='price'><span>{px(v['price'])}</span><small>$ per oncia</small></div></div>")
        trend = _trend(v, True)
        hero = f"<div class='hero{'' if price and trend else ' solo'}'>{price}{trend}</div>" if price or trend else ""
    else:
        trend = _trend(v, past)
        hero = f"<div class='hero{'' if trend else ' solo'}'>{_hero(v, past)}{trend}</div>"
    if past:
        h1 = f"<h1>Il prezzo dell'oro · <b>{day_name(day, True)}</b></h1>"
    elif v.get("halted"):
        h1 = f"<h1>Il prezzo dell'oro · <b>mercato fermo</b> dalle {report.hm(_ts(v['asof']))} di {day_name(_ts(v['asof']))}</h1>"
    elif v["market_open"]:
        h1 = f"<h1>Il prezzo dell'oro di oggi · <b>{day_name(_ts(v['asof']), True)}</b></h1>"
    else:
        h1 = f"<h1>Il prezzo dell'oro · <b>mercato chiuso</b>, si riparte {weekday(day)} {day.day} {report.MESI_L[day.month - 1]}</h1>"
    parts = [h1, "" if past else "<div class='banner' id='banner' role='status'></div>", hero, _ideas(v, past)]
    if not past:
        parts += [_agenda(v), _results(v, opt), _glossary()]
    if opt.get("details"):
        text = "I numeri di questa giornata, per chi fa trading" if past else "Tutti i numeri, per chi fa trading"
        parts.append(f"<div><a class='more' href='{E(opt['details'])}'>{text} →</a></div>")
    parts.append(warning_box(test))
    return "".join(parts)


def page_data(v: dict, latest: bool) -> dict:
    """Figures used by the script of the page (live price, notice when the page is no longer fresh)."""
    live = bool(latest and not v.get("final"))
    ms = lambda iso: int(pd.Timestamp(iso).timestamp() * 1000) if iso else 0
    return {"live": live, "market_open": bool(v.get("market_open")), "halted": bool(v.get("halted")),
            "gen_ms": ms(v.get("generated") or v.get("asof")), "open_ms": ms(v.get("open")), "close_ms": ms(v.get("close")),
            "prev_close": v.get("prev_close"), "since": _since(v) if live and v.get("prev_day") else ""}


# ----------------------------------------------------------------------------- archive
def day_change(v: dict):
    """Change of the session against the previous close (None when unknown)."""
    if v.get("price") is None or not v.get("prev_close"):
        return None
    return v["price"] / v["prev_close"] - 1


def archive_body(entries: list, rec: dict | None, test: dict | None) -> str:
    """List of the sessions, newest first. entries: [{view, href, latest}]."""
    cards = []
    newest = max((pd.Timestamp(e["view"]["tday"]).year for e in entries), default=0)
    for e in entries:
        v = e["view"]
        day = pd.Timestamp(v["tday"])
        live = bool(e.get("latest")) and not v.get("final")
        chg = day_change(v) if (v.get("final") or (live and v.get("market_open"))) else None
        c = f"<span class='c {'up' if chg >= 0 else 'down'}'>{'▲' if chg >= 0 else '▼'} {spct(chg, 2)}</span>" if chg is not None else ""
        tag = ""
        if live:
            tag = f"<span class='tg'>{'oggi' if v.get('market_open') else 'prossima giornata'}</span>"
        t = tally(v["ideas"], not live and not v.get("pending"))
        if v.get("pending"):
            words = "Com'è andata si saprà a fine giornata"
        elif live and not t["n"]:
            n = len(shown_ideas(v)[0])
            words = "Nessuna idea per ora" if not n else ("1 idea in attesa" if n == 1 else f"{n} idee in attesa")
        else:
            words = tally_words(t)
        cards.append(f"<a class='card dayc' href='{E(e['href'])}'><div class='dh'><span><b>{day_name(day, day.year != newest)}</b>{tag}</span>{c}</div>"
                     f"<div class='di'><span class='fh'>{_dots(t)}<span>{words}</span></span>{_total(t)}</div></a>")
    block = record_block(rec, test)
    return (f"<h1 class='big'>Giorni passati</h1><p class='lead'>Per ogni giornata: quanto si è mosso l'oro e com'è andata con le idee "
            f"del giorno. Guadagni e perdite sono in dollari per ogni oncia, prima dei costi.</p>"
            f"{block}<h2>Giorno per giorno</h2><p class='lead'>Dal più recente. Tocca un giorno per vedere le sue idee una per una.</p>"
            f"<div class='days'>{''.join(cards)}</div>{warning_box(test)}")
