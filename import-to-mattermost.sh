#!/usr/bin/env bash
#
# Load an export into a local Mattermost running in Docker, so you can browse
# the archive in the real UI.
#
#   ./import-to-mattermost.sh
#
# Requires Docker (Desktop on macOS/Windows) to be running.
#
set -uo pipefail
cd "$(dirname "$0")" || exit 1

OUT="${MM_OUT:-./export}"
ZIP="${MM_ZIP:-mattermost-import.zip}"
COMPOSE_DIR="mattermost-server"
PASSWORD="${MM_IMPORT_PASSWORD:-Archive-2026!}"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }
die()  { printf '\n\033[31m%s\033[0m\n' "$*"; exit 1; }

command -v docker >/dev/null || die "Docker not found. Install Docker Desktop and start it."
docker info >/dev/null 2>&1 || die "Docker is installed but not running. Start it and try again."
command -v python3 >/dev/null || die "python3 is required."
[ -d "$OUT" ] || die "No export at $OUT. Run ./archive.sh first."

OWNER=$(python3 -c "import json;print(json.load(open('$OUT/export_manifest.json'))['exported_by'])")

if [ "$(uname -m)" = "arm64" ] || [ "$(uname -m)" = "aarch64" ]; then
  warn "arm64 detected. Mattermost publishes amd64 images only, so the server runs"
  warn "under emulation. It works, but the import is slower than native."
  warn "On macOS, enable Docker Desktop > Settings > General > \"Use Rosetta for"
  warn "x86/amd64 emulation on Apple Silicon\" if the container fails to start."
fi

NFILES=$(find "$OUT" -path '*/files/*' -type f 2>/dev/null | wc -l | tr -d ' ')
say "Export has $NFILES downloaded attachments."
EXTRA=""
if [ "$NFILES" -eq 0 ]; then
  warn "No attachments downloaded; importing messages only."
  EXTRA="--no-files"
fi

say "1/5  Building the import archive"
if [ -s "$ZIP" ]; then
  read -r -p "     $ZIP exists ($(du -h "$ZIP" | cut -f1)). Reuse it? [y/N] " r
  [[ "${r:-n}" =~ ^[Yy]$ ]] && BUILD=0 || BUILD=1
else
  BUILD=1
fi
if [ "$BUILD" = "1" ]; then
  python3 mmarchive/to_bulk_import.py "$OUT" --out "$ZIP" --password "$PASSWORD" $EXTRA \
    || die "Conversion failed."
fi

say "2/5  Starting Mattermost + Postgres"
( cd "$COMPOSE_DIR" && docker compose up -d ) || die "docker compose up failed."

say "3/5  Waiting for the server"
for i in $(seq 1 120); do
  if [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8065/api/v4/system/ping)" = "200" ]; then
    echo "     ready after ~$((i*3))s"; UP=1; break
  fi
  sleep 3
done
[ "${UP:-0}" = "1" ] || die "Server never came up. Check: cd $COMPOSE_DIR && docker compose logs mattermost"

say "4/5  Staging the archive inside the server"
# The Mattermost image ships without a shell, so we cannot mkdir inside it.
# A throwaway alpine container mounted on the same named volume does the job.
docker run --rm -v mmarchive_data:/data -v "$PWD":/host alpine \
  sh -c "mkdir -p /data/import && cp /host/$ZIP /data/import/ && chmod -R 777 /data/import" \
  || die "Could not stage the archive into the Docker volume."

# --bypass-upload needs an ABSOLUTE path; a bare filename reports "file doesn't exist"
# even when `mmctl import list available` shows it.
JOB=$( cd "$COMPOSE_DIR" && docker compose exec -T mattermost \
       mmctl --local import process --bypass-upload "/mattermost/data/import/$ZIP" 2>&1 \
       | grep -o 'ID: [a-z0-9]*' | awk '{print $2}' )
[ -n "$JOB" ] || die "Import job was not created."
echo "     job $JOB"

say "5/5  Importing"
START=$(date +%s)
while true; do
  OUTP=$( cd "$COMPOSE_DIR" && docker compose exec -T mattermost \
          mmctl --local import job show "$JOB" 2>/dev/null )
  ST=$( echo "$OUTP" | grep -m1 'Status:' | awk '{print $2}' )
  printf '\r     status: %-12s elapsed: %ss   ' "$ST" "$(( $(date +%s) - START ))"
  case "$ST" in
    success) echo; break ;;
    error|canceled)
      echo; echo "$OUTP" | grep -i error
      die "Import failed. Detail:
  cd $COMPOSE_DIR && docker compose exec -T mattermost mmctl --local import job show $JOB" ;;
  esac
  sleep 8
done

say "Done."
cat <<EOF

  Open       http://localhost:8065
  Username   $OWNER
  Password   $PASSWORD

  Everyone else got a random password, so only your account can log in.
  Your account is a system admin, so every imported channel is visible.

  Stop:              cd $COMPOSE_DIR && docker compose stop
  Start again:       cd $COMPOSE_DIR && docker compose start
  Delete everything: cd $COMPOSE_DIR && docker compose down -v

EOF
