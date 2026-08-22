# Project Journal

A running log of key points in this project's history, newest first.

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
