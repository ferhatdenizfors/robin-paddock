"""International events: RMC International Trophy (3MK Events + Apex Timing),
Rotax MAX Challenge Grand Finals (rotax-racing.com + Apex Timing) and the FIA Karting
championships (fiakarting.com JSON backend).

Plain Python, no AI. Discovery (nothing per-round is hard-coded):

* RMCIT     rmcit.3mkevents.com/en/results.html lists every season page; each season/meeting page
            embeds an Apex Timing iframe (results.php?path=/3mkevents/<year>/rmcit).
* Grand Finals  grandfinals.rotax-racing.com/live-results links the current Apex Timing results
            path (e.g. /korridas/2025/rgf2025/); the same path pattern with the year substituted
            gives the other seasons in the window. Venue from the site's "the-track-<x>" link
            (current edition) or the RMCGF archive page (past editions).
* FIA Karting  backend.fiakarting.com/api/v1/events?year=Y lists events + categories; each
            category has an entry list, a results index and per-session JSON classifications.

Apex Timing pages are structured HTML: the event listing (results.php) names every session per
class, and functions/request_results.php returns one classification table.

A result row is only emitted when Robin is in the classification of the FINAL (with a rank, or an
explicit DNS/DNF/DSQ/DNQ status). Everything else is logged and skipped.
"""
from __future__ import annotations

import html as htmllib
import json
import re
import sys
import urllib.parse
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import (Context, SourceResult, fetch as _fetch, fold, guess_track, mentions_driver,  # noqa: E402
                    result_row)

RMCIT_BASE = 'https://rmcit.3mkevents.com'
RMCIT_INDEX = RMCIT_BASE + '/en/results.html'
GF_BASE = 'https://grandfinals.rotax-racing.com'
GF_LIVE = GF_BASE + '/live-results'
GF_ARCHIVE = GF_BASE + '/grand-finals-archive'
APEX = 'https://www.apex-timing.com/goracing/'
FIA_API = 'https://backend.fiakarting.com/api/v1/'
FIA_SITE = 'https://www.fiakarting.com'

SERIES_RMCIT = 'RMC International Trophy'
SERIES_GF = 'Rotax Grand Finals'
SETTLE_DAYS = 3          # an event counts as "done" (never re-read) this many days after it ended

APEX_CLASS_NAMES = {     # Apex group title (folded) -> display class
    'MICRO': 'Rotax Micro MAX', 'MINI': 'Rotax Mini MAX', 'JUNIOR': 'Rotax Junior MAX',
    'SENIOR': 'Rotax Senior MAX', 'DD2': 'Rotax DD2', 'DD2 MASTERS': 'Rotax DD2 Masters',
    'DD2 MASTER': 'Rotax DD2 Masters',
}

STATUS_WORDS = [  # (regex on folded text, status)
    (r'\bDSQ\b|\bDISQ|DISQUALIF|\bEXCLU', 'DSQ'),
    (r'\bDNS\b|NON PARTANT|NOT STARTED|DID NOT START|NON[- ]STARTER', 'DNS'),
    (r'\bDNQ\b|NON QUALIFI|NOT QUALIFIED', 'DNQ'),
    (r'\bDNF\b|RETIRED|ABANDON|\bNC\b|NOT CLASSIFIED|NON CLASSE', 'DNF'),
]


# ---------------------------------------------------------------- helpers
class _Run:
    """Per-run bookkeeping (request counter, log, context)."""

    def __init__(self, ctx: Context, match=None):
        self.ctx = ctx
        self.res = SourceResult()
        self.requests = 0
        self.per_host = {}
        self.match = match or mentions_driver
        today = ctx.today if isinstance(ctx.today, date) else date.today()
        self.today = today
        since = ctx.since or date(today.year - 1, 1, 1)
        self.since = since
        # seasons to crawl: previous + current, or back to `since` when it is older
        self.years = list(range(min(since.year, today.year - 1), today.year + 1))
        self.fia_years = list(range(max(since.year, today.year - 1), today.year + 1))
        born = str(((ctx.site or {}).get('driver') or {}).get('born') or '2015')[:4]
        self.born = int(born) if born.isdigit() else 2015

    def fetch(self, url, **kw):
        self.requests += 1
        host = urllib.parse.urlsplit(url).netloc
        self.per_host[host] = self.per_host.get(host, 0) + 1
        return _fetch(url, **kw)

    def log(self, msg):
        self.res.log.append('international: ' + msg)

    def settled(self, end: date | None) -> bool:
        return end is not None and (self.today - end).days > SETTLE_DAYS

    def age(self, year: int) -> int:
        return year - self.born


def _text(s) -> str:
    if isinstance(s, dict) or s is None:
        return ''
    s = re.sub(r'<[^>]+>', ' ', str(s))
    return re.sub(r'\s+', ' ', htmllib.unescape(s)).strip()


def _slug(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', fold(s).lower()).strip('-')


def _d(s) -> date | None:
    m = re.search(r'(\d{4})[-/](\d{2})[-/](\d{2})', str(s or ''))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _status(text: str) -> str | None:
    t = fold(text)
    for rx, st in STATUS_WORDS:
        if re.search(rx, t):
            return st
    return None


def _kind(title: str) -> str | None:
    """Session kind from its title: final / prefinal / superheat / heat / quali (None = ignore)."""
    t = fold(title).strip()
    if re.search(r'WARM|PRACTICE \d|FREE|NON[- ]?QUAL|PARTICIPANT|ENTRY|CLASSIFICATION|INTERMEDIATE|'
                 r'POINTS|STARTING GRID|GRILLE|LAP CHART|ANALYSIS|PENALT', t):
        return None
    if re.search(r'PRE[- ]?FINAL', t):
        return 'prefinal'
    if re.search(r'SUPER ?HEAT', t):
        return 'superheat'
    if re.fullmatch(r'(GRAND )?FINALE?( RACE)?', t):
        return 'final'
    if re.fullmatch(r'(GRAND )?FINALE?( RACE)?\s*[-]?\s*[A-D]|[A-D][- ]FINAL|(CONSOLATION|B) FINAL.*|FINAL.*', t):
        return 'final-split'
    if re.search(r'QUALIFYING HEAT|^HEAT\b|MANCHE', t):
        return 'heat'
    if re.search(r'QUALIFYING( PRACTICE| SESSION)?$|^CHRONO|QUALIFYING PRACTICE$|^QUALIFYING$', t):
        return 'quali'
    if re.search(r'QUALIFYING PRACTICE|QUALIFYING SESSION', t):
        return 'quali-split'
    return None


def _split_suffix(title: str) -> str:
    m = re.search(r'(?:[-\s(]|^)([A-D])\)?\s*$', fold(title).strip())
    return m.group(1) if m else ''


# ---------------------------------------------------------------- Apex Timing
def apex_listing(run: _Run, path: str) -> dict | None:
    """Parse results.php?path=... -> {title, start, end, groups: [{id, title, sessions}]}."""
    url = APEX + 'results.php?path=' + path
    txt = run.fetch(url)
    if txt is None:
        run.log(f'Apex listing unavailable: {url}')
        return None
    if 'group_submenu' not in txt:
        return {'url': url, 'title': '', 'start': None, 'end': None, 'groups': [], 'empty': True}
    title = _text((re.search(r'<p class="title">(.*?)</p>', txt, re.S) or [None, ''])[1])
    m = re.search(r'data-start="([^"]*)"\s+data-end="([^"]*)"', txt)
    start, end = (_d(m.group(1)), _d(m.group(2))) if m else (None, None)
    names = {}
    for gid, gt in re.findall(r'data-group="([^"]+)" data-group_title="([^"]*)"', txt):
        if gt and gid != 'documents':
            names.setdefault(gid, htmllib.unescape(gt))
    groups = []
    blocks = re.split(r'<div class="group_submenu[^"]*" data-group="', txt)[1:]
    for blk in blocks:
        gid = blk.split('"', 1)[0]
        if gid not in names:
            continue
        sessions, day = [], None
        for m in re.finditer(r'data-date="([\d/]+)"|data-id="([^"]+)">\s*<span data-image="([^"]*)" class="title">([^<]*)</span>', blk):
            if m.group(1):
                day = _d(m.group(1))
            else:
                sessions.append({'id': m.group(2), 'type': m.group(3), 'title': _text(m.group(4)), 'date': day})
        groups.append({'id': gid, 'title': names[gid], 'sessions': sessions})
    return {'url': url, 'title': title, 'start': start, 'end': end, 'groups': groups, 'empty': not groups}


def apex_table(run: _Run, path: str, group: str, sess: dict) -> list[dict] | None:
    q = urllib.parse.urlencode({'group_id': group, 'file_id': sess['id'], 'path': path.rstrip('/') + '/',
                                'type': sess['type'], 'window_width': 1200})
    txt = run.fetch(APEX + 'functions/request_results.php?' + q)
    if not txt or 'table_results' not in txt:
        return None
    rows = []
    body = txt.split('<tbody>', 1)[-1]
    for chunk in re.split(r'<tr\b', body)[1:]:
        cls = (re.match(r'[^>]*class="([^"]*)"', chunk) or [None, ''])[1]
        cells = [(c, _text(v)) for c, v in re.findall(r'<td[^>]*class="([^"]*)"[^>]*>(.*?)</td>', chunk, re.S)]
        if not cells:
            continue
        get = lambda token: next((v for c, v in cells if token in c.split()), None)  # noqa: E731
        driver = get('driver')
        if driver is None:
            continue
        rank = get('rnk_class') or get('rnk') or ''
        cls_col = [v for c, v in cells if c.strip() == 'center' and not v.isdigit() and v]
        status = None
        if not rank.isdigit():
            status = _status(cls) or next((s for s in (_status(v) for c, v in cells if 'right' in c.split()) if s), None)
        rows.append({'rank': rank if rank.isdigit() else '', 'driver': driver, 'status': status,
                     'cls': cls_col[0] if cls_col else '', 'text': ' | '.join(v for _, v in cells)})
    return rows


def _pos(row: dict | None) -> str | None:
    if not row:
        return None
    return row['rank'] or row['status'] or None


def _find(run: _Run, rows):
    hits = [r for r in rows or [] if run.match(r['driver'])]
    return hits[0] if len(hits) == 1 else None


def apex_event(run: _Run, path: str, meta: dict) -> None:
    """Scan one Apex Timing event. meta: series, venue, source_urls, date(optional), key."""
    key = 'apex:' + path.rstrip('/')
    if run.ctx.seen(key):
        return
    lst = apex_listing(run, path)
    if lst is None:
        return
    if lst['empty']:
        run.log(f'{meta["series"]}: no results published yet at {lst["url"]}')
        if meta.get('year') and meta['year'] < run.today.year:
            run.ctx.mark(key, 'empty')
        return
    end = lst['end'] or meta.get('date')
    if end and end < run.since:
        run.ctx.mark(key, 'before-since')
        return
    year = (end or lst['start'] or run.today).year
    all_final = True
    for g in lst['groups']:
        gname = fold(g['title']).strip()
        if not _apex_candidate(run, gname, year):
            continue
        gkey = f'{key}:{g["id"]}'
        if run.ctx.seen(gkey):
            continue
        finals = [s for s in g['sessions'] if _kind(s['title']) == 'final']
        splits = [s for s in g['sessions'] if _kind(s['title']) == 'final-split']
        if not finals and not splits:
            if run.settled(end):   # class ran without a final (or was cancelled): nothing to wait for
                run.log(f'{meta["series"]} {g["title"]}: no final listed at {lst["url"]}')
                run.ctx.mark(gkey, 'no-final')
            else:
                all_final = False
            continue
        final = finals[-1] if finals else None
        rows = apex_table(run, path, g['id'], final) if final else None
        hit = _find(run, rows)
        if final and rows is None:
            run.log(f'{meta["series"]} {g["title"]}: final table unreadable ({lst["url"]})')
            all_final = False
            continue
        if not hit and splits and not finals:
            # A/B finals: not mapped to one classification; only report when Robin is in one of them
            for s in splits:
                if _find(run, apex_table(run, path, g['id'], s)):
                    run.log(f'{meta["series"]} {g["title"]}: Robin is in split final "{s["title"]}" – '
                            f'not emitted (A/B finals are not merged automatically) {lst["url"]}')
        if not hit:
            if run.settled(end):
                run.ctx.mark(gkey, 'no-robin')
            continue
        row = _apex_row(run, path, lst, g, final, rows, hit, meta)
        if row:
            run.res.results.append(row)
            run.log(f'{meta["series"]} {g["title"]} {row["date"]}: final {row["final"]}')
        if run.settled(end):
            run.ctx.mark(gkey, 'robin')
    if all_final and run.settled(end):
        run.ctx.mark(key, 'done')


def _apex_candidate(run: _Run, gname: str, year: int) -> bool:
    if re.search(r'MICRO|MINI|JUNIOR|CADET|U1[0-4]|\b60\b', gname):
        return True
    return run.age(year) >= 14 and re.search(r'SENIOR|\bOK\b', gname) is not None


def _apex_row(run: _Run, path, lst, g, final, rows, hit, meta) -> dict | None:
    fpos = _pos(hit)
    if fpos is None:
        run.log(f'{meta["series"]} {g["title"]}: Robin in final but no rank/status – skipped')
        return None
    sess = g['sessions']
    same_cls = [r for r in rows if not hit['cls'] or r['cls'] == hit['cls']]

    def pos_in(kind):   # Robin's result in every session of this kind he appears in (listing order)
        out = []
        for s in sess:
            if _kind(s['title']) == kind:
                h = _find(run, apex_table(run, path, g['id'], s))
                if h and _pos(h):
                    out.append((s, _pos(h)))
        return out

    quali = None
    q_all = [s for s in sess if _kind(s['title']) == 'quali']
    if q_all:
        h = _find(run, apex_table(run, path, g['id'], q_all[-1]))
        quali = _pos(h)
    heats = [p for _, p in pos_in('heat')]
    sh = pos_in('superheat')
    pf = pos_in('prefinal')
    prefinal = None
    if sh:
        prefinal = 'SH ' + sh[-1][1]
    elif pf:
        s, p = pf[-1]
        n_pf = sum(1 for x in sess if _kind(x['title']) == 'prefinal')
        suf = _split_suffix(s['title']) if n_pf > 1 else ''
        prefinal = p + (f' ({suf})' if suf else '')
    day = final.get('date') or lst['end']
    cls = APEX_CLASS_NAMES.get(fold(g['title']).strip(), 'Rotax ' + g['title'])
    venue = meta.get('venue') or ''
    tid, country = guess_track(venue, run.ctx.site) if venue else (None, None)
    track = _track_name(run, tid) or (venue or None)
    series = meta['series']
    listing_url = lst['url']
    return result_row(
        id=f'auto-{_slug(series)}-{day.isoformat()}-{_slug(cls)}',
        date=day.isoformat(), series=series, round=meta.get('round'), track=track, trackId=tid,
        country=country, **{'class': cls},
        quali=quali, heats='-'.join(heats) if heats else None, prefinal=prefinal,
        final=fpos, finalPos=int(fpos) if fpos.isdigit() else None, fieldSize=len(same_cls) or None,
        sources=[listing_url] + [u for u in meta.get('sources', []) if u != listing_url],
        confidence='high')


def _track_name(run: _Run, tid):
    if not tid:
        return None
    for t in (run.ctx.site or {}).get('tracks', []):
        if t.get('id') == tid:
            return t.get('name')
    return None


# ---------------------------------------------------------------- RMCIT (3MK Events)
def scan_rmcit(run: _Run) -> None:
    idx = run.fetch(RMCIT_INDEX)
    if idx is None:
        run.log(f'RMCIT index unavailable: {RMCIT_INDEX}')
        return
    seasons = {}   # year -> season page url (None = the index page itself shows it)
    for u, y in re.findall(r'href="(https?://rmcit\.3mkevents\.com/en/results/season-\d+/[a-z-]*?(\d{4})\.html)"', idx):
        seasons.setdefault(int(y), u)
    # the index itself shows the current season ("Results - RMCIT 2026" + its Apex iframe)
    cur = re.search(r'Results - [A-Za-z ]*?(\d{4})', idx) or re.search(r'goracing/results\.php\?path=/[^/"]+/(\d{4})/', idx)
    pages = {}
    if cur:
        pages[int(cur.group(1))] = (RMCIT_INDEX, idx)
    for y in sorted(set(run.years)):
        if y in pages or y not in seasons:
            continue
        pages[y] = (seasons[y], None)
    for y, (url, txt) in sorted(pages.items()):
        skey = 'rmcit-season:' + url
        if y not in run.years or run.ctx.seen(skey):
            continue
        txt = txt if txt is not None else run.fetch(url)
        if txt is None:
            run.log(f'RMCIT season page unavailable: {url}')
            continue
        keys = _rmcit_page(run, url, txt, y, fetch_meetings=True)
        # a past season whose meetings are all processed is never fetched again
        if url != RMCIT_INDEX and y < run.today.year and keys and all(run.ctx.seen(k) for k in keys):
            run.ctx.mark(skey, 'done')


def _rmcit_page(run: _Run, url: str, txt: str, year: int, fetch_meetings: bool) -> list[str]:
    """Scan the Apex events embedded in one season/meeting page; returns their state keys."""
    title = _text((re.search(r'<title>(.*?)</title>', txt, re.S) or [None, ''])[1])
    # "results, photos and news RMCIT - LE MANS - 2026-07-18 - Le Mans Karting International"
    m = re.search(r'\d{4}-\d{2}-\d{2} - (.+)$', title)
    venue = m.group(1).strip() if m else ''
    active = re.search(r'<li class="active">\s*<a href="(https://rmcit\.3mkevents\.com/en/results/(?!season-)[^"/]+/[^"/]+\.html)"', txt)
    srcs = [active.group(1) if active else url]
    shown, keys = set(), []
    for path, fid in re.findall(r'<iframe src="https://www\.apex-timing\.com/goracing/results\.php\?path=([^"&]+)"[^>]*'
                                r'id="results-apex-iframe-([^"]*)"', txt):
        d = _d(fid)
        shown.add(d)
        keys.append('apex:' + urllib.parse.unquote(path).rstrip('/'))
        apex_event(run, urllib.parse.unquote(path), {'series': SERIES_RMCIT, 'venue': venue, 'sources': srcs,
                                                    'date': d, 'year': year})
    if not fetch_meetings:
        return keys
    # other meetings of the same season (a season page embeds only one meeting's results)
    for murl, label in re.findall(r'<a href="(https://rmcit\.3mkevents\.com/en/results/[a-z0-9-]+/[a-z0-9-]+\.html)">'
                                  r'([^<]*\d{4}-\d{2}-\d{2})\s*</a>', txt):
        d = _d(label)
        key = 'rmcit-meeting:' + murl
        if d in shown or run.ctx.seen(key) or (d and d < run.since):
            continue
        shown.add(d)
        mtxt = run.fetch(murl)
        if mtxt:
            sub = _rmcit_page(run, murl, mtxt, year, fetch_meetings=False)
            keys += sub
            if d and run.settled(d) and all(run.ctx.seen(k) for k in sub):
                run.ctx.mark(key)
        else:
            keys.append(key)
    return keys


# ---------------------------------------------------------------- Grand Finals
def scan_gf(run: _Run) -> None:
    live = run.fetch(GF_LIVE)
    if live is None:
        run.log(f'Grand Finals results page unavailable: {GF_LIVE}')
        return
    found = sorted(set(urllib.parse.unquote(p) for p in re.findall(
        r'apex-timing\.com/goracing/results\.php\?path=([^"&\s<]+)', live)))
    if not found:
        run.log('Grand Finals: no Apex Timing link on live-results page')
        return
    paths = {}
    for p in found:
        m = re.search(r'(20\d\d)', p)
        if not m:
            continue
        paths[int(m.group(1))] = p
        for y in run.years:     # same organiser path pattern, other seasons of the window
            paths.setdefault(y, p.replace(m.group(1), str(y)))
    cur_track = re.search(r'href="https://grandfinals\.rotax-racing\.com/the-track-([a-z-]+)"', live)
    archive = None
    for y in sorted(paths):
        if y not in run.years:
            continue
        key = 'apex:' + paths[y].rstrip('/')
        if run.ctx.seen(key):
            continue
        # venue: the current edition's "the-track-<x>" menu link; past editions via the archive (below)
        venue = cur_track.group(1).replace('-', ' ') if (y >= run.today.year and cur_track) else ''
        meta = {'series': SERIES_GF, 'venue': venue, 'sources': [GF_LIVE], 'year': y}
        before = len(run.res.results)
        apex_event(run, paths[y], meta)
        # fill venue for new GF rows from the archive page (1 request, only when needed)
        new = [r for r in run.res.results[before:] if not r.get('trackId')]
        if new:
            if archive is None:
                archive = run.fetch(GF_ARCHIVE) or ''
            m = re.search(r'RMCGF %d\b(.*?)(?=RMCGF \d{4}\b|$)' % y, _text(archive), re.S)
            if m:
                tid, country = guess_track(m.group(1), run.ctx.site)
                for r in new:
                    if tid:
                        r['trackId'], r['country'] = tid, country
                        r['track'] = _track_name(run, tid)
                        r['sources'].append(GF_ARCHIVE)
            for r in new:
                if not r.get('trackId'):
                    run.log(f'Grand Finals {y}: venue not recognised – row has no track')


# ---------------------------------------------------------------- FIA Karting
def _fia_get(run: _Run, endpoint: str, params: dict):
    url = FIA_API + endpoint + '?' + urllib.parse.urlencode(params)
    txt = run.fetch(url)
    if not txt:
        return None, url
    try:
        return json.loads(txt), url
    except ValueError:
        return None, url


def _fia_rows(table: dict) -> list[dict]:
    cols = [str(c.get('title') or '') for c in ((table.get('header') or {}).get('col') or [])]
    fc = [fold(c) for c in cols]
    di = next((i for i, c in enumerate(fc) if c in ('DRIVER', 'NAME', 'PILOTE')), None)
    ri = next((i for i, c in enumerate(fc) if c in ('RNK', 'POS', 'CLT', 'RANK')), None)
    gi = next((i for i, c in enumerate(fc) if c in ('GAP', 'ECART', 'DIFF')), None)
    rows = (table.get('grid') or {}).get('row') or []
    rows = rows if isinstance(rows, list) else [rows]
    out = []
    for r in rows:
        col = r.get('col') or []
        col = col if isinstance(col, list) else [col]
        val = lambda i: _text(col[i]) if i is not None and i < len(col) else ''  # noqa: E731
        st_attr = fold(((r.get('@attributes') or {}).get('status')) or '')
        rank = val(ri)
        status = None
        if not rank.isdigit():
            status = {'DSQ': 'DSQ', 'DNS': 'DNS', 'DNF': 'DNF', 'DNQ': 'DNQ', 'EXC': 'DSQ'}.get(st_attr) or _status(val(gi))
        out.append({'rank': rank if rank.isdigit() else '', 'driver': val(di), 'status': status, 'cls': '',
                    'text': ' | '.join(val(i) for i in range(len(col)))})
    return out


def _fia_table(run: _Run, event_id: str, xml: str):
    data, url = _fia_get(run, 'event/results', {'eventID': event_id, 'xmlFile': xml})
    try:
        item = data['item']
        return _fia_rows(item['data']['table']), item['data'].get('pdf')
    except Exception:
        return None, None


def _fia_series(champ: str) -> str:
    m = re.search(r'FIA Karting.*', champ or '')
    return (m.group(0) if m else champ or 'FIA Karting').strip()


def _fia_candidate(run: _Run, cat: str, year: int) -> bool:
    c = fold(cat)
    if re.search(r'KZ|MASTER', c):
        return False
    if re.search(r'JUNIOR|MINI|CADET', c):
        return True
    return run.age(year) >= 14 and re.search(r'\bOK|SENIOR', c) is not None


def scan_fia(run: _Run, years=None, categories=None, event_filter=None) -> None:
    """FIA Karting: all events of the given seasons (default: previous + current).
    categories / event_filter only narrow the scan (used by the test to verify with another driver)."""
    for y in years or run.fia_years:
        data, url = _fia_get(run, 'events', {'year': y, 'limit': 100})
        if not data or 'items' not in data:
            run.log(f'FIA events list unavailable: {url}')
            continue
        for ev in data['items']:
            if event_filter and not event_filter(ev.get('eventID') or ''):
                continue
            try:
                _fia_event(run, ev, y, categories)
            except Exception as e:  # keep going with the next event
                run.log(f'FIA event {ev.get("eventID")}: {type(e).__name__}: {e}')


def _fia_event(run: _Run, ev: dict, year: int, categories) -> None:
    eid = ev.get('eventID') or ''
    dr = ev.get('dateRange') or {}
    start, end = _d(dr.get('value')), _d(dr.get('end_value'))
    if not eid or not end or end < run.since or end > run.today:
        return
    cats = ((ev.get('categories') or {}).get('items')) or []
    for c in cats:
        cat = (((c.get('category') or {}).get('item')) or {}).get('title') or ''
        champ = (((c.get('championship') or {}).get('item')) or {}).get('title') or ''
        rnd = (((c.get('round') or {}).get('item')) or {}).get('title') or ''
        if categories is not None and fold(cat) not in {fold(x) for x in categories}:
            continue
        if categories is None and not _fia_candidate(run, cat, year):
            continue
        if not c.get('hasResults') or not c.get('results'):
            continue
        key = f'fia:{eid}:{c["results"]}'
        if run.ctx.seen(key):
            continue
        # cheap pre-filter: the entry list
        if c.get('entry'):
            entry, _ = _fia_table(run, eid, c['entry'])
            if entry and not any(run.match(r['driver']) for r in entry):
                if run.settled(end):
                    run.ctx.mark(key, 'not-entered')
                continue
        idx, iurl = _fia_get(run, 'event/results', {'eventID': eid, 'xmlFile': c['results']})
        try:
            days = idx['item']['data']['table']['day']
        except Exception:
            run.log(f'FIA {eid} {cat}: results index unreadable ({iurl})')
            continue
        sessions = []
        for d in days if isinstance(days, list) else [days]:
            dd = _d(((d.get('@attributes') or {}).get('date')))
            ss = d.get('session') or []
            for s in ss if isinstance(ss, list) else [ss]:
                sessions.append({'title': s.get('title') or '', 'type': s.get('type') or '', 'status': s.get('status') or '',
                                 'xml': s.get('xmlFile') or '', 'date': dd})
        finals = [s for s in sessions if _kind(s['title']) == 'final' and s['xml']]
        if not finals:
            run.log(f'FIA {eid} {cat}: no final classification yet')
            continue
        final = finals[-1]
        rows, pdf = _fia_table(run, eid, final['xml'])
        if rows is None:
            run.log(f'FIA {eid} {cat}: final unreadable')
            continue
        hit = _find(run, rows)
        if not hit:
            if run.settled(end):
                run.ctx.mark(key, 'no-robin')
            continue
        fpos = _pos(hit)
        if not fpos:
            run.log(f'FIA {eid} {cat}: driver in final without rank/status – skipped')
            continue

        def pos_of(kind):
            out = []
            for s in sessions:
                if _kind(s['title']) == kind and s['xml']:
                    t, _ = _fia_table(run, eid, s['xml'])
                    h = _find(run, t)
                    if h and _pos(h):
                        out.append((s, _pos(h)))
            return out
        q = [s for s in sessions if _kind(s['title']) == 'quali' and s['xml']]
        quali = _pos(_find(run, _fia_table(run, eid, q[-1]['xml'])[0])) if q else None
        heats = [p for _, p in pos_of('heat')]
        sh, pf = pos_of('superheat'), pos_of('prefinal')
        prefinal = ('SH ' + sh[-1][1]) if sh else None
        if not prefinal and pf:
            n = sum(1 for s in sessions if _kind(s['title']) == 'prefinal')
            suf = _split_suffix(pf[-1][0]['title']) if n > 1 else ''
            prefinal = pf[-1][1] + (f' ({suf})' if suf else '')
        circuit = (((ev.get('circuit') or {}).get('item')) or {}).get('title') or ev.get('location') or ''
        tid, country = guess_track(circuit + ' ' + (ev.get('location') or ''), run.ctx.site)
        country = country or ((ev.get('country') or {}).get('ioc'))
        series = _fia_series(champ)
        day = final['date'] or end
        m = re.search(r'(\d+)', rnd)
        srcs = [u for u in (pdf, FIA_SITE + (ev.get('alias') or '')) if u]
        row = result_row(
            id=f'auto-{_slug(series)}-{day.isoformat()}-{_slug(cat)}', date=day.isoformat(), series=series,
            round=('R' + m.group(1)) if m else None, track=_track_name(run, tid) or circuit or None, trackId=tid,
            country=country, **{'class': cat}, quali=quali, heats='-'.join(heats) if heats else None,
            prefinal=prefinal, final=fpos, finalPos=int(fpos) if fpos.isdigit() else None,
            fieldSize=len(rows) or None, sources=srcs, confidence='high')
        run.res.results.append(row)
        run.log(f'FIA {series} {cat} {day}: final {fpos}')
        if run.settled(end):
            run.ctx.mark(key, 'robin')


# ---------------------------------------------------------------- entry point
def run(ctx: Context, match=None, parts=('rmcit', 'gf', 'fia')) -> SourceResult:
    r = None
    try:
        r = _Run(ctx, match)
        for name, fn in (('rmcit', scan_rmcit), ('gf', scan_gf), ('fia', scan_fia)):
            if name not in parts:
                continue
            try:
                fn(r)
            except Exception as e:
                r.log(f'{name} failed: {type(e).__name__}: {e}')
        r.log(f'{r.requests} HTTP requests {r.per_host}, {len(r.res.results)} result rows')
        r.res.requests = r.requests  # informational
        return r.res
    except Exception as e:
        res = r.res if r else SourceResult()
        res.log.append(f'international: crashed: {type(e).__name__}: {e}')
        return res


if __name__ == '__main__':
    from common import load_site_data
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2022-01-01')
    ap.add_argument('--state', help='JSON state file to read/update (default: fresh state)')
    a = ap.parse_args()
    st = json.loads(Path(a.state).read_text()) if a.state and Path(a.state).exists() else {}
    c = Context(site=load_site_data(), auto={}, state=st, since=datetime.strptime(a.since, '%Y-%m-%d').date())
    out = run(c)
    if a.state:
        Path(a.state).write_text(json.dumps(st, indent=1))
    print(json.dumps({'results': out.results, 'log': out.log}, ensure_ascii=False, indent=1))
