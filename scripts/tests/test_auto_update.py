#!/usr/bin/env python3
"""Offline test of scripts/auto_update.py merge logic (no network).

Fake source modules in a temp dir: one returns a curated duplicate + a genuinely new result,
news (one curated duplicate, one new) and a newer battle table; one crashes; one overruns its
time budget. Checks merging, curated-wins, replacement by a more complete row, write rules.
Exit code 1 on failure.
"""
from __future__ import annotations

import json
import sys
import tempfile
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import auto_update  # noqa: E402
import common  # noqa: E402

site = common.load_site_data()
cur = site['results'][0]            # newest curated row
cur_news = site['news'][0]

FAKE_GOOD = textwrap.dedent(f'''
    import json
    from common import SourceResult, result_row
    PHASE = json.load(open({{phase!r}}))['phase']
    def run(ctx):
        r = SourceResult()
        r.results.append(result_row(id='auto-dup', date={cur['date']!r}, series={cur['series']!r}, final='1', confidence='high'))
        new = dict(id='auto-rmc-ita-2026-10-11-mini', date='2026-10-11', series='RMC Italia', round='R8',
                   track='Franciacorta', trackId='franciacorta', country='ITA', class_='x', final='2', confidence='high')
        new.pop('class_')
        if PHASE == 2:
            new.update(quali='1', heats='1-1', fieldSize=14)
        r.results.append(result_row(**new))
        r.results.append(result_row(id='auto-bad-track', date='2026-10-20', series='Some Cup', trackId='nowhere', final='DNS'))
        r.news.append({{'date': {cur_news['date']!r}, 'headline': {cur_news['headline']!r}, 'url': {cur_news['url']!r} + '?utm=x', 'source': 'x'}})
        r.news.append({{'date': '2026-10-12', 'headline': 'Robin Räikkönen mestariksi', 'url': 'https://example.org/a', 'source': 'Test', 'lang': 'fi'}})
        r.battle = {{'series': {{'fi': 'S', 'sv': 'S', 'en': 'S'}}, 'rows': [{{'name': 'Robin Räikkönen', 'points': 600, 'robin': True}}], 'asOf': '2026-10-11'}}
        r.log.append('fake ok')
        ctx.state.setdefault('seen', {{}})['fake-doc'] = 'x'
        return r
''')
FAKE_CRASH = 'def run(ctx):\n    raise RuntimeError("boom")\n'
FAKE_SLOW = 'import time\ndef run(ctx):\n    time.sleep(8)\n    ctx.state["slow"] = 1\n    from common import SourceResult\n    return SourceResult()\n'

fails = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        fails.append(msg)


with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    src = td / 'sources'
    src.mkdir()
    phase = td / 'phase.json'
    phase.write_text('{"phase": 1}')
    (src / 'fake_good.py').write_text(FAKE_GOOD.replace('{phase!r}', repr(str(phase))), encoding='utf-8')
    (src / 'fake_crash.py').write_text(FAKE_CRASH)
    (src / 'fake_slow.py').write_text(FAKE_SLOW)
    sys.path.insert(0, str(src))
    auto_update.SOURCES_DIR = src
    auto_update.AUTO_JS = td / 'auto.js'
    auto_update.STATE_FILE = td / 'state.json'
    auto_update.GRACE = 1

    print('run 1')
    rc = auto_update.main(['--budget', '2'])
    a = common.load_js_object(td / 'auto.js', 'RR_AUTO')
    st = json.loads((td / 'state.json').read_text())
    check(rc == 0, 'exit code 0 although one source crashed and one overran')
    check([r['id'] for r in a['results']] == ['auto-bad-track', 'auto-rmc-ita-2026-10-11-mini'], 'curated duplicate dropped, new rows kept newest first')
    check('trackId' not in a['results'][0], 'unknown trackId removed')
    check(all(r['auto'] is True for r in a['results']), 'rows flagged auto')
    check([n['url'] for n in a['news']] == ['https://example.org/a'], 'curated news dropped (normalised URL), new news kept')
    check(a['battle'] and a['battle']['asOf'] == '2026-10-11', 'newer battle table used')
    check(a['updated'] and a['checked'].endswith('Z'), 'updated + checked set')
    check(st.get('seen', {}).get('fake-doc') == 'x' and 'slow' not in st, 'state kept from good source, discarded from abandoned one')
    check(any(l['source'] == 'fake_crash' and 'boom' in l['msg'] for l in a['log']), 'crash logged')
    check(any(l['source'] == 'fake_slow' and 'abandoned' in l['msg'] for l in a['log']), 'timeout logged')

    print('run 2 (same content)')
    before = (td / 'auto.js').read_text()
    (src / 'fake_crash.py').unlink()
    (src / 'fake_slow.py').unlink()
    auto_update.main([])
    check((td / 'auto.js').read_text() == before, 'auto.js not rewritten when nothing changed and checked is fresh')

    print('run 3 (more complete row)')
    phase.write_text('{"phase": 2}')
    sys.modules.pop('fake_good', None)
    auto_update.main([])
    a = common.load_js_object(td / 'auto.js', 'RR_AUTO')
    r8 = [r for r in a['results'] if r['id'] == 'auto-rmc-ita-2026-10-11-mini']
    check(len(r8) == 1 and r8[0].get('fieldSize') == 14 and r8[0].get('heats') == '1-1', 'row replaced by the more complete version')

print('OK' if not fails else f'{len(fails)} FAILED')
sys.exit(1 if fails else 0)
