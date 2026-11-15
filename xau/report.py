"""HTML / Markdown rendering of the daily briefing (Italian, self-contained, no external assets)."""
from __future__ import annotations

import html
import json
import math

import pandas as pd

from . import charts, ta
from .charts import COL, fnum
from .engine import ROME, H

e = html.escape
GIORNI_L = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
GIORNI_S = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
MESI_L = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre",
          "ottobre", "novembre", "dicembre"]


# ----------------------------------------------------------------------------- small helpers
def rome(ts: pd.Timestamp) -> pd.Timestamp:
    return ts.tz_convert(ROME)


def d_long(ts) -> str:
    return f"{GIORNI_L[ts.dayofweek]} {ts.day} {MESI_L[ts.month - 1]} {ts.year}"


def d_short(ts) -> str:
    return f"{GIORNI_S[ts.dayofweek]} {ts.day:02d}/{ts.month:02d}"


def hm(ts) -> str:
    return f"{ts.hour:02d}:{ts.minute:02d}"


def pct(x, d=2, sign=True) -> str:
    return fnum(100 * x, d, sign) + "%"


def stars(n: int) -> str:
    return f'<span class="stars" title="forza della confluenza">{"★" * n}<span class="off">{"★" * (3 - n)}</span></span>'


def zrange(lo: float, hi: float) -> str:
    return fnum(lo) if abs(hi - lo) < 0.05 else f"{fnum(lo)}–{fnum(hi)}"


def prev_label(A: dict) -> str:
    return "Ieri" if (A["tday"] - pd.Timestamp(A["prev"]["tday"])).days == 1 else "Ultima seduta"


def week_stats(A: dict) -> dict | None:
    """Open/high/low/close of the week of the last completed session and its change on the previous week's
    close. Returned only when the next session belongs to a new week (weekend briefing)."""
    d1 = A["frames"]["d1"]
    d1 = d1[d1["tday"] < A["tday"]]
    if len(d1) < 8:
        return None
    last = pd.Timestamp(d1["tday"].iloc[-1])
    if last.strftime("%G-W%V") == A["tday"].strftime("%G-W%V"):
        return None
    wk = d1[d1["tday"].dt.strftime("%G-W%V") == last.strftime("%G-W%V")]
    before = d1[d1["tday"] < wk["tday"].iloc[0]]
    if not len(before):
        return None
    ref = float(before["close"].iloc[-1])
    return {"open": float(wk["open"].iloc[0]), "high": float(wk["high"].max()), "low": float(wk["low"].min()),
            "close": float(wk["close"].iloc[-1]), "ref": ref, "chg": float(wk["close"].iloc[-1] / ref - 1),
            "n": int(len(wk)), "first": pd.Timestamp(wk["tday"].iloc[0]), "last": last}


def reopen_text(A: dict) -> str:
    t = rome(A["bounds"]["open"])
    return f"{GIORNI_L[t.dayofweek]} {t.day:02d}/{t.month:02d} alle {hm(t)}"


def session_now(A: dict) -> tuple[str, str]:
    b, t = A["bounds"], A["asof"]
    if not A["market_open"]:
        return "chiuso", "Mercato chiuso"
    if t < b["london"]:
        return "asia", "Sessione asiatica"
    if t < b["ny"]:
        return "london", "Sessione di Londra"
    return "ny", "Sessione di New York"


# ----------------------------------------------------------------------------- calendar
EV_IT = {
    "Unemployment Claims": "Richieste settimanali di sussidi di disoccupazione",
    "Existing Home Sales": "Vendite di case esistenti",
    "Crude Oil Inventories": "Scorte settimanali di petrolio (EIA)",
    "Federal Budget Balance": "Saldo del bilancio federale",
    "30-y Bond Auction": "Asta Treasury a 30 anni",
    "10-y Bond Auction": "Asta Treasury a 10 anni",
    "Core PPI m/m": "Prezzi alla produzione core (PPI) m/m",
    "PPI m/m": "Prezzi alla produzione (PPI) m/m",
    "Core CPI m/m": "Inflazione core (CPI) m/m",
    "CPI m/m": "Inflazione (CPI) m/m",
    "CPI y/y": "Inflazione (CPI) a/a",
    "Core Retail Sales m/m": "Vendite al dettaglio core m/m",
    "Retail Sales m/m": "Vendite al dettaglio m/m",
    "Non-Farm Employment Change": "Nuovi occupati non agricoli (NFP)",
    "Unemployment Rate": "Tasso di disoccupazione",
    "Average Hourly Earnings m/m": "Retribuzioni orarie medie m/m",
    "ADP Non-Farm Employment Change": "Occupati ADP",
    "ISM Manufacturing PMI": "ISM manifatturiero",
    "ISM Services PMI": "ISM servizi",
    "JOLTS Job Openings": "Posti vacanti JOLTS",
    "Federal Funds Rate": "Decisione sui tassi Fed",
    "FOMC Statement": "Comunicato FOMC",
    "FOMC Press Conference": "Conferenza stampa FOMC",
    "FOMC Meeting Minutes": "Verbali FOMC",
    "Core PCE Price Index m/m": "Indice PCE core m/m",
    "Advance GDP q/q": "PIL USA (stima anticipata) t/t",
    "Prelim GDP q/q": "PIL (stima preliminare) t/t",
    "GDP m/m": "PIL m/m",
    "Flash GDP q/q": "PIL (stima flash) t/t",
    "Prelim UoM Consumer Sentiment": "Fiducia consumatori Univ. Michigan (prel.)",
    "CB Consumer Confidence": "Fiducia dei consumatori (Conference Board)",
    "Empire State Manufacturing Index": "Indice Empire State",
    "Philly Fed Manufacturing Index": "Indice Philadelphia Fed",
    "Mortgage Delinquencies": "Insolvenze sui mutui",
    "Bank Holiday": "Festività (mercati chiusi)",
    "ECB President Lagarde Speaks": "BCE: discorso della presidente Lagarde",
    "Fed Chair Powell Speaks": "Fed: discorso del presidente Powell",
    "Revised UoM Consumer Sentiment": "Fiducia consumatori Univ. Michigan (finale)",
    "Revised UoM Inflation Expectations": "Aspettative d'inflazione Univ. Michigan (finale)",
    "Prelim UoM Inflation Expectations": "Aspettative d'inflazione Univ. Michigan (prel.)",
    "Import Prices m/m": "Prezzi all'importazione m/m",
    "Industrial Production m/m": "Produzione industriale m/m",
    "Capacity Utilization Rate": "Utilizzo della capacità produttiva",
    "Building Permits": "Permessi di costruzione",
    "Housing Starts": "Nuovi cantieri residenziali",
    "Pending Home Sales m/m": "Compromessi di vendita di case m/m",
    "New Home Sales": "Vendite di case nuove",
    "Business Inventories m/m": "Scorte delle imprese m/m",
    "ADP Weekly Employment Change": "Occupati ADP (dato settimanale)",
    "Cleveland Fed Inflation Expectations": "Aspettative d'inflazione (Fed di Cleveland)",
    "CB Leading Index m/m": "Indice anticipatore (Conference Board) m/m",
    "Natural Gas Storage": "Stoccaggi di gas naturale",
    "API Weekly Statistical Bulletin": "Scorte di petrolio (API)",
    "NAHB Housing Market Index": "Indice NAHB del mercato immobiliare",
    "TIC Long-Term Purchases": "Acquisti esteri di titoli USA (TIC)",
    "Durable Goods Orders m/m": "Ordini di beni durevoli m/m",
    "Core Durable Goods Orders m/m": "Ordini di beni durevoli core m/m",
}


def ev_title(t: str) -> str:
    if t in EV_IT:
        return EV_IT[t]
    if t.startswith("FOMC Member ") and t.endswith(" Speaks"):
        return f"Fed: discorso di {t[12:-7]}"
    for head in ("Fed Chairman ", "Fed Chair "):
        if t.startswith(head) and t.endswith(" Speaks"):
            return f"Fed: discorso del presidente {t[len(head):-7]}"
    if t.endswith(" Speaks"):
        return f"Discorso: {t[:-7]}"
    return t


def ev_relevance(ev: dict) -> int:
    """0 = hide, 1 = low, 2 = medium, 3 = high relevance for gold (USD-centric)."""
    cur, imp, t = ev["currency"], ev["impact"], ev["title"]
    if imp == "Holiday":
        return 1 if cur == "USD" else 0
    if cur == "USD":
        if imp == "High":
            return 3
        if imp == "Medium" or "Powell" in t or "Fed Chair" in t:
            return 2
        return 1
    if imp == "High":
        return 1
    if imp == "Medium" and any(k in t for k in ("Lagarde", "ECB", "BOE", "BOJ", "Rate")):
        return 1
    return 0


def prepare_events(A: dict, cal: list[dict], ctx: dict) -> dict:
    """Events of the session day (Rome calendar day) and key events of the next day."""
    day = A["tday"].date()
    nxt = (A["tday"] + pd.Timedelta(days=1))
    while nxt.dayofweek >= 5:
        nxt += pd.Timedelta(days=1)
    over = ctx.get("event_overrides", [])
    today, tomorrow, week = [], [], []
    week_end = (A["tday"] + pd.Timedelta(days=4 - A["tday"].dayofweek)).date()      # Friday of the session's week
    for ev in cal:
        tr = rome(ev["time"])
        rel = ev_relevance(ev)
        note = ""
        for ov in over:
            if ov["match"].lower() in ev["title"].lower() and ov.get("date", str(tr.date())) == str(tr.date()):
                rel = ov.get("rel", rel)
                note = ov.get("note", note)
        if rel <= 0:
            continue
        item = {"t": tr, "utc": ev["time"], "cur": ev["currency"], "title": ev_title(ev["title"]),
                "raw": ev["title"], "rel": rel, "f": ev["forecast"], "p": ev["previous"], "note": note,
                "speech": "Speaks" in ev["title"], "past": ev["time"] < A["asof"]}
        if tr.date() == day:
            today.append(item)
        elif tr.date() == nxt.date() and rel >= 2:
            tomorrow.append(item)
        elif nxt.date() < tr.date() <= week_end and rel >= 2:
            week.append(item)
    for x in ctx.get("extra_events", []):
        tr = pd.Timestamp(x["time_rome"]).tz_localize(ROME)
        item = {"t": tr, "utc": tr.tz_convert("UTC"), "cur": x.get("currency", "USD"), "title": x["title"],
                "raw": x["title"], "rel": x.get("rel", 1), "f": x.get("forecast", ""), "p": x.get("previous", ""),
                "note": x.get("note", ""), "speech": x.get("speech", False), "past": tr.tz_convert("UTC") < A["asof"]}
        (today if tr.date() == day else tomorrow).append(item)
    today.sort(key=lambda x: x["t"])
    tomorrow.sort(key=lambda x: x["t"])
    week.sort(key=lambda x: x["t"])
    return {"today": today, "tomorrow": tomorrow, "week": week, "next_day": nxt, "available": bool(cal)}


def ev_num(s: str) -> str:
    """ForexFactory figures ('0.4%', '-298.6B', '194K') in Italian notation."""
    return s.replace(".", ",").replace("-", "\u2212") if s else s


def rel_dots(rel: int) -> str:
    lab = {1: "bassa", 2: "media", 3: "alta"}[rel]
    return f'<span class="rel r{rel}" title="rilevanza {lab} per l\'oro">{"●" * rel}<span class="off">{"●" * (3 - rel)}</span></span>'


# ----------------------------------------------------------------------------- text blocks
def trend_word(v: float) -> tuple[str, str]:
    if v >= 0.6:
        return "rialzista", "long"
    if v >= 0.25:
        return "moderatamente rialzista", "long"
    if v <= -0.6:
        return "ribassista", "short"
    if v <= -0.25:
        return "moderatamente ribassista", "short"
    return "neutrale", "neu"


# One-off study of 12/11/2026 (research/gestione_posizione.py): same entries as the rule replay, different exits.
MGMT_NOTE = ("In una verifica sulle stesse 250 sedute della replica (221 operazioni, studio del 12/11/2026) lo stop a pareggio "
             "dopo +1R o +1,5R, l'uscita anticipata a 1–2R e lo stop a inseguimento hanno spostato il risultato medio di appena "
             "−0,05/+0,02R rispetto alla regola base, contro un margine d'errore di ±0,08R sul risultato stesso, e con segno "
             "diverso nelle due metà del campione. Lo stop a pareggio dopo +1R ha ridotto le operazioni chiuse in perdita dal 62% "
             "al 55%, ma anche quelle in utile dal 38% al 33%: è una scelta sul profilo di rischio, non sul rendimento atteso.")

STATE_PILL = {"in attesa": ("IN ATTESA", "neu"), "non attivato": ("NON ATTIVATO", "off"), "annullato": ("ANNULLATO", "off"),
              "aperto": ("APERTA", "open"),
              "stop": ("STOP", "short"), "target 1": ("TARGET 1", "long"),
              "chiuso a fine sessione": ("CHIUSA A FINE SEDUTA", "off")}


def status_levels(r: dict) -> str:
    d = r["dir"]
    if r["type"] == "fade":
        ref = f"zona {zrange(r['zone_lo'], r['zone_hi'])}"
    elif r["type"] == "break":
        ref = f"chiusura M15 {'sotto' if d < 0 else 'sopra'} {fnum(r['trigger'])}"
    else:
        ref = f"chiusura H1 {'sopra' if d > 0 else 'sotto'} {fnum(r['trigger'])}"
    return ref + f" · stop {fnum(r['stop'])}" + (f" · T1 {fnum(r['t1'])}" if r["t1"] is not None else "")


def status_detail(r: dict, A: dict) -> str:
    """Plain-text account of what happened to a published setup (shared by HTML and Markdown)."""
    d, st, p = r["dir"], r["state"], A["price"]
    if r["entry_px"] is None:
        if st == "annullato":
            return ("La chiusura M15 di conferma è arrivata già oltre il primo obiettivo: movimento esaurito, scenario "
                    "annullato senza operazioni.")
        if st == "non attivato":
            return "Nessun ingresso entro il termine delle 21:00: scaduto senza operazioni."
        if r["type"] == "fade":
            ext = r["seen_hi"] if d < 0 else r["seen_lo"]
            seen = f" ({'massimo' if d < 0 else 'minimo'} dalla pubblicazione: {fnum(ext)})" if ext is not None else ""
            return (f"Prezzo d'ingresso {fnum(r['entry_plan'])} non ancora raggiunto{seen}; dista "
                    f"{fnum(abs(r['entry_plan'] - p), 0)} $ dal prezzo attuale.")
        if (p < r["trigger"]) if d < 0 else (p > r["trigger"]):
            return f"Prezzo oltre il livello d'innesco {fnum(r['trigger'])}, ma manca la chiusura M15 di conferma."
        return (f"Nessuna chiusura M15 {'sotto' if d < 0 else 'sopra'} {fnum(r['trigger'])} finora; il livello dista "
                f"{fnum(abs(r['trigger'] - p), 0)} $ dal prezzo attuale.")
    how = {"fade": "ordine a centro zona", "break": "chiusura M15 di conferma",
           "reclaim": "ritorno sul livello dopo la conferma H1"}[r["type"]]
    head = f"Ingresso alle {hm(rome(r['entry_time']))} a {fnum(r['entry_px'])} ({how}); rischio {fnum(r['risk'])} $/oz."
    if st == "aperto":
        pnl = d * (r["last"] - r["entry_px"])
        t1 = f"mancano {fnum(abs(r['last'] - r['t1']), 0)} $ a T1 e " if r["t1"] is not None else "mancano "
        return (head + f" Miglior prezzo toccato: {fnum(r['best'])} ({fnum(r['mfe'], 1, True)}R). Alle {hm(rome(A['asof']))}: "
                f"{fnum(r['R'], 1, True)}R ({fnum(pnl, 1, True)} $/oz); {t1}{fnum(abs(r['stop'] - r['last']), 0)} $ allo stop.")
    t_out = hm(rome(r["exit_time"]))
    if st == "stop":
        extra = (f" Prima dello stop il prezzo era arrivato a {fnum(r['best'])} ({fnum(r['mfe'], 1, True)}R)."
                 if r["mfe"] is not None and r["mfe"] >= 0.5 else "")
        return head + f" Stop colpito intorno alle {t_out}: {fnum(-1.0, 0, True)}R." + extra
    if st == "target 1":
        return (head + f" T1 raggiunto intorno alle {t_out}: {fnum(r['R'], 1, True)}R."
                + (" In seguito toccato anche T2." if r["t2"] else ""))
    return head + f" Chiusa a fine seduta a {fnum(r['exit_px'])}: {fnum(r['R'], 1, True)}R."


def status_bullet(A: dict, st: dict | None) -> str | None:
    """One-line recap of the setups already published today (first bullet of the summary)."""
    if not st or not st["rows"]:
        return None
    t0 = hm(rome(st["asof"]))
    if st["same"]:
        return f"<b>Piano ufficiale delle {t0}: confermato</b>, nessuno scenario ancora attivato."
    parts = []
    for r in st["rows"]:
        name = f"{e(r['id'])} · {e(r['title'])}"
        if r["state"] == "aperto":
            parts.append(f"{name}: <b>posizione aperta</b> dalle {hm(rome(r['entry_time']))} a {fnum(r['entry_px'])}, ora "
                         f"{fnum(r['R'], 1, True)}R (punta massima {fnum(r['mfe'], 1, True)}R), stop {fnum(r['stop'])}"
                         + (f", T1 {fnum(r['t1'])}" if r["t1"] is not None else ""))
        elif r["state"] == "stop":
            parts.append(f"{name}: <b>stop</b> intorno alle {hm(rome(r['exit_time']))} ({fnum(-1.0, 0, True)}R)")
        elif r["state"] == "target 1":
            parts.append(f"{name}: <b>T1 raggiunto</b> intorno alle {hm(rome(r['exit_time']))} ({fnum(r['R'], 1, True)}R)")
        elif r["state"] == "chiuso a fine sessione":
            parts.append(f"{name}: chiusa a fine seduta ({fnum(r['R'], 1, True)}R)")
        elif r["state"] == "in attesa":
            parts.append(f"{name}: in attesa")
        else:
            parts.append(f"{name}: {r['state']}")
    return f"<b>Scenari già pubblicati oggi</b> (piano ufficiale delle {t0}) — " + "; ".join(parts) + "."


def summary_bullets(A: dict, ev: dict, st: dict | None = None, pv: dict | None = None) -> list[str]:
    p, ind, bias, prev, td = A["price"], A["ind"], A["bias"], A["prev"], A["today"]
    f = bias["flags"]
    out = []
    for sb in (status_bullet(A, st), prev_bullet(pv, A)):
        if sb:
            out.append(sb)
    tfs = ", ".join(f"{k} {trend_word(v)[0]}" for k, v in bias["tf"].items())
    out.append(f"<b>Contesto di trend: {e(bias['label'].lower())}</b> (punteggio {fnum(bias['score'], 2, True)} su una scala "
               f"da −1 a +1). Per timeframe: {e(tfs)}. Struttura H1: {e(bias['structure']['text'])}.")
    pd_ = rome(pd.Timestamp(prev["tday"]).tz_localize("UTC") + 12 * H)
    out.append(f"<b>{prev_label(A)} ({d_short(pd_)})</b>: chiusura a {fnum(prev['close'])} ({pct(prev['chg'])}), range "
               f"{fnum(prev['low'])}–{fnum(prev['high'])} = {fnum(prev['range'], 0)} $ "
               f"({fnum(100 * prev['range'] / ind['atr_d'], 0)}% dell'ATR giornaliero).")
    if td:
        out.append(f"<b>Oggi finora</b>: apertura {fnum(td['open'])}, minimo {fnum(td['low'])}, massimo {fnum(td['high'])}; "
                   f"range {fnum(td['range'], 0)} $, pari al {fnum(100 * f['adr_used'], 0)}% del range medio giornaliero "
                   f"(ADR20 = {fnum(ind['adr20'], 0)} $).")
    if not A["market_open"]:
        ws = week_stats(A)
        if ws:
            out.append(f"<b>Settimana chiusa a {fnum(ws['close'])}: {pct(ws['chg'])}</b> rispetto ai {fnum(ws['ref'])} del venerdì "
                       f"precedente; massimo {fnum(ws['high'])}, minimo {fnum(ws['low'])} (escursione di {fnum(ws['high'] - ws['low'], 0)} $).")
        out.append(f"<b>Mercato chiuso</b>: riapre {reopen_text(A)} (ora di Roma). Livelli e scenari valgono per la prossima "
                   f"seduta; un'apertura in gap può scavalcarli.")
    rs = ind["H1"]["rsi"]
    if f["oversold_h1"] or f["stretched_down"]:
        out.append(f"<b>Mercato molto esteso al ribasso</b>: RSI H1 a {fnum(rs, 0)}, prezzo {fnum(abs(f['ext_atr']), 1)} ATR orari "
                   f"sotto la EMA20 H1 ({fnum(ind['H1']['ema20'])}). Un rimbalzo tecnico è plausibile: meglio attendere "
                   f"il prezzo sulle resistenze che inseguirlo sui minimi.")
    elif f["overbought_h1"] or f["stretched_up"]:
        out.append(f"<b>Mercato molto esteso al rialzo</b>: RSI H1 a {fnum(rs, 0)}, prezzo {fnum(abs(f['ext_atr']), 1)} ATR orari "
                   f"sopra la EMA20 H1 ({fnum(ind['H1']['ema20'])}). Un ritracciamento tecnico è plausibile: meglio attendere "
                   f"il prezzo sui supporti che inseguirlo sui massimi.")
    R = sorted([z for z in A["zones"] if z["side"] == "R" and z["stars"] >= 2], key=lambda z: z["lo"])[:3]
    S = sorted([z for z in A["zones"] if z["side"] == "S" and z["stars"] >= 2], key=lambda z: -z["hi"])[:3]
    if R or S:
        rtxt = " · ".join(f"{zrange(z['lo'], z['hi'])}" for z in R) or "n.d."
        stxt = " · ".join(f"{zrange(z['lo'], z['hi'])}" for z in S) or "n.d."
        out.append(f"<b>Zone chiave</b> — resistenze: {rtxt}. Supporti: {stxt}.")
    key = [x for x in ev["today"] if x["rel"] >= 2 and not x["past"]]
    if key:
        dayl = "" if A["market_open"] else f" {d_short(A['tday'])}"
        out.append(f"<b>Eventi da presidiare{dayl} (ora di Roma)</b>: " +
                   "; ".join(f"{hm(x['t'])} {e(x['title'])}" for x in key[:5]) + ".")
    elif ev["available"]:
        out.append("<b>Agenda</b>: nessun evento USA di rilievo nelle prossime ore della seduta.")
    return out


SETUP_NAMES = {
    ("fade", -1): "Vendita su rimbalzo", ("fade", 1): "Acquisto su ritracciamento",
    ("break", -1): "Rottura ribassista", ("break", 1): "Rottura rialzista",
    ("reclaim", 1): "Inversione rialzista confermata", ("reclaim", -1): "Inversione ribassista confermata",
}


def setup_title(s: dict) -> str:
    if s["role"] == "range":
        return "Vendita su resistenza (range)" if s["dir"] < 0 else "Acquisto su supporto (range)"
    return SETUP_NAMES[(s["type"], s["dir"])]


def setup_brief(s: dict) -> str:
    """'vendita su rimbalzo 3914,9–3923,0' / 'rottura ribassista sotto 3866,6' (plain text)."""
    t = setup_title(s)
    t = t[0].lower() + t[1:]
    if s["type"] == "fade":
        return f"{t} {zrange(s['zone_lo'], s['zone_hi'])}"
    return f"{t} {'sotto' if s['dir'] < 0 else 'sopra'} {fnum(s['trigger'])}"


def one_liner(A: dict) -> str:
    """Plain-text summary of the briefing in one line."""
    asof_r = rome(A["asof"])
    trend = A["bias"]["label"].lower()
    if A["market_open"]:
        head = f"Oro spot a {fnum(A['price'])} $ alle {hm(asof_r)} di {d_short(asof_r)}, trend {trend}."
        lead = "Scenari"
    else:
        head = (f"Oro spot: ultima chiusura {fnum(A['price'])} $ ({d_short(pd.Timestamp(A['prev']['tday']))}), "
                f"trend {trend}.")
        lead = f"Scenari per {d_short(A['tday'])}"
    S = A["scenarios"]
    if not S:
        return f"{head} Nessuno scenario soddisfa i criteri minimi: prezzo lontano dalle zone."
    return f"{head} {lead}: " + " · ".join(f"{x['id']} {setup_brief(x)}" for x in S) + "."


def setup_card(s: dict, A: dict) -> str:
    d = s["dir"]
    side, pill = ("short", "SHORT") if d < 0 else ("long", "LONG")
    rows = []
    zone = zrange(s["zone_lo"], s["zone_hi"])
    if s["type"] == "fade":
        edge = s["zone_lo"] if d < 0 else s["zone_hi"]
        rows.append(("Zona d'ingresso", f"<b class='big'>{zone}</b>"))
        rows.append(("Conferma", f"Reazione del prezzo dentro la zona su M5/M15 (candela di rifiuto, oppure chiusura M15 "
                                 f"di nuovo {'sotto' if d < 0 else 'sopra'} {fnum(edge)}). In alternativa ordine limite a "
                                 f"centro zona: <b>{fnum(s['entry'])}</b>."))
    elif s["type"] == "break":
        rows.append(("Innesco", f"<b class='big'>Chiusura M15 {'sotto' if d < 0 else 'sopra'} {fnum(s['trigger'])}</b>"))
        rows.append(("Ingresso", f"Sulla chiusura di conferma (≈ {fnum(s['entry'])}) oppure sul successivo ritorno a "
                                 f"{fnum(s['trigger'])} da {'sotto' if d < 0 else 'sopra'}."))
    else:
        rows.append(("Innesco", f"<b class='big'>Chiusura H1 {'sopra' if d > 0 else 'sotto'} {fnum(s['trigger'])}</b>"))
        rows.append(("Ingresso", f"Sul ritorno del prezzo a {fnum(s['entry'])} (ex {'resistenza' if d > 0 else 'supporto'} "
                                 f"{zone}), con ordine limite."))
    rows.append(("Stop", f"<b class='neg'>{fnum(s['stop'])}</b> <span class='mut'>· rischio {fnum(s['risk'])} $ per oncia "
                         f"({fnum(s['risk'] * 10, 0)} $ ogni 0,10 lotti)</span>"))
    tg = "<div class='tgs'>" + "".join(
        f"<div class='tg'><span>T{i + 1}</span><b>{fnum(t['price'])}</b><i>{fnum(t['rr'], 1)}R</i><em>{e(t['label'])}</em></div>"
        for i, t in enumerate(s["targets"])) + "</div>"
    rows.append(("Obiettivi", tg))
    if s.get("hurdles"):
        rows.append(("Ostacoli prima di T1", " · ".join(f"{zrange(h['lo'], h['hi'])} <span class='mut'>({fnum(h['rr'], 1)}R)</span>"
                                                        for h in s["hurdles"]) +
                     " <span class='mut'>— zone minori dove il prezzo può fermarsi</span>"))
    rows.append(("Confluenze", e(" · ".join(n.split(" = ")[0] for n in s["confluence"])) + f" &nbsp;{stars(s['stars'])}"))
    if s.get("prob_touch") is not None and not math.isnan(s["prob_touch"]):
        what = "alla zona d'ingresso" if s["type"] == "fade" else "al livello d'innesco"
        rows.append(("Raggiungibilità", f"<b>{fnum(100 * s['prob_touch'], 0)}%</b> <span class='mut'>· quota di sedute passate in cui, "
                                        f"da quest'ora, il prezzo ha coperto una distanza pari a quella che lo separa {what} prima della chiusura</span>"))
    notes = []
    if "stretched" in s["cautions"]:
        notes.append("Il mercato è già molto esteso in questa direzione: rischio elevato di falsa rottura e rimbalzo. "
                     "Size ridotta, oppure attendere un consolidamento o un catalizzatore (dati macro).")
    if "adr" in s["cautions"]:
        notes.append("Il range giornaliero medio è già quasi esaurito: lo spazio residuo per l'estensione è statisticamente limitato.")
    if s["targets"] and s["targets"][0]["rr"] < 1.2:
        notes.append("T1 vale circa 1R: il risultato dipende dal lasciar correre una parte della posizione verso T2.")
    if s.get("adds_to"):
        notes.append(f"C'è già una posizione aperta nella stessa direzione (scenario {s['adds_to']}): questo ingresso "
                     f"si sommerebbe a quella, aumentando il rischio complessivo.")
    if s.get("against"):
        notes.append(f"Direzione opposta alla posizione ancora aperta dello scenario {s['against']}.")
    if s.get("after_stop"):
        notes.append(f"Il prezzo può arrivare qui solo dopo aver fatto scattare lo stop della posizione aperta "
                     f"(scenario {s['after_stop']}): le due operazioni non si sommano.")
    body = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
    warn = "".join(f"<div class='{'warn info' if n.startswith('Il prezzo può arrivare qui') else 'warn'}'>"
                   f"{'ℹ' if n.startswith('Il prezzo può arrivare qui') else '⚠'} {e(n)}</div>" for n in notes)
    return (f"<div class='scn {side}'><div class='scn-h'><span class='id'>{s['id']}</span><div class='tt'>"
            f"<div class='t'>{e(setup_title(s))}</div><div class='r'>{e(s['role'])}</div></div>"
            f"<span class='pill {side}'>{pill}</span></div><dl class='kv'>{body}</dl>{warn}</div>")


# ----------------------------------------------------------------------------- charts
def _sessions_for(df: pd.DataFrame, bounds_fn) -> list:
    from .engine import tday_of
    out = []
    for day in sorted(tday_of(df["time"]).unique()):
        b = bounds_fn(pd.Timestamp(day))
        out += [(b["open"], b["london"], "Asia", COL["blue"]), (b["london"], b["ny"], "Londra", COL["gold"]),
                (b["ny"], b["close"], "New York", COL["violet"])]
    return out


def chart_intraday(A: dict, st: dict | None = None) -> str:
    from .engine import session_bounds
    m15 = A["frames"]["m15"]
    b = A["bounds"]
    prev_open = session_bounds(pd.Timestamp(A["prev"]["tday"]))["open"]
    df = m15[m15["time"] >= prev_open].tail(230).reset_index(drop=True)
    p, atr = A["price"], A["ind"]["atr_d"]
    bands, hl, inc, marks = [], [], [p], []
    seen = set()
    shown = {s["id"] for s in A["scenarios"]}
    for r in (st["rows"] if st else []):        # setups already published today
        d = r["dir"]
        if r["entry_px"] is not None:
            marks.append({"time": r["entry_time"], "dir": d, "label": r["id"],
                          "tip": f"Ingresso scenario {r['id']} ({'short' if d < 0 else 'long'}) alle "
                                 f"{hm(rome(r['entry_time']))} a {fnum(r['entry_px'])}"})
        if r["state"] == "aperto":
            hl.append({"price": r["entry_px"], "label": f"{r['id']} aperta · ingresso {fnum(r['entry_px'])}", "color": COL["violet"],
                       "dash": "6 3", "w": 1.4, "col": 0, "from_future": True, "op": 1})
            hl.append({"price": r["stop"], "label": f"stop {r['id']} {fnum(r['stop'])}", "color": COL["orange"],
                       "dash": "2 3", "col": 0, "from_future": True, "op": 0.9})
            inc += [r["entry_px"], r["stop"]]
            if r["t1"] is not None:
                seen.add(round(r["t1"]))
                hl.append({"price": r["t1"], "label": f"T1·{r['id']} {fnum(r['t1'])}", "color": COL["blue"], "dash": "1 3",
                           "col": 0, "from_future": True, "op": 0.9})
                inc.append(r["t1"])
        elif r["state"] == "in attesa" and r["id"] not in shown:
            c = COL["down"] if d < 0 else COL["up"]
            if r["type"] == "fade":
                bands.append({"lo": r["zone_lo"], "hi": r["zone_hi"], "color": c,
                              "label": f"{r['id']} · zona di {'vendita' if d < 0 else 'acquisto'}"})
                inc += [r["zone_lo"], r["zone_hi"]]
            else:
                hl.append({"price": r["trigger"], "label": f"{r['id']} · rottura {'sotto' if d < 0 else 'sopra'} {fnum(r['trigger'])}",
                           "color": c, "dash": "6 3", "w": 1.4, "col": 0, "from_future": True, "op": 1})
                inc.append(r["trigger"])
            hl.append({"price": r["stop"], "label": f"stop {r['id']} {fnum(r['stop'])}", "color": COL["orange"],
                       "dash": "2 3", "col": 0, "from_future": True, "op": 0.9})
            inc.append(r["stop"])
    for s in A["scenarios"]:
        c = COL["down"] if s["dir"] < 0 else COL["up"]
        verb = {"fade": "vendita" if s["dir"] < 0 else "acquisto", "break": "rottura", "reclaim": "inversione"}[s["type"]]
        if s["type"] == "fade":
            bands.append({"lo": s["zone_lo"], "hi": s["zone_hi"], "color": c, "label": f"{s['id']} · zona di {verb}"})
        else:
            hl.append({"price": s["trigger"], "label": f"{s['id']} · {verb} sotto {fnum(s['trigger'])}" if s["dir"] < 0 else f"{s['id']} · {verb} sopra {fnum(s['trigger'])}", "color": c,
                       "dash": "6 3", "w": 1.4, "col": 0, "from_future": True, "op": 1})
        hl.append({"price": s["stop"], "label": f"stop {s['id']} {fnum(s['stop'])}", "color": COL["orange"],
                   "dash": "2 3", "col": 0, "from_future": True, "op": 0.9})
        inc += [s["stop"], s["zone_lo"], s["zone_hi"]]
        for i, t in enumerate(s["targets"][:2]):
            inc.append(t["price"])
    for s in A["scenarios"]:
        for i, t in enumerate(s["targets"][:2]):
            key = round(t["price"])
            if key in seen:
                continue
            seen.add(key)
            hl.append({"price": t["price"], "label": f"T{i + 1}·{s['id']} {fnum(t['price'])}", "color": COL["blue"], "dash": "1 3",
                       "col": 0, "from_future": True, "op": 0.9})
    pv, pr = A["pivots"], A["prev"]
    refs = [(pr["high"], "Max ieri"), (pr["low"], "Min ieri"), (pr["close"], "Chius. ieri"), (pv["P"], "Pivot P"),
            (pv["R1"], "R1"), (pv["S1"], "S1"), (pv["S2"], "S2"), (pv["R2"], "R2")]
    for price, name in refs:           # the chart keeps only those inside its price range
        hl.append({"price": price, "label": f"{name} {fnum(price)}", "color": "#9aa7b6", "dash": "5 4", "col": 1, "op": 0.55})
    k = math.ceil((p - 1.6 * atr) / 100.0) * 100
    while k <= p + 1.6 * atr:
        hl.append({"price": k, "label": f"{k:.0f}", "color": COL["gold"], "dash": "8 4", "col": 1, "op": 0.5})
        k += 100
    inc = [v for v in inc if abs(v - p) <= 1.35 * atr]
    return charts.candle_chart(df, kind="intraday", height=470, future=0.34, hlines=hl, bands=bands, markers=marks,
                               sessions=_sessions_for(df, session_bounds), price=p, y_include=inc, tick_hours=3, uid="m15")


def chart_h1(A: dict) -> str:
    h1 = A["frames"]["h1"]
    df = h1.tail(240).reset_index(drop=True)
    c = h1["close"]
    ov = [("EMA20", ta.ema(c, 20).tail(240).tolist(), COL["blue"]), ("EMA50", ta.ema(c, 50).tail(240).tolist(), COL["gold"]),
          ("EMA200", ta.ema(c, 200).tail(240).tolist(), COL["violet"])]
    lo, hi = df["low"].min(), df["high"].max()
    bands = [{"lo": z["lo"], "hi": max(z["hi"], z["lo"] + 0.8), "color": COL["down"] if z["side"] == "R" else COL["up"],
              "label": f"{zrange(z['lo'], z['hi'])}"} for z in A["zones"] if z["stars"] == 3 and lo <= z["mid"] <= hi and z["side"] != "X"]
    return charts.candle_chart(df, kind="intraday", height=390, future=0.13, overlays=ov, bands=bands, price=A["price"],
                               tick_hours=0, uid="h1", legend=[("EMA20", COL["blue"]), ("EMA50", COL["gold"]), ("EMA200", COL["violet"])])


def chart_d1(A: dict) -> str:
    d1 = A["frames"]["d1"]
    n = 110
    df = d1.tail(n).reset_index(drop=True)
    c = d1["close"]
    lo, hi = df["low"].min(), df["high"].max()
    ov = [("SMA20", ta.sma(c, 20).tail(n).tolist(), COL["blue"]), ("SMA50", ta.sma(c, 50).tail(n).tolist(), COL["gold"])]
    leg = [("SMA20", COL["blue"]), ("SMA50", COL["gold"])]
    s200 = ta.sma(c, 200).tail(n)
    if s200.notna().any() and lo * 0.99 <= s200.iloc[-1] <= hi * 1.01:
        ov.append(("SMA200", s200.tolist(), COL["violet"]))
        leg.append(("SMA200", COL["violet"]))
    return charts.candle_chart(df, kind="daily", height=330, future=0.04, overlays=ov, price=A["price"], uid="d1", legend=leg)


# ----------------------------------------------------------------------------- sections
def note_when(ctx: dict) -> str:
    """'alle 11:50' for a bare time, otherwise the free text as given (e.g. 'domenica 15/11 alle 18:45')."""
    import re
    t = str(ctx.get("note_time", "")).strip()
    return f"alle {t}" if re.fullmatch(r"\d{1,2}:\d{2}", t) else t


def since_text(A: dict) -> str:
    return "da ieri" if prev_label(A) == "Ieri" else f"da {GIORNI_S[pd.Timestamp(A['prev']['tday']).dayofweek]}"


def sec_kpis(A: dict) -> str:
    p, prev, ind, bias, td = A["price"], A["prev"], A["ind"], A["bias"], A["today"]
    chg = p - prev["close"]
    cls = "pos" if chg >= 0 else "neg"
    _, tcls = trend_word(bias["score"])
    rsi = ind["H1"]["rsi"]
    rsi_note = "ipervenduto" if rsi < 30 else ("ipercomprato" if rsi > 70 else "neutrale")
    since = since_text(A)
    first = ("<span id='pxlabel'>Prezzo spot</span>", f"<span id='px'>{fnum(p)}</span>",
             f"<span class='{cls}' id='pxchg'>{fnum(chg, 1, True)} $ ({pct(chg / prev['close'])})</span> <span id='pxsince'>{since}</span>")
    second = (prev_label(A), f"<span class='{'pos' if prev['chg'] >= 0 else 'neg'}'>{pct(prev['chg'])}</span>", f"chiusura {fnum(prev['close'])}")
    if not A["market_open"]:            # nothing has traded since the last close: show that session instead of a zero change
        pt = pd.Timestamp(prev["tday"])
        d_abs = prev["close"] - prev["close"] / (1 + prev["chg"])
        first = ("<span id='pxlabel'>Ultima chiusura</span>", f"<span id='px'>{fnum(p)}</span>",
                 f"<span class='{'pos' if prev['chg'] >= 0 else 'neg'}' id='pxchg'>{fnum(d_abs, 1, True)} $ ({pct(prev['chg'])})</span> "
                 f"<span id='pxsince'>{GIORNI_S[pt.dayofweek]} {pt.day:02d}/{pt.month:02d}</span>")
        ws = week_stats(A)
        if ws:
            second = ("Settimana", f"<span class='{'pos' if ws['chg'] >= 0 else 'neg'}'>{pct(ws['chg'])}</span>",
                      f"da {fnum(ws['ref'])} a {fnum(ws['close'])}")
    k = [
        first,
        second,
        ("Range di oggi", f"{fnum(td['range'], 0)} $" if td else "—", f"{fnum(100 * bias['flags']['adr_used'], 0)}% del range medio" if td else "seduta non iniziata"),
        ("Volatilità (ATR D1)", f"{fnum(ind['atr_d'], 0)} $", f"ATR orario {fnum(ind['atr_h1'], 1)} $"),
        ("Trend", f"<span class='t-{tcls}'>{e(bias['label'])}</span>", f"punteggio {fnum(bias['score'], 2, True)}"),
        ("RSI H1", f"{fnum(rsi, 0)}", rsi_note),
    ]
    return "<div class='kpis'>" + "".join(
        f"<div class='kpi'><div class='l'>{a}</div><div class='v'>{b}</div><div class='s'>{c}</div></div>" for a, b, c in k) + "</div>"


def sec_summary(A: dict, ev: dict, ctx: dict, st: dict | None = None, pv: dict | None = None) -> str:
    bl = "".join(f"<li>{x}</li>" for x in summary_bullets(A, ev, st, pv))
    tf = "".join(
        f"<div class='tfrow'><span>{k}</span><div class='bar'><i class='{'neg' if v < 0 else 'pos'}' style='"
        f"{'right' if v < 0 else 'left'}:50%;width:{abs(v) * 50:.0f}%'></i></div><b>{fnum(v, 2, True)}</b></div>"
        for k, v in A["bias"]["tf"].items())
    note = ""
    if ctx.get("analyst_note"):
        paras = "".join(f"<p>{e(x)}</p>" for x in ctx["analyst_note"])
        when = f" · scritta {e(note_when(ctx))}" if ctx.get("note_time") else ""
        basis = "delle notizie citate in «Contesto»" if ctx.get("drivers") else "delle notizie del giorno"
        paras += (f"<p class='fine'>Interpretazione redatta con l'ausilio di un assistente di intelligenza artificiale sulla base "
                  f"{basis}: non è un dato di mercato.</p>")
        note = f"<div class='note'><div class='note-h'>Lettura del giorno{when}</div>{paras}</div>"
    return (f"<section id='sintesi'><h2>In sintesi</h2><div class='grid-sum'><div class='card'><ul class='bul'>{bl}</ul>{note}</div>"
            f"<div class='card'><div class='mini-h'>Trend per timeframe</div>{charts.gauge(A['bias']['score'])}"
            f"<div class='tfs'>{tf}</div><p class='fine'>Il punteggio misura la posizione del prezzo rispetto alle medie mobili, "
            f"la loro pendenza, l'RSI e la struttura dei massimi/minimi. Descrive il contesto: <b>non è una previsione</b> "
            f"(vedi «Verifica»).</p></div></div></section>")


def status_origin(r: dict) -> str:
    return "piano ufficiale" if r["official"] else f"aggiornamento delle {hm(rome(r['pub']))} · fuori registro"


def status_rows(rows: list[dict], A: dict) -> str:
    out = ""
    for r in rows:
        lab, cls = STATE_PILL[r["state"]]
        side, pill = ("short", "SHORT") if r["dir"] < 0 else ("long", "LONG")
        out += (f"<div class='strow'><div class='st-a'><div class='st-n'><span class='sid'>{e(r['id'])}</span><b>{e(r['title'])}</b>"
                f"<span class='pill {side}'>{pill}</span></div><div class='evn'>{e(status_origin(r))} · {e(status_levels(r))}</div></div>"
                f"<div class='st-b'><span class='pill {cls}'>{lab}</span></div><div class='st-d'>{e(status_detail(r, A))}</div></div>")
    return out


def prev_bullet(prev: dict | None, A: dict) -> str | None:
    """One-line recap of how the scenarios of the previous session ended."""
    if not prev or not prev["rows"]:
        return None
    t = prev["tday"]
    when = "di ieri" if (A["tday"] - t).days == 1 else "della seduta precedente"
    done, idle = [], []
    for r in prev["rows"]:
        name = f"{e(r['id'])} · {e(r['title'])}" + ("" if r["official"] else " (aggiornamento, fuori registro)")
        if r["entry_px"] is None:
            idle.append(e(r["id"]))
        elif r["state"] == "stop":
            done.append(f"{name}: <b>stop</b> ({fnum(-1.0, 0, True)}R)")
        elif r["state"] == "target 1":
            done.append(f"{name}: <b>T1 raggiunto</b> ({fnum(r['R'], 1, True)}R)")
        else:
            done.append(f"{name}: chiusa a fine seduta ({fnum(r['R'], 1, True)}R)")
    if idle:
        names = idle[0] if len(idle) == 1 else ", ".join(idle[:-1]) + " e " + idle[-1]
        done.append(("scenario " if len(idle) == 1 else "scenari ") + names + (" non attivato" if len(idle) == 1 else " non attivati"))
    return f"<b>Come sono finiti gli scenari {when} ({d_short(t)})</b> — " + "; ".join(done) + "."


def status_block(A: dict, st: dict | None) -> str:
    """Table that follows the setups already published during this session (official plan and triggered refreshes)."""
    if not st:
        return ""
    t0, now = hm(rome(st["asof"])), hm(rome(A["asof"]))
    if st["same"]:
        return (f"<div class='inval' style='margin:0 0 12px'><b>Piano ufficiale delle {t0}: confermato.</b> Nessuno scenario si è "
                f"ancora attivato e il ricalcolo delle {now} restituisce gli stessi livelli.</div>")
    if not st["rows"]:
        return (f"<div class='inval' style='margin:0 0 12px'><b>Piano ufficiale delle {t0}:</b> non prevedeva scenari "
                f"(nessuna zona soddisfaceva i criteri). Qui sotto il ricalcolo delle {now}.</div>")
    rows = status_rows(st["rows"], A)
    return (f"<div class='card' style='margin-bottom:12px'><div class='mini-h'>Scenari già pubblicati oggi · situazione alle {now}</div>"
            f"<div class='strow sthead'><div>Scenario</div><div>Stato</div><div>Che cosa è successo</div></div>{rows}"
            f"<p class='fine'>Il <b>piano ufficiale</b> è quello delle {t0}: entra nel registro così com'è e gli aggiornamenti "
            f"successivi non lo modificano. Gli esiti seguono le regole meccaniche del registro: uscita a T1, allo stop o a fine "
            f"seduta (23:00), con lo stop mai spostato.</p>"
            f"<p class='fine'><b>E la gestione attiva?</b> {MGMT_NOTE}</p></div>")


def sec_plan(A: dict, st: dict | None = None) -> str:
    S = A["scenarios"]
    follow = bool(st and not st["same"])
    opened = [r["id"] for r in (st["rows"] if st else []) if r["state"] == "aperto"]
    if not S and opened:
        body = (f"<div class='card'>Nessun nuovo scenario soddisfa i criteri minimi oltre a quelli già pubblicati: resta da "
                f"gestire la posizione aperta ({', '.join(opened)}) secondo il suo piano.</div>")
    elif not S:
        body = ("<div class='card'>Nessun setup soddisfa i criteri minimi (zona con confluenza sufficiente e rapporto "
                "rischio/rendimento ≥ 1 verso il primo obiettivo). In queste condizioni la scelta coerente è <b>non operare</b> "
                "e attendere che il prezzo si avvicini a una zona della mappa.</div>")
    else:
        head = (f"<div class='mini-h' style='margin:16px 0 9px'>Scenari ricalcolati alle {hm(rome(A['asof']))} · per chi è senza posizione</div>"
                if follow else "")
        body = head + "<div class='scns'>" + "".join(setup_card(s, A) for s in S) + "</div>"
    body = status_block(A, st) + body
    inv = ""
    if A.get("invalidation"):
        d = A["bias"]["dir"]
        nxt = ""
        if A.get("beyond"):
            nxt = (f" Oltre, {'le resistenze successive' if d < 0 else 'i supporti successivi'} sono "
                   + " e ".join(f"{zrange(b['lo'], b['hi'])}" for b in A["beyond"]) + ".")
        inv = (f"<div class='inval'><b>Se il mercato va dall'altra parte.</b> Una chiusura H1 {'sopra' if d < 0 else 'sotto'} "
               f"<b>{fnum(A['invalidation'])}</b> invalida la lettura {'ribassista' if d < 0 else 'rialzista'} di breve: conviene "
               f"sospendere i setup nella direzione del trend e attendere un piano aggiornato, senza cercare l'ingresso contrario "
               f"(nei test storici, su campioni piccoli, gli ingressi contro-trend non hanno dato risultati migliori).{nxt}</div>")
    near = ""
    d = A["bias"]["dir"]
    if d != 0 and S:
        side = "R" if d < 0 else "S"
        zs = sorted([z for z in A["zones"] if z["side"] == side and z["stars"] >= 2], key=lambda z: abs(z["dist"]))
        used = [(s["zone_lo"], s["zone_hi"]) for s in S if s["type"] == "fade"]
        if zs and used and not any(abs(zs[0]["lo"] - u[0]) < 0.11 for u in used) and abs(zs[0]["dist"]) < abs(used[0][0] - A["price"]):
            z = zs[0]
            near = (f"<div class='inval'><b>Perché non la zona più vicina?</b> "
                    f"{'La prima resistenza' if d < 0 else 'Il primo supporto'} è {zrange(z['lo'], z['hi'])} ({e(z['label'])}), "
                    f"ma da lì {'il primo supporto' if d < 0 else 'la prima resistenza'} importante dista meno di 1R con uno stop "
                    f"oltre la zona: rapporto rischio/rendimento insufficiente per un ingresso pianificato.</div>")
    closed = ("" if A["market_open"] else
              f"<li><b>Mercato chiuso</b>: riapre {reopen_text(A)} (ora di Roma). Il piano vale per la prossima seduta; se "
              f"l'apertura avviene in gap oltre una zona o un livello d'innesco, lo scenario relativo è da considerare superato "
              f"e serve un briefing aggiornato a mercato aperto.</li>")
    rules = ("<ul class='rules'>" + closed + "<li>Gli scenari sono <b>piani condizionali</b>: si attivano solo se il prezzo raggiunge la zona "
             "o il livello d'innesco. Nessun ingresso «a mercato» lontano dalle zone.</li>"
             f"<li>Stop sempre oltre la zona, con un margine pari al 15% dell'ATR giornaliero (oggi {fnum(A['buffer'])} $, "
             "circa un ATR orario). R = distanza ingresso–stop.</li>"
             "<li>Niente nuovi ingressi nei 5 minuti prima/dopo i dati macro in agenda e dopo le 21:00 (ora di Roma); "
             "posizioni chiuse entro fine seduta (23:00).</li>"
             "<li>Il piano vale per l'orario indicato in alto: va aggiornato prima dell'apertura di Londra (09:00) e "
             "prima dei dati USA (14:30), perché livelli dinamici e range cambiano.</li></ul>")
    return (f"<section id='piano'><h2>Piano operativo <small>scenari condizionali per la seduta</small></h2>{body}{near}{inv}"
            f"<div class='card' style='margin-top:12px'><div class='mini-h'>Regole d'uso</div>{rules}</div></section>")


def sec_map(A: dict, st: dict | None = None) -> str:
    trig = any(r["entry_px"] is not None for r in (st["rows"] if st else []))
    extra = (" Freccia viola con lettera: ingresso di uno scenario già attivato; linea viola: prezzo d'ingresso della "
             "posizione ancora aperta.") if trig else ""
    return (f"<section id='mappa'><h2>Mappa intraday <small>M15 · ieri e oggi · ora di Roma</small></h2>"
            f"<div class='card chartbox toright'>{chart_intraday(A, st)}</div>"
            f"<p class='fine'>Fasce colorate di sfondo: sessioni Asia / Londra / New York. Bande rosse/verdi: zone d'ingresso degli "
            f"scenari. A destra dell'ultima candela, la proiezione di stop (arancione), obiettivi (azzurro) e riferimenti "
            f"(grigio: livelli di ieri e pivot; oro: soglie tonde e prezzo corrente).{extra}</p></section>")


def sec_ladder(A: dict) -> str:
    p = A["price"]
    tags: dict[int, list[str]] = {}

    def nearest_zone(price_):
        return min(range(len(A["zones"])), key=lambda i: 0 if A["zones"][i]["lo"] - 0.06 <= price_ <= A["zones"][i]["hi"] + 0.06
                   else min(abs(A["zones"][i]["lo"] - price_), abs(A["zones"][i]["hi"] - price_)))

    for s in A["scenarios"]:
        ref = (s["zone_lo"] + s["zone_hi"]) / 2 if s["type"] == "fade" else s["trigger"]
        tags.setdefault(nearest_zone(ref), []).append(("ingresso " if s["type"] == "fade" else "innesco ") + s["id"])
        if s["targets"] and not s["targets"][0]["proj"]:
            tags.setdefault(nearest_zone(s["targets"][0]["price"]), []).append(f"T1 {s['id']}")
    rows = []
    placed = False
    for zi, z in enumerate(A["zones"]):
        if not placed and z["mid"] < p:
            rows.append(f"<tr class='pxrow' id='pxrow'><td colspan='6'><span class='dot'></span> Prezzo corrente "
                        f"<b id='px2'>{fnum(p)}</b></td></tr>")
            placed = True
        kind = {"R": "<span class='pill short'>RES</span>", "S": "<span class='pill long'>SUP</span>",
                "X": "<span class='pill neu'>QUI</span>"}[z["side"]]
        names = [n for n in z["names"]]
        txt = " · ".join(e(n) for n in names[:4]) + (f" <span class='mut'>+{len(names) - 4}</span>" if len(names) > 4 else "")
        tg = "".join(f"<span class='ztag'>{e(t)}</span>" for t in tags.get(zi, []))
        prob = z.get("prob")
        if prob is None or (isinstance(prob, float) and math.isnan(prob)):
            pr = "<td class='num mut'>—</td>"
        else:
            pr = (f"<td class='num'><div class='pb'><i style='width:{100 * prob:.0f}%'></i></div>"
                  f"<span class='pv'>{fnum(100 * prob, 0)}%</span></td>")
        dist = z["lo"] - p if z["side"] == "R" else (z["hi"] - p if z["side"] == "S" else 0.0)
        rows.append(
            f"<tr class='z{z['side']} s{z['stars']}' data-lo='{z['lo']:.2f}' data-hi='{z['hi']:.2f}' title='{e(' | '.join(names))}'>"
            f"<td class='lv'><b>{zrange(z['lo'], z['hi'])}</b>{tg}</td><td>{kind}</td><td class='cf'>{txt}</td>"
            f"<td>{stars(z['stars'])}</td><td class='num dist'>{fnum(dist, 1, True)} $ <span class='mut'>({pct(dist / p)})</span></td>{pr}</tr>")
    if not placed:
        rows.append(f"<tr class='pxrow' id='pxrow'><td colspan='6'><span class='dot'></span> Prezzo corrente <b id='px2'>{fnum(p)}</b></td></tr>")
    ex = A.get("excursion") or {}
    touch = "Tocco oggi" if A["market_open"] else "Tocco in seduta"
    exn = ""
    if ex.get("n"):
        frm = "da quest'ora della seduta" if A["market_open"] else "dall'apertura della seduta"
        frm2 = "Da quest'ora" if A["market_open"] else "Dall'apertura"
        exn = (f"<p class='fine'><b>Come leggere «{touch}»</b>: quota delle ultime {ex['n']} sedute in cui, {frm} "
               f"fino alla chiusura, il prezzo ha percorso almeno quella distanza (normalizzata per l'ATR). È una misura di "
               f"<i>raggiungibilità</i> basata sulla sola volatilità, senza ipotesi di direzione; su dati fuori campione le stime "
               f"sono risultate ben calibrate. {frm2} a fine seduta l'escursione tipica è stata di "
               f"<b>+{fnum(ex['up_med'], 0)} $</b> / <b>−{fnum(ex['dn_med'], 0)} $</b> (mediane); una seduta su cinque è andata oltre "
               f"+{fnum(ex['up_p80'], 0)} $ / −{fnum(ex['dn_p80'], 0)} $.</p>")
    return (f"<section id='livelli'><h2>Livelli e zone di confluenza <small>entro ±1,6 ATR dal prezzo</small></h2>"
            f"<div class='card tablebox'><table class='ladder'><thead><tr><th>Zona</th><th>Tipo</th><th>Cosa c'è</th><th>Forza</th>"
            f"<th class='num'>Distanza</th><th class='num'>{touch}</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
            f"{exn}</section>")


def sec_agenda(A: dict, ev: dict) -> str:
    from .engine import session_bounds
    b = A["bounds"]
    asof_r = rome(A["asof"])
    # hour -> session mapping for the bar colours (Rome clock, DST-safe)
    sess = {}
    for h in range(24):
        t = pd.Timestamp(year=A["tday"].year, month=A["tday"].month, day=A["tday"].day, hour=h, tz=ROME).tz_convert("UTC")
        if b["open"] <= t < b["london"]:
            sess[h] = "asia"
        elif b["london"] <= t < b["ny"]:
            sess[h] = "london"
        elif b["ny"] <= t < b["close"]:
            sess[h] = "ny"
        elif t >= b["close"] + H:
            sess[h] = "asia"          # next session already open
        else:
            sess[h] = "closed"
    evs = []
    for x in ev["today"]:
        if x["rel"] >= 2 or (x["rel"] == 1 and x["raw"] in ("30-y Bond Auction", "10-y Bond Auction")):
            short = x["title"]
            short = {"Richieste settimanali di sussidi di disoccupazione": "Sussidi USA"}.get(short, short)
            if len(short) > 26:
                short = short[:25] + "…"
            evs.append({"hour_f": x["t"].hour + x["t"].minute / 60, "rel": x["rel"], "label": f"{hm(x['t'])} {short}" if x["rel"] >= 2 else None})
    cur = asof_r.hour if asof_r.date() == A["tday"].date() and A["market_open"] else None
    chart = charts.hourly_chart(A["stats"]["hourly"], cur_hour=cur, sess_of_hour=sess, events=evs)
    # calendar table
    if not ev["available"]:
        cal = "<p class='mut'>Calendario macro non disponibile in questo momento: verificare manualmente gli eventi USA della giornata.</p>"
    else:
        def rows(items, with_day=False):
            out = []
            for x in items:
                cls = "past" if x["past"] else ""
                when = (d_short(x["t"]) + " " if with_day else "") + hm(x["t"])
                note = f"<div class='evn'>{e(x['note'])}</div>" if x["note"] else ""
                out.append(f"<tr class='{cls}'><td class='tm'>{when}</td><td>{e(x['cur'])}</td><td>{e(x['title'])}{note}</td>"
                           f"<td>{rel_dots(x['rel'])}</td><td class='num'>{e(ev_num(x['f'])) or '—'}</td><td class='num'>{e(ev_num(x['p'])) or '—'}</td></tr>")
            return "".join(out)
        t1 = rows(ev["today"]) or "<tr><td colspan='6' class='mut'>Nessun evento rilevante per l'oro in calendario.</td></tr>"
        cal = (f"<table class='cal'><thead><tr><th>Ora</th><th>Val.</th><th>Evento</th><th>Rilevanza oro</th><th class='num'>Atteso</th>"
               f"<th class='num'>Preced.</th></tr></thead><tbody>{t1}</tbody></table>")
        if ev["tomorrow"]:
            lab = "Domani" if A["market_open"] and (ev["next_day"] - A["tday"]).days == 1 else "Seduta successiva"
            cal += (f"<div class='mini-h' style='margin-top:14px'>{lab} in evidenza ({d_long(ev['next_day'])})</div>"
                    f"<table class='cal'><tbody>{rows(ev['tomorrow'])}</tbody></table>")
        if ev["week"]:
            cal += (f"<div class='mini-h' style='margin-top:14px'>Più avanti in settimana</div>"
                    f"<table class='cal'><tbody>{rows(ev['week'], with_day=True)}</tbody></table>")
    lo, ny, cl, op = rome(b["london"]), rome(b["ny"]), rome(b["close"]), rome(b["open"])
    ss = A["stats"]["sessions"]
    srow = lambda nm, a, z, k: (f"<tr><td><span class='sq {k}'></span>{nm}</td><td class='num'>{hm(a)} – {hm(z)}</td>"
                                f"<td class='num'>{fnum(ss['avg_range'][k], 0)} $</td><td class='num'>{fnum(100 * ss['high_in'][k], 0)}%</td>"
                                f"<td class='num'>{fnum(100 * ss['low_in'][k], 0)}%</td></tr>")
    stab = (f"<table><thead><tr><th>Sessione</th><th class='num'>Orario (Roma)</th><th class='num'>Range medio</th>"
            f"<th class='num'>Fa il massimo</th><th class='num'>Fa il minimo</th></tr></thead><tbody>"
            f"{srow('Asia', op, lo, 'asia')}{srow('Londra', lo, ny, 'london')}{srow('New York', ny, cl, 'ny')}</tbody></table>"
            f"<p class='fine'>Medie delle ultime {ss['n']} sedute. «Fa il massimo/minimo»: quota di sedute in cui l'estremo "
            f"giornaliero è stato segnato in quella sessione.</p>")
    # operating windows
    day = A["tday"]
    mk = lambda hh, mm, tz: rome(pd.Timestamp(year=day.year, month=day.month, day=day.day, hour=hh, minute=mm, tz=tz))
    top = sorted(A["stats"]["hourly"], key=lambda x: -x["mean"])[:4]
    top_txt = ", ".join(f"{x['hour']:02d}–{(x['hour'] + 1) % 24:02d} ({fnum(x['mean'], 0)} $)" for x in sorted(top, key=lambda x: x["hour"]))
    wins = [("Ore più mosse", f"{top_txt} — range medio orario delle ultime 20 sedute."),
            (f"{hm(lo)} · {hm(mk(9, 30, 'America/New_York'))}", "Apertura di Londra e apertura di Wall Street: i due momenti in cui più spesso parte il movimento della giornata."),
            (f"{hm(mk(10, 30, 'Europe/London'))} · {hm(mk(15, 0, 'Europe/London'))}", "Fixing LBMA dell'oro (mattina e pomeriggio): liquidità istituzionale, possibili strappi brevi.")]
    data_ev = [x for x in ev["today"] if x["rel"] >= 2 and not x["speech"] and not x["past"]]
    if data_ev:
        wins.append(("Pausa sui dati", "; ".join(f"{hm(x['t'] - pd.Timedelta(minutes=5))}–{hm(x['t'] + pd.Timedelta(minutes=5))} ({e(x['title'])})"
                                                 for x in data_ev[:3]) + ": niente nuovi ingressi, spread più larghi."))
    wins.append((f"{hm(cl)}", "Chiusura della seduta (17:00 New York) e pausa di un'ora: posizioni intraday chiuse prima."))
    wl = "".join(f"<li><b>{a}</b><span>{b_}</span></li>" for a, b_ in wins)
    cal_h = "Calendario macro di oggi" if A["market_open"] else f"Calendario macro di {d_long(A['tday'])}"
    return (f"<section id='agenda'><h2>Agenda e orari <small>ora di Roma</small></h2>"
            f"<div class='grid2'><div class='card tablebox'><div class='mini-h'>{cal_h}</div>{cal}</div>"
            f"<div class='card tablebox'><div class='mini-h'>Sessioni</div>{stab}"
            f"<div class='mini-h' style='margin-top:16px'>Finestre operative</div><ul class='win'>{wl}</ul></div></div>"
            f"<div class='card chartbox' style='margin-top:12px'><div class='mini-h'>Quando si muove l'oro: range medio per ora "
            f"(ultime 20 sedute, $) con gli eventi di oggi</div>{chart}</div></section>")


def sec_technical(A: dict, inter: list[dict], yields: dict) -> str:
    ind, bias, p = A["ind"], A["bias"], A["price"]

    def rel(v, name):
        return f"<span class='{'pos' if p >= v else 'neg'}'>{'sopra' if p >= v else 'sotto'}</span> {name} {fnum(v)}"

    def rsi_cell(r):
        c = "neg" if r < 30 else ("pos" if r > 70 else "")
        lab = " ipervenduto" if r < 30 else (" ipercomprato" if r > 70 else "")
        return f"<b class='{c}'>{fnum(r, 0)}</b><span class='mut'>{lab}</span>"

    rows = [
        ("D1", bias["tf"]["D1"], " · ".join([rel(ind["D1"]["sma20"], "SMA20"), rel(ind["D1"]["sma50"], "SMA50"), rel(ind["D1"]["sma200"], "SMA200")]),
         ind["D1"]["rsi"], f"ADX {fnum(ind['D1']['adx'], 0)} · ATR {fnum(ind['atr_d'], 0)} $"),
        ("H4", bias["tf"]["H4"], " · ".join([rel(ind["H4"]["ema20"], "EMA20"), rel(ind["H4"]["ema50"], "EMA50")]), ind["H4"]["rsi"], ""),
        ("H1", bias["tf"]["H1"], " · ".join([rel(ind["H1"]["ema20"], "EMA20"), rel(ind["H1"]["ema50"], "EMA50"), rel(ind["H1"]["ema200"], "EMA200")]),
         ind["H1"]["rsi"], f"ADX {fnum(ind['H1']['adx'], 0)} ({bias['flags']['trend_regime']}) · {e(bias['structure']['text'])}"),
        ("M15", bias["tf"]["M15"], " · ".join([rel(ind["M15"]["ema20"], "EMA20"), rel(ind["M15"]["ema50"], "EMA50")]), ind["M15"]["rsi"],
         f"ATR {fnum(ind['atr_m15'], 1)} $"),
    ]
    trs = ""
    for tf, v, txt, r, note in rows:
        w, cls = trend_word(v)
        trs += (f"<tr><td><b>{tf}</b></td><td><span class='pill {cls}'>{w}</span></td><td>{txt}</td>"
                f"<td class='num'>{rsi_cell(r)}</td><td class='mut'>{note}</td></tr>")
    mtf = (f"<table><thead><tr><th>TF</th><th>Trend</th><th>Prezzo rispetto alle medie</th><th class='num'>RSI(14)</th><th>Note</th></tr>"
           f"</thead><tbody>{trs}</tbody></table>")
    im = ""
    if inter:
        r_ = ""
        for x in inter:
            cc = "" if x["corr"] is None else ("neg" if x["corr"] < -0.3 else ("pos" if x["corr"] > 0.3 else ""))
            cv = "—" if x["corr"] is None else fnum(x["corr"], 2, True)
            cell = lambda v: "<td class='num mut'>—</td>" if v is None else f"<td class='num {'pos' if v >= 0 else 'neg'}'>{pct(v)}</td>"
            r_ += (f"<tr><td>{e(x['name'])}</td><td class='num'>{fnum(x['last'], x['dec'])}</td>{cell(x['chg_today'])}"
                   f"{cell(x['chg_prev'])}{cell(x['chg_5d'])}<td class='num {cc}'>{cv}</td></tr>")
        ytxt = ""
        if yields:
            parts = []
            if yields.get("US10Y"):
                parts.append(f"Treasury 10 anni <b>{fnum(yields['US10Y'], 2)}%</b>")
            if yields.get("US02Y"):
                parts.append(f"2 anni <b>{fnum(yields['US02Y'], 2)}%</b>")
            if yields.get("DXY"):
                parts.append(f"indice DXY <b>{fnum(yields['DXY'], 2)}</b>")
            if parts:
                ytxt = f"<p class='fine'>Rendimenti e dollaro (ultimo dato TradingView): {' · '.join(parts)}.</p>"
        im = (f"<div class='card tablebox' style='margin-top:12px'><div class='mini-h'>Mercati collegati</div><table><thead><tr>"
              f"<th>Strumento</th><th class='num'>Ultimo</th><th class='num'>Oggi</th><th class='num'>Ieri</th><th class='num'>5 sedute</th>"
              f"<th class='num'>Correl. con l'oro (20 gg, H1)</th></tr></thead><tbody>{r_}</tbody></table>{ytxt}"
              f"<p class='fine'>Variazioni calcolate sulla giornata di trading con chiusura alle 17:00 di New York. Correlazione "
              f"negativa col dollaro = l'oro sta rispondendo al biglietto verde.</p></div>")
    return (f"<section id='tecnica'><h2>Quadro tecnico <small>multi-timeframe</small></h2>"
            f"<div class='card tablebox'>{mtf}</div>"
            f"<div class='card chartbox toright' style='margin-top:12px'><div class='mini-h'>H1 · ultime 10 sedute · medie mobili e zone ★★★ "
            f"(rosso = resistenza, verde = supporto)</div>{chart_h1(A)}</div>"
            f"<div class='card chartbox toright' style='margin-top:12px'><div class='mini-h'>Giornaliero · ultimi 5 mesi</div>"
            f"{chart_d1(A)}</div>{im}</section>")


def sec_context(ctx: dict) -> str:
    if not ctx.get("drivers"):
        return ""
    li = ""
    for d in ctx["drivers"]:
        src = ""
        if d.get("source"):
            src = (f" <a class='src' href='{e(d.get('url', '#'))}' target='_blank' rel='noopener'>{e(d['source'])}</a>"
                   if d.get("url") else f" <span class='src'>{e(d['source'])}</span>")
        li += f"<li>{e(d['text'])}{src}</li>"
    return (f"<section id='contesto'><h2>Contesto macro e notizie <small>cosa sta muovendo l'oro</small></h2>"
            f"<div class='card'><ul class='bul'>{li}</ul></div></section>")


def sec_risk(A: dict, eurusd: float | None) -> str:
    S = A["scenarios"]
    default_stop = S[0]["risk"] if S else round(A["ind"]["atr_h1"] * 1.5, 1)
    btn = "".join(f"<button type='button' class='chip' data-stop='{s['risk']}'>Scenario {s['id']} · {fnum(s['risk'])} $</button>" for s in S)
    trs = ""
    for s in S:
        trs += (f"<tr><td><b>{s['id']}</b> · {e(setup_title(s))}</td><td class='num'>{fnum(s['risk'])} $</td>"
                f"<td class='num'>{fnum(s['risk'], 1)} $</td><td class='num'>{fnum(s['risk'] * 10, 0)} $</td>"
                f"<td class='num'>{fnum(s['risk'] * 100, 0)} $</td></tr>")
    tab = (f"<table><thead><tr><th>Scenario</th><th class='num'>Stop (distanza)</th><th class='num'>0,01 lotti</th>"
           f"<th class='num'>0,10 lotti</th><th class='num'>1 lotto</th></tr></thead><tbody>{trs}</tbody></table>" if S else "")
    fx = eurusd or 1.10
    return (f"<section id='rischio'><h2>Rischio e dimensionamento</h2><div class='grid2'>"
            f"<div class='card'><div class='mini-h'>Calcolatore della size</div><div class='calc'>"
            f"<label>Capitale<input id='c-cap' type='number' value='10000' min='0' step='500'></label>"
            f"<label>Valuta del conto<select id='c-cur'><option value='EUR'>EUR</option><option value='USD'>USD</option></select></label>"
            f"<label>Rischio per operazione (%)<input id='c-risk' type='number' value='1' min='0.1' max='10' step='0.1'></label>"
            f"<label>Distanza dello stop ($/oncia)<input id='c-stop' type='number' value='{default_stop}' min='0.5' step='0.5'></label>"
            f"<label>Once per lotto<input id='c-oz' type='number' value='100' min='1' step='1'></label>"
            f"<label>Cambio EUR/USD<input id='c-fx' type='number' value='{fx:.4f}' min='0.5' step='0.0001'></label></div>"
            f"<div class='chips'>{btn}</div><div class='calc-out' id='c-out'></div>"
            f"<p class='fine'>Size = (capitale × rischio %) ÷ (distanza stop × once per lotto). Verificare sul proprio broker "
            f"dimensione del contratto, lotto minimo e margine richiesto.</p></div>"
            f"<div class='card tablebox'><div class='mini-h'>Perdita se scatta lo stop (1 lotto = 100 once)</div>{tab}"
            f"<ul class='rules'><li>Rischio consigliato per singola operazione: non oltre l'1% del capitale; perdita massima "
            f"giornaliera 2–3%, poi stop alle operazioni.</li><li>Gli scenari nella stessa direzione sono correlati: se due "
            f"sono aperti insieme il rischio va diviso, non sommato.</li><li>Con un ATR giornaliero di {fnum(A['ind']['atr_d'], 0)} $ "
            f"lo spread e lo slippage sui dati macro possono valere 1–3 $: vanno messi in conto nel rapporto rischio/rendimento.</li>"
            f"</ul></div></div></section>")


def sec_verify(A: dict, replay: dict | None, journal: dict | None, pv: dict | None = None) -> str:
    st = A["stats"]
    pvh = ""
    if pv and pv.get("rows"):
        pvh = (f"<div class='card' style='margin-top:12px'><div class='mini-h'>Scenari pubblicati nella seduta precedente "
               f"({d_short(pv['tday'])}) · esito finale</div>"
               f"<div class='strow sthead'><div>Scenario</div><div>Esito</div><div>Che cosa è successo</div></div>"
               f"{status_rows(pv['rows'], A)}"
               f"<p class='fine'>Esiti meccanici: uscita a T1, allo stop o a fine seduta. Nel registro entrano solo gli scenari del "
               f"piano ufficiale; quelli pubblicati negli aggiornamenti sono riportati per completezza e, quando vanno nella "
               f"stessa direzione, non sono risultati indipendenti.</p></div>")
    atr = A["ind"]["atr_d"]
    rec = ""
    for r in st["recent"]:
        t = pd.Timestamp(r["tday"])
        chg = r["chg"]
        rec += (f"<tr><td>{d_short(t)}</td><td class='num'>{fnum(r['open'])}</td><td class='num'>{fnum(r['high'])}</td>"
                f"<td class='num'>{fnum(r['low'])}</td><td class='num'><b>{fnum(r['close'])}</b></td>"
                f"<td class='num {'pos' if chg >= 0 else 'neg'}'>{pct(chg)}</td><td class='num'>{fnum(r['range'], 0)} $</td></tr>")
    rect = (f"<table><thead><tr><th>Seduta</th><th class='num'>Apertura</th><th class='num'>Max</th><th class='num'>Min</th>"
            f"<th class='num'>Chiusura</th><th class='num'>Var.</th><th class='num'>Range</th></tr></thead><tbody>{rec}</tbody></table>")
    pv = st["pivot"]
    bars = "".join(
        f"<div class='hb'><span>{k}</span><div class='pb'><i style='width:{100 * pv['rate'][k]:.0f}%'></i></div>"
        f"<b>{fnum(100 * pv['rate'][k], 0)}%</b></div>" for k in ("R3", "R2", "R1", "P", "S1", "S2", "S3"))
    piv = (f"<div class='hbs'>{bars}</div><p class='fine'>Quota delle ultime {pv['n']} sedute in cui il prezzo ha toccato ciascun "
           f"livello pivot classico calcolato sulla seduta precedente.</p>")
    rep = ""
    if replay and replay.get("rows"):
        trs = ""
        for r in replay["rows"]:
            trs += (f"<tr><td>{e(r['label'])}</td><td class='num'>{r['setups']}</td><td class='num'>{r['trades']}</td>"
                    f"<td class='num'>{fnum(r['win'], 0)}%</td><td class='num'>{fnum(r['stop'], 0)}%</td>"
                    f"<td class='num {'pos' if r['avg'] >= 0 else 'neg'}'>{fnum(r['avg'], 2, True)}R</td></tr>")
        t = replay["total"]
        trs += (f"<tr class='tot'><td>Totale</td><td class='num'>{t['setups']}</td><td class='num'>{t['trades']}</td>"
                f"<td class='num'>{fnum(t['win'], 0)}%</td><td class='num'>{fnum(t['stop'], 0)}%</td>"
                f"<td class='num {'pos' if t['avg'] >= 0 else 'neg'}'>{fnum(t['avg'], 2, True)}R <span class='mut'>(±{fnum(t['se'], 2)})</span></td></tr>")
        lean = "lievemente negativo" if t["avg"] < -0.02 else ("lievemente positivo" if t["avg"] > 0.02 else "nullo")
        verdict = (f"{lean} e statisticamente indistinguibile da zero" if abs(t["avg"]) <= 2 * t["se"] else
                   ("positivo nel campione" if t["avg"] > 0 else "negativo nel campione"))
        rep = (f"<div class='card tablebox' style='margin-top:12px'><div class='mini-h'>Verifica: le regole di questo piano applicate "
               f"alle ultime {replay['days']} sedute</div>"
               f"<table><thead><tr><th>Tipo di setup</th><th class='num'>Proposti</th><th class='num'>Attivati</th><th class='num'>Chiusi in utile</th>"
               f"<th class='num'>In stop</th><th class='num'>Risultato medio</th></tr></thead><tbody>{trs}</tbody></table>"
               f"<div class='honest'><b>Cosa dice questa verifica.</b> Replicando meccanicamente le stesse regole su "
               f"{replay['days']} sedute ({e(replay['from'])} – {e(replay['to'])}; piano generato alle 08:00, uscita al primo obiettivo, "
               f"allo stop o a fine seduta) il risultato medio è {fnum(t['avg'], 2, True)}R per operazione: {verdict}, e prima di spread "
               f"e slippage. Inoltre il trend delle 08:00 ha anticipato la direzione del resto della seduta solo nel "
               f"<b>{fnum(replay['bias_hit'], 0)}%</b> dei {replay['bias_n']} giorni con trend definito. "
               f"<b>Conclusione onesta:</b> zone e scenari sono una mappa per operare a rischio definito, non un sistema con un "
               f"vantaggio statistico dimostrato. Il valore sta nel sapere <i>dove</i> il rischio è misurabile e <i>quando</i> "
               f"il mercato si muove; la selezione delle operazioni e la disciplina restano al trader.</div></div>")
    jr = ""
    if journal is not None:
        if journal.get("rows"):
            trs = ""
            for r in journal["rows"][-12:]:
                cls = "pos" if r["R"] > 0 else ("neg" if r["R"] < 0 else "mut")
                trs += (f"<tr><td>{d_short(pd.Timestamp(r['day']))}</td><td>{e(r['id'])} · {e(r['title'])}</td><td>{e(r['status'])}</td>"
                        f"<td class='num {cls}'>{fnum(r['R'], 2, True)}R</td></tr>")
            n = journal["n_trades"]
            count = "1 operazione attivata" if n == 1 else f"{n} operazioni attivate"
            small = ("<p class='fine'>Con così poche operazioni il totale non dice nulla sulla bontà delle regole: fa fede la "
                     "replica su 250 sedute qui sopra.</p>") if n < 30 else ""
            jr = (f"<div class='card tablebox' style='margin-top:12px'><div class='mini-h'>Registro dei piani pubblicati "
                  f"(esito meccanico) · {count}, totale {fnum(journal['tot_R'], 1, True)}R</div>"
                  f"<table><thead><tr><th>Seduta</th><th>Setup</th><th>Esito</th><th class='num'>R</th></tr></thead><tbody>{trs}</tbody></table>"
                  f"{small}</div>")
        else:
            jr = ("<div class='card' style='margin-top:12px'><div class='mini-h'>Registro dei piani pubblicati</div>"
                  "<p class='mut' style='margin:4px 0 0'>Il registro parte dal piano del 12/11/2026. A ogni aggiornamento, il piano ufficiale "
                  "delle sedute concluse (l'ultimo generato prima dell'apertura di Londra) viene verificato sui prezzi reali e "
                  "l'esito viene aggiunto qui, senza possibilità di correzioni a posteriori.</p></div>")
    return (f"<section id='verifica'><h2>Andamento recente e verifica</h2><div class='grid2'>"
            f"<div class='card tablebox'><div class='mini-h'>Ultime sedute (chiusura 17:00 New York)</div>{rect}"
            f"<p class='fine'>ATR(14) giornaliero: {fnum(atr, 0)} $ · range medio 5 sedute: {fnum(A['ind']['adr5'], 0)} $ · "
            f"20 sedute: {fnum(A['ind']['adr20'], 0)} $.</p></div>"
            f"<div class='card'><div class='mini-h'>Quanto spesso vengono toccati i pivot</div>{piv}</div></div>{pvh}{rep}{jr}</section>")


def sec_method(A: dict, quality: dict) -> str:
    q = ""
    if quality.get("rows"):
        trs = "".join(f"<tr><td>{e(r['what'])}</td><td class='num'>{r['ours']}</td><td class='num'>{r['theirs']}</td>"
                      f"<td class='num'>{r['diff']}</td></tr>" for r in quality["rows"])
        q = (f"<div class='mini-h' style='margin-top:14px'>Controllo incrociato dei dati</div><div class='tablebox'><table><thead><tr><th>Grandezza</th>"
             f"<th class='num'>Questo report</th><th class='num'>Fonte indipendente</th><th class='num'>Scarto</th></tr></thead>"
             f"<tbody>{trs}</tbody></table></div><p class='fine'>{e(quality.get('note', ''))}</p>")
    return (f"<section id='metodo'><h2>Metodo, fonti e avvertenze</h2><div class='card'>"
            f"<ul class='rules'>"
            f"<li><b>Dati</b>: candele spot XAU/USD (lato bid) dal feed pubblico Dukascopy; 12.000 barre H1, 5 minuti e 1 minuto per "
            f"le barre in corso. Giornata di trading con chiusura alle 17:00 di New York (convenzione delle piattaforme FX/CFD).</li>"
            f"<li><b>Altre fonti</b>: calendario macro dal feed settimanale di ForexFactory; rendimenti dei Treasury e indice del "
            f"dollaro da TradingView; mercati collegati da Dukascopy; prezzo in tempo reale mostrato in alto da gold-api.com "
            f"(indicativo: può scostarsi di qualche dollaro dal feed delle candele). Le notizie riportano la fonte accanto al testo.</li>"
            f"<li><b>Livelli</b>: massimo/minimo/chiusura di ieri, pivot classici, estremi settimanali e mensili, soglie tonde, "
            f"massimi/minimi di swing su D1, H4 e H1, medie mobili, estremi di sessione, proiezioni ADR. I livelli vicini "
            f"(entro {fnum(min(max(0.07 * A['ind']['atr_d'], 3), 9), 1)} $) sono fusi in zone; la forza (★) cresce col numero e col peso "
            f"delle confluenze stabili (livelli di prezzo e medie giornaliere).</li>"
            f"<li><b>Trend</b>: punteggio da −1 a +1 su D1 (25%), H4 (30%), H1 (30%), M15 (15%).</li>"
            f"<li><b>Scenari</b>: ingresso solo su zone con confluenza ★★ o superiore, ad almeno 0,12 ATR dal prezzo; stop a 0,15 ATR "
            f"giornalieri oltre la zona; primo obiettivo sulla prima zona significativa ad almeno ~1R; setup scartati se una zona "
            f"forte si trova prima di 1R. I livelli che si muovono durante la seduta (medie orarie, estremi di oggi, proiezioni ADR) "
            f"compaiono sulla mappa ma non entrano nelle regole, così il piano non cambia da un aggiornamento all'altro senza un "
            f"motivo di prezzo.</li>"
            f"<li><b>Scenari pubblicati</b>: il piano ufficiale della seduta è l'ultimo generato prima dell'apertura di Londra "
            f"(09:00), congelato in anticipo se uno dei suoi scenari si attiva; è l'unico che entra nel registro. Gli "
            f"aggiornamenti successivi aggiungono scenari con nuove lettere e li seguono fino all'esito; uno scenario ancora in "
            f"attesa viene ritirato se un aggiornamento successivo non lo ripropone. Una rottura la cui chiusura di conferma "
            f"arriva già oltre il primo obiettivo viene annullata.</li>"
            f"<li><b>Frequenze di tocco</b>: distribuzione empirica delle escursioni da quest'ora a fine seduta sulle ultime 250 "
            f"sedute, normalizzata per l'ATR.</li>"
            f"<li><b>Limiti</b>: i prezzi di broker diversi differiscono di alcune decine di centesimi; i livelli dinamici (medie, "
            f"estremi di oggi) cambiano nel corso della seduta; le notizie possono invalidare qualsiasi livello tecnico.</li></ul>{q}"
            f"<div class='disc'><b>Avvertenza.</b> Questo documento è un'analisi informativa generata automaticamente, non una "
            f"consulenza finanziaria né una raccomandazione personalizzata. Il trading su oro con leva (CFD, futures) comporta un "
            f"rischio elevato di perdita rapida del capitale. Ogni decisione operativa e la relativa responsabilità restano a chi opera.</div>"
            f"</div></section>")


# ----------------------------------------------------------------------------- page
CSS = r"""
:root{--bg:#0a0e13;--panel:#111821;--panel2:#18212c;--line:#222c39;--line2:#1a232e;--text:#e6edf3;--muted:#8b98a8;
--gold:#e3b341;--up:#2ebd85;--down:#f6465d;--blue:#58a6ff;--violet:#b392f0;--orange:#f0883e}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
-webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums}
.wrap{max-width:1240px;margin:0 auto;padding:22px 22px 70px}
a{color:var(--blue)}
.top{display:flex;justify-content:space-between;align-items:flex-end;gap:14px;flex-wrap:wrap}
h1{font-size:23px;line-height:1.2;margin:0;font-weight:750;letter-spacing:.1px}
h1 .sym{color:var(--gold)}
.sub{color:var(--muted);font-size:13px;margin-top:3px}
.stamp{text-align:right;font-size:12.5px;color:var(--muted)}
.stamp b{color:var(--text);font-weight:600}
.badge{display:inline-block;border:1px solid var(--line);background:var(--panel);padding:2px 9px;border-radius:999px;font-size:11.5px;color:var(--text);margin-left:6px}
.badge.live{border-color:rgba(46,189,133,.5);color:var(--up)}
.banner{display:none;margin-top:14px;padding:10px 14px;border-radius:10px;background:rgba(240,136,62,.12);border:1px solid rgba(240,136,62,.45);color:#ffd2b0;font-size:13px}
nav{position:sticky;top:0;z-index:20;margin:14px -22px 0;padding:8px 22px;background:rgba(10,14,19,.93);backdrop-filter:blur(8px);
border-bottom:1px solid var(--line);display:flex;gap:2px;overflow-x:auto;scrollbar-width:none}
nav::-webkit-scrollbar{display:none}
nav a{color:var(--muted);text-decoration:none;font-size:12.5px;padding:5px 11px;border-radius:7px;white-space:nowrap}
nav a:hover{color:var(--text);background:var(--panel2)}
.kpis{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin-top:16px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:11px 13px;min-width:0}
.kpi .l{font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.7px}
.kpi .v{font-size:21px;font-weight:720;line-height:1.25;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kpi .s{font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pos{color:var(--up)}.neg{color:var(--down)}.mut{color:var(--muted)}
.t-short{color:var(--down)}.t-long{color:var(--up)}.t-neu{color:var(--gold)}
.kpi .v .t-short,.kpi .v .t-long,.kpi .v .t-neu{font-size:17px}
section{margin-top:30px;scroll-margin-top:52px}
h2{font-size:16.5px;margin:0 0 11px;font-weight:720;letter-spacing:.1px}
h2 small{font-weight:400;color:var(--muted);font-size:12.5px;margin-left:8px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:13px;padding:15px 17px;min-width:0}
.grid2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px}
.grid-sum{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:12px}
.mini-h{font-size:11px;text-transform:uppercase;letter-spacing:.7px;color:var(--muted);font-weight:650;margin-bottom:9px}
ul.bul{margin:0;padding-left:18px}ul.bul li{margin:0 0 7px}ul.bul li:last-child{margin-bottom:0}
.note{margin-top:13px;padding:11px 14px;border-left:3px solid var(--gold);background:rgba(227,179,65,.07);border-radius:0 9px 9px 0}
.note-h{font-size:11px;text-transform:uppercase;letter-spacing:.7px;color:var(--gold);font-weight:700;margin-bottom:4px}
.note p{margin:0 0 7px}.note p:last-child{margin:0}
.gauge{width:100%;max-width:270px;height:auto;display:block}
.tfs{margin-top:8px}
.tfrow{display:grid;grid-template-columns:34px 1fr 44px;align-items:center;gap:8px;font-size:12.5px;margin:5px 0}
.tfrow span{color:var(--muted)}.tfrow b{text-align:right;font-weight:600}
.tfrow .bar{position:relative;height:7px;background:#1b2430;border-radius:4px}
.tfrow .bar:after{content:"";position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:#3b4654}
.tfrow .bar i{position:absolute;top:0;bottom:0;border-radius:4px}
.tfrow .bar i.neg{background:var(--down)}.tfrow .bar i.pos{background:var(--up)}
.fine{font-size:12px;color:var(--muted);margin:10px 0 0;line-height:1.5}
.fine b{color:#c5d0dc;font-weight:600}
.scns{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:12px}
.scn{background:var(--panel);border:1px solid var(--line);border-radius:13px;padding:14px 16px;border-top:3px solid var(--line);min-width:0}
.scn.short{border-top-color:var(--down)}.scn.long{border-top-color:var(--up)}
.scn-h{display:flex;align-items:center;gap:11px;margin-bottom:11px}
.scn-h .id{width:30px;height:30px;border-radius:8px;background:var(--panel2);border:1px solid var(--line);display:grid;place-items:center;font-weight:800;color:var(--gold);flex:none}
.scn-h .tt{flex:1;min-width:0}
.scn-h .t{font-weight:700;font-size:15px;line-height:1.25}
.scn-h .r{font-size:12px;color:var(--muted)}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:10.5px;font-weight:750;letter-spacing:.5px;white-space:nowrap}
.pill.short{background:rgba(246,70,93,.14);color:var(--down)}.pill.long{background:rgba(46,189,133,.14);color:var(--up)}
.pill.neu{background:rgba(227,179,65,.14);color:var(--gold)}
.pill.open{background:rgba(88,166,255,.16);color:var(--blue)}.pill.off{background:#1b2430;color:var(--muted)}
.strow{display:grid;grid-template-columns:minmax(0,38%) 132px minmax(0,1fr);gap:6px 16px;padding:10px 2px;border-top:1px solid var(--line2);align-items:start}
.strow.sthead{border-top:0;padding:0 2px 7px;font-size:10.5px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);font-weight:650}
.strow .st-d{font-size:13px;color:#c5d0dc;line-height:1.5}
.st-n{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.sid{display:inline-grid;place-items:center;width:23px;height:23px;border-radius:6px;background:var(--panel2);border:1px solid var(--line);font-weight:800;color:var(--gold);font-size:12.5px;flex:none}
dl.kv{display:grid;grid-template-columns:108px minmax(0,1fr);gap:7px 12px;margin:0}
dl.kv dt{color:var(--muted);font-size:12px;padding-top:2px}
dl.kv dd{margin:0;font-size:13.5px}
.big{font-size:15.5px}
.tgs{display:grid;gap:4px}
.tg{display:grid;grid-template-columns:22px 62px 44px minmax(0,1fr);align-items:baseline;gap:6px;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:4px 9px;font-size:12.5px}
.tg span{color:var(--muted);font-weight:650}
.tg b{color:var(--blue);font-size:13.5px}.tg i{font-style:normal;color:var(--gold);font-weight:650}
.tg em{font-style:normal;color:var(--muted);font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.warn{margin-top:10px;font-size:12.5px;color:#ffd2b0;background:rgba(240,136,62,.09);border:1px solid rgba(240,136,62,.3);border-radius:9px;padding:7px 10px}
.warn.info{color:#c5d0dc;background:rgba(88,166,255,.07);border-color:rgba(88,166,255,.3)}
.inval{margin-top:12px;padding:10px 14px;border-radius:10px;background:var(--panel);border:1px dashed #3a4656;font-size:13.5px}
ul.rules{margin:0;padding-left:18px;font-size:13px;color:#c5d0dc}ul.rules li{margin:0 0 6px}ul.rules li:last-child{margin:0}
.chartbox{padding:12px 12px 8px;overflow-x:auto}
.chart{width:100%;height:auto;display:block;min-width:860px}
.tablebox{overflow-x:auto}
.tablebox table{min-width:470px}table.ladder{min-width:800px}table.cal{min-width:540px}
table{width:100%;border-collapse:collapse}
th{font-size:10.5px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);font-weight:650;text-align:left;padding:6px 9px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:7px 9px;border-bottom:1px solid var(--line2);vertical-align:middle;font-size:13.5px}
tr:last-child td{border-bottom:0}
td.num,th.num{text-align:right;white-space:nowrap}
.stars{color:var(--gold);letter-spacing:1px;font-size:12px;white-space:nowrap}.stars .off{color:#333e4c}
table.ladder td.lv{white-space:nowrap}
table.ladder td.cf{color:#c5d0dc;font-size:12.5px}
table.ladder tr.zR td.lv b{color:#ff8c9b}table.ladder tr.zS td.lv b{color:#6fdcb0}
table.ladder tr.s1 td{opacity:.72}
.ztag{display:inline-block;margin-left:7px;font-size:10.5px;font-weight:700;color:var(--gold);border:1px solid rgba(227,179,65,.45);border-radius:5px;padding:0 5px}
tr.pxrow td{background:rgba(227,179,65,.1);border-top:1px solid rgba(227,179,65,.5);border-bottom:1px solid rgba(227,179,65,.5);color:var(--gold);font-size:13px;padding:6px 9px}
tr.pxrow b{font-size:14.5px;margin-left:4px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--gold);margin-right:6px;box-shadow:0 0 0 3px rgba(227,179,65,.2)}
.pb{display:inline-block;vertical-align:middle;width:58px;height:6px;background:#1b2430;border-radius:3px;overflow:hidden;margin-right:7px}
.pb i{display:block;height:100%;background:var(--blue);border-radius:3px}
.pv{display:inline-block;min-width:34px}
.rel{letter-spacing:1px;font-size:11px;white-space:nowrap}.rel .off{color:#333e4c}
.rel.r3{color:var(--down)}.rel.r2{color:var(--orange)}.rel.r1{color:var(--muted)}
table.cal td.tm{font-weight:650;white-space:nowrap}
table.cal tr.past td{opacity:.45}
.evn{font-size:12px;color:var(--muted);margin-top:2px;line-height:1.4}
.sq{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:7px}
.sq.asia{background:var(--blue)}.sq.london{background:var(--gold)}.sq.ny{background:var(--violet)}
ul.win{list-style:none;margin:0;padding:0;font-size:13px}ul.win li{display:grid;grid-template-columns:104px minmax(0,1fr);gap:10px;padding:6px 0;border-bottom:1px solid var(--line2)}ul.win li:last-child{border-bottom:0}ul.win li b{font-weight:650;white-space:nowrap}ul.win li span{color:#c5d0dc}
a.src,span.src{font-size:11.5px;color:var(--muted);border:1px solid var(--line);border-radius:5px;padding:0 6px;text-decoration:none;white-space:nowrap;margin-left:3px}
a.src:hover{color:var(--text)}
.calc{display:grid;grid-template-columns:1fr 1fr;gap:10px 12px}
.calc label{font-size:11.5px;color:var(--muted);display:flex;flex-direction:column;gap:4px}
.calc input,.calc select{background:#0c1117;border:1px solid var(--line);border-radius:8px;color:var(--text);padding:7px 9px;font:inherit;font-size:14px;width:100%}
.calc input:focus,.calc select:focus{outline:2px solid rgba(88,166,255,.5);outline-offset:0}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:11px}
.chip{background:var(--panel2);border:1px solid var(--line);color:var(--text);border-radius:999px;padding:4px 11px;font:inherit;font-size:12px;cursor:pointer}
.chip:hover{border-color:var(--gold)}
.calc-out{margin-top:12px;padding:11px 13px;border-radius:10px;background:rgba(227,179,65,.08);border:1px solid rgba(227,179,65,.35);font-size:14px}
.calc-out b{color:var(--gold);font-size:17px}
.hbs{display:grid;gap:6px}
.hb{display:grid;grid-template-columns:28px 1fr 44px;gap:9px;align-items:center;font-size:12.5px}
.hb span{color:var(--muted)}.hb b{text-align:right;font-weight:600}
.hb .pb{width:100%;height:8px;margin:0}
tr.tot td{font-weight:700;border-top:1px solid var(--line)}
.honest{margin-top:12px;padding:12px 14px;border-radius:10px;background:rgba(88,166,255,.07);border:1px solid rgba(88,166,255,.3);font-size:13.5px;line-height:1.55}
.disc{margin-top:14px;padding:11px 14px;border-radius:10px;background:#0c1117;border:1px solid var(--line);font-size:12.5px;color:#aab6c4}
footer{margin-top:26px;color:var(--muted);font-size:12px;text-align:center}
@media (max-width:980px){.kpis{grid-template-columns:repeat(3,minmax(0,1fr))}.grid2,.grid-sum{grid-template-columns:minmax(0,1fr)}}
@media (max-width:760px){.strow{grid-template-columns:minmax(0,1fr) auto}.strow .st-d{grid-column:1/-1}.strow.sthead{display:none}}
@media (max-width:600px){.wrap{padding:14px 12px 50px}nav{margin:12px -12px 0;padding:8px 12px}.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}
h1{font-size:19px}.stamp{text-align:left}.scns{grid-template-columns:minmax(0,1fr)}dl.kv{grid-template-columns:92px minmax(0,1fr)}.calc{grid-template-columns:1fr}
.card{padding:13px 13px}td,th{padding:6px 7px}}
@media print{body{background:#fff;color:#111}nav,.banner,.chips{display:none!important}.card,.scn,.kpi{background:#fff;border-color:#ccc}
.mut,.fine,.kpi .l,.kpi .s,th,dl.kv dt{color:#555}section{break-inside:avoid}}
"""

JS = r"""
(function(){
  var D = JSON.parse(document.getElementById('plan-data').textContent);
  function f(x,d){ if(x===null||isNaN(x)) return 'n.d.'; var s=Math.abs(x).toFixed(d===undefined?1:d).replace('.',','); return (x<0&&parseFloat(Math.abs(x).toFixed(d===undefined?1:d))!==0?'\u2212':'')+s; }
  function fs(x,d){ var s=f(x,d); return (x>0?'+':'')+s; }
  // --- navigation without touching location.hash (works inside sandboxed previews)
  document.querySelectorAll('nav a').forEach(function(a){ a.addEventListener('click',function(ev){ var el=document.getElementById(a.getAttribute('href').slice(1)); if(el){ev.preventDefault(); el.scrollIntoView({behavior:'smooth',block:'start'});} }); });
  // --- staleness banner
  function stale(){ if(D.historical) return; var b=document.getElementById('banner'); var now=Date.now(); if(now>D.close_ms){ b.style.display='block'; b.innerHTML='<b>Briefing scaduto.</b> La seduta a cui si riferisce questo piano è terminata: livelli e scenari non sono più validi. Serve il briefing della seduta corrente.'; } else if(now>D.stale_ms){ b.style.display='block'; b.innerHTML=D.market_open?'<b>Dati di oltre 4 ore fa.</b> I livelli statici restano validi, ma trend, livelli dinamici e scenari vanno aggiornati con un nuovo briefing.':'<b>Briefing preparato a mercato chiuso.</b> Il mercato ha riaperto da oltre 4 ore: trend, livelli dinamici e scenari vanno aggiornati con un nuovo briefing.'; } }
  stale(); setInterval(stale,60000);
  // --- on narrow screens show the most recent part of the price charts first
  document.querySelectorAll('.chartbox.toright').forEach(function(el){ el.scrollLeft=el.scrollWidth; });
  // --- position size calculator
  var ids=['c-cap','c-cur','c-risk','c-stop','c-oz','c-fx'];
  function calc(){ var cap=+document.getElementById('c-cap').value, cur=document.getElementById('c-cur').value, rk=+document.getElementById('c-risk').value, st=+document.getElementById('c-stop').value, oz=+document.getElementById('c-oz').value, fx=+document.getElementById('c-fx').value; var out=document.getElementById('c-out'); if(!(cap>0&&rk>0&&st>0&&oz>0&&fx>0)){ out.textContent='Inserire valori validi.'; return; } var riskAcc=cap*rk/100, riskUsd=cur==='EUR'?riskAcc*fx:riskAcc, lots=riskUsd/(st*oz), lr=Math.floor(lots*100)/100, real=lr*st*oz, realAcc=cur==='EUR'?real/fx:real; var sym=cur==='EUR'?'\u20ac':'$'; out.innerHTML='Size massima: <b>'+f(lr,2)+' lotti</b> ('+f(lr*oz,0)+' once) \u2014 perdita allo stop \u2248 '+f(realAcc,0)+' '+sym+(cur==='EUR'?' ('+f(real,0)+' $)':'')+' su un rischio massimo di '+f(riskAcc,0)+' '+sym+'.'+(lr<0.01?' <span style="color:#f6465d">Con questi parametri anche 0,01 lotti supera il rischio fissato.</span>':''); }
  ids.forEach(function(i){ var el=document.getElementById(i); if(el){ el.addEventListener('input',calc); el.addEventListener('change',calc);} });
  document.querySelectorAll('.chip').forEach(function(c){ c.addEventListener('click',function(){ document.getElementById('c-stop').value=c.getAttribute('data-stop'); calc(); }); });
  if(document.getElementById('c-out')) calc();
  // --- optional live price (only when the file is opened in a normal browser with network access)
  var badge=document.getElementById('livebadge');
  function setPrice(p){
    var px=document.getElementById('px'), px2=document.getElementById('px2'), ch=document.getElementById('pxchg'); if(px) px.textContent=f(p,1); if(px2) px2.textContent=f(p,1); if(ch){ var d=p-D.prev_close; ch.textContent=fs(d,1)+' $ ('+fs(100*d/D.prev_close,2)+'%)'; ch.className=d>=0?'pos':'neg'; }
    if(!D.market_open){ var sn=document.getElementById('pxsince'), lb=document.getElementById('pxlabel'); if(sn) sn.textContent=D.since; if(lb) lb.textContent='Prezzo spot'; }
    var row=document.getElementById('pxrow'); if(!row) return; var tb=row.parentNode, rows=[].slice.call(tb.querySelectorAll('tr[data-lo]')), placed=false;
    rows.forEach(function(r){ var lo=+r.getAttribute('data-lo'), hi=+r.getAttribute('data-hi'), mid=(lo+hi)/2, dist=lo>p?lo-p:(hi<p?hi-p:0); var c=r.querySelector('.dist'); if(c) c.innerHTML=fs(dist,1)+' $ <span class="mut">('+fs(100*dist/p,2)+'%)</span>'; if(!placed&&mid<p){ tb.insertBefore(row,r); placed=true; } });
    if(!placed) tb.appendChild(row); }
  function live(){ if(D.historical||Date.now()>D.close_ms) return; if(!D.market_open&&Date.now()<D.open_ms) return; /* prepared with the market closed: wait for the reopening */
    fetch('https://api.gold-api.com/price/XAU',{cache:'no-store'}).then(function(r){return r.json();}).then(function(j){ if(j&&j.price>0){ setPrice(+j.price); var t=new Date(); badge.className='badge live'; badge.textContent='prezzo live '+('0'+t.getHours()).slice(-2)+':'+('0'+t.getMinutes()).slice(-2)+' (indicativo)'; } }).catch(function(){ /* sandboxed preview or offline: keep the snapshot */ }); }
  live(); setInterval(live,60000);
})();
"""


def build_html(A: dict, X: dict) -> str:
    ctx = X.get("context") or {}
    st, pv = X.get("plan_status"), X.get("prev_session")
    ev = prepare_events(A, X.get("calendar") or [], ctx)
    asof_r = rome(A["asof"])
    gen_r = rome(X["generated"])
    skey, sname = session_now(A)
    day = A["tday"]
    title = f"XAU/USD — Briefing intraday {day.day:02d}/{day.month:02d}/{day.year}"
    head_note = "" if A["market_open"] else " · livelli validi per la prossima seduta"
    hist = " · <b>ricostruzione storica</b>" if X.get("historical") else ""
    fresh_from = A["asof"] if A["market_open"] else max(A["asof"], A["bounds"]["open"])     # closed market: count from the reopening
    data_blob = {"asof_ms": int(A["asof"].timestamp() * 1000), "close_ms": int(A["bounds"]["close"].timestamp() * 1000),
                 "stale_ms": int((fresh_from + 4 * H).timestamp() * 1000), "market_open": bool(A["market_open"]),
                 "open_ms": int(A["bounds"]["open"].timestamp() * 1000),
                 "since": since_text(A), "price": A["price"], "prev_close": A["prev"]["close"],
                 "historical": bool(X.get("historical")),
                 "tday": day.strftime("%Y-%m-%d"), "generated_ms": int(X["generated"].timestamp() * 1000),
                 "bias": A["bias"]["label"], "summary": one_liner(A)}
    parts = [
        f"<!doctype html><html lang='it'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{e(title)}</title><style>{CSS}</style></head><body><div class='wrap'>",
        f"<div class='top'><div><h1><span class='sym'>XAU/USD</span> · Briefing intraday</h1>"
        f"<div class='sub'>{d_long(day).capitalize()}{head_note}</div></div>"
        f"<div class='stamp'>Dati aggiornati alle <b>{hm(asof_r)}</b> del {asof_r.day:02d}/{asof_r.month:02d} (ora di Roma){hist}"
        f"<br><span class='badge'>{e(sname)}</span><span class='badge' id='livebadge'>{'storico' if X.get('historical') else ('istantanea' if A['market_open'] else 'ultima chiusura')}</span></div></div>",
        "<div class='banner' id='banner'></div>",
        "<nav><a href='#sintesi'>Sintesi</a><a href='#piano'>Piano operativo</a><a href='#mappa'>Mappa intraday</a>"
        "<a href='#livelli'>Livelli</a><a href='#agenda'>Agenda e orari</a><a href='#tecnica'>Quadro tecnico</a>"
        + ("<a href='#contesto'>Contesto</a>" if ctx.get("drivers") else "") +
        "<a href='#rischio'>Rischio</a><a href='#verifica'>Verifica</a><a href='#metodo'>Metodo</a></nav>",
        sec_kpis(A), sec_summary(A, ev, ctx, st, pv), sec_plan(A, st), sec_map(A, st), sec_ladder(A), sec_agenda(A, ev),
        sec_technical(A, X.get("intermarket") or [], X.get("yields") or {}), sec_context(ctx),
        sec_risk(A, X.get("eurusd")), sec_verify(A, X.get("replay"), X.get("journal"), pv),
        sec_method(A, X.get("quality") or {}),
        f"<footer>Generato il {gen_r.day:02d}/{gen_r.month:02d}/{gen_r.year} alle {hm(gen_r)} (ora di Roma) · xauusd_monitor "
        f"· dati Dukascopy · solo a scopo informativo</footer></div>",
        f"<script type='application/json' id='plan-data'>{json.dumps(data_blob).replace('<', chr(92) + 'u003c')}</script><script>{JS}</script></body></html>",
    ]
    return "".join(parts)


# ----------------------------------------------------------------------------- markdown
def build_markdown(A: dict, X: dict) -> str:
    ctx = X.get("context") or {}
    ev = prepare_events(A, X.get("calendar") or [], ctx)
    asof_r = rome(A["asof"])
    p, prev, ind, bias = A["price"], A["prev"], A["ind"], A["bias"]
    L = [f"# XAU/USD — Briefing intraday · {d_long(A['tday'])}", "",
         f"_Dati aggiornati alle {hm(asof_r)} del {asof_r.day:02d}/{asof_r.month:02d} (ora di Roma). Prezzi spot, lato bid._", "",
         (f"**Prezzo {fnum(p)}** ({fnum(p - prev['close'], 1, True)} $ {since_text(A)})" if A["market_open"] else
          f"**Ultima chiusura {fnum(p)}** ({pct(prev['chg'])} {d_short(pd.Timestamp(prev['tday']))}; mercato chiuso, riapre {reopen_text(A)})")
         + f" · **Trend: {bias['label']}** "
         f"({fnum(bias['score'], 2, True)}) · ATR D1 {fnum(ind['atr_d'], 0)} $ · RSI H1 {fnum(ind['H1']['rsi'], 0)}", "",
         "## In sintesi"]
    import re
    st, pv = X.get("plan_status"), X.get("prev_session")
    for b in summary_bullets(A, ev, st, pv):
        t = re.sub(r"</?b>", "**", b)
        t = re.sub(r"<[^>]+>", "", t)
        L.append(f"- {html.unescape(t)}")
    if ctx.get("analyst_note"):
        when = f" (scritta {note_when(ctx)})" if ctx.get("note_time") else ""
        L += ["", f"**Lettura del giorno{when}.** " + " ".join(ctx["analyst_note"])]
    follow = bool(st and not st["same"] and st["rows"])
    if follow:
        L += ["", f"## Scenari già pubblicati oggi — situazione alle {hm(asof_r)}"]
        for r in st["rows"]:
            L.append(f"- **{r['id']} · {r['title']}** ({'SHORT' if r['dir'] < 0 else 'LONG'}, {status_origin(r)}: {status_levels(r)}) — "
                     f"**{STATE_PILL[r['state']][0]}**. {status_detail(r, A)}")
        L.append(f"- _Il piano ufficiale è quello delle {hm(rome(st['asof']))}; esiti secondo le regole meccaniche del registro "
                 f"(uscita a T1, allo stop o a fine seduta, stop mai spostato)._")
    L += ["", f"## Piano operativo (scenari ricalcolati alle {hm(asof_r)})" if follow else "## Piano operativo (scenari condizionali)"]
    if not A["scenarios"]:
        L.append("Nessun setup soddisfa i criteri minimi: non operare finché il prezzo non si avvicina a una zona.")
    for s in A["scenarios"]:
        d = s["dir"]
        tg = " · ".join(f"T{i + 1} {fnum(t['price'])} ({fnum(t['rr'], 1)}R)" for i, t in enumerate(s["targets"]))
        if s["type"] == "fade":
            cond = f"zona {zrange(s['zone_lo'], s['zone_hi'])} (centro {fnum(s['entry'])})"
        elif s["type"] == "break":
            cond = f"chiusura M15 {'sotto' if d < 0 else 'sopra'} {fnum(s['trigger'])} (ingresso ≈ {fnum(s['entry'])})"
        else:
            cond = f"chiusura H1 {'sopra' if d > 0 else 'sotto'} {fnum(s['trigger'])}, ingresso sul ritorno a {fnum(s['entry'])}"
        pr = s.get("prob_touch")
        prt = f" · raggiungibilità storica {fnum(100 * pr, 0)}%" if pr is not None and not math.isnan(pr) else ""
        L.append(f"- **{s['id']} · {setup_title(s)}** ({'SHORT' if d < 0 else 'LONG'}, {s['role']}): {cond}; "
                 f"stop **{fnum(s['stop'])}** (rischio {fnum(s['risk'])} $/oz); {tg}{prt}.")
    if A.get("invalidation"):
        d = bias["dir"]
        L.append(f"- **Invalidazione** della lettura {'ribassista' if d < 0 else 'rialzista'}: chiusura H1 "
                 f"{'sopra' if d < 0 else 'sotto'} {fnum(A['invalidation'])}.")
    touch = "Tocco oggi" if A["market_open"] else "Tocco in seduta"
    L += ["", "## Livelli chiave", "", f"| Zona | Tipo | Cosa c'è | Forza | Distanza | {touch} |", "|---|---|---|---|---|---|"]
    placed = False
    for z in A["zones"]:
        if z["stars"] < 2 and abs(z["dist"]) > 0.5 * ind["atr_d"]:
            continue
        if not placed and z["mid"] < p:
            L.append(f"| **{fnum(p)}** | **PREZZO** | | | | |")
            placed = True
        dist = z["lo"] - p if z["side"] == "R" else (z["hi"] - p if z["side"] == "S" else 0)
        pr = "—" if z.get("prob") is None or math.isnan(z["prob"]) else f"{fnum(100 * z['prob'], 0)}%"
        kind = {"R": "Resistenza", "S": "Supporto", "X": "Zona attuale"}[z["side"]]
        L.append(f"| {zrange(z['lo'], z['hi'])} | {kind} | "
                 f"{', '.join(n.split(' = ')[0] for n in z['names'][:3])} | {'★' * z['stars']} | {fnum(dist, 1, True)} $ | {pr} |")
    if ev["available"]:
        L += ["", "## Agenda (ora di Roma)" if A["market_open"] else f"## Agenda di {d_short(A['tday'])} e della settimana (ora di Roma)"]
        def figs(x):
            parts = ([f"atteso {ev_num(x['f'])}"] if x["f"] else []) + ([f"prec. {ev_num(x['p'])}"] if x["p"] else [])
            return f" ({', '.join(parts)})" if parts else ""
        for x in ev["today"]:
            if x["rel"] >= 2 or x["note"]:
                L.append(f"- **{hm(x['t'])}** {x['cur']} · {x['title']}{figs(x)}" + (f" — {x['note']}" if x["note"] else ""))
        nd = "Domani" if A["market_open"] and (ev["next_day"] - A["tday"]).days == 1 else d_short(ev["next_day"])
        for x in ev["tomorrow"]:
            L.append(f"- _{nd} {hm(x['t'])}_ {x['cur']} · {x['title']}{figs(x)}")
        for x in ev["week"]:
            L.append(f"- _{d_short(x['t'])} {hm(x['t'])}_ {x['cur']} · {x['title']}{figs(x)}")
    if pv and pv.get("rows"):
        L += ["", f"## Scenari della seduta precedente ({d_short(pv['tday'])}): esito finale"]
        for r in pv["rows"]:
            L.append(f"- **{r['id']} · {r['title']}** ({'SHORT' if r['dir'] < 0 else 'LONG'}, {status_origin(r)}: {status_levels(r)}) — "
                     f"**{STATE_PILL[r['state']][0]}**. {status_detail(r, A)}")
    rp = X.get("replay")
    if rp and rp.get("total"):
        t = rp["total"]
        L += ["", "## Verifica delle regole",
              f"Replica meccanica su {rp['days']} sedute: {t['trades']} operazioni attivate, {fnum(t['win'], 0)}% in utile, "
              f"{fnum(t['stop'], 0)}% in stop, risultato medio {fnum(t['avg'], 2, True)}R (±{fnum(t['se'], 2)}), prima dei costi. "
              f"Il trend delle 08:00 ha anticipato la direzione della seduta nel {fnum(rp['bias_hit'], 0)}% dei casi. "
              f"Gli scenari sono una mappa a rischio definito, non un sistema con vantaggio statistico dimostrato."]
    L += ["", "_Analisi informativa automatica, non consulenza finanziaria. Il trading con leva comporta un rischio elevato di perdita._", ""]
    return "\n".join(L)
