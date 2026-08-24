# Project Journal

A running log of key points in this project's history, newest first.

---

## 2026-08-24

Today, the 24th of August, the project took a massive leap forward: the
Android application sync path moved from "best effort while the app stays
alive" toward a much more honest, visible, and resilient uploader for getting
Health Connect data from the phone up onto the server.

- Upgraded `react-native-health-connect` to `3.4.0` and added
  `postinstall: patch-package`.
- Added Health Connect background/history permission support and manifest
  declarations.
- Removed unnecessary Health Connect write permission requests/declarations.
- Fixed full pagination for `readRecords()`.
- Replaced per-record delayed `setTimeout` uploads with awaited chunked
  uploads.
- Added retry/backoff and one-time token refresh retry on `401`.
- Added an in-process sync mutex so manual/background syncs cannot overlap.
- Moved `lastSync` advancement until after confirmed server success.
- Added `lastSuccessfulSyncAt`, `lastSyncAttemptAt`, `lastSyncError`,
  per-type permission/read/upload statuses, pages read, upload request count,
  and effective sync window in the app UI.
- Made the main app view scrollable so the status panel and controls do not
  clip.
- Added an Expo Android 35 compatibility patch at
  `app/patches/expo-modules-core+1.12.18.patch`.
- Updated the Android build wrapper to check for SDK 35.
- Verified the change with `node --check app/App.js`,
  `npx patch-package`, `./gradlew :app:assembleDebug`, and
  `./build-android-apk-on-linux.sh`.
- Produced a release APK at
  `app/android/app/build/outputs/apk/release/app-release.apk`.

This does not yet add durable WorkManager scheduling, and release signing is
still using the existing debug-keystore behavior, but the Android app now has
a far stronger foundation for full-history and unattended sync.

---

## 2026-08-22

Created this journal file to start tracking the history of the project.

Also (see the same-dated work in the README): transitioned this fork to be
built primarily via a local Linux/WSL command-line toolchain instead of
Android Studio, and disconnected the app from the original author's Sentry
infrastructure. Added `build-android-apk-on-linux.sh` (in the repo root) — a wrapper that
runs a setup/dependency preflight check and captures each build's output to a
timestamped log in `build-logs/`.

On Sentry: it's actually a pretty cool tool (watched a YouTube video about
it). For now it's commented out / disconnected from this project, but it
would be cool to explore adding something like it to future projects for
proper crash/error monitoring.

---

## Background / project origins (late 2025)

Near the end of 2025 I got super interested in building a health dashboard
that replicated all the features of Fitbit and Whoop, but totally locally in
my home lab. I landed upon the project created by the current author, and
because I wasn't very familiar with Android development, I decided to fork the
project and build on top of it.

I've been using the app on my phone on and off for the past eight months or
so. Two ongoing problems have come up in that time:

- **Intermittent connectivity.** Right now I use NetBird to make sure the app
  can connect into my home lab, and that connection has been kind of
  intermittent. Whenever I forget to turn NetBird on, the app is unable to
  sync.

- **No reliable background sync.** The app has a larger problem: it's not
  really able to synchronize data in the background, so it has to be open on
  my phone for a sync to happen.

Between these two issues, a lot of the data hasn't actually been captured in
the database I have running in the home lab.
