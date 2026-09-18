"""Provide hashlib.pbkdf2_hmac on Cloudflare Python Workers.

Pyodide's default hashlib omits OpenSSL KDFs, so Django's PBKDF2 hasher
raises AttributeError. Prefer Web Crypto (native, fast); fall back to HMAC.
"""

from __future__ import annotations

import hashlib
import hmac

_DIGEST_SIZE = {
    "sha1": 20,
    "sha224": 28,
    "sha256": 32,
    "sha384": 48,
    "sha512": 64,
}

_WEB_HASH = {
    "sha1": "SHA-1",
    "sha256": "SHA-256",
    "sha384": "SHA-384",
    "sha512": "SHA-512",
}


def install() -> None:
    if not hasattr(hashlib, "pbkdf2_hmac"):
        hashlib.pbkdf2_hmac = pbkdf2_hmac  # type: ignore[attr-defined]


def pbkdf2_hmac(hash_name: str, password, salt, iterations: int, dklen: int | None = None) -> bytes:
    password = bytes(password)
    salt = bytes(salt)
    name = hash_name.lower().replace("-", "")
    if dklen is None:
        dklen = _DIGEST_SIZE[name]
    try:
        derived = _pbkdf2_webcrypto(name, password, salt, iterations, dklen)
    except Exception:
        derived = None
    if derived is not None:
        return derived
    return _pbkdf2_hmac_python(name, password, salt, iterations, dklen)


def _to_u8(data: bytes):
    from js import Uint8Array

    view = Uint8Array.new(len(data))
    for index, value in enumerate(data):
        view[index] = value
    return view


def _pbkdf2_webcrypto(
    hash_name: str, password: bytes, salt: bytes, iterations: int, dklen: int
) -> bytes | None:
    web_hash = _WEB_HASH.get(hash_name)
    if web_hash is None:
        return None
    try:
        from js import Uint8Array, crypto
        from pyodide.ffi import run_sync, to_js
    except ImportError:
        return None

    async def derive():
        key = await crypto.subtle.importKey(
            "raw",
            _to_u8(password),
            to_js({"name": "PBKDF2"}),
            False,
            to_js(["deriveBits"]),
        )
        bits = await crypto.subtle.deriveBits(
            to_js(
                {
                    "name": "PBKDF2",
                    "salt": _to_u8(salt),
                    "iterations": iterations,
                    "hash": web_hash,
                }
            ),
            key,
            dklen * 8,
        )
        return bytes(Uint8Array.new(bits))

    return run_sync(derive())


def _pbkdf2_hmac_python(
    hash_name: str, password: bytes, salt: bytes, iterations: int, dklen: int
) -> bytes:
    def prf(message: bytes) -> bytes:
        return hmac.new(password, message, hash_name).digest()

    digest_size = _DIGEST_SIZE[hash_name]
    block_count = (dklen + digest_size - 1) // digest_size
    output = bytearray()
    for block in range(1, block_count + 1):
        u = prf(salt + block.to_bytes(4, "big"))
        mixed = bytearray(u)
        for _ in range(iterations - 1):
            u = prf(u)
            for index, value in enumerate(u):
                mixed[index] ^= value
        output.extend(mixed)
    return bytes(output[:dklen])
