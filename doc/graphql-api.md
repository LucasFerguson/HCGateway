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
}
```

See `graphql-api/src/schema/typeDefs.ts` for the complete SDL - it is the
source of truth; this document is a pointer, not a duplicate.

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
- Introspection stays on (for tooling/codegen); the GraphiQL landing page is
  disabled outside development (`GRAPHQL_PLAYGROUND_ENABLED`).

## Known gaps (see the implementation report for the full list)

- `@defer` is declared in the schema (valid on fragments wrapping expensive
  fields like raw samples/stages) but is not yet functionally incremental:
  no released `@apollo/server` version (v4 or v5, stable/rc/alpha) currently
  supports graphql-js 17's stable incremental execution - its peer range is
  pinned to `graphql ^16.x`. Deferred selections execute correctly today,
  just as part of one complete response rather than a streamed one, until
  Apollo ships a release compatible with graphql 17.
- `StrainSummary.workouts` always returns `[]`: per-workout strain detail is
  computed in-memory during a pipeline run (`pipeline.py`) but is not
  persisted to any `_analytics_*` collection, so there is nothing for this
  read-only service to read back. Needs a Python-side change (e.g.
  persisting `strain.workouts` into `_analytics_daily` or a new collection)
  before this field can return real data.
- `HeartRateData.series(resolution:)` from the original design-doc
  hypothesis was not implemented: there is no bucketed-series aggregation
  for arbitrary resolutions anywhere in the Python analytics pipeline to
  read from, and this service is a thin passthrough by design (no new
  aggregation logic is implemented in Node). `records` and `samples` cover
  the same data at full detail; a bucketed series would need to be
  materialized by the Python pipeline first.

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
Docker required) - covers tenant isolation, missing-data representation, and
the depth/alias/complexity limiter rejecting pathological queries. These are
separate from and not part of the Python `python -m unittest discover` suite.
