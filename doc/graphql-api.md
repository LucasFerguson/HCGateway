# GraphQL read API

Status: implemented, phase 1 (read-only, no mutations, no subscriptions)
Companion documents: [`graphql-schema-design.md`](graphql-schema-design.md) (the
design this was built from) and [`graphql-read-api-audit.md`](graphql-read-api-audit.md)
(the REST baseline it consolidates).

## What this is

A new Node.js + TypeScript service (`graphql-api/`, Apollo Server) that exposes
prepared analytics and raw Health Connect records as a single read-only
GraphQL endpoint, running as its own Compose container alongside `api`,
`analytics-worker`, `calendar-worker`, and `db`. It connects directly to the
same MongoDB instance with its own `MONGO_URI` - the same shared-database,
separate-process pattern the Python workers already use. It never writes to
MongoDB and does not route through the Flask API.

- Endpoint: `POST http://<host>:6645/graphql` (container: `hcgateway_graphql_api`)
- Liveness: `GET http://<host>:6645/health` returns `{"status":"ok"}`
- Auth: `Authorization: Bearer <token>` - the same token issued by
  `POST /api/v2/login`. The token is looked up in `hcgateway.users`
  independently of Flask (the one deliberately duplicated piece of Python
  logic, kept intentionally small) and rejected if missing, unrecognized, or
  expired, before any resolver executes.
- Every field is scoped to the authenticated user's own `hcgateway_<userId>`
  database, derived once from the token. No field accepts a user ID or
  database name as an argument.

## Root shape

```graphql
type Query {
  viewer: Viewer!
}

type Viewer {
  analytics: Analytics!       # prepared, run-scoped analytics
  sourceRecords: SourceRecords!  # raw Health Connect records, one field per signal
  sources: SourceCatalog!     # inventory + observed devices
  ingestion: IngestionStatus! # phone sync heartbeat, analytics job, current run
  config: AnalyticsConfig!    # read-only mirror of GET /analytics/config
  habits(range: TimeRange): [Habit!]! # imported WHOOP journal questions/responses
}
```

See `graphql-api/src/schema/typeDefs.ts` for the complete SDL - it is the
source of truth; this document is a pointer, not a duplicate.

## WHOOP journal habits

`Viewer.habits(range:)` exposes the WHOOP journal export stored in the
authenticated user's `habitDefinitions` and `habitEntries` collections. The
range is applied to each response's cycle-end instant with the usual half-open
`start <= cycleEndAt < endExclusive` semantics. Every known question is
returned even when it has no response in the selected range, which lets a
frontend distinguish “no” from “not asked/not recorded.”

The response field is intentionally named `answeredYes`, not `completed`:
questions such as “Experienced a headache?” describe symptoms, so a yes value
is not necessarily a desirable habit completion. Each entry also exposes its
source local cycle timestamps, canonical UTC instants, local end date, source
UTC offset, and optional notes.

Imports are idempotent and run separately from the read-only GraphQL service.
WHOOP may include answers from the currently open cycle without a cycle-end
timestamp. The importer reports and skips those incomplete rows because they
cannot yet be assigned to a day; a later export will import them after WHOOP
closes the cycle.
After rebuilding the Python image, import an export from the repository root:

```bash
docker exec -i hcgateway_api \
  python -m analytics_engine.whoop_journal \
  --username lucas \
  --source-export-date 2026-08-24 \
  < raw-data/whoop/2026-08-24/journal_entries.csv
```

The importer logs only aggregate row/question counts. Raw exports remain
ignored by Git and must not be copied into an image or committed.

Example frontend query:

```graphql
query Habits($range: TimeRange) {
  viewer {
    habits(range: $range) {
      id
      source
      question
      firstSeenDate
      lastSeenDate
      entryCount
      entries {
        id
        date
        cycleEndAt
        cycleEndLocal
        sourceUtcOffsetMinutes
        answeredYes
        notes
      }
    }
  }
}
```

The analytics root and date/month-keyed analytics buckets expose stable cache
identities scoped by authenticated account and prepared run. Shared metric
types additionally include the metric key, so (for example) steps and weight
on the same date cannot collide in Apollo's cache. These new IDs are opaque
client identities, not MongoDB record IDs. Existing IDs on source/business
objects such as sleep events and workouts retain their established meaning;
clients should keep their cache policy explicit for those types.

Closed analytics values that drive presentation are GraphQL enums rather than
free-form strings: healthspan status, healthspan factor key/unit, and metric
unit. Their wire values use GraphQL's uppercase convention (`READY`,
`RESTING_HEART_RATE`, `BPM`, `MS`, and so on), while the persisted Python
analytics representation remains lowercase.

## Safety model (all env-configurable, see `graphql-api/.env.example`)

- No pagination; time ranges are optional everywhere they're accepted
  (omitting one returns full history), per explicit product direction. The
  backstop is:
  - a per-resolver-field wall-clock timeout (`GRAPHQL_RESOLVER_TIMEOUT_MS`,
    default 25s);
  - a serialized-response-size cap (`GRAPHQL_RESPONSE_SIZE_CAP_BYTES`,
    default 100MB);
  - a query depth limit (`GRAPHQL_MAX_QUERY_DEPTH`, default 12), alias limit
    (`GRAPHQL_MAX_ALIAS_COUNT`, default 30), and a weighted complexity
    ceiling (`GRAPHQL_MAX_QUERY_COMPLEXITY`, default 2000) that scores
    unbounded raw-sample fields much higher than bounded/derived fields -
    all three are GraphQL validation rules that reject a pathological query
    before any resolver runs.
- No field accepts an arbitrary caller-supplied MongoDB filter. Every
  `SourceRecords` field is a fixed, typed, single-signal read - this is the
  one REST behavior (`POST /api/v2/fetch/{method}`) deliberately not
  reproduced.
- Missing health data is never coerced to a numeric zero: every
  `MetricValue`-shaped field carries an explicit `MetricStatus` alongside
  `value: null` when data is absent, insufficient, or blocked.
- Logs never contain tokens, raw health values, or record IDs.
- Introspection stays on (for tooling/codegen); the interactive Sandbox
  landing page is controlled independently by `GRAPHQL_PLAYGROUND_ENABLED`
  (not `NODE_ENV` - see "Using the query explorer" below for why that
  distinction matters in practice).

## Known gaps (see the implementation report for the full list)

- `@defer` is declared in the schema (valid on fragments wrapping expensive
  fields like raw samples/stages) but is not yet functionally incremental:
  no released `@apollo/server` version (v4 or v5, stable/rc/alpha) currently
  supports graphql-js 17's stable incremental execution - its peer range is
  pinned to `graphql ^16.x`. Deferred selections execute correctly today,
  just as part of one complete response rather than a streamed one, until
  Apollo ships a release compatible with graphql 17.
- ~~`StrainSummary.workouts` always returns `[]`~~ **Fixed 2026-09-20.**
  `store.py`'s `_daily_documents` now writes a `strainWorkouts` field into
  each `_analytics_daily` date document (full per-workout strain detail:
  `loadMinutes`, `zoneMinutes`, `timeline`, `quality` - not just the reduced
  `strainContribution`/`strainQuality` `Day.timeline.workouts` already
  carried), grouped onto every local date the workout's start or end
  touches, mirroring `day_dashboard.py`'s existing workout/date membership
  rule so a midnight-spanning workout lands correctly on both dates. The
  GraphQL resolver reads it back across all dates and dedupes by workout id.
  Requires an analytics rebuild (`POST /api/v2/analytics/rebuild`) for
  existing runs computed before this change, since prepared runs are
  immutable - a new upload or rebuild is needed for this field to stop
  being empty on data processed prior to this fix.
- `HeartRateData.series(resolution:)` from the original design-doc
  hypothesis was not implemented: there is no bucketed-series aggregation
  for arbitrary resolutions anywhere in the Python analytics pipeline to
  read from, and this service is a thin passthrough by design (no new
  aggregation logic is implemented in Node). `records` and `samples` cover
  the same data at full detail; a bucketed series would need to be
  materialized by the Python pipeline first.

## Using the query explorer

Visiting `http://<host>:6645/graphql` directly in a browser serves Apollo's
embedded Sandbox UI (when `GRAPHQL_PLAYGROUND_ENABLED=true`) instead of raw
JSON. **This embedded version does not work over a LAN IP or any non-`localhost`
host**, and it never will as configured: it's loaded from Apollo's own HTTPS
origin (`sandbox.embed.apollographql.com`) inside an iframe, and browsers
block an HTTPS page from calling a plain-HTTP endpoint as mixed content.
Visiting from `localhost` on the same machine as the containers has no such
mismatch and works directly; visiting via `http://192.168.x.x:6645/graphql`
from another device on the network will show "Unable to reach server" /
"Schema Introspection Failure" even with the right token, because the
browser never lets the request leave the page - this is a browser security
policy, not a server misconfiguration, and no server-side change fixes it
short of adding TLS in front of this service.

**From another device on the network, use the standalone Sandbox instead:**

1. Get a token (same as "Verifying it works" below).
2. Open **https://studio.apollographql.com/sandbox/explorer** - a normal,
   non-embedded HTTPS page. Browsers are generally more permissive about a
   top-level page (as opposed to an embedded iframe) reaching a local/private
   HTTP address, though this still depends on the browser and may not work
   everywhere.
3. Enter the endpoint URL at the top: `http://<host>:6645/graphql` (e.g.
   `http://192.168.8.239:6645/graphql`).
4. Open the **Headers** panel (bottom of the operation editor) and add:
   `Authorization` → `Bearer <token>`.
5. The schema explorer on the left should populate, and operations run
   against your real data, same as the embedded version would.

If the standalone version is also blocked by your browser's mixed-content
policy, the reliable fallback is opening `http://localhost:6645/graphql`
directly on the machine running the containers - there is no HTTPS/HTTP
mismatch there at all, so it works without any of the above.

## Verifying it works

```bash
docker compose up -d --build
docker compose ps   # all five services, including hcgateway_graphql_api, healthy

# Get a token the same way prior sessions have (see doc/session-handoff.md):
docker exec hcgateway_api python3 -c "
import pymongo
mongo = pymongo.MongoClient('mongodb://root:<password>@db:27017/hcgateway?authSource=admin')
print(mongo['hcgateway']['users'].find_one({'username': 'lucas'})['token'])
"

curl -s -X POST http://localhost:6645/graphql \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"query":"{ viewer { config { homeTimeZone } analytics { runId } } }"}'
```

Tests: `cd graphql-api && npm test` (Vitest + `mongodb-memory-server`, no
Docker required) - covers tenant isolation, missing-data representation,
prepared-contract enums/cache identities, persisted workout deduplication,
typed sleep breakdowns, and the depth/alias/complexity limiter rejecting
pathological queries. These are separate from and not part of the Python
`python -m unittest discover` suite.
