"""Static web site built from the briefings already written in the output folder.

    <site>/index.html              latest briefing
    <site>/archivio/index.html     every session, with the verified outcome of its official plan
    <site>/archivio/<day>.html     one permanent page per session
    <site>/version.json            generation time of the latest briefing (open pages use it to offer a reload)

Pure post-processing of the report HTML: no market data is downloaded, so the site can be rebuilt at any
time (`run_briefing.py --site-only`). Every link is relative: the folder works at the root of a domain, in a
sub-folder (e.g. GitHub Pages project sites) and opened from disk.
"""
from __future__ import annotations

import csv
import glob
import html
import json
import os
import re
import shutil
import zipfile
from urllib.parse import quote

import pandas as pd

from . import report
from .charts import fnum
from .engine import ROME
from .extras import NO_TRADE

DEFAULTS = {
    "titolo": "XAU/USD · Monitor intraday",
    "sottotitolo": ("Briefing intraday sull'oro spot: trend, zone di supporto e resistenza, scenari con stop e "
                    "obiettivi, agenda macro in ora di Roma."),
    "autore": "",            # who publishes the page (shown in the footer)
    "contatto": "",          # e-mail address or link (shown in the footer)
    "interessi": "",         # disclosure of positions / interests in gold (shown in the footer)
    "indirizzo": "",         # public address of the site, e.g. https://nome.netlify.app/ (canonical links, link previews)
    "indicizzabile": False,  # False = search engines are asked not to index the pages
    "archivio_max": 0,       # 0 = keep every session; N = publish only the last N sessions
}
ZIP_ROOT = "xauusd-sito"
DAY_RE = re.compile(r"^XAUUSD_briefing_(\d{4}-\d{2}-\d{2})\.html$")
BLOB_RE = re.compile(r"<script type='application/json' id='plan-data'>(.*?)</script>", re.S)
GEN_RE = re.compile(r"<footer>Generato il (\d\d)/(\d\d)/(\d{4}) alle (\d\d):(\d\d)")
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
A_CSS, A_TOP, A_FOOT, A_END = "</style></head>", "<body><div class='wrap'>", "</footer>", "</body></html>"

ICON_SVG = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32' aria-hidden='true'>"
            "<rect width='32' height='32' rx='7' fill='#18212c'/>"
            "<path d='M10.2 11.5h11.6l4 10.5H6.2z' fill='#e3b341'/>"
            "<path d='M10.2 11.5h11.6l1.25 3.3H8.95z' fill='#f6dc8d'/></svg>")
ICON_URI = "data:image/svg+xml," + quote(ICON_SVG.replace(" aria-hidden='true'", ""), safe="")

SITE_CSS = """
.sitebar{display:flex;justify-content:space-between;align-items:center;gap:8px 14px;flex-wrap:wrap;margin:-4px 0 16px;padding-bottom:12px;border-bottom:1px solid var(--line)}
.brand{display:inline-flex;align-items:center;gap:9px;color:var(--text);text-decoration:none;font-weight:700;font-size:14px;letter-spacing:.1px}
.brand svg{width:24px;height:24px;flex:none}
.sitelinks{display:flex;gap:2px}
.sitelinks a{color:var(--muted);text-decoration:none;font-size:13px;padding:5px 11px;border-radius:7px;white-space:nowrap}
.sitelinks a:hover,.sitelinks a.on{color:var(--text);background:var(--panel2)}
.sitenote{display:flex;justify-content:space-between;align-items:center;gap:6px 16px;flex-wrap:wrap;margin:0 0 16px;padding:10px 14px;border-radius:10px;background:rgba(88,166,255,.08);border:1px solid rgba(88,166,255,.35);color:#c5d0dc;font-size:13px}
.sitenote b{color:var(--text)}
.sitenote a{white-space:nowrap;font-weight:600;text-decoration:none}
.sitefoot{margin-top:16px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:12px;line-height:1.55;text-align:center}
.sitefoot p{margin:9px auto 0;max-width:880px}
.sitefoot b{color:#c5d0dc;font-weight:600}
.pager{display:flex;justify-content:center;gap:6px;flex-wrap:wrap}
.pager a{color:var(--text);text-decoration:none;border:1px solid var(--line);background:var(--panel);border-radius:8px;padding:5px 12px;font-size:12.5px;white-space:nowrap}
.pager a:hover{border-color:var(--gold)}
.upd{display:none;position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:60;align-items:center;gap:12px;width:max-content;max-width:calc(100vw - 24px);padding:10px 12px 10px 16px;border-radius:12px;background:#15241c;border:1px solid rgba(46,189,133,.6);color:#d4f5e6;font-size:13.5px;line-height:1.4;box-shadow:0 10px 34px rgba(0,0,0,.55)}
.upd button{flex:none;background:var(--up);color:#06130d;border:0;border-radius:8px;padding:6px 13px;font:inherit;font-weight:700;font-size:13px;cursor:pointer}
@media print{.sitebar,.sitenote,.upd,.pager{display:none!important}}
"""
OLD_CSS = "#banner{display:none!important}"      # past sessions: the static note replaces the expiry banner
ARCH_CSS = """
.kpis.k4{grid-template-columns:repeat(4,minmax(0,1fr))}
.arow{display:grid;grid-template-columns:210px minmax(0,1fr) 78px;gap:8px 18px;padding:13px 2px;border-top:1px solid var(--line2);align-items:start}
.arow:first-child{border-top:0;padding-top:2px}.arow:last-child{padding-bottom:2px}
.a-day a{font-weight:700;font-size:14.5px;color:var(--text);text-decoration:none;white-space:nowrap}
.a-day a:hover{color:var(--gold)}
.a-day .mut{font-size:12px;margin-top:2px;line-height:1.45}
.a-sc{display:grid;gap:7px}
.sc{display:grid;grid-template-columns:23px minmax(0,1fr);gap:9px;align-items:start;font-size:13.5px}
.sc-b{display:flex;align-items:center;gap:4px 9px;flex-wrap:wrap;min-height:23px}
.a-r{text-align:right;font-weight:700;font-size:14.5px;white-space:nowrap}
.a-r .mut{font-weight:400}
.newtag{display:inline-block;margin-left:8px;font-size:10.5px;font-weight:700;color:var(--gold);border:1px solid rgba(227,179,65,.45);border-radius:5px;padding:0 5px;vertical-align:1px}
@media (max-width:760px){.kpis.k4{grid-template-columns:repeat(2,minmax(0,1fr))}.arow{grid-template-columns:minmax(0,1fr) auto}.a-sc{grid-column:1/-1;grid-row:2}}
"""
SITE_JS = r"""
(function(){
  var S=JSON.parse(document.getElementById('site-data').textContent), bar=document.getElementById('upd');
  if(!bar||!/^https?:$/.test(location.protocol)) return;   /* opened from disk or in a sandboxed preview: nothing to check */
  function check(){ fetch(S.root+'version.json?t='+Date.now(),{cache:'no-store'}).then(function(r){return r.ok?r.json():null;}).then(function(v){
    if(!v||!(v.generated_ms>S.generated_ms)) return;
    document.getElementById('upd-t').textContent=(v.tday!==S.tday?'È stato pubblicato il briefing di una nuova seduta':'È stato pubblicato un aggiornamento')+' ('+v.label+').';
    bar.style.display='flex'; }).catch(function(){}); }
  document.getElementById('upd-go').addEventListener('click',function(){ if(S.index) location.reload(); else location.href=S.root+'index.html'; });
  check(); setInterval(check,180000);
  document.addEventListener('visibilitychange',function(){ if(!document.hidden) check(); });
})();
"""


def E(s) -> str:
    return html.escape(str(s), quote=True)


def day_label(ts: pd.Timestamp) -> str:
    return f"{report.GIORNI_S[ts.dayofweek]} {ts.day:02d}/{ts.month:02d}/{ts.year}"


def stamp(ts: pd.Timestamp) -> str:
    r = ts.tz_convert(ROME)
    return f"{r.day:02d}/{r.month:02d}/{r.year} alle {report.hm(r)}"


# ----------------------------------------------------------------------------- configuration
def load_config(path: str | None) -> dict:
    """Defaults overridden by the keys found in the JSON file (unknown keys are ignored)."""
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            user = json.load(fh)
        if not isinstance(user, dict):
            raise ValueError("il file di configurazione del sito deve contenere un oggetto JSON")
        for k in DEFAULTS:
            if user.get(k) is not None:
                cfg[k] = user[k]
    for k in ("titolo", "sottotitolo", "autore", "contatto", "interessi", "indirizzo"):
        cfg[k] = str(cfg[k]).strip()
    cfg["titolo"] = cfg["titolo"] or DEFAULTS["titolo"]
    base = cfg["indirizzo"]
    if base and not re.match(r"https?://", base):
        base = "https://" + base
    cfg["indirizzo"] = (base.rstrip("/") + "/") if base else ""
    cfg["indicizzabile"] = cfg["indicizzabile"] is True or str(cfg["indicizzabile"]).strip().lower() in ("true", "si", "sì", "1")
    try:
        cfg["archivio_max"] = max(0, int(cfg["archivio_max"] or 0))
    except (TypeError, ValueError):
        cfg["archivio_max"] = 0
    return cfg


def reminders(cfg: dict) -> list[str]:
    """What is still missing before the site is advertised as public (shown after every build)."""
    out = []
    if not cfg["autore"]:
        out.append("«autore» vuoto: la pagina non dice chi la pubblica")
    if not cfg["interessi"]:
        out.append("«interessi» vuoto: manca la dichiarazione sulle posizioni in oro di chi pubblica")
    if not cfg["indirizzo"]:
        out.append("«indirizzo» vuoto: niente indirizzi canonici né immagine di anteprima nei link condivisi")
    if not cfg["indicizzabile"]:
        out.append("«indicizzabile» è false: ai motori di ricerca viene chiesto di non indicizzare le pagine")
    return out


# ----------------------------------------------------------------------------- what has been published
def _json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def collect(out_dir: str) -> list[dict]:
    """One entry per session with a briefing in `out_dir` (historical rebuilds and the 'latest' copy excluded)."""
    days = []
    for path in sorted(glob.glob(os.path.join(out_dir, "XAUUSD_briefing_*.html"))):
        m = DAY_RE.match(os.path.basename(path))
        if not m:
            continue
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
        missing = [a for a in (A_CSS, A_TOP, A_FOOT, A_END) if raw.count(a) != 1]
        bm = BLOB_RE.search(raw)
        if missing or not bm or not TITLE_RE.search(raw):
            raise ValueError(f"{os.path.basename(path)}: struttura del report non riconosciuta, sito non aggiornato")
        blob = json.loads(bm.group(1))
        day = m.group(1)
        ts = pd.Timestamp(day)
        if blob.get("generated_ms"):
            gen = pd.Timestamp(int(blob["generated_ms"]), unit="ms", tz="UTC")
        else:                                            # reports written before the site existed
            g = GEN_RE.search(raw)
            gen = (pd.Timestamp(year=int(g.group(3)), month=int(g.group(2)), day=int(g.group(1)), hour=int(g.group(4)),
                                minute=int(g.group(5)), tz=ROME).tz_convert("UTC") if g
                   else pd.Timestamp(int(blob["asof_ms"]), unit="ms", tz="UTC"))
        plan = _json(os.path.join(out_dir, "plans", f"plan_{day}.json"))
        summary = blob.get("summary")
        if not summary:
            summary = f"Oro spot, briefing intraday di {report.d_long(ts)}."
            if plan:
                sc = " · ".join(f"{s['id']} {report.setup_brief(s)}" for s in plan["setups"])
                summary = (f"Oro spot, briefing intraday di {report.d_short(ts)}: trend {plan['bias'].lower()}."
                           + (f" Scenari: {sc}." if sc else ""))
        days.append({"day": day, "ts": ts, "raw": raw, "blob": blob, "generated": gen, "plan": plan, "summary": summary,
                     "title": html.unescape(TITLE_RE.search(raw).group(1))})
    return days


def journal_stats(out_dir: str) -> dict:
    rows = []
    path = os.path.join(out_dir, "journal.csv")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    tr = [r for r in rows if r["esito"] not in NO_TRADE]
    return {"setups": len(rows), "trades": len(tr), "tot_R": sum(float(r["R"]) for r in tr),
            "sessions": len({r["seduta"] for r in rows})}


def latest_replay(out_dir: str) -> dict | None:
    files = sorted(glob.glob(os.path.join(out_dir, "cache", "replay_*.json")))
    rp = _json(files[-1]) if files else None
    rp = rp.get("data") if isinstance(rp, dict) else None
    return rp if rp and rp.get("total") else None


# ----------------------------------------------------------------------------- page chrome
def _head(cfg: dict, title: str, desc: str, root: str, path: str, article: bool) -> str:
    tags = [f"<meta name='description' content='{E(desc)}'>",
            f"<meta name='robots' content='{'index,follow' if cfg['indicizzabile'] else 'noindex,nofollow'}'>",
            "<meta name='theme-color' content='#0a0e13'><meta name='color-scheme' content='dark'>",
            f"<link rel='icon' type='image/svg+xml' href='{ICON_URI}'>",
            f"<meta property='og:type' content='{'article' if article else 'website'}'>",
            f"<meta property='og:site_name' content='{E(cfg['titolo'])}'>",
            f"<meta property='og:title' content='{E(title)}'>",
            f"<meta property='og:description' content='{E(desc)}'>",
            "<meta property='og:locale' content='it_IT'>"]
    assets = cfg.get("_assets", ())                      # images actually copied into the site
    if "apple-touch-icon.png" in assets:
        tags.append(f"<link rel='apple-touch-icon' href='{root}apple-touch-icon.png'>")
    if cfg["autore"]:
        tags.append(f"<meta name='author' content='{E(cfg['autore'])}'>")
    base = cfg["indirizzo"]
    card = "summary"
    if base:                                             # link previews need absolute addresses
        tags += [f"<link rel='canonical' href='{E(base + path)}'>", f"<meta property='og:url' content='{E(base + path)}'>"]
        if "og.png" in assets:
            tags += [f"<meta property='og:image' content='{E(base)}og.png'>", "<meta property='og:image:width' content='1200'>",
                     "<meta property='og:image:height' content='630'>"]
            card = "summary_large_image"
    tags.append(f"<meta name='twitter:card' content='{card}'>")
    return "".join(tags)


def _bar(cfg: dict, root: str, here: str) -> str:
    on_l = " class='on'" if here == "latest" else ""
    on_a = " class='on'" if here == "archive" else ""
    return (f"<div class='sitebar' role='navigation' aria-label='Sito'><a class='brand' href='{root}index.html'>{ICON_SVG}"
            f"<span>{E(cfg['titolo'])}</span></a><div class='sitelinks'><a href='{root}index.html'{on_l}>Ultimo briefing</a>"
            f"<a href='{root}archivio/index.html'{on_a}>Archivio</a></div></div>")


def _contact(c: str) -> str:
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", c):
        return f"<a href='mailto:{E(c)}'>{E(c)}</a>"
    if re.match(r"https?://", c):
        return f"<a href='{E(c)}' rel='noopener'>{E(c)}</a>"
    return E(c)


def _foot(cfg: dict, root: str, pager: str = "", warning: bool = True) -> str:
    who = ""
    if cfg["autore"] or cfg["contatto"] or cfg["interessi"]:
        bits = []
        if cfg["autore"]:
            bits.append(f"Pubblicato da <b>{E(cfg['autore'])}</b>")
        if cfg["contatto"]:
            bits.append(f"contatto: {_contact(cfg['contatto'])}")
        line = " · ".join(bits)
        if cfg["interessi"]:
            line += ("<br>" if line else "") + f"<b>Interessi di chi pubblica.</b> {E(cfg['interessi'])}"
        who = f"<p>{line}</p>"
    warn = ("<p><b>Avvertenza.</b> Analisi informativa prodotta in automatico da regole meccaniche, uguale per tutti i lettori: "
            "non è consulenza finanziaria né una raccomandazione personalizzata. La verifica storica pubblicata nei briefing "
            "mostra che queste regole non hanno un vantaggio statistico dimostrato. Il trading con leva comporta un rischio "
            "elevato di perdita rapida del capitale.</p>") if warning else ""
    src = ("<p>Dati: candele spot XAU/USD dal feed pubblico Dukascopy; calendario macro ForexFactory; prezzo in tempo reale "
           "indicativo gold-api.com; controlli incrociati Swissquote e TradingView. Orari in ora di Roma. Metodo, fonti e "
           "avvertenze in fondo a ogni briefing.</p>") if warning else ""
    privacy = ("<p>Questo sito non usa cookie né strumenti di tracciamento. Il prezzo in tempo reale è richiesto dal browser "
               "di chi legge direttamente a gold-api.com.</p>")
    return f"<div class='sitefoot'>{pager}{warn}{who}{src}{privacy}</div>"


def _inject(raw: str, head: str, css: str, top: str, foot: str, end: str) -> str:
    out = raw.replace("</title>", "</title>" + head, 1)          # first <title> only: charts carry their own <title> tooltips
    out = out.replace(A_CSS, css + A_CSS, 1)
    out = out.replace(A_TOP, A_TOP + top, 1)
    out = out.replace(A_FOOT, A_FOOT + foot, 1)
    return out.replace(A_END, end + A_END, 1)


def briefing_page(d: dict, cfg: dict, root: str, is_index: bool, latest: dict, prev_d: dict | None, next_d: dict | None) -> str:
    is_latest = d["day"] == latest["day"]
    path = "" if is_index else f"archivio/{d['day']}.html"
    head = _head(cfg, d["title"], d["summary"], root, path, article=True)
    top = _bar(cfg, root, "latest" if is_latest else "")
    if not is_latest:
        top += (f"<div class='sitenote'><span>Stai leggendo il briefing di <b>{report.d_long(d['ts'])}</b>: livelli e scenari "
                f"valevano per quella seduta.</span><a href='{root}index.html'>Vai all'ultimo briefing&nbsp;→</a></div>")
    links = []
    if prev_d:
        links.append(f"<a href='{root}archivio/{prev_d['day']}.html'>←&nbsp;{day_label(prev_d['ts'])}</a>")
    links.append(f"<a href='{root}archivio/index.html'>Archivio dei briefing</a>")
    if next_d:
        links.append(f"<a href='{root}archivio/{next_d['day']}.html'>{day_label(next_d['ts'])}&nbsp;→</a>")
    if is_index:
        links.append(f"<a href='archivio/{d['day']}.html' title='Indirizzo che resterà valido anche dopo i prossimi aggiornamenti'>"
                     f"Link permanente a questo briefing</a>")
    foot = _foot(cfg, root, f"<div class='pager'>{''.join(links)}</div>", warning=False)
    end = ""
    if is_latest:                                        # only the current page offers the reload when a newer one is published
        site_data = {"root": root, "index": is_index, "tday": d["day"], "generated_ms": int(d["generated"].timestamp() * 1000)}
        end = ("<div class='upd' id='upd' role='status'><span id='upd-t'></span><button type='button' id='upd-go'>"
               f"{'Ricarica' if is_index else 'Apri'}</button></div>"
               f"<script type='application/json' id='site-data'>{json.dumps(site_data)}</script><script>{SITE_JS}</script>")
    return _inject(d["raw"], head, SITE_CSS + ("" if is_latest else OLD_CSS), top, foot, end)


# ----------------------------------------------------------------------------- archive index
def _plan_when(d: dict) -> str:
    asof = pd.Timestamp(d["plan"]["asof"]).tz_convert(ROME)
    if asof.strftime("%Y-%m-%d") == d["day"]:
        return f"piano delle {report.hm(asof)}"
    return f"piano di {report.d_short(asof)}, {report.hm(asof)}"


def _archive_row(d: dict, is_latest: bool) -> str:
    plan = d["plan"]
    tag = "<span class='newtag'>ULTIMO</span>" if is_latest else ""
    if not plan:
        return (f"<div class='arow'><div class='a-day'><a href='{d['day']}.html'>{day_label(d['ts'])}</a>{tag}</div>"
                f"<div class='a-sc mut'>Piano non disponibile.</div><div class='a-r mut'>—</div></div>")
    res = {r["id"]: r for r in plan.get("results", [])}
    lines, tot, traded = [], 0.0, False
    for s in plan["setups"]:
        r = res.get(s["id"])
        if r is None:
            pill = "<span class='pill neu'>ESITO A FINE SEDUTA</span>"
        else:
            label, cls = report.STATE_PILL.get(r["status"], (r["status"].upper(), "off"))
            pill = f"<span class='pill {cls}'>{E(label)}</span>"
            if r["status"] not in NO_TRADE:
                traded = True
                tot += float(r["R"])
                pill += f" <b class='{'pos' if r['R'] > 0 else ('neg' if r['R'] < 0 else 'mut')}'>{fnum(r['R'], 2, True)}R</b>"
        brief = report.setup_brief(s)
        lines.append(f"<div class='sc'><span class='sid'>{E(s['id'])}</span><div class='sc-b'><span>{E(brief[0].upper() + brief[1:])}</span>"
                     f"{pill}</div></div>")
    if not plan["setups"]:
        lines.append("<div class='mut'>Nessuno scenario: prezzo lontano dalle zone.</div>")
    if not plan.get("evaluated"):
        total = "<span class='mut'>—</span>"
    elif traded:
        total = f"<span class='{'pos' if tot > 0 else ('neg' if tot < 0 else 'mut')}'>{fnum(tot, 2, True)}R</span>"
    else:
        total = "<span class='mut'>0R</span>"
    return (f"<div class='arow'><div class='a-day'><a href='{d['day']}.html'>{day_label(d['ts'])}</a>{tag}"
            f"<div class='mut'>{E(plan['bias'])} · {_plan_when(d)}</div></div>"
            f"<div class='a-sc'>{''.join(lines)}</div><div class='a-r'>{total}</div></div>")


def archive_page(days: list[dict], cfg: dict, out_dir: str) -> str:
    latest = days[-1]
    js, rp = journal_stats(out_dir), latest_replay(out_dir)
    title = f"Archivio dei briefing — {cfg['titolo']}"
    desc = f"Tutti i briefing intraday sull'oro (XAU/USD) seduta per seduta, con l'esito verificato degli scenari. {cfg['sottotitolo']}"
    n = len(days)
    k = [f"<div class='kpi'><div class='l'>Sedute in archivio</div><div class='v'>{n}</div>"
         f"<div class='s'>dal {day_label(days[0]['ts'])[4:]}</div></div>",
         f"<div class='kpi'><div class='l'>Scenari verificati</div><div class='v'>{js['setups']}</div>"
         f"<div class='s'>{js['trades']} {'attivato' if js['trades'] == 1 else 'attivati'} dal mercato</div></div>",
         f"<div class='kpi'><div class='l'>Totale del registro</div><div class='v'>"
         f"<span class='{'pos' if js['tot_R'] > 0 else ('neg' if js['tot_R'] < 0 else 'mut')}'>{fnum(js['tot_R'], 1, True)}R</span></div>"
         f"<div class='s'>{'1 operazione' if js['trades'] == 1 else str(js['trades']) + ' operazioni'}</div></div>"]
    honest = ""
    if rp:
        t = rp["total"]
        k.append(f"<div class='kpi'><div class='l'>Replica su {rp['days']} sedute</div><div class='v'>"
                 f"<span class='{'pos' if t['avg'] > 0 else 'neg'}'>{fnum(t['avg'], 2, True)}R</span></div>"
                 f"<div class='s'>medio per operazione (±{fnum(t['se'], 2)})</div></div>")
        honest = (f" Fa fede la replica delle stesse regole su {rp['days']} sedute riportata in ogni briefing: {t['trades']} operazioni, "
                  f"risultato medio {fnum(t['avg'], 2, True)}R (±{fnum(t['se'], 2)}) prima dei costi, cioè nessun vantaggio statistico dimostrato.")
    small = ("Con così poche operazioni il totale del registro non dice nulla sulla bontà delle regole." if js["trades"] < 30
             else "Il totale del registro è al lordo di spread e slippage.")
    rows = "".join(_archive_row(d, d is latest) for d in reversed(days))
    body = (f"<div class='top'><div><h1><span class='sym'>XAU/USD</span> · Archivio dei briefing</h1>"
            f"<div class='sub'>Un briefing per seduta, con l'esito degli scenari verificato sui prezzi reali</div></div>"
            f"<div class='stamp'>Ultimo aggiornamento<br><b>{stamp(latest['generated'])}</b> (ora di Roma)</div></div>"
            f"<div class='kpis k4'>{''.join(k)}</div>"
            f"<section><h2>Sedute <small>dalla più recente</small></h2><div class='card'>{rows}</div>"
            f"<div class='honest'><b>Come leggere gli esiti.</b> Per ogni seduta conta il <b>piano ufficiale</b>, cioè l'ultimo "
            f"pubblicato prima dell'apertura di Londra (09:00, ora di Roma). L'esito è meccanico: ingresso solo se il prezzo "
            f"raggiunge la zona o conferma la rottura, uscita al primo obiettivo, allo stop o a fine seduta, stop mai spostato. "
            f"R è il risultato in multipli del rischio iniziale (distanza ingresso–stop). Gli esiti vengono aggiunti dopo la "
            f"chiusura e non sono mai corretti a posteriori. {small}{honest}</div></section>")
    head = _head(cfg, title, desc, "../", "archivio/", article=False)
    return (f"<!doctype html><html lang='it'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{E(title)}</title>{head}<style>{report.CSS}{SITE_CSS}{ARCH_CSS}</style></head><body><div class='wrap'>"
            f"{_bar(cfg, '../', 'archive')}{body}{_foot(cfg, '../')}</div></body></html>")


# ----------------------------------------------------------------------------- build
def _write(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def make_zip(site_dir: str, zip_path: str) -> int:
    """Zip the site inside a single top folder, ready to be extracted and uploaded as it is."""
    files = []
    for base, dirs, names in os.walk(site_dir):
        dirs.sort()
        files += [os.path.join(base, f) for f in sorted(names)]
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in files:
            z.write(f, os.path.join(ZIP_ROOT, os.path.relpath(f, site_dir)).replace(os.sep, "/"))
    return os.path.getsize(zip_path)


def sitemap(days: list[dict], cfg: dict) -> str:
    """Addresses of every page for the search engines (needs the public address of the site)."""
    base = cfg["indirizzo"]
    last = days[-1]["generated"].tz_convert(ROME).isoformat(timespec="seconds")
    urls = [(base, last), (base + "archivio/", last)]
    urls += [(f"{base}archivio/{d['day']}.html", d["generated"].tz_convert(ROME).isoformat(timespec="seconds")) for d in reversed(days)]
    rows = "".join(f"<url><loc>{E(u)}</loc><lastmod>{t}</lastmod></url>\n" for u, t in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + rows + "</urlset>\n")


def build(out_dir: str, site_dir: str, cfg: dict | None = None, assets_dir: str | None = None, zip_path: str | None = None,
          verify_dirs: tuple = ()) -> dict | None:
    """(Re)build the whole site from the briefings in `out_dir`. Returns None when there is nothing to publish.
    Ownership-verification files of the search engines (google*.html, BingSiteAuth.xml) found in the assets
    folder or in `verify_dirs` are published at the root of the site as they are."""
    assets = []
    if assets_dir and os.path.isdir(assets_dir):
        assets = [f for f in sorted(os.listdir(assets_dir)) if f.lower().endswith((".png", ".ico", ".svg", ".jpg", ".webp", ".txt"))]
    proofs = {}
    for folder in (assets_dir, *verify_dirs):
        if folder and os.path.isdir(folder):
            for f in sorted(os.listdir(folder)):
                if re.fullmatch(r"google[0-9A-Za-z_-]+\.html|BingSiteAuth\.xml", f):
                    proofs[f] = os.path.join(folder, f)
    cfg = dict(cfg or load_config(None), _assets=tuple(assets))
    days = collect(out_dir)
    if not days:
        return None
    if cfg["archivio_max"]:
        days = days[-cfg["archivio_max"]:]
    latest = days[-1]
    arch = os.path.join(site_dir, "archivio")
    os.makedirs(arch, exist_ok=True)
    keep = {f"{d['day']}.html" for d in days} | {"index.html"}
    for f in os.listdir(arch):                           # only pages written by this builder are ever removed
        if f not in keep and re.fullmatch(r"\d{4}-\d{2}-\d{2}\.html", f):
            os.remove(os.path.join(arch, f))
    for i, d in enumerate(days):
        prev_d = days[i - 1] if i > 0 else None
        next_d = days[i + 1] if i + 1 < len(days) else None
        _write(os.path.join(arch, f"{d['day']}.html"), briefing_page(d, cfg, "../", False, latest, prev_d, next_d))
    _write(os.path.join(site_dir, "index.html"),
           briefing_page(latest, cfg, "", True, latest, days[-2] if len(days) > 1 else None, None))
    _write(os.path.join(arch, "index.html"), archive_page(days, cfg, out_dir))
    gen_r = latest["generated"].tz_convert(ROME)
    version = {"generated_ms": int(latest["generated"].timestamp() * 1000), "generated": gen_r.isoformat(),
               "label": f"{gen_r.day:02d}/{gen_r.month:02d} alle {report.hm(gen_r)}", "tday": latest["day"],
               "asof_ms": int(latest["blob"]["asof_ms"]), "summary": latest["summary"]}
    _write(os.path.join(site_dir, "version.json"), json.dumps(version, ensure_ascii=False, indent=1) + "\n")
    _write(os.path.join(site_dir, ".nojekyll"), "")
    for f in assets:
        shutil.copyfile(os.path.join(assets_dir, f), os.path.join(site_dir, f))
    for f, src in proofs.items():
        shutil.copyfile(src, os.path.join(site_dir, f))
    smap = os.path.join(site_dir, "sitemap.xml")
    if cfg["indirizzo"] and cfg["indicizzabile"]:
        _write(smap, sitemap(days, cfg))
    elif os.path.exists(smap):                           # no longer public: do not leave a stale map behind
        os.remove(smap)
    size = 0
    for base, _, names in os.walk(site_dir):
        size += sum(os.path.getsize(os.path.join(base, f)) for f in names)
    out = {"dir": site_dir, "pages": len(days) + 2, "sessions": len(days), "latest": latest["day"], "bytes": size,
           "generated": latest["generated"], "zip": None, "sitemap": os.path.exists(smap), "proofs": sorted(proofs)}
    if zip_path:
        make_zip(site_dir, zip_path)
        out["zip"] = zip_path
    return out
