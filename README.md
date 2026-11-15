# XAU/USD · Monitor intraday

Genera un briefing operativo sull'oro spot (XAU/USD) con dati di mercato aggiornati:
contesto di trend, zone di supporto/resistenza, scenari condizionali con stop e obiettivi,
agenda macro in ora di Roma, volatilità per fascia oraria e una verifica onesta delle regole
sui prezzi passati.

## Uso

```bash
pip install -r requirements.txt      # Python 3.10 o superiore
python3 run_briefing.py              # briefing con i dati di adesso
```

Il report viene scritto in `output/`:

| File | Contenuto |
|---|---|
| `XAUUSD_briefing_<data>.html` | Report completo (autonomo: si apre con doppio clic, nessun file esterno) |
| `XAUUSD_briefing_latest.html` | Copia dell'ultimo report, con nome fisso |
| `XAUUSD_briefing_<data>.md` | Sintesi testuale, comoda da incollare in chat o note |
| `plans/plan_<data>.json` | Piano ufficiale della seduta, in formato leggibile da programmi |
| `plans/session_<data>.json` | Tutti gli scenari pubblicati nella seduta (piano ufficiale e aggiornamenti), con lettera e ora |
| `journal.csv` | Registro: esito dei piani delle sedute concluse, verificato sui prezzi reali |

Accanto a `output/` viene ricostruito a ogni esecuzione anche il **sito da pubblicare**: cartella `site/`
(ultimo briefing come pagina principale, archivio delle sedute con gli esiti) e `xauusd-sito.zip`.
Come metterlo online, anche con aggiornamento automatico su GitHub, è spiegato in
[PUBBLICAZIONE.md](PUBBLICAZIONE.md).

Aperto in un normale browser, il report aggiorna da solo il prezzo ogni minuto (fonte indicativa:
gold-api.com) e ricalcola le distanze dai livelli. Se la seduta è finita o i dati hanno più di
quattro ore compare un avviso: va rigenerato.

Altre opzioni:

```bash
python3 run_briefing.py --asof "2026-11-11 08:00"   # ricostruisce il briefing di un momento passato (ora di Roma)
python3 run_briefing.py --no-replay                 # salta la verifica statistica (più veloce)
python3 run_briefing.py --site-only                 # ricostruisce solo il sito dai briefing già generati
python3 run_briefing.py --no-site                   # genera il briefing senza toccare il sito
python3 tests/selftest.py                           # controlli di coerenza del motore
python3 research/gestione_posizione.py              # studio: la gestione attiva cambia i risultati?
```

## Routine consigliata

1. **Prima di Londra (08:30–09:00)** — rigenerare: è il piano «ufficiale» che finisce nel registro.
2. **Prima dei dati USA (14:15)** — rigenerare: livelli dinamici, range e scenari cambiano.
3. A fine giornata non serve fare nulla: al briefing successivo il piano del giorno prima viene
   verificato automaticamente e aggiunto a `journal.csv`.

**Nel fine settimana** (o nella pausa serale 23:00–00:00) il briefing si può generare lo stesso: riporta
l'ultima chiusura, il bilancio della settimana, l'esito degli scenari di venerdì, l'agenda dell'intera
settimana entrante e un piano per la seduta di lunedì. È un piano provvisorio: l'apertura può avvenire
in gap, quindi va rigenerato a mercato aperto, prima di Londra.

Per l'esecuzione automatica sul proprio computer:

```bash
# Linux / macOS (cron): giorni feriali alle 08:30 e alle 14:15
30 8 * * 1-5  cd /percorso/xauusd_monitor && python3 run_briefing.py --quiet
15 14 * * 1-5 cd /percorso/xauusd_monitor && python3 run_briefing.py --quiet
```

Su Windows: Utilità di pianificazione → nuova attività → programma `python`, argomenti
`run_briefing.py --quiet`, cartella di avvio quella del progetto.

## Aggiornamenti durante la seduta

Ogni scenario mostrato viene seguito finché non ha un esito. Quando si rigenera il briefing a seduta
in corso, in testa al «Piano operativo» compare la tabella **Scenari già pubblicati oggi**: per ciascuno
lo stato (in attesa, aperta, stop, target 1, annullato, non attivato), ora e prezzo d'ingresso, miglior
prezzo toccato e risultato in R a quell'ora. Sulla mappa una freccia viola segna l'ingresso.

- Il **piano ufficiale** è l'ultimo generato prima dell'apertura di Londra (09:00). Se uno dei suoi
  scenari si attiva prima, il piano resta congelato: un'operazione aperta o chiusa non può sparire dal
  registro. È l'unico che entra in `journal.csv`.
- Gli **aggiornamenti successivi** non toccano il piano ufficiale: aggiungono i propri scenari con le
  lettere seguenti (C, D, …). Una lettera indica sempre la stessa operazione per tutta la seduta.
- Uno scenario di un aggiornamento ancora in attesa viene **ritirato** se l'aggiornamento successivo
  non lo ripropone; se invece si è attivato, viene seguito fino all'esito.
- Uno scenario identico a una posizione già aperta non viene riproposto; se un nuovo scenario può
  attivarsi mentre una posizione è aperta, la scheda lo segnala.
- Gli esiti seguono le regole meccaniche: uscita al primo obiettivo, allo stop o a fine seduta, stop
  mai spostato. Una rottura la cui chiusura M15 di conferma è già oltre il primo obiettivo è annullata.
- Il giorno dopo, la «Sintesi» e la sezione «Verifica» riportano l'esito finale di tutti gli scenari
  della seduta precedente, compresi quelli degli aggiornamenti (indicati come «fuori registro»).

## Note del giorno (facoltative)

Il file `context/<data>.json` aggiunge al report una lettura qualitativa, le notizie con le fonti e
correzioni alla rilevanza degli eventi in calendario. Se manca, il report si genera lo stesso senza
quelle sezioni. Vedi `context/2026-11-12.json` come esempio; il campo `note_time` indica l'ora in cui
la nota è stata scritta e compare accanto al titolo.

## Come ragiona

- **Dati**: candele spot XAU/USD (lato bid) dal feed pubblico Dukascopy. Giornata di trading con
  chiusura alle 17:00 di New York, come sulle piattaforme FX/CFD. Ora legale gestita in automatico.
- **Livelli**: estremi e chiusura di ieri, pivot classici, estremi settimanali/mensili, soglie tonde,
  swing su D1/H4/H1, medie mobili, estremi di sessione, proiezioni del range medio. I livelli vicini
  vengono fusi in zone, con una forza (★) che cresce con numero e peso delle confluenze.
- **Trend**: punteggio da −1 a +1 su D1, H4, H1 e M15 (medie mobili, pendenza, RSI, struttura).
- **Scenari**: un ingresso su ritracciamento e uno su rottura, nella direzione del trend; due ingressi
  ai bordi del range se il mercato è laterale. Stop a 0,15 ATR giornalieri oltre la zona, primo obiettivo
  sulla prima zona significativa ad almeno ~1R. I livelli che si muovono in seduta (medie orarie, estremi
  di oggi) sono mostrati ma non entrano nelle regole: il piano cambia solo per un motivo di prezzo. Le soglie sono in `CFG` dentro `xau/engine.py`.
- **Frequenze di tocco**: distribuzione reale delle escursioni da quell'ora a fine seduta nelle ultime
  250 sedute, normalizzata per l'ATR (verificata fuori campione: stime ben calibrate).
- **Verifica**: le stesse regole vengono riapplicate alle ultime 250 sedute usando solo i dati
  disponibili in quel momento (nessuno sguardo al futuro; lo controlla `tests/selftest.py`).

## Limiti da conoscere

- La verifica storica mostra che le regole, applicate meccanicamente, **non hanno un vantaggio
  statistico**: il risultato medio è intorno a zero prima dei costi, e il trend del mattino non
  anticipa la direzione della seduta. Il report è una mappa per operare a rischio definito, non un
  generatore di segnali.
- Nemmeno la **gestione attiva** cambia il quadro: nello studio `research/gestione_posizione.py`
  (12/11/2026, 221 operazioni su 250 sedute) stop a pareggio, uscite anticipate e stop a inseguimento
  hanno spostato il risultato medio di appena −0,05/+0,02R rispetto alla regola base (margine d'errore
  sul risultato: ±0,08R), con segno diverso nelle due metà del campione. Lo stop a pareggio dopo +1R
  riduce le operazioni in perdita (dal 62% al 55%) ma anche quelle in utile (dal 38% al 33%).
- Le fonti sono pubbliche e gratuite, senza garanzie di servizio: se il feed dei prezzi non risponde
  lo script si ferma con un messaggio; se manca il calendario o il controllo incrociato, il report
  viene generato senza quelle parti.
- I prezzi dei broker differiscono di qualche decina di centesimi da quelli del report.
- Le fonti concedono i dati per uso personale: prima di pubblicare il sito leggere il punto 1 di
  [PUBBLICAZIONE.md](PUBBLICAZIONE.md).

Analisi informativa, non consulenza finanziaria. Il trading con leva comporta un rischio elevato di
perdita.
