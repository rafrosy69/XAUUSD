"""Static web site built from the briefings already written in the output folder.

    <site>/index.html              the session in progress, in plain words (price, direction, ideas of the day)
    <site>/archivio/index.html     every session, with how its ideas went
    <site>/archivio/<day>.html     one permanent plain page per session
    <site>/dettagli/index.html     technical briefing of the session in progress (the full report)
    <site>/dettagli/<day>.html     technical briefing of each session
    <site>/version.json            generation time of the latest briefing (open pages use it to offer a reload)

The plain pages are drawn by xau/simple.py from the small JSON summaries saved next to each report; the
technical pages are the stored reports with navigation and footer added. No market data is downloaded, so
the site can be rebuilt at any time (`run_briefing.py --site-only`). Every link is relative: the folder
works at the root of a domain, in a sub-folder (e.g. GitHub Pages project sites) and opened from disk.
"""
from __future__ import annotations

import glob
import html
import json
import os
import re
import shutil
import zipfile
from urllib.parse import quote

import pandas as pd

from . import report, simple
from .engine import ROME

DEFAULTS = {
    "titolo": "Oro oggi",
    "sottotitolo": ("Il prezzo dell'oro (XAU/USD) spiegato con parole semplici: tendenza, idee del giorno con entrata, "
                    "obiettivo e stop, appuntamenti importanti. Orari italiani."),
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
SITE_JS = r"""
(function(){
  var S=JSON.parse(document.getElementById('site-data').textContent), bar=document.getElementById('upd');
  if(!bar||!/^https?:$/.test(location.protocol)) return;   /* opened from disk or in a sandboxed preview: nothing to check */
  function check(){ fetch(S.root+'version.json?t='+Date.now(),{cache:'no-store'}).then(function(r){return r.ok?r.json():null;}).then(function(v){
    if(!v||!(v.generated_ms>S.generated_ms)) return;
    document.getElementById('upd-t').textContent=(v.tday!==S.tday?'È uscita la pagina di una nuova giornata':'È uscito un aggiornamento')+' ('+v.label+').';
    bar.style.display='flex'; }).catch(function(){}); }
  document.getElementById('upd-go').addEventListener('click',function(){ if(S.reload) location.reload(); else location.href=S.go; });
  check(); setInterval(check,180000);
  document.addEventListener('visibilitychange',function(){ if(!document.hidden) check(); });
})();
"""


def E(s) -> str:
    return html.escape(str(s), quote=True)


def day_label(ts: pd.Timestamp) -> str:
    return f"{report.GIORNI_S[ts.day_of_week]} {ts.day:02d}/{ts.month:02d}/{ts.year}"


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
    """One entry per session with a briefing in `out_dir` (historical rebuilds and the 'latest' copy excluded).
    `view` is the plain summary of the session: the one saved with the report or, for older sessions, a
    minimal one rebuilt from the official plan."""
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
        tech = blob.get("summary")                       # one-line description of the technical page
        if not tech:
            tech = f"Oro spot, briefing intraday di {report.d_long(ts)}."
            if plan:
                sc = " · ".join(f"{x['id']} {report.setup_brief(x)}" for x in plan["setups"])
                tech = (f"Oro spot, briefing intraday di {report.d_short(ts)}: trend {plan['bias'].lower()}."
                        + (f" Scenari: {sc}." if sc else ""))
        view = simple.load(out_dir, day)
        if view is None:
            try:
                view = simple.legacy_view(day, plan)
            except Exception:                            # unreadable plan: the page of that day only says so
                view = simple.legacy_view(day, None)
        days.append({"day": day, "ts": ts, "raw": raw, "blob": blob, "generated": gen, "plan": plan, "view": view,
                     "summary": view["summary"], "tech_summary": tech, "title": html.unescape(TITLE_RE.search(raw).group(1))})
    return days


def latest_replay(out_dir: str) -> dict | None:
    files = sorted(glob.glob(os.path.join(out_dir, "cache", "replay_*.json")))
    rp = _json(files[-1]) if files else None
    rp = rp.get("data") if isinstance(rp, dict) else None
    return rp if rp and rp.get("total") else None


# ----------------------------------------------------------------------------- page chrome
def _head(cfg: dict, title: str, desc: str, root: str, path: str, article: bool, dark: bool = False) -> str:
    theme = ("<meta name='theme-color' content='#0a0e13'><meta name='color-scheme' content='dark'>" if dark else
             "<meta name='theme-color' content='#f6f3ec' media='(prefers-color-scheme: light)'>"
             "<meta name='theme-color' content='#0c1015' media='(prefers-color-scheme: dark)'><meta name='color-scheme' content='light dark'>")
    tags = [f"<meta name='description' content='{E(desc)}'>",
            f"<meta name='robots' content='{'index,follow' if cfg['indicizzabile'] else 'noindex,nofollow'}'>", theme,
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
    """Navigation of the plain pages."""
    on_l = " class='on'" if here == "latest" else ""
    on_a = " class='on'" if here == "archive" else ""
    return (f"<header class='bar'><a class='brand' href='{root}index.html'>{ICON_SVG}<span>{E(cfg['titolo'])}</span></a>"
            f"<nav aria-label='Pagine del sito'><a href='{root}index.html'{on_l}>Oggi</a>"
            f"<a href='{root}archivio/index.html'{on_a}>Giorni passati</a></nav></header>")


def _bar_tech(cfg: dict, root: str) -> str:
    """Navigation of the technical pages (they keep the dark look of the report)."""
    return (f"<div class='sitebar' role='navigation' aria-label='Sito'><a class='brand' href='{root}index.html'>{ICON_SVG}"
            f"<span>{E(cfg['titolo'])}</span></a><div class='sitelinks'><a href='{root}index.html'>Oggi</a>"
            f"<a href='{root}archivio/index.html'>Giorni passati</a><a href='{root}dettagli/index.html' class='on'>Per chi fa trading</a>"
            f"</div></div>")


def _contact(c: str) -> str:
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", c):
        return f"<a href='mailto:{E(c)}'>{E(c)}</a>"
    if re.match(r"https?://", c):
        return f"<a href='{E(c)}' rel='noopener'>{E(c)}</a>"
    return E(c)


def _who(cfg: dict) -> str:
    if not (cfg["autore"] or cfg["contatto"] or cfg["interessi"]):
        return ""
    bits = []
    if cfg["autore"]:
        bits.append(f"Pubblicato da <b>{E(cfg['autore'])}</b>")
    if cfg["contatto"]:
        bits.append(f"contatto: {_contact(cfg['contatto'])}")
    line = " · ".join(bits)
    if cfg["interessi"]:
        line += ("<br>" if line else "") + f"<b>Interessi di chi pubblica.</b> {E(cfg['interessi'])}"
    return f"<p>{line}</p>"


def _foot(cfg: dict, pager: str = "") -> str:
    """Footer of the plain pages: who publishes, where the data come from, privacy."""
    src = ("<p>Da dove vengono i dati: prezzi dell'oro da Dukascopy, calendario degli appuntamenti da ForexFactory, prezzo in "
           "diretta da gold-api.com. Tutti gli orari sono italiani.</p>")
    privacy = ("<p>Questo sito non usa cookie e non raccoglie dati su chi lo legge. Il prezzo in diretta viene chiesto dal tuo "
               "browser direttamente a gold-api.com.</p>")
    return f"<footer class='foot'>{pager}{_who(cfg)}{src}{privacy}</footer>"


def _foot_tech(cfg: dict, pager: str = "") -> str:
    privacy = ("<p>Questo sito non usa cookie né strumenti di tracciamento. Il prezzo in tempo reale è richiesto dal browser "
               "di chi legge direttamente a gold-api.com.</p>")
    return f"<div class='sitefoot'>{pager}{_who(cfg)}{privacy}</div>"


def _update_bar(root: str, reload: bool, go: str, d: dict) -> str:
    """Notice shown by an open page when a newer briefing has been published."""
    site_data = {"root": root, "reload": reload, "go": go, "tday": d["day"], "generated_ms": int(d["generated"].timestamp() * 1000)}
    return ("<div class='upd' id='upd' role='status'><span id='upd-t'></span><button type='button' id='upd-go'>"
            f"{'Aggiorna' if reload else 'Apri'}</button></div>"
            f"<script type='application/json' id='site-data'>{json.dumps(site_data)}</script><script>{SITE_JS}</script>")


def _body(d: dict, opt: dict) -> tuple:
    """(html, view actually drawn). A summary that cannot be drawn (e.g. written by an older version of the
    programs) never breaks the site: the page falls back to what the official plan says."""
    try:
        return simple.body(d["view"], opt), d["view"]
    except Exception:
        try:
            v = simple.legacy_view(d["day"], d["plan"])
        except Exception:
            v = simple.legacy_view(d["day"], None)
        return simple.body(v, opt), v


def _document(title: str, head: str, top: str, body: str, foot: str, end: str = "") -> str:
    return (f"<!doctype html><html lang='it'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{E(title)}</title>{head}<style>{simple.CSS}</style></head><body><div class='wrap'>{top}<main>{body}</main>{foot}</div>"
            f"{end}</body></html>")


# ----------------------------------------------------------------------------- plain pages
def simple_page(d: dict, cfg: dict, root: str, is_index: bool, latest: dict, prev_d: dict | None, next_d: dict | None,
                rec: dict | None, test: dict | None) -> str:
    """Plain page of one session: the page of the session in progress (index and its permanent copy) or the
    recap of a past one."""
    is_latest = d["day"] == latest["day"]
    path = "" if is_index else f"archivio/{d['day']}.html"
    if is_index:
        title = f"{cfg['titolo']}: prezzo dell'oro, tendenza e idee del giorno (XAU/USD)"
    else:
        title = f"Oro, {simple.day_name(d['ts'], True)}: prezzo e idee del giorno — {cfg['titolo']}"
    head = _head(cfg, title, d["summary"], root, path, article=True)
    top = _bar(cfg, root, "latest" if is_latest else "")
    if not is_latest:
        top += (f"<div class='pastnote'><span>Stai guardando una giornata passata: <b>{simple.day_name(d['ts'], True)}</b>.</span>"
                f"<a href='{root}index.html'>Vai a oggi&nbsp;→</a></div>")
    opt = {"latest": is_latest, "record": rec, "test": test, "archive": f"{root}archivio/index.html",
           "details": f"{root}dettagli/{'index' if is_index else d['day']}.html"}
    links = []
    if prev_d:
        links.append(f"<a href='{root}archivio/{prev_d['day']}.html'>←&nbsp;{simple.day_short(prev_d['ts'])}</a>")
    links.append(f"<a href='{root}archivio/index.html'>Tutti i giorni</a>")
    if next_d:
        links.append(f"<a href='{root}archivio/{next_d['day']}.html'>{simple.day_short(next_d['ts'])}&nbsp;→</a>")
    foot = _foot(cfg, f"<div class='pager'>{''.join(links)}</div>")
    body, v = _body(d, opt)
    end = ""
    if is_latest and not v.get("final"):                 # live price and freshness notice
        end += (f"<script type='application/json' id='oro-data'>{json.dumps(simple.page_data(v, True))}</script>"
                f"<script>{simple.JS}</script>")
    if is_latest:                                        # only the current page offers the reload when a newer one is published
        end += _update_bar(root, is_index, f"{root}index.html", d)
    return _document(title, head, top, body, foot, end)


def archive_page(days: list[dict], cfg: dict, rec: dict | None, test: dict | None) -> str:
    latest = days[-1]
    title = f"Giorni passati — {cfg['titolo']}"
    desc = f"L'oro (XAU/USD) giorno per giorno: quanto si è mosso il prezzo e com'è andata con le idee pubblicate. {cfg['sottotitolo']}"
    entries = [{"view": d["view"], "href": f"{d['day']}.html", "latest": d is latest} for d in reversed(days)]
    head = _head(cfg, title, desc, "../", "archivio/", article=False)
    return _document(title, head, _bar(cfg, "../", "archive"), simple.archive_body(entries, rec, test), _foot(cfg))


def _checked(d: dict) -> dict:
    """The session with a summary that the list of the days and the scoreboard can read for sure."""
    try:
        simple.archive_body([{"view": d["view"], "href": "x.html", "latest": False}], None, None)
        simple.record([d["view"]])
        return d
    except Exception:
        try:
            v = simple.legacy_view(d["day"], d["plan"])
        except Exception:
            v = simple.legacy_view(d["day"], None)
        return dict(d, view=v, summary=v["summary"])


# ----------------------------------------------------------------------------- technical pages
def _inject(raw: str, head: str, css: str, top: str, foot: str, end: str) -> str:
    out = raw.replace("</title>", "</title>" + head, 1)          # first <title> only: charts carry their own <title> tooltips
    out = out.replace(A_CSS, css + A_CSS, 1)
    out = out.replace(A_TOP, A_TOP + top, 1)
    out = out.replace(A_FOOT, A_FOOT + foot, 1)
    return out.replace(A_END, end + A_END, 1)


def tech_page(d: dict, cfg: dict, is_index: bool, latest: dict, prev_d: dict | None, next_d: dict | None) -> str:
    """Technical briefing of one session (folder dettagli/): the stored report with navigation and footer."""
    root = "../"
    is_latest = d["day"] == latest["day"]
    path = "dettagli/" if is_index else f"dettagli/{d['day']}.html"
    head = _head(cfg, d["title"], d["tech_summary"], root, path, article=True, dark=True)
    top = _bar_tech(cfg, root)
    if is_latest:
        top += (f"<div class='sitenote'><span><b>Pagina per chi fa trading</b>: tutti i numeri, i livelli e le verifiche dietro le "
                f"idee di oggi.</span><a href='{root}index.html'>←&nbsp;Torna alla pagina semplice</a></div>")
    else:
        top += (f"<div class='sitenote'><span>Stai leggendo i numeri di <b>{report.d_long(d['ts'])}</b>: livelli e scenari "
                f"valevano per quella giornata.</span><a href='{root}archivio/{d['day']}.html'>←&nbsp;Pagina semplice di quel giorno</a></div>")
    links = []
    if prev_d:
        links.append(f"<a href='{prev_d['day']}.html'>←&nbsp;{day_label(prev_d['ts'])}</a>")
    links.append(f"<a href='{root}archivio/index.html'>Giorni passati</a>")
    if next_d:
        links.append(f"<a href='{next_d['day']}.html'>{day_label(next_d['ts'])}&nbsp;→</a>")
    if is_index:
        links.append(f"<a href='{d['day']}.html' title='Indirizzo che resterà valido anche dopo i prossimi aggiornamenti'>"
                     f"Link permanente a questo briefing</a>")
    foot = _foot_tech(cfg, f"<div class='pager'>{''.join(links)}</div>")
    end = _update_bar(root, is_index, "index.html", d) if is_latest else ""
    return _inject(d["raw"], head, SITE_CSS + ("" if is_latest else OLD_CSS), top, foot, end)


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
    """Addresses of the pages for the search engines (needs the public address of the site)."""
    base = cfg["indirizzo"]
    last = days[-1]["generated"].tz_convert(ROME).isoformat(timespec="seconds")
    urls = [(base, last), (base + "archivio/", last), (base + "dettagli/", last)]
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
    days = [_checked(d) for d in days]
    latest = days[-1]
    rec = simple.record([d["view"] for d in days if d is not latest])          # concluded sessions only
    test = latest["view"].get("test") or simple._test(latest_replay(out_dir))
    arch, tech = os.path.join(site_dir, "archivio"), os.path.join(site_dir, "dettagli")
    keep = {f"{d['day']}.html" for d in days} | {"index.html"}
    for folder in (arch, tech):
        os.makedirs(folder, exist_ok=True)
        for f in os.listdir(folder):                     # only pages written by this builder are ever removed
            if f not in keep and re.fullmatch(r"\d{4}-\d{2}-\d{2}\.html", f):
                os.remove(os.path.join(folder, f))
    for i, d in enumerate(days):
        prev_d = days[i - 1] if i > 0 else None
        next_d = days[i + 1] if i + 1 < len(days) else None
        _write(os.path.join(arch, f"{d['day']}.html"), simple_page(d, cfg, "../", False, latest, prev_d, next_d, rec, test))
        _write(os.path.join(tech, f"{d['day']}.html"), tech_page(d, cfg, False, latest, prev_d, next_d))
    before = days[-2] if len(days) > 1 else None
    _write(os.path.join(site_dir, "index.html"), simple_page(latest, cfg, "", True, latest, before, None, rec, test))
    _write(os.path.join(tech, "index.html"), tech_page(latest, cfg, True, latest, before, None))
    _write(os.path.join(arch, "index.html"), archive_page(days, cfg, rec, test))
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
    out = {"dir": site_dir, "pages": 2 * len(days) + 3, "sessions": len(days), "latest": latest["day"], "bytes": size,
           "generated": latest["generated"], "zip": None, "sitemap": os.path.exists(smap), "proofs": sorted(proofs)}
    if zip_path:
        make_zip(site_dir, zip_path)
        out["zip"] = zip_path
    return out
