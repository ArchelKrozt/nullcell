"""Node identity — see NULLCELL-ARQUITECTURA.md §3.

One 32-byte seed produces both keys deterministically:

* **Ed25519** for signing (and, since §3, the key the address itself carries).
* **X25519** for ECDH, derived as ``sha256(seed || "nullcell-x25519")``.

Deriving X25519 from the same seed means a node has exactly one secret to back
up, and that reloading a seed reproduces an identical identity — which is the
success test for M0.

The key file is CBOR, mode 600, and may be encrypted with a passphrase using
argon2id (via PyNaCl, so there is no extra dependency) plus XSalsa20-Poly1305.
"""

from __future__ import annotations

import hashlib
import os
import stat
import sys
from pathlib import Path
from typing import Final

import cbor2
import nacl.pwhash
import nacl.secret
import nacl.signing
import nacl.utils
from nacl.public import PrivateKey as X25519PrivateKey

from nullcell.core import ids

__all__ = ["Identity", "IdentityError", "KEY_FILE_VERSION"]

KEY_FILE_VERSION: Final[int] = 1
SEED_LEN: Final[int] = 32
FILE_MODE: Final[int] = 0o600
_X25519_CONTEXT: Final[bytes] = b"nullcell-x25519"

# Interactive-grade argon2id: ~0.1 s and 32 MiB. The threat here is an attacker
# who already has the file, so this is a speed bump sized for a human typing a
# passphrase, not the anti-Sybil PoW of §10.
_OPSLIMIT: Final[int] = nacl.pwhash.argon2id.OPSLIMIT_INTERACTIVE
_MEMLIMIT: Final[int] = nacl.pwhash.argon2id.MEMLIMIT_INTERACTIVE


class IdentityError(Exception):
    """Key file missing, malformed, or the passphrase is wrong."""


class Identity:
    """A node's long-term keypair."""

    __slots__ = ("_seed", "_signing", "_x25519", "_nid")

    def __init__(self, seed: bytes) -> None:
        if len(seed) != SEED_LEN:
            raise IdentityError(f"seed must be {SEED_LEN} bytes, got {len(seed)}")
        self._seed = seed
        self._signing = nacl.signing.SigningKey(seed)
        self._x25519 = X25519PrivateKey(hashlib.sha256(seed + _X25519_CONTEXT).digest())
        self._nid = ids.encode_nid(self.public_key)

    # ---------------------------------------------------------------- keys
    @property
    def public_key(self) -> bytes:
        """Raw 32-byte Ed25519 public key — the value the address carries (§3)."""
        return bytes(self._signing.verify_key)

    @property
    def signing_key(self) -> nacl.signing.SigningKey:
        return self._signing

    @property
    def ed25519_seed(self) -> bytes:
        """The 32-byte Ed25519 seed.

        Needed to hand the same key to ``cryptography`` for the TLS certificate
        (§4). Secret: never log it, never put it in a descriptor.
        """
        return self._seed

    @property
    def x25519_private(self) -> X25519PrivateKey:
        return self._x25519

    @property
    def x25519_public(self) -> bytes:
        return bytes(self._x25519.public_key)

    @property
    def nid(self) -> str:
        return self._nid

    @property
    def hostname(self) -> str:
        return self._nid + ids.SUFFIX

    @property
    def routing_key(self) -> bytes:
        return ids.routing_key(self.public_key)

    @property
    def short(self) -> str:
        return ids.short(self._nid)

    # ------------------------------------------------------------ signing
    def sign(self, message: bytes) -> bytes:
        """Detached Ed25519 signature."""
        return self._signing.sign(message).signature

    @staticmethod
    def verify(pub: bytes, message: bytes, signature: bytes) -> bool:
        try:
            nacl.signing.VerifyKey(pub).verify(message, signature)
        except Exception:
            return False
        return True

    # --------------------------------------------------------- persistence
    @classmethod
    def generate(cls) -> Identity:
        return cls(nacl.utils.random(SEED_LEN))

    @classmethod
    def load_or_create(cls, path: Path, passphrase: str | None = None) -> tuple[Identity, bool]:
        """Load the identity at *path*, creating it on first run.

        Returns ``(identity, created)`` so the caller can tell the user which
        happened — a silently regenerated identity is a new node on the network.
        """
        if path.exists():
            return cls.load(path, passphrase), False
        identity = cls.generate()
        identity.save(path, passphrase)
        return identity, True

    @classmethod
    def load(cls, path: Path, passphrase: str | None = None) -> Identity:
        try:
            blob = cbor2.loads(path.read_bytes())
        except Exception as exc:
            raise IdentityError(f"{path} is not a readable Nullcell key file: {exc}") from exc

        if not isinstance(blob, dict) or blob.get("v") != KEY_FILE_VERSION:
            raise IdentityError(f"unsupported key file version in {path}")

        if blob.get("enc"):
            if passphrase is None:
                raise IdentityError(f"{path} is passphrase-protected")
            key = nacl.pwhash.argon2id.kdf(
                nacl.secret.SecretBox.KEY_SIZE,
                passphrase.encode(),
                blob["salt"],
                opslimit=blob["ops"],
                memlimit=blob["mem"],
            )
            try:
                seed = nacl.secret.SecretBox(key).decrypt(blob["seed"])
            except Exception as exc:
                raise IdentityError("wrong passphrase") from exc
        else:
            seed = blob["seed"]

        return cls(seed)

    def save(self, path: Path, passphrase: str | None = None) -> None:
        """Write the key file atomically with mode 600."""
        if passphrase is None:
            blob = {"v": KEY_FILE_VERSION, "enc": False, "seed": self._seed}
        else:
            salt = nacl.utils.random(nacl.pwhash.argon2id.SALTBYTES)
            key = nacl.pwhash.argon2id.kdf(
                nacl.secret.SecretBox.KEY_SIZE,
                passphrase.encode(),
                salt,
                opslimit=_OPSLIMIT,
                memlimit=_MEMLIMIT,
            )
            blob = {
                "v": KEY_FILE_VERSION,
                "enc": True,
                "salt": salt,
                "ops": _OPSLIMIT,
                "mem": _MEMLIMIT,
                "seed": nacl.secret.SecretBox(key).encrypt(self._seed),
            }

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        # Create with 600 from the start: never let the secret exist world-readable,
        # not even for the instant between write and chmod.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(cbor2.dumps(blob))
                fh.flush()
                os.fsync(fh.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, path)
        if sys.platform != "win32":
            path.chmod(FILE_MODE)

    def check_permissions(self, path: Path) -> str | None:
        """Return a warning if the key file is readable by anyone else."""
        if sys.platform == "win32" or not path.exists():
            return None
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            return f"{path} tiene modo {mode:04o}; debería ser 0600"
        return None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Identity {self.short}>"
