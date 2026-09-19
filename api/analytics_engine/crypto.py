import base64
import json

from cryptography.fernet import Fernet


PLAIN_STORAGE_FORMAT = "plain-bson-v1"


def cipher_for_user(user):
    password_hash = user["password"]
    key = base64.urlsafe_b64encode(password_hash.encode("utf-8").ljust(32)[:32])
    return Fernet(key)


def encrypt_json(cipher, value):
    return cipher.encrypt(json.dumps(value, separators=(",", ":")).encode()).decode()


def decrypt_json(cipher, value):
    return json.loads(cipher.decrypt(value.encode()).decode())


def decode_stored_json(cipher, value):
    """Read either transitional plaintext BSON or a legacy Fernet JSON value."""
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        raise TypeError(f"Unsupported stored JSON value: {type(value).__name__}")
    return decrypt_json(cipher, value)
