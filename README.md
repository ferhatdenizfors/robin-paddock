# Robin Räikkönen Paddock

Epävirallinen fanisivu Robin Räikkösen karting-urasta – tulokset, kalenteri, uutiset, tekniikka, radat ja Kimi-vertailu (FI / SV / EN).
*Unofficial fan page. Not affiliated with the Räikkönen family, Red Bull or any series or team.*

## Så fungerar det
| Del | Fil | Uppdateras |
|---|---|---|
| Sidan (design + kod) | `site/index.html` | för hand |
| Kurerat innehåll (texter, resultat, banor, quiz …) | `site/data.js` | för hand (valfritt Claude-jobb) |
| Automatiskt innehåll (nya resultat, nyheter, poängställning) | `site/auto.js` | **varje natt av GitHub Actions** |
| Bilder, bankartor, ikoner, flaggor | `site/assets.js`, `site/assets/` | sällan |

Varje natt kör `.github/workflows/site.yml` skriptet `scripts/auto_update.py` (vanlig Python, ingen AI):
- läser de officiella resultatlistorna (Rotax Italia, Rotax Schweiz, WSK / RMC Euro Trophy, RMCIT / Grand Finals, FIA Karting) och lägger in nya tävlingar där Robin finns i finalen,
- läser tidningarnas egna RSS-flöden och lägger in nya artiklar om Robin (rubrik + länk),
- uppdaterar poängtabellen i titelstriden,
- bygger sidan (`python build.py --check` → `dist/`) och publicerar den på GitHub Pages.

Sidan sköter sig själv: nedräkning, "nästa tävling" och kalender går på datum, och statusraden byggs om från senaste resultatet.

## Manuellt
```bash
pip install -r requirements.txt
python scripts/auto_update.py --dry-run   # visa vad som skulle uppdateras
python build.py --check                   # validera data.js och bygg dist/
```
Se `DATA_SCHEMA.md` för dataformatet och `UPDATE_GUIDE.md` för hur kurerat innehåll uppdateras.

## Licenser och källor
Foton från Wikimedia Commons (fotografer och licenser listas på sidan), ikoner: Phosphor Icons (MIT), flaggor: flag-icons (MIT), bankartor: © OpenStreetMap contributors (ODbL). Resultat och nyheter länkar alltid till sin källa.
