#!/usr/bin/env bash
# Salva nel repository piani, registro e archivio dopo ogni aggiornamento. Lo chiama il flusso di GitHub
# (.github/workflows): piani e registro a ogni giro, le pagine dei briefing (file grandi) ogni 3 ore e
# nelle ore di fine seduta, per non gonfiare il repository quando gli aggiornamenti sono frequenti.
set -e
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
rm -f xauusd-sito.zip
mkdir -p .git/info output/cache
printf 'site/\n*.zip\n__pycache__/\noutput/cache/calendar_*\n' >> .git/info/exclude

f=output/cache/pagine.txt
u=$(cat "$f" 2>/dev/null | tr -cd 0-9 || true)
ora=$(date +%s)
h=$(date -u +%H)
if [ $(( ora - 10#${u:-0} )) -ge 10800 ] || [ "$h" = 20 ] || [ "$h" = 21 ]; then
  echo "$ora" > "$f"
  git add -A
else
  git add -A -- . ':(exclude)output/XAUUSD_briefing_*'
fi

if git diff --cached --quiet; then
  echo "Nessuna novità da salvare."
else
  git commit -q -m "Briefing del $(TZ=Europe/Rome date '+%d/%m/%Y %H:%M')"
  # se nel frattempo qualcuno ha caricato file, ci si rimette in coda; se non si riesce, si salva al prossimo giro
  git push || { git pull --rebase --autostash && git push; } || {
    git rebase --abort || true
    echo "::warning::Salvataggio rimandato al prossimo giro"
  }
fi
