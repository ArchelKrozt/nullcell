"""Outbound links with identity verification — §4.

Verification deliberately does *not* use the world PKI. The address already
carries the peer's public key (§3), so after the handshake we compare the
certificate's key against it and drop the connection on any mismatch. MITM is
impossible without the peer's private key, with no CA and no DNS involved.
"""

from __future__ import annotations

import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aioquic.asyncio import connect
from aioquic.quic.configuration import QuicConfiguration

from nullcell.core import ids
from nullcell.net.cert import CertError, verify_cert
from nullcell.net.link import FrameProtocol
from nullcell.net.listener import ALPN

__all__ = ["client_configuration", "open_link"]


def client_configuration() -> QuicConfiguration:
    """QUIC configuration for a client.

    ``verify_mode`` is ``CERT_NONE`` because chain validation against public
    CAs is meaningless here — the certificate is self-signed on purpose. aioquic
    still parses and keeps the peer certificate, which is what we verify against
    the address. Skipping this check would make every link MITM-able, so
    :func:`open_link` always performs it.
    """
    config = QuicConfiguration(is_client=True, alpn_protocols=ALPN, idle_timeout=60.0)
    config.verify_mode = ssl.CERT_NONE
    return config


@asynccontextmanager
async def open_link(
    nid: str,
    host: str,
    port: int,
    *,
    verify: bool = True,
) -> AsyncIterator[FrameProtocol]:
    """Open a verified link to *nid* reachable at *host*:*port*.

    ``verify=False`` exists only for benchmarking the transport in isolation and
    must never be used on a real link.
    """
    address = ids.decode_nid(nid)

    async with connect(
        host,
        port,
        configuration=client_configuration(),
        create_protocol=FrameProtocol,
    ) as proto:
        await proto.handshake_done

        if verify:
            cert = proto.peer_certificate
            if cert is None:
                raise CertError(
                    "peer presented no certificate (or this aioquic build moved "
                    "the attribute FrameProtocol.peer_certificate reads)"
                )
            verify_cert(cert, address.pub)

        proto.send_versions()
        yield proto
