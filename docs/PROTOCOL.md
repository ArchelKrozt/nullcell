# Nullcell wire protocol

Normative reference for what is implemented. Design rationale lives in
`NULLCELL-ARQUITECTURA.md`; section markers below point at it.

Current protocol version: **1**.

## Addresses (§3)

```
NID = base32_nopad( pub_ed25519(32) || checksum(2) || version(1) )   -> 56 chars
checksum = sha256("nullcell-addr" || pub || version)[:2]
version  = bits 0..6  address format version (1)
           bit 7      class: 0 = public portal, 1 = private service
```

The address carries the key itself, so a peer is verified locally without
fetching its descriptor. The DHT routing key is a different value, `sha256(pub)`.

Hostnames: `<nid>.null` canonical, `<nid>.null.alt` also accepted (RFC 9476).

## Link (§4)

QUIC over UDP, ALPN `nullcell/1`. The listening side presents a self-signed
certificate whose subject public key **is** its Ed25519 identity key and whose
SAN holds `<nid>.null`. The connecting side ignores the world PKI entirely and
compares that key against the one in the address it asked for; a mismatch closes
the link.

**Authentication is one-sided.** A client never presents a certificate. Asking
for one would deanonymise it at the link layer.

## Frames (§5)

Every frame is exactly 512 bytes:

```
[circ_id u32][cmd u8][payload 507]
```

`circ_id` is local to a link. The side that *initiated* the link allocates ids
with bit 31 clear; the side that accepted it sets bit 31. `circ_id` 0 is
reserved for link-level frames.

| `cmd` | Name | Meaning |
|---|---|---|
| 1 | `VERSIONS` | First frame of every link; payload is a list of version bytes |
| 2 | `CREATE` | Begin a circuit handshake (M3) |
| 3 | `CREATED` | Handshake reply (M3) |
| 4 | `RELAY` | Carries the subheader below |
| 5 | `DESTROY` | Tear down a circuit |
| 6 | `PADDING` | Traffic-analysis padding |
| 7 | `PING` | Link liveness and RTT; body is echoed |
| 8 | `PONG` | Reply to `PING`, first 8 bytes of the body echoed |

`EXTEND`/`EXTENDED` are **relay** commands, not link commands: they must travel
encrypted end-to-end to the hop doing the extending.

### `RELAY` payload

```
[rcmd u8][recognized u16][stream_id u16][digest u32][length u16][data 496]
```

* `recognized == 0` means the frame is addressed to this hop.
* `stream_id` multiplexes application streams inside one circuit.
* `digest` is 4 bytes of a running digest of the stream with one hop, which
  detects the tagging attack. **Computing it lands in M3**; the field is packed
  and parsed today but not yet verified.

| `rcmd` | Name |
|---|---|
| 1 | `DATA` |
| 2 | `BEGIN` |
| 3 | `CONNECTED` |
| 4 | `END` |
| 5 | `SENDME` |
| 6 | `EXTEND` |
| 7 | `EXTENDED` |
| 8 | `TRUNCATED` |
| 9 | `RESOLVE` |

## Version negotiation (§5)

The side that initiated the link opens a stream and sends `VERSIONS` first; the
other side answers on that same stream. Both pick the highest version in common,
or the link closes with "no common protocol version".

## Not yet implemented

Circuits (M3), rendezvous (M4), the DHT (M2), SENDME flow control, padding
machines, circuit lifetime and rekey. The frame format reserves everything they
need.
