#!/usr/bin/env python3
"""Nightly automatic update of site/auto.js (window.RR_AUTO) - plain Python, no AI.

    python scripts/auto_update.py                 # normal nightly run
    python scripts/auto_update.py --dry-run       # show what would change, write nothing
    python scripts/auto_update.py --only wsk      # run one source module
    python scripts/auto_update.py --since 2025-01-01
    python scripts/auto_update.py --backfill      # fresh state, since 2022-01-01 (re-reads all history)

Every module in scripts/sources/ exposes run(ctx) -> SourceResult. Each one runs in its own
thread with a time budget and a private copy of the state; a crash or timeout only loses that
source's work for this night. The curated site/data.js always wins: auto.js only carries news,
results and standings that are not already in the curated data.

Exit code 0 even when sources fail (they are logged); non-zero only on programming errors.
"""
from __future__ import annotations

import argparse
import copy
import importlib
import json
import logging
import re
import sys
import threading
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / 'sources'))

import common  # noqa: E402
from common import AUTO_JS, STATE_FILE, Context, SourceResult, load_js_object, load_site_data, now_utc  # noqa: E402

SOURCES_DIR = HERE / 'sources'
logging.getLogger('pypdf').setLevel(logging.ERROR)   # 'Rotated text discovered' etc.
BACKFILL_SINCE = date(2022, 1, 1)
BUDGET_NORMAL = 600          # seconds per source on a nightly run
BUDGET_BACKFILL = 1800       # seconds per source on a backfill
GRACE = 90                   # after the budget, fetch() returns None; the module gets this long to wrap up
MAX_NEWS = 150
MAX_LOG = 30
REWRITE_AFTER_DAYS = 6       # rewrite auto.js at least this often (keeps the scheduled workflow alive)
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

RESULT_KEYS = ['id', 'date', 'year', 'series', 'round', 'track', 'trackId', 'country', 'class', 'team',
               'chassis', 'engine', 'quali', 'heats', 'prefinal', 'final', 'finalPos', 'champ', 'fieldSize',
               'entries', 'note', 'sources', 'confidence', 'auto']
NEWS_KEYS = ['date', 'headline', 'source', 'url', 'lang', 'category', 'importance', 'summary', 'auto']
BATTLE_KEYS = ['series', 'after', 'rows', 'asOf', 'decider', 'note', 'sources']


# ---------------------------------------------------------------- helpers
def ordered(d: dict, keys: list[str]) -> dict:
    out = {k: d[k] for k in keys if k in d}
    for k in sorted(d):
        if k not in out:
            out[k] = d[k]
    return out


def norm_url(u) -> str:   # same rule as normUrl() in site/index.html
    s = re.sub(r'^https?://(www\.)?', '', str(u or ''), flags=re.I)
    return re.sub(r'[?#].*$', '', s).rstrip('/').lower()


def norm_head(h) -> str:  # same rule as normHead() in site/index.html
    return re.sub(r'[^a-z0-9äöåü]+', '', str(h or '').lower())


def parse_day(s) -> date | None:
    m = re.match(r'^(\d{4})-(\d{2})(?:-(\d{2}))?', str(s or ''))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))
    except ValueError:
        return None


def same_event(a: dict, b: dict) -> bool:
    """Same rule as sameEvent() in site/index.html: date within 2 days and same series or track."""
    da, db = parse_day(a.get('date')), parse_day(b.get('date'))
    if not da or not db or abs((da - db).days) > 2:
        return False
    if a.get('trackId') and a.get('trackId') == b.get('trackId'):
        return True
    return str(a.get('series') or '').lower() == str(b.get('series') or '').lower()


def completeness(r: dict) -> int:
    return sum(1 for v in r.values() if v not in (None, '', [], {}))


def as_json(v) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------- sanitising
def clean_result(r, track_ids: set, notes: list, src: str) -> dict | None:
    if not isinstance(r, dict):
        return None
    r = dict(r)
    if not DATE_RE.match(str(r.get('date', ''))) or not r.get('id') or not r.get('series') or not str(r.get('final', '')).strip():
        notes.append((src, f'row dropped (missing id/date/series/final): {str(r)[:160]}'))
        return None
    if r.get('trackId') and r['trackId'] not in track_ids:
        notes.append((src, f'{r["id"]}: unknown trackId {r["trackId"]!r} removed'))
        r.pop('trackId')
    fp = r.get('finalPos')
    if fp is not None and not isinstance(fp, (int, float)) or isinstance(fp, bool):
        r['finalPos'] = common.pos_num(fp)
    if 'finalPos' not in r:
        r['finalPos'] = common.pos_num(r.get('final'))
    r.setdefault('year', int(r['date'][:4]))
    r['auto'] = True
    return ordered(r, RESULT_KEYS)


def clean_news(n, notes: list, src: str) -> dict | None:
    if not isinstance(n, dict):
        return None
    if not DATE_RE.match(str(n.get('date', ''))[:10]) or not str(n.get('url', '')).startswith('http') or not n.get('headline'):
        notes.append((src, f'news item dropped (bad date/url/headline): {str(n)[:160]}'))
        return None
    n = dict(n)
    n['date'] = str(n['date'])[:10]
    n['auto'] = True
    return ordered(n, NEWS_KEYS)


def valid_battle(b) -> bool:
    return (isinstance(b, dict) and isinstance(b.get('rows'), list) and len(b['rows']) > 0
            and all(isinstance(x, dict) and x.get('name') and isinstance(x.get('points'), (int, float)) for x in b['rows']))


def battle_rows_key(b: dict) -> str:
    return as_json([[x.get('name'), x.get('points')] for x in b.get('rows', [])])


# ---------------------------------------------------------------- running sources
def discover_modules(only: str | None) -> list[str]:
    names = sorted(p.stem for p in SOURCES_DIR.glob('*.py') if not p.stem.startswith('_'))
    if only:
        wanted = [x.strip() for x in only.split(',') if x.strip()]
        missing = [w for w in wanted if w not in names]
        if missing:
            raise SystemExit(f'unknown source module(s): {", ".join(missing)} (available: {", ".join(names)})')
        names = [n for n in names if n in wanted]
    return names


def run_source(name: str, ctx: Context, budget: int) -> tuple[SourceResult | None, str, float, int]:
    """Returns (result or None, status, seconds, requests)."""
    box: dict = {}
    req0 = common.request_total()
    t0 = time.time()

    def target():
        common.set_deadline(time.time() + budget)
        try:
            mod = importlib.import_module(name)
            box['res'] = mod.run(ctx)
        except BaseException as e:  # module broke its contract or failed to import
            box['err'] = f'{type(e).__name__}: {e}'
            box['tb'] = traceback.format_exc(limit=4)

    th = threading.Thread(target=target, name=f'source-{name}', daemon=True)
    th.start()
    th.join(budget + GRACE)
    secs = time.time() - t0
    reqs = common.request_total() - req0
    if th.is_alive():
        return None, f'abandoned after {int(secs)} s (time budget {budget} s)', secs, reqs
    if 'err' in box:
        return None, 'crashed: ' + box['err'], secs, reqs
    res = box.get('res')
    if not isinstance(res, SourceResult):
        return None, f'run() returned {type(res).__name__}, not SourceResult', secs, reqs
    status = 'ok' if secs <= budget else f'ok (time budget {budget} s reached - partial run)'
    return res, status, secs, reqs


def merge_state(base: dict, sub: dict) -> dict:
    """Merge a module's state (from a fresh start) into the existing state (used for --backfill --only)."""
    out = copy.deepcopy(base)
    for k, v in sub.items():
        if k == 'seen' and isinstance(v, dict):
            out.setdefault('seen', {}).update(v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Nightly automatic update of site/auto.js (no AI).')
    ap.add_argument('--since', help='only look at events after YYYY-MM-DD')
    ap.add_argument('--dry-run', action='store_true', help='write nothing, show what would change')
    ap.add_argument('--only', help='run only this source module (comma-separated list allowed)')
    ap.add_argument('--backfill', action='store_true', help=f'fresh state, since {BACKFILL_SINCE}')
    ap.add_argument('--budget', type=int, help='time budget per source in seconds')
    ap.add_argument('-v', '--verbose', action='store_true', help='print every module log line')
    args = ap.parse_args(argv)

    t_start = time.time()
    today = datetime.now(timezone.utc).date()
    since = date.fromisoformat(args.since) if args.since else (BACKFILL_SINCE if args.backfill else None)
    budget = args.budget or (BUDGET_BACKFILL if args.backfill else BUDGET_NORMAL)

    site = load_site_data()
    if not site:
        raise SystemExit('site/data.js could not be read')
    prev = load_js_object(AUTO_JS, 'RR_AUTO') if AUTO_JS.exists() else {}
    prev = prev if isinstance(prev, dict) else {}
    old_state = json.loads(STATE_FILE.read_text(encoding='utf-8')) if STATE_FILE.exists() else {}
    state = {} if args.backfill else copy.deepcopy(old_state)

    track_ids = {t.get('id') for t in site.get('tracks', [])}
    notes: list[tuple[str, str]] = []      # (source, message) for the RR_AUTO log
    summary: list[str] = []
    new_news: list[dict] = []
    new_results: list[dict] = []
    battles: list[dict] = []

    for name in discover_modules(args.only):
        ctx = Context(site=site, auto=prev, state=copy.deepcopy(state), today=today, since=since, dry_run=args.dry_run)
        res, status, secs, reqs = run_source(name, ctx, budget)
        if res is None:
            notes.append((name, status))
            summary.append(f'  {name:<14} FAILED  {status}  ({reqs} requests, {secs:.0f} s)')
            continue
        state = ctx.state
        n_news = n_res = 0
        for n in res.news or []:
            c = clean_news(n, notes, name)
            if c:
                new_news.append(c)
                n_news += 1
        for r in res.results or []:
            c = clean_result(r, track_ids, notes, name)
            if c:
                new_results.append(c)
                n_res += 1
        if res.battle is not None:
            if valid_battle(res.battle):
                battles.append(ordered(res.battle, BATTLE_KEYS))
            else:
                notes.append((name, 'battle table dropped (no rows with name + points)'))
        lines = [str(x) for x in (res.log or [])]
        if args.verbose:
            for ln in lines:
                print(f'    [{name}] {ln}')
        for ln in lines[-6:]:
            notes.append((name, ln[:300]))
        line = (f'{status}: {reqs} requests, {secs:.0f} s, {n_news} news, {n_res} result rows'
                + (', battle table' if res.battle else ''))
        notes.append((name, line))
        summary.append(f'  {name:<14} {line}')

    if args.backfill and args.only:
        state = merge_state(old_state, state)

    # ---------------- news
    cur_urls = {norm_url(n.get('url')) for n in site.get('news', [])}
    cur_heads = {norm_head(n.get('headline')) for n in site.get('news', [])}
    news, seen_u, seen_h = [], set(), set()
    added_news = 0
    for n in [x for x in prev.get('news', []) if isinstance(x, dict)] + new_news:
        u, h = norm_url(n.get('url')), norm_head(n.get('headline'))
        if u in cur_urls or h in cur_heads or u in seen_u or h in seen_h:
            continue
        seen_u.add(u)
        seen_h.add(h)
        news.append(n)
    prev_news_urls = {norm_url(n.get('url')) for n in prev.get('news', []) if isinstance(n, dict)}
    news.sort(key=lambda n: str(n.get('date', '')), reverse=True)
    news = news[:MAX_NEWS]
    added_news = sum(1 for n in news if norm_url(n.get('url')) not in prev_news_urls)

    # ---------------- results
    curated = [r for r in site.get('results', []) if isinstance(r, dict)]
    results = []
    for r in prev.get('results', []):
        if isinstance(r, dict) and not any(same_event(c, r) for c in curated):
            results.append(r)
    added_res = replaced_res = skipped_curated = 0
    for r in new_results:
        if any(same_event(c, r) for c in curated):
            skipped_curated += 1
            continue
        idx = next((i for i, o in enumerate(results) if o.get('id') == r['id']), None)
        if idx is None:
            idx = next((i for i, o in enumerate(results) if same_event(o, r)), None)
        if idx is None:
            results.append(r)
            added_res += 1
        elif as_json(results[idx]) != as_json(r) and (results[idx].get('id') == r['id'] or completeness(r) >= completeness(results[idx])):
            results[idx] = r
            replaced_res += 1
    results.sort(key=lambda r: (str(r.get('date', '')), str(r.get('id', ''))), reverse=True)

    # ---------------- battle
    battle = prev.get('battle') if valid_battle(prev.get('battle')) else None
    cb = site.get('battle') if isinstance(site.get('battle'), dict) else None
    if battles:
        cand = max(battles, key=lambda b: str(b.get('asOf') or ''))
        if cb and str(cand.get('asOf') or '') < str(cb.get('asOf') or ''):
            notes.append(('merge', f'battle table as of {cand.get("asOf")} is older than the curated one ({cb.get("asOf")}) - not used'))
            battle = None
        elif cb and str(cand.get('asOf') or '') == str(cb.get('asOf') or '') and battle_rows_key(cand) == battle_rows_key(cb):
            battle = None   # identical to the curated table: keep the curated one (it has a hand-written note)
        elif battle and str(battle.get('asOf') or '') > str(cand.get('asOf') or ''):
            pass            # keep the newer previous table
        else:
            battle = cand
    elif battle and cb and str(battle.get('asOf') or '') < str(cb.get('asOf') or ''):
        battle = None       # the curated data has caught up

    # ---------------- output
    content = {'news': news, 'results': results, 'battle': battle}
    prev_content = {'news': prev.get('news', []), 'results': prev.get('results', []), 'battle': prev.get('battle')}
    changed = as_json(content) != as_json(prev_content)
    checked = now_utc()
    prev_checked = parse_day(str(prev.get('checked', ''))[:10])
    stale = prev_checked is None or (today - prev_checked).days >= REWRITE_AFTER_DAYS
    log = [x for x in prev.get('log', []) if isinstance(x, dict)] + [
        {'date': today.isoformat(), 'source': s, 'msg': m} for s, m in notes]
    auto = {
        'updated': today.isoformat() if changed else (prev.get('updated') or None),
        'checked': checked,
        'news': news,
        'results': results,
        'battle': battle,
        'log': log[-MAX_LOG:],
    }
    state_changed = as_json(state) != as_json(old_state)
    write_auto = (changed or stale) and not args.dry_run
    write_state = state_changed and not args.dry_run
    if write_auto:
        AUTO_JS.write_text('window.RR_AUTO = ' + json.dumps(auto, ensure_ascii=False, indent=1) + ';\n', encoding='utf-8')
    if write_state:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + '\n', encoding='utf-8')

    # ---------------- summary
    total_req = common.request_total()
    print(f'auto_update {today} {"(dry run) " if args.dry_run else ""}{"backfill " if args.backfill else ""}'
          f'since={since or "module default"} budget={budget}s')
    print('\n'.join(summary))
    print(f'  news:    {len(news)} in auto.js ({added_news} new)')
    print(f'  results: {len(results)} in auto.js ({added_res} new, {replaced_res} replaced, {skipped_curated} already curated)')
    print(f'  battle:  {("as of " + str(battle.get("asOf"))) if battle else "none (curated table is current)"}')
    print(f'  requests: {total_req} ' + json.dumps(dict(sorted(common.REQUESTS.items())), ensure_ascii=False))
    print(f'  runtime: {time.time() - t_start:.0f} s')
    what = 'content changed' if changed else ('no content change, refreshing "checked"' if stale else 'no change')
    if args.dry_run:
        print(f'  dry run: {what}; nothing written')
    else:
        print(f'  {what}: auto.js {"written" if write_auto else "unchanged"}, state {"written" if write_state else "unchanged"}')
    for s, m in notes:
        if s == 'merge' or 'FAILED' in m or m.startswith(('crashed', 'abandoned')):
            print(f'  note [{s}] {m}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
