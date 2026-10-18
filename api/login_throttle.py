"""Slows down password guessing on ``/login``.

After ``MAX_FAILURES`` failed sign-ins for one username within ``WINDOW``,
that username is refused until the oldest failure ages out, even with the
right password. Failures are counted per username, not per address: every
dashboard sign-in reaches this API from the dashboard's container, so
addresses would lump all visitors together. The dashboard keeps its own
per-visitor limit on top of this.

Unknown usernames are counted the same way, so lockouts don't reveal which
usernames exist. A TTL index removes failures once they're out of the window.
"""

import datetime

FAILURES_COLLECTION = 'login_failures'
MAX_FAILURES = 10
WINDOW = datetime.timedelta(minutes=15)

_indexes_ready = False


def _failures(db):
    global _indexes_ready
    failures = db[FAILURES_COLLECTION]
    if not _indexes_ready:
        failures.create_index('at', expireAfterSeconds=int(WINDOW.total_seconds()))
        failures.create_index('username')
        _indexes_ready = True
    return failures


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def is_locked(db, username):
    """Whether ``username`` has used up its failed attempts for now."""
    recent = {'username': username, 'at': {'$gte': _now() - WINDOW}}
    return _failures(db).count_documents(recent, limit=MAX_FAILURES) >= MAX_FAILURES


def record_failure(db, username):
    _failures(db).insert_one({'username': username, 'at': _now()})


def clear_failures(db, username):
    """Forget ``username``'s failures after it signs in successfully."""
    _failures(db).delete_many({'username': username})
