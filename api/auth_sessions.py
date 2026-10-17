"""Per-device login sessions.

Every login creates its own session, so the phone and the dashboard each hold
an independent token and signing out of one leaves the other alone. Sessions
don't expire; they end only when that device signs out (``/revoke``).

Tokens are stored as SHA-256 hashes, so a database dump doesn't hand out
working credentials. ``graphql-api/src/security/auth.ts`` mirrors
``user_id_for_token`` and must stay in sync with it.

Legacy single-token fields on the user document (``token``/``refresh``/
``expiry``) are still honoured so installs from before this change keep
working: their access token until it expires, and their refresh token, which
is converted into a session the first time it is used.
"""

import datetime
import hashlib
import secrets

SESSIONS_COLLECTION = 'sessions'

# The response still carries `expiry` for older app builds. Sessions don't
# expire, so report a date far enough out that no client ever refreshes early.
_REPORTED_LIFETIME = datetime.timedelta(days=365 * 100)

_indexes_ready = False


def _hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _sessions(db):
    global _indexes_ready
    sessions = db[SESSIONS_COLLECTION]
    if not _indexes_ready:
        sessions.create_index('tokenHash', unique=True)
        sessions.create_index('refreshHash', unique=True)
        sessions.create_index('userId')
        _indexes_ready = True
    return sessions


def _response(token, refresh):
    expiry = datetime.datetime.now(datetime.timezone.utc) + _REPORTED_LIFETIME
    return {'token': token, 'refresh': refresh, 'expiry': expiry.isoformat()}


def create_session(db, user_id, client=None):
    """Start a new session for ``user_id``; returns the login response body."""
    token = secrets.token_urlsafe(32)
    refresh = secrets.token_urlsafe(32)
    _sessions(db).insert_one({
        'userId': user_id,
        'tokenHash': _hash(token),
        'refreshHash': _hash(refresh),
        'client': client,
        'createdAt': datetime.datetime.now(datetime.timezone.utc),
    })
    return _response(token, refresh)


def user_id_for_token(db, token):
    """The user id a bearer token belongs to, or None if it isn't valid."""
    session = _sessions(db).find_one({'tokenHash': _hash(token)}, {'userId': 1})
    if session:
        return session['userId']

    legacy = db['users'].find_one({'token': token}, {'expiry': 1})
    if legacy and 'expiry' in legacy and datetime.datetime.now() <= legacy['expiry']:
        return legacy['_id']
    return None


def refresh_session(db, refresh):
    """Issue a new access token for the session ``refresh`` belongs to.

    Returns the response body, or None for an unknown refresh token. The
    refresh token itself is not rotated, matching the old behaviour.
    """
    sessions = _sessions(db)
    session = sessions.find_one({'refreshHash': _hash(refresh)})
    if session:
        token = secrets.token_urlsafe(32)
        sessions.update_one({'_id': session['_id']}, {'$set': {'tokenHash': _hash(token)}})
        return _response(token, refresh)

    legacy = db['users'].find_one({'refresh': refresh})
    if legacy:
        db['users'].update_one({'_id': legacy['_id']}, {'$unset': {'token': 1, 'refresh': 1, 'expiry': 1}})
        return create_session(db, legacy['_id'], client='migrated')
    return None


def revoke_session(db, token):
    """End the session ``token`` belongs to. Returns False if there was none."""
    if _sessions(db).delete_one({'tokenHash': _hash(token)}).deleted_count:
        return True
    result = db['users'].update_one({'token': token}, {'$unset': {'token': 1, 'refresh': 1, 'expiry': 1}})
    return result.modified_count > 0
