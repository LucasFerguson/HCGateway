#!/usr/bin/env bash
#
# build-android-apk-on-linux.sh — Build the HCGateway Android APK on Linux / WSL.
#
# What it does:
#   1. Runs a dependency / setup preflight check (Java 17, Android SDK,
#      env vars, google-services.json, node_modules, patch-package, gradlew).
#   2. Runs the release build, streaming output to the terminal AND saving a
#      timestamped log to build-logs/ so every attempt is captured for later
#      searching / documentation.
#
# Usage (run from anywhere; this script lives in the repo root):
#   ./build-android-apk-on-linux.sh
#
# The APK, on success, lands at:
#   app/android/app/build/outputs/apk/release/app-release.apk
#
# See README.md ("Building locally on Linux / WSL") for the one-time setup.

set -uo pipefail

# --- Locate the repo root (this script lives in the repo root) --------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$REPO_ROOT/app"
ANDROID_DIR="$APP_DIR/android"
LOG_DIR="$REPO_ROOT/build-logs"

# --- Colors (only if stdout is a terminal) ----------------------------------
if [ -t 1 ]; then
  RED=$'\033[31m'; GRN=$'\033[32m'; YLW=$'\033[33m'; BLU=$'\033[34m'; BLD=$'\033[1m'; RST=$'\033[0m'
else
  RED=; GRN=; YLW=; BLU=; BLD=; RST=
fi

ok()   { echo "  ${GRN}✔${RST} $1"; }
warn() { echo "  ${YLW}!${RST} $1"; }
fail() { echo "  ${RED}x${RST} $1"; }

# --- Preflight dependency / setup check -------------------------------------
# Collects problems into an array; if any are fatal we stop before building.
PROBLEMS=()

echo "${BLD}${BLU}==> Dependency & setup check${RST}"

# Java 17
if command -v java >/dev/null 2>&1; then
  JVER="$(java -version 2>&1 | head -1)"
  if echo "$JVER" | grep -q '"17'; then
    ok "Java 17 found — $JVER"
  else
    warn "Java found but not 17 — $JVER (build expects Java 17)"
    PROBLEMS+=("Java is not version 17")
  fi
else
  fail "java not found on PATH"
  PROBLEMS+=("Java 17 not installed (sudo apt install openjdk-17-jdk)")
fi

# JAVA_HOME
if [ -n "${JAVA_HOME:-}" ] && [ -x "${JAVA_HOME:-}/bin/java" ]; then
  ok "JAVA_HOME set and valid — $JAVA_HOME"
else
  fail "JAVA_HOME unset or invalid (currently: '${JAVA_HOME:-}')"
  PROBLEMS+=("JAVA_HOME must point to a JDK 17, e.g. /usr/lib/jvm/java-17-openjdk-amd64")
fi

# ANDROID_HOME + sdk components
# Resolve the SDK path from ANDROID_HOME, or fall back to sdk.dir in
# app/android/local.properties (gradle reads that file directly, so a build
# can succeed even when ANDROID_HOME is not exported in the shell).
SDK_DIR=""
if [ -n "${ANDROID_HOME:-}" ] && [ -d "${ANDROID_HOME:-}" ]; then
  SDK_DIR="$ANDROID_HOME"
  ok "ANDROID_HOME set — $SDK_DIR"
elif [ -f "$ANDROID_DIR/local.properties" ]; then
  SDK_DIR="$(grep -E '^sdk\.dir=' "$ANDROID_DIR/local.properties" | head -1 | cut -d= -f2-)"
  if [ -n "$SDK_DIR" ] && [ -d "$SDK_DIR" ]; then
    ok "SDK found via local.properties sdk.dir — $SDK_DIR (ANDROID_HOME unset)"
  else
    SDK_DIR=""
  fi
fi

if [ -n "$SDK_DIR" ]; then
  [ -d "$SDK_DIR/platform-tools" ] && ok "platform-tools present" \
    || { warn "platform-tools missing"; PROBLEMS+=("Run: sdkmanager --install \"platform-tools\""); }
  if ls "$SDK_DIR"/platforms/android-35 >/dev/null 2>&1; then
    ok "platforms;android-35 present"
  else
    warn "platforms;android-35 missing"
    PROBLEMS+=("Run: sdkmanager --install \"platforms;android-35\"")
  fi
  if ls "$SDK_DIR"/build-tools/35.* >/dev/null 2>&1; then
    ok "build-tools;35.x present"
  else
    warn "build-tools;35.x missing"
    PROBLEMS+=("Run: sdkmanager --install \"build-tools;35.0.0\"")
  fi
else
  fail "Android SDK not found (ANDROID_HOME unset and no valid sdk.dir in app/android/local.properties)"
  PROBLEMS+=("Set ANDROID_HOME to the SDK, or add sdk.dir=<path> to app/android/local.properties")
fi

# sdkmanager on PATH (nice-to-have)
command -v sdkmanager >/dev/null 2>&1 && ok "sdkmanager on PATH" \
  || warn "sdkmanager not on PATH (only needed for installing SDK packages)"

# Firebase config in both required spots
for f in "$APP_DIR/firebase/google-services.json" "$ANDROID_DIR/app/google-services.json"; do
  if [ -f "$f" ]; then
    ok "google-services.json present — ${f#$REPO_ROOT/}"
  else
    fail "Missing google-services.json — ${f#$REPO_ROOT/}"
    PROBLEMS+=("Copy your google-services.json to ${f#$REPO_ROOT/}")
  fi
done

# node_modules
if [ -d "$APP_DIR/node_modules" ]; then
  ok "node_modules present"
else
  fail "node_modules missing"
  PROBLEMS+=("Run: (cd app && npm install)")
fi

# patch-package applied (the foreground-service patch)
PATCH_MARKER="$APP_DIR/node_modules/@supersami/rn-foreground-service"
if [ -d "$PATCH_MARKER" ]; then
  ok "@supersami/rn-foreground-service present (run 'npx patch-package' if you just reinstalled)"
else
  warn "@supersami/rn-foreground-service not found — did npm install run?"
fi

# gradlew executable
if [ -x "$ANDROID_DIR/gradlew" ]; then
  ok "gradlew is executable"
else
  warn "gradlew is not executable — fixing with chmod +x"
  chmod +x "$ANDROID_DIR/gradlew" && ok "gradlew made executable" \
    || PROBLEMS+=("Run: chmod +x app/android/gradlew")
fi

# --- Stop if any fatal problems ---------------------------------------------
if [ "${#PROBLEMS[@]}" -gt 0 ]; then
  echo
  echo "${BLD}${RED}==> Setup incomplete — fix these before building:${RST}"
  for p in "${PROBLEMS[@]}"; do echo "  ${RED}•${RST} $p"; done
  echo
  echo "See README.md → 'Building locally on Linux / WSL' for the full setup."
  exit 1
fi

echo "${GRN}All checks passed.${RST}"
echo

# Safety net: even though sentry.gradle is not applied in this fork's native
# build, this makes sure the Sentry auto source-map upload stays disabled if a
# future Expo prebuild ever regenerates the native project with Sentry wired
# back in. sentry.gradle honors this env var (returns early when it's 'true').
export SENTRY_DISABLE_AUTO_UPLOAD=true

# --- Build, capturing output to a timestamped log ---------------------------
mkdir -p "$LOG_DIR"
# Filename must be deterministic-ish and sortable. Use `date` for a real
# timestamp; if `date` somehow fails, fall back to an epoch seconds value.
STAMP="$(date +%Y-%m-%d_%H-%M-%S 2>/dev/null || echo "build-$(date +%s)")"
LOG_FILE="$LOG_DIR/build_${STAMP}.log"

echo "${BLD}${BLU}==> Building release APK${RST}"
echo "  Log: ${LOG_FILE#$REPO_ROOT/}"
echo "  (streaming below and saving to the log file simultaneously)"
echo

# Header inside the log for context when searching back later.
{
  echo "HCGateway Android build log"
  echo "timestamp:  $STAMP"
  echo "java:       $(java -version 2>&1 | head -1)"
  echo "SDK dir:    ${SDK_DIR:-<from local.properties>}"
  echo "ANDROID_HOME=${ANDROID_HOME:-<unset>}"
  echo "JAVA_HOME=${JAVA_HOME:-<unset>}"
  echo "git HEAD:   $(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null) $(git -C "$REPO_ROOT" log -1 --format=%s 2>/dev/null)"
  echo "command:    ./gradlew assembleRelease"
  echo "=========================================================="
} > "$LOG_FILE"

# Run the build. `tee -a` streams to terminal AND appends to the log.
# PIPESTATUS[0] preserves gradle's exit code through the tee pipe.
( cd "$ANDROID_DIR" && ./gradlew assembleRelease ) 2>&1 | tee -a "$LOG_FILE"
BUILD_RC="${PIPESTATUS[0]}"

echo | tee -a "$LOG_FILE"
if [ "$BUILD_RC" -eq 0 ]; then
  APK="$ANDROID_DIR/app/build/outputs/apk/release/app-release.apk"
  {
    echo "=========================================================="
    echo "RESULT: SUCCESS (exit $BUILD_RC)"
    [ -f "$APK" ] && echo "APK: ${APK#$REPO_ROOT/} ($(du -h "$APK" | cut -f1))"
  } | tee -a "$LOG_FILE"
  echo "${BLD}${GRN}==> Build succeeded.${RST}"
  [ -f "$APK" ] && echo "  APK: ${APK#$REPO_ROOT/}"
else
  {
    echo "=========================================================="
    echo "RESULT: FAILED (exit $BUILD_RC)"
  } | tee -a "$LOG_FILE"
  echo "${BLD}${RED}==> Build failed (exit $BUILD_RC).${RST}"
  echo "  Full log saved to: ${LOG_FILE#$REPO_ROOT/}"
  echo "  Tip: grep past logs for an error, e.g.:"
  echo "       grep -l -i 'error' build-logs/*.log"
fi

exit "$BUILD_RC"
