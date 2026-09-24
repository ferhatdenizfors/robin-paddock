# Publicering – Robin Räikkönen Paddock

## Claude-artefakt (huvudlänk)
- URL: https://claude.ai/artifact/Erts8j1ERbdy5Qtm9X6JFE
- Sida: `site/index.html`. Tillhörande filer: `data.js`, `assets.js`, `assets/photos/*.jpg`, `assets/kart/gp-racing-kart.jpg`.
- Delning görs från sidans Share-meny (artefakten är privat tills den delas).

### Publicera om (samma länk)
1. Artifact-verktyget: `action: "read"`, `url: <URL ovan>` (krävs innan en annan konversation får publicera till länken).
2. Artifact-verktyget: `action: "publish"`, `url: <URL ovan>`, `file_path: "site/index.html"`,
   `files: {"data.js": "site/data.js"}` (lägg även till `"assets.js": "site/assets.js"` och bildfiler om de ändrats; filer som utelämnas behålls).
   Sökvägarna är relativa till projektmappen – sessionen måste arbeta i projektmappen.

## Egen hosting (valfritt)
`python3 build.py --check` bygger `dist/` (index.html + data.js + assets.js + assets/) som fungerar på vilken statisk hosting som helst:
- GitHub Pages: pusha innehållet i `dist/` till ett repo och aktivera Pages. Den veckovisa uppdateringen kan då även committa/pusha.
- Netlify Drop: dra `dist/`-mappen till app.netlify.com/drop.
