# Mettere online il briefing

Ogni volta che si genera il briefing (`python3 run_briefing.py`) viene ricostruito anche un sito statico
pronto da pubblicare:

| Percorso | Contenuto |
|---|---|
| `site/index.html` | L'ultimo briefing: è la pagina principale |
| `site/archivio/index.html` | Elenco delle sedute con l'esito verificato degli scenari del piano ufficiale |
| `site/archivio/<data>.html` | Una pagina permanente per ogni seduta (indirizzo che non cambia) |
| `site/version.json` | Ora dell'ultimo aggiornamento: le pagine aperte lo controllano e propongono di ricaricare |
| `xauusd-sito.zip` | La stessa cartella compressa, da scaricare in un colpo solo |

Sono solo file: niente server, niente database. Tutti i collegamenti sono relativi, quindi il sito funziona
alla radice di un dominio, in una sottocartella e anche aprendo `index.html` dal disco.
`python3 run_briefing.py --site-only` ricostruisce il sito senza scaricare dati (utile dopo aver cambiato
`site.json`).

## 1. Prima di renderlo pubblico

Tre cose da sistemare, in ordine di importanza. Non è un parere legale.

**a) I dati sono di terzi e sono concessi per uso personale.**
Le condizioni d'uso di Dukascopy (da cui arrivano le candele) ammettono solo l'uso personale e non
commerciale e vietano di pubblicare, ridistribuire o rielaborare i contenuti senza consenso scritto
(<https://www.dukascopy.com/swiss/english/legal-pages/terms-of-use/>). Per ForexFactory (calendario macro)
risulta lo stesso divieto di ripubblicazione senza consenso scritto, calendario compreso
(<https://fxmacrodata.com/articles/fxmacrodata-vs-forex-factory>; la pagina ufficiale è
<https://www.forexfactory.com/notices>). Per un sito pubblico servono quindi **il loro consenso** oppure
**altre fonti**. Due bozze di richiesta sono in fondo a questo documento. Se il consenso non arriva, il
motore si può adattare: calendario da fonti ufficiali (BLS, BEA, Federal Reserve) o rimosso dal sito;
prezzi da un fornitore con licenza di pubblicazione (in genere a pagamento) o dalle API del proprio broker.

**b) Chi pubblica scenari operativi ha degli obblighi.**
Diffondere al pubblico analisi con ingressi, stop e obiettivi non richiede autorizzazioni, ma può rientrare
tra le «raccomandazioni di investimento» del regolamento europeo sugli abusi di mercato (MAR, art. 20, e
regolamento delegato 2016/958): identità di chi pubblica ben visibile, data e ora, fatti distinti dalle
interpretazioni, fonti indicate, interessi e conflitti dichiarati. ESMA e Consob ricordano che la formula
«non è consulenza finanziaria» da sola non basta. Riferimenti:
[Consob, Quaderno FinTech 18](https://www.consob.it/documents/d/area-pubblica/fintech18),
[Diritto Bancario](https://www.dirittobancario.it/wp-content/uploads/2025/06/2025-Di-Carlo-Gobbo-Fininfluencer.pdf),
[Teleborsa/ANSA](https://teleborsa.ansa.it/notiziario/finanza/finfluencer-chi-e-cosa-fa-e-come-diventarlo/).

- Il sito ha già: data e ora di ogni briefing, metodo e fonti, lettura del giorno separata dai dati,
  verifica storica delle regole, archivio con gli esiti di tutti i piani, avvertenza sui rischi.
- Resta da compilare in `site.json`: `autore`, `contatto`, `interessi` (per esempio «Chi pubblica può
  detenere posizioni sull'oro, anche in direzione diversa dagli scenari»; scrivere ciò che è vero).
- Da non fare: rispondere ai lettori su cosa fare con i loro soldi. La consulenza personalizzata è
  un'attività riservata. Link di affiliazione a broker o abbonamenti cambiano il quadro: in quel caso
  sentire prima un professionista.

**c) Motori di ricerca.** Finché `indicizzabile` è `false` le pagine chiedono ai motori di ricerca di non
essere indicizzate: il sito è raggiungibile da chi ha il link, ma non compare su Google. Conviene lasciarlo
così finché i punti a) e b) non sono a posto, poi mettere `true`.

Il sito non usa cookie né strumenti di tracciamento (lo dice anche il piè di pagina). Il prezzo in tempo
reale è chiesto dal browser di chi legge a gold-api.com.

## 2. GitHub Pages con aggiornamento automatico

Il briefing si rigenera da solo e il sito si aggiorna senza toccare nulla. Con l'account gratuito GitHub
Pages funziona solo sui repository **pubblici**: codice, piani e registro saranno visibili a tutti.

Una volta sola (non serve estrarre file né usare programmi):

1. Creare un account su <https://github.com> e un nuovo repository di tipo **Public**.
2. **Settings → Pages → Build and deployment → Source: GitHub Actions**.
3. **Add file → Upload files**: caricare `xauusd-github.zip` così com'è, senza estrarlo, poi *Commit changes*.
4. Creare nel repository il file `.github/workflows/aggiorna-sito.yml` con il contenuto di
   `github/aggiorna-sito.yml`, poi *Commit changes*.

Al salvataggio del punto 4 parte la prima esecuzione: scompatta il pacchetto nel repository, genera il
briefing e pubblica il sito su `https://<utente>.github.io/<repository>/` (servono da tre a cinque minuti).

Per rendere tutto più semplice, `python3 github/prepara_pacchetto.py --repo utente/repository` ricrea il
pacchetto, stampa i link diretti alle tre pagine (quello del punto 4 apre il file già compilato) e scrive
`GUIDA_GITHUB.html`, una guida illustrata con gli stessi link.

Per cambiare le impostazioni basta aprire `site.json` nel repository (icona della matita), modificare e
salvare: ogni modifica fatta a mano nel repository fa partire subito un aggiornamento.

Da lì in poi il briefing parte da solo **ogni 30 minuti** dal lunedì al venerdì, giorno e notte, e la
domenica sera (righe `cron` di `aggiorna-sito.yml`, in ora UTC). Gli orari di GitHub sono indicativi: un
aggiornamento può arrivare in ritardo o saltare, ma con questa cadenza il successivo arriva presto. In
qualsiasi momento si può lanciare un aggiornamento a mano: scheda Actions → il flusso → *Run workflow*.

A ogni giro vengono salvati nel repository piani e registro; le pagine dei briefing, che sono file grandi,
solo ogni tre ore e nelle ore di fine seduta (lo fa `github/salva.sh`), così il repository non si gonfia.

Da sapere:

- **Collaudo.** Il flusso è stato provato in locale passo per passo (installazione del pacchetto,
  generazione, salvataggio, sito), ma non su GitHub: la prima esecuzione è il collaudo vero. Se fallisce,
  il motivo è nel registro dell'esecuzione (scheda Actions).
- **Fonti dei dati.** Se l'esecuzione si ferma con «dati di mercato non disponibili», il feed dei prezzi
  rifiuta le richieste dai server di GitHub. In quel caso il sito resta all'ultima versione e mostra
  l'avviso di dati non aggiornati. L'alternativa è far girare lo script sul proprio computer (vedi README)
  e caricare la cartella `site/`.
- **Lettura del giorno.** Il commento alle notizie non è automatico: compare solo se nel repository c'è il
  file `context/<data>.json` della seduta. Lo si può caricare a mano nella cartella `context`.
- **Senza GitHub Pages attivo** (punto 2 saltato, o repository privato con account gratuito) l'esecuzione
  genera e salva comunque il briefing e segnala che il sito non è stato pubblicato. Dopo aver attivato
  Pages: scheda Actions → *Aggiorna briefing e sito* → *Run workflow*.
- GitHub sospende le esecuzioni pianificate dei repository pubblici rimasti a lungo senza attività: se
  arriva l'avviso, si riattivano dalla scheda Actions.

## 3. Impostazioni (`site.json`)

| Campo | Significato |
|---|---|
| `titolo` | Nome del sito, in alto a sinistra |
| `sottotitolo` | Descrizione usata nell'archivio e nelle anteprime dei link |
| `autore` | Chi pubblica (nome e cognome o ragione sociale): compare nel piè di pagina |
| `contatto` | Indirizzo e-mail o link per essere contattati |
| `interessi` | Dichiarazione sugli interessi di chi pubblica (posizioni in oro, rapporti con broker) |
| `indirizzo` | Indirizzo pubblico del sito, es. `https://utente.github.io/oro-intraday/`. Attiva gli indirizzi canonici e l'immagine di anteprima quando si condivide un link |
| `indicizzabile` | `false` = fuori dai motori di ricerca; `true` = indicizzabile |
| `archivio_max` | `0` = tutte le sedute; `N` = solo le ultime N |

Le immagini in `assets/` (`og.png` per le anteprime dei link, `apple-touch-icon.png` per l'icona sul
telefono) si possono sostituire con le proprie, mantenendo i nomi.

## Farsi trovare su Google

1. In `site.json` impostare `indirizzo` (l'indirizzo pubblico del sito) e `"indicizzabile": true`. Da quel
   momento le pagine non chiedono più di essere escluse e il sito pubblica anche `sitemap.xml`, l'elenco
   delle pagine per i motori di ricerca.
2. Aprire <https://search.google.com/search-console>, aggiungere il sito come proprietà di tipo
   **Prefisso URL** e scegliere la verifica con **File HTML**: Google fa scaricare un file `google….html`.
3. Caricare quel file nel progetto, nella cartella principale o in `assets/`, senza rinominarlo: viene
   pubblicato così com'è alla radice del sito. Poi cliccare **Verifica** su Google.
4. In Search Console, menu **Sitemap**: scrivere `sitemap.xml` e cliccare **Invia**.

Google decide da solo se e quando mostrare le pagine: di solito servono da qualche giorno a qualche
settimana.

Per aggiornare i programmi di un repository già attivo senza toccare piani, registro e note:
`python3 github/prepara_pacchetto.py --aggiornamento` crea `xauusd-aggiornamento.zip`, da caricare nel
repository come un file qualsiasi.

## 4. Altre strade

- **Netlify, a mano.** Estrarre `xauusd-sito.zip` e trascinare la cartella su <https://app.netlify.com/drop>:
  il sito è online in un minuto con un indirizzo `…netlify.app`. Per aggiornarlo: pagina *Deploys* del
  sito, trascinare la cartella nuova nel riquadro in fondo.
- **Hosting proprio.** Caricare il contenuto di `site/` via FTP o dal pannello del provider, nella cartella
  pubblica o in una sottocartella.

## 5. Come si comporta la pagina online

- In alto restano sempre visibili data e ora dei dati. Se sono passate più di quattro ore, o la seduta è
  finita, compare un avviso.
- Quando viene pubblicato un aggiornamento, chi ha la pagina aperta vede in basso il pulsante *Ricarica*.
- Le pagine delle sedute passate segnalano che il piano non è più valido e rimandano all'ultimo briefing.
- Nell'archivio gli esiti compaiono dopo la chiusura della seduta e non vengono mai corretti.

## Bozze per chiedere il consenso

Da inviare dalla pagina contatti dei due siti, in inglese.

> **Subject: Permission request – free chart data on a small public page**
>
> Hello, I run a small, free web page with a daily intraday briefing on spot gold (XAU/USD). Levels and
> charts are computed from the candles of your free chart service (freeserv.dukascopy.com) and Dukascopy is
> credited on every page. The page carries no advertising and offers no raw data for download.
> Could you confirm in writing whether this use is allowed, or under which conditions (credit, link,
> licence)? Page: … Thank you, …

> **Subject: Permission request – weekly calendar export on a small public page**
>
> Hello, I run a small, free web page with a daily briefing on gold. I would like to show the US events of
> the day taken from your weekly calendar export (time, title, forecast, previous), translated into Italian
> and credited to Forex Factory with a link. Could you confirm in writing whether this is allowed, or under
> which conditions? Page: … Thank you, …
