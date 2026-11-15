#!/usr/bin/env python3
"""Self-test: conventions, mechanical evaluation and absence of look-ahead.

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


def test_site():
    """Web site built from the briefings: latest one as index, a permanent page per session, archive with the
    verified outcomes, relative links only (works at a domain root, in a sub-folder and from disk)."""
    import inspect
    import re
    import tempfile
    import zipfile
    from xau import report, site
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

    def plan(day, asof, results=None):
        p = {"tday": day, "asof": asof, "price": 3900.0, "bias": "Ribassista forte", "score": -0.9, "evaluated": results is not None,
             "setups": [dict(fade, title="Vendita su rimbalzo"), dict(brk, dir=-1, title="Rottura ribassista")]}
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
                 {"id": "B", "title": "Rottura ribassista", "status": "target 1", "R": 3.99, "entry_px": 3956.6, "exit_px": 3900.0}]
        for day, asof, res in (("2026-11-12", "2026-11-12T01:08:00+00:00", res12), ("2026-11-16", "2026-11-13T22:00:00+00:00", None)):
            with open(os.path.join(out, "plans", f"plan_{day}.json"), "w", encoding="utf-8") as fh:
                json.dump(plan(day, asof, res), fh)
        with open(os.path.join(out, "journal.csv"), "w", encoding="utf-8", newline="") as fh:
            fh.write("seduta,piano_delle,trend,setup,titolo,esito,R,ingresso,uscita\r\n"
                     "2026-11-12,02:08,Ribassista forte,A,Vendita su rimbalzo,non attivato,0.0,,\r\n"
                     "2026-11-12,02:08,Ribassista forte,B,Rottura ribassista,target 1,3.99,3956.616,3900.0\r\n")
        with open(os.path.join(out, "cache", "replay_2026-11-16.json"), "w", encoding="utf-8") as fh:
            json.dump({"sig": "x", "data": {"days": 250, "total": {"setups": 490, "trades": 221, "win": 38.0, "stop": 58.4,
                                                                    "avg": -0.0964, "se": 0.0826}}}, fh)
        zpath = os.path.join(tmp, "sito.zip")
        res = site.build(out, web, site.load_config(None), zip_path=zpath)
        assert res["sessions"] == 3 and res["latest"] == "2026-11-16" and res["generated"] == pd.Timestamp(gen, unit="ms", tz="UTC")
        made = sorted(os.path.relpath(os.path.join(b, f), web).replace(os.sep, "/") for b, _, fs in os.walk(web) for f in fs)
        assert made == [".nojekyll", "archivio/2026-11-12.html", "archivio/2026-11-13.html", "archivio/2026-11-16.html",
                        "archivio/index.html", "index.html", "version.json"], made
        read = lambda rel: open(os.path.join(web, rel), encoding="utf-8").read()
        index, last, first, arch = read("index.html"), read("archivio/2026-11-16.html"), read("archivio/2026-11-12.html"), read("archivio/index.html")
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
        for text in (index, last, first, arch):          # not indexed unless asked, no absolute address without a site URL
            assert text.count("<meta name='description'") == 1 and "noindex,nofollow" in text
            assert "rel='canonical'" not in text and "og:image" not in text and "apple-touch-icon" not in text
        assert "<title>XAU/USD — Briefing intraday 16/11/2026</title><meta name='description'" in index
        assert "A &lt;b&gt;&#x27;x&#x27;&lt;/b&gt;." in index and "<svg><title>tooltip</title></svg>" in index     # escaped, charts untouched
        # the current briefing offers the reload; past sessions carry the note and hide the expiry banner instead
        for text, is_index in ((index, True), (last, False)):
            assert "id='site-data'" in text and "Stai leggendo il briefing" not in text and "#banner{display:none" not in text
            assert json.loads(re.search(r"id='site-data'>(.*?)</script>", text).group(1)) == {
                "root": "" if is_index else "../", "index": is_index, "tday": "2026-11-16", "generated_ms": gen}
        assert "Link permanente a questo briefing" in index and "Link permanente" not in last
        assert "id='site-data'" not in first and "Stai leggendo il briefing di <b>giovedì 12 novembre 2026</b>" in first
        assert "#banner{display:none!important}" in first and "href='../archivio/2026-11-13.html'" in first
        # archive: newest first, outcomes of the official plans, honest totals
        assert arch.index("2026-11-16.html") < arch.index("2026-11-13.html") < arch.index("2026-11-12.html")
        assert "TARGET 1</span> <b class='pos'>+3,99R</b>" in arch and arch.count("ESITO A FINE SEDUTA") == 2
        assert "Piano non disponibile" in arch and "piano di ven 13/11, 23:00" in arch and "piano delle 02:08" in arch
        assert "<span class='pos'>+4,0R</span>" in arch and "<span class='neg'>−0,10R</span>" in arch and "non dice nulla" in arch
        with zipfile.ZipFile(zpath) as z:
            names = z.namelist()
        assert sorted(names) == sorted(site.ZIP_ROOT + "/" + m for m in made)
        again = site.build(out, web, site.load_config(None))
        assert again["zip"] is None and read("index.html") == index and read("archivio/index.html") == arch            # reproducible

        # public address, indexing, publisher: absolute links for previews; the archive can be capped
        cfg_path = os.path.join(tmp, "site.json")
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump({"indirizzo": "esempio.it/oro", "indicizzabile": True, "autore": "Mario <Rossi>", "contatto": "m@esempio.it",
                       "interessi": "Può detenere posizioni sull'oro.", "archivio_max": 2, "ignota": 1}, fh)
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
        assert not os.path.exists(os.path.join(web, "archivio", "2026-11-12.html")) and "2026-11-12" not in read("archivio/index.html")
        index, last = read("index.html"), read("archivio/2026-11-16.html")
        assert "index,follow" in index and "<link rel='canonical' href='https://esempio.it/oro/'>" in index
        assert "<link rel='canonical' href='https://esempio.it/oro/archivio/2026-11-16.html'>" in last
        assert "<meta property='og:image' content='https://esempio.it/oro/og.png'>" in last
        assert "Pubblicato da <b>Mario &lt;Rossi&gt;</b>" in index and "href='mailto:m@esempio.it'" in index
        assert "Può detenere posizioni sull&#x27;oro." in index and "non usa cookie" in index
        # search engines: page map only for a public, indexable site; ownership files published untouched
        smap = read("sitemap.xml")
        assert smap.count("<url>") == 4 and "<loc>https://esempio.it/oro/</loc>" in smap
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


def test_no_lookahead():
    h1 = data.duka_candles(interval="1HOUR", bars=8000)
    m5 = data.duka_candles(interval="5MIN", bars=15000, max_pages=6)
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
    for fn in (test_conventions, test_evaluation, test_tracking, test_session_book, test_calendar_week, test_week_stats,
               test_site, test_no_lookahead):
        fn()
        print(f"ok  {fn.__name__}")
    print("Tutti i controlli superati.")
