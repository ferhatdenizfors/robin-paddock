# Veckouppdatering – Robin Räikkönen Paddock

Instruktioner för den schemalagda uppdateringen (och för manuella uppdateringar).

## Filer
- `site/data.js` – ALLT innehåll (`window.RR_DATA = {...};`). Schema: `DATA_SCHEMA.md`.
- `site/index.html` – sidans kod/design. Rör inte vid uppdateringar.
- `build.py` – `python3 build.py --check` validerar data.js och bygger `dist/` (för egen hosting).
- `research/` – ursprunglig research (bakgrund, källor).

## Steg vid varje uppdatering
1. Läs `site/data.js` och notera `meta.updated`, senaste `results`, `calendar` och `news`.
2. Sök (WebSearch/WebFetch) efter allt nytt sedan `meta.updated`:
   - Resultat: rotaxmaxkart.it (RMC Italia), rotaxmax.ch (Schweiz), rotax-racing.com / rmc-eurotrophy / grandfinals, wskarting.it, fiakarting.com, apex-timing.com, vroomkart.com/.it, kartcom.com.
   - Finska medier: Ilta-Sanomat (is.fi), Iltalehti, MTV Uutiset, Yle, Turun Sanomat; internationellt: RacingNews365, Motorsport.com, RACER, RaceFans, GPblog, PlanetF1, grandprix.com, Red Bull.
   - Sökord: "Robin Räikkönen", "Robin Raikkonen", "Räikkösen poika karting", "Robin Räikkönen Red Bull".
3. Uppdatera `site/data.js`:
   - Nya tävlingar → nya rader i `results` (samma fältformat, unika `id`, `finalPos` som tal, `sources`). Uppdatera `champ` (ställning) och vid behov `seasons[år].summary` och `titles`.
   - Passerade tävlingar tas bort ur `calendar`; nya kända tävlingar läggs till (status confirmed/likely/possible/unknown med motivering i `note`). 2027-program → `calendar2027`/`plans2027`.
   - Nya artiklar → överst i `news` (rubrik ordagrant på originalspråk, sammanfattning med egna ord på fi/sv/en, exakt URL).
   - Justera `driver.status` så att den beskriver läget just nu.
   - Ny bana → lägg till i `tracks` (verifiera koordinater och längd) innan den används som `trackId`.
   - Sätt `meta.updated` till dagens datum.
4. Regler: bara verifierade fakta med källa; Robin är minderårig – bara offentlig idrottsinfo; finskan ska vara naturlig (huvudspråk), alla texter på fi/sv/en.
5. Kör `python3 build.py --check` – måste sluta med `OK`.
6. Publicera om artefakten till SAMMA länk (se `PUBLISH.md`) med `site/index.html` som sida och `data.js` som tillhörande fil.
7. Om `dist/` är kopplad till GitHub Pages (se `PUBLISH.md`): committa och pusha.
8. Hittades inget nytt: ändra ingenting och publicera inte om.
