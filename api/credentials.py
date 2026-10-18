"""What a username, password and client label may look like.

These are allowlists on purpose: anything that reaches a Mongo query, a log
line or an admin's terminal (``manage_accounts.py list``) has already been
reduced to a short run of plain ASCII characters, so query operators, control
characters, terminal escapes and look-alike Unicode can't get through.
"""

import re

# Lowercase so `Lucas` and `lucas` can't be two different people.
_USERNAME = re.compile(r'[a-z0-9][a-z0-9._-]{2,31}')
_CLIENT = re.compile(r'[a-z0-9-]{1,32}')

USERNAME_RULE = 'username must be 3-32 characters: lowercase letters, digits, dots, dashes or underscores, starting with a letter or digit'
PASSWORD_MIN = 8
# Argon2 copes with long input, but nobody needs more, and it bounds the work.
PASSWORD_MAX = 256
REFRESH_MAX = 128


def normalize_username(value):
    """The canonical form of ``value``, or None if it isn't a valid username."""
    if not isinstance(value, str):
        return None
    username = value.strip().lower()
    return username if _USERNAME.fullmatch(username) else None


def valid_password(value, minimum=PASSWORD_MIN):
    return isinstance(value, str) and minimum <= len(value) <= PASSWORD_MAX


def client_label(value):
    """Which app is signing in (``dashboard``, ``android``...), or None."""
    return value if isinstance(value, str) and _CLIENT.fullmatch(value) else None


def printable(value):
    """``value`` safe to print to a terminal: anything outside printable
    ASCII is shown as an escape instead of being interpreted."""
    return str(value).encode('ascii', 'backslashreplace').decode('ascii').translate(
        {code: f'\\x{code:02x}' for code in [*range(0x20), 0x7f]}
    )
