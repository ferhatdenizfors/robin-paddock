"""Rotax Max Challenge Italia (www.rotaxmaxkart.it) - official LSTiming / Apex GoRacing booklets.

Discovery (no per-round URLs are hard-coded)
    1. The WordPress page list (wp-json search "risultati") names one page per season
       ("Risultati 2026" -> /risultati-2026/). Current and previous season are read; a season
       without a page (there is no "Risultati 2025") is simply logged. Fallback: /risultati-YYYY/.
    2. A season page has one text heading per round ("Cremona 8/3/2026", "7-Laghi Kart 30/08/2026")
       followed by one PDF link per class ("Micro / Mini / Junior ...") or one "Tutti risultati"
       PDF, and a "Campionato Nazionale" block with <class>_championship.html tables.
    3. Rounds are matched to the organiser's season calendar PDF (linked from /calendario/,
       cached in state for a week) to get the round number (R1..R8) and the season length.
    4. Per round, documents are tried in this order: Robin's current class (Mini, or the class he
       was last found in), then "all results" / unlabelled documents, then the other youth
       classes (Junior, Mini). Adult / Micro documents are never downloaded.
    5. Rounds that are over but not yet on the results page (the results page lags weeks behind)
       are found through the "Info Point" posts linked in the site sidebar: the post embeds a
       karting.ch event page -> youcrono.com "Risultati" page -> apex-timing.com GoRacing results,
       whose per-class "Booklet" PDF is the same official document the organiser uploads later.

State
    Every parsed PDF is ctx.mark()ed ("robin:<final>", "no-robin"). A round in which Robin's final
    was found is marked "rmci:<year>:R<n>" and never looked at again. A PDF whose content belongs
    to a different class than its link says (2026 R6: the "Mini" link serves the Micro booklet)
    is marked "mismatch:<date>" and re-checked weekly for 90 days in case the organiser fixes it.

Parsing
    Official sections only ("Document 9.1 OFFICIAL Finale (R1)", "Documento 7.1 UFFICIALE ...",
    or VEMASOFT "Risultati Definitivi ... FINAL"), class taken from the section's own class line.
    A row is emitted only when Robin is in the official FINAL classification of his class (or is
    listed there as DNS/DNF/DSQ). Quali / heats / prefinal come from the official sections of the
    same document. Anything ambiguous is skipped and logged.

Battle
    From the latest season's <class>_championship.html (net points = "Net" column after the
    organiser's drop-results rule), only when Robin is in the table.
"""
from __future__ import annotations

import html as _html
import re
import sys
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

try:
    from common import SourceResult, fetch, fold, guess_track, pdf_text, result_row
except ImportError:  # pragma: no cover - when imported as scripts.sources.rotax_italia
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from common import SourceResult, fetch, fold, guess_track, pdf_text, result_row

SERIES = 'RMC Italia'
SERIES_SLUG = 'rmc-italia'
BASE = 'https://www.rotaxmaxkart.it'
REST_PAGES = BASE + '/wp-json/wp/v2/pages?search=risultati&per_page=50&_fields=link,title'
CALENDAR_PAGE = BASE + '/calendario/'
STATE_KEY = 'rotax_italia'

MAX_DOCS_PER_RUN = 25          # PDF downloads per night (the rest follows the next night)
INFO_POINT_WINDOW = 60         # days after an event during which the timing chain is followed
MISMATCH_RECHECK_DAYS = 7
MISMATCH_GIVE_UP_DAYS = 90

CLASS_NAMES = {
    'MICRO': 'Rotax Micro MAX', 'MINI': 'Rotax Mini MAX', 'JUNIOR': 'Rotax Junior MAX',
    'SENIOR': 'Rotax Senior MAX', 'DD2': 'Rotax DD2', 'DDM': 'Rotax DD2 Master',
}
BATTLE_CLASS = {'MICRO': 'Rotax Micro', 'MINI': 'Rotax Mini', 'JUNIOR': 'Rotax Junior',
                'SENIOR': 'Rotax Senior', 'DD2': 'Rotax DD2', 'DDM': 'Rotax DD2 Master'}
YOUTH = ['MINI', 'JUNIOR']      # classes Robin can plausibly race 2026-2028 (age 11-13)

_requests: list[str] = []


def _get(url: str, *, binary: bool = False):
    _requests.append(url)
    return fetch(url, binary=binary)


# ---------------------------------------------------------------- small helpers
def is_robin(line: str) -> bool:
    c = re.sub(r'\s+', '', fold(line))
    return 'RAIKKONENROBIN' in c or 'ROBINRAIKKONEN' in c


def class_key(text: str) -> str | None:
    """'Rotax Mini' / '...-MIN.pdf' / 'ROTAX MINI MAXROTAX MINI MAX' -> 'MINI' (None if unknown)."""
    t = fold(text)
    if re.search(r'(?<![A-Z0-9])E-?(10|20)(?![0-9])', t):
        return 'ELECTRIC'
    if 'TILLOTSON' in t:
        return 'TILLOTSON'
    for key, pat in (('MICRO', r'MICRO|(?<![A-Z])MIC(?![A-Z])'), ('MINI', r'MINI|(?<![A-Z])MIN(?![A-Z])'),
                     ('JUNIOR', r'JUNIOR|(?<![A-Z])JUN(?![A-Z])'),
                     ('DDM', r'DD2[\s_-]*MASTER|(?<![A-Z])DDM(?![A-Z])'), ('DD2', r'DD2'),
                     ('SENIOR', r'SENIOR|(?<![A-Z])SEN(?![A-Z])|(?<![A-Z])MAX(?![A-Z])'),
                     ('ALL', r'TUTTI|(?<![A-Z])ALL(?![A-Z])|RISULTATI COMPLETI')):
        if re.search(pat, t):
            return key
    return None


def _strip_tags(s: str) -> str:
    return re.sub(r'\s+', ' ', _html.unescape(re.sub(r'<[^>]+>', ' ', s or ''))).replace('\xa0', ' ').strip()


def _abs(url: str, base: str = BASE) -> str:
    return urllib.parse.urljoin(base + ('/' if not base.endswith('/') else ''), _html.unescape(url.strip()))


def _mstate(ctx) -> dict:
    return ctx.state.setdefault(STATE_KEY, {})


def _seen_note(ctx, key: str) -> str:
    return str((ctx.state.get('seen') or {}).get(key, ''))


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


# ---------------------------------------------------------------- discovery: season pages
def season_pages(ctx, log) -> dict[int, str]:
    years = {ctx.today.year, ctx.today.year - 1}
    pages: dict[int, str] = {}
    data = _get(REST_PAGES)
    if data:
        for m in re.finditer(r'"link"\s*:\s*"([^"]+)"\s*,\s*"title"\s*:\s*\{\s*"rendered"\s*:\s*"([^"]*)"', data):
            link, title = m.group(1).replace('\\/', '/'), _strip_tags(m.group(2))
            tm = re.search(r'\bRisultati\b.*?\b(20\d\d)\b', title, re.I)
            if tm and int(tm.group(1)) in years and '/en/' not in link:
                pages.setdefault(int(tm.group(1)), link)
    else:
        log.append('rotax_italia: WordPress page list not reachable; guessing /risultati-YYYY/')
        for y in years:
            pages[y] = f'{BASE}/risultati-{y}/'
    for y in sorted(years):
        if y not in pages:
            log.append(f'rotax_italia: no "Risultati {y}" page on rotaxmaxkart.it (nothing published for {y})')
    return pages


def _tokens(page_html: str) -> list[tuple]:
    """Ordered ('text', str) / ('link', href, label) tokens of the page's main content."""
    i = page_html.find('entry-content')
    body = page_html[i:] if i >= 0 else page_html
    j = body.find('<footer')
    body = body[:j] if j > 0 else body
    body = re.sub(r'<(script|style)\b[\s\S]*?</\1>', ' ', body, flags=re.I)
    out: list[tuple] = []
    for m in re.finditer(r'<a\s[^>]*?href="([^"]+)"[^>]*>([\s\S]*?)</a>|<[^>]+>|([^<]+)', body, re.I):
        if m.group(1):
            out.append(('link', _abs(m.group(1)), _strip_tags(m.group(2))))
        elif m.group(3):
            for part in re.split(r'\n', _html.unescape(m.group(3))):
                t = part.replace('\xa0', ' ').strip()
                if t:
                    out.append(('text', t))
    return out


HEADING_RE = re.compile(r'^(.*?)\s*(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(20\d\d)\s*$')


def parse_season_page(page_html: str, year: int) -> tuple[list[dict], list[dict], str]:
    """-> events [{venue, date, docs:[{url,label,ckey}]}], championship tables [{url,label,ckey}], note."""
    events: list[dict] = []
    champs: list[dict] = []
    cur = None
    champ_note = ''
    in_champ = False
    for tok in _tokens(page_html):
        if tok[0] == 'text':
            t = tok[1]
            m = HEADING_RE.match(t)
            if m and len(m.group(1)) <= 60:
                d = _safe_date(int(m.group(4)), int(m.group(3)), int(m.group(2)))
                if d and d.year == year:
                    cur = {'venue': m.group(1).strip(' -–:'), 'date': d, 'docs': []}
                    events.append(cur)
                    in_champ = False
                    continue
            if re.search(r'campionato|classifica', t, re.I):
                cur = None
                in_champ = 'nazionale' in t.lower() or in_champ
                continue
            if in_champ and re.search(r'gare|round|scart', t, re.I):
                champ_note = t
            continue
        _, href, label = tok
        low = href.lower()
        if re.search(r'_championship\.html?$', low) or (in_champ and low.endswith(('.html', '.htm', '.pdf'))):
            champs.append({'url': href, 'label': label, 'ckey': class_key(label) or class_key(href.rsplit('/', 1)[-1])})
            continue
        if cur is None or '/wp-content/uploads/' not in low or not low.endswith('.pdf'):
            continue
        if re.search(r'decisioni|decision|campionato|classifica|comunicat', low + ' ' + label.lower()):
            continue
        ck = class_key(label) or class_key(href.rsplit('/', 1)[-1])
        if any(d['url'] == href for d in cur['docs']):
            # the same PDF linked twice under two labels (2026 R6: Junior file also behind "Senior")
            continue
        cur['docs'].append({'url': href, 'label': label, 'ckey': ck})
    return events, champs, champ_note


def info_points(page_html: str) -> list[dict]:
    """Sidebar 'Info Point' posts: [{url, label, date}] (date from the slug when present)."""
    out = {}
    for m in re.finditer(r'href="(https?://(?:www\.)?rotaxmaxkart\.it/[^"]*info-point[^"]*)"[^>]*>([\s\S]*?)</a>', page_html, re.I):
        url = _html.unescape(m.group(1))
        if '/en/' in url:
            continue
        dm = re.search(r'(\d{1,2})-(\d{1,2})-(20\d\d)/?$', url)
        d = _safe_date(int(dm.group(3)), int(dm.group(2)), int(dm.group(1))) if dm else None
        out[url] = {'url': url, 'label': _strip_tags(m.group(2)), 'date': d}
    return list(out.values())


# ---------------------------------------------------------------- discovery: calendar
CAL_ROW = re.compile(r'^\s*(\d{1,2})/(\d{1,2})/(\d{2,4})\s+RMCI\s+(\S.*?)\s{2,}R(\d{1,2})\b')


def season_calendar(ctx, year: int, log) -> list[dict]:
    """[{round:int, date:date, venue:str}] of the RMCI national rounds (cached one week)."""
    st = _mstate(ctx).setdefault('calendar', {})
    cached = st.get(str(year))
    if cached and cached.get('checked', '') >= (ctx.today - timedelta(days=7)).isoformat():
        return [{'round': r[0], 'date': date.fromisoformat(r[1]), 'venue': r[2]} for r in cached.get('rounds', [])]
    page = _get(CALENDAR_PAGE)
    rounds: list[dict] = []
    url = None
    if page:
        for m in re.finditer(r'href="([^"]+\.pdf)"[^>]*>([\s\S]*?)</a>', page, re.I):
            href, label = _html.unescape(m.group(1)), _strip_tags(m.group(2))
            f = fold(label + ' ' + href)
            if str(year) in f and 'CALENDARIO' in fold(href) and not re.search(r'SUD|CENTRO|INTERNAZ|ZONA', f):
                url = _abs(href)
                break
    if url and cached and cached.get('url') == url:
        cached['checked'] = ctx.today.isoformat()
        return [{'round': r[0], 'date': date.fromisoformat(r[1]), 'venue': r[2]} for r in cached.get('rounds', [])]
    if url:
        data = _get(url, binary=True)
        if data:
            try:
                for line in pdf_text(data).splitlines():
                    m = CAL_ROW.match(line)
                    if m:
                        # the season year is authoritative (the 2026 PDF says 13/06/25 for Lonato)
                        d = _safe_date(year, int(m.group(2)), int(m.group(1)))
                        if d:
                            rounds.append({'round': int(m.group(5)), 'date': d, 'venue': m.group(4).strip()})
            except Exception as e:  # noqa: BLE001
                log.append(f'rotax_italia: calendar PDF unreadable ({e})')
    if rounds:
        st[str(year)] = {'url': url, 'checked': ctx.today.isoformat(),
                         'rounds': [[r['round'], r['date'].isoformat(), r['venue']] for r in rounds]}
    else:
        log.append(f'rotax_italia: no {year} RMCI calendar found on {CALENDAR_PAGE}')
        if cached:
            return [{'round': r[0], 'date': date.fromisoformat(r[1]), 'venue': r[2]} for r in cached.get('rounds', [])]
    return rounds


def match_round(cal: list[dict], d: date | None, venue: str, site: dict) -> dict | None:
    if not d or not cal:
        return None
    tid = guess_track(venue.replace('-', ' '), site)[0] if venue else None
    best = None
    for r in cal:
        delta = abs((r['date'] - d).days)
        same_track = tid and guess_track(r['venue'], site)[0] == tid
        if delta <= 2 or (same_track and delta <= 10):
            if best is None or delta < best[0]:
                best = (delta, r)
    return best[1] if best else None


# ---------------------------------------------------------------- PDF parsing
LST_SESSION = re.compile(
    r'(Prefinale|Pre-?final|Finale|Final|Manche\s*(\d)|Heat\s*(\d)|Prove\s+di\s+qualificazione|Qualifiche|'
    r'Qualifying(?:\s+practice)?|Merge\s+manches|Classifica\s+manches|Prove\s+libere(?:\s+ufficiali)?|'
    r'Official\s+practice|Warm[\s-]*up|Starting\s+grid)\s*\((\w{1,4})\)', re.I)
VEMA_SESSION = re.compile(r'^\s*(PREFINAL|PRE-FINAL|FINAL|HEAT\s*-?\s*H?(\d)|QUALIFYING(?:\s+PRACTICE)?|PRACTICE|WARM\s*UP)\b.*\bStart\s+mode', re.I)
PAGE_END = re.compile(r'\b(?:Page|Pagina|pag:)\s*\d+\s*/\s*\d+', re.I)
TIME_RE = re.compile(r'(?<![\d.:])(?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{3}(?![\d])|(?<![\d.:])\d{2}[.,]\d{3}(?![\d])')
STATUS_RE = re.compile(r'(?<![A-Z])(DNS|DNF|DSQ|DQ|DNQ|NC|EXC|SQUALIFICATO|RIT|RITIRATO)(?![A-Z])')
SKIP_ROW = re.compile(r'(?:^|\s)(?:No\.|N°)\s*\d|Leader|Best lap|Migliore|Record|Start Time|Ora Partenza|Inizio|Weather|Meteo|'
                      r'Giro\b|Griglia|Partenza|Intervallo|Penalt|Descrizione|Presidente|Chairman|Timekeeping', re.I)


def _kind(name: str) -> tuple[str, int | None]:
    t = fold(name)
    if t.startswith('PRE'):
        return ('prefinal', None) if 'FINAL' in t else ('other', None)
    m = re.match(r'(?:MANCHE|HEAT)\s*-?\s*H?\s*(\d)', t)
    if m:
        return 'heat', int(m.group(1))
    if t.startswith('FINAL'):
        return 'final', None
    if 'QUALIF' in t and 'MERGE' not in t:
        return 'quali', None
    return 'other', None


def _section_class(lines: list[str], i: int) -> str | None:
    for j in (i, i + 1, i + 2, i + 3, i - 1, i - 2, i - 3):
        if 0 <= j < len(lines):
            ln = lines[j]
            f = fold(ln).strip()
            if 'CHALLENGE' in f:
                continue
            if re.match(r'(ROTAX|TILLOTSON|E-?10|E-?20)\b', f):
                head = re.split(r'DOCUMENT|\s{3,}', f)[0]
                ck = class_key(head)
                if ck and ck != 'ALL':
                    return ck
    return None


def parse_sections(text: str) -> tuple[list[dict], str, int | None]:
    """Split a booklet into sessions [{kind, heat, cls, official, docno, date, lines}], title line, year."""
    lines = text.splitlines()
    secs: list[dict] = []
    cur = None
    year = None
    title = ''
    for i, ln in enumerate(lines):
        ym = re.search(r'\b\d{1,2}(?:-\d{1,2})?/\d{1,2}/(20\d\d)\b', ln)
        if ym and year is None:
            year = int(ym.group(1))
        if not title and re.search(r'RMC|ROTAX MAX CHALLENGE', ln, re.I) and re.search(r'20\d\d', ln):
            title = re.sub(r'\s{2,}', '  ', ln.strip())
        m = LST_SESSION.search(ln)
        vm = None if m else VEMA_SESSION.match(ln)
        if m or vm:
            if m:
                name = m.group(1)
                official = bool(re.search(r'OFFICIAL|UFFICIALE', ln)) and not re.search(
                    r'non\s+ufficial|No official|information|Lap Chart|Contagiri|Analys|Analisi|Grid|Griglia', ln, re.I)
                dm = re.search(r'Document[o]?\s+(\d+(?:\.\d+)?)', ln, re.I)
                docno = float(dm.group(1)) if dm else 0.0
            else:
                name = vm.group(1)
                window = ' '.join(lines[max(0, i - 4):i])
                official = bool(re.search(r'Risultati\s+Definitivi', window, re.I)) and 'EXPORT' not in fold(window)
                docno = 0.0
            kind, heat = _kind(name)
            cur = {'kind': kind, 'heat': heat, 'name': name, 'cls': _section_class(lines, i),
                   'official': official, 'docno': docno, 'date': None, 'lines': [], 'line_no': i}
            secs.append(cur)
            continue
        if cur is None:
            continue
        if PAGE_END.search(ln):
            cur = None
            continue
        cur['lines'].append(ln)
        if cur['date'] is None:
            dm = re.search(r'(?:Start Time|Ora Partenza)\s*:\s*(\d{1,2})/(\d{1,2})', ln) or \
                re.search(r'Inizio:\s*(\d{1,2})/(\d{1,2})/(20\d\d)', ln)
            if dm:
                cur['date'] = (int(dm.group(1)), int(dm.group(2)), int(dm.group(3)) if dm.lastindex >= 3 else None)
    for s in secs:
        if s['date']:
            d, mo, y = s['date']
            s['date'] = _safe_date(y or year or 0, mo, d) if (y or year) else None
    return secs, title, year


def _leading_ints(line: str) -> list[tuple[int, int]]:
    """[(value, column)] of the integers before the first word of a row."""
    out = []
    for m in re.finditer(r'\S+', line):
        tok = m.group(0)
        if re.fullmatch(r'\d{1,3}', tok):
            out.append((int(tok), m.start()))
            if len(out) == 3:
                break
        else:
            break
    return out


def section_rows(sec: dict) -> list[dict]:
    """Classification rows: {line, ints, status, robin}. Rows keep document order."""
    rows = []
    for ln in sec['lines']:
        if not ln.strip() or SKIP_ROW.search(ln):
            continue
        f = fold(ln)
        words = re.findall(r"[A-Z][A-Z'`.-]+", f)
        if len(words) < 2:
            continue
        st = STATUS_RE.search(f)
        lead_status = re.match(r'^\s*(DNS|DNF|DSQ|DQ|DNQ|NC|EXC)\s+\d', f)
        ints = _leading_ints(ln)
        has_time = bool(TIME_RE.search(ln))
        if not ints and not lead_status:
            continue
        if not has_time and not st:
            continue
        status = None
        if lead_status:
            status = lead_status.group(1)
        elif st and not has_time:
            status = st.group(1)
        elif st and re.search(r'\s(DNS|DNF|DSQ|DQ)\s*(\d{1,3})?\s*$', f):
            status = re.search(r'\s(DNS|DNF|DSQ|DQ)\s*(\d{1,3})?\s*$', f).group(1)
        rows.append({'line': ln, 'ints': ints, 'status': status, 'robin': is_robin(ln)})
    return rows


def robin_position(sec: dict, log: list[str], what: str) -> tuple[str | None, int]:
    """-> (position string or status, number of rows) for Robin in a classification section."""
    rows = section_rows(sec)
    n = len(rows)
    hits = [i for i, r in enumerate(rows) if r['robin']]
    if not hits:
        return None, n
    if len(hits) > 1:
        log.append(f'rotax_italia: {what}: Robin listed {len(hits)}x in one classification - skipped')
        return None, n
    r = rows[hits[0]]
    if r['status']:
        s = r['status']
        return {'DQ': 'DSQ', 'SQUALIFICATO': 'DSQ', 'RIT': 'DNF', 'RITIRATO': 'DNF', 'EXC': 'DSQ'}.get(s, s), n
    order = sum(1 for x in rows[:hits[0] + 1] if not x['status'])
    rank_cols = [x['ints'][0][1] for x in rows if len(x['ints']) >= 2 and not x['status']]
    rank_col = max(set(rank_cols), key=rank_cols.count) if rank_cols else None
    ints = r['ints']
    if len(ints) >= 2:
        pos = ints[0][0]
    elif len(ints) == 1 and rank_col is not None and abs(ints[0][1] - rank_col) <= 2:
        pos = ints[0][0]
    elif len(ints) == 1 and rank_col is not None:
        pos = order                                   # rank cell empty in the extraction: use row order
    elif len(ints) == 1:
        pos = ints[0][0] if ints[0][0] == order else None
    else:
        pos = None
    if pos is None or not (1 <= pos <= n):
        log.append(f'rotax_italia: {what}: could not read Robin\'s position from "{r["line"].strip()[:90]}" - skipped')
        return None, n
    if pos != order:
        log.append(f'rotax_italia: {what}: printed rank {pos} but row order {order} (rank kept)')
    return str(pos), n


def best_sections(secs: list[dict]) -> dict[tuple, dict]:
    """Latest official version per (class, kind, heat); continuation pages are merged."""
    out: dict[tuple, dict] = {}
    for s in secs:
        if not s['official'] or not s['cls'] or s['kind'] == 'other':
            continue
        key = (s['cls'], s['kind'], s['heat'])
        prev = out.get(key)
        if prev is None or s['docno'] > prev['docno']:
            out[key] = dict(s, lines=list(s['lines']))
        elif s['docno'] == prev['docno']:
            prev['lines'].extend(s['lines'])       # page 2 of the same document
            prev['date'] = prev['date'] or s['date']
    return out


def parse_booklet(text: str, log: list[str], what: str) -> dict:
    """-> {classes:set, robin_class, final, finalPos, fieldSize, quali, heats, prefinal, date, title, round}."""
    secs, title, year = parse_sections(text)
    best = best_sections(secs)
    res = {'classes': {k[0] for k in best}, 'title': title, 'year': year}
    rm = re.search(r'(?:RND|ROUND|RN|RD)[\s_.^-]*(\d{1,2})(?!\d)|(?<!\d)(\d{1,2})\s*\^?\s*(?:RD|ROUND)\b', fold(title))
    res['round'] = int(rm.group(1) or rm.group(2)) if rm else None
    finals = []
    for (cls, kind, _), sec in best.items():
        if kind == 'final':
            pos, n = robin_position(sec, log, f'{what} {cls} final')
            if pos:
                finals.append((cls, pos, n, sec))
    if not finals:
        in_other = sorted({f'{c} {k}{h or ""}' for (c, k, h), s in best.items() if any(is_robin(x) for x in s['lines'])})
        if in_other:
            log.append(f'rotax_italia: {what}: Robin appears in {", ".join(in_other)} but not in an official final classification - no row')
        return res
    if len(finals) > 1:
        log.append(f'rotax_italia: {what}: Robin in finals of several classes {[f[0] for f in finals]} - skipped')
        return res
    cls, pos, n, fsec = finals[0]
    res.update(robin_class=cls, final=pos, fieldSize=n, date=fsec['date'])
    q = best.get((cls, 'quali', None))
    if q:
        res['quali'] = robin_position(q, log, f'{what} {cls} quali')[0]
    heats = sorted((h, s) for (c, k, h), s in best.items() if c == cls and k == 'heat')
    if heats and [h for h, _ in heats] == list(range(1, len(heats) + 1)):
        hp = [robin_position(s, log, f'{what} {cls} heat {h}')[0] for h, s in heats]
        if all(hp):
            res['heats'] = '-'.join(hp)
    pf = best.get((cls, 'prefinal', None))
    if pf:
        res['prefinal'] = robin_position(pf, log, f'{what} {cls} prefinal')[0]
    return res


# ---------------------------------------------------------------- Apex GoRacing (info-point path)
APEX_RE = re.compile(r'https?://(?:www\.)?apex-timing\.com/goracing/results\.php\?[^"\'\s<>]*path=[^"\'\s<>]+', re.I)


def _all_links(page: str, base: str) -> list[str]:
    return [_abs(u, base) for u in re.findall(r'(?:href|src)="([^"]+)"', page)]


def find_apex(url: str, log: list[str]) -> str | None:
    """Follow info point -> karting.ch -> youcrono -> apex GoRacing (max 3 hops, 5 pages)."""
    frontier, visited = [url], set()
    for _ in range(3):
        nxt = []
        for u in frontier:
            if u in visited or len(visited) >= 5:
                continue
            visited.add(u)
            page = _get(u)
            if not page:
                continue
            m = APEX_RE.search(_html.unescape(page))
            if m:
                return _html.unescape(m.group(0))
            from_kch = 'karting.ch' in u.lower()
            for link in _all_links(page, u):
                low = link.lower()
                if 'live' in low or link in visited:
                    continue
                if 'youcrono.com/pagina/' in low:
                    nxt.insert(0, link)                    # timing site: most promising
                elif 'karting.ch/rennen/event.php' in low and not from_kch:
                    nxt.append(link)                       # (not its own language variants)
        frontier = list(dict.fromkeys(nxt))[:3]
        if not frontier:
            break
    return None


def apex_booklets(apex_url: str, log: list[str]) -> list[dict]:
    """[{url, ckey, title}] booklet PDFs of an Apex GoRacing results page."""
    page = _get(apex_url)
    if not page:
        return []
    pm = re.search(r'var\s+path\s*=\s*"([^"]+)"', page)
    path = pm.group(1) if pm else urllib.parse.parse_qs(urllib.parse.urlsplit(apex_url).query).get('path', [''])[0]
    out = []
    for gm in re.finditer(r'<div class="group_submenu[^"]*" data-group="([^"]+)" data-group_title="([^"]+)">([\s\S]*?)(?=<div class="group_submenu|$)', page):
        gtitle, body = _html.unescape(gm.group(2)), gm.group(3)
        for im in re.finditer(r'data-id="([^"]+)">\s*<span data-image="pdf" class="title">([^<]*)</span>', body):
            if 'BOOKLET' in fold(im.group(2)) or not out:
                pdf = 'results' + path + im.group(1) + '.pdf'
                out.append({'url': urllib.parse.urljoin(apex_url, 'results_download.php?pdf=' + urllib.parse.quote(pdf, safe='')),
                            'ckey': class_key(gtitle), 'title': gtitle})
    if not out:
        log.append(f'rotax_italia: no booklet on {apex_url} yet')
    return out


# ---------------------------------------------------------------- rows
def build_row(ctx, parsed: dict, *, url: str, venue: str, heading_date: date | None, rnd: dict | None,
              log: list[str]) -> dict | None:
    cls = parsed['robin_class']
    d = parsed.get('date') or heading_date
    if parsed.get('date') and heading_date and abs((parsed['date'] - heading_date).days) > 10:
        log.append(f'rotax_italia: {url}: final dated {parsed["date"]} but listed as {heading_date} - PDF date used')
    if not d:
        log.append(f'rotax_italia: {url}: no date for the final - skipped')
        return None
    rno = parsed.get('round')
    if rnd and rno and rnd['round'] != rno:
        log.append(f'rotax_italia: {url}: document says round {rno}, calendar says R{rnd["round"]} (document kept)')
    rno = rno or (rnd['round'] if rnd else None)
    tid, country = guess_track(' '.join([venue.replace('-', ' '), parsed.get('title') or '',
                                         rnd['venue'] if rnd else '']), ctx.site)
    track = next((t.get('name') for t in ctx.site.get('tracks', []) if t.get('id') == tid), None) or venue or None
    final = parsed['final']
    return result_row(
        id=f'auto-{SERIES_SLUG}-{d.isoformat()}-{CLASS_NAMES[cls].lower().replace(" ", "-")}',
        date=d.isoformat(), series=SERIES, round=f'R{rno}' if rno else None,
        track=track, trackId=tid, country=country, **{'class': CLASS_NAMES[cls]},
        quali=parsed.get('quali'), heats=parsed.get('heats'), prefinal=parsed.get('prefinal'),
        final=final, finalPos=int(final) if final.isdigit() else None,
        fieldSize=parsed.get('fieldSize'), sources=[url], confidence='high')


def _learn_names(ctx, text: str) -> None:
    """Remember 'SURNAME Firstname' pairs (unambiguous order) for the battle table."""
    names = _mstate(ctx).setdefault('names', {})
    for m in re.finditer(r"(?<![\w])([A-ZÀ-Ý][A-ZÀ-Ý'’-]{1,}(?: [A-ZÀ-Ý][A-ZÀ-Ý'’-]{1,})?) ([A-ZÀ-Ý][a-zà-ÿ'’-]+(?: [A-ZÀ-Ý][a-zà-ÿ'’-]+)?)(?=\s{2})", text):
        key = f'{fold(m.group(1))}|{fold(m.group(2))}'
        if len(names) < 600 or key in names:
            names[key] = 1


def display_name(ctx, raw: str) -> str:
    if is_robin(raw):
        return 'Robin Räikkönen'
    toks = raw.split()
    nice = [t.capitalize() if t.isupper() else t for t in toks]
    names = _mstate(ctx).get('names', {})
    for k in range(1, len(toks)):
        a, b = ' '.join(nice[:k]), ' '.join(nice[k:])
        if f'{fold(a)}|{fold(b)}' in names:      # "Pacchetti Edoardo" -> surname first
            return f'{b} {a}'
        if f'{fold(b)}|{fold(a)}' in names:      # "Kevin Turbo" -> already first-last
            return f'{a} {b}'
    if len(nice) >= 2:                            # table convention: "Surname Firstname"
        return ' '.join(nice[1:] + nice[:1])
    return ' '.join(nice)


# ---------------------------------------------------------------- battle
def parse_championship(page: str) -> dict | None:
    tm = re.search(r'<table[\s\S]*?</table>', page)
    if not tm:
        return None
    rows = re.findall(r'<tr[^>]*>([\s\S]*?)</tr>', tm.group(0))
    head = None
    body = []
    for r in rows:
        ths = re.findall(r'<th[^>]*>([\s\S]*?)</th>', r)
        if ths and head is None:
            head = [_strip_tags(x) for x in ths]
            continue
        tds = [_strip_tags(x) for x in re.findall(r'<td[^>]*>([\s\S]*?)</td>', r)]
        if tds:
            body.append(tds)
    if not head or not body:
        return None
    hf = [fold(h) for h in head]

    def col(pred):
        return next((i for i, h in enumerate(hf) if pred(h)), None)
    ci = {'pos': col(lambda h: h.startswith('POS')), 'driver': col(lambda h: 'DRIVER' in h or 'PILOTA' in h),
          'net': col(lambda h: h.startswith('NET')), 'total': col(lambda h: h.startswith('TOTAL'))}
    if ci['driver'] is None or ci['net'] is None:
        return None
    events = []
    for i, h in enumerate(hf):
        m = re.match(r'^([A-Z]{2,4})\s+R\d$', h)
        if m and m.group(1) not in events:
            events.append(m.group(1))
    played = []
    for ev in events:
        idx = [i for i, h in enumerate(hf) if h.startswith(ev + ' R')]
        if any(len(r) > i and r[i] not in ('', '—', '-') for r in body for i in idx):
            played.append(ev)
    out = []
    for r in body:
        if len(r) <= max(v for v in ci.values() if v is not None):
            continue
        pos = r[ci['pos']] if ci['pos'] is not None else ''
        net = re.sub(r'[^\d]', '', r[ci['net']])
        if not pos.isdigit() or not net:
            continue
        out.append({'pos': int(pos), 'name': r[ci['driver']], 'net': int(net)})
    out.sort(key=lambda x: x['pos'])
    return {'rows': out, 'events': events, 'played': played}


def build_battle(ctx, champs: list[dict], year: int, cal: list[dict], log: list[str], prefer: list[str]) -> dict | None:
    order = sorted(champs, key=lambda c: (prefer.index(c['ckey']) if c['ckey'] in prefer else 99))
    for ch in order:
        if ch['ckey'] not in prefer:
            continue
        page = _get(ch['url'])
        if not page:
            log.append(f'rotax_italia: championship table {ch["url"]} not reachable')
            continue
        t = parse_championship(page)
        if not t or not t['rows']:
            log.append(f'rotax_italia: championship table {ch["url"]} not understood')
            continue
        if not any(is_robin(r['name']) for r in t['rows']):
            continue
        n = len(t['played'])
        cal_sorted = sorted(cal, key=lambda r: r['date'])
        as_of = cal_sorted[n - 1]['date'] if 0 < n <= len(cal_sorted) else None
        final_done = bool(cal_sorted) and n >= len(cal_sorted)
        cls = BATTLE_CLASS.get(ch['ckey'], ch['label'])
        series_txt = f'RMC Italia · {cls} {year}'
        if final_done:
            after = {'fi': 'Lopputulokset (nettopisteet)', 'sv': 'Slutställning (nettopoäng)', 'en': 'Final standings (net points)'}
        else:
            after = {'fi': f'Tilanne {n}. osakilpailun jälkeen (nettopisteet)',
                     'sv': f'Ställning efter {n} deltävlingar (nettopoäng)',
                     'en': f'Standings after round {n} (net points)'}
        top = t['rows'][:4]
        rows = []
        for r in top:
            row = {'name': display_name(ctx, r['name']), 'points': r['net']}
            if is_robin(r['name']):
                row['robin'] = True
            rows.append(row)
        if not any(r.get('robin') for r in rows):
            rr = next(r for r in t['rows'] if is_robin(r['name']))
            rows.append({'name': 'Robin Räikkönen', 'points': rr['net'], 'robin': True, 'pos': rr['pos']})
        battle = {'series': {'fi': series_txt, 'sv': series_txt, 'en': series_txt}, 'after': after, 'rows': rows}
        if as_of:
            battle['asOf'] = as_of.isoformat()
        last = cal_sorted[-1] if cal_sorted else None
        if last and not final_done and last['date'] >= ctx.today:
            battle['decider'] = {'date': last['date'].isoformat(), 'trackId': guess_track(last['venue'], ctx.site)[0]}
            if not battle['decider']['trackId']:
                del battle['decider']['trackId']
        battle['sources'] = [ch['url']]
        return battle
    return None


# ---------------------------------------------------------------- main
def _process_doc(ctx, doc: dict, ev: dict, rnd: dict | None, res: SourceResult, budget: list[int]) -> str:
    """-> 'row' | 'none' | 'mismatch' | 'skip' | 'error'."""
    url = doc['url']
    note = _seen_note(ctx, url)
    if note.startswith('mismatch:'):
        first = note.split(':', 2)
        try:
            last_check = date.fromisoformat(first[1])
        except ValueError:
            last_check = ctx.today - timedelta(days=MISMATCH_RECHECK_DAYS)
        ev_date = ev.get('date') or ctx.today
        if (ctx.today - last_check).days < MISMATCH_RECHECK_DAYS or (ctx.today - ev_date).days > MISMATCH_GIVE_UP_DAYS:
            return 'skip'
    elif note:
        return 'skip'
    if budget[0] <= 0:
        return 'skip'
    budget[0] -= 1
    data = _get(url, binary=True)
    if not data:
        res.log.append(f'rotax_italia: could not download {url}')
        return 'error'
    try:
        text = pdf_text(data)
    except Exception as e:  # noqa: BLE001
        res.log.append(f'rotax_italia: unreadable PDF {url} ({e})')
        ctx.mark(url, 'unreadable')
        return 'error'
    what = url.rsplit('/', 1)[-1]
    parsed = parse_booklet(text, res.log, what)
    classes = parsed['classes']
    if doc.get('ckey') in CLASS_NAMES and classes and doc['ckey'] not in classes:
        res.log.append(f'rotax_italia: "{doc.get("label")}" link {what} contains {"/".join(sorted(classes))} '
                       f'results, not {doc["ckey"]} (organiser upload error) - re-checked weekly')
        ctx.mark(url, f'mismatch:{ctx.today.isoformat()}')
        return 'mismatch'
    if not classes:
        res.log.append(f'rotax_italia: no official classification found in {what}')
    if not parsed.get('robin_class'):
        ctx.mark(url, 'no-robin')
        return 'none'
    _learn_names(ctx, text)
    row = build_row(ctx, parsed, url=url, venue=ev.get('venue', ''), heading_date=ev.get('date'), rnd=rnd, log=res.log)
    if not row:
        ctx.mark(url, 'no-row')
        return 'none'
    if ctx.since and row['date'] < ctx.since.isoformat():
        ctx.mark(url, 'before-since')
        return 'none'
    res.results.append(row)
    ctx.mark(url, f'robin:{row["final"]}')
    _mstate(ctx)['last_class'] = parsed['robin_class']
    return 'row'


def _doc_order(docs: list[dict], prefer: list[str]) -> list[dict]:
    first = [d for d in docs if d['ckey'] == prefer[0]]
    general = [d for d in docs if d['ckey'] in ('ALL', None)]
    rest = [d for p in prefer[1:] for d in docs if d['ckey'] == p]
    return first + general + rest


def run(ctx) -> SourceResult:
    res = SourceResult()
    _requests.clear()
    try:
        _run(ctx, res)
    except Exception as e:  # noqa: BLE001 - a source must never raise
        res.log.append(f'rotax_italia: unexpected error {type(e).__name__}: {e}')
    res.log.append(f'rotax_italia: {len(_requests)} HTTP requests, {len(res.results)} new result rows'
                   + (', battle table updated' if res.battle else ''))
    return res


def _run(ctx, res: SourceResult) -> None:
    since = ctx.since or date(ctx.today.year - 1, 1, 1)
    st = _mstate(ctx)
    cur_cls = st.get('last_class') or 'MINI'
    prefer = [cur_cls] + [c for c in YOUTH if c != cur_cls]
    budget = [MAX_DOCS_PER_RUN]
    pages = season_pages(ctx, res.log)
    newest_champs: tuple[int, list[dict]] | None = None
    last_listed: date | None = None
    sidebar: list[dict] = []
    cals: dict[int, list[dict]] = {}
    for year in sorted(pages):
        if year < since.year:
            continue
        page = _get(pages[year])
        if not page:
            res.log.append(f'rotax_italia: {pages[year]} not reachable')
            continue
        events, champs, _note = parse_season_page(page, year)
        sidebar += info_points(page)
        if champs:
            newest_champs = (year, champs)
        if not events:
            res.log.append(f'rotax_italia: no rounds found on {pages[year]}')
            continue
        need_cal = any(ev['date'] >= since - timedelta(days=10) for ev in events)
        cal = cals.setdefault(year, season_calendar(ctx, year, res.log) if need_cal or champs else [])
        for ev in events:
            last_listed = max(last_listed or ev['date'], ev['date'])
            if ev['date'] < since - timedelta(days=10):
                continue
            rnd = match_round(cal, ev['date'], ev['venue'], ctx.site)
            done_key = f'rmci:{year}:R{rnd["round"]}' if rnd else f'rmci:{ev["date"].isoformat()}'
            if ctx.seen(done_key):
                continue
            outcome = []
            for doc in _doc_order(ev['docs'], prefer):
                o = _process_doc(ctx, doc, ev, rnd, res, budget)
                outcome.append(o)
                if o == 'row':
                    ctx.mark(done_key, res.results[-1]['id'])
                    break
            if 'row' not in outcome and any(o in ('none', 'mismatch') for o in outcome):
                res.log.append(f'rotax_italia: {ev["venue"]} {ev["date"]}: Robin not found in the '
                               f'{sum(1 for o in outcome if o != "skip")} document(s) checked')
    # rounds already raced but not yet on the results page: Info Point -> official timing
    seen_ip = set()
    for ip in sorted(sidebar, key=lambda x: x['date'] or date.min):
        d = ip['date']
        if not d or ip['url'] in seen_ip:
            continue
        seen_ip.add(ip['url'])
        if d > ctx.today or d < since or (ctx.today - d).days > INFO_POINT_WINDOW:
            continue
        if last_listed and d <= last_listed + timedelta(days=3):
            continue
        cal = cals.get(d.year) or season_calendar(ctx, d.year, res.log)
        cals[d.year] = cal
        rnd = match_round(cal, d, ip['label'], ctx.site)
        done_key = f'rmci:{d.year}:R{rnd["round"]}' if rnd else f'rmci:{d.isoformat()}'
        if ctx.seen(done_key):
            continue
        apex = st.setdefault('apex', {}).get(ip['url']) or find_apex(ip['url'], res.log)
        if not apex:
            res.log.append(f'rotax_italia: {ip["label"] or ip["url"]}: official timing not online yet')
            continue
        st['apex'][ip['url']] = apex
        booklets = apex_booklets(apex, res.log)
        docs = _doc_order([b for b in booklets if b['ckey'] in YOUTH], prefer)
        ev = {'venue': ip['label'], 'date': d}
        for b in docs:
            o = _process_doc(ctx, {'url': b['url'], 'label': b['title'], 'ckey': b['ckey']}, ev, rnd, res, budget)
            if o == 'row':
                ctx.mark(done_key, res.results[-1]['id'])
                break
    if budget[0] <= 0:
        res.log.append(f'rotax_italia: download cap of {MAX_DOCS_PER_RUN} PDFs reached - rest follows next run')
    # championship battle (latest season that has tables)
    if newest_champs:
        year, champs = newest_champs
        cal = cals.get(year) or season_calendar(ctx, year, res.log)
        res.battle = build_battle(ctx, champs, year, cal, res.log, prefer)
        if res.battle is None:
            res.log.append(f'rotax_italia: Robin not in the {year} championship tables checked - no battle')
