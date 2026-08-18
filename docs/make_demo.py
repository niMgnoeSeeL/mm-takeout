#!/usr/bin/env python3
"""
Build a synthetic export tree purely for the README screenshots.

Everything here is invented. The point is to show the real viewer rendering
realistic-looking data without putting anyone's actual messages in a public repo.
"""
import json, os, shutil, base64, zlib, struct, random

OUT = '/tmp/demo-export'
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(f'{OUT}/channels', exist_ok=True)
random.seed(11)

BASE = 1750000000000  # fixed timestamp so runs are reproducible
DAY = 86400000

USERS = [
    ('u_rivera00000000000000000', 'a.rivera',   'Ana',   'Rivera'),
    ('u_okafor00000000000000000', 'd.okafor',   'Daniel', 'Okafor'),
    ('u_lindqvist000000000000000', 'm.lindqvist', 'Mira', 'Lindqvist'),
    ('u_tanaka00000000000000000', 'k.tanaka',   'Kenji', 'Tanaka'),
    ('u_bot00000000000000000000', 'ci-bot',     'CI',    'Bot'),
]
UID = {u[1]: u[0] for u in USERS}

def png_chart():
    """A small plot-looking PNG, drawn by hand so we need no image libraries."""
    W = H = 220
    px = [[(250, 250, 252) for _ in range(W)] for _ in range(H)]
    for x in range(20, W - 10):                      # axes
        px[H - 25][x] = (150, 155, 165)
    for y in range(15, H - 25):
        px[y][20] = (150, 155, 165)
    series = [(43, 108, 176), (163, 93, 74)]
    for si, col in enumerate(series):
        prev = None
        for i in range(0, 20):
            x = 22 + i * 9
            y = int(H - 35 - (40 + si * 45) * (0.4 + 0.6 * abs((i / 19.0) ** (0.6 + si * 0.5))))
            if prev:
                x0, y0 = prev
                for t in range(0, 30):
                    xx = int(x0 + (x - x0) * t / 29.0); yy = int(y0 + (y - y0) * t / 29.0)
                    for dy in (-1, 0, 1):
                        if 0 <= yy + dy < H and 0 <= xx < W:
                            px[yy + dy][xx] = col
            prev = (x, y)
    raw = b''.join(b'\x00' + bytes(v for p in row for v in p) for row in px)
    def chunk(t, d):
        c = struct.pack('>I', len(d)) + t + d
        return c + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', W, H, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))

CHART = png_chart()
FILEID = 'f_chart000000000000000000'

def post(pid, uid, msg, ts, root='', files=None, reacts=None, embeds=None, edited=False, ptype=''):
    md = {}
    if files:
        md['files'] = files
    if reacts:
        md['reactions'] = [{'user_id': UID[u], 'emoji_name': e, 'create_at': ts,
                            'post_id': pid, 'delete_at': 0} for u, e in reacts]
    if embeds:
        md['embeds'] = embeds
    return {'id': pid, 'create_at': ts, 'update_at': ts, 'edit_at': ts if edited else 0,
            'delete_at': 0, 'is_pinned': False, 'user_id': uid, 'root_id': root,
            'message': msg, 'type': ptype, 'props': {}, 'file_ids': [f['id'] for f in (files or [])],
            'metadata': md, 'hashtags': '', 'reply_count': 0}

CH = []

def channel(dirname, team, name, disp, ctype, posts, purpose=''):
    d = f'{OUT}/channels/{dirname}'
    os.makedirs(d, exist_ok=True)
    cid = 'c_' + dirname.replace('-', '')[:24]
    json.dump({'id': cid, 'name': name, 'display_name': disp, 'type': ctype,
               'purpose': purpose, 'header': '', 'total_msg_count': len(posts)},
              open(f'{d}/channel.json', 'w'))
    json.dump(posts, open(f'{d}/posts.json', 'w'))
    json.dump([{'user_id': u[0], 'channel_id': cid} for u in USERS],
              open(f'{d}/members.json', 'w'))
    nfiles = sum(len((p.get('metadata') or {}).get('files') or []) for p in posts)
    nbytes = sum(f['size'] for p in posts for f in ((p.get('metadata') or {}).get('files') or []))
    CH.append({'team': team, 'channel': name, 'channel_display': disp, 'channel_id': cid,
               'type': ctype, 'dir': f'channels/{dirname}', 'posts_exported': len(posts),
               'attachment_files_in_metadata': nfiles, 'attachment_bytes_estimated': nbytes,
               'attachment_failures': [], 'server_total_msg_count': len(posts),
               'first_post_at': posts[0]['create_at'], 'last_post_at': posts[-1]['create_at']})

# ---------------------------------------------------------------- #retrieval
t = BASE
P = []
P.append(post('p01', UID['a.rivera'],
              "Reran the ablation overnight. Dropping the reranker costs us about **4 points** of recall@10, "
              "which is more than I expected.", t))
t += 700000
P.append(post('p02', UID['m.lindqvist'],
              "That tracks with what the original paper reports. Did you hold the index fixed across runs?", t))
t += 400000
P.append(post('p03', UID['a.rivera'], "Yes, same index, same seed. Here's the curve:", t,
              files=[{'id': FILEID, 'name': 'recall-ablation.png', 'size': len(CHART),
                      'mime_type': 'image/png', 'extension': 'png'}],
              reacts=[('m.lindqvist', '+1'), ('k.tanaka', 'eyes'), ('d.okafor', 'tada')]))
t += 1500000
P.append(post('p04', UID['k.tanaka'],
              "Nice. One thing to check before we write this up:\n"
              "```python\ndef recall_at_k(ranked, gold, k=10):\n"
              "    hits = len(set(ranked[:k]) & set(gold))\n"
              "    return hits / max(len(gold), 1)   # guard against empty gold\n```\n"
              "If `gold` is ever empty we were silently counting it as a miss.", t))
t += 900000
P.append(post('p05', UID['a.rivera'],
              "Good catch, that affects 12 of 4096 queries. Rerunning.", t, edited=True))
t += DAY
P.append(post('p06', UID['d.okafor'],
              "Worth reading before Thursday: https://example.org/papers/dense-retrieval-survey", t,
              embeds=[{'type': 'opengraph', 'url': 'https://example.org/papers/dense-retrieval-survey',
                       'data': {'site_name': 'example.org',
                                'title': 'A Survey of Dense Retrieval Methods',
                                'description': 'We review dense retrieval architectures published '
                                               'between 2019 and 2025, and compare them under a '
                                               'unified evaluation protocol.'}}]))
t += 300000
P.append(post('p07', UID['m.lindqvist'], "> compare them under a unified evaluation protocol\n"
                                         "This is the part we should copy for our own eval section.", t))
t += 600000
r = P[-1]['id']
P.append(post('p08', UID['a.rivera'], "Agreed. I'll draft it tomorrow.", t, root=r))
t += 200000
P.append(post('p09', UID['k.tanaka'], "Can you include the per-dataset breakdown too?", t, root=r))
t += 250000
P.append(post('p10', UID['a.rivera'], "Yep, table 3 will have it.", t, root=r,
              reacts=[('k.tanaka', '+1')]))
t += 400000
P.append(post('p11', UID['ci-bot'],
              "Pipeline **eval-nightly** finished in 41m 12s — 0 failed, 3 skipped.", t))
channel('research-lab__retrieval', 'research-lab', 'retrieval', 'Retrieval', 'O', P,
        'dense retrieval experiments')

# ---------------------------------------------------------------- #paper-draft (private)
t = BASE + DAY
P2 = []
P2.append(post('q01', UID['m.lindqvist'],
               "Draft outline is up. Section 4 is still a stub.", t))
t += 1200000
P2.append(post('q02', UID['d.okafor'],
               "I can take 4.2 if nobody has started it. Deadline is the 14th, right?", t))
t += 500000
P2.append(post('q03', UID['m.lindqvist'], "14th AoE. Take it.", t, reacts=[('d.okafor', 'raised_hands')]))
t += DAY
P2.append(post('q04', UID['k.tanaka'],
               "Reviewer 2 on the last submission complained the baselines were undertrained. "
               "Let's preempt that with a training-budget table.", t))
t += 800000
P2.append(post('q05', UID['a.rivera'], "Added as ~~appendix C~~ appendix B.", t, edited=True))
for i in range(6, 24):
    t += 400000 + i * 60000
    who = ['a.rivera', 'd.okafor', 'm.lindqvist', 'k.tanaka'][i % 4]
    P2.append(post(f'q{i:02d}', UID[who],
                   ["Pushed the revised intro.", "Numbers in table 2 are stale, regenerating.",
                    "Can someone sanity-check the significance test?",
                    "Figure 3 is unreadable in greyscale — switching to dashed lines.",
                    "Camera-ready checklist is in the shared folder."][i % 5], t))
channel('research-lab__paper-draft', 'research-lab', 'paper-draft', 'Paper Draft', 'P', P2)

# ---------------------------------------------------------------- DM
t = BASE + 2 * DAY
P3 = []
P3.append(post('d01', UID['d.okafor'], "Are you going to the reading group?", t))
t += 300000
P3.append(post('d02', UID['a.rivera'], "Yes, 15 min late — teaching until 4.", t))
t += 200000
P3.append(post('d03', UID['d.okafor'], "No worries, I'll save you a seat.", t,
               reacts=[('a.rivera', 'pray')]))
channel('dm__d.okafor', '_direct', 'u_rivera00000000000000000__u_okafor00000000000000000',
        'd.okafor', 'D', P3)

# ---------------------------------------------------------------- group DM
t = BASE + 3 * DAY
P4 = []
P4.append(post('g01', UID['k.tanaka'], "Lunch before the seminar?", t))
t += 250000
P4.append(post('g02', UID['m.lindqvist'], "In. 12:15 at the usual place.", t))
t += 150000
P4.append(post('g03', UID['d.okafor'], "Can't today, dentist.", t))
channel('group__a1b2c3d4__a.rivera-d.okafor-k.tanaka', '_direct',
        'g1b2c3d4e5f6', 'a.rivera, d.okafor, k.tanaka, m.lindqvist', 'G', P4)

# ---------------------------------------------------------------- write the rest
for c in CH:
    d = f"{OUT}/{c['dir']}/files"
    os.makedirs(d, exist_ok=True)
open(f"{OUT}/channels/research-lab__retrieval/files/{FILEID}__recall-ablation.png", 'wb').write(CHART)

json.dump([{'id': u[0], 'username': u[1], 'first_name': u[2], 'last_name': u[3],
            'email': f'{u[1]}@example.org', 'nickname': '', 'position': ''} for u in USERS],
          open(f'{OUT}/users.json', 'w'))
json.dump({u[0]: u[1] for u in USERS}, open(f'{OUT}/users_by_id.json', 'w'))
json.dump([{'id': 't_researchlab', 'name': 'research-lab', 'display_name': 'Research Lab'}],
          open(f'{OUT}/teams.json', 'w'))
json.dump({'server': 'https://chat.example.org', 'exported_by': 'a.rivera',
           'exported_at_unix_ms': BASE, 'channels': CH, 'user_count': len(USERS)},
          open(f'{OUT}/export_manifest.json', 'w'))

print(f"demo export at {OUT}: {len(CH)} channels, "
      f"{sum(c['posts_exported'] for c in CH)} posts")
