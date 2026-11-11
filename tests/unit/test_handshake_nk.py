"""Circuit handshake — §5.

The regression these tests exist for: every other test builds a relay with a
``cryptography`` key generated on the spot, so nothing exercised the handshake
with the key a node actually has. :mod:`nullcell.core.identity` derives that
one with PyNaCl, and the two classes share a name and nothing else — a real
relay refused every CREATE while the suite stayed green.
"""

import os

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from nullcell.core.identity import Identity
from nullcell.onion.handshake_nk import (
    HANDSHAKE_LEN,
    Initiator,
    Responder,
    x25519_public_bytes,
)


def _complete(static_private, public_bytes):
    initiator = Initiator(public_bytes)
    create = initiator.create()
    assert len(create) == HANDSHAKE_LEN
    created, responder_keys = Responder(static_private).respond(create)
    initiator_keys = initiator.created(created)
    return initiator_keys, responder_keys


def test_handshake_with_a_real_node_identity():
    """What a relay actually runs with: the key Identity derives (PyNaCl)."""
    identity = Identity(os.urandom(32))
    initiator_keys, responder_keys = _complete(identity.x25519_private, identity.x25519_public)
    assert initiator_keys.forward == responder_keys.forward
    assert initiator_keys.backward == responder_keys.backward


def test_handshake_with_a_cryptography_key():
    """And the key the rest of the tests generate, which must keep working."""
    static = X25519PrivateKey.generate()
    initiator_keys, responder_keys = _complete(static, x25519_public_bytes(static))
    assert initiator_keys.forward == responder_keys.forward
    assert initiator_keys.backward == responder_keys.backward


def test_public_bytes_agrees_with_the_identity_it_came_from():
    """The descriptor publishes ``x25519_public``; ``own_hop`` derives it from
    the private key. If those two disagree, nobody can reach the relay."""
    identity = Identity(os.urandom(32))
    assert x25519_public_bytes(identity.x25519_private) == identity.x25519_public
