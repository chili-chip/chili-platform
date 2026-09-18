"""Password hashers that work in Cloudflare Python Workers.

Pyodide hashlib has SHA-256 but not OpenSSL's pbkdf2_hmac. Django's default
PBKDF2 hasher therefore cannot run unless we polyfill KDF, which is still
too expensive at 1_000_000 iterations on the Worker CPU budget.
"""

from __future__ import annotations

import hashlib

from django.contrib.auth.hashers import BasePasswordHasher
from django.utils.crypto import constant_time_compare, get_random_string
from django.utils.encoding import force_bytes


class SaltedSHA256PasswordHasher(BasePasswordHasher):
    algorithm = "salted_sha256"

    def salt(self) -> str:
        return get_random_string(16)

    def encode(self, password, salt):
        self._check_encode_args(password, salt)
        digest = hashlib.sha256(force_bytes(salt) + force_bytes(password)).hexdigest()
        return f"{self.algorithm}${salt}${digest}"

    def decode(self, encoded):
        algorithm, salt, digest = encoded.split("$", 2)
        return {"algorithm": algorithm, "salt": salt, "hash": digest}

    def verify(self, password, encoded):
        decoded = self.decode(encoded)
        return constant_time_compare(encoded, self.encode(password, decoded["salt"]))

    def safe_summary(self, encoded):
        decoded = self.decode(encoded)
        return {
            "algorithm": decoded["algorithm"],
            "salt": decoded["salt"],
            "hash": decoded["hash"][:8],
        }

    def must_update(self, encoded):
        return False
