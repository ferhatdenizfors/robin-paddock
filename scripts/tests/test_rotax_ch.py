"""Live test for scripts/sources/rotax_ch.py (Rotax Max Challenge Switzerland).

    .venv/bin/python scripts/tests/test_rotax_ch.py

Runs the module twice against www.rotaxmax.ch with a fresh state and since=2022-01-01:
  1. cold run  - must reproduce every curated Swiss RMC final in site/data.js
  2. warm run  - same state: must download nothing new (index + season pages only)
Prints an expected-vs-parsed table and the request counts. Exit code 1 on failure.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / 'sources'))

import common  # noqa: E402
import rotax_ch  # noqa: E402

# (date, round, class, final, finalPos, quali, heats, fieldSize) - checked by hand against the PDFs
KNOWN = [
    ('2022-10-01', 'R6', 'Rotax Micro MAX', '15', 15, '9', '11-17', 18),
    ('2023-04-23', 'R1', 'Rotax Micro MAX', '3', 3, '3', '2-2', 7),
    ('2023-05-29', 'R2', 'Rotax Micro MAX', '8', 8, '2', '1-2', 10),
    ('2023-06-11', 'R3', 'Rotax Micro MAX', '3', 3, '4', '3-2', 9),
    ('2023-07-09', 'R4', 'Rotax Micro MAX', '2', 2, '3', '2-2', 8),
    ('2023-08-13', 'R5', 'Rotax Micro MAX', '3', 3, '4', '3-2', 7),
    ('2023-09-23', 'R6', 'Rotax Micro MAX', '2', 2, '4', '3-2', 7),
    ('2024-04-01', 'R1', 'Rotax Micro MAX', '1', 1, '4', '1-1', 11),
    ('2024-05-05', 'R2', 'Rotax Micro MAX', '2', 2, '3', '1-1', 11),
    ('2024-06-02', 'R3', 'Rotax Micro MAX', '2', 2, '1', '1-1', 10),
    ('2024-07-14', 'R4', 'Rotax Micro MAX', '1', 1, '1', '1-1', 12),
    ('2024-08-11', 'R5', 'Rotax Micro MAX', '1', 1, '1', '1-1', 11),
    ('2024-09-28', 'R6', 'Rotax Micro MAX', '3', 3, '1', '1-1', 11),
    ('2025-05-18', 'R3', 'Rotax Mini MAX', '1', 1, '1', '1-1', 14),
    ('2025-10-11', 'R6', 'Rotax Mini MAX', '2', 2, '1', '1-2', 12),
    ('2026-05-10', 'R2', 'Rotax Mini MAX', '1', 1, '1', '1-1', 16),
    ('2026-06-21', 'R3', 'Rotax Mini MAX', '1', 1, '1', '1-1', 18),
]
FIELDS = ('date', 'round', 'class', 'final', 'finalPos', 'quali', 'heats', 'fieldSize')


def near(a: str, b: str, days: int = 2) -> bool:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days) <= days


def main() -> int:
    site = common.load_site_data()
    state: dict = {}
    ctx = common.Context(site=site, auto={}, state=state, since=date(2022, 1, 1))
    cold = rotax_ch.run(ctx)
    cold_requests = len(rotax_ch._requests)
    warm = rotax_ch.run(common.Context(site=site, auto={}, state=state, since=date(2022, 1, 1)))
    warm_requests = len(rotax_ch._requests)
    nightly = rotax_ch.run(common.Context(site=site, auto={}, state=state))   # default: prev + current season
    nightly_requests = len(rotax_ch._requests)

    fails: list[str] = []
    rows = cold.results
    got = {(r['date'], r['class']): r for r in rows}

    # 1. hard-coded known finals
    for k in KNOWN:
        exp = dict(zip(FIELDS, k))
        r = got.get((exp['date'], exp['class']))
        if not r:
            fails.append(f'missing {exp["date"]} {exp["round"]} {exp["class"]}')
            continue
        for f in FIELDS:
            if r.get(f) != exp[f]:
                fails.append(f'{exp["date"]} {f}: expected {exp[f]!r}, parsed {r.get(f)!r}')
        if r.get('series') != rotax_ch.SERIES or r.get('confidence') != 'high' or not r.get('sources'):
            fails.append(f'{exp["date"]}: bad series/confidence/sources')
        if not r.get('trackId') or not r.get('country'):
            fails.append(f'{exp["date"]}: track not resolved ({r.get("track")!r})')

    # 2. comparison with curated site data (same series, date +-2 days)
    curated = [c for c in site.get('results', []) if c.get('series') == rotax_ch.SERIES and c['date'] >= '2022-01-01']
    print(f'{"curated":<34} | {"parsed":<44} | ok')
    print('-' * 86)
    matched = set()
    for c in sorted(curated, key=lambda c: c['date']):
        r = next((r for r in rows if near(r['date'], c['date'])), None)
        cs = f'{c["date"]} {c["round"]:<3} {c["class"][6:11]:<5} F{c["final"]:<3}'
        if not r:
            print(f'{cs:<34} | {"-- missing --":<44} | NO')
            fails.append(f'curated {c["date"]} not parsed')
            continue
        matched.add(r['id'])
        rs = f'{r["date"]} {r.get("round", ""):<3} {r["class"][6:11]:<5} F{r["final"]:<3} Q{r.get("quali", "-")} H{r.get("heats", "-")} n={r.get("fieldSize")}'
        ok = (r['final'] == c['final'] and r['finalPos'] == c['finalPos'] and r['round'] == c['round']
              and r['class'] == c['class'] and near(r['date'], c['date']))
        print(f'{cs:<34} | {rs:<44} | {"yes" if ok else "NO"}')
        if not ok:
            fails.append(f'curated {c["date"]} differs from parsed {r["date"]}')
    extra = [r for r in rows if r['id'] not in matched]
    for r in extra:
        print(f'{"(no curated row)":<34} | {r["date"]} {r.get("round")} {r["class"]} F{r["final"]}')

    # 3. politeness / incremental behaviour
    print(f'\nrequests: cold {cold_requests}, warm {warm_requests}, nightly(prev+current season) {nightly_requests}')
    if warm.results or nightly.results:
        fails.append('warm run re-emitted rows (state not respected)')
    if warm_requests > 10 or nightly_requests > 5:
        fails.append('warm run too many requests')

    for line in cold.log:
        if 'final' not in line.split(':')[-1]:
            print('log:', line)
    if fails:
        print('\nFAIL\n  ' + '\n  '.join(fails))
        return 1
    print(f'\nOK - {len(KNOWN)} known finals reproduced, {len(extra)} extra rows')
    return 0


if __name__ == '__main__':
    sys.exit(main())
