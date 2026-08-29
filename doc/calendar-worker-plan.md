# Calendar sleep export plan

Status: deferred design; no calendar export is implemented yet.

This document records the intended boundary between health analytics and the
FluidCalendar integration. The first delivery milestone is to export every
reconciled sleep event, including naps, whose local wake date falls within an
initial two-week backfill window.

## Design goals

- Compute health meaning once in the analytics pipeline and make the prepared
  result reusable by the dashboard, calendar export, and future consumers.
- Keep calendar delivery independent from ingestion and analytics execution so
  it can be stopped, restarted, retried, or disabled without interrupting them.
- Reuse one repository, Docker image, Python package, database access layer,
  encryption implementation, and prepared-data contract. A separate worker is
  an operational boundary, not a second implementation of sleep analysis.
- Make retries idempotent and corrections explicit. Never create duplicate
  calendar events merely because a process restarted or an HTTP response was
  lost.
- Preserve missing and partial data as such. The exporter must not infer zero
  minutes for an unavailable stage.

## Responsibility boundary

### Analytics worker

The existing `analytics-worker` remains the sole owner of health-data meaning.
Its prepared sleep event should contain everything a downstream consumer needs
without repeating reconciliation or stage calculations, including:

- a canonical event identity, the member recording identities, and the
  analytics run/model version; durable cross-run delivery identity remains a
  calendar-ledger responsibility because canonical membership can change;
- local wake date and configured home timezone;
- canonical start and end instants;
- sleep-window and actual-sleep durations;
- normalized stage durations and their availability/quality state;
- canonical source and source/device provenance that is safe to expose;
- recording count and references or summaries for the recordings in the
  reconciled group;
- enough reconciliation/quality metadata to explain why the canonical
  recording was selected; and
- an explicit indication that an event is a nap if the analytics contract can
  define that reliably. Until then, every reconciled event is exported and no
  event is excluded merely because it is short or not the day's main sleep.

The v8.2 pipeline groups recordings when they overlap by at least 80 percent of
the shorter recording before assigning the selected recording's local wake
date. Candidates within 98 percent of the longest window are ranked by
validated stage detail and timeline coverage, then duration and stable
identifiers. It exposes the decision and labels the longest distinct event on a
wake date `main`, with other events `supplemental`. Calendar code must consume
those results rather than reproduce the rules or infer that every supplemental
event is necessarily a nap.

Prepared sleep semantics should be documented in
`doc/frontend-data-model.md` when that contract changes. The encrypted
`_analytics_sleep_events` collection is the natural durable handoff, subject to
any schema improvements made during analytics cleanup.

### Calendar worker

Add a thin, separately running `calendar-worker` Compose service after the
prepared sleep contract is settled. It should use the same locally built image
and import the same `analytics_engine` package as the existing worker. Its work
is limited to:

1. discover completed prepared sleep events that need delivery;
2. render calendar presentation fields from already computed values;
3. call FluidCalendar with bounded timeouts;
4. record the remote result and payload identity;
5. retry transient failures without blocking analytics; and
6. update or, under an agreed policy, delete calendar events when prepared
   events are corrected or withdrawn.

Formatting such as `Deep sleep: 1h 16m` belongs here. Computing the underlying
deep-sleep minutes does not.

The separate process provides independent lifecycle, logs, retries, and failure
isolation. It does not provide the only possible form of parallelism. Threads,
async work, multiprocessing, or two loops in one process could run work in
parallel, but would couple calendar availability and process lifecycle to the
analytics worker. With a separate service, calendar delivery can be disabled
with `docker compose stop calendar-worker` and re-enabled with
`docker compose start calendar-worker`.

A Compose profile is not planned initially. Profiles make optional startup
explicit, but also make it easy for a normal `docker compose up -d` to omit the
integration unintentionally. A normal separate service plus an application
configuration switch is simpler for this deployment.

## Durable delivery state

Calendar HTTP calls must occur after analytics has completed and published its
prepared run, never inside the analytics write transaction. Use a small durable
queue and/or synchronization ledger scoped to the HCGateway user. A ledger row
should contain only delivery state, not another copy of the health payload:

- stable prepared sleep-event identity;
- destination calendar/feed identity;
- current analytics run or prepared-content fingerprint;
- rendered payload hash;
- FluidCalendar event ID and, when returned, provider external event ID;
- state such as pending, delivering, delivered, retryable failure, permanent
  failure, or pending deletion;
- attempt count, next-attempt time, lease owner/expiry, and last error summary;
- delivered, updated, and last-seen timestamps.

Use leases or atomic claims so multiple worker instances cannot deliver the
same job concurrently. Store only bounded and sanitized error details. The
ledger must not duplicate raw encrypted health records or credentials.

There are two reasonable ways to discover work:

- have successful analytics publication enqueue/mark affected prepared events;
  or
- have the calendar worker compare the current prepared run with its ledger.

The first can reduce scans; the second makes recovery and backfill simpler. A
hybrid is preferable: enqueue as an optimization, then periodically reconcile
the current run against the ledger as the correctness backstop.

## Initial event representation

Subject to final product choices, the proposed first version is:

- one timed calendar event for every reconciled sleep event, including naps;
- event span equal to the canonical recording's exact start and end instants;
- send those canonical UTC instants plus the prepared event's IANA `timeZone`;
  never derive calendar time from the worker host timezone;
- title `Sleep`, unless a reliable analytics-owned nap classification supports
  a distinct title;
- description containing actual sleep duration, sleep-window duration,
  available stage durations, canonical source, and recording count;
- no zero-valued substitute for missing stage information; and
- initial backfill covering today plus the preceding 13 local wake dates in the
  configured home timezone.

The calendar description is a presentation projection, not a second prepared
health schema. It should be deterministic so an unchanged prepared event
produces the same payload hash.

## FluidCalendar contract

The current reference is `doc/external/API.md`. It provides:

- API-key authentication using `Authorization: Bearer` or `X-API-Key`;
- `GET /api/feeds` to select a writable destination feed;
- `POST /api/events` with `feedId`, title, absolute start/end timestamps,
  optional description, and `skipIfExists`;
- strict `skipIfExists` matching on feed plus title, start, end, and
  description, returning `200` for a match and `201` for a creation;
- event update and deletion by FluidCalendar event ID; and
- a windowed read endpoint for events overlapping a time range.

The documented strict-match create is sufficient to make an identical retry
safe, but it is not a stable integration identity. If a corrected sleep event
changes its time or description, `skipIfExists` treats it as new. Reliable
correction and deletion therefore require the local ledger to retain the
FluidCalendar event ID, or a future FluidCalendar feature such as an
integration-owned `source` plus `sourceId` with lookup/upsert semantics.

Before implementation, confirm or extend the API documentation for:

- the exact shape and truncation/pagination behavior of the windowed read;
- validation and error response bodies;
- which HTTP statuses are transient and safe to retry;
- provider timeout and partial-failure behavior; and
- whether FluidCalendar will add an integration-key lookup/upsert endpoint.

Provider-backed feed writes synchronously reach Google, Outlook, or CalDAV;
local-feed writes stay in FluidCalendar's database. That makes timeouts,
idempotency, and conservative retry behavior important even on a trusted
homelab network.

## Configuration and secrets

Avoid using environment variables for ordinary integration settings. Store
non-secret, per-user settings through an authenticated HCGateway configuration
surface, for example:

- enabled/disabled state;
- FluidCalendar base URL;
- destination feed ID;
- backfill horizon;
- event title/presentation preferences; and
- correction/deletion policy.

Do not store the raw FluidCalendar API key in source control, Compose YAML, the
prepared analytics documents, or logs. Prefer a Docker secret mounted as a
read-only file and have the calendar client read that file. The final design
must define how a secret is associated with a HCGateway user if more than one
user enables export.

## Decisions still open

- Which HCGateway user and FluidCalendar feed receive the initial export?
- Should the first 14-day window mean today plus 13 prior wake dates, or 14
  completed dates before today?
- Should naps use `Sleep` or a distinct `Nap` title, and what analytics-owned
  criterion distinguishes them?
- When improved reconciliation changes the canonical recording, should the
  existing remote event always be updated automatically?
- When a prepared event disappears because source data was deleted or grouping
  changed, should the owned remote event be deleted, retained, or deleted only
  after it is absent from multiple completed analytics runs?
- Should a provider-backed calendar or a FluidCalendar-local feed be the
  initial target?
- Is a ledger-held FluidCalendar event ID sufficient, or should FluidCalendar
  gain first-class integration identity and idempotent upsert?
- What should happen if the configured feed becomes read-only, disabled, or is
  replaced?

Deletion must never target an event the integration cannot prove it owns.

## Implementation phases

1. **Consolidate analytics semantics.** Extract shared sleep-duration and stage
   aggregation helpers, reconcile inconsistent consumers, improve and explain
   canonical selection as appropriate, define a stable prepared event shape,
   and add focused tests. Do not add calendar HTTP behavior in this phase.
2. **Finalize the FluidCalendar contract and settings.** Resolve the open API,
   destination, correction, deletion, and secret-association decisions. Add
   authenticated configuration without exposing the API key.
3. **Build delivery primitives.** Add the FluidCalendar client, deterministic
   payload renderer, durable ledger/queue, atomic leasing, retry classification,
   and unit tests using fake HTTP responses.
4. **Add the separate service.** Introduce the `calendar-worker` entry point and
   Compose service using the shared image. Verify independent stop/start and
   ensure calendar failures cannot fail an analytics run.
5. **Backfill and observe.** Start with a dry-run/report mode, then synchronize
   the agreed 14-day local-wake-date window. Verify that overlapping device
   recordings produce one event while separate naps remain separate, and that
   repeated runs do not duplicate events.
6. **Enable ongoing reconciliation.** Process new completed analytics runs,
   exercise correction and deletion policy, document operations, and add API
   and handoff documentation for every new configuration or status endpoint.
