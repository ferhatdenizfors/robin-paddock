"""Shared helpers for the automatic updater (no Claude/AI involved).

Every source module under scripts/sources/ exposes

    def run(ctx: Context) -> SourceResult

and must never raise: wrap network/parse problems and report them in SourceResult.log.
"""
from __future__ import annotations

import io
import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / 'site'
STATE_FILE = ROOT / 'data' / 'auto_state.json'
AUTO_JS = SITE / 'auto.js'

USER_AGENT = 'RobinPaddockFanPage/1.0 (unofficial non-commercial fan page; github.com/ferhatdenizfors/robin-paddock)'
DRIVER_PATTERNS = [r'R[AÄ]IKK[OÖ]NEN\s*,?\s*ROBIN', r'ROBIN\s+R[AÄ]IKK[OÖ]NEN', r'R[AÄ]IKK[OÖ]NEN\s+R\.?\b']

_last_request: dict[str, float] = {}
REQUESTS: dict[str, int] = {}        # HTTP attempts per host (read by auto_update.py for its summary)
_budget = threading.local()          # per-thread deadline set by auto_update.py (time budget per source)


def set_deadline(ts: float | None) -> None:
    """After time.time() > ts, fetch() in this thread returns None without a request."""
    _budget.deadline = ts


def request_total() -> int:
    return sum(REQUESTS.values())


# ---------------------------------------------------------------- http
def fetch(url: str, *, binary: bool = False, timeout: int = 30, min_interval: float = 1.5, retries: int = 2):
    """GET with a polite per-host delay. Returns str (or bytes) or None on failure."""
    host = urllib.parse.urlsplit(url).netloc
    deadline = getattr(_budget, 'deadline', None)
    if deadline is not None and time.time() > deadline:
        return None
    wait = min_interval - (time.time() - _last_request.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    for attempt in range(retries + 1):
        REQUESTS[host] = REQUESTS.get(host, 0) + 1
        try:
            req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': '*/*'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                _last_request[host] = time.time()
                data = r.read()
                if binary:
                    return data
                charset = r.headers.get_content_charset() or 'utf-8'
                return data.decode(charset, errors='replace')
        except urllib.error.HTTPError as e:
            _last_request[host] = time.time()
            if e.code in (403, 404, 410):
                return None
        except Exception:
            _last_request[host] = time.time()
        time.sleep(2 * (attempt + 1))
    return None


def pdf_text(data: bytes) -> str:
    """Extract text from a PDF (layout mode keeps table columns on one line).

    Pages whose text sits inside form XObjects (e.g. DynaPDF/Apex booklets) come back
    empty in layout mode; those pages fall back to a positional reconstruction
    (fragments grouped by baseline, columns separated by runs of spaces)."""
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    out = []
    for page in reader.pages:
        try:
            txt = page.extract_text(extraction_mode='layout') or ''
        except Exception:
            txt = ''
        if not txt.strip():
            try:
                txt = _pdf_page_positional(page)
            except Exception:
                txt = ''
        if not txt.strip():
            try:
                txt = page.extract_text() or ''
            except Exception:
                txt = ''
        out.append(txt)
    return '\n'.join(out)


def _pdf_page_positional(page, y_tol: float = 2.5, char_w: float = 4.0) -> str:
    """Rebuild text lines from positioned fragments (visitor API, follows form XObjects)."""
    frags = []

    def visit(text, cm, tm, _fd, _fs):
        if not text or not text.strip():
            return
        if text.strip().count('\n') >= 2:
            return  # pypdf re-reports a whole form XObject's text as one blob: duplicate, no layout
        x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        for i, part in enumerate(text.split('\n')):
            if part.strip():
                frags.append((y - i * 10, x, part.strip()))

    page.extract_text(visitor_text=visit)
    if not frags:
        return ''
    frags.sort(key=lambda f: -f[0])
    lines, cur, cur_y = [], [], None
    for y, x, s in frags:
        if cur_y is None or abs(y - cur_y) <= y_tol:
            cur.append((x, s))
            cur_y = y if cur_y is None else cur_y
        else:
            lines.append(cur)
            cur, cur_y = [(x, s)], y
    if cur:
        lines.append(cur)
    out = []
    for ln in lines:
        ln.sort()
        buf = ''
        for x, s in ln:
            col = int(max(x, 0) / char_w)
            if buf:
                buf += ' ' * max(2, col - len(buf))
            else:
                buf = ' ' * col
            buf += s
        out.append(buf.rstrip())
    return '\n'.join(out)


# ---------------------------------------------------------------- text
def fold(s: str) -> str:
    """Uppercase ASCII fold: 'Räikkönen' -> 'RAIKKONEN'."""
    s = unicodedata.normalize('NFKD', s or '')
    return ''.join(c for c in s if not unicodedata.combining(c)).upper()


def mentions_driver(text: str) -> bool:
    t = fold(text)
    return 'RAIKKONEN' in t and ('ROBIN' in t or re.search(r'RAIKKONEN\s+R\b', t) is not None)


def driver_lines(text: str) -> list[str]:
    """Lines of a results document that contain Robin (folded match)."""
    return [ln for ln in (text or '').splitlines() if 'RAIKKONEN' in fold(ln) and ('ROBIN' in fold(ln) or re.search(r'RAIKKONEN\s+R\b', fold(ln)))]


def iso(d: date | datetime | str | None) -> str | None:
    if d is None:
        return None
    if isinstance(d, str):
        return d
    return d.strftime('%Y-%m-%d')


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# ---------------------------------------------------------------- site data
def load_js_object(path: Path, var: str) -> dict:
    if not path.exists():
        return {}
    txt = path.read_text(encoding='utf-8')
    m = re.match(r'\s*window\.' + re.escape(var) + r'\s*=\s*', txt)
    if not m:
        return {}
    body = txt[m.end():].strip()
    if body.endswith(';'):
        body = body[:-1]
    return json.loads(body)


def load_site_data() -> dict:
    return load_js_object(SITE / 'data.js', 'RR_DATA')


def track_index(site: dict) -> list[tuple[str, list[str], str]]:
    """[(trackId, [folded keywords], country)] built from site data tracks."""
    idx = []
    for t in site.get('tracks', []):
        words = {fold(t.get('name', '')), fold(t.get('short', '')), fold(t.get('town', '') or '')}
        words |= {fold(w) for w in re.split(r'[\s,()/–-]+', (t.get('town') or '') + ' ' + (t.get('short') or '')) if len(w) > 3}
        idx.append((t['id'], [w for w in words if w], t.get('country', '')))
    return idx


EXTRA_TRACK_WORDS = {
    'lonato': ['SOUTH GARDA', 'LONATO'], 'franciacorta': ['FRANCIACORTA', 'CASTREZZATO'],
    'sette-laghi': ['7 LAGHI', 'SETTE LAGHI', 'CASTELLETTO'], 'sarno': ['SARNO', 'NAPOLI'],
    'cremona': ['CREMONA'], 'jesolo': ['JESOLO', 'PISTA AZZURRA'], 'viterbo': ['VITERBO', 'LEOPARD'],
    'ala': ['ALA'], 'wohlen': ['WOHLEN'], 'levier': ['LEVIER'], 'vesoul': ['VESOUL', 'PUSEY'],
    'wackersdorf': ['WACKERSDORF'], 'le-mans': ['LE MANS'], 'genk': ['GENK'], 'la-conca': ['LA CONCA', 'MURO LECCESE'],
    'portimao': ['PORTIMAO', 'ALGARVE'], 'kecskemet': ['KECSKEMET'], 'bira': ['BIRA', 'PATTAYA'],
}


def guess_track(text: str, site: dict) -> tuple[str | None, str | None]:
    """Best trackId + country for a venue string / document header."""
    t = fold(text)
    for tid, words in EXTRA_TRACK_WORDS.items():
        for w in words:
            if (w == 'ALA' and re.search(r'\bALA\b', t)) or (w != 'ALA' and w in t):
                country = next((c for i, _, c in track_index(site) if i == tid), None)
                return tid, country
    for tid, words, country in track_index(site):
        if any(len(w) > 4 and w in t for w in words):
            return tid, country
    return None, None


def pos_num(v) -> int | None:
    m = re.match(r'^\s*P?\s*(\d{1,3})\b', str(v or ''))
    return int(m.group(1)) if m else None


def result_row(**kw) -> dict:
    """Result row in the site's schema (see DATA_SCHEMA.md), flagged auto."""
    row = {k: v for k, v in kw.items() if v not in (None, '', [])}
    row['auto'] = True
    if 'finalPos' not in row:
        row['finalPos'] = pos_num(row.get('final'))
    if 'year' not in row and row.get('date'):
        row['year'] = int(str(row['date'])[:4])
    row.setdefault('confidence', 'medium')
    return row


# ---------------------------------------------------------------- run context
@dataclass
class Context:
    site: dict                       # RR_DATA (curated)
    auto: dict                       # previous RR_AUTO
    state: dict                      # persistent state (processed urls etc.)
    today: date = field(default_factory=lambda: datetime.now(timezone.utc).date())
    since: date | None = None        # only look at events after this date
    dry_run: bool = False

    def seen(self, key: str) -> bool:
        return key in self.state.setdefault('seen', {})

    def mark(self, key: str, note: str = '') -> None:
        self.state.setdefault('seen', {})[key] = note or self.today.isoformat()


@dataclass
class SourceResult:
    news: list[dict] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    battle: dict | None = None
    log: list[str] = field(default_factory=list)
