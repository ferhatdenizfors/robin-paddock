"""Live test for scripts/sources/international.py (network required, ~3-4 minutes).

    .venv/bin/python scripts/tests/test_international.py

1. Cold run (fresh state, since=2022-01-01): the known international finals must parse exactly,
   and every curated RMCIT / Grand Finals row in site/data.js with a full date must be matched.
2. Warm run (same state): far fewer requests and no rows re-emitted.
3. FIA Karting: discovery + name search verified on another driver (Will Green won the Junior
   final of the 2026 FIA Karting European Championship round 1 at La Conca).
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / 'sources'))

from common import Context, fold, load_site_data  # noqa: E402
import international  # noqa: E402

KNOWN = [  # (date, series, class, final, quali, heats, prefinal)
    ('2025-07-19', 'RMC International Trophy', 'Rotax Mini MAX', '32', '27', 'DSQ-23-29', 'SH 29'),
    ('2026-07-18', 'RMC International Trophy', 'Rotax Mini MAX', '32', '2', '2-29-1', 'SH 30'),
    ('2024-10-26', 'Rotax Grand Finals', 'Rotax Micro MAX', '5', '11', '5-35', '31'),
]
SERIES = {'RMC International Trophy', 'Rotax Grand Finals', 'FIA Karting European Championship',
          'FIA Karting World Championship'}


def near(a: str, b: str, days=2) -> bool:
    try:
        return abs(date.fromisoformat(a) - date.fromisoformat(b)) <= timedelta(days=days)
    except ValueError:
        return False


def main() -> int:
    site = load_site_data()
    state: dict = {}
    fails = []

    # ---- 1. cold run
    ctx = Context(site=site, auto={}, state=state, since=date(2022, 1, 1))
    res = international.run(ctx)
    cold = res.requests
    print('\n'.join(res.log))
    rows = res.results
    for d, series, cls, final, quali, heats, pf in KNOWN:
        r = next((x for x in rows if x['date'] == d and x['series'] == series and x['class'] == cls), None)
        if not r:
            fails.append(f'missing {d} {series} {cls}')
            continue
        got = (r.get('final'), r.get('quali'), r.get('heats'), r.get('prefinal'))
        if got != (final, quali, heats, pf):
            fails.append(f'{d} {series}: got {got}, want {(final, quali, heats, pf)}')
        if r.get('confidence') != 'high' or not r.get('sources') or not r['id'].startswith('auto-'):
            fails.append(f'{d} {series}: bad metadata {r}')
    curated = [x for x in site.get('results', []) if x.get('series') in SERIES and len(x.get('date', '')) == 10
               and x['date'] >= '2022-01-01']
    for c in curated:
        r = next((x for x in rows if x['series'] == c['series'] and near(x['date'], c['date'])), None)
        if not r:
            fails.append(f'curated {c["date"]} {c["series"]} not found')
        elif r.get('final') != c.get('final') or r.get('finalPos') != c.get('finalPos'):
            fails.append(f'curated {c["date"]} {c["series"]}: final {r.get("final")} != {c.get("final")}')
    extra = [x for x in rows if not any(x['series'] == c['series'] and near(x['date'], c['date']) for c in curated)]
    for x in extra:
        print('NOTE uncurated row:', x['date'], x['series'], x.get('class'), x.get('final'))

    # ---- 2. warm run
    ctx2 = Context(site=site, auto={}, state=state, since=date(2022, 1, 1))
    res2 = international.run(ctx2)
    warm = res2.requests
    print('\n'.join(res2.log))
    if res2.results:
        fails.append(f'warm run re-emitted {len(res2.results)} rows')
    if warm >= 40 or warm >= cold:
        fails.append(f'warm run too many requests: {warm} (cold {cold})')

    # ---- 3. FIA Karting discovery + name search on a known 2026 winner
    fia_ctx = Context(site=site, auto={}, state={}, since=date(2026, 1, 1))
    run = international._Run(fia_ctx, match=lambda name: 'GREEN' in fold(name) and 'WILL' in fold(name))
    international.scan_fia(run, years=[2026], categories=['Junior'],
                           event_filter=lambda eid: 'La Conca' in eid and 'WKC' not in eid)
    print('\n'.join(run.res.log))
    g = next((x for x in run.res.results if x['date'] == '2026-04-12'), None)
    if not g or g.get('final') != '1' or g.get('series') != 'FIA Karting European Championship' \
            or g.get('round') != 'R1' or g.get('trackId') != 'la-conca':
        fails.append(f'FIA check failed: {g}')
    else:
        print('FIA ok:', g['date'], g['series'], g['round'], g['class'], 'final', g['final'],
              'quali', g.get('quali'), 'heats', g.get('heats'), g.get('prefinal'), 'field', g.get('fieldSize'))
    # and Robin himself is searched on FIA (no results expected before 2027)
    if any(x['series'].startswith('FIA') for x in rows):
        print('NOTE: FIA rows for Robin found:', [x for x in rows if x['series'].startswith('FIA')])

    print(f'\nrequests: cold={cold} warm={warm}; FIA check={run.requests}')
    print('rows:', [(x['date'], x['series'], x['class'], x['final']) for x in rows])
    if fails:
        print('\nFAIL\n  ' + '\n  '.join(fails))
        return 1
    print('\nOK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
