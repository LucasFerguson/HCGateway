# WHOOP ZIP vs Health Connect database vs Pixel Watch 4

Snapshot date: **2026-08-24**

This document compares:

1. the supplied `raw-data/whoop/2026-08-24/my_whoop_data_2026_08_24.zip`
   archive;
2. raw records actually stored for the primary `lucas` account in HCGateway's
   MongoDB database; and
3. data a Google Pixel Watch 4 can gather, with a separate check for whether
   Google Health currently documents writing that data to Health Connect and
   whether HCGateway currently reads it.

No raw health values were copied into this document. ZIP inspection was limited
to filenames, column names, row counts, and non-empty counts. Database inspection
was limited to collection counts, source packages, and representative field
shapes.

## How to read the comparison

- **Yes** means the data is directly present or documented as supported.
- **Derived** means an app computes the value from underlying measurements.
- **No** means it was not found in the supplied archive/database or is outside
  the current Health Connect/HCGateway path.
- **Conditional** means availability depends on region, permissions, app sync,
  account/device setup, or Google's current sharing behavior.
- MongoDB counts are raw Health Connect records, not individual samples. For
  example, one `heartRate` record can contain many timestamped heart-rate samples.
- “Fitbit / likely Pixel Watch” in the database columns means source package
  `com.fitbit.FitbitMobile`. Most records from this source after 2026-02-05
  overlap the stated Pixel Watch 4 era, but Fitbit omitted manufacturer/model,
  so the column describes a source and a strong candidate rather than proven
  physical-device identity.

## Main comparison

| Data or metric | Supplied WHOOP ZIP | WHOOP data in Health Connect DB | Fitbit / likely Pixel Watch data in DB | Other DB sources | Pixel Watch 4 / Google Health capability | Current HCGateway result or gap |
| --- | --- | --- | --- | --- | --- | --- |
| Record/cycle time and timezone | Cycle start/end/timezone in all four CSVs | Record start/end, app, and available provenance | Record start/end, app, and available provenance | Same envelope for Google Fit/phone records | Supported | Captured |
| Continuous/sample heart rate | **No raw time series**; cycle/workout average and maximum only | **186** `heartRate` records with timestamped BPM samples | **235,863** `heartRate` records with timestamped BPM samples | None | Multi-path optical HR sensor; Google Health documents writing HR | Fitbit/likely-Pixel DB is far more granular than the ZIP |
| Average and maximum heart rate | Cycle and workout averages/maxima | Derivable from WHOOP samples | Derivable from Fitbit samples | None | Measured/derived | Day API derives hourly min/p25/mean/p75/max across selected sources |
| Resting heart rate | 160 populated cycle rows | **153** `restingHeartRate` records | None | None | Google Health documents writing RHR | WHOOP supplies it; Fitbit/likely-Pixel does not currently appear |
| Heart-rate variability (RMSSD) | 160 populated cycle rows | None | None | None | Google Health tracks and documents writing HRV | **Gap:** backend mapper exists, but Android does not request/read HRV |
| Blood oxygen / SpO2 | 159 populated cycle rows | **151** `oxygenSaturation` records | None | None | Watch measures overnight SpO2 | Google Health's current write table omits oxygen saturation; Pixel delivery is not guaranteed |
| Respiratory/breathing rate | 160 cycle rows and all 200 sleep rows | **153** `respiratoryRate` records | None | None | Google Health documents writing respiratory rate | WHOOP supplies it; Fitbit/likely-Pixel does not currently appear |
| Skin temperature | 160 populated cycle rows | None | None | None | Pixel Watch 4 measures sleep-relative skin-temperature variation and Google Health documents writing `SkinTemperature` | **Gap:** uploader requests `BodyTemperature`, which is a different type |
| ECG / AFib spot checks | No | None | None | None | Pixel Watch supports regional 30-second ECG checks and PDF export | Not captured through the current Health Connect pipeline |
| Heart alerts / irregular-rhythm events | No | None as dedicated events | None as dedicated events | None | Pixel Watch supports regional alerts | Underlying HR can exist, but notifications/results are not captured |
| cEDA/body responses and stress | No | None | None | None | Pixel Watch has cEDA and Google Health stress/body-response features | No current HCGateway record type |
| Recovery/readiness score | WHOOP Recovery on 160 cycle rows | WHOOP's vendor score is **not** written; raw sleep/RHR inputs are present | Google readiness is **not** written; raw HR/sleep inputs are present | None | Google computes readiness from HRV, RHR, and sleep | HCGateway computes its own provisional score; currently partial without HRV |
| Day strain/cardio load | WHOOP Day Strain on 178 cycle rows | WHOOP Day Strain is not written | Google cardio/target load is not written | None | Google computes cardio and target load | HCGateway strain is experimental and non-proprietary, not WHOOP parity |
| Sleep session/window | Onset/wake and cycle boundaries; 200 sleep rows | **153** `sleepSession` records | **424** `sleepSession` records | **21** Google Fit sessions | Watch tracks sleep sessions | Captured and reconciled across sources by local wake date |
| Sleep stages | Light/deep/SWS/REM/awake totals | Timestamped stages within sessions | Timestamped stages within sessions | May be present in Google Fit sessions | Google Health documents writing sleep stages | Database stage intervals are more granular than ZIP totals |
| Nap flag | Explicit field on all 200 sleep rows | No dedicated Boolean; separate sessions remain | No dedicated Boolean; separate sessions remain | Same | Short sleep detection depends on Fitbit behavior | Preserve separate sessions; explicit WHOOP flag is not imported |
| Sleep performance/score | Present on every sleep row | WHOOP score not written | Google sleep score not written | None | Google Health displays sleep score | HCGateway percentage is a separate partial fixed-target calculation |
| Asleep/in-bed/awake duration | Yes | Derived from window/stages | Derived from window/stages | Derived when stages exist | Derived | Captured/derived |
| Light/deep/REM duration | Yes | Derived from stages | Derived from stages | Derived when stages exist | Derived | Captured/derived |
| Sleep need and debt | Yes | Vendor values not written; underlying sleep exists | Vendor values not written; underlying sleep exists | Underlying sleep may exist | Google may use personalized targets | HCGateway derives separate 7/30/90-day debt windows |
| Sleep efficiency/consistency | Yes | Vendor values not written; derivable inputs exist | Vendor values not written; derivable inputs exist | Derivable inputs may exist | Google displays sleep insights | HCGateway derives its own consistency/efficiency semantics |
| Steps | No | **5** records | **11,725** records | **8,935** Google Fit/Android/phone records | Google Health documents writing steps | Fitbit/likely-Pixel is the largest watch/app source |
| Distance | No | None | **43,901** records | **6,386** Google Fit records | Google Health documents writing distance | Captured |
| Speed | No | None | None | **8,925** Google Fit records | Google Health documents writing speed | Captured, but not from Fitbit source |
| Step cadence | No | None | **27** records | None | Google Health documents writing cadence | Captured but sparse |
| Elevation gained | No | None | **802** records | None | Watch has altimeter/barometer; Google Health writes elevation gained | Captured |
| Floors climbed | No | None | **802** records | None | Google Health documents writing floors | Captured |
| Workout/exercise session | 239 workouts with time, duration, and activity | **211** `exerciseSession` records | **127** `exerciseSession` records | None | Watch tracks 50+ exercise types | Captured with type/title/notes/laps/segments/route container |
| Workout HR zones | Zone 1–5 percentages on every workout | Explicit percentages not written; derivable from HR | Explicit percentages not written; derivable from HR | None | Google displays HR zones | HCGateway derives zones from HR and configured thresholds |
| Workout strain | WHOOP Activity Strain | Vendor workout strain not written | Google load not written | None | Google derives cardio load | HCGateway's workout strain is a distinct experimental estimate |
| Workout GPS availability | Boolean `GPS enabled` | Route container observed empty | Route container observed empty | Speed/distance exist separately | Pixel Watch 4 has dual-frequency GPS; Google Health can write routes | **Gap:** separate exercise-route permission/data is not requested |
| Raw GPS coordinates/route | No | None observed | None observed | None observed | Watch can gather workout routes | Not captured |
| Active calories | WHOOP energy by cycle/workout, not a distinct active-calorie series | **211** `activeCaloriesBurned` records | None | None | Google Health reads but does not currently document writing active calories | Only WHOOP supplies this Health Connect type today |
| Total calories | WHOOP energy by cycle/workout | None | **26,926** `totalCaloriesBurned` records | **5,526** Google Fit records | Google Health documents writing total calories | Fitbit/likely-Pixel is the primary source |
| VO2 max / cardio fitness | No | None | None | None | Google Health documents reading/writing VO2 max, conditionally | Requested by HCGateway but no value is stored |
| Weight | No | None | **1** record | None | Usually manual/scale data, not a watch sensor | Captured; do not attribute it to Pixel Watch hardware |
| Body fat | No | None | None | None | Usually manual/scale data | Collection exists but is empty |
| Journal questions/answers/notes | 122 rows; 12 notes populated | None | None | None | Google mindfulness/stress logging uses a different model | Requires a separate journal importer/model |
| Other manual/medical/cycle data | No | None | None currently | None currently | Some types can come from Google Health or other apps, not Pixel Watch sensors | Supported in part by uploader; absence remains missing, never zero |

## Supplied WHOOP ZIP inventory

| CSV file | Rows | Available columns/data |
| --- | ---: | --- |
| `physiological_cycles.csv` | 181 | Cycle start/end/timezone; Recovery; RHR; HRV; skin temperature; SpO2; Day Strain; energy; max/average HR; sleep onset/wake; sleep performance; respiratory rate; asleep/in-bed/light/deep/REM/awake duration; sleep need/debt/efficiency/consistency |
| `sleeps.csv` | 200 | Cycle and sleep windows; sleep performance; respiratory rate; duration and stage totals; sleep need/debt/efficiency/consistency; nap flag |
| `workouts.csv` | 239 | Cycle and workout windows; duration; activity name; Activity Strain; energy; max/average HR; percentage in HR zones 1–5; GPS-enabled flag |
| `journal_entries.csv` | 122 | Cycle window/timezone; question text; yes/no answer; free-text note |

The ZIP is strong for WHOOP's daily/workout/sleep summaries and proprietary
derived metrics, but it does **not** contain raw heart-rate samples, GPS
coordinates, step/distance/floor time series, ECG, or raw sensor streams.

## Live primary-account database inventory

Only non-empty raw collections are listed here.

| Health Connect collection | Records | Sources |
| --- | ---: | --- |
| `heartRate` | 236,049 | Fitbit 235,863; WHOOP 186 |
| `distance` | 50,287 | Fitbit 43,901; Google Fit 6,386 |
| `totalCaloriesBurned` | 32,452 | Fitbit 26,926; Google Fit 5,526 |
| `steps` | 20,665 | Fitbit 11,725; Google Fit 6,244; Android/other 2,691; WHOOP 5 |
| `speed` | 8,925 | Google Fit 8,925 |
| `elevationGained` | 802 | Fitbit 802 |
| `floorsClimbed` | 802 | Fitbit 802 |
| `sleepSession` | 598 | Fitbit 424; Google Fit 21; WHOOP 153 |
| `exerciseSession` | 338 | Fitbit 127; WHOOP 211 |
| `activeCaloriesBurned` | 211 | WHOOP 211 |
| `respiratoryRate` | 153 | WHOOP 153 |
| `restingHeartRate` | 153 | WHOOP 153 |
| `oxygenSaturation` | 151 | WHOOP 151 |
| `stepsCadence` | 27 | Fitbit 27 |
| `weight` | 1 | Fitbit 1 |
| **Total** | **351,614** | Raw records across the collections above |

Collections also exist but currently contain zero records for basal/body
temperature, basal metabolic rate, blood glucose, blood pressure, body fat,
bone mass, cervical mucus, cycling cadence, height, hydration, lean body mass,
menstruation flow/period, nutrition, ovulation test, power, VO2 max, and
wheelchair pushes. There is no HRV or Health Connect skin-temperature collection
created by the current uploader.

## Device and source attribution found in the database

Health Connect exposes two different provenance concepts:

- `dataOrigin` / the stored `app` field identifies the **application that wrote
  the record**, not necessarily the physical sensor; and
- `metadata.device` can describe the physical device's manufacturer, model,
  and type, but those fields are supplied by the writing app and can be missing.

This distinction matters here because both an old Fitbit Inspire and a Pixel
Watch can be written by `com.fitbit.FitbitMobile`. The current records support
the following attribution, without proving every physical-device boundary:

| Observed provenance group | Records and coverage | What can safely be concluded |
| --- | --- | --- |
| Fitbit/Google Health; device type `unknown`; automatically recorded | 238,143 records, 2026-02-05 through 2026-08-24 | Strongly overlaps the stated Pixel Watch era and contains most HR, sleep, distance, elevation, floors, steps, and cadence. It is a reasonable Pixel Watch candidate, but manufacturer/model are absent, so the metadata alone does not prove it. |
| Fitbit/Google Health; device type `unknown`; recording method unknown | 37,161 records, 2026-02-05 through 2026-08-24 | Distance, floors, and total-calorie records from the same Fitbit-era window; likely account/app-derived companion records rather than a separately identifiable sensor. |
| Fitbit/Google Health; no preserved device metadata | 45,250 records, 2025-03-17 through 2026-08-23 | This legacy group almost certainly includes the 2025 Fitbit Inspire history, but its long range can also include later Fitbit/Pixel records. It cannot be split reliably by package name alone. |
| Fitbit/Google Health; device type `fitness_band` | 43 exercise sessions, 2026-02-06 through 2026-08-21 | Health Connect type 6 means fitness band. The absent manufacturer/model prevents confidently calling it the Inspire; the date range also overlaps the stated Pixel Watch era. |
| Google Fit; no preserved device metadata | 27,102 records, 2025-03-17 through 2026-01-13 | Written by Google Fit; physical source could be phone-derived, imported, or another connected device. |
| Health Connect phone source; `samsung` `SM-S928U`; type `phone` | 772 step records, 2026-07-26 through 2026-08-24 | Explicitly attributable to the Samsung phone's tracker. A further 103 step records from related hashed phone-source packages lack complete device metadata. |
| Android source; no preserved device metadata | 1,816 step records, 2026-01-07 through 2026-06-01 | Phone/platform-derived steps are plausible, but the physical device is not identified in these legacy records. |
| WHOOP app | 1,223 records across legacy and type-unknown groups | Attributable to the WHOOP app, though the physical WHOOP model is not supplied. |

The additive `GET /api/v2/analytics/devices` endpoint now exposes this as a
derived device catalog. Each entry has a stable observed ID, human-readable
description, source package, device fields, identity quality, ambiguity flag,
recording-method counts, signals, date range, and the exact metadata fields used
to associate records. It is deliberately derived from raw records rather than
stored as a second mutable source of truth.

Date-window aliases could later label the likely “2025 Inspire” and “2026 Pixel
Watch 4” eras, but they should be user-confirmed because records overlap and the
Fitbit writer omitted the model. Such aliases should describe an inference; they
must not overwrite original provenance.

## Most important implementation opportunities

1. **Add `HeartRateVariabilityRmssd` to the Android `RECORD_TYPES` list and
   manifest permissions.** The backend mapper and analytics model already
   support it. This is the clearest path to complete rather than partial
   Recovery scores.
2. **Add the Health Connect `SkinTemperature` type.** Do not map Pixel Watch
   skin-temperature variation to `BodyTemperature`; they are distinct records
   with different semantics.
3. **Add exercise-route permission and ingestion** if route maps are wanted.
   Treat location data as especially sensitive and keep it encrypted.
4. **Do not expect ECG, cEDA/body responses, WHOOP Recovery/Strain, Google
   readiness/cardio load, or journal entries to arrive as ordinary current
   Health Connect records.** Those require vendor exports/APIs, separate import
   models, or local re-computation from available raw signals.
5. **Run a post-purchase verification inventory.** After Pixel Watch 4 and
   Google Health have synced for several nights, check Health Connect's “See app
   data” screen and this API's `/api/v2/analytics/inventory`. Device capability
   does not guarantee that Google Health writes every metric, and permissions,
   region, app version, and account setup all matter.
6. **Add user-confirmed device aliases/date windows if historical separation is
   needed.** The API now reports what is observable, including ambiguity. A
   future alias registry can map an observed source plus an inclusive date
   window to “Fitbit Inspire” or “Pixel Watch 4” while retaining the original
   `app` and `provenance` fields.

## Sources and project references

- Supplied archive:
  `raw-data/whoop/2026-08-24/my_whoop_data_2026_08_24.zip` (inspected locally,
  without copying raw values).
- HCGateway uploader record list: [`../app/App.js`](../app/App.js).
- Backend-supported analytics mappings:
  [`../api/analytics_engine/repository.py`](../api/analytics_engine/repository.py).
- Prepared analytics semantics: [`frontend-data-model.md`](frontend-data-model.md).
- [WHOOP: How to Export Your Data](https://support.whoop.com/s/article/How-to-Export-Your-Data?language=en_US).
- [Google: Pixel Watch 4 technical and device specifications](https://support.google.com/googlepixelwatch/answer/12651869?hl=en-IN).
- [Google: Get started with Google Health on Pixel Watch](https://support.google.com/googlehealth/answer/14237107?hl=en).
- [Google: Google Health and Health Connect data types](https://support.google.com/googlehealth/answer/14506680?hl=en).
- [Android Developers: Health Connect data types](https://developer.android.com/health-and-fitness/health-connect/data-types).
- [Android Developers: Health Connect device metadata](https://developer.android.com/reference/androidx/health/connect/client/records/metadata/Device).
- [Android Developers: Health Connect recording methods](https://developer.android.com/reference/androidx/health/connect/client/records/metadata/Metadata).
- [Google: ECG availability and per-result PDF export](https://support.google.com/googlehealth/answer/14236718?hl=en).

This is a data-engineering comparison, not a claim that any score or wearable
measurement is clinically interchangeable. HCGateway Recovery and strain remain
provisional, non-clinical heuristics.
