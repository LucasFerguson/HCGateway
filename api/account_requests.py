"""Account requests waiting for an admin.

Signing up doesn't create an account: it files a request holding the
username and an already-hashed password, and an admin turns it into a real
account with ``manage_accounts.py``. Until then the username can't sign in
anywhere, on the dashboard or the phone app.

``/signup`` answers every well-formed request the same way, whether it was
queued or not, so the form can't be used to find out which usernames exist.
"""

import datetime
import logging

from bson.objectid import ObjectId
from pymongo.errors import DuplicateKeyError

REQUESTS_COLLECTION = 'account_requests'

# The signup form is public, so cap the queue: past this, new requests are
# dropped (and logged) instead of growing the database without bound.
MAX_PENDING = 50

_indexes_ready = False
log = logging.getLogger(__name__)


def _requests(db):
    global _indexes_ready
    requests = db[REQUESTS_COLLECTION]
    if not _indexes_ready:
        requests.create_index('username', unique=True)
        _indexes_ready = True
    return requests


def file_request(db, username, password_hash, client=None):
    """Queue a request. Returns True if it was queued, False if the username
    already has an account or a request, or the queue is full."""
    if db['users'].find_one({'username': username}, {'_id': 1}):
        return False
    requests = _requests(db)
    if requests.count_documents({}) >= MAX_PENDING:
        log.warning('account request queue is full (%d); dropped a request', MAX_PENDING)
        return False
    try:
        requests.insert_one({
            'username': username,
            'password': password_hash,
            'client': client,
            'requestedAt': datetime.datetime.now(datetime.timezone.utc),
        })
    except DuplicateKeyError:
        return False
    return True


def pending_request(db, username):
    """The waiting request for ``username``, or None."""
    return _requests(db).find_one({'username': username})


def list_requests(db):
    """Every waiting request, oldest first, without password hashes."""
    return list(_requests(db).find({}, {'password': 0}).sort('requestedAt', 1))


def approve_request(db, username):
    """Turn ``username``'s request into an account. Returns the new user id,
    or None if there was no request or the username is already taken."""
    requests = _requests(db)
    request = requests.find_one({'username': username})
    if not request:
        return None
    if db['users'].find_one({'username': username}, {'_id': 1}):
        return None
    user_id = str(ObjectId())
    db['users'].insert_one({
        '_id': user_id,
        'username': username,
        'password': request['password'],
        'approvedAt': datetime.datetime.now(datetime.timezone.utc),
    })
    requests.delete_one({'_id': request['_id']})
    return user_id


def reject_request(db, username):
    """Delete ``username``'s request. Returns False if there was none."""
    return _requests(db).delete_one({'username': username}).deleted_count > 0
