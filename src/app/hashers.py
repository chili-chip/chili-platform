"""Password hashers that work in Cloudflare Python Workers.

New passwords use PBKDF2-SHA256 through ``hashlib.pbkdf2_hmac``. On a Worker
that function is the Web Crypto implementation in ``app.hashlib_compat``.
Django's default of 1_000_000 iterations overruns the Worker CPU budget, so
new hashes use ``WorkerPBKDF2PasswordHasher.iterations``.

``SaltedSHA256PasswordHasher`` stays installed so hashes written before this
change still verify. A successful login rewrites them with PBKDF2.
"""

from __future__ import annotations

import hashlib

from django.contrib.auth.hashers import BasePasswordHasher, PBKDF2PasswordHasher
from django.utils.crypto import constant_time_compare, get_random_string
from django.utils.encoding import force_bytes


class WorkerPBKDF2PasswordHasher(PBKDF2PasswordHasher):
    """PBKDF2-SHA256 at a work factor that finishes inside one Worker request.

    Stored iteration counts are read back on verify, so a hash written with a
    different count (including Django's 1_000_000) still checks.
    """

    iterations = 100_000


class SaltedSHA256PasswordHasher(BasePasswordHasher):
    """Legacy hasher: one SHA-256 of salt + password. Verify only."""

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
