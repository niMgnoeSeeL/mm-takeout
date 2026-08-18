#!/usr/bin/env bash
#
# mattermost-archiver -- export your Mattermost data and make it readable.
#
#   ./archive.sh            full run: messages, attachments, viewer
#   ./archive.sh list       show your teams and channels, then stop
#   ./archive.sh messages   messages + metadata only (no attachments)
#   ./archive.sh attachments  download attachments for an existing export
#   ./archive.sh viewer     rebuild the HTML viewer from an existing export
#
set -uo pipefail
cd "$(dirname "$0")" || exit 1

OUT="${MM_OUT:-./export}"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }
die()  { printf '\n\033[31m%s\033[0m\n' "$*"; exit 1; }

command -v python3 >/dev/null || die "python3 is required."

# ---------------------------------------------------------------- credentials
[ -f .env ] && . ./.env

if [ -z "${MM_URL:-}" ]; then
  read -r -p "Mattermost server URL (e.g. https://chat.example.com): " MM_URL
fi
MM_URL="${MM_URL%/}"

if [ -z "${MM_TOKEN:-}" ]; then
  cat <<'EOF'

Authentication. Two options:

  1. Personal access token -- Profile > Security > Personal Access Tokens.
     If that section is missing, your admin has not enabled them.

  2. Session cookie -- open Mattermost in Chrome, then
     DevTools (F12 / Cmd+Opt+I) > Application > Cookies > your server,
     and copy the value of MMAUTHTOKEN. Expires when you log out.

EOF
  read -r -p "Token: " MM_TOKEN
fi
export MM_URL MM_TOKEN
[ -n "$MM_TOKEN" ] || die "No token given."

# fail early and clearly rather than deep inside a loop
python3 - <<'PY' || die "Could not authenticate. Check the URL and token."
import json, os, sys, urllib.request, urllib.error
url = os.environ['MM_URL'].rstrip('/') + '/api/v4/users/me'
r = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + os.environ['MM_TOKEN']})
try:
    me = json.load(urllib.request.urlopen(r, timeout=30))
except urllib.error.HTTPError as e:
    print(f"  server said HTTP {e.code}", file=sys.stderr); sys.exit(1)
except Exception as e:
    print(f"  {e}", file=sys.stderr); sys.exit(1)
print(f"  authenticated as {me['username']} <{me.get('email','')}>")
PY

CMD="${1:-all}"

case "$CMD" in
  list)
    say "Your teams"
    python3 mmarchive/export.py --list
    for t in $(python3 mmarchive/export.py --list | awk '{print $1}'); do
      say "Channels in $t"
      python3 mmarchive/export.py --list-channels "$t"
    done
    exit 0
    ;;

  attachments)
    [ -d "$OUT" ] || die "No export found at $OUT. Run ./archive.sh messages first."
    say "Downloading attachments"
    WORKERS="${WORKERS:-5}" python3 mmarchive/fetch_files.py "$OUT" || die "Attachment download failed."
    say "Rebuilding viewer"
    python3 mmarchive/build_viewer.py "$OUT"
    exit 0
    ;;

  viewer)
    [ -d "$OUT" ] || die "No export found at $OUT."
    python3 mmarchive/build_viewer.py "$OUT"
    exit 0
    ;;
esac

# ------------------------------------------------------------------ what to get
TYPES="${MM_TYPES:-}"
if [ -z "$TYPES" ]; then
  cat <<'EOF'

What should be exported?

  1  Public and private channels          (O P)
  2  Channels plus group DMs              (O P G)
  3  Everything, including 1:1 DMs        (O P D G)

EOF
  read -r -p "Choice [1/2/3, default 3]: " c
  case "${c:-3}" in
    1) TYPES="OP" ;;
    2) TYPES="OPG" ;;
    *) TYPES="ODPG" ;;
  esac
fi

say "Exporting messages and metadata to $OUT"
warn "Tip: this pass is quick. Attachments are the slow part and come after."
python3 mmarchive/export.py --all-my-channels --types "$TYPES" --no-files --out "$OUT" \
  || die "Export failed."

python3 - "$OUT" <<'PY'
import json, sys
m = json.load(open(f'{sys.argv[1]}/export_manifest.json'))
ch = m['channels']
print()
print(f"  channels    {len(ch)}")
print(f"  messages    {sum(c['posts_exported'] for c in ch):,}")
print(f"  attachments {sum(c['attachment_files_in_metadata'] for c in ch):,} "
      f"({sum(c['attachment_bytes_estimated'] for c in ch)/1048576:,.0f} MB)")
PY

if [ "$CMD" = "messages" ]; then
  say "Building viewer"
  python3 mmarchive/build_viewer.py "$OUT"
  say "Done. Open $OUT/index.html"
  exit 0
fi

echo
read -r -p "Download the attachments now? [Y/n] " a
if [[ "${a:-y}" =~ ^[Nn]$ ]]; then
  say "Skipping attachments. Run ./archive.sh attachments later."
else
  say "Downloading attachments"
  WORKERS="${WORKERS:-5}" python3 mmarchive/fetch_files.py "$OUT" || warn "Some attachments failed; see $OUT/download_report.json"
fi

say "Building viewer"
python3 mmarchive/build_viewer.py "$OUT"

say "Done."
cat <<EOF

  Read it:    open $OUT/index.html
  Verify it:  python3 mmarchive/verify.py $OUT
  Import into a real Mattermost:  ./import-to-mattermost.sh

  Your token is still valid. If you used a session cookie, revoke it with
  Profile > Security > View and Log Out of Active Sessions.

EOF
