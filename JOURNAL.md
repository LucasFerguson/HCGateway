# Project Journal

A running log of key points in this project's history, newest first.

---

## 2026-10-17

A big day: browser login, per-device sessions, password changes, HRV and skin
temperature collection. It also turned up several infrastructure problems on
the homelab host that are worth writing down, because some are strange.

**MongoDB crashed six times in two minutes (18:48 to 18:50 UTC).** Each time
mongod aborted with a fatal assertion: WiredTiger error 24, "Too many open
files." The `hcgateway_db` container runs with Docker's default soft limit of
1,024 open files, while MongoDB recommends 64,000, and WiredTiger keeps one
file per collection and per index (417 data files already). A backend test run
that created extra test databases pushed it over. Docker's restart policy
brought it back each time, and record counts were checked afterwards and
matched exactly. Fix still pending: add `ulimits: nofile: 64000` to the `db`
service and recreate the container. This limit will be hit again as accounts
are added.

**The Docker daemon itself segfaulted (19:13 UTC)** in the middle of an image
build, and took every container down with it: both dashboards, all HCGateway
services and MongoDB. They did not come back on their own despite
`restart: unless-stopped`; only dbgate did. The crash was a general-protection
fault inside Go's map hashing (`aeshashbody`) in buildkit's mount code, which
looks more like the CPU state being corrupted than a normal bug. This host runs
the 7.0.2 (pve) kernel that `docker-compose.yml` already flags for crashing
userspace allocators (MongoDB's TCMalloc, SERVER-121912), so it is probably the
same kernel problem showing up in a different program. It was the only daemon
crash in 30 days of logs, and the retried build went through fine. Recovery
was starting the database first, then everything else. Upgrading the host
kernel is the real fix.

**The disk is 88% full (5.1 GB free of 45 GB).** Docker uses 19 GB, of which
the build cache (4.8 GB) and unused images (1.9 GB) are reclaimable. MongoDB
lives on this disk, so running out of space would take the database down.
This also blocks installing an Android build toolchain on the server, which
needs roughly 6 to 8 GB.

**Full analytics recomputes took 18 minutes, not one.** The run documents made
runs look like 60 to 80 seconds, but their `startedAt` is written after the
computation, so it only ever measured the save step. A py-spy dump of the live
worker showed the time going to strain: every workout scanned all 6.3 million
heart-rate segments. A binary search brought a real run down to 4 minutes 46
seconds with identical output.

**HRV now files by night (analytics v8.7), as an experiment.** After the
new app version started collecting heart-rate variability, the dashboard's
numbers ran lower than Google Health's. Google's HRV screen for Sep 18 to
Oct 17 shows a 148 ms monthly average (personal range 70 to 215 ms) and
weekly averages of 123, 121, 143, 171 and 159 ms. The screenshot covers the
same dates as our data (Sep 18 to Oct 16), so the weekly averages make a
fair comparison target.

The readings themselves agree on scale: 1,550 five-minute RMSSD readings,
median 131 ms, 80% between 74 and 185 ms, all taken between about 11 PM and
7:30 AM (the watch only measures HRV asleep, and HRV differs by sleep
stage). The old method took the median per calendar date, which split each
night across two dates, while sleep is filed by wake date.

Seven candidate methods were scored offline against Google's weekly
averages (mean absolute error per week):

| Method | Weekly error |
| --- | --- |
| Median per calendar date (old, v8.6) | 17.7 ms |
| Median per wake date | 16.3 ms |
| Mean per wake date | 15.7 ms |
| **Mean inside the main sleep, per wake date (chosen)** | **15.1 ms** |
| Median inside the main sleep | 16.7 ms |
| Mean in deep sleep only | 13.9 ms (only 20 nights have stages) |
| Mean in light + deep sleep | 14.7 ms (20 nights) |

The chosen method matches how Fitbit describes its nightly HRV and keeps 26
of 27 nights; the one night with no matching main sleep uses all its
readings and is flagged. After deploying, the real v8.7 run scored 15.5 ms
(month average 134 vs Google's 148).

It can't match exactly, and that's now proven: on Sep 28 Google reports
225 ms, but every one of the 11 readings exported for that night is below
195 ms (Oct 4: 210 ms vs a 197 ms maximum). Google's nightly HRV is computed
on Fitbit's side, probably from beat-to-beat data, not from the readings it
writes to Health Connect. Our biggest gaps are on Google's peak nights.
Possible next steps: try deep-sleep-only as an extra series, or show Google's
own nightly value if it ever becomes available through an export.

**WHOOP stopped writing to Health Connect on 2026-10-08.** Resting heart rate,
SpO2 and respiratory rate only ever came from WHOOP, so all three have had no
new data since then. Worth checking the WHOOP app's Health Connect permissions.

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
