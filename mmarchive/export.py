#!/usr/bin/env python3
"""
Mattermost channel exporter -> raw JSON + file attachments + metadata.

Usage:
  export MM_URL=https://mattermost.example.com
  export MM_TOKEN=xxxxxxxxxxxx

  python3 mm_export.py --list
  python3 mm_export.py --list-channels TEAM_NAME
  python3 mm_export.py --channel team-name:channel-name --channel other:general --out ./export
  python3 mm_export.py --all-my-channels --team team-name --out ./export
"""
import argparse, json, os, re, sys, time, urllib.parse, urllib.request, urllib.error
from pathlib import Path

def log(*a):
    print(*a, file=sys.stderr, flush=True)

class MM:
    def __init__(self, base, token):
        base = base.rstrip('/')
        if not base.endswith('/api/v4'):
            base += '/api/v4'
        self.base = base
        self.token = token

    def req(self, path, params=None, raw=False, method='GET', body=None):
        url = self.base + path
        if params:
            url += '?' + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        last = None
        for attempt in range(6):
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header('Authorization', 'Bearer ' + self.token)
            req.add_header('User-Agent', 'mm-export/1.0')
            if data:
                req.add_header('Content-Type', 'application/json')
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    payload = r.read()
                return payload if raw else json.loads(payload or b'null')
            except urllib.error.HTTPError as e:
                body_txt = e.read()[:800].decode('utf8', 'replace')
                if e.code == 429:
                    wait = e.headers.get('Retry-After') or e.headers.get('X-Ratelimit-Reset') or '2'
                    try: wait = int(float(wait))
                    except Exception: wait = 2
                    log(f"  rate limited, sleeping {min(max(wait,1),60)}s")
                    time.sleep(min(max(wait, 1), 60)); continue
                if e.code in (500, 502, 503, 504):
                    time.sleep(2 ** attempt); last = e; continue
                raise RuntimeError(f"HTTP {e.code} on {url}\n{body_txt}")
            except (urllib.error.URLError, TimeoutError) as e:
                last = e; time.sleep(2 ** attempt); continue
        raise RuntimeError(f"request failed after retries: {url} ({last})")

    def paged(self, path, per_page=200, params=None):
        page = 0
        while True:
            p = dict(params or {}); p['page'] = page; p['per_page'] = per_page
            items = self.req(path, p) or []
            if not items:
                return
            for it in items:
                yield it
            if len(items) < per_page:
                return
            page += 1

    # ---- posts ---------------------------------------------------------
    def channel_posts(self, cid, per_page=200):
        """Cursor-paginate backwards through the full channel history."""
        posts, pages, before = {}, 0, None
        while True:
            params = {'per_page': per_page}
            if before:
                params['before'] = before
            res = self.req(f'/channels/{cid}/posts', params) or {}
            order = res.get('order') or []
            posts.update(res.get('posts') or {})
            pages += 1
            log(f"  page {pages}: +{len(order)} posts (total unique {len(posts)})")
            if not order or not res.get('prev_post_id'):
                break
            before = order[-1]          # oldest post on this page; `before` is exclusive
        return posts, pages

    def thread(self, root_id):
        return self.req(f'/posts/{root_id}/thread')

def sanitize(name, fallback='file'):
    name = re.sub(r'[^A-Za-z0-9._-]+', '_', (name or '').strip())[:120]
    return name or fallback

def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding='utf-8')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default=os.environ.get('MM_URL'))
    ap.add_argument('--token', default=os.environ.get('MM_TOKEN'))
    ap.add_argument('--channel', action='append', default=[],
                    help='team-name:channel-name, or a bare channel id')
    ap.add_argument('--team', help='team name, used with --all-my-channels')
    ap.add_argument('--all-my-channels', action='store_true')
    ap.add_argument('--types', default='OP',
                    help='channel types to include with --all-my-channels: O public, P private, D dm, G group')
    ap.add_argument('--out', default='./export')
    ap.add_argument('--list', action='store_true', help='list my teams')
    ap.add_argument('--list-channels', metavar='TEAM', help='list channels in a team')
    ap.add_argument('--no-files', action='store_true', help='skip attachment downloads')
    ap.add_argument('--per-page', type=int, default=200)
    args = ap.parse_args()

    if not args.url or not args.token:
        ap.error('need --url/--token (or MM_URL/MM_TOKEN env vars)')

    mm = MM(args.url, args.token)
    me = mm.req('/users/me')
    log(f"authenticated as {me.get('username')} ({me.get('email')}) id={me.get('id')}")

    teams = list(mm.paged('/users/me/teams'))
    if args.list:
        for t in teams:
            print(f"{t['name']:<30} {t.get('display_name','')}   id={t['id']}")
        return

    teams_by_name = {t['name']: t for t in teams}

    if args.list_channels:
        t = teams_by_name.get(args.list_channels)
        if not t:
            sys.exit(f"team '{args.list_channels}' not found. have: {list(teams_by_name)}")
        chans = list(mm.paged(f"/users/me/teams/{t['id']}/channels"))
        kind = {'O': 'public', 'P': 'private', 'D': 'dm', 'G': 'group'}
        for c in sorted(chans, key=lambda c: (c['type'], c['name'])):
            print(f"{kind.get(c['type'], c['type']):<8} {c['name']:<40} "
                  f"{(c.get('display_name') or '')[:40]:<42} msgs~{c.get('total_msg_count', 0)}  id={c['id']}")
        return

    # ---- resolve targets ----------------------------------------------
    targets = []
    for spec in args.channel:
        if ':' in spec:
            tname, cname = spec.split(':', 1)
            t = teams_by_name.get(tname)
            if not t:
                sys.exit(f"team '{tname}' not found. have: {list(teams_by_name)}")
            ch = mm.req(f"/teams/{t['id']}/channels/name/{urllib.parse.quote(cname)}")
            targets.append((t, ch))
        else:
            ch = mm.req(f'/channels/{spec}')
            t = teams_by_name.get(next((x['name'] for x in teams if x['id'] == ch.get('team_id')), ''), {'name': ch.get('team_id') or 'noteam'})
            targets.append((t, ch))

    if args.all_my_channels:
        tlist = [teams_by_name[args.team]] if args.team else teams
        want = set(args.types.upper())
        for t in tlist:
            for ch in mm.paged(f"/users/me/teams/{t['id']}/channels"):
                if ch['type'] in want:
                    targets.append((t, ch))

    # DMs/group DMs are server-wide and show up under every team -- dedupe by channel id
    seen_ids, deduped = set(), []
    for t, ch in targets:
        if ch['id'] in seen_ids:
            continue
        seen_ids.add(ch['id'])
        if ch['type'] in ('D', 'G'):
            t = {'name': '_direct', 'display_name': 'Direct & Group Messages'}
        deduped.append((t, ch))
    targets = deduped

    # friendly names for DMs: resolve the other participant
    uname_cache = {}
    def uname(uid):
        if uid not in uname_cache:
            try:
                uname_cache[uid] = (mm.req(f'/users/{uid}') or {}).get('username', uid)
            except Exception:
                uname_cache[uid] = uid
        return uname_cache[uid]

    def label_for(t, ch):
        if ch['type'] == 'D':
            parts = ch['name'].split('__')
            other = next((x for x in parts if x != me['id']), parts[0])
            return f"dm__{uname(other)}" + ("__self" if len(set(parts)) == 1 else "")
        if ch['type'] == 'G':
            return f"group__{ch['id'][:8]}__{sanitize((ch.get('display_name') or '').replace(', ', '-'))[:60]}"
        return f"{t.get('name','noteam')}__{ch['name']}"

    if not targets:
        sys.exit('no channels selected (use --channel or --all-my-channels)')

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    all_user_ids, manifest = set(), []

    for t, ch in targets:
        label = label_for(t, ch)
        cdir = out / 'channels' / sanitize(label)
        log(f"\n=== {label}  (id={ch['id']}, total_msg_count={ch.get('total_msg_count')})")
        write_json(cdir / 'channel.json', ch)

        try:
            write_json(cdir / 'channel_stats.json', mm.req(f"/channels/{ch['id']}/stats"))
        except Exception as e:
            log(f"  stats unavailable: {e}")

        try:
            members = list(mm.paged(f"/channels/{ch['id']}/members"))
            write_json(cdir / 'members.json', members)
            all_user_ids.update(m['user_id'] for m in members)
        except Exception as e:
            log(f"  members unavailable: {e}")

        posts, pages = mm.channel_posts(ch['id'], args.per_page)
        ordered = sorted(posts.values(), key=lambda p: (p.get('create_at', 0), p.get('id', '')))
        write_json(cdir / 'posts.json', ordered)
        with (cdir / 'posts.jsonl').open('w', encoding='utf-8') as f:
            for p in ordered:
                f.write(json.dumps(p, ensure_ascii=False) + '\n')
        all_user_ids.update(p.get('user_id') for p in ordered if p.get('user_id'))

        # ---- attachment size tally from post metadata (no download needed) ----
        meta_files, meta_bytes = 0, 0
        for p_ in ordered:
            for f_ in ((p_.get('metadata') or {}).get('files') or []):
                meta_files += 1
                meta_bytes += (f_.get('size') or 0)

        # ---- attachments ----
        file_index, downloaded, failed = [], 0, []
        if not args.no_files:
            fdir = cdir / 'files'
            for p in ordered:
                for fid in (p.get('file_ids') or []):
                    try:
                        info = mm.req(f'/files/{fid}/info')
                    except Exception as e:
                        failed.append({'file_id': fid, 'post_id': p['id'], 'stage': 'info', 'error': str(e)})
                        continue
                    fname = f"{fid}__{sanitize(info.get('name'), 'file')}"
                    dest = fdir / fname
                    rec = {'file_id': fid, 'post_id': p['id'], 'name': info.get('name'),
                           'size': info.get('size'), 'mime_type': info.get('mime_type'),
                           'create_at': info.get('create_at'), 'saved_as': f'files/{fname}'}
                    if dest.exists() and dest.stat().st_size == (info.get('size') or -1):
                        rec['status'] = 'cached'; file_index.append(rec); downloaded += 1; continue
                    try:
                        blob = mm.req(f'/files/{fid}', raw=True)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(blob)
                        rec['status'] = 'ok'; rec['bytes_written'] = len(blob)
                        downloaded += 1
                    except Exception as e:
                        rec['status'] = 'error'; rec['error'] = str(e)
                        failed.append({'file_id': fid, 'post_id': p['id'], 'stage': 'download', 'error': str(e)})
                    file_index.append(rec)
            write_json(cdir / 'files_index.json', file_index)

        entry = {
            'team': t.get('name'), 'team_display': t.get('display_name'),
            'channel': ch['name'], 'channel_display': ch.get('display_name'),
            'channel_id': ch['id'], 'type': ch['type'],
            'server_total_msg_count': ch.get('total_msg_count'),
            'posts_exported': len(ordered),
            'root_posts': sum(1 for p in ordered if not p.get('root_id')),
            'replies': sum(1 for p in ordered if p.get('root_id')),
            'system_posts': sum(1 for p in ordered if (p.get('type') or '').startswith('system_')),
            'deleted_posts': sum(1 for p in ordered if p.get('delete_at')),
            'first_post_at': ordered[0].get('create_at') if ordered else None,
            'last_post_at': ordered[-1].get('create_at') if ordered else None,
            'api_pages': pages,
            'attachments_referenced': sum(len(p.get('file_ids') or []) for p in ordered),
            'attachment_bytes_estimated': meta_bytes,
            'attachment_files_in_metadata': meta_files,
            'attachments_downloaded': downloaded,
            'attachment_failures': failed,
            'dir': str(cdir.relative_to(out)),
        }
        manifest.append(entry)
        log(f"  -> {entry['posts_exported']} posts, {meta_files} attachments "
            f"({meta_bytes/1048576:.1f} MB), downloaded {downloaded}")

    # ---- users ----
    uids = sorted(u for u in all_user_ids if u)
    users = []
    for i in range(0, len(uids), 100):
        try:
            users.extend(mm.req('/users/ids', method='POST', body=uids[i:i + 100]) or [])
        except Exception as e:
            log(f"  user lookup batch failed: {e}")
    write_json(out / 'users.json', users)
    write_json(out / 'users_by_id.json', {u['id']: u.get('username') for u in users})
    write_json(out / 'teams.json', teams)
    write_json(out / 'export_manifest.json', {
        'server': args.url, 'exported_by': me.get('username'),
        'exported_at_unix_ms': int(time.time() * 1000),
        'channels': manifest, 'user_count': len(users),
    })
    log(f"\nDone. {len(manifest)} channel(s), {len(users)} users -> {out}")

if __name__ == '__main__':
    main()
