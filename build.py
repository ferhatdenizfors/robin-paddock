#!/usr/bin/env python3
"""Robin Räikkönen Paddock – build & validate.

  python3 build.py            # merge build/fragments/*.json -> site/data.js, validate, write dist/
  python3 build.py --check    # validate existing site/data.js only, write dist/ (used by GitHub Actions)

site/data.js is the single source of truth once built; later updates (the weekly job)
edit site/data.js directly and run `python3 build.py --check`.
"""
import json, re, sys, pathlib, datetime

ROOT = pathlib.Path(__file__).resolve().parent
FRAG = ROOT / 'build' / 'fragments'
SITE = ROOT / 'site'
DIST = ROOT / 'dist'
ORDER = ['core', 'results', 'news', 'tech', 'tracks', 'ladder', 'kimi', 'extras', 'manual']
LANGS = ('fi', 'sv', 'en')
DATE_RE = re.compile(r'^\d{4}-\d{2}(-\d{2})?$')


def deep_merge(a, b):
    for k, v in b.items():
        if k in a and isinstance(a[k], dict) and isinstance(v, dict):
            deep_merge(a[k], v)
        else:
            a[k] = v
    return a


def load_data_js():
    txt = (SITE / 'data.js').read_text(encoding='utf-8')
    m = re.match(r'\s*window\.RR_DATA\s*=\s*', txt)
    if not m:
        raise SystemExit('site/data.js must start with "window.RR_DATA ="')
    body = txt[m.end():].strip()
    if body.endswith(';'):
        body = body[:-1]
    return json.loads(body)


def write_data_js(data):
    js = 'window.RR_DATA = ' + json.dumps(data, ensure_ascii=False, indent=1) + ';\n'
    (SITE / 'data.js').write_text(js, encoding='utf-8')


def validate(d):
    errs, warns = [], []

    def ml(v, where, required=True):
        """multilingual object check"""
        if v is None:
            if required:
                errs.append(f'{where}: missing')
            return
        if isinstance(v, str):
            warns.append(f'{where}: plain string, not {{fi,sv,en}}')
            return
        if not isinstance(v, dict):
            errs.append(f'{where}: not an object')
            return
        for l in LANGS:
            if not str(v.get(l, '')).strip():
                errs.append(f'{where}.{l}: empty')

    def walk(v, where):
        # every dict that looks multilingual must be complete
        if isinstance(v, dict):
            keys = set(v.keys())
            if keys and keys <= set(LANGS) | {'orig'}:
                ml(v, where)
                return
            for k, x in v.items():
                walk(x, f'{where}.{k}')
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, f'{where}[{i}]')
    walk(d, 'RR_DATA')

    track_ids = {t.get('id') for t in d.get('tracks', [])}
    for t in d.get('tracks', []):
        for f in ('lat', 'lon'):
            if f in t and not isinstance(t[f], (int, float)):
                errs.append(f'tracks[{t.get("id")}].{f}: not a number')
        if 'lengthM' in t and not isinstance(t['lengthM'], (int, float)):
            warns.append(f'tracks[{t.get("id")}].lengthM: not a number (no bar in chart)')

    seen = set()
    for i, r in enumerate(d.get('results', [])):
        w = f'results[{i}] {r.get("id")}'
        if r.get('id') in seen:
            errs.append(f'{w}: duplicate id')
        seen.add(r.get('id'))
        if not DATE_RE.match(str(r.get('date', ''))):
            errs.append(f'{w}: bad date {r.get("date")!r}')
        if r.get('trackId') and r['trackId'] not in track_ids:
            errs.append(f'{w}: unknown trackId {r["trackId"]!r}')
        fp = r.get('finalPos')
        if fp is not None and not isinstance(fp, (int, float)):
            errs.append(f'{w}: finalPos not a number')

    for key in ('calendar', 'calendar2027'):
        for i, e in enumerate(d.get(key, [])):
            w = f'{key}[{i}] {e.get("id")}'
            for f in ('start', 'end'):
                if e.get(f) and not DATE_RE.match(str(e[f])):
                    errs.append(f'{w}: bad {f} {e[f]!r}')
            if e.get('trackId') and e['trackId'] not in track_ids:
                errs.append(f'{w}: unknown trackId {e["trackId"]!r}')
            if e.get('status') not in ('confirmed', 'likely', 'possible', 'unknown'):
                errs.append(f'{w}: bad status {e.get("status")!r}')

    for i, n in enumerate(d.get('news', [])):
        if not DATE_RE.match(str(n.get('date', ''))):
            errs.append(f'news[{i}]: bad date {n.get("date")!r}')
        if not str(n.get('url', '')).startswith('http'):
            errs.append(f'news[{i}]: bad url')

    anat = (d.get('tech') or {}).get('anatomy') or {}
    for p in ('fairing', 'chassis', 'tyres', 'steering', 'logger', 'tank', 'seat', 'radiator', 'engine', 'exhaust', 'drive', 'axle', 'brakes'):
        if p not in anat:
            errs.append(f'tech.anatomy.{p}: missing')
    for c in (d.get('tech') or {}).get('classes', []):
        for f in ('hp', 'weightKg', 'topSpeed'):
            if f in c and not isinstance(c[f], (int, float)):
                errs.append(f'tech.classes[{c.get("id")}].{f}: not a number')

    for i, q in enumerate(d.get('quiz', [])):
        opts = q.get('options', [])
        if len(opts) != 4:
            errs.append(f'quiz[{i}]: {len(opts)} options')
        if not isinstance(q.get('answer'), int) or not 0 <= q['answer'] < len(opts):
            errs.append(f'quiz[{i}]: bad answer index')
    return errs, warns


def write_assets():
    """Collect visual assets (built by the asset workflow) into site/assets.js."""
    B = ROOT / 'build'
    def load(name, default):
        p = B / name
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default
    shapes = load('track_shapes.json', {})
    tracks = {k: {'d': v['d'], 'viewBox': v.get('viewBox', '0 0 1000 1000'), 'confidence': v.get('confidence', 'medium')}
              for k, v in shapes.items() if isinstance(v, dict) and v.get('d')}
    photos = [p for p in load('photos.json', []) if (SITE / p.get('file', '')).exists()]
    icons = load('icons.json', {})
    flags = load('flags.json', {})
    kart = load('kart_diagram.json', None)
    if kart and not (SITE / kart.get('file', '')).exists():
        kart = None
    assets = {'tracks': tracks, 'photos': photos, 'icons': icons, 'flags': flags, 'kart': kart}
    (SITE / 'assets.js').write_text('window.RR_ASSETS = ' + json.dumps(assets, ensure_ascii=False, separators=(',', ':')) + ';\n', encoding='utf-8')
    return {'tracks': len(tracks), 'photos': len(photos), 'icons': len([k for k in icons if not k.startswith('_')]), 'flags': len([k for k in flags if not k.startswith('_')]), 'kart': bool(kart)}


def write_dist():
    src = (SITE / 'index.html').read_text(encoding='utf-8')
    cut = src.index('</style>') + len('</style>')
    head, body = src[:cut], src[cut:]
    page = ('<!doctype html>\n<html lang="fi">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            '<meta name="description" content="Epävirallinen fanisivu: Robin Räikkösen karting-ura – tulokset, kalenteri, uutiset ja tekniikka.">\n'
            '<style>[hidden]{display:none!important}img{max-width:100%}</style>\n'
            + head + '\n</head>\n<body>' + body + '\n</body>\n</html>\n')
    DIST.mkdir(exist_ok=True)
    (DIST / 'index.html').write_text(page, encoding='utf-8')
    (DIST / 'data.js').write_text((SITE / 'data.js').read_text(encoding='utf-8'), encoding='utf-8')
    (DIST / 'assets.js').write_text((SITE / 'assets.js').read_text(encoding='utf-8'), encoding='utf-8')
    auto = SITE / 'auto.js'
    (DIST / 'auto.js').write_text(auto.read_text(encoding='utf-8') if auto.exists() else 'window.RR_AUTO = null;\n', encoding='utf-8')
    import shutil
    if (DIST / 'assets').exists():
        shutil.rmtree(DIST / 'assets')
    if (SITE / 'assets').exists():
        shutil.copytree(SITE / 'assets', DIST / 'assets')
    (DIST / '.nojekyll').write_text('', encoding='utf-8')


def main():
    check_only = '--check' in sys.argv
    if check_only:
        data = load_data_js()
    else:
        data = {}
        for k in ORDER:
            p = FRAG / f'{k}.json'
            if not p.exists():
                print(f'!! missing fragment {k}')
                continue
            deep_merge(data, json.loads(p.read_text(encoding='utf-8')))
        data.setdefault('meta', {})['updated'] = data.get('meta', {}).get('updated') or datetime.date.today().isoformat()
    errs, warns = validate(data)
    for w in warns:
        print('warn:', w)
    for e in errs:
        print('ERROR:', e)
    if errs:
        raise SystemExit(f'{len(errs)} error(s) – data.js not written' if not check_only else f'{len(errs)} error(s)')
    if not check_only:
        write_data_js(data)
    if not check_only:
        print('assets', write_assets())   # needs build/*.json manifests (local only)
    write_dist()
    counts = {k: (len(v) if isinstance(v, (list, dict)) else 1) for k, v in data.items()}
    print('OK', json.dumps(counts, ensure_ascii=False))


if __name__ == '__main__':
    main()
