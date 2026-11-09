"""Client-side circuits — telescopic construction, §5.

Three hops, built one at a time:

1. ``CREATE`` to the guard over a verified link (§4). The client presents no
   identity; the guard proves its own.
2. ``EXTEND`` **addressed to the guard**, carrying the next hop's address and a
   fresh Noise message for it. The guard opens its own link onward and relays
   the handshake. The client ends with a key it shares with hop 2 and with
   nobody else — the guard never saw it.
3. The same again for hop 3.

Afterwards the guard knows who you are but not where you are going, the exit
knows where you are going but not who you are, and the middle knows neither.

``EXTEND`` travels as a *relay* command, never as a link command: if the guard
could read and rewrite where you extend to, it would be choosing your path.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Final

from nullcell.core import ids
from nullcell.core.frames import (
    Cmd,
    RelayCmd,
    new_circ_id,
    pack,
    pack_relay_payload,
    parse_relay_payload,
)
from nullcell.net.link import FrameProtocol
from nullcell.onion.flow import FlowControl, FlowError
from nullcell.onion.handshake_nk import HANDSHAKE_LEN, HandshakeError, Initiator
from nullcell.onion.layers import LayerError, OnionPath

__all__ = [
    "BUILD_TIMEOUT",
    "MAX_AGE",
    "MAX_BYTES",
    "MAX_DIRTY",
    "MAX_IDLE",
    "Circuit",
    "CircuitError",
    "Hop",
]

BUILD_TIMEOUT: Final[float] = 30.0

#: §5 — a circuit stops taking *new* streams after this long in use. A circuit
#: that lives for hours links together everything you did for hours.
MAX_DIRTY: Final[float] = 600.0
#: An unused circuit is given back rather than left occupying volunteers.
MAX_IDLE: Final[float] = 600.0
#: Forward secrecy inside a live circuit. §5 called for a rekey; it is
#: implemented as **retirement and rebuild** instead — see `should_retire`.
MAX_BYTES: Final[int] = 1024**3
MAX_AGE: Final[float] = 3600.0

_EXTEND_HEADER = struct.Struct("!32s32sH")  # ed25519 pub, x25519 pub, endpoint length

log = logging.getLogger("nullcell.onion")


class CircuitError(Exception):
    """Construction failed, or the circuit was torn down."""


@dataclass(slots=True)
class Hop:
    """A relay we intend to route through, or already do."""

    pub: bytes  # Ed25519 identity — also the address (§3)
    x25519: bytes  # static key for the Noise NK handshake
    endpoints: list[str] = field(default_factory=list)
    rtt_ms: float | None = None

    @property
    def nid(self) -> str:
        return ids.encode_nid(self.pub)

    @property
    def short(self) -> str:
        return ids.short(self.nid)


class Circuit:
    """One circuit, from the client's end."""

    def __init__(self, link: FrameProtocol, *, circ_id: int | None = None) -> None:
        self.link = link
        self.circ_id = new_circ_id(link_initiator=link.is_client) if circ_id is None else circ_id
        self.path = OnionPath()
        self.hops: list[Hop] = []
        self.closed = False
        self.flow = FlowControl()
        self.created_at = time.monotonic()
        self.first_use_at: float | None = None
        self.last_use_at = self.created_at
        self.bytes_sent = 0
        self.frames_sent = 0
        self.frames_received = 0
        self.streams: set[int] = set()
        self._pending: dict[int, asyncio.Future[bytes]] = {}
        self._handshakes: list[Initiator] = []
        self.on_data: object = None  # Callable[[int, int, bytes], None]
        link.route(self.circ_id, self._on_frame)

    # ------------------------------------------------------------------ status
    def __len__(self) -> int:
        return len(self.path)

    @property
    def ready(self) -> bool:
        return not self.closed and len(self.path) > 0

    @property
    def age(self) -> float:
        return time.monotonic() - self.created_at

    @property
    def dirty_for(self) -> float:
        """Seconds since this circuit was first used. 0 if still clean."""
        return 0.0 if self.first_use_at is None else time.monotonic() - self.first_use_at

    @property
    def accepts_new_streams(self) -> bool:
        """§5: no new streams on a circuit that has been in use for 10 minutes."""
        return self.ready and self.dirty_for < MAX_DIRTY

    @property
    def should_retire(self) -> tuple[bool, str]:
        """Whether to tear this circuit down, and why.

        §5 asked for a **rekey** of the layer keys every 1 GB or hour. This
        implements it as **retirement and rebuild** instead: an in-place rekey
        needs a protocol message that §5 does not define, and a fresh circuit
        gives the same forward secrecy with none of that machinery — it is also
        what Tor does. Said here rather than left as a silent divergence.
        """
        if self.closed:
            return True, "already closed"
        if self.bytes_sent >= MAX_BYTES:
            return True, f"carried {self.bytes_sent / 1024**3:.1f} GB"
        if self.age >= MAX_AGE:
            return True, f"{self.age / 60:.0f} min old"
        if self.first_use_at is None and self.age >= MAX_IDLE:
            return True, "never used"
        if self.dirty_for >= MAX_DIRTY and not self.streams:
            return True, "idle since its last stream"
        return False, ""

    def describe(self) -> str:
        """``m4x…kz > p9z…aa > q2w…bb`` — what the HUD shows (§9)."""
        return " > ".join(hop.short for hop in self.hops)

    # ------------------------------------------------------------ construction
    async def build(self, hops: list[Hop]) -> Circuit:
        """Create the first hop and extend through the rest."""
        if not hops:
            raise CircuitError("a circuit needs at least one hop")
        await self._create(hops[0])
        for hop in hops[1:]:
            await self._extend(hop)
        log.info("circuit %08x ready: %s", self.circ_id, self.describe())
        return self

    async def _create(self, hop: Hop) -> None:
        initiator = Initiator(hop.x25519)
        waiter = self._expect(Cmd.CREATED)
        self.link.send_frame(self.circ_id, Cmd.CREATE, initiator.create())
        try:
            created = await asyncio.wait_for(waiter, BUILD_TIMEOUT)
        except TimeoutError as exc:
            raise CircuitError(f"{hop.short} did not answer CREATE") from exc

        try:
            keys = initiator.created(created[:HANDSHAKE_LEN])
        except HandshakeError as exc:
            raise CircuitError(f"{hop.short} failed the handshake: {exc}") from exc

        self.path.add(keys)
        self.hops.append(hop)
        self._handshakes.append(initiator)

    async def _extend(self, hop: Hop) -> None:
        """Ask the last hop to extend the circuit to *hop*."""
        initiator = Initiator(hop.x25519)
        endpoint = (hop.endpoints[0] if hop.endpoints else "").encode()
        data = (
            _EXTEND_HEADER.pack(hop.pub, hop.x25519, len(endpoint)) + endpoint + initiator.create()
        )

        waiter = self._expect(RelayCmd.EXTENDED)
        self.send_relay(RelayCmd.EXTEND, data)
        try:
            extended = await asyncio.wait_for(waiter, BUILD_TIMEOUT)
        except TimeoutError as exc:
            raise CircuitError(f"extending to {hop.short} timed out") from exc

        if len(extended) < HANDSHAKE_LEN:
            raise CircuitError(f"{hop.short} returned a truncated handshake")
        try:
            keys = initiator.created(extended[:HANDSHAKE_LEN])
        except HandshakeError as exc:
            raise CircuitError(f"{hop.short} failed the handshake: {exc}") from exc

        self.path.add(keys)
        self.hops.append(hop)
        self._handshakes.append(initiator)

    # --------------------------------------------------------------- messaging
    def send_relay(
        self,
        rcmd: RelayCmd | int,
        data: bytes = b"",
        *,
        stream_id: int = 0,
        hop_index: int | None = None,
    ) -> None:
        """Send a relay message to one hop, wrapped for the whole path."""
        if self.closed:
            raise CircuitError("circuit is closed")
        payload = pack_relay_payload(rcmd, data, stream_id=stream_id)
        self.link.send_frame(self.circ_id, Cmd.RELAY, self.path.wrap(payload, hop_index))
        self.frames_sent += 1

    async def send_data(self, data: bytes, *, stream_id: int) -> None:
        """Send application data, waiting for credit if the window is shut (§5).

        Only DATA is metered. Control messages go through
        :meth:`send_relay` unmetered, or a circuit with a closed window could
        never be told to shut down.
        """
        if not stream_id:
            raise CircuitError("application data needs a non-zero stream id")
        if self.first_use_at is None:
            self.first_use_at = time.monotonic()
        if not self.accepts_new_streams and stream_id not in self.streams:
            raise CircuitError(
                f"circuit has been in use for {self.dirty_for / 60:.0f} min "
                "and takes no new streams (§5)"
            )
        await self.flow.acquire(stream_id)
        self.streams.add(stream_id)
        self.send_relay(RelayCmd.DATA, data, stream_id=stream_id)
        self.bytes_sent += len(data)
        self.last_use_at = time.monotonic()

    def _on_sendme(self, stream_id: int) -> None:
        """A hop returned credit."""
        try:
            if stream_id:
                self.flow.stream_send(stream_id).credit()
            else:
                self.flow.circuit_send.credit()
        except FlowError as exc:
            self._tear_down(f"bad SENDME: {exc}")

    def _on_delivered(self, stream_id: int) -> None:
        """Return credit for data we received."""
        circuit_due, stream_due = self.flow.on_delivered(stream_id)
        if circuit_due:
            self.send_relay(RelayCmd.SENDME, b"")
        if stream_due:
            self.send_relay(RelayCmd.SENDME, b"", stream_id=stream_id)

    def _expect(self, want: int) -> asyncio.Future[bytes]:
        future: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
        self._pending[int(want)] = future
        return future

    # -------------------------------------------------------------- receiving
    def _on_frame(self, circ_id: int, cmd: int, payload: memoryview) -> None:
        if self.closed:
            return
        if cmd == Cmd.DESTROY:
            self._tear_down("the far end sent DESTROY")
            return
        if cmd == Cmd.CREATED:
            self._resolve(int(Cmd.CREATED), bytes(payload))
            return
        if cmd != Cmd.RELAY:
            return

        try:
            _, plain = self.path.unwrap(bytes(payload))
        except LayerError as exc:
            # §5: a frame no hop claims has been tampered with or corrupted, and
            # carrying on would be exactly what a tagging attack needs.
            self._tear_down(str(exc))
            return

        self.frames_received += 1
        header = parse_relay_payload(plain)
        if self._resolve(header.rcmd, header.payload):
            return
        if header.rcmd == RelayCmd.SENDME:
            self._on_sendme(header.stream_id)
            return
        if header.rcmd == RelayCmd.DATA:
            self._on_delivered(header.stream_id)
        if header.rcmd == RelayCmd.END or header.rcmd == RelayCmd.TRUNCATED:
            self._tear_down(f"hop reported {RelayCmd(header.rcmd).name}")
            return
        if callable(self.on_data):
            self.on_data(header.stream_id, header.rcmd, header.payload)

    def _resolve(self, key: int, value: bytes) -> bool:
        future = self._pending.pop(int(key), None)
        if future is None or future.done():
            return False
        future.set_result(value)
        return True

    # ---------------------------------------------------------------- teardown
    def _tear_down(self, reason: str) -> None:
        if self.closed:
            return
        self.closed = True
        log.info("circuit %08x down: %s", self.circ_id, reason)
        for future in self._pending.values():
            if not future.done():
                future.set_exception(CircuitError(reason))
        self._pending.clear()
        self.link.unroute(self.circ_id)

    def destroy(self) -> None:
        """Tear the circuit down and tell the guard."""
        if self.closed:
            return
        with contextlib.suppress(Exception):  # the link may already be gone
            self.link.send_frame(self.circ_id, Cmd.DESTROY, b"")
        self._tear_down("closed locally")


def unpack_extend(data: bytes) -> tuple[Hop, bytes]:
    """Parse an ``EXTEND`` payload into ``(next_hop, handshake)``.

    Untrusted input from a client, so every length is checked before use.
    """
    if len(data) < _EXTEND_HEADER.size:
        raise CircuitError("EXTEND payload is too short")
    pub, x25519, endpoint_len = _EXTEND_HEADER.unpack_from(data, 0)
    offset = _EXTEND_HEADER.size
    if endpoint_len > 64 or len(data) < offset + endpoint_len + HANDSHAKE_LEN:
        raise CircuitError("EXTEND payload is malformed")
    endpoint = data[offset : offset + endpoint_len].decode(errors="replace")
    handshake = data[offset + endpoint_len : offset + endpoint_len + HANDSHAKE_LEN]
    return Hop(pub=pub, x25519=x25519, endpoints=[endpoint] if endpoint else []), handshake


def frame_create(circ_id: int, handshake: bytes) -> bytes:
    """A raw ``CREATE`` frame, for a relay extending on a client's behalf."""
    return pack(circ_id, Cmd.CREATE, handshake)
