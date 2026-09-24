"""news_rss - automatic news items about Robin Räikkönen from publishers' own RSS/Atom feeds.

No AI, no scraping of article pages: only the feed's own <title>, <description>/<summary>,
<link> and <pubDate> are read. Output is headline (verbatim) + link + date + outlet; article
bodies are never copied (descriptions are only used for matching and then discarded).

Aggregator feeds (Google News, Bing News, ...) are deliberately NOT used.

Matching (on folded = upper-case ASCII text, see common.fold):
  R  = Robin form:      ROBIN, ROBININ, ROBINILLE, ROBINILTA, ROBINISTA, ROBINIA, ROBINS ...
                        (also 'ROBIN-POIKA' etc. since '-' is a word boundary)
  K  = Räikkönen form:  RAIKKONEN, RAIKKONEN'S, RAIKKONENS, RAIKONEN (typo), and every Finnish
                        inflection RAIKKOS* (RAIKKOSEN, RAIKKOSELLE, RAIKKOSELTA, RAIKKOSTA,
                        RAIKKOSESTA, RAIKKOSEEN, RAIKKOSET, RAIKKOSTEN ...)
  An item matches when
    1. the title contains R and K                      ("Robin Räikkönen ajoi voittoon!")
    2. the title contains R and the description has R next to K (<= 40 chars apart)
    3. the title contains K and the description has R next to K
                                                       ("Red Bull signs Kimi Raikkonen's son ..."
                                                        + "Robin Raikkonen, the son of ...")
    4. the title contains a 'son of Kimi' phrase (KIMI'S SON, RAIKKONEN'S SON, SON OF KIMI,
       RAIKKONEN JR, RAIKKOSEN POIKA, KIMIN POIKA, FIGLIO DI (KIMI) RAIKKONEN, RAIKKONENS SON,
       SONEN TILL KIMI ...), title+description contain K, and title+description have
       karting/junior context (KART*, ROTAX, WSK, RED BULL JUNIOR, JUNIOR TEAM, JUNIORI*,
       MINI MAX, MICRO MAX).
  Everything else that mentions K is a "near miss" (logged, not emitted).

Category (first rule that matches on the folded headline; if none, on the first 300 chars of the
description; keyword stems per language):
  injury    fi LOUKKAANTU, LOUKKAS, VAMMA, SAIRAALA, KOLARI | en INJUR, HOSPITAL | it INFORTUN,
            OSPEDALE | sv SKADA, SJUKHUS
  result    fi VOITTO/VOITTI/VOITTOON, PALKINTO, PALLI, PODIUM, FINAALI, SIJA, MESTARI, KISA,
            KILPA*, DOMINO, TAKAISKU, AJOI, POTTI, SUORITU, POKAALI, OHITT | en WIN/WINS/WON/WINNER, VICTORY, PODIUM, CHAMPION,
            TITLE, RACE, FINAL, RESULT, P1..P9, POLE | it VITTORIA, VINCE, PODIO, GARA, CAMPIONE,
            TRIONF | sv SEGER, VANN, VINNER, PALLEN, TAVLING
  signing   fi SOPIMU, ALLEKIRJOIT, JUNIORIOHJELM, JUNIORITALL, SIIRTY | en SIGN, JOINS, CONTRACT,
            JUNIOR TEAM, DEVELOPMENT PROGRAMME, ACADEMY, LINE-UP | it INGAGG, FIRMA, CONTRATTO, ENTRA |
            sv KONTRAKT, SKRIVER PA
  interview fi HAASTATTEL, PALJASTAA/PALJASTUS, PUHUU, KERTOO, AVAUTU, KOMMENTOI, SANOO | en INTERVIEW, SAYS, SPEAKS,
            REVEALS, EXCLUSIVE, TALKS | it INTERVISTA, PARLA, DICHIARA, RACCONTA | sv INTERVJU,
            BERATTAR, SAGER
  feature   fi NAKOKULMA, NAKEMYS, ANALYYS, KOLUMNI, ARVIO, ASIANTUNTIJA, MIKSI, KUKA ON | en FEATURE,
            ANALYSIS, COLUMN, OPINION, WHY, WHO IS, PROFILE, NEXT ICEMAN | it ANALISI, COMMENTO,
            CHI E | sv ANALYS, KRONIKA, VARFOR
  other     everything else
"""
from __future__ import annotations

import html
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import Context, SourceResult, fetch, fold  # noqa: E402

try:
    from zoneinfo import ZoneInfo
    HELSINKI = ZoneInfo('Europe/Helsinki')
except Exception:  # tzdata missing -> keep the feed's own offset
    HELSINKI = None

# (feed url, outlet display name, lang). Publisher feeds only.
FEEDS: list[tuple[str, str, str]] = [
    # Finnish
    ('https://www.is.fi/rss/formula1.xml', 'Ilta-Sanomat', 'fi'),
    ('https://www.is.fi/rss/urheilu.xml', 'Ilta-Sanomat', 'fi'),
    ('https://www.iltalehti.fi/rss/formulat.xml', 'Iltalehti', 'fi'),
    ('https://www.iltalehti.fi/rss/urheilu.xml', 'Iltalehti', 'fi'),
    ('https://yle.fi/rss/t/18-283219/fi', 'Yle', 'fi'),        # Yle topic "Robin Räikkönen"
    ('https://yle.fi/rss/t/18-202403/fi', 'Yle', 'fi'),        # Yle topic "Kimi Räikkönen"
    ('https://yle.fi/rss/urheilu', 'Yle', 'fi'),
    ('https://www.mtvuutiset.fi/api/feed/rss/urheilu', 'MTV Uutiset', 'fi'),
    ('https://www.hs.fi/rss/urheilu.xml', 'Helsingin Sanomat', 'fi'),
    ('https://www.suomif1.com/feed/', 'SuomiF1', 'fi'),
    # English
    ('https://www.motorsport.com/rss/f1/news/', 'Motorsport.com', 'en'),
    ('https://www.motorsport.com/rss/kart/news/', 'Motorsport.com', 'en'),
    ('https://www.racefans.net/feed/', 'RaceFans', 'en'),
    ('https://racer.com/feed/', 'RACER', 'en'),
    ('https://www.grandprix.com/rss.xml', 'GRANDPRIX.com', 'en'),
    ('https://www.vroomkart.com/rss.xml', 'Vroomkart', 'en'),
    ('https://racingnews365.com/feed/news.xml', 'RacingNews365', 'en'),
    ('https://www.gpblog.com/en/sitemap/news.xml', 'GPblog', 'en'),   # advertised by gpblog as its RSS
    ('https://www.gpfans.com/en/rss.xml', 'GPFans', 'en'),
    # Italian
    ('https://www.vroomkart.it/rss.xml', 'Vroomkart Italia', 'it'),
    ('https://it.motorsport.com/rss/kart/news/', 'Motorsport.com Italia', 'it'),
    ('https://it.motorsport.com/rss/f1/news/', 'Motorsport.com Italia', 'it'),
]

# ------------------------------------------------------------------ matching
ROBIN = r"\bROBIN(?:IN|ILLE|ILTA|ISTA|IA|IIN|ILLA|S)?\b"
RAIK = r"\bRA[IY]K{1,2}ON{1,2}ENS?\b|\bRAIKKOS[A-Z]*\b"
RE_ROBIN = re.compile(ROBIN)
RE_RAIK = re.compile(RAIK)
RE_NEAR = re.compile(r"(?:%s)[^.!?\n]{0,40}?(?:%s)|(?:%s)[^.!?\n]{0,40}?(?:%s)" % (ROBIN, RAIK, RAIK, ROBIN))
RE_SON = re.compile(
    r"KIMI(?: RAIKKONEN)?'S? (?:\d+[- ]YEAR[- ]OLD )?SON|RAIKKONEN'S? (?:\d+[- ]YEAR[- ]OLD )?SON|"
    r"SON OF (?:FORMER |F1 |WORLD |2007 )*(?:CHAMPION )?(?:KIMI|RAIKKONEN)|RAIKKONEN JR|"
    r"RAIKKOSEN (?:\w+ )?POI(?:KA|JA)|KIMIN POI(?:KA|JA)|"
    r"FIGLIO DI (?:KIMI )?RAIKKONEN|FIGLIO DI KIMI|RAIKKONENS SON|SONEN TILL (?:KIMI|RAIKKONEN)")
RE_KART_CTX = re.compile(r"KART|ROTAX|\bWSK\b|RED BULL JUNIOR|JUNIOR TEAM|JUNIORI|MINI ?MAX|MICRO ?MAX|MINI U10")

CATEGORY_RULES: list[tuple[str, str]] = [
    ('injury', r"LOUKKAANTU|LOUKKAS|\bVAMMA|SAIRAALA|KOLARI|INJUR|HOSPITAL|INFORTUN|OSPEDAL|\bSKADA|SJUKHUS"),
    ('result', r"\bVOITT?O|\bVOITTI|\bVOITTOON|PALKINTO|PALLI|PODI|FINAALI|\bSIJA|MESTAR|KILPAILU|DOMINO|"
               r"TAKAISKU|\bAJOI\b|KISA|\bKILPA|POTTI|SUORITU|POKAAL|OHITT|\bWIN(?:S|NER|NING)?\b|\bWON\b|VICTORY|CHAMPION|\bTITLE|\bRACE\b|\bFINAL\b|"
               r"RESULT|\bP[1-9]\b|\bPOLE\b|VITTORIA|\bVINCE|\bGARA\b|CAMPION|TRIONF|\bSEGER|\bVANN\b|VINNER|"
               r"PALLEN|TAVLING"),
    ('signing', r"SOPIMU|ALLEKIRJOIT|JUNIORIOHJELM|JUNIORITALL|SIIRTY|\bSIGN|\bJOINS?\b|CONTRACT|JUNIOR TEAM|"
                r"DEVELOPMENT PROGRAMME|ACADEMY|INGAGG|\bFIRMA|LINE-?UP|CONTRATT|\bENTRA\b|KONTRAKT|SKRIVER PA"),
    ('interview', r"HAASTATTEL|PALJASTA|PALJASTU|\bPUHUU|\bKERTOO|AVAUTU|KOMMENTOI|\bSANOO|INTERVIEW|\bSAYS\b|SPEAKS|REVEALS|"
                  r"EXCLUSIVE|\bTALKS?\b|INTERVISTA|\bPARLA|DICHIARA|RACCONTA|INTERVJU|BERATTAR|\bSAGER\b"),
    ('feature', r"NAKOKULMA|NAKEMYS|ANALYYS|KOLUMNI|\bARVIO|ASIANTUNTIJA|\bMIKSI\b|\bKUKA ON\b|FEATURE|ANALYSIS|COLUMN|"
                r"OPINION|\bWHY\b|\bWHO IS\b|PROFILE|NEXT ICEMAN|ANALISI|COMMENTO|\bCHI E\b|\bANALYS\b|KRONIKA|VARFOR"),
]
_CAT = [(c, re.compile(p)) for c, p in CATEGORY_RULES]

TRACKING_PARAMS = re.compile(r"^(utm_.*|origin|ref|ref_src|fbclid|gclid|mc_cid|mc_eid|cmpid|at_.*|source|"
                             r"src|share|igshid|ito|_ga|xtor|rss|feed)$", re.I)


RE_FAMILY_PRIVATE = re.compile(r"\bRIANNA")


def _norm(s: str) -> str:
    return fold(s).replace('’', "'").replace('‘', "'").replace('´', "'").replace('`', "'")


def match_reason(title: str, desc: str) -> str | None:
    """Why this item is about Robin (rule id) or None."""
    t, d = _norm(title), _norm(desc)
    # privacy: skip family-life stories that name Robin's younger sibling (not about his racing)
    if RE_FAMILY_PRIVATE.search(t):
        return None
    t_robin, t_raik = RE_ROBIN.search(t), RE_RAIK.search(t)
    d_near = RE_NEAR.search(d)
    if t_robin and t_raik:
        return 'title'
    if t_robin and d_near:
        return 'title-robin+desc'
    if t_raik and d_near:
        return 'title-raik+desc'
    if RE_SON.search(t) and RE_RAIK.search(t + ' ' + d) and RE_KART_CTX.search(t + ' ' + d):
        return 'son+karting'
    return None


def mentions_raikkonen(title: str, desc: str) -> bool:
    return RE_RAIK.search(_norm(title) + ' ' + _norm(desc)) is not None


def categorize(title: str, desc: str = '') -> str:
    t = _norm(title)
    for cat, rx in _CAT:            # the headline decides first ...
        if rx.search(t):
            return cat
    d = _norm(desc)[:300]
    for cat, rx in _CAT:            # ... then the start of the description
        if rx.search(d):
            return cat
    return 'other'


# ------------------------------------------------------------------ urls / dedupe
def clean_url(url: str) -> str:
    """Canonical article URL: https, no fragment, no tracking params."""
    url = html.unescape((url or '').strip())
    p = urllib.parse.urlsplit(url)
    if not p.scheme or not p.netloc:
        return url
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k)]
    scheme = 'https' if p.scheme in ('http', 'https') else p.scheme
    return urllib.parse.urlunsplit((scheme, p.netloc.lower(), p.path, urllib.parse.urlencode(q), ''))


def url_key(url: str) -> str:
    """Dedupe key: host without www + article id (uuid / long number) or path."""
    p = urllib.parse.urlsplit(clean_url(url))
    host = p.netloc.lower().removeprefix('www.')
    m = re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', p.path) \
        or re.search(r'(?:art-|/a/|/news/|/)(\d{2}-\d{6,}|\d{6,})(?:[/.-]|$)', p.path)
    ident = m.group(1) if m and m.groups() else (m.group(0) if m else p.path.rstrip('/').lower())
    return host + '|' + ident


def headline_key(h: str) -> str:
    return re.sub(r'[^A-Z0-9]+', ' ', _norm(h)).strip()


# ------------------------------------------------------------------ feed parsing
def _strip_html(s: str) -> str:
    s = html.unescape(s or '')
    if '&lt;' in s or '&amp;' in s:
        s = html.unescape(s)
    s = re.sub(r'<[^>]+>', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _local(tag: str) -> str:
    return tag.rsplit('}', 1)[-1].lower() if isinstance(tag, str) else ''


def parse_date(s: str | None) -> datetime | None:
    s = (s or '').strip()
    if not s:
        return None
    dt = None
    try:
        dt = parsedate_to_datetime(s)
    except Exception:
        try:
            dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        except Exception:
            m = re.match(r'(\d{4})-(\d{2})-(\d{2})', s)
            if m:
                dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 12, tzinfo=timezone.utc)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def local_day(dt: datetime) -> str:
    if HELSINKI is not None:
        dt = dt.astimezone(HELSINKI)
    return dt.strftime('%Y-%m-%d')


def _items_etree(text: str) -> list[dict]:
    root = ET.fromstring(text.encode('utf-8') if isinstance(text, str) else text)
    out = []
    for el in root.iter():
        name = _local(el.tag)
        if name not in ('item', 'entry'):
            continue
        it = {'title': '', 'desc': '', 'link': '', 'date': ''}
        for ch in el:
            n = _local(ch.tag)
            txt = ''.join(ch.itertext()) if n in ('title', 'description', 'summary') else (ch.text or '')
            if n == 'title':
                it['title'] = txt
            elif n in ('description', 'summary') and not it['desc']:
                it['desc'] = txt
            elif n == 'link':
                href = ch.get('href')
                if href and ch.get('rel', 'alternate') == 'alternate':
                    it['link'] = it['link'] or href
                elif not href and txt.strip():
                    it['link'] = txt.strip()
            elif n == 'guid' and not it['link'] and ch.get('isPermaLink', 'true') == 'true' and txt.startswith('http'):
                it['link'] = txt.strip()
            elif n in ('pubdate', 'published', 'date') or (n == 'updated' and not it['date']):
                if n != 'updated' or not it['date']:
                    it['date'] = txt.strip()
        out.append(it)
    return out


def _items_regex(text: str) -> list[dict]:
    """Fallback for feeds that are not well-formed XML."""
    out = []
    for m in re.finditer(r'<(item|entry)[\s>].*?</\1>', text, re.S | re.I):
        blk = m.group(0)

        def tag(*names):
            for n in names:
                mm = re.search(r'<%s\b[^>]*>(.*?)</%s>' % (n, n), blk, re.S | re.I)
                if mm:
                    v = mm.group(1)
                    v = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', v, flags=re.S)
                    return v
            return ''
        link = tag('link').strip()
        if not link:
            mm = re.search(r'<link[^>]*href="([^"]+)"', blk)
            link = mm.group(1) if mm else ''
        out.append({'title': tag('title'), 'desc': tag('description', 'summary'), 'link': link,
                    'date': tag('pubDate', 'published', 'dc:date', 'updated')})
    return out


def parse_feed(text: str) -> list[dict]:
    try:
        items = _items_etree(text)
    except Exception:
        items = _items_regex(text)
    for it in items:
        it['title'] = re.sub(r'\s+', ' ', html.unescape(html.unescape(it['title'] or ''))).strip()
        it['desc'] = _strip_html(it['desc'])[:1500]
        it['dt'] = parse_date(it['date'])
    return [it for it in items if it['title'] and it['link']]


# ------------------------------------------------------------------ main
def scan(ctx: Context, feeds=None, log: list[str] | None = None, stats: dict | None = None):
    """Fetch all feeds -> (matches, near_misses). Items are dicts with outlet/lang/title/desc/url/day/reason."""
    log = log if log is not None else []
    stats = stats if stats is not None else {}
    matches, near = [], []
    for url, outlet, lang in feeds or FEEDS:
        stats['requests'] = stats.get('requests', 0) + 1
        try:
            text = fetch(url, timeout=25, retries=1)
        except Exception as e:  # fetch should not raise, but be safe
            text = None
            log.append(f'news_rss: {outlet}: fetch error {e!r} ({url})')
        if not text:
            log.append(f'news_rss: feed unavailable: {url}')
            continue
        try:
            items = parse_feed(text)
        except Exception as e:
            log.append(f'news_rss: could not parse {url}: {e!r}')
            continue
        if not items:
            log.append(f'news_rss: 0 items in {url}')
        for it in items:
            if not it['dt']:
                if mentions_raikkonen(it['title'], it['desc']):
                    log.append(f'news_rss: skipped (no date) {outlet}: {it["title"]}')
                continue
            rec = {'outlet': outlet, 'lang': lang, 'title': it['title'], 'desc': it['desc'],
                   'url': clean_url(it['link']), 'day': local_day(it['dt']), 'feed': url}
            reason = match_reason(it['title'], it['desc'])
            if reason:
                rec['reason'] = reason
                matches.append(rec)
            elif mentions_raikkonen(it['title'], it['desc']):
                near.append(rec)
    return matches, near


def run(ctx: Context) -> SourceResult:
    res = SourceResult()
    try:
        _run(ctx, res)
    except Exception as e:  # never raise
        res.log.append(f'news_rss: unexpected error {e!r}')
    return res


def _run(ctx: Context, res: SourceResult) -> None:
    since = ctx.since.isoformat() if ctx.since else None
    stats: dict = {}
    matches, near = scan(ctx, log=res.log, stats=stats)

    # everything we already have: curated news, previously auto-published news
    known_urls, known_heads = set(), set()
    for n in (ctx.site.get('news') or []) + ((ctx.auto or {}).get('news') or []):
        if n.get('url'):
            known_urls.add(url_key(n['url']))
        if n.get('headline'):
            known_heads.add(headline_key(n['headline']))

    matches.sort(key=lambda r: r['day'], reverse=True)
    for r in matches:
        if since and r['day'] < since:
            continue
        uk, hk = url_key(r['url']), headline_key(r['title'])
        state_key = 'news:' + uk
        if uk in known_urls or hk in known_heads:
            continue
        if ctx.seen(state_key):
            continue
        known_urls.add(uk)
        known_heads.add(hk)
        res.news.append({
            'date': r['day'], 'headline': r['title'], 'source': r['outlet'], 'url': r['url'],
            'lang': r['lang'], 'category': categorize(r['title'], r['desc']), 'importance': 3, 'auto': True,
        })
        if not ctx.dry_run:
            ctx.mark(state_key, r['day'])

    # near misses: only recent ones, each logged once
    horizon = (ctx.today - timedelta(days=3)).isoformat()
    for r in near:
        if r['day'] < horizon or (since and r['day'] < since):
            continue
        k = 'news-near:' + url_key(r['url'])
        if ctx.seen(k):
            continue
        res.log.append(f'news_rss: near-miss (mentions Räikkönen, not Robin) {r["day"]} {r["outlet"]}: {r["title"]}')
        if not ctx.dry_run:
            ctx.mark(k, r['day'])
    _prune(ctx)
    res.log.append(f'news_rss: {stats.get("requests", 0)} feeds requested, {len(matches)} Robin items in feeds, '
                   f'{len(res.news)} new')


def _prune(ctx: Context, keep_days: int = 400) -> None:
    """Drop near-miss markers older than ~a year so the state file does not grow forever."""
    seen = ctx.state.get('seen') or {}
    cutoff = (ctx.today - timedelta(days=keep_days)).isoformat()
    for k in [k for k, v in seen.items() if k.startswith('news-near:') and isinstance(v, str) and v < cutoff]:
        del seen[k]


if __name__ == '__main__':  # manual check: python scripts/sources/news_rss.py
    from common import load_site_data
    c = Context(site=load_site_data(), auto={}, state={}, since=date(2022, 1, 1), dry_run=True)
    r = run(c)
    for n in r.news:
        print(n['date'], n['source'], n['category'], '|', n['headline'], '|', n['url'])
    print('\n'.join(r.log))
