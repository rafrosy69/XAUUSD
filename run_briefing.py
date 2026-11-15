#!/usr/bin/env python3
"""XAU/USD intraday briefing generator.

    python3 run_briefing.py                       # briefing with live data
    python3 run_briefing.py --asof "2026-11-11 08:00"   # rebuild a past briefing (Rome time)
    python3 run_briefing.py --no-replay            # skip the rule-replay statistics (faster)
    python3 run_briefing.py --site-only            # rebuild the web site from the briefings already in ./output

Outputs (folder ./output): XAUUSD_briefing_<date>.html / .md, XAUUSD_briefing_latest.html,
plans/plan_<date>.json, journal.csv
Web site (folder ./site + xauusd-sito.zip): index.html (latest briefing), archivio/ (one page per session)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from xau import data, engine, extras, report, site  # noqa: E402
from xau.charts import fnum  # noqa: E402


def log(msg: str, quiet: bool = False):
    if not quiet:
        print(msg, flush=True)


def load_calendar(cache_dir: str, now: pd.Timestamp, tday: pd.Timestamp) -> list[dict]:
    """Macro calendar for the week of the session `tday`, refreshed at most every 6 hours (the public feed
    is rate-limited); falls back to the cached copy when the feed is unreachable. The feed switches to the
    new week during the weekend: it is kept only when it really covers the week of the session, so a
    briefing prepared on Saturday never shows last week's agenda as if it were next week's."""
    os.makedirs(cache_dir, exist_ok=True)
    key = tday.strftime("%G-W%V")
    path = os.path.join(cache_dir, f"calendar_{key}.json")
    cached = None
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                cached = json.load(fh)
        except Exception:
            cached = None
    if cached and (now - pd.Timestamp(cached["fetched"])) < pd.Timedelta(hours=6):
        items = cached["items"]
    else:
        fresh = data.ff_calendar()
        in_week = sum(1 for x in fresh if x["time"].tz_convert(engine.ROME).strftime("%G-W%V") == key)
        if fresh and in_week >= 3:
            items = [dict(x, time=x["time"].isoformat()) for x in fresh]
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"fetched": now.isoformat(), "items": items}, fh, ensure_ascii=False)
        else:
            items = cached["items"] if cached else []
    return [dict(x, time=pd.Timestamp(x["time"])) for x in items]


def publish(a, quiet: bool = False) -> bool:
    """Rebuild the static site next to the output folder. Never blocks the briefing: problems are reported."""
    out = os.path.abspath(a.out)
    site_dir = os.path.abspath(a.site) if a.site else os.path.join(os.path.dirname(out), "site")
    try:
        cfg = site.load_config(a.site_config or os.path.join(HERE, "site.json"))
        res = site.build(out, site_dir, cfg, assets_dir=os.path.join(HERE, "assets"), verify_dirs=(HERE,),
                         zip_path=None if a.no_zip else os.path.join(os.path.dirname(site_dir), site.ZIP_ROOT + ".zip"))
    except Exception as ex:
        print(f"ATTENZIONE: sito non aggiornato ({ex}).", file=sys.stderr)
        return False
    if res is None:
        log("Sito: nessun briefing da pubblicare.", quiet)
        return False
    log(f"Sito: {res['dir']}/index.html · {res['sessions']} sedute in archivio · {res['bytes'] / 1e6:.1f} MB"
        + (f" · pacchetto {res['zip']}" if res["zip"] else ""), quiet)
    if res["sitemap"] or res["proofs"]:
        log("      motori di ricerca: " + ", ".join((["sitemap.xml"] if res["sitemap"] else []) + res["proofs"]), quiet)
    for r in site.reminders(cfg):
        log(f"      site.json · {r}", quiet)
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Briefing intraday XAU/USD")
    ap.add_argument("--asof", help="istante di analisi in ora di Roma (es. '2026-11-11 08:00'); default: adesso")
    ap.add_argument("--out", default=os.path.join(HERE, "output"), help="cartella di output")
    ap.add_argument("--context", help="file JSON con note/eventi curati (default: context/<data>.json)")
    ap.add_argument("--no-replay", action="store_true", help="salta la replica statistica delle regole")
    ap.add_argument("--replay-days", type=int, default=250, help="sedute usate per la replica delle regole")
    ap.add_argument("--site", help="cartella del sito da pubblicare (default: 'site' accanto alla cartella di output)")
    ap.add_argument("--site-config", help="impostazioni del sito (default: site.json)")
    ap.add_argument("--no-site", action="store_true", help="non aggiornare il sito")
    ap.add_argument("--no-zip", action="store_true", help="non creare xauusd-sito.zip accanto alla cartella del sito")
    ap.add_argument("--site-only", action="store_true", help="ricostruisce solo il sito dai briefing già presenti (nessun download)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    if a.site_only:
        return 0 if publish(a, a.quiet) else 1
    t0 = time.time()
    now = pd.Timestamp.now(tz="UTC")
    historical = bool(a.asof)
    end = pd.Timestamp(a.asof).tz_localize(engine.ROME).tz_convert("UTC") if historical else None

    log("1/5  Scarico i dati di mercato (Dukascopy)…", a.quiet)
    try:
        h1 = data.duka_candles(interval="1HOUR", bars=12000, end=end)
        cache_dir = os.path.join(a.out, "cache")
        replay = None if a.no_replay else extras.replay_cached(cache_dir, end or now, a.replay_days)
        need_long = not a.no_replay and replay is None
        m5 = data.duka_candles(interval="5MIN", bars=(a.replay_days + 12) * 280 if need_long else 12000, end=end, max_pages=40)
        m1 = None if historical else data.duka_candles(interval="1MIN", bars=600)
    except data.DataError as ex:
        print(f"ERRORE: dati di mercato non disponibili ({ex}). Riprovare tra qualche minuto.", file=sys.stderr)
        return 2
    m1_last = None
    if historical:
        asof = end
        m5_live = m5
    else:
        m5_end = m5["time"].iloc[-1] + pd.Timedelta(minutes=5)
        tail = m1[m1["time"] >= m5_end]
        m5_live = pd.concat([m5, engine.resample(tail, 5)], ignore_index=True) if len(tail) else m5
        asof = max(m1["time"].iloc[-1] + pd.Timedelta(minutes=1), m5_end)
        m1_last = float(m1["close"].iloc[-1])

    log("2/5  Analisi: livelli, zone, trend, scenari…", a.quiet)
    A = engine.analyze(h1, m5_live, asof)

    log("3/5  Dati di contorno: calendario, mercati collegati, controlli incrociati…", a.quiet)
    X = {"generated": now, "historical": historical}
    ctx_path = a.context or os.path.join(HERE, "context", f"{A['tday'].strftime('%Y-%m-%d')}.json")
    if os.path.exists(ctx_path):
        with open(ctx_path, encoding="utf-8") as fh:
            X["context"] = json.load(fh)
    if not historical:
        X["calendar"] = load_calendar(os.path.join(a.out, "cache"), now, A["tday"])
        X["quality"] = extras.quality_check(A, m1_last)
        X["yields"] = extras.yields_snapshot()
    try:
        X["intermarket"], X["eurusd"] = extras.intermarket(h1, asof, A["price"], live=not historical)
    except Exception:
        X["intermarket"], X["eurusd"] = [], None

    if not a.no_replay:
        log("4/5  Replica delle regole sulle sedute passate…" + (" (già calcolata oggi)" if replay else ""), a.quiet)
        try:
            X["replay"] = replay or extras.replay_summary(h1, m5, asof, a.replay_days, cache_dir)
        except Exception as ex:  # statistics are optional: never block the briefing
            log(f"     (replica non disponibile: {ex})", a.quiet)
    os.makedirs(a.out, exist_ok=True)
    if not historical:
        X["journal"] = extras.update_journal(a.out, m5, now)
        X["plan_status"] = extras.sync_book(a.out, A, m5_live, report.setup_title)
        X["prev_session"] = extras.previous_session(a.out, A, m5)

    log("5/5  Scrivo il report…", a.quiet)
    html = report.build_html(A, X)
    md = report.build_markdown(A, X)
    tag = A["tday"].strftime("%Y-%m-%d") + ("_storico_" + asof.tz_convert(engine.ROME).strftime("%H%M") if historical else "")
    p_html = os.path.join(a.out, f"XAUUSD_briefing_{tag}.html")
    p_md = os.path.join(a.out, f"XAUUSD_briefing_{tag}.md")
    with open(p_html, "w", encoding="utf-8") as fh:
        fh.write(html)
    with open(p_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    if not historical:
        with open(os.path.join(a.out, "XAUUSD_briefing_latest.html"), "w", encoding="utf-8") as fh:
            fh.write(html)
        titles = {s["id"]: report.setup_title(s) for s in A["scenarios"]}
        extras.save_plan(a.out, A, titles, keep=X.get("plan_status") is not None)

    r = asof.tz_convert(engine.ROME)
    log("", a.quiet)
    log(f"XAU/USD {fnum(A['price'])} alle {r.strftime('%H:%M')} del {r.strftime('%d/%m/%Y')} (Roma) · trend: {A['bias']['label']} "
        f"({fnum(A['bias']['score'], 2, True)}) · ATR D1 {fnum(A['ind']['atr_d'], 0)} $ · RSI H1 {fnum(A['ind']['H1']['rsi'], 0)}", a.quiet)
    for s in A["scenarios"]:
        tg = " · ".join(f"T{i + 1} {fnum(t['price'])}" for i, t in enumerate(s["targets"]))
        ref = f"zona {fnum(s['zone_lo'])}–{fnum(s['zone_hi'])}" if s["type"] == "fade" else f"innesco {fnum(s['trigger'])}"
        log(f"  {s['id']} {report.setup_title(s):32s} {ref:28s} stop {fnum(s['stop'])}  {tg}", a.quiet)
    log(f"\nReport: {p_html}\nSintesi: {p_md}", a.quiet)
    if not historical and not a.no_site:
        publish(a, a.quiet)
    log(f"(tempo: {time.time() - t0:.0f} s)", a.quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
