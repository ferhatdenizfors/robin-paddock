"""Rotax Max Challenge Switzerland (www.rotaxmax.ch) - official IPS / Orbits result PDFs.

Discovery
    1. https://www.rotaxmax.ch/resultate-rotax-max-challenge-schweiz/ lists one page per season
       (/resultate-2026/, /resultate-2025/, ... older ones under /meisterschaft/).
    2. Each season page has an <h2> per round ("RMC 3 7-Laghi", "RMC 6 Wohlen - Finalrennen")
       followed by one PDF button per class ("Kategorie Micro" -> .../2024/08/2024RMC5MIC.pdf).
       Championship tables ("..._Meisterschaft_...pdf") are ignored.
    3. Only youth classes are downloaded (Micro / Mini / Junior and any class we do not know);
       adult classes (Max, Max Master, DD2, DD2 Master) are skipped without a request.
    4. A PDF whose upload folder (/YYYY/MM/) is older than ctx.since is skipped without a request.
       Every PDF that was parsed (Robin found or not) is ctx.mark()ed and never fetched again.

Parsing
    Two timing-system layouts are handled:
      * IPS    : "RM Micro      Rangliste Final" + "Sonntag, 11. Juni 2023 - 7 Laghi" page header
      * Orbits : " Final Micro      01.10.2022 14:26" session line (MyLaps Orbits print-out)
    A row is emitted only if Robin is in the FINAL classification with a time (or as DQ/DNS/DNF in
    Orbits' "Nicht Klassifiziert" block, or listed under "Disqualifikation" in the IPS penalties).
"""
from __future__ import annotations

import html as _html
import re
import sys
from datetime import date, timedelta
from pathlib import Path

try:
    from common import (SourceResult, fetch, fold, guess_track, pdf_text, result_row)
except ImportError:  # pragma: no cover - when imported as scripts.sources.rotax_ch
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from common import (SourceResult, fetch, fold, guess_track, pdf_text, result_row)

SERIES = 'Rotax Max Challenge Switzerland'
SERIES_SLUG = 'rmc-sui'
BASE = 'https://www.rotaxmax.ch'
INDEX_URL = BASE + '/resultate-rotax-max-challenge-schweiz/'
MAX_PDFS_PER_RUN = 120         # hard cap for a cold back-fill; the rest is picked up the next night

# folded class keyword -> canonical class name (checked in this order)
CLASSES = [
    ('MICRO', 'Rotax Micro MAX'),
    ('MINI', 'Rotax Mini MAX'),
    ('JUNIOR', 'Rotax Junior MAX'),
    ('MIC', 'Rotax Micro MAX'),     # short file-name forms: 2024RMC5MIC.pdf, 2026RMC3MIN.pdf
    ('MIN', 'Rotax Mini MAX'),
    ('JUN', 'Rotax Junior MAX'),
]
ADULT = re.compile(r'\b(DD2|DDM|MAX|MASTER|SENIOR|E20|THUNDER)\b')

MONTHS = {
    'JANUAR': 1, 'JANUARY': 1, 'JANVIER': 1, 'GENNAIO': 1,
    'FEBRUAR': 2, 'FEBRUARY': 2, 'FEVRIER': 2, 'FEBBRAIO': 2,
    'MARZ': 3, 'MARCH': 3, 'MARS': 3, 'MARZO': 3,
    'APRIL': 4, 'AVRIL': 4, 'APRILE': 4,
    'MAI': 5, 'MAY': 5, 'MAGGIO': 5,
    'JUNI': 6, 'JUNE': 6, 'JUIN': 6, 'GIUGNO': 6,
    'JULI': 7, 'JULY': 7, 'JUILLET': 7, 'LUGLIO': 7,
    'AUGUST': 8, 'AOUT': 8, 'AGOSTO': 8,
    'SEPTEMBER': 9, 'SEPTEMBRE': 9, 'SETTEMBRE': 9,
    'OKTOBER': 10, 'OCTOBER': 10, 'OCTOBRE': 10, 'OTTOBRE': 10,
    'NOVEMBER': 11, 'NOVEMBRE': 11,
    'DEZEMBER': 12, 'DECEMBER': 12, 'DECEMBRE': 12, 'DICEMBRE': 12,
}

_requests: list[str] = []


def _get(url: str, *, binary: bool = False):
    _requests.append(url)
    return fetch(url, binary=binary)


def compact(s: str) -> str:
    """Folded + all whitespace removed ('Rob in Rä ikkönen' -> 'ROBINRAIKKONEN')."""
    return re.sub(r'\s+', '', fold(s))


def is_robin(line: str) -> bool:
    c = compact(line)
    return 'ROBINRAIKKONEN' in c or 'RAIKKONENROBIN' in c


def slug(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', fold(s).lower()).strip('-')


def class_of(text: str) -> str | None:
    t = fold(text)
    for key, name in CLASSES:
        tail = '' if len(key) > 3 else r'(?![A-Z])'
        if re.search(r'(?<![A-Z])' + key + tail, t):
            return name
    return None


# ---------------------------------------------------------------- discovery
def season_pages(ctx, log) -> dict[int, str]:
    """{year: url} for the seasons we want (previous + current, plus everything >= since)."""
    this = ctx.today.year
    years = {this, this - 1}
    if ctx.since:
        years |= set(range(max(ctx.since.year, 2015), this + 1))
        years = {y for y in years if y >= ctx.since.year}
    pages: dict[int, str] = {}
    idx = _get(INDEX_URL)
    if idx:
        for href in re.findall(r'href="([^"]+)"', idx):
            m = re.search(r'/resultate-(\d{4})/?$', href)
            if m and int(m.group(1)) in years:
                pages[int(m.group(1))] = _html.unescape(href)
    else:
        log.append('rotax_ch: results index not reachable, falling back to /resultate-YYYY/')
    for y in sorted(years):
        if y not in pages and y >= this - 1:
            pages[y] = f'{BASE}/resultate-{y}/'   # page may not be linked yet
    return pages


def season_documents(page_html: str, year: int) -> list[dict]:
    """[{round, venue, label, url}] from one season page (round headings + class buttons)."""
    m = re.search(r'<div class="entry-content[\s\S]*?(?:</article>|$)', page_html)
    body = m.group(0) if m else page_html
    docs, heading = [], ''
    for tok in re.finditer(r'<h[1-4][^>]*>([\s\S]*?)</h[1-4]>|<a\s[^>]*href="([^"]+\.pdf)"[^>]*>([\s\S]*?)</a>', body, re.I):
        if tok.group(1) is not None:
            heading = _html.unescape(re.sub(r'<[^>]+>', '', tok.group(1))).strip()
            continue
        url = _html.unescape(tok.group(2))
        if not url.startswith('http'):
            url = BASE + '/' + url.lstrip('/')
        label = _html.unescape(re.sub(r'<[^>]+>', ' ', tok.group(3))).replace('\xa0', ' ').strip()
        fname = url.rsplit('/', 1)[-1]
        if 'MEISTERSCHAFT' in fold(fname) or 'MEISTERSCHAFT' in fold(heading):
            continue
        rm = re.search(r'\bRMC\s*(\d{1,2})\b', heading, re.I) or re.search(r'RMC-?(\d{1,2})', fname, re.I)
        venue = ''
        hm = re.match(r'\s*RMC\s*\d{1,2}\s*(.*)$', heading, re.I)
        if hm:
            venue = re.split(r'\s[–—-]\s', hm.group(1))[0].strip()
        docs.append({'round': int(rm.group(1)) if rm else None, 'venue': venue, 'heading': heading,
                     'label': label, 'url': url, 'year': year})
    return docs


def upload_month_end(url: str) -> date | None:
    m = re.search(r'/uploads/(\d{4})/(\d{2})/', url)
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    nxt = date(y + (mo == 12), mo % 12 + 1, 1)
    return nxt - timedelta(days=1)


# ---------------------------------------------------------------- PDF parsing
ROW = re.compile(r'^\s*(\d{1,3})\.?\s+(\d{1,3})\s+(\S.*)$')
NC_ROW = re.compile(r'^\s*(DQ|DSQ|DNS|DNF|DNQ|NC|NK)\s+(\d{1,3})\s+(\S.*)$')
IPS_SESSION = re.compile(r'^\s*(\S.*?)\s{2,}Rangliste\s+(\S.*?)\s*$')
ORBITS_SESSION = re.compile(r'^\s*(\S.*?)\s{2,}(\d{2})\.(\d{2})\.(\d{4})\s+\d{1,2}:\d{2}\s*$')
IPS_DATE = re.compile(r'^\s*[A-Za-zÄÖÜäöüéè]+,\s*(\d{1,2})\.?\s*([A-Za-zÄÖÜäöüéèû]+)\s+(\d{4})\s*-\s*(.+?)\s*$')
PRINT = re.compile(r'^\s*(?:Druck|Gedruckt):\s*(\d{2})\.(\d{2})\.(\d{4})\s+(\d{1,2})[:.](\d{2})')
TIME = re.compile(r'\b\d{1,2}:\d{2}\.\d{3}\b|\b\d{2}\.\d{3}\b')


def session_kind(title: str) -> str:
    t = compact(title)
    if re.search(r'\d\.\+\d\.LAUF', t) or 'GESAMT' in t or 'TOTAL' in t:
        return 'combined'
    if 'TRAINING' in t or 'PRACTICE' in t:
        return 'practice'
    if 'ZEITFAHREN' in t or 'QUALIFYING' in t or t.startswith('QUALIF') or t.startswith('ZF'):
        return 'quali'
    m = re.match(r'(\d)\.(?:QUALIFIKATIONS)?LAUF', t) or re.match(r'(?:HEAT|LAUF)(\d)', t)
    if m:
        return 'heat' + m.group(1)
    if 'VORFINAL' in t or 'PREFINAL' in t or 'PRE-FINAL' in t:
        return 'prefinal'
    if 'FINAL' in t:
        return 'final'
    return 'other'


def parse_pdf(text: str) -> dict:
    """Split an RMC PDF into sessions: [{kind, title, date, rows, dq, header_points}]."""
    sessions: list[dict] = []
    cur = None
    header_date = None
    venue = None
    prints: list[tuple[date, int]] = []
    for line in text.splitlines():
        m = IPS_DATE.match(line)
        if m and fold(m.group(2)) in MONTHS:
            try:
                header_date = date(int(m.group(3)), MONTHS[fold(m.group(2))], int(m.group(1)))
                venue = m.group(4)
            except ValueError:
                pass
            continue
        m = PRINT.match(line)
        if m:
            try:
                prints.append((date(int(m.group(3)), int(m.group(2)), int(m.group(1))),
                               int(m.group(4)) * 60 + int(m.group(5))))
            except ValueError:
                pass
            continue
        m = IPS_SESSION.match(line)
        if m:
            cur = {'kind': session_kind(m.group(2)), 'title': line.strip(), 'klass': m.group(1),
                   'date': header_date, 'rows': [], 'dq': set(), 'points_col': False, 'layout': 'ips'}
            sessions.append(cur)
            continue
        m = ORBITS_SESSION.match(line)
        if m and not re.search(r'\bkm\b', line):
            try:
                d = date(int(m.group(4)), int(m.group(3)), int(m.group(2)))
            except ValueError:
                d = None
            cur = {'kind': session_kind(m.group(1)), 'title': m.group(1).strip(), 'klass': m.group(1),
                   'date': d, 'rows': [], 'dq': set(), 'points_col': False, 'layout': 'orbits'}
            sessions.append(cur)
            continue
        if cur is None:
            continue
        c = compact(line)
        if re.match(r'^(RANG|POS\.)', c) and 'NAME' in c:
            cur['points_col'] = re.search(r'\bM\d\s*$', line) is not None
            continue
        if c.startswith('DISQ') and ':' in line:
            # 'Disquailifizie rt: 13, 9 5 Regelver stoss' -> {'13', '95'} (pdf splits digits with spaces)
            nums = re.split(r'[A-Za-zÀ-ÿ]', line.split(':', 1)[1])[0]
            cur['dq'] |= {re.sub(r'\s+', '', n) for n in nums.split(',') if re.fullmatch(r'[\d\s]+', n.strip() or 'x')}
            continue
        m = ROW.match(line)
        if m and re.search(r'[A-Za-zÀ-ÿ]{2}', m.group(3)):
            cur['rows'].append({'pos': m.group(1), 'no': m.group(2), 'rest': m.group(3), 'line': line})
            continue
        m = NC_ROW.match(line)
        if m and re.search(r'[A-Za-zÀ-ÿ]{2}', m.group(3)):
            cur['rows'].append({'pos': m.group(1), 'no': m.group(2), 'rest': m.group(3), 'line': line, 'nc': True})
    return {'sessions': sessions, 'header_date': header_date, 'venue': venue, 'prints': prints}


def race_date(doc: dict, final: dict, log: list[str], url: str) -> date | None:
    """Final's date; if an IPS header date contradicts live print-outs of the same day, trust the prints.

    IPS print-outs printed live at the track are spread over the race day (qualifying in the
    morning, final in the afternoon); a batch re-print days later spans only a few minutes.
    """
    d = final.get('date') or doc.get('header_date')
    prints = doc.get('prints') or []
    days = {p[0] for p in prints}
    if d and len(days) == 1:
        pd = next(iter(days))
        span = max(p[1] for p in prints) - min(p[1] for p in prints)
        if pd != d and span >= 90 and 0 < (pd - d).days <= 7:
            log.append(f'rotax_ch: {url}: header date {d} but live print-outs dated {pd} - using {pd}')
            return pd
    return d


def time_in(rest: str) -> bool:
    return TIME.search(rest) is not None


def robin_pos(session: dict) -> str | None:
    for r in session['rows']:
        if is_robin(r['line']):
            if r.get('nc'):
                return {'DQ': 'DSQ', 'NK': 'NC'}.get(r['pos'], r['pos'])
            if r['no'] in session['dq']:
                return 'DSQ'
            return str(int(r['pos']))
    return None


def parse_document(text: str, doc: dict, ctx, log: list[str]) -> tuple[dict | None, bool]:
    """(row or None, complete) - complete=False means the PDF has no final yet (retry later)."""
    url = doc['url']
    parsed = parse_pdf(text)
    sessions = parsed['sessions']
    finals = [s for s in sessions if s['kind'] == 'final']
    if not finals:
        log.append(f'rotax_ch: {url}: no final classification found (yet)')
        return None, False
    if not any(is_robin(ln) for ln in text.splitlines()):
        return None, True
    robin_finals = [s for s in finals if any(is_robin(r['line']) for r in s['rows'])]
    if not robin_finals:
        log.append(f'rotax_ch: {url}: Robin appears in the document but not in a final classification - skipped')
        return None, True
    if len(robin_finals) > 1:
        log.append(f'rotax_ch: {url}: Robin in {len(robin_finals)} finals - ambiguous, skipped')
        return None, True
    final = robin_finals[0]
    rrow = next(r for r in final['rows'] if is_robin(r['line']))

    # class: session header must agree with the link label / file name
    klass = class_of(final['klass']) or class_of(final['title'])
    k_link = class_of(doc['label']) or class_of(url.rsplit('/', 1)[-1])
    if not klass or (k_link and klass != k_link):
        log.append(f'rotax_ch: {url}: class unclear (header {final["klass"]!r}, link {doc["label"]!r}) - skipped')
        return None, True

    # final position / status
    if rrow.get('nc'):
        fin = {'DQ': 'DSQ', 'NK': 'NC'}.get(rrow['pos'], rrow['pos'])
    elif rrow['no'] in final['dq']:
        fin = 'DSQ'
    elif time_in(rrow['rest']):
        fin = str(int(rrow['pos']))
    else:
        log.append(f'rotax_ch: {url}: Robin listed in the final without a time (DNS?) - skipped')
        return None, True
    entries = len(final['rows'])     # everyone listed in the final classification (incl. 0-lap retirements)

    d = race_date(parsed, final, log, url)
    if not d:
        log.append(f'rotax_ch: {url}: no event date found - skipped')
        return None, True
    if ctx.since and d < ctx.since:
        return None, True

    rnd = doc.get('round')
    if not rnd:
        m = re.search(r'Rotax Max Challenge\s+(?:RMC\s*)?(\d{1,2})\b|RMC\s*(\d{1,2})\b', text)
        rnd = int(m.group(1) or m.group(2)) if m else None
    venue_txt = doc.get('venue') or parsed.get('venue') or ''
    tid, country = guess_track(re.sub(r'[-_]', ' ', venue_txt + ' ' + (parsed.get('venue') or '')), ctx.site)
    track = next((t.get('name') for t in ctx.site.get('tracks', []) if t.get('id') == tid), None) or venue_txt or None

    def pos_in(kind: str) -> str | None:
        ss = [s for s in sessions if s['kind'] == kind and s is not final
              and class_of(s['klass']) in (None, klass)]   # PDFs sometimes contain a stray page of another class
        for s in ss:
            p = robin_pos(s)
            if p:
                return p
        return None

    heats = [pos_in(f'heat{i}') for i in range(1, 5)]
    while heats and heats[-1] is None:
        heats.pop()
    heats_s = '-'.join(h or '–' for h in heats) if heats else None

    row = result_row(
        id=f'auto-{SERIES_SLUG}-{d.isoformat()}-{slug(klass)}',
        date=d.isoformat(), series=SERIES, round=f'R{rnd}' if rnd else None,
        track=track, trackId=tid, country=country, **{'class': klass},
        quali=pos_in('quali'), heats=heats_s, prefinal=pos_in('prefinal'),
        final=fin, finalPos=int(fin) if fin.isdigit() else None,
        fieldSize=entries or None, sources=[url], confidence='high',
    )
    return row, True


# ---------------------------------------------------------------- entry point
def run(ctx) -> SourceResult:
    res = SourceResult()
    _requests.clear()
    try:
        _run(ctx, res)
    except Exception as e:  # never raise
        res.log.append(f'rotax_ch: unexpected error {type(e).__name__}: {e}')
    res.log.append(f'rotax_ch: {len(_requests)} HTTP requests, {len(res.results)} result rows')
    return res


def _run(ctx, res: SourceResult) -> None:
    log = res.log
    pages = season_pages(ctx, log)
    todo: list[dict] = []
    for year, url in sorted(pages.items(), reverse=True):
        page = _get(url)
        if not page:
            if year < ctx.today.year:
                log.append(f'rotax_ch: season page {url} not reachable')
            continue
        docs = season_documents(page, year)
        if not docs:
            log.append(f'rotax_ch: {url}: no result PDFs listed')
        for d in docs:
            if ctx.seen(d['url']):
                continue
            if not doc_class(d) and ADULT.search(fold(d['label'] + ' ' + d['url'].rsplit('/', 1)[-1].replace('RMC', ' '))):
                continue          # adult class - Robin (born 2015) cannot race there
            end = upload_month_end(d['url'])
            if ctx.since and end and end < ctx.since:
                continue
            todo.append(d)
    # Robin races one class per meeting: try the class he raced most recently first and stop
    # looking at a round as soon as he has been found in one of its classes.
    pref = preferred_class(ctx)
    rank = {pref: 0} if pref else {}
    todo.sort(key=lambda d: (-d['year'], -(d['round'] or 0), rank.get(doc_class(d), 1)))
    found_rounds: set = set()
    downloads = 0
    for d in todo:
        key = (d['year'], d['round'], d['heading'])
        if d['round'] and key in found_rounds:
            ctx.mark(d['url'], 'robin-found-in-other-class')
            continue
        if downloads >= MAX_PDFS_PER_RUN:
            log.append(f'rotax_ch: download cap {MAX_PDFS_PER_RUN} reached - remaining PDFs next run')
            break
        downloads += 1
        data = _get(d['url'], binary=True)
        if not data:
            log.append(f'rotax_ch: could not download {d["url"]}')
            continue
        try:
            text = pdf_text(data)
        except Exception as e:
            log.append(f'rotax_ch: {d["url"]}: PDF text extraction failed ({type(e).__name__})')
            continue
        try:
            row, complete = parse_document(text, d, ctx, log)
        except Exception as e:
            log.append(f'rotax_ch: {d["url"]}: parse error {type(e).__name__}: {e}')
            continue
        if row:
            res.results.append(row)
            found_rounds.add(key)
            log.append(f'rotax_ch: {row["date"]} {row.get("round")} {row["class"]}: final {row["final"]}')
        if complete:
            ctx.mark(d['url'], 'robin' if row else 'no-robin')


def doc_class(d: dict) -> str | None:
    return class_of(d['label']) or class_of(d['url'].rsplit('/', 1)[-1])


def preferred_class(ctx) -> str | None:
    rows = [r for r in (ctx.site.get('results') or []) + ((ctx.auto or {}).get('results') or [])
            if r.get('series') == SERIES and r.get('date') and r.get('class')]
    return max(rows, key=lambda r: r['date'])['class'] if rows else None
