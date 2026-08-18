#!/usr/bin/env python3
"""
Convert an mm_export tree into a Mattermost bulk-import archive.

  python3 tools/to_bulk_import.py .                 # full archive incl. attachments
  python3 tools/to_bulk_import.py . --no-files      # messages only (fast, small)
  python3 tools/to_bulk_import.py . --channels-only # skip DMs and group DMs

Produces  mattermost-import.zip  containing import.jsonl plus the attachment files.
Import it with:
  docker compose exec -T mattermost mmctl --local import process --bypass-upload mattermost-import.zip
"""
import argparse, glob, json, os, re, sys, zipfile

ap = argparse.ArgumentParser()
ap.add_argument('root', nargs='?', default='.')
ap.add_argument('--out', default='mattermost-import.zip')
ap.add_argument('--no-files', action='store_true', help='skip attachments')
ap.add_argument('--channels-only', action='store_true', help='skip DMs / group DMs')
ap.add_argument('--password', default='Archive-2026!', help='password for the owner account')
ap.add_argument('--limit', type=int, default=0, help='cap posts per channel (for testing)')
args = ap.parse_args()

R = args.root.rstrip('/')
manifest = json.load(open(f'{R}/export_manifest.json'))
teams_raw = json.load(open(f'{R}/teams.json'))
users_raw = {u['id']: u for u in json.load(open(f'{R}/users.json'))}
OWNER = manifest['exported_by']

def log(*a): print(*a, file=sys.stderr, flush=True)

# ---------------------------------------------------------------- usernames
USED = set()
def clean_username(raw, uid):
    u = re.sub(r'[^a-z0-9._-]', '', (raw or '').lower())
    u = re.sub(r'^[._-]+', '', u)
    if len(u) < 3:
        u = 'user-' + uid[:8].lower()
    u = u[:22]
    base = u
    n = 1
    while u in USED:
        suf = str(n); n += 1
        u = base[:22 - len(suf) - 1] + '-' + suf
    USED.add(u)
    return u

UNAME, EMAILS = {}, set()
def email_for(u, uname):
    e = (u.get('email') or '').strip().lower()
    if not e or '@' not in e or e in EMAILS:
        e = f'{uname}@archive.invalid'
        i = 1
        while e in EMAILS:
            e = f'{uname}-{i}@archive.invalid'; i += 1
    EMAILS.add(e)
    return e

# ------------------------------------------------------- collect what we need
chan_entries, dm_entries, self_dm = [], [], []
for e in manifest['channels']:
    d = f"{R}/{e['dir']}"
    if not os.path.exists(f'{d}/posts.json'):
        continue
    ch = json.load(open(f'{d}/channel.json'))
    # A self-DM ("notes to self") is a direct channel whose two member ids are the
    # same person. Mattermost's importer rejects a one-member direct channel
    # ("members list contains too few items"), so carry those posts into a normal
    # private channel rather than dropping them.
    if ch['type'] == 'D' and len(set((ch.get('name') or '').split('__'))) == 1:
        e = dict(e, team=None)          # team filled in below, once we know the teams
        ch = dict(ch, type='P', name='self-notes',
                  display_name='Notes to self (imported self-DM)')
        self_dm.append((e, ch, d))
        continue
    (dm_entries if ch['type'] in ('D', 'G') else chan_entries).append((e, ch, d))

# attach the self-notes channel to the first real team
if self_dm:
    home = next((x[0]['team'] for x in chan_entries if x[0].get('team')), None)
    for e, ch, d in self_dm:
        e['team'] = home
        chan_entries.append((e, ch, d))
    log(f"  self-DM -> private channel 'self-notes' on team '{home}'")

if args.channels_only:
    dm_entries = []

# Only people who actually left a trace: posters, reactors, and DM participants.
# Members of big public channels who never posted would just be dead accounts.
need = set()
for e, ch, d in chan_entries + dm_entries:
    for p in json.load(open(f'{d}/posts.json')):
        need.add(p['user_id'])
        for r in ((p.get('metadata') or {}).get('reactions') or []):
            need.add(r['user_id'])
    if ch['type'] in ('D', 'G') and os.path.exists(f'{d}/members.json'):
        for m in json.load(open(f'{d}/members.json')):
            need.add(m['user_id'])

# owner first so it gets the clean username
owner_id = next((i for i, u in users_raw.items() if u.get('username') == OWNER), None)
order = ([owner_id] if owner_id else []) + sorted(x for x in need if x != owner_id)
for uid in order:
    if not uid:
        continue
    u = users_raw.get(uid) or {}
    UNAME[uid] = clean_username(u.get('username') or '', uid)

# a stand-in for bots/webhooks/deleted accounts that aren't real users
BOT_ID = '__archive_bot__'
UNAME[BOT_ID] = clean_username('archive-bot', 'botbotbo')
users_raw[BOT_ID] = {'id': BOT_ID, 'username': 'archive-bot', 'email': '',
                     'first_name': 'Archive', 'last_name': 'Bot'}
need.add(BOT_ID)

def uref(uid):
    return UNAME.get(uid) or UNAME[BOT_ID]

# --------------------------------------------------------- team + membership
TEAMS = {t['id']: t for t in teams_raw}
team_of_dir = {}
for e, ch, d in chan_entries:
    team_of_dir[e['dir']] = e['team']

memberships = {}   # uid -> {team_name: set(channel_name)}
for e, ch, d in chan_entries:
    tname = e['team']
    if os.path.exists(f'{d}/members.json'):
        for m in json.load(open(f'{d}/members.json')):
            if m['user_id'] in need:
                memberships.setdefault(m['user_id'], {}).setdefault(tname, set()).add(ch['name'])
# posters must be members of the channel or their post is rejected
for e, ch, d in chan_entries:
    for p in json.load(open(f'{d}/posts.json')):
        memberships.setdefault(p['user_id'], {}).setdefault(e['team'], set()).add(ch['name'])
# the owner should land in every channel so the archive is fully browsable
for e, ch, d in chan_entries:
    if owner_id:
        memberships.setdefault(owner_id, {}).setdefault(e['team'], set()).add(ch['name'])

# ------------------------------------------------------------------ writing
lines = []
add = lines.append
add({'type': 'version', 'version': 1})

used_teams = sorted({e['team'] for e, ch, d in chan_entries})
for tname in used_teams:
    t = next((x for x in teams_raw if x['name'] == tname), None)
    add({'type': 'team', 'team': {
        'name': tname,
        'display_name': (t or {}).get('display_name') or tname,
        'type': 'O',
        'description': (t or {}).get('description') or '',
    }})

for e, ch, d in chan_entries:
    add({'type': 'channel', 'channel': {
        'team': e['team'], 'name': ch['name'],
        'display_name': ch.get('display_name') or ch['name'],
        'type': ch['type'],
        'header': (ch.get('header') or '')[:1024],
        'purpose': (ch.get('purpose') or '')[:250],
    }})

for uid in sorted(need):
    u = users_raw.get(uid) or {}
    uname = uref(uid)
    tl = []
    for tname, chans in sorted(memberships.get(uid, {}).items()):
        if tname not in used_teams:
            continue
        tl.append({'name': tname, 'roles': 'team_user',
                   'channels': [{'name': c, 'roles': 'channel_user'} for c in sorted(chans)]})
    rec = {
        'username': uname,
        'email': email_for(u, uname),
        'auth_service': '',
        'password': args.password if u.get('username') == OWNER else os.urandom(9).hex() + 'Aa1!',
        'nickname': (u.get('nickname') or '')[:64],
        'first_name': (u.get('first_name') or '')[:64],
        'last_name': (u.get('last_name') or '')[:64],
        'position': (u.get('position') or '')[:128],
        'roles': 'system_admin system_user' if u.get('username') == OWNER else 'system_user',
    }
    if tl:
        rec['teams'] = tl
    add({'type': 'user', 'user': rec})

# ------------------------------------------------------------------- posts
ATT = []      # (zip_arcname, source_path)
def attach(p, e, d):
    out = []
    if args.no_files:
        return out
    for f in ((p.get('metadata') or {}).get('files') or []):
        nm = re.sub(r'[^A-Za-z0-9._-]+', '_', (f.get('name') or '').strip())[:120] or 'file'
        disk = f"{d}/files/{f['id']}__{nm}"
        if not os.path.exists(disk):
            continue
        # Mattermost stores attachments at data/<rel> inside the zip, but the
        # jsonl must reference <rel> WITHOUT the data/ prefix -- the importer
        # prepends it. Getting this wrong makes the import silently drop files.
        rel = f"attachments/{f['id']}/{nm}"   # id as a dir keeps names clean but unique
        ATT.append((f'data/{rel}', disk))
        out.append({'path': rel})
    return out

def body(p):
    """Message text, with bot/webhook cards folded in so nothing is silently lost."""
    msg = p.get('message') or ''
    extra = []
    for a in ((p.get('props') or {}).get('attachments') or []):
        bits = [a.get('pretext'), a.get('title'), a.get('text') or a.get('fallback')]
        t = '\n'.join(x for x in bits if x)
        if t:
            extra.append(t)
    if extra:
        msg = (msg + '\n\n' if msg else '') + '\n\n'.join(extra)
    ov = (p.get('props') or {}).get('override_username')
    if ov and p['user_id'] not in UNAME:
        msg = f'*[{ov}]* ' + msg
    return msg.strip()

def reactions(p):
    out = []
    for r in ((p.get('metadata') or {}).get('reactions') or []):
        if not re.fullmatch(r'[a-z0-9_+-]{1,64}', r.get('emoji_name') or ''):
            continue
        out.append({'user': uref(r['user_id']), 'emoji_name': r['emoji_name'],
                    'create_at': r.get('create_at') or p['create_at']})
    return out

def usable(p):
    if (p.get('type') or '').startswith('system_'):
        return False
    return bool(body(p)) or bool((p.get('metadata') or {}).get('files'))

stats = {'posts': 0, 'replies': 0, 'dm_posts': 0, 'skipped': 0, 'attachments': 0}

def build_posts(entry, ch, d, direct):
    raw = json.load(open(f'{d}/posts.json'))
    if args.limit:
        raw = raw[-args.limit:]
    have = {p['id'] for p in raw}
    kids = {}
    roots = []
    for p in raw:
        if p.get('root_id') and p['root_id'] in have:
            kids.setdefault(p['root_id'], []).append(p)
        else:
            roots.append(p)
    for p in roots:
        children = sorted(kids.get(p['id'], []), key=lambda x: x['create_at'])
        if not usable(p):
            # root unusable but replies exist -> promote the first usable reply
            children = [c for c in children if usable(c)]
            if not children:
                stats['skipped'] += 1
                continue
            p, children = children[0], children[1:]
        reps = []
        for c in children:
            if not usable(c):
                stats['skipped'] += 1
                continue
            r = {'user': uref(c['user_id']), 'message': body(c), 'create_at': c['create_at']}
            a = attach(c, entry, d)
            if a: r['attachments'] = a; stats['attachments'] += len(a)
            rx = reactions(c)
            if rx: r['reactions'] = rx
            reps.append(r)
            stats['replies'] += 1
        rec = {'user': uref(p['user_id']), 'message': body(p), 'create_at': p['create_at']}
        a = attach(p, entry, d)
        if a: rec['attachments'] = a; stats['attachments'] += len(a)
        rx = reactions(p)
        if rx: rec['reactions'] = rx
        if reps: rec['replies'] = reps
        if direct:
            rec['channel_members'] = direct
            add({'type': 'direct_post', 'direct_post': rec})
            stats['dm_posts'] += 1
        else:
            rec['team'] = entry['team']; rec['channel'] = ch['name']
            add({'type': 'post', 'post': rec})
            stats['posts'] += 1

for e, ch, d in chan_entries:
    build_posts(e, ch, d, None)

# --------------------------------------------------------- DMs and group DMs
for e, ch, d in dm_entries:
    members = []
    if os.path.exists(f'{d}/members.json'):
        members = [m['user_id'] for m in json.load(open(f'{d}/members.json'))]
    if not members:
        members = sorted({p['user_id'] for p in json.load(open(f'{d}/posts.json'))})
    names = sorted({uref(m) for m in members if m in UNAME})
    if len(names) < 2:
        log(f"  skipping {e['dir']} (no resolvable members)")
        continue
    add({'type': 'direct_channel', 'direct_channel': {'members': names}})
    build_posts(e, ch, d, names)

# ------------------------------------------------------------------- output
jsonl = '\n'.join(json.dumps(l, ensure_ascii=False) for l in lines) + '\n'
seen_arc = set()
with zipfile.ZipFile(args.out, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as z:
    z.writestr('import.jsonl', jsonl)
    for arc, disk in ATT:
        if arc in seen_arc:
            continue
        seen_arc.add(arc)
        z.write(disk, arc)

log(f"\n{args.out}  ({os.path.getsize(args.out)/1048576:.1f} MB)")
log(f"  teams {len(used_teams)}  channels {len(chan_entries)}  dm/group {len(dm_entries)}  users {len(need)}")
log(f"  posts {stats['posts']:,}  replies {stats['replies']:,}  direct {stats['dm_posts']:,}"
    f"  attachments {len(seen_arc):,}  skipped {stats['skipped']:,}")
log(f"  owner account: {UNAME.get(owner_id, OWNER)}  password: {args.password}")
