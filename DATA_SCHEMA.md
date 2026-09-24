# Robin Räikkönen Paddock – dataschema

`site/data.js` innehåller exakt en tilldelning: `window.RR_DATA = { ... };` (giltig JSON efter `=`).
Sidan (`site/index.html`) renderar allt från detta objekt. Alla fält är valfria om inget annat sägs – saknas data döljs sektionen.

## Konventioner
- **Flerspråkig text** = objekt `{ "fi": "...", "sv": "...", "en": "..." }`. Finska är huvudspråket och ska låta naturligt (inte översättningsfinska). Svenska = rikssvenska.
- **Datum** = `"YYYY-MM-DD"` (eller `"YYYY-MM"` om dagen är okänd).
- **Länder** = IOC/FIA-trebokstavskod: `ITA`, `SUI`, `FIN`, `FRA`, `ESP`, `BEL`, `SWE`, `GER`, `POR`, `UAE`, `BHR` …
- **sources** = array med URL:er. Inga påhittade fakta: saknas uppgift → utelämna fältet.
- **Integritet**: Robin är minderårig. Bara offentlig idrottsinfo (resultat, klasser, team, utrustning, offentliga citat). Ingen skola, bostadsort, privata familjeförhållanden, inga bilder.
- Sammanfattningar skrivs med egna ord. Rubriker på nyheter återges ordagrant på originalspråk (korta).

## Toppnivå

```jsonc
{
  "meta": { "updated": "2026-09-24" },

  "driver": {
    "name": "Robin Räikkönen",
    "born": "2015-01-27",            // bara om offentligt källbelagt, annars "2015"
    "nationality": "FIN",
    "number": "7",                    // nuvarande tävlingsnummer om känt, annars utelämna
    "current": { "year": 2026, "class": "…", "team": "…", "chassis": "…", "engine": "…", "tyres": "…" },
    "status": { "fi": "…", "sv": "…", "en": "…" }   // 1–2 meningar om läget just nu
  },

  "redbull": {
    "announced": "2026-09-09", "starts": "2027",
    "summary": {fi,sv,en},
    "quotes": [ Quote ]
  },

  "seasons": [ { "year": 2025, "age": "10", "classes": ["…"], "teams": ["…"], "chassis": ["…"], "engines": ["…"], "summary": {fi,sv,en} } ],

  "results": [ {
    "id": "2025-wsk-sms-r2", "date": "2025-02-09", "year": 2025,
    "series": "WSK Super Master Series", "round": "R2",
    "track": "Adria Karting Raceway", "trackId": "adria", "country": "ITA",
    "class": "Mini U10", "team": "…", "chassis": "…", "engine": "…",
    "quali": "12", "heats": "4-2-3", "prefinal": "5", "final": "3",   // strängar; "DNF", "DSQ", "–" tillåtna
    "finalPos": 3,                    // numerisk huvudfinalplacering eller null (används för statistik/diagram)
    "champ": "P4",                    // ställning i serien efter tävlingen/slutställning (sträng eller {fi,sv,en})
    "fieldSize": 34,
    "note": {fi,sv,en},               // kort höjdpunkt (ledde varv, omkörningar, olycka …)
    "sources": ["…"], "confidence": "high|medium|low"
  } ],

  "titles": [ { "year": 2024, "title": {fi,sv,en}, "sources": ["…"] } ],

  "calendar": [ {                      // kommande tävlingar (från idag och framåt)
    "id": "2026-wsk-final-cup-r1", "start": "2026-10-15", "end": "2026-10-18",
    "series": "WSK Final Cup", "round": "R1", "name": {fi,sv,en},
    "track": "…", "trackId": "lonato", "country": "ITA", "class": "…",
    "status": "confirmed|likely|possible|unknown",
    "note": {fi,sv,en}, "sources": ["…"]
  } ],
  "calendar2027": [ samma form ],
  "plans2027": {fi,sv,en},

  "news": [ {
    "date": "2026-09-09", "headline": "Robin Raikkonen joins Red Bull junior team",
    "source": "RACER", "url": "https://…", "lang": "en",
    "category": "result|signing|injury|interview|feature|other", "importance": 1-5,
    "summary": {fi,sv,en}
  } ],

  "quotes": [ Quote ],                 // citat OM Robins racing (Kimi, Red Bull, team)

  "tech": {
    "anatomy": {                       // exakt dessa nycklar
      "fairing": Part, "chassis": Part, "tyres": Part, "steering": Part, "logger": Part, "tank": Part,
      "seat": Part, "radiator": Part, "engine": Part, "exhaust": Part, "drive": Part, "axle": Part, "brakes": Part
    },
    "classes": [ {
      "id": "mini", "name": "Mini (WSK Mini U10)", "aka": "60 Mini",
      "robin": "raced|current|next|future|reference",   // reference = F4/F1 som jämförelse
      "age": {fi,sv,en}, "engine": {fi,sv,en},
      "hp": 9, "hpText": {fi,sv,en},  // hp = number för diagrammet
      "rpm": {fi,sv,en}, "gearbox": {fi,sv,en},
      "weightKg": 110, "weightText": {fi,sv,en},
      "tyres": {fi,sv,en},
      "topSpeed": 90, "topSpeedText": {fi,sv,en},
      "note": {fi,sv,en}, "sources": ["…"]
    } ],
    "equipment": [ { "year": 2026, "class": "…", "team": "…", "chassis": "…", "engine": "…", "tyres": "…", "sources": ["…"] } ],
    "weekend": [ { "step": {fi,sv,en}, "text": {fi,sv,en} } ],
    "glossary": [ { "term": {fi,sv,en}, "text": {fi,sv,en} } ],
    "facts": [ { "text": {fi,sv,en} } ]
  },

  "tracks": [ {
    "id": "lonato", "name": "South Garda Karting", "short": "Lonato",
    "town": "Lonato del Garda", "region": "Lombardia", "country": "ITA",
    "lat": 45.44, "lon": 10.5, "lengthM": 1010, "widthM": "8–10", "corners": "…",
    "direction": {fi,sv,en}, "record": {fi,sv,en}, "homologation": "…",
    "desc": {fi,sv,en}, "notable": {fi,sv,en}, "robin": {fi,sv,en},
    "kimi": false, "sources": ["…"]
  } ],

  "ladder": {
    "steps": [ { "order": 1, "stage": {fi,sv,en}, "minAge": {fi,sv,en}, "typical": {fi,sv,en}, "robinYear": "2030", "now": false, "done": false, "desc": {fi,sv,en} } ],   // done=true: steg Robin redan klarat (robinYear = när, t.ex. "2022–24"); now=true: nuvarande steg
    "superlicence": { "text": {fi,sv,en}, "points": [ { "series": {fi,sv,en}, "points": "40-30-20…" } ] },
    "rules": [ { "topic": {fi,sv,en}, "text": {fi,sv,en} } ]
  },

  "rbjt": {
    "founded": "…", "how": {fi,sv,en}, "kartSignings": {fi,sv,en},
    "graduates": [ { "name": "Max Verstappen", "note": {fi,sv,en} } ],
    "dropped": [ { "name": "…", "note": {fi,sv,en} } ],
    "roster": [ "…" ]
  },

  "finns": {
    "f1": [ { "name": "Keke Rosberg", "years": "1978–1986", "highlight": {fi,sv,en} } ],
    "juniors": [ { "name": "…", "series": "…", "note": {fi,sv,en} } ]
  },

  "kimi": {
    "stats": { "starts": "349", "wins": "21", "poles": "18", "podiums": "103", "fastestLaps": "46", "titles": "2007", "note": {fi,sv,en} },
    "vs": [ { "topic": {fi,sv,en}, "kimi": {fi,sv,en}, "robin": {fi,sv,en} } ],
    "path": [ { "year": "1990", "age": "10", "cat": {fi,sv,en}, "text": {fi,sv,en}, "key": true } ],
    "quotes": [ Quote ],
    "role": {fi,sv,en}, "helmet": {fi,sv,en}, "nicknames": [ "Iceman", … ]
  },

  "facts": [ { "text": {fi,sv,en} } ],   // "Tiesitkö?" på startsidan

  "quiz": [ { "q": {fi,sv,en}, "options": [ {fi,sv,en} ×4 ], "answer": 0, "explain": {fi,sv,en} } ]
}
```

**Part** = `{ "title": {fi,sv,en}, "text": {fi,sv,en}, "nerd": {fi,sv,en} }` (nerd = valfri extra nördig detalj).

**Quote** = `{ "who": "Kimi Räikkönen", "date": "2026-09-09" | "year": "2012", "text": "originalcitatet", "lang": "en", "tr": {fi,sv,en}, "context": {fi,sv,en}, "source": "https://…" }`.
