"""Invitaciones: una sola cosa que copiar — §1, §7.

Entrar exige saber de alguien, y la línea de semilla cruda —con su arroba y sus
dos puntos— es justo el tipo de cosa que se corta mal al pegarla.
"""

from __future__ import annotations

import pytest

from nullcell.core import ids, invite
from nullcell.core.identity import Identity


def seed(endpoint: str = "203.0.113.9:4433") -> str:
    return f"{Identity.generate().hostname.removesuffix(ids.SUFFIX)}@{endpoint}"


def test_it_round_trips():
    original = [seed(), seed("mivps.com:4433")]
    back = invite.parse(invite.build(original, portal="nullcell"))
    assert back.seeds == original
    assert back.portal == "nullcell"


def test_a_bare_seed_line_is_accepted_too():
    """Asking somebody to tell an invitation from a seed is asking them to
    understand a difference that does not concern them."""
    line = seed()
    assert invite.parse(line).seeds == [line]


def test_it_stays_readable_rather_than_opaque():
    """Whoever receives it can see which cells they are being told to connect
    to. The first contact is direct and reveals their address, so an opaque
    blob would be asking for blind trust in exactly the wrong place."""
    text = invite.build([seed()], portal="nullcell")
    assert text.startswith("nullcell://join?")
    assert "203.0.113.9:4433" in text
    assert "p=nullcell" in text


def test_the_portal_suffix_is_normalised():
    assert invite.parse(invite.build([seed()], portal="NULLCELL.null")).portal == "nullcell"


def test_an_invitation_without_a_portal_is_fine():
    assert invite.parse(invite.build([seed()])).portal is None


# ------------------------------------------------------------------ rechazos
@pytest.mark.parametrize(
    "text",
    ["", "   ", "no-es-nada", "@host:1", "alguien@", "alguien@sinpuerto"],
)
def test_nonsense_is_refused_with_a_reason(text):
    with pytest.raises(invite.InviteError):
        invite.parse(text)


def test_an_address_that_fails_its_checksum_is_refused():
    """A near-miss address is exactly what somebody would hand you on purpose."""
    good = seed()
    nid, _, endpoint = good.partition("@")
    other = "z" if nid[0] != "z" else "q"
    with pytest.raises(invite.InviteError):
        invite.parse(f"{other}{nid[1:]}@{endpoint}")


def test_building_without_seeds_is_refused():
    with pytest.raises(invite.InviteError, match="ninguna parte"):
        invite.build([])


def test_a_broken_seed_does_not_invalidate_the_others():
    """The invitation carries several precisely so that one failing does not
    cost the whole thing."""
    good = seed()
    text = f"nullcell://join?s=basura&s={good}"
    assert invite.parse(text).seeds == [good]


def test_an_invitation_with_only_broken_seeds_says_so():
    with pytest.raises(invite.InviteError, match="usable"):
        invite.parse("nullcell://join?s=basura")


def test_an_unknown_action_is_refused():
    with pytest.raises(invite.InviteError, match="join"):
        invite.parse(f"nullcell://borrartodo?s={seed()}")


def test_too_many_seeds_are_trimmed():
    """Más que un puñado no es una invitación, es una lista de arranque."""
    many = [seed(f"203.0.113.{i}:4433") for i in range(invite.MAX_SEEDS + 5)]
    assert len(invite.parse(invite.build(many)).seeds) == invite.MAX_SEEDS
