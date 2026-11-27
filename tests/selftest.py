#!/usr/bin/env python3
"""Self-test: conventions, mechanical evaluation, web site and absence of look-ahead.

    python3 tests/selftest.py            (needs network: downloads a few months of candles)
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from xau import data, engine  # noqa: E402


def bars(seq, start="2026-01-05 08:00"):
    t = pd.date_range(start, periods=len(seq), freq="5min", tz="UTC")
    return pd.DataFrame([dict(time=t[i], open=o, high=h, low=l, close=c, volume=1) for i, (o, h, l, c) in enumerate(seq)])


def test_conventions():
    pv = engine.pivots_classic(4066.787, 3982.415, 3987.245)
    assert abs(pv["P"] - 4012.149) < 1e-3 and abs(pv["R1"] - 4041.883) < 1e-3 and abs(pv["S1"] - 3957.511) < 1e-3
    assert abs(pv["R2"] - 4096.521) < 1e-3 and abs(pv["S2"] - 3927.777) < 1e-3
    # trading day rolls at 17:00 New York, summer and winter time
    for ts, exp in (("2026-11-11 21:59", "2026-11-11"), ("2026-11-11 22:00", "2026-11-12"),
                    ("2026-07-15 20:59", "2026-07-15"), ("2026-07-15 21:00", "2026-07-16"),
                    ("2026-11-08 23:30", "2026-11-09")):
        assert engine.tday_of(pd.Timestamp(ts, tz="UTC")).strftime("%Y-%m-%d") == exp, ts
    b = engine.session_bounds(pd.Timestamp("2026-11-12"))
    assert b["open"].strftime("%d %H:%M") == "11 23:00" and b["close"].strftime("%d %H:%M") == "12 22:00"


def test_evaluation():
    t0, dl, cl = (pd.Timestamp(x, tz="UTC") for x in ("2026-01-05 08:00", "2026-01-05 20:00", "2026-01-05 22:00"))
    short = {"id": "A", "type": "fade", "dir": -1, "entry": 100.0, "stop": 110.0, "targets": [{"price": 90.0}]}
    brk = {"id": "B", "type": "break", "dir": -1, "trigger": 100.0, "entry": 99.0, "stop": 105.0, "targets": [{"price": 92.0}]}
    long_ = {"id": "C", "type": "fade", "dir": 1, "entry": 100.0, "stop": 90.0, "targets": [{"price": 110.0}]}
    cases = [
        (short, [(95, 96, 94, 95), (95, 101, 95, 100), (100, 100, 95, 96), (96, 96, 89, 90)], "target 1", 1.0),
        (short, [(95, 96, 94, 95), (95, 101, 95, 100), (100, 111, 99, 108)], "stop", -1.0),
        (short, [(95, 101, 95, 100), (100, 111, 89, 95)], "stop", -1.0),               # both in one bar -> stop first
        (short, [(95, 99, 94, 95), (95, 99.9, 90, 91)], "non attivato", 0.0),
        (short, [(95, 101, 89, 95), (95, 111, 95, 100)], "stop", -1.0),                # target inside the fill bar is ignored
        (brk, [(101, 102, 99, 101), (101, 102, 100.5, 101), (101, 102, 100.2, 101.5)] * 2, "non attivato", 0.0),
        (brk, [(101, 102, 100, 101), (101, 101, 99, 99.5), (99.5, 100, 98, 98), (98, 99, 91, 92)], "target 1", round(6 / 7, 2)),
        # confirming close (91.5) already beyond the first target (92): cancelled, never a fake "+R at T1"
        (brk, [(101, 102, 100, 101), (101, 101, 95, 96), (96, 96, 91, 91.5), (91.5, 93, 90, 92)], "annullato", 0.0),
        (long_, [(105, 106, 104, 105), (105, 105, 99, 100), (100, 111, 100, 110)], "target 1", 1.0),
        (long_, [(105, 106, 104, 105), (105, 105, 99, 100), (100, 101, 89, 90)], "stop", -1.0),
    ]
    for s, seq, status, r_mult in cases:
        r = engine.evaluate_setup(s, bars(seq), t0, dl, cl)
        assert r["status"] == status and abs(r["R"] - r_mult) < 1e-9, (s["id"], seq, r)


def test_tracking():
    """Live status of a published setup: waiting, open (with best/worst excursion), stopped, expired."""
    from xau import extras
    t0, dl, cl = (pd.Timestamp(x, tz="UTC") for x in ("2026-01-05 08:00", "2026-01-05 20:00", "2026-01-05 22:00"))
    brk = {"id": "B", "type": "break", "dir": -1, "trigger": 100.0, "entry": 99.0, "stop": 105.0, "targets": [{"price": 80.0}]}
    fade = {"id": "A", "type": "fade", "dir": -1, "entry": 100.0, "stop": 110.0, "targets": [{"price": 90.0}]}
    seq = [(101, 102, 100.5, 101), (101, 101, 99.5, 100.5), (100.5, 100.5, 98.5, 99),      # M15 close at 99 -> short at 99, risk 6
           (99, 100, 95, 96), (96, 97, 90, 91), (91, 96.5, 91, 96)]
    now = t0 + pd.Timedelta(minutes=30)
    r = extras.track_setup(brk, bars(seq), t0, dl, cl, now)
    assert r["state"] == "aperto" and r["entry_px"] == 99 and r["best"] == 90 and r["worst"] == 100, r
    assert abs(r["mfe"] - 1.5) < 1e-9 and abs(r["mae"] - 1 / 6) < 1e-9 and abs(r["R"] - 0.5) < 1e-9, r
    assert extras.track_setup(brk, bars(seq), t0, dl, cl, cl)["state"] == "chiuso a fine sessione"
    r = extras.track_setup(fade, bars([(95, 99, 94, 95)] * 4), t0, dl, cl, now)
    assert r["state"] == "in attesa" and r["entry_px"] is None and r["seen_hi"] == 99, r
    assert extras.track_setup(fade, bars([(95, 99, 94, 95)] * 4), t0, dl, cl, dl)["state"] == "non attivato"
    r = extras.track_setup(fade, bars([(95, 101, 95, 100), (100, 100, 93, 94), (94, 111, 94, 108)]), t0, dl, cl, now)
    assert r["state"] == "stop" and r["R"] == -1.0 and r["best"] == 93 and abs(r["mfe"] - 0.7) < 1e-9, r


def test_session_book():
    """Official plan, refreshes, stable letters, withdrawal of stale scenarios, no flattering of the register."""
    import tempfile
    from xau import extras
    day = pd.Timestamp("2026-01-05")
    t = pd.date_range("2026-01-04 23:00", "2026-01-05 21:55", freq="5min", tz="UTC")      # the whole session
    m5 = pd.DataFrame({"time": t, "open": 97.0, "high": 97.0, "low": 97.0, "close": 97.0, "volume": 1})
    m5.loc[m5["time"] == pd.Timestamp("2026-01-05 04:00", tz="UTC"), "high"] = 102.5          # fills the short at 102
    late = m5["time"] >= pd.Timestamp("2026-01-05 10:25", tz="UTC")                            # M15 close at 94 -> break below 95
    m5.loc[late, ["open", "high", "low", "close"]] = 94.0

    def setup(kind, entry, stop, target, trigger=None):
        return {"id": "?", "type": kind, "dir": -1, "role": "con il trend", "zone_lo": entry - 1, "zone_hi": entry + 1,
                "trigger": trigger, "entry": entry, "stop": stop, "risk": abs(stop - entry),
                "targets": [{"price": target, "rr": 1.0, "label": "x", "proj": False}], "rr1": 1.0}

    def run(out, when, scen):
        A = {"tday": day, "asof": pd.Timestamp(when, tz="UTC"), "price": 97.0, "bias": {"label": "x", "score": 0.0},
             "bounds": engine.session_bounds(day), "invalidation": None, "scenarios": [dict(x) for x in scen]}
        for i, x in enumerate(A["scenarios"]):
            x["id"] = "ABCDEF"[i]                       # letters as the engine assigns them at every run
        st = extras.sync_book(out, A, m5[m5["time"] < A["asof"]], lambda x: "titolo")
        extras.save_plan(out, A, {x["id"]: "titolo" for x in A["scenarios"]}, keep=st is not None)
        with open(os.path.join(out, "plans", "plan_2026-01-05.json"), encoding="utf-8") as fh:
            plan = json.load(fh)
        return A, st, plan

    s1, s2 = setup("fade", 100.0, 110.0, 90.0), setup("break", 89.0, 93.0, 80.0, trigger=90.0)
    s3, s4 = setup("fade", 102.0, 112.0, 80.0), setup("fade", 105.0, 115.0, 95.0)
    s5, s6 = setup("break", 94.0, 99.0, 85.0, trigger=95.0), setup("fade", 100.0, 108.0, 92.0)
    with tempfile.TemporaryDirectory() as out:
        A, st, plan = run(out, "2026-01-05 01:00", [s1, s2])                 # first plan = official
        assert st is None and plan["asof"].startswith("2026-01-05T01:00") and len(plan["setups"]) == 2
        A, st, plan = run(out, "2026-01-05 03:00", [s3])                     # pre-London, nothing triggered: replaced
        assert st is None and plan["asof"].startswith("2026-01-05T03:00") and plan["setups"][0]["entry"] == 102.0
        A, st, plan = run(out, "2026-01-05 05:00", [s4])                     # pre-London but A is in position: frozen
        assert plan["asof"].startswith("2026-01-05T03:00"), "a triggered official plan must not be replaced"
        assert st and [(r["id"], r["state"], r["official"]) for r in st["rows"]] == [("A", "aperto", True)]
        assert [x["id"] for x in A["scenarios"]] == ["B"] and A["scenarios"][0].get("adds_to") == "A"
        A, st, plan = run(out, "2026-01-05 09:00", [s4, s5])                 # same scenario keeps its letter, new one gets C
        assert [x["id"] for x in A["scenarios"]] == ["B", "C"] and [r["id"] for r in st["rows"]] == ["A"]
        A, st, plan = run(out, "2026-01-05 10:00", [s5])                     # B no longer proposed: withdrawn
        assert [x["id"] for x in A["scenarios"]] == ["C"]
        A, st, plan = run(out, "2026-01-05 12:00", [s5, s6])                 # C triggered at 10:30: followed, not re-proposed
        assert [(r["id"], r["state"], r["official"]) for r in st["rows"]] == [("A", "aperto", True), ("C", "aperto", False)], st["rows"]
        assert [x["id"] for x in A["scenarios"]] == ["D"], "a withdrawn letter is never reused"
        # D (short at 100): inside A's stop-target range -> adds to A; beyond C's stop (99) -> only after C is stopped
        assert A["scenarios"][0].get("adds_to") == "A" and A["scenarios"][0].get("after_stop") == "C"
        ra, rc = st["rows"]
        assert ra["entry_px"] == 102.0 and abs(ra["R"] - 0.8) < 1e-9 and rc["entry_px"] == 94.0 and rc["R"] == 0.0
        with open(os.path.join(out, "plans", "session_2026-01-05.json"), encoding="utf-8") as fh:
            book = json.load(fh)
        assert [(x["id"], bool(x["withdrawn"])) for x in book["setups"]] == [("A", False), ("B", True), ("C", False), ("D", False)]
        assert plan["asof"].startswith("2026-01-05T03:00") and not st["same"]
        # the day after: final outcome of everything published (withdrawn B left out), refreshes flagged as such
        pv = extras.previous_session(out, {"tday": pd.Timestamp("2026-01-06")}, m5)
        assert pv["tday"] == day and [(r["id"], r["state"], r["official"]) for r in pv["rows"]] == [
            ("A", "chiuso a fine sessione", True), ("C", "chiuso a fine sessione", False), ("D", "non attivato", False)], pv["rows"]
        assert abs(pv["rows"][0]["R"] - 0.8) < 1e-9
        assert extras.previous_session(out, {"tday": day}, m5) is None            # no earlier session with a book
    with tempfile.TemporaryDirectory() as out:                              # unchanged plan after London: "confirmed"
        run(out, "2026-01-05 07:00", [s4])
        A, st, plan = run(out, "2026-01-05 09:30", [s4])
        assert st and st["same"] and [x["id"] for x in A["scenarios"]] == ["A"] and plan["asof"].startswith("2026-01-05T07:00")


def test_letters():
    """Scenario ids for a session refreshed every few minutes: never reused, never exhausted."""
    from xau import extras
    used = set()
    seq = []
    for _ in range(60):
        seq.append(extras.next_letter(used))
        used.add(seq[-1])
    assert seq[:3] == ["A", "B", "C"] and "I" not in seq and "O" not in seq and len(set(seq)) == 60
    assert seq[23] == "Z" and seq[24] == "AA" and seq[25] == "AB"
    assert extras.next_letter({"A", "C"}) == "B"


def test_calendar_week():
    """The weekly feed is kept only when it covers the week of the session (weekend briefings)."""
    import tempfile
    import run_briefing
    mk = lambda day, n: [{"time": pd.Timestamp(f"{day} 13:30", tz="UTC") + pd.Timedelta(hours=i), "currency": "USD",
                          "title": f"Event {i}", "impact": "High", "forecast": "", "previous": ""} for i in range(n)]
    monday = pd.Timestamp("2026-11-16")                         # session of ISO week 47
    orig = data.ff_calendar
    try:
        with tempfile.TemporaryDirectory() as d:
            calls = []
            data.ff_calendar = lambda: calls.append(1) or mk("2026-11-12", 5)       # feed still on week 46 (Saturday)
            out = run_briefing.load_calendar(d, pd.Timestamp("2026-11-14 10:00", tz="UTC"), monday)
            assert out == [] and not os.listdir(d), "last week's agenda must not be shown for next week"
            data.ff_calendar = lambda: calls.append(1) or mk("2026-11-17", 5)       # feed switched to week 47 (Sunday)
            out = run_briefing.load_calendar(d, pd.Timestamp("2026-11-15 18:00", tz="UTC"), monday)
            assert len(out) == 5 and os.listdir(d) == ["calendar_2026-W47.json"]
            n = len(calls)
            out = run_briefing.load_calendar(d, pd.Timestamp("2026-11-15 20:00", tz="UTC"), monday)   # fresh cache: no request
            assert len(out) == 5 and len(calls) == n
            data.ff_calendar = lambda: calls.append(1) or []                        # feed down after 6 hours: cached copy
            out = run_briefing.load_calendar(d, pd.Timestamp("2026-11-16 09:00", tz="UTC"), monday)
            assert len(out) == 5 and len(calls) == n + 1
    finally:
        data.ff_calendar = orig


def test_week_stats():
    """Weekly recap shown when the next session opens a new week."""
    from xau import report
    days = pd.bdate_range("2026-11-02", "2026-11-13")
    d1 = pd.DataFrame({"tday": days, "open": 100.0, "high": 110.0, "low": 90.0, "close": [100.0 + i for i in range(len(days))]})
    d1.loc[d1["tday"] == pd.Timestamp("2026-11-12"), ["high", "low"]] = [120.0, 80.0]
    ws = report.week_stats({"tday": pd.Timestamp("2026-11-16"), "frames": {"d1": d1}})
    assert ws and ws["n"] == 5 and ws["high"] == 120.0 and ws["low"] == 80.0 and ws["close"] == 109.0 and ws["ref"] == 104.0
    assert abs(ws["chg"] - (109.0 / 104.0 - 1)) < 1e-12
    assert report.week_stats({"tday": pd.Timestamp("2026-11-13"), "frames": {"d1": d1}}) is None     # same week: no recap


JARGON = ("setup", "scenari", "scenario", "trend", "pivot", "atr", "rsi", "long", "short", "breakout", "bias", "intraday", "briefing",
          "seduta", "sedute", "ritracciamento", "rimbalzo", "rottura", "supporto", "resistenza", "timeframe", "target", "trigger",
          "innesco", "r:r", "spread", "slippage", "fade", "rialzista", "ribassista")


def jargon(page: str) -> list:
    """Trading jargon found in the visible text of a page (the plain pages must not contain any)."""
    import html
    import re
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S)
    words = set(re.findall(r"[a-zà-ù:]+", html.unescape(re.sub(r"<[^>]+>", " ", text)).lower()))
    return sorted(w for w in JARGON if w in words)


def test_simple():
    """Plain pages: everyday wording, dollars per ounce, outcome of the ideas, scoreboard, recap of a concluded session."""
    import tempfile
    from xau import simple
    assert simple.px(3650.46) == "3650,5" and simple.usd(18.44) == "18" and simple.usd(4.46) == "4,5" and simple.susd(-38.6) == "−39"
    assert [simple.trend_level(x) for x in (0.9, 0.6, 0.35, 0.0, -0.3, -0.75)] == [2, 2, 1, 0, -1, -2]
    buy_dip = {"id": "H", "dir": 1, "kind": "fade", "level": 3650.5, "entry": 3650.5, "stop": 3631.1, "target": 3689.6, "state": "in attesa",
               "official": False, "now": True, "entry_px": None, "entry_time": None, "exit_px": None, "exit_time": None, "last": None}
    sell_brk = dict(buy_dip, id="B", dir=-1, kind="break", level=3563.2, entry=3560.8, stop=3578.6, target=3500.0)
    assert simple.idea_phrase(buy_dip) == "comprare se il prezzo scende a 3650,5" and simple.idea_phrase(buy_dip, True) == "comprare a 3650,5"
    assert simple.idea_phrase(sell_brk) == "vendere se il prezzo scende sotto 3563,2" and simple.idea_phrase(sell_brk, True) == "vendere sotto 3563,2"
    risk, gain, res = simple.idea_money(buy_dip)
    assert abs(risk - 19.4) < 1e-9 and abs(gain - 39.1) < 1e-9 and res is None
    assert simple.outcome(buy_dip) == "" and simple.idea_state(buy_dip) == ("In attesa", "wait")
    assert simple.idea_state(buy_dip, past=True) == ("Non scattata", "off")
    opened = dict(buy_dip, state="aperto", entry_px=3650.5, entry_time="2026-11-27T11:00:00+00:00", exit_px=3660.0, last=3660.0)
    assert simple.outcome(opened) == "open" and simple.idea_state(opened) == ("In corso", "open")
    assert abs(simple.idea_money(opened)[2] - 9.5) < 1e-9
    assert simple.outcome(opened, past=True) == "win" and simple.idea_state(opened, past=True) == ("Chiusa in guadagno", "win")
    stopped = dict(sell_brk, state="stop", entry_px=3560.8, exit_px=3578.6)
    hit = dict(sell_brk, state="target 1", entry_px=3560.8, exit_px=3500.0)
    flat = dict(sell_brk, state="chiuso a fine sessione", entry_px=3560.8, exit_px=3560.9)
    late = dict(sell_brk, state="chiuso a fine sessione", entry_px=3560.8, exit_px=3570.8)
    void = dict(sell_brk, state="annullato")
    assert [simple.idea_state(i, True)[0] for i in (stopped, hit, flat, late, void)] == [
        "Chiusa in perdita", "Obiettivo raggiunto", "Chiusa in pari", "Chiusa in perdita", "Annullata"]
    t = simple.tally([buy_dip, opened, stopped, hit, flat, late, void], past=False)
    assert (t["n"], t["win"], t["loss"], t["flat"], t["open"]) == (5, 1, 2, 1, 1) and abs(t["usd"] - (-17.8 + 60.8 - 0.1 - 10.0)) < 1e-9
    assert simple.tally_words(t) == "5 idee scattate: 1 in guadagno, 2 in perdita, 1 in pari, 1 in corso"
    assert simple.tally_words(simple.tally([buy_dip, void], True)) == "Nessuna idea è scattata"
    # scoreboard: every idea that started in the concluded sessions, the running one left out
    day = lambda d, ideas, final=True: {"tday": d, "final": final, "ideas": ideas}
    rec = simple.record([day("2026-11-13", [stopped, void]), day("2026-11-12", [hit, flat]), day("2026-11-16", [hit], final=False)])
    assert (rec["since"], rec["sessions"], rec["trades"], rec["wins"], rec["losses"], rec["flat"]) == ("2026-11-12", 2, 3, 1, 1, 1)
    assert abs(rec["usd"] - 42.9) < 1e-9 and simple.record([day("2026-11-16", [buy_dip])]) is None
    test = {"days": 250, "trades": 221, "win": 38.0, "avg": -0.0964, "se": 0.0826}
    block = simple.record_block(rec, test)
    assert "In 2 giornate le idee sono scattate <b>3 volte</b>" in block and "+43 $ per oncia" in block
    assert "circa 4 volte su 10" in block and "non hanno dimostrato di far guadagnare" in block
    assert "hanno dato un piccolo guadagno" in simple.record_block(rec, dict(test, win=55.0, avg=0.3)) and simple.record_block(None, test) == ""
    # appointments in everyday words; same time = one line, the most important name first
    fed = "banca centrale americana (Fed)"
    assert simple.plain_event("Core PCE Price Index m/m", "Indice PCE core m/m") == "Inflazione negli Stati Uniti (il dato più seguito dalla Fed)"
    assert simple.plain_event("FOMC Member Bowman Speaks", "x") == f"Parla Bowman, dirigente della {fed}"
    assert simple.plain_event("Fed Chair Powell Testifies", "x") == f"Parla al Congresso Powell, presidente della {fed}"
    assert simple.plain_event("Unknown Thing", "Nome tecnico") == "Nome tecnico"
    assert simple.plain_event("CPI m/m", "Inflazione (CPI) m/m", "AUD") == "Inflazione (CPI) m/m"
    ev = lambda t, name, rel, past=False: {"t": t, "name": name, "rel": rel, "past": past}
    assert simple._group_events([ev("T1", "PIL", 2), ev("T1", "Inflazione", 3), ev("T1", "PIL", 2), ev("T2", "Fiducia", 2, True)]) == [
        {"t": "T1", "names": ["Inflazione", "PIL"], "rel": 3, "past": False}, {"t": "T2", "names": ["Fiducia"], "rel": 2, "past": True}]
    # a session known only through its official plan
    plan = {"tday": "2026-11-12", "asof": "2026-11-12T01:08:00+00:00", "price": 3900.0, "bias": "Ribassista forte", "score": -0.9,
            "evaluated": True, "close": "2026-11-12T22:00:00+00:00",
            "setups": [{"id": "A", "type": "fade", "dir": -1, "trigger": None, "entry": 3918.9, "stop": 3940.0, "targets": [{"price": 3880.0}]},
                       {"id": "B", "type": "break", "dir": -1, "trigger": 3866.6, "entry": 3864.0, "stop": 3878.8, "targets": [{"price": 3800.0}]}],
            "results": [{"id": "A", "status": "non attivato", "R": 0.0, "entry_px": None, "exit_px": None},
                        {"id": "B", "status": "target 1", "R": 4.32, "entry_px": 3864.0, "exit_px": 3800.0}]}
    v = simple.legacy_view("2026-11-12", plan)
    assert v["final"] and not v["pending"] and v["trend"]["level"] == -2 and [i["state"] for i in v["ideas"]] == ["non attivato", "target 1"]
    assert v["summary"] == "Oro (XAU/USD), giovedì 12 novembre 2026, tendenza in forte discesa. Com'è andata con le 2 idee del giorno."
    page = simple.body(v, {"latest": False})
    assert "Obiettivo raggiunto" in page and "+64 $" in page and "Non scattata" in page and "1 idea scattata: 1 in guadagno" in page
    assert not jargon(page), jargon(page)
    pend = simple.body(simple.legacy_view("2026-11-16", dict(plan, tday="2026-11-16", evaluated=False, results=[])), {"latest": True})
    assert "si saprà a fine giornata" in pend and "In attesa" in pend and "Non scattata" not in pend
    none = simple.legacy_view("2026-11-13", None)
    assert none["ideas"] == [] and "Quel giorno nessuna idea" in simple.body(none, {})
    # recap of a concluded session on the complete prices (same session as test_session_book)
    t5 = pd.date_range("2026-01-04 23:00", "2026-01-05 21:55", freq="5min", tz="UTC")
    m5 = pd.DataFrame({"time": t5, "open": 97.0, "high": 97.0, "low": 97.0, "close": 97.0, "volume": 1})
    m5.loc[m5["time"] == pd.Timestamp("2026-01-05 04:00", tz="UTC"), "high"] = 102.5          # fills the short at 102
    m5.loc[m5["time"] >= pd.Timestamp("2026-01-05 10:25", tz="UTC"), ["open", "high", "low", "close"]] = 94.0
    setup = lambda i, kind, entry, stop, target, pub, official=False, withdrawn=None, trigger=None: {
        "id": i, "type": kind, "dir": -1, "trigger": trigger, "entry": entry, "stop": stop, "targets": [{"price": target}],
        "pub": f"2026-01-05T{pub}:00+00:00", "official": official, "withdrawn": withdrawn}
    book = {"tday": "2026-01-05", "official_asof": "2026-01-05T03:00:00+00:00", "setups": [
        setup("A", "fade", 102.0, 112.0, 80.0, "03:00", official=True),                       # sold at 102, closed at 94 at the end
        setup("B", "fade", 105.0, 115.0, 95.0, "05:00", withdrawn="2026-01-05T10:00:00+00:00"),
        setup("C", "break", 94.0, 99.0, 85.0, "09:00", trigger=95.0),                          # sold at 94, closed at 94
        setup("D", "fade", 100.0, 108.0, 92.0, "12:00")]}                                      # never reached
    d1 = pd.DataFrame({"tday": [pd.Timestamp("2026-01-02"), pd.Timestamp("2026-01-05")], "open": [96.0, 97.0], "high": [99.0, 102.5],
                       "low": [95.0, 94.0], "close": [96.0, 94.0]})
    frames = {"d1": d1, "m15": engine.resample(m5, 15)}
    with tempfile.TemporaryDirectory() as out:
        os.makedirs(os.path.join(out, "plans"))
        with open(os.path.join(out, "plans", "session_2026-01-05.json"), "w", encoding="utf-8") as fh:
            json.dump(book, fh)
        v = simple.final_view(out, "2026-01-05", frames, m5)
        assert v["final"] and v["price"] == 94.0 and v["prev_close"] == 96.0 and json.loads(json.dumps(v)) == v
        assert [(i["id"], simple.idea_state(i, True)[0]) for i in v["ideas"]] == [("A", "Chiusa in guadagno"), ("C", "Chiusa in pari")]
        assert v["summary"] == ("Oro (XAU/USD), lunedì 5 gennaio 2026: chiusura a 94,0 $ (−2,08% sul giorno prima). "
                                "Com'è andata con le 2 idee del giorno.")
        page = simple.body(v, {"latest": False})
        assert "Chiusura della giornata" in page and "+8,0 $" in page and "2 idee scattate: 1 in guadagno, 1 in pari" in page
        assert not jargon(page), jargon(page)
        for d in ("2026-01-05", "2026-01-06"):
            with open(os.path.join(out, f"XAUUSD_briefing_{d}.html"), "w", encoding="utf-8") as fh:
                fh.write("x")
        A = {"tday": pd.Timestamp("2026-01-06"), "frames": frames}
        assert simple.finalize_past(out, A, m5) == 1 and simple.load(out, "2026-01-05")["final"]
        assert simple.finalize_past(out, A, m5) == 0, "the recap of a concluded session is written once"
        os.remove(simple.view_path(out, "2026-01-05"))
        assert simple.finalize_past(out, A, m5.iloc[50:]) == 0 and simple.load(out, "2026-01-05") is None      # prices do not cover it
        shown = {"available": True, "next": [], "today": [ev("2026-01-05T13:30:00+00:00", "Inflazione", 3)]}
        simple.save(out, dict(v, final=False, events=shown))             # summary written while the session was running
        assert simple.finalize_past(out, A, m5) == 1
        again = simple.load(out, "2026-01-05")
        assert again["final"] and again["events"]["today"] == [ev("2026-01-05T13:30:00+00:00", "Inflazione", 3, past=True)]


def test_site():
    """Web site built from the briefings: the day in plain words as index, one plain page and one technical page per
    session, the list of the past days; relative links only (works at a domain root, in a sub-folder and from disk)."""
    import inspect
    import re
    import tempfile
    import zipfile
    from xau import report, simple, site
    src = inspect.getsource(report.build_html)           # the builder relies on these fixed points of the report
    for anchor in ("</style></head><body><div class='wrap'>", "<footer>Generato il ", "</footer></div>", "id='plan-data'>",
                   "</script></body></html>"):
        assert anchor in src, anchor
    fade = {"id": "A", "type": "fade", "dir": -1, "role": "con il trend", "zone_lo": 3914.9, "zone_hi": 3923.0, "trigger": None}
    brk = {"id": "B", "type": "break", "dir": 1, "role": "con il trend · rottura", "zone_lo": 3866.6, "zone_hi": 3871.1, "trigger": 3866.6}
    assert report.setup_brief(fade) == "vendita su rimbalzo 3914,9–3923,0"
    assert report.setup_brief(brk) == "rottura rialzista sopra 3866,6"
    A = {"asof": pd.Timestamp("2026-11-12 10:53", tz="UTC"), "bias": {"label": "Ribassista forte"}, "market_open": True,
         "price": 3950.12, "scenarios": [fade, brk], "tday": pd.Timestamp("2026-11-12"), "prev": {"tday": "2026-11-11"}}
    assert report.one_liner(A) == ("Oro spot a 3950,1 $ alle 11:53 di gio 12/11, trend ribassista forte. Scenari: "
                                   "A vendita su rimbalzo 3914,9–3923,0 · B rottura rialzista sopra 3866,6.")
    closed = report.one_liner(dict(A, market_open=False, tday=pd.Timestamp("2026-11-16"), prev={"tday": "2026-11-13"}, scenarios=[]))
    assert closed.startswith("Oro spot: ultima chiusura 3950,1 $ (ven 13/11), trend ribassista forte.") and "Nessuno scenario" in closed

    def page(day, hhmm, blob, title=None):
        d = pd.Timestamp(day)
        title = title or f"XAU/USD — Briefing intraday {d.strftime('%d/%m/%Y')}"
        return ("<!doctype html><html lang='it'><head><meta charset='utf-8'><title>" + title + "</title><style>body{}</style></head>"
                "<body><div class='wrap'><div class='banner' id='banner'></div><nav><a href='#s'>S</a></nav>"
                "<section id='s'><svg><title>tooltip</title></svg><a class='src' href='https://example.com/news'>fonte</a></section>"
                f"<footer>Generato il {d.strftime('%d/%m/%Y')} alle {hhmm} (ora di Roma)</footer></div>"
                f"<script type='application/json' id='plan-data'>{json.dumps(blob)}</script><script>/*js*/</script></body></html>")

    sell_top = dict(fade, entry=3918.9, stop=3940.0, risk=21.1, targets=[{"price": 3880.0}], rr1=1.8, title="Vendita su rimbalzo")
    sell_brk = dict(brk, dir=-1, entry=3864.0, stop=3878.8, risk=14.8, targets=[{"price": 3800.0}], rr1=4.3, title="Rottura ribassista")

    def plan(day, asof, results=None):
        p = {"tday": day, "asof": asof, "price": 3900.0, "bias": "Ribassista forte", "score": -0.9, "evaluated": results is not None,
             "setups": [sell_top, sell_brk]}
        if results is not None:
            p["results"] = results
        return p

    def links(text):
        return re.findall(r"""(?:href|src)=['"]([^'"]+)['"]""", text)

    with tempfile.TemporaryDirectory() as tmp:
        out, web = os.path.join(tmp, "output"), os.path.join(tmp, "site")
        os.makedirs(os.path.join(out, "plans"))
        os.makedirs(os.path.join(out, "cache"))
        old = {"asof_ms": 1794480900000, "close_ms": 1794520800000, "price": 3954.13, "prev_close": 3987.245, "historical": False}
        gen = int(pd.Timestamp("2026-11-15 17:33", tz="UTC").timestamp() * 1000)
        new = dict(old, asof_ms=1794607200000, tday="2026-11-16", generated_ms=gen, bias="Ribassista forte",
                   summary="Oro spot: ultima chiusura 3883,2 $ (ven 13/11), trend ribassista forte. Scenari per lun 16/11: A <b>'x'</b>.")
        files = {"XAUUSD_briefing_2026-11-12.html": page("2026-11-12", "11:55", old),
                 "XAUUSD_briefing_2026-11-13.html": page("2026-11-13", "12:22", old),
                 "XAUUSD_briefing_2026-11-16.html": page("2026-11-16", "18:33", new),
                 "XAUUSD_briefing_latest.html": page("2026-11-16", "18:33", new),                     # ignored: copy of the last one
                 "XAUUSD_briefing_2026-11-11_storico_0800.html": page("2026-11-11", "08:00", old)}    # ignored: historical rebuild
        for name, text in files.items():
            with open(os.path.join(out, name), "w", encoding="utf-8") as fh:
                fh.write(text)
        res12 = [{"id": "A", "title": "Vendita su rimbalzo", "status": "non attivato", "R": 0.0, "entry_px": None, "exit_px": None},
                 {"id": "B", "title": "Rottura ribassista", "status": "target 1", "R": 4.32, "entry_px": 3864.0, "exit_px": 3800.0}]
        for day, asof, res in (("2026-11-12", "2026-11-12T01:08:00+00:00", res12), ("2026-11-16", "2026-11-13T22:00:00+00:00", None)):
            with open(os.path.join(out, "plans", f"plan_{day}.json"), "w", encoding="utf-8") as fh:
                json.dump(plan(day, asof, res), fh)
        with open(os.path.join(out, "cache", "replay_2026-11-16.json"), "w", encoding="utf-8") as fh:
            json.dump({"sig": "x", "data": {"days": 250, "total": {"setups": 490, "trades": 221, "win": 38.0, "stop": 58.4,
                                                                    "avg": -0.0964, "se": 0.0826}}}, fh)

        def read(rel):
            with open(os.path.join(web, rel), encoding="utf-8") as fh:
                return fh.read()

        zpath = os.path.join(tmp, "sito.zip")
        res = site.build(out, web, site.load_config(None), zip_path=zpath)
        assert res["sessions"] == 3 and res["latest"] == "2026-11-16" and res["generated"] == pd.Timestamp(gen, unit="ms", tz="UTC")
        made = sorted(os.path.relpath(os.path.join(b, f), web).replace(os.sep, "/") for b, _, fs in os.walk(web) for f in fs)
        days = ["2026-11-12", "2026-11-13", "2026-11-16"]
        assert made == ([".nojekyll"] + [f"archivio/{d}.html" for d in days] + ["archivio/index.html"]
                        + [f"dettagli/{d}.html" for d in days] + ["dettagli/index.html", "index.html", "version.json"]), made
        index, last, first, arch = read("index.html"), read("archivio/2026-11-16.html"), read("archivio/2026-11-12.html"), read("archivio/index.html")
        tech, tech_first = read("dettagli/index.html"), read("dettagli/2026-11-12.html")
        ver = json.loads(read("version.json"))
        assert ver["generated_ms"] == gen and ver["tday"] == "2026-11-16" and ver["label"] == "15/11 alle 18:33"
        # every local link resolves inside the site and none is tied to a domain root or to this machine
        for rel in made:
            if not rel.endswith(".html"):
                continue
            for href in links(read(rel)):
                if href.startswith(("#", "data:", "mailto:", "https://example.com/")):
                    continue
                assert not href.startswith(("/", "http")) and "localhost" not in href, (rel, href)
                target = os.path.normpath(os.path.join(web, os.path.dirname(rel), href))
                assert target.startswith(web) and os.path.isfile(target), (rel, href)
        for text in (index, last, first, arch, tech, tech_first):       # not indexed unless asked, no absolute address without a site URL
            assert text.count("<meta name='description'") == 1 and "noindex,nofollow" in text
            assert "rel='canonical'" not in text and "og:image" not in text and "apple-touch-icon" not in text
        # plain pages: everyday words only; with no stored summary they tell what the official plan says
        for text in (index, last, first, arch):
            assert not jargon(text), jargon(text)
        assert "<title>Oro oggi: prezzo dell&#x27;oro, tendenza e idee del giorno (XAU/USD)</title><meta name='description'" in index
        assert "<title>Oro, giovedì 12 novembre 2026: prezzo e idee del giorno — Oro oggi</title>" in first
        assert "Stai guardando una giornata passata: <b>giovedì 12 novembre 2026</b>" in first and "Stai guardando" not in index + last
        assert "Obiettivo raggiunto" in first and "+64 $" in first and "Non scattata" in first and "si saprà a fine giornata" in index
        assert "href='../archivio/2026-11-13.html'" in first and "href='../dettagli/2026-11-12.html'" in first
        assert "href='dettagli/index.html'" in index and "href='../dettagli/2026-11-16.html'" in last
        assert "id='oro-data'" not in index + last + first, "no live figures without the summary of the running session"
        # the current page offers the reload when a newer one is published; past ones do not
        for text, is_index in ((index, True), (last, False)):
            assert json.loads(re.search(r"id='site-data'>(.*?)</script>", text).group(1)) == {
                "root": "" if is_index else "../", "reload": is_index, "go": "index.html" if is_index else "../index.html",
                "tday": "2026-11-16", "generated_ms": gen}
        assert "id='site-data'" not in first and "id='site-data'" not in arch
        # list of the days: newest first, how the ideas went, honest scoreboard of the concluded sessions
        assert arch.index("2026-11-16.html") < arch.index("2026-11-13.html") < arch.index("2026-11-12.html")
        assert "1 idea scattata: 1 in guadagno" in arch and "<b class='tot up'>+64 $</b>" in arch and "Nessuna idea è scattata" in arch
        assert "In 2 giornate le idee sono scattate <b>1 volta</b>" in arch and "non hanno dimostrato di far guadagnare" in arch
        assert "In 2 giornate le idee sono scattate <b>1 volta</b>" not in first
        # technical pages: the stored report, untouched, with navigation back to the plain pages
        assert "<title>XAU/USD — Briefing intraday 16/11/2026</title><meta name='description'" in tech
        assert "A &lt;b&gt;&#x27;x&#x27;&lt;/b&gt;." in tech and "<svg><title>tooltip</title></svg>" in tech         # escaped, charts untouched
        assert "Torna alla pagina semplice" in tech and "#banner{display:none" not in tech and "id='site-data'" in tech
        assert "Link permanente a questo briefing" in tech and "Link permanente" not in read("dettagli/2026-11-16.html")
        assert "Stai leggendo i numeri di <b>giovedì 12 novembre 2026</b>" in tech_first and "#banner{display:none!important}" in tech_first
        assert "id='site-data'" not in tech_first and "href='../archivio/2026-11-12.html'" in tech_first
        with zipfile.ZipFile(zpath) as z:
            names = z.namelist()
        assert sorted(names) == sorted(site.ZIP_ROOT + "/" + m for m in made)
        again = site.build(out, web, site.load_config(None))
        assert again["zip"] is None and read("index.html") == index and read("archivio/index.html") == arch            # reproducible

        # with the summary of the running session the index is the live page
        asof = "2026-11-13T22:00:00+00:00"
        live = {"v": simple.VERSION, "final": False, "tday": "2026-11-16", "asof": asof, "generated": "2026-11-15T17:33:00+00:00",
                "market_open": False, "open": "2026-11-15T23:00:00+00:00", "close": "2026-11-16T22:00:00+00:00",
                "deadline": "2026-11-16T20:00:00+00:00", "price": 3883.2, "prev_close": 3883.2, "prev_day": "2026-11-13",
                "prev_chg": 0.0017, "atr": 100.0, "day": None,
                "trend": {"score": -0.9, "level": -2, "short": -0.8, "long": -1.0, "ran_up": False, "ran_down": False},
                "ideas": [simple._idea_from_scenario(x, True, asof) for x in (sell_top, sell_brk)],
                "events": {"available": True, "next": [], "today": [
                    {"t": "2026-11-16T13:30:00+00:00", "name": "Inflazione negli Stati Uniti", "rel": 3, "past": False}]},
                "prev": None, "test": None, "chart": {"g2": None, "s2": None, "m6": None}}
        live["summary"] = simple.summary_line(live)
        assert live["summary"] == ("Oro (XAU/USD): mercato chiuso, ultimo prezzo 3883,2 $, tendenza in forte discesa. Idee per lunedì: "
                                   "vendere se il prezzo scende sotto 3866,6; vendere se il prezzo sale a 3918,9.")
        simple.save(out, live)
        site.build(out, web, site.load_config(None))
        index = read("index.html")
        assert "<meta name='description' content='Oro (XAU/USD): mercato chiuso, ultimo prezzo 3883,2 $" in index and not jargon(index)
        assert "Le idee per lunedì" in index and "Se il prezzo sale a 3918,9" in index and "Se il prezzo scende sotto 3866,6" in index
        assert "Riapre a mezzanotte tra domenica e lunedì" in index and "In forte discesa" in index
        assert "Appuntamenti di lunedì" in index and ">14:30<" in index and "molto importante" in index
        assert "In 2 giornate le idee sono scattate <b>1 volta</b>" in index and "id='banner'" in index and "id='px'" in index
        blob = json.loads(re.search(r"id='oro-data'>(.*?)</script>", index).group(1))
        assert blob["live"] is True and blob["market_open"] is False and blob["prev_close"] == 3883.2 and blob["since"] == "rispetto a venerdì"
        assert "id='oro-data'" in read("archivio/2026-11-16.html") and "2 idee in attesa" in read("archivio/index.html")
        # a summary that cannot be drawn never breaks the site: that page falls back to the official plan
        simple.save(out, dict(live, trend={"level": 9}))
        site.build(out, web, site.load_config(None))
        assert "si saprà a fine giornata" in read("index.html") and "id='oro-data'" not in read("index.html")
        simple.save(out, live)

        # public address, indexing, publisher: absolute links for previews; the archive can be capped
        cfg_path = os.path.join(tmp, "site.json")
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump({"indirizzo": "esempio.it/oro", "indicizzabile": True, "autore": "Mario <Rossi>", "contatto": "m@esempio.it",
                       "interessi": "Può avere operazioni aperte sull'oro.", "archivio_max": 2, "ignota": 1}, fh)
        cfg = site.load_config(cfg_path)
        assert cfg["indirizzo"] == "https://esempio.it/oro/" and cfg["indicizzabile"] is True and "ignota" not in cfg
        pics = os.path.join(tmp, "assets")
        os.makedirs(pics)
        for name in ("og.png", "apple-touch-icon.png", "note.md"):                # only images and .txt files are published
            with open(os.path.join(pics, name), "wb") as fh:
                fh.write(b"x")
        site.build(out, web, cfg, assets_dir=pics)
        assert os.path.isfile(os.path.join(web, "og.png")) and not os.path.exists(os.path.join(web, "note.md"))
        assert "<link rel='apple-touch-icon' href='apple-touch-icon.png'>" in read("index.html")
        assert "<link rel='apple-touch-icon' href='../apple-touch-icon.png'>" in read("archivio/index.html")
        for folder in ("archivio", "dettagli"):
            assert not os.path.exists(os.path.join(web, folder, "2026-11-12.html")), folder
        assert "2026-11-12" not in read("archivio/index.html") and "Il bilancio dal" not in read("archivio/index.html")
        index, last = read("index.html"), read("archivio/2026-11-16.html")
        assert "index,follow" in index and "<link rel='canonical' href='https://esempio.it/oro/'>" in index
        assert "<link rel='canonical' href='https://esempio.it/oro/archivio/2026-11-16.html'>" in last
        assert "<link rel='canonical' href='https://esempio.it/oro/dettagli/'>" in read("dettagli/index.html")
        assert "<meta property='og:image' content='https://esempio.it/oro/og.png'>" in last
        assert "Pubblicato da <b>Mario &lt;Rossi&gt;</b>" in index and "href='mailto:m@esempio.it'" in index
        assert "Può avere operazioni aperte sull&#x27;oro." in index and "non usa cookie" in index
        assert "Pubblicato da <b>Mario &lt;Rossi&gt;</b>" in read("dettagli/index.html")
        # search engines: page map only for a public, indexable site; ownership files published untouched
        smap = read("sitemap.xml")
        assert smap.count("<url>") == 5 and "<loc>https://esempio.it/oro/</loc>" in smap and "<loc>https://esempio.it/oro/dettagli/</loc>" in smap
        assert "<loc>https://esempio.it/oro/archivio/2026-11-16.html</loc><lastmod>2026-11-15T18:33:00+01:00</lastmod>" in smap
        with open(os.path.join(pics, "google1a2b3c4d5e6f7a8b.html"), "w", encoding="utf-8") as fh:
            fh.write("google-site-verification: google1a2b3c4d5e6f7a8b.html")
        with open(os.path.join(tmp, "googleffff0000aaaa1111.html"), "w", encoding="utf-8") as fh:
            fh.write("google-site-verification: googleffff0000aaaa1111.html")
        res = site.build(out, web, cfg, assets_dir=pics, verify_dirs=(tmp,))
        assert res["sitemap"] and res["proofs"] == ["google1a2b3c4d5e6f7a8b.html", "googleffff0000aaaa1111.html"]
        assert read("google1a2b3c4d5e6f7a8b.html") == "google-site-verification: google1a2b3c4d5e6f7a8b.html"
        assert read("googleffff0000aaaa1111.html").endswith("googleffff0000aaaa1111.html")
        site.build(out, web, dict(cfg, indicizzabile=False), assets_dir=pics)
        assert not os.path.exists(os.path.join(web, "sitemap.xml")) and "noindex,nofollow" in read("index.html")
        site.build(out, web, cfg, assets_dir=pics)
        assert len(site.reminders(site.load_config(None))) == 4 and site.reminders(cfg) == []
        assert "href='archivio/2026-11-13.html'" in index and "2026-11-12.html" not in index

        # a report with an unknown structure stops the build instead of publishing a broken page
        with open(os.path.join(out, "XAUUSD_briefing_2026-11-17.html"), "w", encoding="utf-8") as fh:
            fh.write("<html><body>altro</body></html>")
        try:
            site.build(out, web, cfg)
        except ValueError as ex:
            assert "2026-11-17" in str(ex)
        else:
            raise AssertionError("structure check missing")


_MARKET = {}


def market():
    """Real candles, downloaded once for the tests that need them."""
    if not _MARKET:
        _MARKET["h1"] = data.duka_candles(interval="1HOUR", bars=8000)
        _MARKET["m5"] = data.duka_candles(interval="5MIN", bars=15000, max_pages=6)
    return _MARKET["h1"], _MARKET["m5"]


def test_plain_pages():
    """End to end on real prices: two updates of one session and the first one of the next session, then the site.
    The recap of the concluded session is written once and agrees with the register; plain pages carry no jargon."""
    import re
    import tempfile
    from xau import extras, report, simple, site
    h1, m5 = market()
    days = [pd.Timestamp(d) for d in sorted(pd.unique(engine.tday_of(m5["time"])))][:-1]         # the running session left out
    d0, d1 = next((a, b) for a, b in zip(days[-2::-1], days[:0:-1]) if (b - a).days == 1)          # two sessions in a row
    replay = {"days": 250, "total": {"setups": 490, "trades": 221, "win": 38.0, "stop": 58.4, "avg": -0.0964, "se": 0.0826}}
    iso = lambda d: d.strftime("%Y-%m-%d")
    last = {}

    def run(out, d, hour, minute):
        now = pd.Timestamp(year=d.year, month=d.month, day=d.day, hour=hour, minute=minute, tz="Europe/Rome").tz_convert("UTC")
        hh, mm = h1[h1["time"] + engine.H <= now], m5[m5["time"] + pd.Timedelta(minutes=5) <= now]
        A = engine.analyze(hh, mm, now)
        X = {"generated": now, "historical": False}
        X["journal"] = extras.update_journal(out, mm, now)
        X["plan_status"] = extras.sync_book(out, A, mm, report.setup_title)
        X["prev_session"] = extras.previous_session(out, A, mm)
        with open(os.path.join(out, f"XAUUSD_briefing_{iso(A['tday'])}.html"), "w", encoding="utf-8") as fh:
            fh.write(report.build_html(A, X))
        extras.save_plan(out, A, {x["id"]: report.setup_title(x) for x in A["scenarios"]}, keep=X["plan_status"] is not None)
        v = simple.view(A, dict(X, replay=replay))
        simple.save(out, v)
        last["X"] = dict(X, replay=replay)
        return A, v, simple.finalize_past(out, A, mm)

    with tempfile.TemporaryDirectory() as tmp:
        out, web = os.path.join(tmp, "output"), os.path.join(tmp, "site")
        os.makedirs(out)
        A, v, n = run(out, d0, 1, 45)                                    # first plan of the session: the official one
        assert n == 0 and not v["final"] and v["market_open"] and v["tday"] == iso(d0) and json.loads(json.dumps(v)) == v
        assert len(v["ideas"]) == len(A["scenarios"]) and all(i["state"] == "in attesa" and i["official"] for i in v["ideas"])
        assert v["test"] == {"days": 250, "trades": 221, "win": 38.0, "avg": -0.096, "se": 0.083}
        A, v, n = run(out, d0, 15, 0)                                    # afternoon refresh: the official ideas are still there
        official = sorted(i["id"] for i in v["ideas"] if i["official"])
        assert n == 0 and v["tday"] == iso(d0) and simple.load(out, iso(d0)) == v and v["halted"] is False
        # holiday or early close: the session is formally running but no price has arrived for a while
        later = last["X"]["generated"] + pd.Timedelta(minutes=simple.HALT_MIN + 5)
        still = simple.view(A, dict(last["X"], generated=later))
        cards, waiting, _ = simple.shown_ideas(still)
        assert still["halted"] is True and waiting == [] and all(i["state"] == "aperto" for i in cards)
        assert still["summary"].startswith("Oro (XAU/USD): mercato fermo, ultimo prezzo ")
        page = simple.body(still, {"latest": True})
        assert "mercato fermo" in page and "Il mercato è fermo" in page and "Prezzo dell’oro adesso" not in page
        assert cards or "Mercato fermo: per ora nessuna idea." in page
        assert not jargon(page), jargon(page)
        blob = simple.page_data(still, True)
        assert blob["halted"] is True and blob["gen_ms"] == int(later.timestamp() * 1000)
        A, v, n = run(out, d1, 2, 0)                                     # next session: the recap of the previous one is written
        assert n == 1 and v["tday"] == iso(d1) and v["market_open"]
        rec0 = simple.load(out, iso(d0))
        assert rec0["final"] and sorted(i["id"] for i in rec0["ideas"] if i["official"]) == official
        assert all(i["state"] not in simple.LIVE_STATES for i in rec0["ideas"])
        with open(os.path.join(out, "plans", f"plan_{iso(d0)}.json"), encoding="utf-8") as fh:
            plan = json.load(fh)
        assert plan["evaluated"] and sorted(r["id"] for r in plan["results"]) == official
        for r in plan["results"]:                                        # same outcome as the register of the technical pages
            i = next(x for x in rec0["ideas"] if x["id"] == r["id"])
            res = simple.idea_money(i)[2]
            assert i["state"] == r["status"] and (res is None) == (r["entry_px"] is None), (i, r)
            assert res is None or abs(res) < 1e-6 or (res > 0) == (r["R"] > 0), (i, r)
        if v["prev"]:                                                    # "how it went yesterday" tells the same story
            states = {i["id"]: i["state"] for i in rec0["ideas"]}
            assert v["prev"]["day"] == iso(d0) and all(states[i["id"]] == i["state"] for i in v["prev"]["ideas"])
        assert run(out, d1, 2, 0)[2] == 0, "the recap of a concluded session is written once"
        res = site.build(out, web, site.load_config(None))
        assert res["sessions"] == 2 and res["latest"] == iso(d1)

        def read(rel):
            with open(os.path.join(web, rel), encoding="utf-8") as fh:
                return fh.read()

        index, past, arch, tech = read("index.html"), read(f"archivio/{iso(d0)}.html"), read("archivio/index.html"), read("dettagli/index.html")
        for name, text in (("index", index), ("past", past), ("arch", arch)):
            assert not jargon(text), (name, jargon(text))
        assert "Le idee di oggi" in index and "id='px'" in index and "id='banner'" in index and "Tendenza" in index
        blob = json.loads(re.search(r"id='oro-data'>(.*?)</script>", index).group(1))
        assert blob["live"] is True and blob["market_open"] is True and blob["prev_close"] == rec0["price"] and blob["since"] == "rispetto a ieri"
        assert blob["halted"] is False and blob["gen_ms"] > 0
        assert "Chiusura della giornata" in past and "Stai guardando una giornata passata" in past and "id='oro-data'" not in past
        assert "Giorno per giorno" in arch and arch.index(f"{iso(d1)}.html") < arch.index(f"{iso(d0)}.html")
        assert "id='plan-data'" in tech and "Torna alla pagina semplice" in tech
        if simple.tally(rec0["ideas"], True)["n"]:
            assert "Il bilancio dal" in index and "Provate su <b>250 giornate passate</b>" in index


def test_no_lookahead():
    h1, m5 = market()
    days = sorted(pd.unique(engine.tday_of(m5["time"])))[8:-2]
    rng = np.random.default_rng(7)
    for k in rng.choice(len(days), 8, replace=False):
        d = pd.Timestamp(days[k])
        asof = pd.Timestamp(year=d.year, month=d.month, day=d.day, hour=int(rng.choice([2, 8, 11, 15, 19])),
                            minute=int(rng.choice([0, 5, 35])), tz="Europe/Rome").tz_convert("UTC")
        full = engine.analyze(h1, m5, asof)
        past = engine.analyze(h1[h1["time"] + engine.H <= asof], m5[m5["time"] < asof], asof)
        key = lambda a: json.dumps([a["scenarios"], [(z["lo"], z["hi"], z["score"], z["prob"]) for z in a["zones"]],
                                    a["bias"]["score"], a["price"]], default=str, sort_keys=True)
        assert key(full) == key(past), f"look-ahead detected at {asof}"


if __name__ == "__main__":
    for fn in (test_conventions, test_evaluation, test_tracking, test_session_book, test_letters, test_calendar_week,
               test_week_stats, test_simple, test_site, test_plain_pages, test_no_lookahead):
        fn()
        print(f"ok  {fn.__name__}")
    print("Tutti i controlli superati.")
