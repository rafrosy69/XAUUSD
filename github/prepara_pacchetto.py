#!/usr/bin/env python3
"""Prepara xauusd-github.zip, il pacchetto da caricare in un repository GitHub, e stampa i link diretti.

    python3 github/prepara_pacchetto.py --repo utente/repository

Il pacchetto si carica così com'è (un solo file, senza estrarlo): alla prima esecuzione il flusso
.github/workflows/aggiorna-sito.yml lo scompatta nel repository e lo elimina. I file stanno alla radice
dello zip, quindi chi preferisce può anche estrarlo e trascinare tutto il contenuto nel repository.
"""
import argparse
import fnmatch
import os
import zipfile
from urllib.parse import quote

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(HERE, "github", "aggiorna-sito.yml")
ALARM = os.path.join(HERE, "github", "orologio.yml")         # the clock that starts the updates, see PUBBLICAZIONE.md
SKIP_DIRS = {"site", "__pycache__", ".git", ".github"}
SKIP_FILES = ["*.zip", "*.pyc", "calendar_*.json", "GUIDA_GITHUB.html", ".DS_Store"]


def build(out: str, code_only: bool = False) -> int:
    """Whole project, or (code_only) just programs and settings: uploaded to a repository that is already
    running, the second one updates the code without touching the plans, the register and the notes."""
    skip = SKIP_DIRS | ({"output", "context"} if code_only else set())
    files = []
    for base, dirs, names in os.walk(HERE):
        dirs[:] = sorted(d for d in dirs if d not in skip)
        files += [os.path.join(base, f) for f in sorted(names) if not any(fnmatch.fnmatch(f, p) for p in SKIP_FILES)]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in files:
            z.write(f, os.path.relpath(f, HERE).replace(os.sep, "/"))
        z.write(WORKFLOW, ".github/workflows/aggiorna-sito.yml")
        z.write(ALARM, ".github/workflows/orologio.yml")
    return len(files) + 2


def links(repo: str, wf_name: str = "aggiorna-sito.yml") -> dict:
    """Direct links for a repository. `wf_name` is the file name the workflow gets in .github/workflows/."""
    with open(WORKFLOW, encoding="utf-8") as fh:
        text = fh.read()
    with open(ALARM, encoding="utf-8") as fh:
        alarm = fh.read()
    base = f"https://github.com/{repo}"
    owner, name = repo.split("/", 1)
    return {
        "environment": f"{base}/settings/environments/new",
        "orologio": f"{base}/new/main?filename={quote('.github/workflows/orologio.yml', safe='')}&value={quote(alarm, safe='')}",
        "pages": f"{base}/settings/pages",
        "upload": f"{base}/upload/main",
        "workflow": f"{base}/new/main?filename={quote('.github/workflows/' + wf_name, safe='')}&value={quote(text, safe='')}",
        "actions": f"{base}/actions",
        "site": f"https://{owner.lower()}.github.io/{name}/",
    }


GUIDE_CSS = """
*{box-sizing:border-box}
body{margin:0;background:#f6f8fa;color:#1f2328;font:17px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:26px 18px 60px}
h1{font-size:28px;line-height:1.2;margin:0 0 6px}
.lead{color:#59636e;margin:0 0 22px}
.step{background:#fff;border:1px solid #d1d9e0;border-radius:14px;padding:20px 22px;margin:0 0 18px}
.step h2{font-size:21px;margin:0 0 10px;display:flex;align-items:center;gap:12px}
.num{flex:none;width:38px;height:38px;border-radius:50%;background:#1f883d;color:#fff;display:grid;place-items:center;font-size:19px;font-weight:700}
.num.zero{background:#59636e}.num.ok{background:#0969da}
ol{margin:10px 0 0;padding-left:24px}li{margin:0 0 7px}
.go{display:inline-block;background:#0969da;color:#fff;text-decoration:none;font-weight:600;border-radius:8px;padding:9px 16px;margin:4px 0 2px}
.url{display:block;font:13px/1.4 ui-monospace,Consolas,monospace;color:#59636e;word-break:break-all;margin:2px 0 8px}
.gh{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;font-size:14px;line-height:1.4;border:1px solid #d1d9e0;border-radius:10px;background:#fff;padding:14px 16px;margin:14px 0 4px;position:relative}
.gh .cap{position:absolute;top:-10px;left:12px;background:#fff;color:#59636e;font-size:11.5px;padding:0 6px;letter-spacing:.3px}
.gh b{font-weight:600}
.btn{display:inline-block;border:1px solid #d1d9e0;background:#f6f8fa;border-radius:6px;padding:5px 12px;font-weight:600;font-size:14px}
.btn.green{background:#1f883d;border-color:#1a7f37;color:#fff}
.ring{outline:3px solid #cf222e;outline-offset:3px;border-radius:7px}
.tag{display:inline-block;background:#cf222e;color:#fff;font-size:12px;font-weight:700;border-radius:999px;padding:1px 9px;margin-left:10px;vertical-align:middle}
.menu{display:inline-block;border:1px solid #d1d9e0;border-radius:8px;box-shadow:0 6px 18px rgba(31,35,40,.12);margin-top:8px;min-width:230px;overflow:hidden}
.menu div{padding:8px 12px;border-top:1px solid #eaeef2}.menu div:first-child{border-top:0}
.drop{border:2px dashed #afb8c1;border-radius:10px;text-align:center;padding:22px 10px;color:#59636e}
.drop strong{display:block;color:#1f2328;font-size:17px;margin-bottom:10px}
.file{display:inline-block;background:#fff8c5;border:1px solid #d4a72c;border-radius:6px;padding:3px 10px;font-weight:600;font-family:ui-monospace,Consolas,monospace;font-size:13.5px}
.row{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
.path{font-family:ui-monospace,Consolas,monospace;font-size:13.5px;color:#0969da}
.dialog{border:1px solid #d1d9e0;border-radius:10px;padding:12px 14px;margin-top:12px;max-width:380px;box-shadow:0 6px 18px rgba(31,35,40,.12)}
.runs div{display:flex;gap:10px;align-items:center;padding:7px 0;border-top:1px solid #eaeef2}.runs div:first-child{border-top:0}
.dot{flex:none;width:16px;height:16px;border-radius:50%}
.dot.y{background:#d4a72c}.dot.g{background:#1a7f37}.dot.r{background:#cf222e}
.note{background:#ddf4ff;border:1px solid #54aeff;border-radius:10px;padding:12px 16px;margin:0 0 18px;font-size:15.5px}
.warn{background:#fff8c5;border-color:#d4a72c}
code{font-family:ui-monospace,Consolas,monospace;font-size:.92em;background:#eff2f5;border-radius:5px;padding:1px 5px}
@media (max-width:560px){body{font-size:16px}.step{padding:16px 15px}h1{font-size:23px}}
"""


def guide(repo: str, short: str = "") -> str:
    """Elementary illustrated guide (Italian) with the direct links for the given repository.
    `short` is an optional shortened address of the long pre-filled link, shown as text to copy."""
    import html
    L = links(repo)
    e = html.escape
    alt = (f"se il pulsante non si apre, copia questo indirizzo nel browser: <b>{e(short)}</b>" if short else
           "il link è molto lungo: se il pulsante non funziona, usa quello nel messaggio in chat")
    name = repo.split("/", 1)[1]
    return f"""<!doctype html><html lang='it'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Mettere online il briefing: 3 passi</title><style>{GUIDE_CSS}</style></head><body><div class='wrap'>
<h1>Mettere online il briefing: 3 passi</h1>
<p class='lead'>Repository <b>{e(repo)}</b>. Per ogni passo: apri la pagina indicata e fai clic dove vedi il bordo rosso nella figura.
Non serve scrivere nulla e non serve estrarre file.</p>
<div class='note'>Se i pulsanti blu di questa guida non si aprono, usa gli stessi link che trovi nel messaggio in chat.</div>

<div class='step'><h2><span class='num zero'>0</span>Prima: scarica il pacchetto</h2>
<ol><li>Scarica sul computer il file <span class='file'>xauusd-github.zip</span>. Finisce nella cartella <b>Download</b>.</li>
<li><b>Non aprirlo e non estrarlo</b>: va caricato così com'è.</li>
<li>Se in Download ce ne sono due con lo stesso nome (per esempio «xauusd-github (1).zip»), va bene uno qualsiasi.</li></ol></div>

<div class='step'><h2><span class='num'>1</span>Attiva la pubblicazione</h2>
<a class='go' href='{e(L["pages"])}' target='_blank' rel='noopener'>Apri la pagina «Pages»</a><span class='url'>{e(L["pages"])}</span>
<ol><li>Cerca il titolo <b>Build and deployment</b> e, sotto, la riga <b>Source</b>.</li>
<li>Clicca sul riquadro che dice <b>Deploy from a branch</b>: si apre un piccolo elenco.</li>
<li>Scegli <b>GitHub Actions</b>. Fatto: non c'è niente da salvare.</li></ol>
<div class='gh'><span class='cap'>come appare su GitHub</span><b style='font-size:16px'>Build and deployment</b><br><br><b>Source</b><br>
<span class='btn'>Deploy from a branch ▾</span><br>
<div class='menu'><div>Deploy from a branch</div><div><span class='ring'><b>GitHub Actions</b></span><span class='tag'>clicca qui</span></div></div></div></div>

<div class='step'><h2><span class='num'>2</span>Carica il pacchetto</h2>
<a class='go' href='{e(L["upload"])}' target='_blank' rel='noopener'>Apri la pagina di caricamento</a><span class='url'>{e(L["upload"])}</span>
<ol><li>Al centro c'è un grande riquadro. Clicca la scritta blu <b>choose your files</b>.</li>
<li>Nella finestra che si apre vai in <b>Download</b>, clicca <span class='file'>xauusd-github.zip</span> e poi <b>Apri</b>.</li>
<li>Aspetta che il nome del file compaia sotto il riquadro.</li>
<li>Scendi in fondo alla pagina e clicca il pulsante verde <b>Commit changes</b>.</li></ol>
<div class='gh'><span class='cap'>come appare su GitHub</span>
<div class='drop'><strong>Drag files here to add them to your repository</strong>Or <span class='ring' style='color:#0969da'>choose your files</span><span class='tag'>1° clic</span></div>
<p style='margin:12px 0 10px'><span class='file'>xauusd-github.zip</span></p>
<span class='btn green ring'>Commit changes</span><span class='tag'>2° clic</span></div></div>

<div class='step'><h2><span class='num'>3</span>Accendi l'aggiornamento automatico</h2>
<a class='go' href='{e(L["workflow"])}' target='_blank' rel='noopener'>Apri la pagina già compilata</a>
<span class='url'>{alt}</span>
<ol><li>Si apre una pagina piena di testo già scritto. <b>Non modificare nulla.</b></li>
<li>In alto a destra clicca il pulsante verde <b>Commit changes...</b></li>
<li>Compare una finestrella: clicca di nuovo il pulsante verde <b>Commit changes</b>.</li></ol>
<div class='gh'><span class='cap'>come appare su GitHub</span>
<div class='row'><span class='path'>{e(name)} / .github / workflows / aggiorna-sito.yml</span>
<span><span class='btn'>Cancel changes</span> <span class='btn green ring'>Commit changes...</span><span class='tag'>1° clic</span></span></div>
<div class='dialog'><b>Commit changes</b><br><span style='color:#59636e'>Commit directly to the main branch</span><br><br>
<span class='btn'>Cancel</span> <span class='btn green ring'>Commit changes</span><span class='tag'>2° clic</span></div></div></div>

<div class='step'><h2><span class='num ok'>✓</span>Fatto: aspetta qualche minuto</h2>
<p style='margin:0 0 8px'>GitHub ora prepara il briefing e pubblica il sito da solo. Servono da 3 a 5 minuti. Poi il sito è qui:</p>
<a class='go' href='{e(L["site"])}' target='_blank' rel='noopener'>Apri il sito</a><span class='url'>{e(L["site"])}</span>
<p style='margin:10px 0 6px'>Per vedere a che punto è:</p>
<a class='go' href='{e(L["actions"])}' target='_blank' rel='noopener'>Apri la pagina «Actions»</a><span class='url'>{e(L["actions"])}</span>
<div class='gh'><span class='cap'>come appare su GitHub</span><div class='runs'>
<div><span class='dot y'></span><span>pallino <b>giallo</b>: sta lavorando, aspetta</span></div>
<div><span class='dot g'></span><span>spunta <b>verde</b>: finito, il sito è online</span></div>
<div><span class='dot r'></span><span>croce <b>rossa</b>: qualcosa non è andato, scrivilo in chat</span></div></div></div></div>

<div class='note warn'><b>Da lì in poi non devi fare più nulla.</b> Il briefing si aggiorna da solo ogni 30 minuti dal lunedì al
venerdì, giorno e notte, e la domenica sera. Gli orari di GitHub sono indicativi: un aggiornamento può arrivare in ritardo.</div>
</div></body></html>
"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Pacchetto e link per GitHub")
    ap.add_argument("--repo", help="repository nella forma utente/nome")
    ap.add_argument("--out", default=os.path.join(HERE, "xauusd-github.zip"))
    ap.add_argument("--short", default="", help="indirizzo abbreviato del link già compilato, da mostrare nella guida")
    ap.add_argument("--file", default="aggiorna-sito.yml", help="nome del file del flusso nel repository")
    ap.add_argument("--aggiornamento", action="store_true",
                    help="prepara anche il pacchetto di aggiornamento: solo programmi e impostazioni, per un repository già attivo")
    ap.add_argument("--nome", default="xauusd-aggiornamento.zip",
                    help="nome del pacchetto di aggiornamento (un nome nuovo evita di confonderlo con uno scaricato in passato)")
    ap.add_argument("--solo-aggiornamento", action="store_true",
                    help="prepara soltanto il pacchetto di aggiornamento, senza quello completo")
    a = ap.parse_args()
    if not a.solo_aggiornamento:
        n = build(a.out)
        print(f"{a.out}: {n} file, {os.path.getsize(a.out) / 1e6:.2f} MB")
    if a.aggiornamento or a.solo_aggiornamento:
        upd = os.path.join(HERE, os.path.basename(a.nome))
        n = build(upd, code_only=True)
        print(f"{upd}: {n} file, {os.path.getsize(upd) / 1e6:.2f} MB")
    if a.repo:
        for k, v in links(a.repo, a.file).items():
            print(f"{k}: {v}")
        with open(os.path.join(HERE, "GUIDA_GITHUB.html"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(guide(a.repo, a.short))
        print("guida: GUIDA_GITHUB.html")
