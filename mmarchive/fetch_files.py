#!/usr/bin/env python3
"""Parallel attachment downloader for an mm_export.py output tree."""
import json, glob, os, sys, time, threading, urllib.request, urllib.error, re
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ['MM_URL'].rstrip('/') + '/api/v4'
TOK  = os.environ['MM_TOKEN']
ROOT = sys.argv[1] if len(sys.argv) > 1 else 'export'
WORKERS = int(os.environ.get('WORKERS', '5'))

def sanitize(n, fb='file'):
    return re.sub(r'[^A-Za-z0-9._-]+', '_', (n or '').strip())[:120] or fb

jobs = []
for pj in sorted(glob.glob(f'{ROOT}/channels/*/posts.json')):
    cdir = os.path.dirname(pj)
    for p in json.load(open(pj)):
        for fi in ((p.get('metadata') or {}).get('files') or []):
            fid = fi['id']
            dest = os.path.join(cdir, 'files', f"{fid}__{sanitize(fi.get('name'))}")
            jobs.append({'id': fid, 'dest': dest, 'size': fi.get('size') or 0,
                         'name': fi.get('name'), 'post_id': p['id'],
                         'mime': fi.get('mime_type'), 'channel': os.path.basename(cdir)})

total_bytes = sum(j['size'] for j in jobs)
lock = threading.Lock()
state = {'done': 0, 'bytes': 0, 'fail': []}
t0 = time.time()

def fetch(j):
    if os.path.exists(j['dest']) and os.path.getsize(j['dest']) == j['size'] and j['size'] > 0:
        with lock:
            state['done'] += 1; state['bytes'] += j['size']
        return
    os.makedirs(os.path.dirname(j['dest']), exist_ok=True)
    for attempt in range(5):
        try:
            r = urllib.request.Request(f"{BASE}/files/{j['id']}",
                                       headers={'Authorization': 'Bearer ' + TOK,
                                                'User-Agent': 'mm-export/1.0'})
            with urllib.request.urlopen(r, timeout=300) as resp:
                blob = resp.read()
            tmp = j['dest'] + '.part'
            with open(tmp, 'wb') as f:
                f.write(blob)
            os.replace(tmp, j['dest'])
            with lock:
                state['done'] += 1; state['bytes'] += len(blob)
                n, b = state['done'], state['bytes']
            if n % 25 == 0:
                el = time.time() - t0
                print(f"{n}/{len(jobs)}  {b/1048576:.0f}/{total_bytes/1048576:.0f} MB  "
                      f"{b/1048576/el:.2f} MB/s  eta {((total_bytes-b)/max(b/el,1))/60:.1f} min",
                      flush=True)
            return
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5); continue
            if e.code in (500, 502, 503, 504):
                time.sleep(2 ** attempt); continue
            with lock: state['fail'].append({**j, 'error': f'HTTP {e.code}'})
            return
        except Exception as e:
            if attempt == 4:
                with lock: state['fail'].append({**j, 'error': str(e)})
                return
            time.sleep(2 ** attempt)

print(f"{len(jobs)} files, {total_bytes/1048576:.0f} MB, {WORKERS} workers", flush=True)
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    list(ex.map(fetch, jobs))

el = time.time() - t0
json.dump({'total': len(jobs), 'ok': state['done'], 'failed': state['fail'],
           'bytes': state['bytes'], 'seconds': el},
          open(f'{ROOT}/download_report.json', 'w'), indent=2)
print(f"\nDONE {state['done']}/{len(jobs)} files, {state['bytes']/1048576:.0f} MB "
      f"in {el/60:.1f} min, {len(state['fail'])} failures", flush=True)
