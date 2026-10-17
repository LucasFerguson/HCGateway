#!/usr/bin/env bash
#
# publish-apk.sh — Publish an HCGateway Android APK so the dashboard can serve it.
#
# Copies the APK into releases/ as hcgateway-<version>.apk and
# hcgateway-latest.apk, then writes releases/latest.json:
#   {version, versionCode, fileName, sizeBytes, sha256, publishedAt}
# The version comes from app/app.json (expo.version, expo.android.versionCode),
# so bump those before building. The dashboard (health-connect-dashboard)
# mounts releases/ read-only and serves the latest APK at /download/android.
# Only the newest 5 versioned APKs are kept, to save disk space.
#
# Usage (on the server):
#   scripts/publish-apk.sh [path-to-apk]
# Default APK path: app/android/app/build/outputs/apk/release/app-release.apk
#
# From your PC, after building the APK there:
#   scp app-release.apk root@192.168.8.239:/root/HCGateway/releases/incoming.apk && ssh root@192.168.8.239 /root/HCGateway/scripts/publish-apk.sh /root/HCGateway/releases/incoming.apk
# (incoming.apk is removed once it has been published.)
#
# Note: app.json is read from this server's checkout, so it must have the same
# version as the build (commit and pull the bump before publishing).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_JSON="$REPO_ROOT/app/app.json"
RELEASES_DIR="$REPO_ROOT/releases"
DEFAULT_APK="$REPO_ROOT/app/android/app/build/outputs/apk/release/app-release.apk"
KEEP=5

APK="${1:-$DEFAULT_APK}"

die() { echo "publish-apk: $*" >&2; exit 1; }

[ -f "$APK" ] || die "APK not found: $APK"
[ -f "$APP_JSON" ] || die "app.json not found: $APP_JSON"
command -v python3 >/dev/null || die "python3 is required"
command -v sha256sum >/dev/null || die "sha256sum is required"

# A quick sanity check: APKs are zip files, which start with "PK".
[ "$(head -c 2 "$APK")" = "PK" ] || die "$APK does not look like an APK (not a zip file)"

read -r VERSION VERSION_CODE < <(python3 - "$APP_JSON" <<'PY'
import json, sys
expo = json.load(open(sys.argv[1]))["expo"]
print(expo["version"], expo["android"]["versionCode"])
PY
)
[[ "$VERSION" =~ ^[0-9A-Za-z.+-]+$ ]] || die "unexpected expo.version: $VERSION"
[[ "$VERSION_CODE" =~ ^[0-9]+$ ]] || die "unexpected expo.android.versionCode: $VERSION_CODE"

mkdir -p "$RELEASES_DIR"
FILE_NAME="hcgateway-$VERSION.apk"
VERSIONED="$RELEASES_DIR/$FILE_NAME"
LATEST="$RELEASES_DIR/hcgateway-latest.apk"

# Write to temp files and rename, so the dashboard never serves a partial APK.
cp "$APK" "$VERSIONED.tmp" && mv -f "$VERSIONED.tmp" "$VERSIONED"
cp "$VERSIONED" "$LATEST.tmp" && mv -f "$LATEST.tmp" "$LATEST"

SIZE_BYTES="$(stat -c %s "$LATEST")"
SHA256="$(sha256sum "$LATEST" | cut -d' ' -f1)"
PUBLISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

python3 - "$RELEASES_DIR/latest.json.tmp" "$VERSION" "$VERSION_CODE" "$FILE_NAME" "$SIZE_BYTES" "$SHA256" "$PUBLISHED_AT" <<'PY'
import json, sys
out, version, code, name, size, sha, at = sys.argv[1:]
with open(out, "w") as f:
    json.dump({"version": version, "versionCode": int(code), "fileName": name,
               "sizeBytes": int(size), "sha256": sha, "publishedAt": at}, f, indent=2)
    f.write("\n")
PY
mv -f "$RELEASES_DIR/latest.json.tmp" "$RELEASES_DIR/latest.json"

# The uploaded copy from a PC is no longer needed.
if [ "$(realpath "$APK")" = "$(realpath -m "$RELEASES_DIR/incoming.apk")" ]; then
  rm -f "$APK"
fi

# Keep only the newest $KEEP versioned APKs (by modification time).
mapfile -t OLD < <(ls -1t "$RELEASES_DIR"/hcgateway-*.apk 2>/dev/null \
  | grep -v '/hcgateway-latest\.apk$' | tail -n +$((KEEP + 1)))
for f in "${OLD[@]}"; do
  rm -f -- "$f" && echo "Removed old release $(basename "$f")"
done

echo "Published HCGateway v$VERSION (versionCode $VERSION_CODE)"
echo "  $VERSIONED ($SIZE_BYTES bytes)"
echo "  sha256 $SHA256"
echo "  served by the dashboard at /download/android"
