"""Live test for scripts/sources/wsk.py (WSK Promotion results site: WSK series + RMC Euro Trophy).

    .venv/bin/python scripts/tests/test_wsk.py            # cold + warm run (cold takes ~10-15 min)
    .venv/bin/python scripts/tests/test_wsk.py state.json # reuse/keep a state file (fast when warm)

Runs the module with since=2022-01-01, then a second (warm) time on the same state, and asserts every
curated WSK / RMC Euro Trophy result in site/data.js.  Exit code 1 on failure.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / 'sources'))

import common  # noqa: E402
import wsk  # noqa: E402

# (date, series, round, class, final, finalPos, quali, heats, prefinal) - checked by hand against the PDFs
KNOWN = [
    ('2024-09-08', 'WSK Super Cup', None, 'Mini U10', '28', 28, '14', '20-16-11', '15 (A)'),
    ('2025-02-09', 'WSK Super Master Series', 'R2', 'Mini U10', '14', 14, '7', '2-3-15', '25'),
    ('2025-02-23', 'WSK Super Master Series', 'R3', 'Mini U10', 'DNQ', None, '18', '25-16-19', '18 (B)'),
    ('2025-03-09', 'WSK Super Master Series', 'R4', 'Mini U10', 'DNS', None, '10', '4-19-25', 'DNS'),
    ('2025-09-07', 'RMC Euro Trophy', 'R4', 'Rotax Mini MAX', 'DNS', None, '13', '27-DNS-DNS', 'DNS'),
    ('2026-03-15', 'RMC Euro Trophy', 'R1', 'Rotax Mini MAX', '27', 27, '2', '2-1-8', '2'),
    ('2026-05-03', 'RMC Euro Trophy', 'R2', 'Rotax Mini MAX', '23', 23, '11', '2-8-9', '8'),
]
TRACKS = {'2024-09-08': 'franciacorta', '2025-02-09': 'sarno', '2025-02-23': 'lonato', '2025-03-09': 'franciacorta',
          '2025-09-07': 'sarno', '2026-03-15': 'cremona', '2026-05-03': 'wackersdorf'}
FIELDS = ('date', 'series', 'round', 'class', 'final', 'finalPos', 'quali', 'heats', 'prefinal')


def near(a: str, b: str, days: int = 2) -> bool:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days) <= days


def main() -> int:
    state_file = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    state = json.loads(state_file.read_text()) if state_file and state_file.exists() else {}
    site = common.load_site_data()
    since = date(2022, 1, 1)

    n0 = wsk.request_count()
    first = wsk.run(common.Context(site=site, auto={}, state=state, since=since))
    n1 = wsk.request_count()
    second = wsk.run(common.Context(site=site, auto={}, state=state, since=since))
    n2 = wsk.request_count()
    if state_file:
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=1))

    print('\n'.join(line for line in first.log if 'SKIPPED' in line or 'error' in line or 'final' in line))
    print(f'\nrequests: first run {n1 - n0}, second (warm) run {n2 - n1}\n')

    rows = second.results
    ok = True
    hdr = '%-10s %-24s %-5s %-15s %-9s %-9s %-10s' % ('date', 'series', 'rnd', 'class', 'expected', 'parsed', 'status')
    print(hdr)
    print('-' * len(hdr))
    matched = set()
    for exp in KNOWN:
        e = dict(zip(FIELDS, exp))
        cand = [r for r in rows if r['series'] == e['series'] and near(r['date'], e['date']) and r['class'] == e['class']]
        r = cand[0] if cand else None
        if r:
            matched.add(r['id'])
        bad = []
        if not r:
            bad.append('missing')
        else:
            for f in FIELDS[2:]:
                if r.get(f) != e[f]:
                    bad.append(f'{f}={r.get(f)!r}')
            if r.get('trackId') != TRACKS[e['date']]:
                bad.append(f'trackId={r.get("trackId")!r}')
            if r.get('confidence') != 'high':
                bad.append('confidence')
        ok &= not bad
        print('%-10s %-24s %-5s %-15s %-9s %-9s %s' % (e['date'], e['series'][:24], e['round'] or '-', e['class'],
                                                       e['final'], r['final'] if r else '-', 'OK' if not bad else ', '.join(bad)))
    r1 = next((r for r in rows if r['date'] == '2026-03-15'), None)
    if not r1 or 'Fastest lap' not in json.dumps(r1.get('note', {})):
        print('2026-03-15: fastest lap in the final not detected')
        ok = False
    extra = [r for r in rows if r['id'] not in matched]
    print('\nrows without a KNOWN counterpart:')
    for r in extra:
        print('  ', r['date'], r['series'], r.get('round'), r['class'], 'final', r['final'], r.get('sources', [''])[0])
    if not first.results:
        ok = False
    if n2 - n1 > 15:
        print('warm run used too many requests')
        ok = False
    print('\nPASS' if ok else '\nFAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
