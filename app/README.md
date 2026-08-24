# HCGateway Android application

This React Native Android client reads permitted records from Health Connect and
uploads encrypted-at-rest raw data to the HCGateway API. It supports paginated
reads, chunked/retried uploads, malformed-record isolation, configurable history,
local sync coverage, a foreground sync service, and server inventory/status.

General build instructions are in the repository [README](../README.md). This
file tracks Android-specific work needed to make the application complete,
reliable, private, and maintainable.

## Priority 0: complete and trustworthy data capture

- [ ] **Actually ingest Health Connect HRV (`HeartRateVariabilityRmssd`).** The
  manifest already declares the permission and the backend already normalizes
  it, but `App.js` does not include the record type in `RECORD_TYPES`. Verify the
  payload on a Pixel Watch, add it to sync/status coverage, and confirm Recovery
  changes from `partial` to `available` only when its quality gates pass.

- [ ] **Add the distinct Health Connect `SkinTemperature` record.** Do not
  substitute `BodyTemperature`; Pixel Watch sleep-relative skin-temperature
  deltas have different semantics. Add the feature/OS capability check, manifest
  permission, read path, server support, and missing/unsupported UI states.

- [ ] **Add exercise-route ingestion behind explicit location consent.** Request
  the separate route permission only when the user opts in, preserve provenance,
  encrypt coordinates, handle routes that require a second permission response,
  and provide a “delete location history” control.

- [ ] **Audit every current Google Health write type against the uploader.** In
  particular verify Pixel Watch delivery of resting HR, respiratory rate, SpO2,
  VO2 max, active calories, cadence, elevation, floors, and workout sessions.
  Distinguish “watch measures it,” “Google Health displays it,” “Google Health
  writes it,” and “HCGateway successfully uploaded it.”

- [ ] **Add newly relevant Health Connect types deliberately.** Review activity
  intensity, mindfulness sessions, planned exercise, sexual activity,
  intermenstrual bleeding, and future SDK types. Only request permissions for
  data the app can explain, upload, document, and use.

- [ ] **Create an on-device data coverage audit.** Show each requested type's
  permission, Health Connect record count/date range/source apps, last successful
  upload, server count/date range, and any discrepancy between phone and server.

- [ ] **Verify units and payload schemas with fixtures from real supported
  devices.** Cover energy, temperature, speed, distance, mass, percentage,
  cadence, stage codes, exercise types, timezone offsets, and nullable metadata.
  Never silently convert missing measurements to zero.

## Priority 0: durable synchronization

- [ ] **Replace timer-only foreground work with durable WorkManager scheduling.**
  Support device reboot, application process death, doze, battery optimization,
  network constraints, and exponential retry. Keep the foreground notification
  for active long-running transfers where Android requires it.

- [ ] **Persist a server-acknowledged upload queue.** A process crash between a
  Health Connect read and server acknowledgement must not lose the batch or move
  the successful checkpoint. Make queue entries idempotent by record ID/version.

- [ ] **Switch routine syncs to change-token or affected-window processing.**
  Avoid rescanning and re-uploading a large history every two hours. Retain an
  explicit repair/full-history mode for backfills and reconciliation.

- [ ] **Reconcile local coverage with server truth.** The current
  `syncedDaysByType` map is advisory and can become stale after reinstall,
  database restoration, deletion, or a changed server. Add a safe comparison and
  repair flow rather than trusting the local map as authoritative.

- [ ] **Handle Health Connect deletions and updates.** Use client record version,
  last-modified metadata, and change/deletion tokens where supported so the
  server can distinguish new, updated, and removed records.

- [ ] **Add bounded concurrency and back-pressure.** Prevent high-frequency
  heart-rate history from exhausting memory, overwhelming the API, or repeatedly
  triggering expensive full analytics rebuilds.

- [ ] **Make partial sync outcomes resumable.** Persist per-type/page progress,
  retain isolated rejection details, and let users retry only failed types or
  date ranges.

## Device identity and provenance

- [ ] **Display source application and physical-device metadata separately.**
  Explain that `dataOrigin` identifies the writing app while manufacturer/model/
  type describe the sensor only when that app supplies them.

- [ ] **Consume `/api/v2/analytics/devices` in the Android UI.** Show observed
  device IDs, descriptions, signal coverage, recording methods, and ambiguity
  warnings without exposing health values.

- [ ] **Add user-confirmed device aliases and date windows.** Allow labels such
  as “2025 Fitbit Inspire” and “2026 Pixel Watch 4” when Fitbit omits model data.
  Preserve the original provenance and clearly mark date-window matching as an
  inference that may overlap other sources.

- [ ] **Capture uploader installation identity separately from sensor identity.**
  A random resettable installation ID can help diagnose which phone uploaded a
  batch without pretending that phone measured every record. Do not use hardware
  identifiers or advertising IDs.

## Permissions, privacy, and security

- [ ] **Replace the all-at-once permission request with contextual groups.** Ask
  for core activity/sleep first and optional vitals, cycle, nutrition, body
  measurements, location, background, and history access only when enabled.

- [ ] **Add a privacy dashboard and deletion controls.** Show what is read, what
  has been uploaded, source/device provenance, retention behavior, and links to
  delete a type/date range on the server and revoke local permissions.

- [ ] **Move access and refresh tokens out of plain AsyncStorage.** Use Android
  Keystore-backed secure storage, rotate tokens safely, clear credentials on
  logout/revoke, and never include tokens in backups.

- [ ] **Remove sensitive console logging.** Do not print FCM tokens, auth
  responses, raw records, record IDs, or rejected payload contents in release
  builds. Add structured redaction for diagnostic logs.

- [ ] **Harden network configuration.** Require HTTPS outside explicit local
  development, validate the configured server URL, use timeouts everywhere,
  distinguish TLS/auth/network/server failures, and consider certificate pinning
  only with a documented rotation/recovery strategy.

- [ ] **Review Firebase and notification necessity.** Minimize collected device
  identifiers and permissions; document exactly what FCM is used for or remove it
  if durable local scheduling makes it unnecessary.

- [ ] **Make telemetry strictly opt-in and self-hosted/configurable.** Sentry is
  currently disconnected from the original author's infrastructure. If restored,
  redact health/auth data and provide an obvious in-app consent and disable path.

## User experience and operations

- [ ] **Redesign onboarding as a readiness checklist.** Verify Health Connect
  availability, server reachability, authentication, core permissions, optional
  history/background access, battery restrictions, and first successful upload.

- [ ] **Improve sync status language.** Separate reading, queued, uploading,
  acknowledged, analytics pending, completed, partially failed, and idle. Explain
  that the server's 120-second heartbeat does not prove the Android task is alive.

- [ ] **Add actionable error details.** For each failed type show a safe error
  category, affected date/page, retry action, permission/settings shortcut, and
  whether any records from that type succeeded.

- [ ] **Add notification controls.** Let users choose persistent sync, failure,
  completion, permission, and stale-sync notifications; create proper Android
  channels with useful names/descriptions.

- [ ] **Add accessibility and visual polish.** Support screen readers, dynamic
  text, high contrast, dark mode, large progress lists, localization-ready copy,
  and touch targets that meet Android guidance.

- [ ] **Add import/export diagnostics.** Export a redacted support bundle with
  app/build versions, permissions, source/type counts, sync timings, and errors—
  never raw health values, credentials, or precise routes.

## Code quality and testing

- [ ] **Break up `App.js`.** Extract API/auth, secure storage, Health Connect,
  sync queue, background scheduling, status store, and screens into testable
  modules. Consider TypeScript for record contracts and state transitions.

- [ ] **Add automated JavaScript tests.** Cover pagination, date windows,
  timezone/DST behavior, permission denial, batch splitting, retries, token
  refresh, malformed timestamps, local coverage, and process-resume behavior.

- [ ] **Add Android integration tests.** Exercise onboarding, permission changes,
  offline/online recovery, reboot/process death, foreground notification,
  full-history access, and representative Health Connect fixtures.

- [ ] **Add contract tests against the Flask API.** Pin request/response shapes,
  supported record names, case normalization, provenance fields, duplicate
  handling, maximum batch size, and additive version compatibility.

- [ ] **Introduce linting, formatting, and static checks.** Run them with
  `node --check`, patch-package verification, unit tests, and debug/release builds
  in one documented verification command.

- [ ] **Plan dependency upgrades.** Move React Native, Expo, Health Connect
  client, Android target SDK, Gradle, and Java versions in tested increments;
  revalidate the foreground-service patches after every upgrade.

## Release readiness

- [ ] **Replace debug-keystore release signing.** Store production signing keys
  outside the repository, document backup/recovery, and verify upgrade-compatible
  signed APK/AAB installation.

- [ ] **Add a reproducible CI release pipeline.** Build, test, generate checksums
  and an SBOM, archive redacted logs, and publish artifacts only from tagged
  commits without embedding local secrets.

- [ ] **Complete Play health-app declarations and privacy documentation.** Keep
  every requested Health Connect permission tied to a visible feature and remove
  permissions that are not actively supported.

- [ ] **Add versioned migrations and rollback notes.** Cover AsyncStorage/secure
  storage keys, local queue schema, permission changes, endpoint contracts, and
  foreground/background scheduling changes.

- [ ] **Run a physical-device release matrix.** At minimum test the current phone
  and Pixel Watch 4 on supported Android versions, plus upgrade/reinstall,
  restricted battery mode, Wi-Fi/mobile/VPN changes, multi-day offline use, and a
  large full-history backfill.
