# Automatisk uppdatering (ingen AI)

Allt här är vanlig Python (standardbiblioteket + `pypdf`). Ingen AI och inget Claude-konto behövs:
GitHub Actions kör skripten varje natt helt på egen hand.

## Vad körs och när
`.github/workflows/site.yml`:

| Händelse | Vad händer |
|---|---|
| Varje natt 04:17 UTC (och knappen *Run workflow*) | `update`: kör `scripts/auto_update.py`, committar `site/auto.js` + `data/auto_state.json` om något ändrats ("Automaattinen päivitys ÅÅÅÅ-MM-DD"), sedan `deploy` |
| Push till `main` | bara `deploy`: `python build.py --check` → `dist/` → GitHub Pages |

`auto_update.py` kör varje modul i `scripts/sources/` (egen tråd och tidsgräns, en krasch stoppar inte de andra):

| Modul | Källa | Ger |
|---|---|---|
| `rotax_italia` | rotaxmaxkart.it (+ Apex/youcrono-booklets) | resultat, poängtabellen (titelstriden) |
| `rotax_ch` | rotaxmax.ch | resultat |
| `wsk` | wskarting.it (alla WSK-serier, RMC Euro Trophy) | resultat |
| `international` | RMCIT, Rotax Grand Finals, FIA Karting | resultat |
| `news_rss` | 22 RSS-flöden från tidningar | nyheter (rubrik + länk) |

## Vad uppdateras
- `site/auto.js` (`window.RR_AUTO`): nyheter, resultat och poängtabell som **inte redan finns** i den
  kurerade `site/data.js`. Kurerad data vinner alltid (samma tävling = datum ±2 dagar och samma serie
  eller bana). Sidan slår ihop de två filerna när den laddas och märker automatiska rader med AUTO.
- `data/auto_state.json`: vilka dokument som redan lästs, så att varje natt bara hämtar nytt
  (normalt ca 37 anrop och 40 sekunder, varav 22 är nyhetsflödena).
- `auto.js` skrivs bara om när innehållet ändrats, eller var sjätte dag (för att GitHub inte ska
  stänga av schemat efter 60 dagar utan commits). `log` i filen visar de senaste 30 noteringarna.
- `site/data.js` och `site/index.html` rörs aldrig.

## Köra manuellt
```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/auto_update.py --dry-run -v      # visa vad som skulle ändras, skriv inget
.venv/bin/python scripts/auto_update.py                   # som nattkörningen
.venv/bin/python scripts/auto_update.py --only wsk        # en källa
.venv/bin/python scripts/auto_update.py --since 2025-01-01
.venv/bin/python scripts/auto_update.py --backfill        # nytt state, läs allt sedan 2022 (~15 min)
.venv/bin/python scripts/tests/test_rotax_ch.py           # livetest för en modul
```
Vill du ta bort en automatisk rad: lägg in tävlingen/nyheten i `site/data.js` (den kurerade vinner),
eller radera raden ur `site/auto.js` och committa.

## Lägga till en ny källa
1. Skapa `scripts/sources/<namn>.py` med `def run(ctx) -> SourceResult` (se `common.py`).
   - Får aldrig kasta undantag: fånga allt och skriv till `result.log`.
   - Hitta nya dokument själv via arrangörens resultatsidor (inga hårdkodade URL:er per tävling).
   - Hoppa över det som redan lästs: `ctx.seen(url)` / `ctx.mark(url)`. Respektera `ctx.since`.
   - Använd `fetch()` (artig fördröjning per värd) och `result_row(...)` för resultatrader.
   - Ge bara en rad när Robin säkert finns i finalens officiella resultat. Osäkert → hoppa över och logga.
2. Skriv ett livetest i `scripts/tests/test_<namn>.py` som jämför mot resultaten i `site/data.js`.
3. Kör `.venv/bin/python scripts/auto_update.py --only <namn> --since 2022-01-01` och sedan
   `--backfill` en gång, så att state redan känner till gamla dokument. Committa `data/auto_state.json`.

`auto_update.py` hittar modulen automatiskt; inget annat behöver ändras.
