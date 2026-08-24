# Lucas Notes

running project on 2025-04-15 on server pve3
API url
`http://l192.168.8.239:6644`

## Scratchpad / TODO (for me + coding agents working in this repo)

This is a shared task list. Coding agents reading this directory: please check
here for known issues and open work, and update it as items are resolved.

Context: this repo contains the mobile client, REST API, MongoDB configuration,
and the background analytics backend for a self-hosted health dashboard.

For the current implementation checkpoint and recommended next work, start with
[doc/session-handoff.md](doc/session-handoff.md).

- [x] **Backend analytics engine.** Ported the dashboard's
  `health-analytics-v6` TypeScript behavior to Python and extended it as
  `health-analytics-v8.1`, including multi-device
  sleep reconciliation, daily metrics, sleep debt/consistency, and experimental
  healthspan, Recovery, and cardiovascular strain estimates. Recovery and strain
  are provisional, non-clinical heuristics with explicit quality gates and TODOs
  for longitudinal validation. A separate leased worker writes encrypted, immutable
  MongoDB runs; authenticated snapshot, daily, status, config, rebuild, and
  inventory endpoints are frontend-ready. See
  [doc/frontend-data-model.md](doc/frontend-data-model.md).

- [ ] **Review `/revoke` HTTP-method discrepancy.** The API docs
  ([doc/api-documentation.yml](doc/api-documentation.yml)) document
  `/api/v2/revoke` as **POST**, but the implementation
  ([api/apiVersions/v2/routes.py](api/apiVersions/v2/routes.py)) defines it as
  **DELETE**. Inherited verbatim from upstream. No runtime impact yet (the app
  doesn't call `/revoke`), but a client following the docs would get a 405.
  Decide which is canonical and make them match.

- [x] **In-app sync status UI (per-type breakdown).** Done. Added a module-level
  `syncStatus` store + listener that `sync()` in [app/App.js](app/App.js) updates
  live; the App component renders a status panel with overall progress and a
  per-record-type list with status badges (pending / syncing / done / failed /
  none). Frontend-only. Follow-ups if wanted: persist last-run summary across
  app restarts; surface per-type error text on tap.

- [x] **Sync history beyond 30 days.** Done in code; **needs on-device testing.**
  What was done in [app/App.js](app/App.js): replaced the hardcoded `getDate() - 29`
  windows with a configurable, persisted `historyDays`; added a "History window
  (days back)" input and a **"Sync Full History"** button; and added
  `requestHistoryPermission()` which asks for the raw Android permission
  `android.permission.health.READ_HEALTH_DATA_HISTORY` via `PermissionsAndroid`
  whenever the window reaches past 30 days. Also **fixed the manifest permission
  string** (the upstream merge added the invalid
  `android.permission.PERMISSION_READ_HEALTH_DATA_HISTORY`; corrected to
  `android.permission.health.READ_HEALTH_DATA_HISTORY`) and added it to
  `app.json` permissions for prebuild durability.

  ⚠️ **Caveats to verify on-device (Android 14+):**
  - The JS lib `react-native-health-connect@3.2.1` bundles
    `androidx.health.connect:connect-client:1.1.0-alpha06`, which predates
    formal history-permission support (added ~alpha07). We request the raw
    permission string directly to work around this. Confirm the OS actually
    shows the "access past data" prompt and that reads past 30 days return data.
  - If it does not work, options: bump `react-native-health-connect` (and the
    bundled connect-client), or verify the permission appears in Health
    Connect's app settings. Document findings here.

- [x] **Android sync tolerates malformed records and tracks local coverage.**
  Done in [app/App.js](app/App.js). Upload batches now pre-filter records with
  impossible timestamps, such as `endTime` before `startTime`, and recursively
  split any server-rejected batch to isolate the bad record instead of losing
  the rest of the batch. The in-app sync panel reports invalid/skipped records.

  The app also keeps an advisory local `syncedDaysByType` map in AsyncStorage
  so repeated full-history syncs can skip days it has already uploaded. This
  tracker is intentionally local and can be stale after reinstall, app data
  reset, or major changes; the app includes both a **Force re-upload locally
  tracked days** toggle to slam data back into the server and a **Reset Local
  Day Tracker** button. Duplicate raw records are safe because the server
  upserts by Health Connect record ID.

  The **Refresh Server Inventory** button calls
  `GET /api/v2/analytics/inventory` and displays high-level raw database
  coverage, including total records, overall range, and Steps range/count when
  present.


# HCGateway
HCGateway is a platform to let developers connect to the Health Connect API on Android via a REST API. You can view the documentation for the REST API [here](https://hcgateway.shuchir.dev/)

The platform consists of four parts:
- A REST API/server
- A background analytics worker
- MongoDB for raw and prepared data
- A mobile application that pings the server periodically

> [!NOTE]
> This project is still in development. The API may change without notice. The mobile application is also in development and may not work as expected. Please report any issues you find.

> [!IMPORTANT]
> The database was recently migrated from Appwrite to MongoDB. If you were using the Appwrite version, you will need to migrate your data to the new database. You can find the migration script in the `scripts/` folder. You will need to install the `appwrite` and `pymongo` libraries to run the script, then run the script with the following command: `python3 migrate_1.5.0.py`.


## How it Works
- The mobile application pings the server every 2 hours to send data. The following data types are supported-
    - Active Calories Burned (`activeCaloriesBurned`)
    - Basal Body Temperature (`basalBodyTemperature`)
    - Basal Metabolic Rate (`basalMetabolicRate`)
    - Blood Glucose (`bloodGlucose`)
    - Blood Pressure (`bloodPressure`)
    - Body Fat (`bodyFat`)
    - Body Temperature (`bodyTemperature`)
    - Bone Mass (`boneMass`)
    - Cervical Mucus (`cervicalMucus`)
    - Distance (`distance`)
    - Exercise (`exerciseSession`)
    - Elevation Gained (`elevationGained`)
    - Floors Climbed (`floorsClimbed`)
    - Heart Rate (`heartRate`)
    - Height (`height`)
    - Hydration (`hydration`)
    - Lean Body Mass (`leanBodyMass`)
    - Menstruation Flow (`menstruationFlow`)
    - Menstruation Period (`menstruationPeriod`)
    - Nutrition (`nutrition`)
    - Ovulation Test (`ovulationTest`)
    - Oxygen Saturation (`oxygenSaturation`)
    - Power (`power`)
    - Respiratory Rate (`respiratoryRate`)
    - Resting Heart Rate (`restingHeartRate`)
    - Sleep (`sleepSession`)
    - Speed (`speed`)
    - Steps (`steps`)
    - StepsCadence (`stepsCadence`)
    - Total Calories Burned (`totalCaloriesBurned`)
    - VO2 Max (`vo2Max`)
    - Weight (`weight`)
    - Wheelchair Pushes (`wheelchairPushes`)

Support for more types is planned for the future.

- Each sync takes approximatly 15 minutes
- `GET /api/v2/sync/status` exposes server-observed upload activity. It reports
  active for 120 seconds after the latest authenticated phone upload and is
  also included as `phoneSync` in `GET /api/v2/analytics/status`.
- The server encrypts the data using Fernet encryption, then stores it in a mongo database.
- The server exposes an API to let developers login and get the data for their users.

The platform allows two-way sync, which means you can make changes to your local Health Connect store remotely via REST api.

## Get Started
- There is a live instance hosted at https://api.hcgateway.shuchir.dev/ that you can use. You can also host your own instance. To learn more on Self Hosting, skip down to the Self Hosting section.
- You can install the mobile application through the APK file. You can find the latest APK file in the releases section of this repository.
- The minimum requirement for the APK file is Android Oreo (8.0)
- Once you install the Android APK file, signup by entering a username and password
- Once you see a screen showing your user id, you have successfully signed up. Your data will sync in 2 hours. This is customizable. You also have the option to force a sync any time through the application.

## Database
### Users Structure
```
users {
    _id: string
    username: string
    password: string
    fcmToken: string
    expiry: datetime
    token: string
    refresh: string
}
```
> [!NOTE]
> The password of the user encrypted using Argon 2 format. The password is never stored as is, and cannot be retrieved through any API.

### Database Structure
```
hcgateway_[user_id]: string {
    dataType: string {
        _id: string
        data: string
        id: string
        start: datetime
        end: datetime
        app: string
    }
}
```

### Parameters
- `$id` - The ID of the object. 
- `data` - The data of the object encrypted using Fernet. When asked for through the API, the data will be decrypted for you using the user's hashed password found from the user id.
- `id` - The ID of the object- This is the same as `_id` and is only kept for backward compatibility. May be removed in future versions.
- `start` - The start date and time of the object
- `end` - The end date and time of the object. Might not be present for some objects.
- `app` - The app package string that the object was synced from.


## REST API
The documentation for the REST API can be found at https://hcgateway.shuchir.dev/

## Mobile Application
The mobile application is a React Native Android app that syncs Health Connect
records to the server every 2 hours by default. It starts a foreground service
for recurring sync work.

The sync path is intentionally defensive:

- Health Connect reads are paginated.
- Uploads are chunked and awaited.
- A failed batch is split to isolate malformed records so the rest can still
  reach the server.
- The app only advances its successful sync checkpoint after confirmed server
  uploads.
- The status screen shows per-type progress, invalid records skipped, locally
  skipped records, server inventory, and local synced-day coverage.
- The local synced-day tracker is only an optimization. Use **Force re-upload
  locally tracked days** when you want to re-send everything in the selected
  window.

## Self Hosting
You can self host the server and database for full control. However, if you'd like to push from your own server, you must build the mobile application yourself. You can find the instructions to build the mobile application below. This is because the app is packaged with the firebase key, and cannot change it dynamically. Again, firebase is only necessary if you want to push from your own server.
### Firebase
Follow these steps to set up Firebase:
1. Create a new Firebase project at https://console.firebase.google.com/
2. Add an Android app to the project
3. Download the `google-services.json` file and place it in the `firebase/` folder as well as the `android/app/` folder

### Docker (recommended)
1. **Prerequisites**\
    Ensure that you have Docker and Docker Compose installed on your system.

2. **Setting up the Environment**

   - You’ll need to configure environment variables before starting the services.
   - Copy the root `.env.example` file to `.env` and set a strong local MongoDB password.
   - Copy `api/.env.example` to `api/.env` and configure it as necessary. When setting `MONGO_URI`, use `mongodb://root:<the-same-password>@db:27017/hcgateway?authSource=admin`.

    - Visit the firebase console > project settings > Service accounts and click generate new private key
    - Save the file as `service-account.json` in the `api/` folder

3. **Running the Containers with Docker Compose**\
    The project uses Docker Compose for the API, analytics worker, and MongoDB:
    ```bash
   docker compose up -d --build
    ```
You can access the API at `http://localhost:6644`

Useful lifecycle commands:

```bash
docker compose ps
docker compose logs -f analytics-worker
docker compose down       # preserves the bind-mounted ./db data
docker compose up -d
```

To report the on-disk database size from anywhere, run:

```bash
./calculate-database-folder-disk-usage-in-gigabytes.sh
```

It prints decimal GB, binary GiB, and exact bytes. If host permissions prevent
reading MongoDB's files, it falls back to measuring `/data/db` through the
running database container.

Do not add `--volumes` to `down` unless database deletion is intentional. On
this host, read the MongoDB/kernel compatibility note in
[doc/frontend-data-model.md](doc/frontend-data-model.md) before changing the
pinned database image.


### Manual
#### Server
- Prerequisites: Python 3, mongoDB
- Clone this repository
- `cd` into the api/ folder
- run `pip install -r requirements.txt`
- rename `.env.example` to `.env` and fill in the values
- Visit the firebase console > project settings > Service accounts and click generate new private key
- Save the file as `service-account.json` in the `api/` folder
- run `gunicorn --bind 0.0.0.0:6644 --workers 2 --threads 4 main:app` to start
  the API
- in another process, run `python3 -m analytics_engine.worker` to start the
  analytics worker

#### Mobile Application
- Prerequisites: Node.js 18+, npm, Android Studio (SDK, build-tools, platform-tools), Java 17
    - Install from this site ( version 17.0.14+7 ) https://www.openlogic.com/openjdk-downloads
- in another window/tab, `cd` into the app/ folder
- run `npm install`
- If you wish to remove sentry:
```
yarn remove @sentry/react-native
npx @sentry/wizard -i reactNative -p android --uninstall
```
- If you wish to change sentry to your own instance:
    - Change the `dsn` in `App.js` to your own DSN
    - Change the server, org name, and project name in app.json
    - Change these details again in android/sentry.properties
    - Change the DSN in the AndroidManifest.xml
- run `npx patch-package` to apply a patch to the foreground service library
- run `npm run android` to start the application, or `cd android && ./gradlew assembleRelease` to build the APK file
    - It is also possible to now use eas build to build the APK file. You can find more at https://docs.expo.dev/build/eas-build/ **NOTE: This must be a local build, since you need to run patch-package before building the APK file.**

---

## Notes on this fork's setup (deviations from the original author)

This fork is built and run for personal use only (single device). The following changes were made so the app is **not** connected to the original author's (shuchir) infrastructure, and so it builds locally on a Linux/WSL machine without Android Studio. They are documented here for future reference.

**This is now the primary/intended way to build this project.** The methodology behind building the APK on Linux is simply so that I don't have to install the full Android Studio on Windows across all the devices I want to edit this app on — a lightweight Linux/WSL command-line toolchain travels much more easily.

### Sentry (crash reporting) — disconnected from the original author
The upstream project ships wired to the original author's Sentry instance. That has been removed so no crash/error data is ever sent to them:
- `app/android/app/src/main/AndroidManifest.xml` — removed the native `io.sentry.dsn` meta-data that pointed at `sentry.shuchir.dev`. Without a DSN the native Sentry SDK has nowhere to report.
- `app/android/sentry.properties` — all values commented out, including the original author's build-time **auth token** (used for source-map uploads to their org).
- `app/app.json` — removed `organization`/`project` from the `@sentry/react-native/expo` plugin config (only relevant if a prebuild is ever run).
- `app/App.js` — Sentry was already disabled here upstream-of-this-note (`isSentryEnabled = false`, empty `dsn: ''` in the toggles). A commented-out reference to the old DSN remains but never executes.

### Firebase
- Uses this fork's own Firebase project (package `org.lucasferguson.hcgateway`, project `hcgateway-app`). The real `google-services.json` is not committed; copy it into both `app/firebase/google-services.json` and `app/android/app/google-services.json` before building.
- The app package was renamed from `dev.shuchir.hcgateway` to `org.lucasferguson.hcgateway` (see commit history) to fix build issues.

### Building locally on Linux / WSL (no Android Studio) — the current build process

Only the Android SDK **command-line tools** are needed, not the full IDE. This is the exact process followed to get a working build, in order:

1. **Install Java 17** (via the distro package manager):
   ```bash
   sudo apt update && sudo apt install -y openjdk-17-jdk
   ```

2. **Download the Android SDK command-line tools.** Get the latest "Command line tools only" package for Linux directly from Google's official site — <https://developer.android.com/studio#command-line-tools-only> — rather than a pinned URL, since the version bumps over time and old links rot. Unzip it so the tools end up at `~/Android/Sdk/cmdline-tools/latest/` (the folder inside must be named `latest`).

3. **Set the environment variables** (these examples are for the fish shell — put them in `~/.config/fish/config.fish` so every shell has them; for bash/zsh use `export` in `~/.bashrc`/`~/.zshrc`):
   ```fish
   set -gx ANDROID_HOME $HOME/Android/Sdk
   set -gx JAVA_HOME /usr/lib/jvm/java-17-openjdk-amd64
   fish_add_path $ANDROID_HOME/cmdline-tools/latest/bin $ANDROID_HOME/platform-tools
   ```

4. **Install the SDK packages and accept licenses** (`android-34` / `build-tools;34.0.0` match `compileSdkVersion: 34` in `app/app.json`):
   ```bash
   sdkmanager --install "platform-tools" "platforms;android-34" "build-tools;34.0.0"
   sdkmanager --licenses   # accept all
   ```

5. **Put the Firebase file in place** — copy your real `google-services.json` into **both** `app/firebase/google-services.json` and `app/android/app/google-services.json` (see the Firebase section above; it is git-ignored so it never lives in the repo).

6. **Install JS deps and apply the required patch:**
   ```bash
   cd app
   npm install
   npx patch-package        # patches @supersami/rn-foreground-service — required before building
   ```

7. **Build the APK.** The easiest way is the wrapper script in the repo root, which first runs a dependency/setup preflight check and then builds while saving a timestamped log to `build-logs/`:
   ```bash
   ./build-android-apk-on-linux.sh
   ```
   Or run gradle directly:
   ```bash
   cd app/android
   chmod +x gradlew         # only needed once, if the executable bit is missing
   ./gradlew assembleRelease
   ```
   The first run downloads the Gradle 8.6 distribution and all Android dependencies (10–20 min); later builds are much faster. The APK lands at `app/android/app/build/outputs/apk/release/app-release.apk`. Sideload it onto the phone.

**Gotchas encountered along the way (already fixed in this repo):**
- `gradlew` needs its executable bit set on a fresh checkout: `chmod +x app/android/gradlew`.
- `app/android/gradle.properties` previously hardcoded `org.gradle.java.home` to a **Windows** JDK path, which broke the Linux build. It is now left unset so Gradle falls back to `JAVA_HOME`, keeping the file portable across machines/OSes.
- `package-lock.json` / `yarn.lock` may show churn when installing on Linux — this is just platform-specific native binaries (e.g. `@sentry/cli`, `lightningcss`) swapping from `win32-x64` to `linux-x64-gnu`. Expected when moving the build off Windows.

> [!NOTE]
> Because a few files carry machine/OS-specific values (JDK path, native lockfile binaries), switching between building on Windows and Linux may require small local adjustments. The repo is currently tuned for the Linux/WSL command-line build described above.

### Reverting to an Android Studio build
If this repo ever needs to go back to being built with Android Studio (e.g. on a Windows machine), the following would need to be changed back:
- **`app/android/gradle.properties`** — re-add `org.gradle.java.home` pointing at that machine's JDK install (or rely on Android Studio's bundled JDK / the IDE's Gradle JDK setting instead of the env var).
- **Toolchain** — install Android Studio and let it manage the SDK, platform-tools, and build-tools, instead of the standalone `cmdline-tools` + `sdkmanager` setup above.
- **`package-lock.json` / `yarn.lock`** — expect the reverse native-binary churn (`linux-x64-gnu` → `win32-x64`) after running `npm install` on Windows.
- **Sentry (optional)** — if crash reporting is wanted again, restore a DSN in `app/android/app/src/main/AndroidManifest.xml`, the values in `app/android/sentry.properties`, and the `organization`/`project` in `app/app.json` (point them at *your own* Sentry, not the original author's).
- Everything else (Firebase file placement, `patch-package`) stays the same — those are not tied to the build environment.
