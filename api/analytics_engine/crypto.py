import base64
import json

from cryptography.fernet import Fernet


def cipher_for_user(user):
    password_hash = user["password"]
    key = base64.urlsafe_b64encode(password_hash.encode("utf-8").ljust(32)[:32])
    return Fernet(key)


def encrypt_json(cipher, value):
    return cipher.encrypt(json.dumps(value, separators=(",", ":")).encode()).decode()


def decrypt_json(cipher, value):
    return json.loads(cipher.decrypt(value.encode()).decode())
