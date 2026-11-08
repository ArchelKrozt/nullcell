# Security policy

## Reporting a vulnerability

Do **not** open a public issue for a vulnerability that affects user anonymity,
key material, or relay operators.

Email the maintainers with "nullcell security" in the subject. You get an
acknowledgement within 72 hours and a first assessment within 7 days. If a fix
needs coordination with relay operators, we agree a disclosure date with you
before publishing.

## What is in scope

* Anything that deanonymises a client or a service beyond the documented threat
  model (`NULLCELL-ARQUITECTURA.md` §10).
* Key recovery, address spoofing, or accepting a peer whose key does not match
  its address (§3, §4).
* Remote crashes or resource exhaustion in a relay.
* Attacks on the local UI from an ordinary web page (§9).

## What is out of scope

These are known and documented, not bugs:

* **End-to-end traffic correlation.** Not solved here, not solved in Tor.
* **A global adversary** observing the whole network.
* **Sybil and eclipse attacks on the DHT.** Only partially mitigated (§7); the
  decentralised relay selection problem is open research and the README says so.
* **Relay enumeration.** Relay descriptors are public by necessity.
* **Enumeration of public portals.** Being findable means being enumerable; use
  a private service (§6) if that matters.
* **Post-quantum key recovery** from a published address (§3).

## Supported versions

Pre-1.0: only the current `main` branch receives fixes.
