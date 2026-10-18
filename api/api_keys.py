"""Read-only API keys.

A key lets a script read its owner's health data through the GraphQL API.
Read-only holds by construction: GraphQL has no mutations, and the REST API
(sync uploads, password changes, sessions, key management) never accepts a
key, because ``auth_sessions.user_id_for_token`` only knows sessions. Only
``graphql-api/src/security/auth.ts`` looks keys up.

Keys look like ``hcg_<43 url-safe characters>``. Only the SHA-256 is stored,
with the first few characters kept so people can tell their keys apart; the
full key is shown once, when it's made. Making, listing and revoking keys
all need a signed-in session.
"""

import datetime
import hashlib
import re
import secrets

from bson.objectid import ObjectId

KEYS_COLLECTION = 'api_keys'
KEY_PREFIX = 'hcg_'
MAX_KEYS_PER_USER = 10
# Enough for "hcg_" plus 6 characters of the secret, which is plenty to tell
# ten keys apart and far too little to guess the rest.
_DISPLAY_LENGTH = 10

# Names are labels for the owner ("laptop export", "grafana"), so a short
# allowlist keeps them safe to show anywhere.
_NAME = re.compile(r'[A-Za-z0-9 ._()-]{1,48}')
NAME_RULE = 'name must be 1-48 characters: letters, digits, spaces, dots, dashes, underscores or parentheses'
_KEY_ID = re.compile(r'[0-9a-f]{24}')

_indexes_ready = False


def _keys(db):
    global _indexes_ready
    keys = db[KEYS_COLLECTION]
    if not _indexes_ready:
        keys.create_index('keyHash', unique=True)
        keys.create_index('userId')
        _indexes_ready = True
    return keys


def hash_key(key):
    return hashlib.sha256(key.encode('utf-8')).hexdigest()


def normalize_name(value):
    """The key name to store, or None if ``value`` isn't a valid one."""
    if not isinstance(value, str):
        return None
    name = ' '.join(value.split())
    return name if _NAME.fullmatch(name) else None


def _public(doc):
    return {
        'id': doc['_id'],
        'name': doc['name'],
        'prefix': doc['prefix'],
        'createdAt': doc['createdAt'].isoformat(),
        'lastUsedAt': doc['lastUsedAt'].isoformat() if doc.get('lastUsedAt') else None,
    }


def create_key(db, user_id, name):
    """Make a key for ``user_id``. Returns ``(public fields, full key)``, or
    None if the user already has ``MAX_KEYS_PER_USER`` keys."""
    keys = _keys(db)
    if keys.count_documents({'userId': user_id}) >= MAX_KEYS_PER_USER:
        return None
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    doc = {
        '_id': str(ObjectId()),
        'userId': user_id,
        'name': name,
        'keyHash': hash_key(key),
        'prefix': key[:_DISPLAY_LENGTH],
        'scope': 'read',
        'createdAt': datetime.datetime.now(datetime.timezone.utc),
        'lastUsedAt': None,
    }
    keys.insert_one(doc)
    return _public(doc), key


def list_keys(db, user_id):
    """``user_id``'s keys, newest first, without hashes."""
    return [_public(doc) for doc in _keys(db).find({'userId': user_id}).sort('createdAt', -1)]


def revoke_key(db, user_id, key_id):
    """Delete one of ``user_id``'s keys. Returns False if they have no key
    with that id; someone else's key id counts as not found."""
    if not isinstance(key_id, str) or not _KEY_ID.fullmatch(key_id):
        return False
    return _keys(db).delete_one({'_id': key_id, 'userId': user_id}).deleted_count > 0
