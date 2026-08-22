# Lucas Notes

running project on 2025-04-15 on server pve3
API url
`http://l192.168.8.239:6644`

## Scratchpad / TODO (for me + coding agents working in this repo)

This is a shared task list. Coding agents reading this directory: please check
here for known issues and open work, and update it as items are resolved.

Context: this repo is the **client app + REST API** for a self-hosted health
dashboard. There is a **separate back-end server project** (not yet wired into
this repo / not yet shared with the agents working here) that is developed
alongside this one. Keep that in mind — some work spans both.

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

- [ ] **Sync history beyond 30 days.** Two hardcoded `getDate() - 29` windows in
  [app/App.js](app/App.js) cap history at ~30 days. We merged the
  `PERMISSION_READ_HEALTH_DATA_HISTORY` manifest permission, but Health Connect
  only returns older data if that history permission is also **requested at
  runtime and granted**. Work: lift the hardcoded window + request the history
  permission. Needs on-device testing to confirm HC actually returns older data.


# HCGateway
HCGateway is a platform to let developers connect to the Health Connect API on Android via a REST API. You can view the documentation for the REST API [here](https://hcgateway.shuchir.dev/)

The platform consists of two parts:
- A REST API/server
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
The mobile application is a simple Android application that pings the server every 2 hours (customizable) to send data. It starts a foreground service to do this, and the service will run even if the application is closed. The application is written in React Native.

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
   - Copy the provided `.env.example` file to `.env` inside the `api/` directory and configure it as necessary. When setting the `MONGO_URI` variable, the following format should be used: `mongodb://<username>:<password>@db:27017/hcgateway?authSource=admin`
   - Set the mongo DB username and password in the `docker-compose.yml` file as well.

    - Visit the firebase console > project settings > Service accounts and click generate new private key
    - Save the file as `service-account.json` in the `api/` folder

3. **Running the Containers with Docker Compose**\
    The project uses Docker Compose for easier container orchestration. To run the API using Docker Compose, run the following command:
    ```bash
   docker-compose up -d
   ```
You can access the API at `http://localhost:6644`


### Manual
#### Server
- Prerequisites: Python 3, mongoDB
- Clone this repository
- `cd` into the api/ folder
- run `pip install -r requirements.txt`
- rename `.env.example` to `.env` and fill in the values
- Visit the firebase console > project settings > Service accounts and click generate new private key
- Save the file as `service-account.json` in the `api/` folder
- run `python3 main.py` to start the server

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
