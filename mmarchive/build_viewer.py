#!/usr/bin/env python3
"""
Compact an export tree into data.js and drop the static HTML viewer next to it.

  python3 mmarchive/build_viewer.py ./export

Produces export/data.js and export/index.html. Open index.html in any browser.
"""
import json, glob, os, re, shutil, sys

ROOT = (sys.argv[1] if len(sys.argv) > 1 else 'export').rstrip('/')
IMG = re.compile(r'^image/')

users = {u['id']: u for u in json.load(open(f'{ROOT}/users.json'))}
manifest = json.load(open(f'{ROOT}/export_manifest.json'))

# Team display names come from the server, not a hardcoded table.
TEAM_LABEL = {t['name']: (t.get('display_name') or t['name'])
              for t in json.load(open(f'{ROOT}/teams.json'))}
TEAM_LABEL['_direct'] = 'Direct & Group Messages'

channels, posts_by_ch = [], {}

for entry in manifest['channels']:
    cdir = f"{ROOT}/{entry['dir']}"
    ch = json.load(open(f'{cdir}/channel.json'))
    raw = json.load(open(f'{cdir}/posts.json'))
    if not raw:
        continue
    reldir = entry['dir']

    disp = ch.get('display_name') or ch['name']
    if ch['type'] == 'D':
        disp = entry['dir'].split('dm__')[-1].replace('__self', ' (you)')
    elif ch['type'] == 'G':
        disp = ch.get('display_name') or 'group'

    out = []
    for p in raw:
        md = p.get('metadata') or {}
        files = []
        for f in (md.get('files') or []):
            fname = re.sub(r'[^A-Za-z0-9._-]+', '_', (f.get('name') or '').strip())[:120] or 'file'
            files.append([f"{f['id']}__{fname}", f.get('name'), f.get('size') or 0,
                          1 if IMG.match(f.get('mime_type') or '') else 0])
        reacts = {}
        for r in (md.get('reactions') or []):
            reacts[r['emoji_name']] = reacts.get(r['emoji_name'], 0) + 1
        props = p.get('props') or {}
        atts = []
        for a in (props.get('attachments') or []):
            atts.append([a.get('title') or '', a.get('text') or a.get('fallback') or '',
                         a.get('pretext') or ''])
        rec = [p['id'], p['create_at'], p['user_id'], p.get('message') or '',
               p.get('root_id') or '', p.get('type') or '']
        extra = {}
        if files:   extra['f'] = files
        if reacts:  extra['r'] = sorted(reacts.items(), key=lambda x: -x[1])
        if atts:    extra['a'] = atts
        if p.get('edit_at'):  extra['e'] = 1
        if p.get('is_pinned'): extra['p'] = 1
        ov = props.get('override_username') or props.get('webhook_display_name')
        if ov: extra['w'] = ov
        rec.append(extra)
        out.append(rec)

    posts_by_ch[ch['id']] = out
    channels.append({
        'id': ch['id'], 'dir': reldir, 'name': ch['name'], 'disp': disp,
        'type': ch['type'], 'team': TEAM_LABEL.get(entry['team'], entry['team'] or ''),
        'n': len(out), 'files': entry['attachment_files_in_metadata'],
        'first': out[0][1] if out else 0, 'last': out[-1][1] if out else 0,
    })

# only ship users who actually appear
seen = set()
for arr in posts_by_ch.values():
    for r in arr:
        seen.add(r[2])
for c in channels:
    pass
umap = {}
for uid in seen:
    u = users.get(uid) or {}
    name = (f"{u.get('first_name','')} {u.get('last_name','')}").strip() or u.get('nickname') or ''
    umap[uid] = [u.get('username') or uid[:8], name]

channels.sort(key=lambda c: (c['team'], {'O': 0, 'P': 1, 'G': 2, 'D': 3}[c['type']], c['disp'].lower()))

data = {'server': manifest['server'], 'me': manifest['exported_by'],
        'exported_at': manifest['exported_at_unix_ms'],
        'users': umap, 'channels': channels, 'posts': posts_by_ch}

with open(f'{ROOT}/data.js', 'w', encoding='utf-8') as f:
    f.write('window.MM=')
    json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    f.write(';')

# ship the viewer alongside the data so the folder is self-contained
HERE = os.path.dirname(os.path.abspath(__file__))
src = os.path.join(HERE, '..', 'viewer', 'index.html')
if os.path.exists(src):
    shutil.copyfile(src, f'{ROOT}/index.html')
else:
    print('warning: viewer/index.html not found, data.js written without it', file=sys.stderr)

print(f"data.js: {os.path.getsize(f'{ROOT}/data.js')/1048576:.1f} MB, "
      f"{len(channels)} channels, {sum(len(v) for v in posts_by_ch.values()):,} posts, {len(umap)} users")
