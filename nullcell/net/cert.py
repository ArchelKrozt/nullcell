"""Self-signed QUIC certificates bound to the node identity — §4.

The certificate's subject public key *is* the node's Ed25519 identity key, and
the certificate is self-signed with that same key. Since an address carries the
public key (§3), verifying a peer is a 32-byte comparison done locally: no
descriptor fetch, no CA, no DNS, and the world PKI is ignored entirely.
"""

from __future__ import annotations

import datetime as dt
import hmac
from typing import Final

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.x509.oid import NameOID

from nullcell.core import ids
from nullcell.core.identity import Identity

__all__ = ["CertError", "cert_public_key", "make_cert", "verify_cert"]

CERT_VALIDITY_DAYS: Final[int] = 30


class CertError(Exception):
    """The peer certificate does not match the address we asked for."""


def make_cert(identity: Identity) -> tuple[x509.Certificate, Ed25519PrivateKey]:
    """Build a self-signed certificate whose public key is the node identity."""
    # The same seed that produced the PyNaCl signing key, loaded into
    # `cryptography` so aioquic can use it for TLS.
    private = Ed25519PrivateKey.from_private_bytes(identity.ed25519_seed)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, identity.nid)])
    now = dt.datetime.now(dt.UTC)

    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=CERT_VALIDITY_DAYS))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(identity.nid + ids.SUFFIX)]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(private, algorithm=None)
    )
    return cert, private


def cert_public_key(cert: x509.Certificate) -> bytes:
    """Raw 32-byte Ed25519 public key from a certificate."""
    pub = cert.public_key()
    if not isinstance(pub, Ed25519PublicKey):
        raise CertError(f"peer certificate key is {type(pub).__name__}, expected Ed25519")
    return pub.public_bytes(Encoding.Raw, PublicFormat.Raw)


def verify_cert(cert: x509.Certificate, expected_pub: bytes) -> None:
    """Check a peer certificate against the key carried by its address (§4).

    Raises :class:`CertError` on any mismatch. This replaces PKI validation
    outright — there is no chain, no CA and no name resolution involved.
    """
    actual = cert_public_key(cert)

    # Not a timing-sensitive comparison (both values are public), but constant
    # time costs nothing and keeps the habit.
    if not hmac.compare_digest(actual, expected_pub):
        raise CertError(
            "peer presented a different identity: address says "
            f"{ids.short(ids.encode_nid(expected_pub))}, peer is "
            f"{ids.short(ids.encode_nid(actual))}"
        )

    # The certificate must also be self-signed by that key: otherwise anyone
    # could wrap someone else's public key in a certificate they control.
    try:
        Ed25519PublicKey.from_public_bytes(actual).verify(
            cert.signature, cert.tbs_certificate_bytes
        )
    except Exception as exc:
        raise CertError("peer certificate is not self-signed by its own key") from exc
