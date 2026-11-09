"""Private service descriptors — §6, the second class of service.

A **public portal** files its descriptor in the clear under ``sha256(nid)``, and
accepts the consequence §6 states plainly: being findable is being enumerable.
Any node storing the record learns the service and its intro points, and
walking the keyspace yields a census of the network.

A **private service** gets neither. Its descriptor is filed under a blinded,
time-rotated index and its body is encrypted, so the node holding it:

* cannot name the service — it sees only ``A' = h·A``, never ``A``,
* cannot read the contents — the key comes from the address, which it lacks,
* cannot find it by walking the keyspace, because the index rotates,
* and can still *verify* it, because the record is signed by the blinded key
  and carries it. Spam is refused without anyone being identified.

Three derivations, all computable by whoever knows the address and by nobody
else (:mod:`nullcell.onion.blinding`):

=================  ======================================================
blinded key        ``A' = h·A`` where ``h = H("nullcell-blind" ‖ A ‖ period)``
index              ``sha256("nullcell-index" ‖ A' ‖ period)``
body key           ``HKDF(sha256("nullcell-cred" ‖ A), period)``
=================  ======================================================
"""

from __future__ import annotations

import time
from typing import Any, Final

import cbor2
import nacl.secret
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from nullcell.core.identity import Identity
from nullcell.dht import pow as powmod
from nullcell.dht import records as rec
from nullcell.dht.records import Record, RecordError, RecordMalformed, RecordType
from nullcell.onion import blinding

__all__ = [
    "PrivateError",
    "build_private",
    "descriptor_key",
    "open_private",
    "publish_keys",
]

_HKDF_INFO: Final[bytes] = b"nullcell-descriptor-v1"


class PrivateError(RecordError):
    """A private descriptor that cannot be opened, or is not ours to open."""


def descriptor_key(pub: bytes, period: int) -> bytes:
    """Symmetric key for a private descriptor's body (§6)."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=nacl.secret.SecretBox.KEY_SIZE,
        salt=None,
        info=_HKDF_INFO + period.to_bytes(8, "big", signed=True),
    ).derive(blinding.credential(pub))


def publish_keys(pub: bytes, now: float | None = None) -> list[tuple[int, bytes]]:
    """``(period, index)`` pairs a descriptor should be filed under right now.

    More than one inside the overlap window, so a period boundary never makes a
    service briefly unreachable (§6).
    """
    now = time.time() if now is None else now
    return [(period, blinding.index_key(pub, period)) for period in blinding.periods_for(now)]


async def build_private(
    identity: Identity,
    body: dict[str, Any],
    *,
    period: int,
    ttl: int = 3600,
    ts: int | None = None,
) -> Record:
    """Build a private service descriptor for one time period.

    The record carries the **blinded** public key in its ``pub`` field and is
    signed by the matching blinded private key, so an ordinary Ed25519
    verification succeeds while the real key never appears.
    """
    blinded = blinding.blind_public(identity.public_key, period)
    scalar, prefix = blinding.blind_private(identity.ed25519_seed, identity.public_key, period)
    sealed = nacl.secret.SecretBox(descriptor_key(identity.public_key, period)).encrypt(
        cbor2.dumps(body, canonical=True)
    )

    draft = Record(
        type=RecordType.PRIVATE,
        key=blinding.index_key(identity.public_key, period),
        pub=blinded,
        ts=int(time.time()) if ts is None else ts,
        ttl=ttl,
        body={"p": period, "enc": bytes(sealed)},
    )

    challenge = draft.unsigned_bytes()
    nonce = await powmod.solve_async(challenge, draft.required_difficulty)
    signature = blinding.sign_blinded(challenge, scalar, prefix, blinded)

    signed = Record(
        type=draft.type,
        key=draft.key,
        pub=draft.pub,
        ts=draft.ts,
        ttl=draft.ttl,
        body=draft.body,
        nonce=nonce,
        sig=signature,
    )
    if len(signed.encode()) > rec.MAX_RECORD_BYTES:
        raise RecordMalformed("private descriptor exceeds the size limit once signed")
    return signed


def open_private(record: Record, pub: bytes, *, now: int | None = None) -> dict[str, Any]:
    """Verify and decrypt a private descriptor, given the service's address.

    Raises :class:`PrivateError` if the record is not this service's, or
    :class:`~nullcell.dht.records.RecordError` if it fails validation. The
    caller must know the address; that is the entire access control.
    """
    if record.type is not RecordType.PRIVATE:
        raise PrivateError(f"not a private descriptor: {record.type}")

    period = record.body.get("p")
    sealed = record.body.get("enc")
    if not isinstance(period, int) or not isinstance(sealed, bytes):
        raise RecordMalformed("private descriptor body must hold 'p' and 'enc'")

    # Full validation first — signature against the blinded key it carries,
    # key binding, clock and proof of work.
    rec.verify(record, now=now)

    # Then: is this descriptor actually the one for *this* address?
    if record.pub != blinding.blind_public(pub, period):
        raise PrivateError("this descriptor belongs to a different service")

    try:
        plaintext = nacl.secret.SecretBox(descriptor_key(pub, period)).decrypt(sealed)
    except Exception as exc:
        raise PrivateError(f"could not decrypt the descriptor: {exc}") from exc

    opened = cbor2.loads(plaintext)
    if not isinstance(opened, dict):
        raise RecordMalformed("decrypted descriptor is not a map")
    return opened
