# Leaderboards: goal and working plan

Status: goal agreed, design not final (2026-10-17). Nothing here is built yet.

## Goal

Lucas and a close friend each sync their own Health Connect data into this
HCGateway instance, under separate accounts. On the dashboard, both of them
can compare and contrast on leaderboards, for example:

- who slept more over the last week or month
- who did more exercise or activity (steps, strain, workouts)
- other per-person totals and averages worth competing on

Either person can open the same leaderboard page from their own account.

## What exists today

- Accounts and sessions: each person has their own account. Accounts are
  created on the dashboard's `/signup`, and every phone and browser holds its
  own non-expiring session (`api/auth_sessions.py`).
- Per-user isolation: every user's data lives in their own database
  (`hcgateway_<userId>`), and the GraphQL API derives the database from the
  session token alone, so no query can read another user's data
  (`graphql-api/src/__tests__/tenantIsolation.test.ts`). Leaderboards need a
  deliberate, consented exception to this, not a loosening of it.
- Prepared analytics: the analytics worker already computes daily sleep,
  steps, strain, recovery and more per user. Leaderboard numbers can be
  derived from those daily documents instead of re-reading raw records.

## Options considered

1. **Admin account that can read all data.** Simple to reason about, but it
   punches a hole in tenant isolation, and the friend's dashboard would need
   the admin credential to see the board.
2. **Prepared leaderboards (leaning this way).** A small sharing record (a
   "group") says which accounts agree to compare. The analytics worker writes
   weekly and monthly per-member totals for each group into a shared
   collection. GraphQL exposes one field that returns a group's board only to
   its members. This matches the backend rule that computation happens in the
   Python pipeline and GraphQL only serves stored results (`AGENTS.md`).
3. **Full read-only sharing.** Because Lucas and his friend are happy to share
   everything, the same group record could later let a member open the
   other's whole dashboard read-only. A possible later step on top of option
   2, not a starting point.

## Open questions

- Which metrics, and how each is ranked (total vs. average; how missing days
  count; missing data must never become zero).
- Window definitions: calendar week (Monday to Sunday) and month, or rolling
  7 and 30 days.
- Group shape: just two people, or groups that can grow.
- How a group is formed: an invite the other person accepts, or set up by
  hand on the server at first.
- Time zones: each person's days are in their own home time zone.

## Related

- Public hosting is planned behind Cloudflare Access, possibly with single
  sign-on into the matching account. That changes how people sign in, not how
  leaderboard data is shared.
