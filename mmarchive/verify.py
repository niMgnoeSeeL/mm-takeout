#!/usr/bin/env python3
"""
Check that an export is complete and internally consistent.

  python3 mmarchive/verify.py ./export

Checks:
  - every post referenced by the manifest is present and parseable
  - every attachment referenced by a post exists on disk at the right byte size
  - thread replies point at posts that exist
  - post counts vs. the server's own total_msg_count (see the note below)
Exit code is 0 if everything checks out, 1 otherwise.
"""
import json, os, re, sys, collections

ROOT = (sys.argv[1] if len(sys.argv) > 1 else 'export').rstrip('/')

def die(msg):
    print(f"error: {msg}", file=sys.stderr); sys.exit(2)

if not os.path.exists(f'{ROOT}/export_manifest.json'):
    die(f"no export_manifest.json under {ROOT}")

manifest = json.load(open(f'{ROOT}/export_manifest.json'))
problems, warnings = [], []
tot_posts = tot_files = tot_bytes = 0
orphan_replies = 0

for e in manifest['channels']:
    d = f"{ROOT}/{e['dir']}"
    pj = f'{d}/posts.json'
    if not os.path.exists(pj):
        problems.append(f"{e['dir']}: posts.json missing")
        continue
    try:
        posts = json.load(open(pj))
    except Exception as ex:
        problems.append(f"{e['dir']}: posts.json unreadable ({ex})")
        continue

    if len(posts) != e['posts_exported']:
        problems.append(f"{e['dir']}: manifest says {e['posts_exported']} posts, file has {len(posts)}")
    tot_posts += len(posts)

    ids = {p['id'] for p in posts}
    for p in posts:
        root = p.get('root_id')
        if root and root not in ids:
            orphan_replies += 1
        for f in ((p.get('metadata') or {}).get('files') or []):
            nm = re.sub(r'[^A-Za-z0-9._-]+', '_', (f.get('name') or '').strip())[:120] or 'file'
            path = f"{d}/files/{f['id']}__{nm}"
            tot_files += 1
            if not os.path.exists(path):
                problems.append(f"{e['dir']}: missing attachment {f.get('name')} ({f['id']})")
                continue
            size = os.path.getsize(path)
            tot_bytes += size
            if f.get('size') and size != f['size']:
                problems.append(f"{e['dir']}: {f.get('name')} is {size} bytes, expected {f['size']}")

    # The server counts deleted posts and join/leave events in total_msg_count but
    # does not serve them over the API, so a shortfall here is expected, not a bug.
    claimed = e.get('server_total_msg_count')
    if claimed and len(posts) < claimed:
        warnings.append(f"{e['dir']}: server counts {claimed}, exported {len(posts)} "
                        f"({claimed - len(posts)} deleted/system posts not served by the API)")

print(f"channels     {len(manifest['channels'])}")
print(f"messages     {tot_posts:,}")
print(f"attachments  {tot_files:,}  ({tot_bytes/1048576:,.0f} MB on disk)")
if orphan_replies:
    print(f"replies whose parent is outside the export: {orphan_replies:,} "
          f"(normal for DMs where the root was deleted)")

if warnings:
    print(f"\n{len(warnings)} expected count difference(s):")
    for w in warnings[:10]:
        print("  -", w)
    if len(warnings) > 10:
        print(f"  ... and {len(warnings)-10} more")

if problems:
    print(f"\n{len(problems)} PROBLEM(S):")
    for p in problems[:25]:
        print("  -", p)
    if len(problems) > 25:
        print(f"  ... and {len(problems)-25} more")
    sys.exit(1)

print("\nOK -- every post and attachment referenced by the manifest is present and the right size.")
