"""Path selection — §7, guards and diversity."""

import os
import random
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from nullcell.onion.handshake_nk import x25519_public_bytes
from nullcell.onion.path_select import (
    GUARD_COUNT,
    GUARD_LIFETIME,
    MIN_OBSERVED_AGE,
    GuardSet,
    PathBuilder,
    PathError,
    RelayCandidate,
    RelayDirectory,
    endpoint_prefix,
    select_path,
)

OLD = time.time() - MIN_OBSERVED_AGE - 60


def directory_of(*relays) -> RelayDirectory:
    """Un directorio con esos relevos, saltándose `ingest` (que exige un
    registro firmado): aquí se prueba el mensaje, no la verificación."""
    directory = RelayDirectory()
    for relay in relays:
        directory._relays[relay.pub] = relay  # noqa: SLF001
    return directory


def candidate(host: str, *, first_seen: float = OLD, pow_bits: int = 9) -> RelayCandidate:
    """A usable relay at *host*. ``host`` may be an IP or a name."""
    return RelayCandidate(
        pub=os.urandom(32),
        x25519=x25519_public_bytes(X25519PrivateKey.generate()),
        endpoints=[f"{host}:4433"],
        first_seen=first_seen,
        pow_bits=pow_bits,
    )


def diverse(count: int, **kwargs) -> list[RelayCandidate]:
    """Candidates each in its own /16."""
    return [candidate(f"10.{i}.0.1", **kwargs) for i in range(count)]


# ------------------------------------------------------------------- prefixes
def test_prefix_is_16_for_ipv4_and_48_for_ipv6():
    assert endpoint_prefix("1.2.3.4:443") == endpoint_prefix("1.2.9.9:443")
    assert endpoint_prefix("1.2.3.4:443") != endpoint_prefix("1.3.3.4:443")
    assert endpoint_prefix("[2001:db8::1]:443") == endpoint_prefix("[2001:db8:0:1::9]:443")
    assert endpoint_prefix("[2001:db9::1]:443") != endpoint_prefix("[2001:db8::1]:443")


def test_a_hostname_is_keyed_by_its_parent_domain():
    """§7 will not resolve a relay it is merely weighing up, so a hostname is
    keyed by its domain instead of being excluded. Excluding it meant a relay
    announced by DNS name — the normal way to deploy on a VPS — could not be
    part of any circuit at all."""
    assert endpoint_prefix("relay.example.com:443") == "dns:example.com"
    assert endpoint_prefix("otro.example.com:443") == "dns:example.com"
    assert endpoint_prefix("relay.example.net:443") == "dns:example.net"


def test_a_bare_name_still_has_no_key():
    """It says nothing about who runs it."""
    assert endpoint_prefix("localhost:4433") is None
    assert endpoint_prefix("nonsense") is None


def test_two_relays_under_one_domain_count_as_one_operator():
    """Conservative on purpose: it refuses some paths that would have been
    fine, and §7 says refusing beats fudging."""
    assert endpoint_prefix("a.midominio.com:1") == endpoint_prefix("b.midominio.com:2")


def test_an_observed_address_wins_over_the_announced_one():
    """A relay announcing a hostname might resolve to an address already used
    by another hop, and only an observation can tell us."""
    relay = candidate("relay.example.com")
    assert relay.prefixes == {"dns:example.com"}
    relay.note_observed("203.0.113.9:4433")
    assert relay.prefixes == {"203.0.0.0/16"}


def test_an_observation_unmasks_two_names_as_one_machine(tmp_path):
    """The gap a hostname key cannot close on its own."""
    first, second, third = (
        candidate("a.uno.com"),
        candidate("b.dos.com"),
        candidate("c.tres.com"),
    )
    guards = GuardSet(tmp_path / "guards.json")
    assert len(select_path([first, second, third], guards)) == 3

    # Now we learn that two of them answer on the same machine.
    first.note_observed("203.0.113.9:4433")
    second.note_observed("203.0.113.9:4433")
    with pytest.raises(PathError):
        select_path([first, second, third], GuardSet(tmp_path / "g2.json"))


# ---------------------------------------------------------------------- guards
def test_guards_are_pinned_and_reused(tmp_path):
    pool = diverse(10)
    guards = GuardSet(tmp_path / "guards.json")
    first = set(guards.ensure(pool))
    assert len(first) == GUARD_COUNT
    assert set(guards.ensure(pool)) == first, "guards must not churn"


def test_guards_survive_a_restart(tmp_path):
    pool = diverse(10)
    path = tmp_path / "guards.json"
    chosen = set(GuardSet(path).ensure(pool))
    assert set(GuardSet(path).pubs) == chosen


def test_guards_are_in_distinct_prefixes(tmp_path):
    """Two guards behind one operator is one guard with extra steps."""
    pool = [candidate("10.1.0.1"), candidate("10.1.0.2"), candidate("10.1.0.3")]
    guards = GuardSet(tmp_path / "guards.json")
    assert len(guards.ensure(pool)) == 1


def test_young_relays_are_not_eligible_as_guards(tmp_path):
    fresh = diverse(5, first_seen=time.time())
    guards = GuardSet(tmp_path / "guards.json")
    assert guards.ensure(fresh) == []


def test_expired_guards_are_retired(tmp_path):
    pool = diverse(10)
    guards = GuardSet(tmp_path / "guards.json")
    guards.ensure(pool)
    assert len(guards) == GUARD_COUNT
    later = time.time() + GUARD_LIFETIME + 1
    guards.ensure(pool, now=later)
    # Retired and replaced in one pass, so the set stays full.
    assert len(guards) == GUARD_COUNT


def test_corrupt_guard_file_is_survivable(tmp_path):
    path = tmp_path / "guards.json"
    path.write_text("no soy json")
    assert GuardSet(path).pubs == []


# ------------------------------------------------------------------ selection
def test_selects_three_hops_starting_at_a_guard(tmp_path):
    pool = diverse(12)
    guards = GuardSet(tmp_path / "guards.json")
    hops = select_path(pool, guards, rng=random.Random(1))
    assert len(hops) == 3
    assert guards.is_guard(hops[0].pub)


def test_all_three_hops_are_in_distinct_prefixes(tmp_path):
    pool = diverse(12)
    guards = GuardSet(tmp_path / "guards.json")
    for seed in range(20):
        hops = select_path(pool, guards, rng=random.Random(seed))
        prefixes = [endpoint_prefix(h.endpoints[0]) for h in hops]
        assert len(set(prefixes)) == 3, f"seed {seed} reused a prefix"


def test_no_hop_appears_twice(tmp_path):
    pool = diverse(12)
    guards = GuardSet(tmp_path / "guards.json")
    for seed in range(20):
        hops = select_path(pool, guards, rng=random.Random(seed))
        assert len({h.pub for h in hops}) == 3


def test_a_concentrated_network_is_refused_not_fudged(tmp_path):
    """§7: better no circuit than one the user believes is diverse."""
    pool = [candidate("10.1.0.1"), candidate("10.1.0.2"), candidate("10.1.0.3")]
    guards = GuardSet(tmp_path / "guards.json")
    with pytest.raises(PathError, match="distinct prefixes"):
        select_path(pool, guards)


def test_too_few_relays_is_refused(tmp_path):
    guards = GuardSet(tmp_path / "guards.json")
    with pytest.raises(PathError, match="2 of 2 relays are usable"):
        select_path(diverse(2), guards)


def test_a_brand_new_node_must_wait(tmp_path):
    guards = GuardSet(tmp_path / "guards.json")
    with pytest.raises(PathError, match="too recently observed"):
        select_path(diverse(10, first_seen=time.time()), guards)


def test_relays_below_the_work_floor_are_excluded(tmp_path):
    guards = GuardSet(tmp_path / "guards.json")
    with pytest.raises(PathError):
        select_path(diverse(10, pow_bits=2), guards, min_pow_bits=9)


def test_candidate_from_a_relay_descriptor():
    import asyncio

    from nullcell.core.identity import Identity
    from nullcell.dht import pow as P
    from nullcell.dht import records as R

    original = dict(P.DIFFICULTY)
    P.DIFFICULTY.update(dict.fromkeys(P.DIFFICULTY, 3))
    try:
        identity = Identity.generate()
        body = R.RelayBody(
            endpoints=["10.0.0.1:4433"], x25519=identity.x25519_public, bandwidth=5_000_000
        ).as_dict()
        record = asyncio.run(R.build(identity, R.RecordType.RELAY, body))
        found = RelayCandidate.from_record(record, pow_bits=9)
        assert found is not None
        assert found.pub == identity.public_key
        assert found.bandwidth == 5_000_000
        assert found.prefixes == {"10.0.0.0/16"}

        name = asyncio.run(R.build(identity, R.RecordType.NAME, {"name": "x"}))
        assert RelayCandidate.from_record(name) is None  # wrong record type
    finally:
        P.DIFFICULTY.clear()
        P.DIFFICULTY.update(original)
        P.shutdown_pool()


# --------------------- M10: esperar y actuar son reacciones opuestas (§7)
def test_the_message_says_waiting_is_useless_when_operators_are_missing(tmp_path):
    """El mensaje culpaba siempre al suelo de antigüedad, también cuando lo
    que faltaban eran operadores: le decía a alguien que esperara en el único
    caso en que esperar no va a cambiar nada jamás."""
    paths = PathBuilder(directory_of(candidate("62.171.160.130")), GuardSet(tmp_path / "g.json"))
    message = paths.why_not_ready()
    assert "esperar no basta" in message
    assert "operador" in message
    assert "antigüedad" not in message


def test_the_message_says_to_wait_once_the_operators_are_there(tmp_path):
    relays = [candidate(f"62.{171 + i}.160.130", first_seen=time.time()) for i in range(3)]
    paths = PathBuilder(directory_of(*relays), GuardSet(tmp_path / "g.json"))
    message = paths.why_not_ready()
    assert "sólo hay que esperar" in message
    assert "esperar no basta" not in message


def test_no_relays_at_all_asks_for_a_seed(tmp_path):
    paths = PathBuilder(RelayDirectory(), GuardSet(tmp_path / "g.json"))
    assert "semilla" in paths.why_not_ready()


def test_a_ready_directory_has_nothing_to_say(tmp_path):
    relays = [candidate(f"62.{171 + i}.160.130", first_seen=OLD) for i in range(3)]
    paths = PathBuilder(directory_of(*relays), GuardSet(tmp_path / "g.json"))
    assert paths.why_not_ready() == ""


# ------------------- M10: una red nueva no tiene 3 operadores todavía (§7)
def test_a_shorter_circuit_can_be_built_when_asked_for(tmp_path):
    """La crítica que lo motivó, y es justa: exigir 3 operadores *siempre*
    significa que una red nueva no puede existir nunca, porque nada funciona
    hasta que ya hay gente y no hay gente hasta que algo funciona."""
    only_one = directory_of(candidate("62.171.160.130", first_seen=OLD))

    strict = PathBuilder(only_one, GuardSet(tmp_path / "a.json"))
    assert not strict.ready, "con 3 saltos y un relevo no hay ruta, y eso es correcto"

    relaxed = PathBuilder(only_one, GuardSet(tmp_path / "b.json"), hops=1)
    assert relaxed.ready
    assert len(relaxed.build()) == 1


def test_fewer_hops_is_reported_as_not_anonymous(tmp_path):
    directory = directory_of(candidate("62.171.160.130", first_seen=OLD))
    assert PathBuilder(directory, GuardSet(tmp_path / "a.json")).anonymous is True
    for hops in (1, 2):
        shortened = PathBuilder(directory, GuardSet(tmp_path / f"{hops}.json"), hops=hops)
        assert shortened.anonymous is False


def test_the_warning_names_what_is_actually_lost():
    """«Menos seguro» no dice nada. Lo que hay que decir es quién ve qué."""
    from nullcell.onion.path_select import anonymity_warning

    assert anonymity_warning(3) == ""
    uno, dos = anonymity_warning(1), anonymity_warning(2)
    assert "ve tu dirección IP y a qué portal vas" in uno
    assert "el primero sabe quién eres y el segundo adónde vas" in dos
    for texto in (uno, dos):
        assert "SIN ANONIMATO" in texto
        assert "El contenido sigue cifrado" in texto, "no exagerar tampoco"


def test_the_hop_count_cannot_be_zero(tmp_path):
    """Cero saltos no es un circuito, es una conexión directa con otro nombre."""
    assert PathBuilder(RelayDirectory(), GuardSet(tmp_path / "g.json"), hops=0).hops == 1
