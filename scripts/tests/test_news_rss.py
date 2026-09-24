"""Test for scripts/sources/news_rss.py.

    .venv/bin/python scripts/tests/test_news_rss.py            # offline rules + live feeds
    .venv/bin/python scripts/tests/test_news_rss.py --offline  # rules only (no network)

Offline part: the matching rules must accept every curated Robin headline that names him (and the
'Kimi's son' + karting ones), must reject look-alikes, URL/headline normalisation must dedupe.
Live part: runs the module twice with since=2022-01-01 on a fresh state and checks the output is
well formed, contains no duplicates of the curated news, and that a warm second run adds nothing.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'scripts' / 'sources'))

import common  # noqa: E402
import news_rss  # noqa: E402
from common import Context, load_site_data  # noqa: E402

SHOULD_MATCH = [  # (title, description)
    ('Robin Räikkönen ajoi voittoon!', ''),
    ('Robin Räikköselle sopimus Red Bullin juniorikuljettajien ohjelmaan', ''),
    ('Nyt puhuu Max Verstappen – Avautui Viaplaylle Robin Räikkösestä: ”Loppujen lopuksi”', ''),
    ('Mika Salolta kova arvio: Tämän miehen lähtö helpottaa Robin Räikköstä', ''),
    ('Robin Räikköseltä, 8, hieno suoritus', ''),
    ('Minttu Räikkönen jakoi kuvan loukkaantuneesta Robin-pojasta', ''),
    ('Kimi Räikkönen tunnelmoi: ”Hyviä hetkiä Robinin kanssa”', ''),
    ('Paljastus Räikkösten sisäpiiristä: tällainen on Robin, 11', ''),
    ('Red Bull signs Kimi Raikkonen’s son as junior driver',
     'Robin Raikkonen, the son of 2007 Formula 1 world champion Kimi, has been signed to the Red Bull Junior Team.'),
    ('Kimi Raikkonen\'s son makes karting debut', ''),
    ('Red Bull set to sign Kimi Raikkonen\'s son to junior programme', 'The Finn races in karting.'),
    ('Il figlio di Kimi Raikkonen vince nel karting', 'Rotax Mini MAX'),
    ('Robin Raikkonen takes dominant win at Franciacorta', ''),
]
SHOULD_NOT_MATCH = [
    ('Kimi Räikkösen ex-pomo joutuu oikeuteen', 'Entinen tallipäällikkö.'),
    ('Robin Frijns wins in Formula E', 'Dutch driver Robin Frijns.'),
    ('Oscar Piastri saavutti jo Kimi Räikkösen', ''),
    ('Red Bull signing also inspires Valtteri Bottas to join the effort',
     "Valtteri Bottas is excited about Kimi Raikkonen's son, Robin Raikkonen, joining."),  # title names neither
    ('Kimi Räikkösen poika jätettävä rauhaan', 'Kolumni lapsista.'),  # son phrase but no karting context
    ('Kimi Antonelli wins in Baku', 'Robin and Raikkonen fans ...'),
]
CATEGORY = {
    'Robin Räikkönen ajoi voittoon!': 'result',
    'Robin Räikköselle sopimus Red Bullin juniorikuljettajien ohjelmaan': 'signing',
    'Minttu Räikkönen jakoi kuvan loukkaantuneesta Robin-pojasta': 'injury',
    'HBL: Robin Räikkönen, 9, antoi harvinaisen haastattelun': 'interview',
    'Näkökulma: Miksi Robin Räikkösestä ei enää saisi sanoa totuutta ääneen?': 'feature',
    'Kuva Robin Räikkösestä ja Toto Wolffista leviää': 'other',
    'Robin Raikkonen takes dominant win at Franciacorta': 'result',
}


def offline() -> None:
    for t, d in SHOULD_MATCH:
        assert news_rss.match_reason(t, d), f'should match: {t}'
    for t, d in SHOULD_NOT_MATCH:
        assert not news_rss.match_reason(t, d), f'should NOT match: {t}'
    for t, c in CATEGORY.items():
        assert news_rss.categorize(t) == c, f'category {t!r}: {news_rss.categorize(t)} != {c}'
    u = 'https://www.iltalehti.fi/formulat/a/1f4c3cdf-f216-46c8-9c5f-5591c49566fb?utm_source=rss#x'
    assert news_rss.clean_url(u) == 'https://www.iltalehti.fi/formulat/a/1f4c3cdf-f216-46c8-9c5f-5591c49566fb'
    assert news_rss.clean_url('https://yle.fi/a/74-20245360?origin=rss') == 'https://yle.fi/a/74-20245360'
    assert news_rss.url_key('https://www.is.fi/urheilu/art-2000011998891.html') == \
        news_rss.url_key('https://is.fi/formula1/art-2000011998891.html')
    assert news_rss.headline_key('Robin Räikkönen: Taas voitto!') == news_rss.headline_key('Robin Raikkonen – taas VOITTO')
    # every curated headline that names Robin is accepted by the title rule
    site = load_site_data()
    missed = [n['headline'] for n in site.get('news', [])
              if 'ROBIN' in common.fold(n['headline']) and 'RAIKK' in common.fold(n['headline'])
              and not news_rss.match_reason(n['headline'], '')]
    assert not missed, f'curated headlines not matched: {missed}'
    print('offline: OK')


def live() -> None:
    site = load_site_data()
    calls = [0]
    real = common.fetch

    def counting(*a, **k):
        calls[0] += 1
        return real(*a, **k)
    news_rss.fetch = counting
    ctx = Context(site=site, auto={}, state={}, since=date(2022, 1, 1))
    r1 = news_rss.run(ctx)
    cold = calls[0]
    print(f'live run 1: {len(r1.news)} new items, {cold} requests')
    for n in r1.news:
        print(f"  {n['date']} {n['source']:14} {n['category']:9} {n['headline'][:80]} | {n['url']}")
    unavailable = [ln for ln in r1.log if 'unavailable' in ln or 'could not parse' in ln]
    for ln in unavailable:
        print('  ', ln)
    assert len(unavailable) <= len(news_rss.FEEDS) // 3, 'too many feeds failing'
    curated_urls = {news_rss.url_key(n['url']) for n in site.get('news', []) if n.get('url')}
    curated_heads = {news_rss.headline_key(n['headline']) for n in site.get('news', [])}
    seen_u, seen_h = set(), set()
    for n in r1.news:
        assert set(n) == {'date', 'headline', 'source', 'url', 'lang', 'category', 'importance', 'auto'}, n
        assert len(n['date']) == 10 and n['date'] >= '2022-01-01', n
        assert n['category'] in ('result', 'signing', 'injury', 'interview', 'feature', 'other'), n
        assert n['lang'] in ('fi', 'en', 'it', 'sv') and n['auto'] is True and n['importance'] == 3
        assert 'utm_' not in n['url'] and 'origin=rss' not in n['url'], n['url']
        assert news_rss.match_reason(n['headline'], '') or 'RAIKK' in common.fold(n['headline']), n
        uk, hk = news_rss.url_key(n['url']), news_rss.headline_key(n['headline'])
        assert uk not in curated_urls and hk not in curated_heads, f'duplicate of curated: {n}'
        assert uk not in seen_u and hk not in seen_h, f'duplicate in output: {n}'
        seen_u.add(uk)
        seen_h.add(hk)
    calls[0] = 0
    r2 = news_rss.run(ctx)
    print(f'live run 2 (warm): {len(r2.news)} new items, {calls[0]} requests')
    assert r2.news == [], 'warm run must not re-emit items'
    assert calls[0] <= 40
    print('live: OK')


if __name__ == '__main__':
    offline()
    if '--offline' not in sys.argv:
        live()
