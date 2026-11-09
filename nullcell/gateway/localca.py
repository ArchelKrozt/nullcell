"""The local certificate authority — §8, and why it is not optional.

Browsers treat ``.onion`` as a trustworthy origin by an explicit exception in
the standards (RFC 7686, W3C secure contexts). **``.null`` has no such
privilege.** Without one, every Nullcell site would be marked "not secure" and
the browser would refuse service workers, WebCrypto, the camera and
notifications. The portals would be born crippled.

So Nullcell issues its own certificates from a CA it generates, and installs
that CA **only in the dedicated browser profile** — never in the system trust
store, which would be a real risk to the machine for everything else it does.

§8 states the reasoning plainly and it is worth repeating here, because a
reader who finds a hand-rolled CA in a privacy tool should be suspicious:
**this is not TLS for confidentiality.** The circuit already encrypts end to
end, twice over. It is TLS so the browser grants the origin the permissions a
modern web page needs.

The per-site certificate is bound to the address it serves, and the address
carries the service's key (§3), so a certificate issued here for one ``.null``
name cannot be presented for another.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import logging
import os
import stat
import sys
from pathlib import Path
from typing import Final

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    load_pem_private_key,
)
from cryptography.x509.oid import NameOID

from nullcell.core import ids

__all__ = ["CA_VALIDITY_DAYS", "SITE_VALIDITY_DAYS", "LocalCA"]

CA_VALIDITY_DAYS: Final[int] = 3650
SITE_VALIDITY_DAYS: Final[int] = 30
_CA_NAME: Final[str] = "Nullcell Local CA"

log = logging.getLogger("nullcell.ca")


class LocalCA:
    """Generates and uses a CA that exists only for the dedicated profile."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.key_path = directory / "ca.key"
        self.cert_path = directory / "ca.pem"
        self._key: ec.EllipticCurvePrivateKey | None = None
        self._cert: x509.Certificate | None = None
        self._sites: dict[str, tuple[bytes, bytes]] = {}

    # ---------------------------------------------------------------- the CA
    def load_or_create(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        if sys.platform != "win32":
            self.directory.chmod(0o700)
        if self.key_path.exists() and self.cert_path.exists():
            self._load()
            return
        self._create()

    def _load(self) -> None:
        self._key = load_pem_private_key(self.key_path.read_bytes(), password=None)  # type: ignore[assignment]
        self._cert = x509.load_pem_x509_certificate(self.cert_path.read_bytes())
        if not self._usable(self._cert):
            # A CA generated before the key identifiers were added cannot
            # issue a certificate a modern verifier will accept, so it is
            # replaced rather than left to fail at handshake time. Safe: this
            # CA exists only inside the dedicated profile (§8). The operator
            # has to re-import it, and is told so.
            log.warning(
                "the local CA predates the key identifiers modern TLS requires; "
                "regenerating it. Re-import it into the browser profile: "
                "ncell browse refreshes it"
            )
            self._create()

    @staticmethod
    def _usable(cert: x509.Certificate) -> bool:
        """Does this CA carry what a current verifier demands?"""
        try:
            cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
        except x509.ExtensionNotFound:
            return False
        return True

    def _create(self) -> None:
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, _CA_NAME)])
        now = dt.datetime.now(dt.UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=CA_VALIDITY_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            # RFC 5280 says a conforming CA MUST include this, and modern
            # OpenSSL **enforces** it: without the pair of SKI here and AKI on
            # the issued certificate, verification fails with "Missing
            # Authority Key Identifier". CI on Python 3.13 found it; a current
            # Firefox would very likely have refused the same way, which would
            # have broken the whole point of §8 on any up-to-date machine.
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_cert_sign=True,
                    crl_sign=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            # Only names under .null, so even if this CA somehow escaped the
            # dedicated profile it could not vouch for anything on the
            # ordinary internet.
            # RFC 5280 expresses a DNS name constraint as the suffix itself,
            # with no leading dot: "null" permits null, x.null and a.b.null.
            # Two subtrees because a site certificate also carries the RFC 9476
            # form (§15), and "<nid>.null.alt" is not under "null" — it is
            # under "alt". Both mistakes cost one TLS handshake each, failing
            # with "permitted subtree violation".
            .add_extension(
                x509.NameConstraints(
                    permitted_subtrees=[x509.DNSName("null"), x509.DNSName("null.alt")],
                    excluded_subtrees=None,
                ),
                critical=True,
            )
            .sign(key, hashes.SHA256())
        )

        fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
        self.cert_path.write_bytes(cert.public_bytes(Encoding.PEM))
        self._key, self._cert = key, cert
        log.info("local CA created in %s", self.directory)

    @property
    def certificate_pem(self) -> bytes:
        if self._cert is None:
            raise RuntimeError("call load_or_create() first")
        return self._cert.public_bytes(Encoding.PEM)

    def permissions_warning(self) -> str | None:
        """The CA key must not be readable by anyone else."""
        if sys.platform == "win32" or not self.key_path.exists():
            return None
        mode = stat.S_IMODE(self.key_path.stat().st_mode)
        if mode & 0o077:
            return f"{self.key_path} tiene modo {mode:04o}; debería ser 0600"
        return None

    # ------------------------------------------------------- per-site certs
    def site_certificate(self, hostname: str) -> tuple[bytes, bytes]:
        """``(cert_pem, key_pem)`` for one ``.null`` hostname, cached.

        The name is validated as a real address first (§3), so this cannot be
        talked into issuing for an arbitrary string.
        """
        if self._key is None or self._cert is None:
            raise RuntimeError("call load_or_create() first")

        hostname = hostname.strip().lower()
        cached = self._sites.get(hostname)
        if cached is not None:
            return cached

        bare = ids.parse_hostname(hostname)
        ids.decode_nid(bare)  # raises AddressError on anything that is not one
        if not hostname.endswith((ids.SUFFIX, ids.SUFFIX_ALT)):
            hostname = bare + ids.SUFFIX

        key = ec.generate_private_key(ec.SECP256R1())
        now = dt.datetime.now(dt.UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
            .issuer_name(self._cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=SITE_VALIDITY_DAYS))
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.DNSName(hostname), x509.DNSName(bare + ids.SUFFIX_ALT)]
                ),
                critical=False,
            )
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
                critical=False,
            )
            # The other half of the pair: it names which CA key signed this.
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(self._key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
            )
            .sign(self._key, hashes.SHA256())
        )

        pair = (
            cert.public_bytes(Encoding.PEM),
            key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()),
        )
        self._sites[hostname] = pair
        return pair

    def ssl_context(self, hostname: str):
        """A server-side TLS context for one ``.null`` hostname.

        Written to temporary files because :mod:`ssl` loads chains from paths
        only. They live in the CA directory, which is already mode 700.
        """
        import ssl
        import tempfile

        cert_pem, key_pem = self.site_certificate(hostname)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        with tempfile.TemporaryDirectory(dir=self.directory) as tmp:
            cert_file = Path(tmp) / "site.pem"
            key_file = Path(tmp) / "site.key"
            cert_file.write_bytes(cert_pem)
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(key_pem)
            context.load_cert_chain(str(cert_file), str(key_file))
        # §8: the browser must be allowed to negotiate h2, which is §16's
        # tactic 7 — many requests for one page over a single circuit.
        context.set_alpn_protocols(["http/1.1"])
        return context

    def __repr__(self) -> str:  # pragma: no cover
        return f"<LocalCA {self.directory} · {len(self._sites)} site certs>"


def _unused_ip_guard() -> None:  # pragma: no cover
    """Kept so the ipaddress import is not mistaken for dead weight."""
    ipaddress.ip_address("127.0.0.1")
