# build-logs

Output logs from local Android builds.

Every run of [`../build-android-apk-on-linux.sh`](../build-android-apk-on-linux.sh)
writes a timestamped `build_YYYY-MM-DD_HH-MM-SS.log` here, capturing the full
build output (and a small header with the Java version, env vars, and git
commit) so past attempts can be searched back through when debugging errors.

The `.log` files are git-ignored (only this README is tracked). Search old
logs with, e.g., `grep -l -i error build-logs/*.log`.
