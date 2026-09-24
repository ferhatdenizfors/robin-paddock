"""Live regression test for scripts/sources/rotax_italia.py (needs network, ~1 min).

    .venv/bin/python scripts/tests/test_rotax_italia.py

Runs the module twice with a fresh state (cold, then warm) and since=2022-01-01, and checks the
parsed RMC Italia rows against the curated 2026 Mini results.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / 'sources'))

from common import Context, load_site_data  # noqa: E402
import rotax_italia  # noqa: E402

# date -> (round, quali, heats, prefinal, final, fieldSize)   (curated site/data.js, 2026 Rotax Mini MAX)
KNOWN = {
    '2026-03-08': ('R1', '1', '1-1', None, '1', 19),
    '2026-04-12': ('R2', '2', '5-2', None, '2', 10),
    '2026-05-17': ('R3', '3', '3-3', None, '3', 13),
    '2026-06-13': ('R4', '2', '4-2', None, '2', 14),
    '2026-07-05': ('R5', '1', '2-3', '2', '2', 13),
    '2026-09-20': ('R7', '1', '1-1', None, '1', None),   # via Info Point -> apex-timing booklet
}
# 2026-08-30 (R6, final 1): the organiser's "Mini" PDF contains the Micro booklet -> expected miss


def main() -> int:
    site = load_site_data()
    state: dict = {}
    ctx = Context(site=site, auto={}, state=state, since=date(2022, 1, 1))
    res = rotax_italia.run(ctx)
    cold = len(rotax_italia._requests)
    for line in res.log:
        print('  log:', line)
    rows = {r['date']: r for r in res.results}
    errors = []
    for d, (rnd, quali, heats, prefinal, final, field) in KNOWN.items():
        r = rows.get(d)
        if not r:
            errors.append(f'{d} {rnd}: missing')
            continue
        got = (r.get('round'), r.get('quali'), r.get('heats'), r.get('prefinal'), r.get('final'))
        exp = (rnd, quali, heats, prefinal, final)
        if got != exp:
            errors.append(f'{d}: expected {exp}, got {got}')
        if field is not None and r.get('fieldSize') != field:
            errors.append(f'{d}: fieldSize {r.get("fieldSize")} != {field}')
        if r.get('series') != 'RMC Italia' or r.get('class') != 'Rotax Mini MAX' or r.get('confidence') != 'high':
            errors.append(f'{d}: bad series/class/confidence {r.get("series")} {r.get("class")} {r.get("confidence")}')
        if r.get('finalPos') != int(final):
            errors.append(f'{d}: finalPos {r.get("finalPos")}')
    r6 = rows.get('2026-08-30')
    if r6 and r6.get('final') != '1':
        errors.append(f'2026-08-30: organiser fixed the R6 PDF but final is {r6.get("final")} (expected 1)')
    extra = sorted(set(rows) - set(KNOWN) - {'2026-08-30'})
    if extra:
        print('  NOTE rows without curated counterpart (check by hand):', extra)

    b = res.battle
    if not b or not any(x.get('robin') for x in b.get('rows', [])):
        errors.append('battle missing or without Robin')
    elif 'round 6' in b['after']['en']:
        exp = [('Kevin Turbo', 495), ('Robin Räikkönen', 480), ('Edoardo Pacchetti', 456), ('Gabriele Servello', 447)]
        got = [(x['name'], x['points']) for x in b['rows']]
        if got != exp or b.get('asOf') != '2026-08-30':
            errors.append(f'battle after R6: expected {exp} asOf 2026-08-30, got {got} asOf {b.get("asOf")}')
    else:
        print('  NOTE championship table has moved on:', b['after']['en'], [(x['name'], x['points']) for x in b['rows']])

    # warm run: same state, nothing new expected
    res2 = rotax_italia.run(ctx)
    warm = len(rotax_italia._requests)
    if res2.results:
        errors.append(f'warm run produced {len(res2.results)} rows again')
    print(f'requests: cold {cold}, warm {warm}')
    if warm > 10:
        errors.append(f'warm run too chatty: {warm} requests')

    for e in errors:
        print('FAIL', e)
    print('OK' if not errors else f'{len(errors)} failure(s)')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
