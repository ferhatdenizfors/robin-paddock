"""WSK Promotion results site (www.wskarting.it) - no AI, plain parsing.

Covers every series whose timing documents WSK publishes:
  * WSK Super Master Series / Champions Cup / Euro Series / Open Cup (Open Series) / Final Cup / Super Cup
    -> round index pages  https://www.wskarting.it/results.asp?yy=YYYY&r=N&c=C&s=<series>
       discovered from the RESULTS menu on https://www.wskarting.it/index.asp (lists every series/year/round)
  * RMC Euro Trophy        -> https://www.wskarting.it/results/rotax_euro/YYYY/rotax_euro_results_roundN.asp
  * RMC Euro Trophy Winter Cup (Rotax Winter Trophy)
                           -> https://www.wskarting.it/results/rotax_winter/YYYY/rotax_winter_results_roundN.asp
                              + the "current" page https://www.wskarting.it/rotax/rotax_winter_results.asp
    (Rotax rounds are discovered by walking round1, round2, ... until the organiser's page is empty/404.)

Every round index page is a timetable grid (one column pair per class) whose links call
openResult('<file>.pdf?update=N') relative to sResultsPath.  Per class we use
  Participants  -> cheap check whether Robin entered at all
  Booklet       -> all official classifications of the weekend (quali, heats, prefinal, final, verified list)
  Final         -> fallback when the booklet is not (yet) published
A result row is only produced when Robin is found in the official FINAL classification, or explicitly
listed there as DNS/DNF/DSQ, or when the official final-phase ranking puts him outside the final grid
(DNQ) - never guessed.
"""
from __future__ import annotations

import re
import sys
import traceback
import urllib.parse
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import (Context, SourceResult, fetch, fold, guess_track, mentions_driver,  # noqa: E402
                    result_row)

BASE = 'https://www.wskarting.it/'
HOME = BASE + 'index.asp'
WINTER_CURRENT = BASE + 'rotax/rotax_winter_results.asp'
ROTAX_DIRS = {'rotax_euro': 'RMC Euro Trophy', 'rotax_winter': 'RMC Euro Trophy Winter Cup'}

CANONICAL = ['WSK Super Master Series', 'WSK Champions Cup', 'WSK Euro Series', 'WSK Open Cup',
             'WSK Final Cup', 'WSK Super Cup', 'RMC Euro Trophy']
S_NAMES = {  # results.asp ?s= parameter (lower case) -> series name (fallback when the page has no <h1>)
    'wsk_supermasterseries': 'WSK Super Master Series', 'wsk_masterseries': 'WSK Master Series',
    'wsk_championscup': 'WSK Champions Cup', 'wsk_euroseries': 'WSK Euro Series',
    'wsk_opencup': 'WSK Open Cup', 'wsk_openseries': 'WSK Open Series', 'wsk_finalcup': 'WSK Final Cup',
    'wsk_supercup': 'WSK Super Cup',
}
SKIP_S = {'wsk_test'}  # official collective tests: no classification worth a result row

# classes Robin (born 2015) will not race for years -> never downloaded
EXCLUDE_CLASS = re.compile(r'KZ|SENIOR|\bSEN\b|DD2|MASTER|E20|SHIFTER|^OK$|^OK-?N$|^X30 SENIOR')

ISO3_TO_IOC = {'DEU': 'GER', 'CHE': 'SUI', 'NLD': 'NED', 'PRT': 'POR', 'DNK': 'DEN', 'HRV': 'CRO',
               'GRC': 'GRE', 'ARE': 'UAE', 'BHR': 'BRN', 'SVN': 'SLO', 'LVA': 'LAT', 'ZAF': 'RSA'}

MONTHS = {m: i for i, m in enumerate(['JANUARY', 'FEBRUARY', 'MARCH', 'APRIL', 'MAY', 'JUNE', 'JULY', 'AUGUST',
                                       'SEPTEMBER', 'OCTOBER', 'NOVEMBER', 'DECEMBER'], 1)}

BOOKLET_GRACE_DAYS = 14   # keep looking for a missing booklet this long after the event
GIVE_UP_DAYS = 30         # stop re-checking an incomplete round this long after the event

_requests = {'n': 0}


def _get(url: str, binary: bool = False):
    _requests['n'] += 1
    return fetch(url, binary=binary)


def request_count() -> int:
    return _requests['n']


# ---------------------------------------------------------------- small helpers
def _slug(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', fold(s).lower()).strip('-')


def _clean_url(u: str) -> str:
    return u.split('?', 1)[0]


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _parse_iso(s) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _canonical_series(name: str) -> str:
    f = fold(name)
    for c in CANONICAL:
        if fold(c) == f:
            return c
    return name.strip()


def _class_display(name: str) -> str:
    n = re.sub(r'\s+', ' ', name).strip()
    f = fold(n)
    m = re.match(r'^ROTAX\s+(MICRO|MINI|JUNIOR)(\s+MAX)?$', f)
    if m:
        return 'Rotax %s MAX' % m.group(1).title()
    if f in ('OK JUNIOR', 'OKJ', 'OK-JUNIOR'):
        return 'OK-Junior'
    if f in ('OK-N JUNIOR', 'OKN JUNIOR', 'OKN-JUNIOR', 'OK-NJ'):
        return 'OK-N Junior'
    return n


# ---------------------------------------------------------------- round index page (HTML grid)
class _GridParser(HTMLParser):
    """Collects rows/cells of <table class="results_table"> including rowspan/colspan and openResult links."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[dict]]] = []
        self._depth = 0
        self._in_rt = None      # depth of the current results_table
        self._row = None
        self._cell = None
        self._link = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'table':
            self._depth += 1
            if self._in_rt is None and 'results_table' in (a.get('class') or ''):
                self._in_rt = self._depth
                self.tables.append([])
            return
        if self._in_rt is None or self._depth != self._in_rt:
            return
        if tag == 'tr':
            self._row = []
            self.tables[-1].append(self._row)
        elif tag in ('td', 'th') and self._row is not None:
            def _int(v):
                try:
                    return max(1, int(v))
                except Exception:
                    return 1
            self._cell = {'cls': a.get('class') or '', 'colspan': _int(a.get('colspan', 1)),
                          'rowspan': _int(a.get('rowspan', 1)), 'text': '', 'links': []}
            self._row.append(self._cell)
        elif tag == 'a' and self._cell is not None:
            m = re.search(r"openResult\('([^']+)'\)", a.get('onclick') or '')
            if m:
                self._link = {'file': m.group(1), 'label': ''}
                self._cell['links'].append(self._link)

    def handle_endtag(self, tag):
        if tag == 'table':
            if self._in_rt is not None and self._depth == self._in_rt:
                self._in_rt = None
                self._row = self._cell = self._link = None
            self._depth -= 1
        elif tag == 'a':
            self._link = None
        elif tag in ('td', 'th'):
            self._cell = None
            self._link = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell['text'] += data
        if self._link is not None:
            self._link['label'] += data


def parse_round_page(html: str, page_url: str) -> dict:
    """-> {'series': str|None, 'venue': str|None, 'end': date|None, 'base': url, 'classes': {name: [(label, url)]}}"""
    out = {'series': None, 'venue': None, 'end': None, 'base': None, 'classes': {}}
    m = re.search(r'<h1[^>]*>\s*<span[^>]*>([^<]+)</span>\s*RESULTS', html, re.I)
    if m:
        out['series'] = m.group(1).strip()
    m = re.search(r'alt="Series Logo"[^>]*>(.*?)TIME TABLE', html, re.S | re.I)
    if m:
        parts = [re.sub(r'\s+', ' ', p).strip() for p in re.split(r'<[^>]+>', m.group(1))]
        parts = [p for p in parts if p and p != '-']
        dates = [p for p in parts if re.search(r'\b(19|20)\d\d\b', p)]
        venue = [p for p in parts if not re.search(r'\b(19|20)\d\d\b', p)]
        if venue:
            out['venue'] = venue[0]
        if dates:
            ds = re.findall(r'([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})', dates[0])
            if ds:
                mon, d, y = ds[-1]
                if fold(mon) in MONTHS:
                    out['end'] = date(int(y), MONTHS[fold(mon)], int(d))
    paths = [p for p in re.findall(r"sResultsPath\s*=\s*'([^']*)'", html) if p]
    base = urllib.parse.urljoin(page_url, paths[-1]) if paths else page_url
    out['base'] = base

    gp = _GridParser()
    try:
        gp.feed(html)
    except Exception:
        pass
    for rows in gp.tables:
        occupied: set[tuple[int, int]] = set()
        headers: list[tuple[int, int, str]] = []
        for r, row in enumerate(rows):
            c = 0
            placed = []
            for cell in row:
                while (r, c) in occupied:
                    c += 1
                for dr in range(cell['rowspan']):
                    for dc in range(cell['colspan']):
                        occupied.add((r + dr, c + dc))
                placed.append((c, cell))
                c += cell['colspan']
            if any('category_title' in cell['cls'] for _, cell in placed):
                headers = [(c0, c0 + cell['colspan'], re.sub(r'\s+', ' ', cell['text']).strip())
                           for c0, cell in placed if 'category_title' in cell['cls']]
                continue
            for c0, cell in placed:
                for ln in cell['links']:
                    cls = next((h[2] for h in headers if h[0] <= c0 < h[1]), None)
                    if not cls:
                        continue
                    url = urllib.parse.urljoin(base, ln['file'])
                    out['classes'].setdefault(cls, []).append((re.sub(r'\s+', ' ', ln['label']).strip(), url))
    return out


def classify_links(links: list[tuple[str, str]]) -> dict:
    """Pick participants / booklet / final documents of one class column."""
    res = {'participants': None, 'booklet': None, 'final': None, 'finals': []}
    for label, url in links:
        f = fold(label).strip()
        if f.startswith('PARTICIPANT') or f.startswith('ENTRY LIST'):
            res['participants'] = url
        elif 'BOOKLET' in f:
            res['booklet'] = url
        elif re.match(r'^(FINAL|FINALE)\b', f) and not re.search(r'PENAL|PHENAL|PHASE|RANKING', f):
            res['finals'].append((label, url))
    if len(res['finals']) == 1:
        res['final'] = res['finals'][0][1]
    return res


# ---------------------------------------------------------------- PDF documents
def pdf_pages(data: bytes) -> list[str]:
    from pypdf import PdfReader
    import io
    reader = PdfReader(io.BytesIO(data))
    out = []
    for page in reader.pages:
        try:
            out.append(page.extract_text(extraction_mode='layout') or '')
        except Exception:
            try:
                out.append(page.extract_text() or '')
            except Exception:
                out.append('')
    return out


HEADER_RE = re.compile(r'Document\s*(\d+(?:\.\d+)?)\s*(OFFICIAL)?\s*(.*)$')
RANKED_RE = re.compile(r'^\s*(\d{1,3})\s+(?:\d{1,3}\s+)?(\d{1,4})\s{2,}\S')
NC_STATUS_RE = re.compile(r'\b(DNS|DNF|DSQ|DQ|NC|RETIRED|DISQUALIFIED|EXCLUDED|EXC|NP)\b')
STATUS_MAP = {'DNS': 'DNS', 'NP': 'DNS', 'DNF': 'DNF', 'RETIRED': 'DNF', 'NC': 'DNF', 'DSQ': 'DSQ', 'DQ': 'DSQ',
              'DISQUALIFIED': 'DSQ', 'EXCLUDED': 'DSQ', 'EXC': 'DSQ'}


def _session_kind(name: str) -> tuple[str, str | None]:
    """-> (kind, group) kind in final|prefinal|heat|quali|quali_group|ranking|other"""
    f = fold(name).strip()
    f = re.sub(r'\s*\([A-Z0-9 ]*\)\s*$', '', f)  # drop "(PFB)" style suffix
    if re.search(r'PENAL|PHENAL|RANKING|AFTER|STANDING', f) or re.match(r'^ELIMINATORY (HEATS|MANCHES)$', f):
        return 'ranking', None
    if re.match(r'^(FINAL|FINALE)$', f):
        return 'final', None
    if re.match(r'^(FINAL|FINALE)\b', f):
        return 'final_other', None
    m = re.match(r'^PRE-?FINALE?(?:\s+([A-Z]))?$', f)
    if m:
        return 'prefinal', m.group(1)
    if re.match(r'^PRE-?FINAL', f):
        return 'prefinal', None
    if re.match(r'^QUALIFYING( PRACTICE)?$', f):
        return 'quali', None
    if re.match(r'^QUALIFYING', f):
        return 'quali_group', None
    if re.match(r'^(ELIMINATORY HEATS?|HEAT|HEATS|MANCHE|SUPER HEAT|QUALIFYING HEAT|RACE)\b', f):
        return 'heat', None
    return 'other', None


def parse_booklet(pages: list[str]) -> dict:
    """Split a WSK/Apex booklet into official result sessions.

    -> {'sessions': [{'doc', 'name', 'kind', 'group', 'lines'}], 'entries': int|None, 'footer': str|None}
    """
    sessions: list[dict] = []
    entries = None
    footer = None
    for text in pages:
        lines = text.splitlines()
        nonempty = [ln for ln in lines if ln.strip()]
        if not nonempty:
            continue
        if footer is None:
            for ln in nonempty:
                if re.search(r'Page\s+\d+\s*/\s*\d+', ln) and re.search(r'\d{4}', ln):
                    footer = re.sub(r'\s*Page\s+\d+\s*/\s*\d+.*$', '', ln).strip()
                    break
        m_ent = re.search(r'VERIFIED LIST\s*\((\d+)\s*Drivers?\)', text, re.I)
        if m_ent:
            entries = int(m_ent.group(1))
        m = HEADER_RE.search(nonempty[0])
        if not m or not m.group(2):
            continue
        if len(nonempty) < 2 or fold(nonempty[1]).strip() != 'RESULTS':
            continue
        doc = float(m.group(1))
        name = m.group(3).strip()
        kind, group = _session_kind(name)
        if sessions and sessions[-1]['doc'] == doc and sessions[-1]['name'] == name:
            sessions[-1]['lines'].extend(lines)       # continuation page
        else:
            sessions.append({'doc': doc, 'name': name, 'kind': kind, 'group': group, 'lines': list(lines)})
    sessions.sort(key=lambda s: s['doc'])
    return {'sessions': sessions, 'entries': entries, 'footer': footer}


def _is_robin(line: str) -> bool:
    f = fold(line)
    return re.search(r'RAIKKONEN\s+ROBIN|ROBIN\s+RAIKKONEN', f) is not None


def classification(lines: list[str]) -> dict:
    """Parse one official classification.

    -> {'ranked': int, 'nc': [(status)], 'robin': ('pos', n) | ('status', 'DNS') | None, 'robin_no': str|None}
    """
    ranked = []
    nc = []
    robin = None
    robin_no = None
    in_nc = False
    for ln in lines:
        f = fold(ln)
        if re.match(r'^\s*NOT CLASSIFIED', f):
            in_nc = True
            continue
        if re.match(r'^\s*NO\.\s*\d', f) or 'LEADERS' in f or 'BEST LAP' in f or 'RECORD' in f:
            continue
        m = RANKED_RE.match(ln)
        if m and not in_nc:
            ranked.append(int(m.group(1)))
            if _is_robin(ln):
                robin = ('pos', int(m.group(1)))
                robin_no = m.group(2)
            continue
        if in_nc:
            m2 = re.match(r'^\s*(\d{1,4})\s{2,}\S', ln)
            st = NC_STATUS_RE.search(f)
            if m2 and st:
                status = STATUS_MAP.get(st.group(1), st.group(1))
                nc.append(status)
                if _is_robin(ln):
                    robin = ('status', status)
                    robin_no = m2.group(1)
    # sanity: ranks must be 1..n without gaps, otherwise the layout was not understood
    ok = ranked == list(range(1, len(ranked) + 1))
    return {'ranked': len(ranked), 'nc': nc, 'robin': robin, 'robin_no': robin_no, 'ok': ok}


def _fmt(res) -> str | None:
    if not res:
        return None
    return str(res[1])


def analyse_booklet(pages: list[str]) -> dict:
    """Robin's weekend from a class booklet.  -> dict with final/finalPos/... or {'skip': reason}"""
    bk = parse_booklet(pages)
    sessions = bk['sessions']
    info = {'entries': bk['entries'], 'footer': bk['footer']}
    if not any(_is_robin(ln) for p in pages for ln in p.splitlines()):
        return {**info, 'absent': True}
    finals = [s for s in sessions if s['kind'] == 'final']
    others = [s for s in sessions if s['kind'] == 'final_other']
    if not finals:
        return {**info, 'skip': 'no official final classification in booklet' + (' (only %s)' % ', '.join(s['name'] for s in others) if others else '')}
    if len(finals) > 1 or others:
        return {**info, 'skip': 'several final classifications (%s) - not handled' % ', '.join(s['name'] for s in finals + others)}
    fin = finals[0]
    fc = classification(fin['lines'])
    if not fc['ok'] or fc['ranked'] == 0:
        return {**info, 'skip': 'final classification layout not understood'}

    # qualifying (merged classification if there is one)
    quali = None
    for s in sessions:
        if s['kind'] == 'quali' and s['doc'] < fin['doc']:
            c = classification(s['lines'])
            if c['robin']:
                quali = _fmt(c['robin'])
    # heats in chronological (document) order
    heats = []
    for s in sessions:
        if s['kind'] == 'heat' and s['doc'] < fin['doc']:
            c = classification(s['lines'])
            if c['robin']:
                heats.append(_fmt(c['robin']))
    # prefinal(s)
    prefinal = None
    pf_status = None
    for s in sessions:
        if s['kind'] == 'prefinal' and s['doc'] < fin['doc']:
            c = classification(s['lines'])
            if c['robin']:
                prefinal = _fmt(c['robin']) + (' (%s)' % s['group'] if s['group'] else '')
                pf_status = c['robin']
    out = {**info, 'quali': quali, 'heats': '-'.join(heats) if heats else None, 'prefinal': prefinal,
           'fieldSize': fc['ranked'] + len([x for x in fc['nc'] if x != 'DNS']), 'number': fc['robin_no']}

    if fc['robin']:
        if fc['robin'][0] == 'pos':
            out.update(final=str(fc['robin'][1]), finalPos=fc['robin'][1])
        else:
            out.update(final=fc['robin'][1], finalPos=None)
        fl = any(re.search(r'BEST LAP\s*:\s*NO\.\s*\d+\s+RAIKKONEN\s+ROBIN', fold(ln)) for ln in fin['lines'])
        out['fastestLap'] = fl
        return out

    # Robin raced this weekend but is not in the final classification
    last_race = pf_status
    if last_race is None:
        for s in sessions:
            if s['kind'] == 'heat' and s['doc'] < fin['doc']:
                c = classification(s['lines'])
                if c['robin']:
                    last_race = c['robin']
    if last_race is None:
        return {**info, 'skip': 'Robin listed in booklet but in no race classification'}
    if last_race == ('status', 'DNS'):
        out.update(final='DNS', finalPos=None)
        return out
    grid = fc['ranked'] + len(fc['nc'])
    ranking = [s for s in sessions if s['kind'] == 'ranking' and s['doc'] < fin['doc']]
    if ranking:
        c = classification(ranking[-1]['lines'])
        if c['robin'] and c['robin'][0] == 'pos' and c['robin'][1] > grid:
            out.update(final='DNQ', finalPos=None)
            return out
    return {**info, 'skip': 'Robin not in the final classification and DNQ/DNS could not be proven'}


def analyse_final_doc(pages: list[str]) -> dict:
    """Fallback when only the per-class Final document exists."""
    bk = parse_booklet(pages)
    fins = [s for s in bk['sessions'] if s['kind'] == 'final']
    info = {'entries': None, 'footer': bk['footer']}
    if len(fins) != 1:
        return {**info, 'skip': 'final document not understood'}
    fc = classification(fins[0]['lines'])
    if not fc['ok'] or fc['ranked'] == 0:
        return {**info, 'skip': 'final classification layout not understood'}
    if not fc['robin']:
        return {**info, 'absent_final': True}
    out = {**info, 'fieldSize': fc['ranked'] + len([x for x in fc['nc'] if x != 'DNS']), 'number': fc['robin_no']}
    if fc['robin'][0] == 'pos':
        out.update(final=str(fc['robin'][1]), finalPos=fc['robin'][1])
    else:
        out.update(final=fc['robin'][1], finalPos=None)
    out['fastestLap'] = any(re.search(r'BEST LAP\s*:\s*NO\.\s*\d+\s+RAIKKONEN\s+ROBIN', fold(ln)) for ln in fins[0]['lines'])
    return out


def parse_footer(footer: str | None) -> dict:
    """'WSK Super Master Series Rnd4 - Franciacorta (ITA)  06-09/03/2025' -> venue, country, end date."""
    out = {'venue': None, 'country': None, 'end': None}
    if not footer:
        return out
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})\s*$', footer.strip())
    if m:
        try:
            out['end'] = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    m = re.search(r'\(([A-Z]{3})\)', footer)
    if m:
        out['country'] = ISO3_TO_IOC.get(m.group(1), m.group(1))
    m = re.search(r'-\s*([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .\'’]+?)\s*\([A-Z]{3}\)', footer)
    if m and not re.search(r'\bWSK\b|PRESENTED', fold(m.group(1))):
        out['venue'] = m.group(1).strip()
    return out


# ---------------------------------------------------------------- venue helpers
def _track(ctx: Context, *texts: str | None) -> tuple[str | None, str | None, str | None]:
    joined = ' '.join(t for t in texts if t)
    tid, country = guess_track(joined, ctx.site) if joined else (None, None)
    if tid:
        t = next((t for t in ctx.site.get('tracks', []) if t.get('id') == tid), {})
        name, short = t.get('name') or '', t.get('short') or ''
        disp = name if not short or fold(short) in fold(name) else '%s, %s' % (name, short)
        return disp or None, tid, country or t.get('country')
    venue = next((t for t in texts if t), None)
    if venue:
        venue = re.sub(r'^(Circuito|Circuit)\s+', '', venue).strip()
    return venue, None, None


_eurotrophy_cache: dict = {}


def eurotrophy_venue(year: int, rnd: int) -> str | None:
    """Venue of an RMC Euro Trophy round from the series site's race menu ('RMCET #2 Wackersdorf')."""
    if 'txt' not in _eurotrophy_cache:
        h = _get('https://www.rotaxmaxchallenge-eurotrophy.com/')
        _eurotrophy_cache['txt'] = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', h)) if h else ''
    txt = _eurotrophy_cache['txt']
    if not txt or not re.search(r'\b%d\b' % year, txt) or not re.search(r'%d Race Calendar|Euro Trophy %d' % (year, year), txt):
        return None
    m = re.search(r'RMCET #%d ([A-Z][A-Za-z\-]+(?: [A-Z][A-Za-z\-]+)?)' % rnd, txt)
    return m.group(1) if m else None


# ---------------------------------------------------------------- per round
def process_round(ctx: Context, res: SourceResult, key: str, url: str, meta: dict, html: str | None = None) -> None:
    st = ctx.state.setdefault('wsk', {})
    rounds = st.setdefault('rounds', {})
    info = rounds.setdefault(key, {})
    today = ctx.today
    end_known = _parse_iso(info.get('end'))
    if info.get('complete'):
        return
    if end_known and end_known > today:
        return  # not raced yet; the date is known from an earlier visit
    if html is None:
        html = _get(url)
    if not html:
        info['fails'] = info.get('fails', 0) + 1
        res.log.append(f'wsk: could not fetch {url} (failure {info["fails"]})')
        if info['fails'] >= 3 and meta.get('year', today.year) < today.year:
            info['complete'] = True   # broken page of a past season (e.g. HTTP 500): stop retrying
            res.log.append(f'wsk: {key} given up after 3 failed fetches')
        return
    page = parse_round_page(html, url)
    series = _canonical_series(page['series']) if page['series'] else meta['series']
    if meta.get('force_series'):
        series = meta['series']
    end = page['end'] or end_known
    if end:
        info['end'] = end.isoformat()
    info['url'] = url
    if not page['classes']:
        info['checked'] = today.isoformat()
        if end and (today - end).days > GIVE_UP_DAYS:
            info['complete'] = True   # past round that never got documents (cancelled / moved)
        return  # empty timetable (future round)
    if end and end > today:
        return
    if end and ctx.since and end < ctx.since:
        info['complete'] = True
        info['note'] = 'before since'
        return

    cls_state = info.setdefault('classes', {})
    all_done = True
    for cls, links in page['classes'].items():
        if EXCLUDE_CLASS.search(fold(cls)):
            continue
        docs = classify_links(links)
        if not docs['booklet'] and not docs['finals']:
            continue  # combined quali/heat column or class without a final
        cst = cls_state.setdefault(cls, {})
        if cst.get('status') in ('absent', 'done'):
            continue
        try:
            _process_class(ctx, res, key, url, meta, series, page, cls, docs, cst, end)
        except Exception as e:  # never let one document kill the run
            res.log.append(f'wsk: {key} {cls}: error {e!r}')
            cst['error'] = repr(e)[:200]
        if cst.get('status') not in ('absent', 'done'):
            all_done = False
    if all_done:
        info['complete'] = True
    elif end and (today - end).days > GIVE_UP_DAYS:
        info['complete'] = True
        res.log.append(f'wsk: {key} giving up on incomplete classes after {GIVE_UP_DAYS} days')


def _process_class(ctx, res, key, url, meta, series, page, cls, docs, cst, end):
    # 1. participants list (small) - skip the class when Robin did not enter
    if docs['participants'] and cst.get('status') not in ('entered', 'final-only'):
        pu = docs['participants']
        data = _get(pu, binary=True)
        if data is None:
            res.log.append(f'wsk: {key} {cls}: participants list unavailable')
        else:
            txt = '\n'.join(pdf_pages(data)) if data[:4] == b'%PDF' else ''
            ctx.mark(_clean_url(pu), 'wsk participants')
            if txt and not mentions_driver(txt):
                cst['status'] = 'absent'
                return
            if txt:
                cst['status'] = 'entered'
    # 2. booklet (all official classifications)
    if docs['booklet']:
        bu = docs['booklet']
        data = _get(bu, binary=True)
        if data and data[:4] == b'%PDF':
            ctx.mark(_clean_url(bu), 'wsk booklet')
            a = analyse_booklet(pdf_pages(data))
            if a.get('absent'):
                cst['status'] = 'absent'
                return
            if a.get('skip'):
                res.log.append(f'wsk: {key} {cls}: SKIPPED - {a["skip"]} ({_clean_url(bu)})')
                cst['status'] = 'done'
                cst['skip'] = a['skip']
                return
            _emit(ctx, res, key, url, meta, series, page, cls, a, [_clean_url(bu)], end)
            cst['status'] = 'done'
            return
        res.log.append(f'wsk: {key} {cls}: booklet listed but not downloadable ({_clean_url(bu)})')
    # 3. fallback: final document only
    if docs['final']:
        fu = docs['final']
        if cst.get('final_seen') == fu:     # same document version already analysed
            pass
        else:
            data = _get(fu, binary=True)
            if data and data[:4] == b'%PDF':
                ctx.mark(_clean_url(fu), 'wsk final')
                cst['final_seen'] = fu
                a = analyse_final_doc(pdf_pages(data))
                if a.get('skip'):
                    res.log.append(f'wsk: {key} {cls}: final doc - {a["skip"]}')
                elif not a.get('absent_final'):
                    _emit(ctx, res, key, url, meta, series, page, cls, a, [_clean_url(fu)], end)
                    cst['status'] = 'final-only'
    elif docs['finals']:
        res.log.append(f'wsk: {key} {cls}: several finals ({", ".join(l for l, _ in docs["finals"])}) - skipped')
        cst['status'] = 'done'
        return
    # without booklet we cannot prove DNQ/DNS; wait a little for the booklet, then give up
    if end and (ctx.today - end).days > BOOKLET_GRACE_DAYS:
        if cst.get('status') != 'final-only':
            res.log.append(f'wsk: {key} {cls}: no booklet after {BOOKLET_GRACE_DAYS} days and Robin not in final doc')
        cst['status'] = 'done'


def _emit(ctx, res, key, url, meta, series, page, cls, a, sources, end_page):
    ft = parse_footer(a.get('footer'))
    end = end_page or ft['end']
    if not end:
        res.log.append(f'wsk: {key} {cls}: no event date found - skipped')
        return
    if ctx.since and end < ctx.since:
        return
    venue = page['venue'] or ft['venue']
    if not venue and meta.get('rotax') == 'rotax_euro':
        venue = eurotrophy_venue(meta['year'], meta['round'])
    track, tid, country = _track(ctx, page['venue'], ft['venue'], venue)
    country = country or ft['country']
    klass = _class_display(cls)
    rnd = meta.get('round_label')
    row = result_row(
        id='auto-%s-%s-%s' % (_slug(series), end.isoformat(), _slug(klass)),
        date=end.isoformat(), series=series, round=rnd, track=track, trackId=tid, country=country,
        **{'class': klass},
        quali=a.get('quali'), heats=a.get('heats'), prefinal=a.get('prefinal'),
        final=a['final'], finalPos=a.get('finalPos'), fieldSize=a.get('fieldSize'), entries=a.get('entries'),
        number=a.get('number'),
        note={'fi': 'Finaalin nopein kierros.', 'sv': 'Snabbaste varvet i finalen.',
              'en': 'Fastest lap in the final.'} if a.get('fastestLap') else None,
        sources=sources + [url], confidence='high',
    )
    if row.get('finalPos') is None:
        row['finalPos'] = None
    st = ctx.state.setdefault('wsk', {}).setdefault('rows', {})
    st[row['id']] = row
    res.log.append(f'wsk: {row["date"]} {series} {rnd or ""} {klass}: final {row["final"]}')


# ---------------------------------------------------------------- discovery
def _years(ctx: Context) -> list[int]:
    cur = ctx.today.year
    start = cur - 1
    if ctx.since:
        start = max(min(ctx.since.year, start), cur - 6)
    return list(range(start, cur + 1))


def discover_wsk(ctx: Context, res: SourceResult, years: list[int]) -> list[tuple[str, str, dict]]:
    html = _get(HOME)
    if not html:
        res.log.append('wsk: homepage unavailable - WSK series not checked')
        return []
    found = {}
    for yy, r, c, s in re.findall(r'results\.asp\?yy=(\d{4})&(?:amp;)?r=(\d+)&(?:amp;)?c=(\d+)&(?:amp;)?s=([A-Za-z0-9_]+)', html):
        yy, r = int(yy), int(r)
        sl = s.lower()
        if yy not in years + [ctx.today.year + 1] or sl in SKIP_S:
            continue
        k = (yy, sl, r)
        if k not in found:
            found[k] = 'https://www.wskarting.it/results.asp?yy=%d&r=%d&c=%s&s=%s' % (yy, r, c, s)
    counts = {}
    for (yy, sl, r) in found:
        counts[(yy, sl)] = max(counts.get((yy, sl), 0), r)
    out = []
    for (yy, sl, r), url in sorted(found.items()):
        meta = {'series': S_NAMES.get(sl, sl), 'year': yy, 'round': r,
                'round_label': ('R%d' % r) if counts[(yy, sl)] > 1 else None}
        out.append(('%d:%s:%d' % (yy, sl, r), url, meta))
    return out


def run_rotax(ctx: Context, res: SourceResult, years: list[int]) -> None:
    st = ctx.state.setdefault('wsk', {})
    rounds = st.setdefault('rounds', {})
    done_years = st.setdefault('years_done', [])
    for d, name in ROTAX_DIRS.items():
        for yy in years:
            if '%s:%d' % (d, yy) in done_years:
                continue
            n = 1
            all_complete = True
            while n <= 12:
                key = '%s:%d:%d' % (d, yy, n)
                meta = {'series': name, 'force_series': True, 'year': yy, 'round': n, 'round_label': 'R%d' % n,
                        'rotax': d}
                if rounds.get(key, {}).get('complete'):
                    n += 1
                    continue
                end = _parse_iso(rounds.get(key, {}).get('end'))
                url = '%sresults/%s/%d/%s_results_round%d.asp' % (BASE, d, yy, d, n)
                if end and end > ctx.today:
                    all_complete = False
                    n += 1
                    continue
                html = _get(url)
                if not html or 'openResult' not in html:
                    break
                process_round(ctx, res, key, url, meta, html)
                if not rounds.get(key, {}).get('complete'):
                    all_complete = False
                n += 1
            if yy < ctx.today.year and all_complete:
                done_years.append('%s:%d' % (d, yy))
    # the current Winter Cup page lives outside the per-year folders
    html = _get(WINTER_CURRENT)
    if html and 'openResult' in html:
        m = re.findall(r"sResultsPath\s*=\s*'[^']*results/(rotax_\w+)/(\d{4})/Round(\d+)/'", html)
        if m:
            d, yy, n = m[-1][0], int(m[-1][1]), int(m[-1][2])
            if yy in years and d in ROTAX_DIRS:
                key = '%s:%d:%d' % (d, yy, n)
                meta = {'series': ROTAX_DIRS[d], 'force_series': True, 'year': yy, 'round': n,
                        'round_label': 'R%d' % n, 'rotax': d}
                process_round(ctx, res, key, WINTER_CURRENT, meta, html)


# ---------------------------------------------------------------- entry point
def run(ctx: Context) -> SourceResult:
    res = SourceResult()
    start_n = _requests['n']
    try:
        years = _years(ctx)
        st = ctx.state.setdefault('wsk', {})
        st.setdefault('rounds', {})
        st.setdefault('rows', {})
        for key, url, meta in discover_wsk(ctx, res, years):
            try:
                process_round(ctx, res, key, url, meta)
            except Exception as e:
                res.log.append(f'wsk: {key}: error {e!r}')
        try:
            run_rotax(ctx, res, years)
        except Exception as e:
            res.log.append(f'wsk: rotax: error {e!r}')
        rows = []
        for row in st['rows'].values():
            d = _parse_iso(row.get('date'))
            if ctx.since and d and d < ctx.since:
                continue
            rows.append(dict(row))
        rows.sort(key=lambda r: r.get('date', ''), reverse=True)
        res.results = rows
    except Exception as e:
        res.log.append('wsk: fatal %r\n%s' % (e, traceback.format_exc(limit=3)))
    res.log.append('wsk: %d HTTP requests' % (_requests['n'] - start_n))
    return res


if __name__ == '__main__':
    import json
    from common import load_site_data
    since = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
    c = Context(site=load_site_data(), auto={}, state={}, since=since)
    r = run(c)
    print('\n'.join(r.log))
    print(json.dumps(r.results, ensure_ascii=False, indent=1))
