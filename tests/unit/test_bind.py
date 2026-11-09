"""Who may reach the plaintext side of the gateway — §8.

The gateway terminates the local TLS, so reaching it is reading it. These
tests are the boundary of that, stated as a list of addresses.
"""

from __future__ import annotations

import pytest

from nullcell.gateway.bind import BindRefused, check_bind, is_loopback


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "[::1]", " 127.0.0.1 "])
def test_loopback_is_the_default_case(host):
    assert check_bind(host) == host.strip().strip("[]")


@pytest.mark.parametrize(
    "host",
    [
        "10.8.0.1",  # WireGuard, the setup docs/VPS.md describes
        "192.168.1.20",
        "172.16.4.4",
        # CGNAT, which Tailscale uses. It is *not* `is_private`, so the
        # hand-written range list this check started as refused the very case
        # the flag exists for.
        "100.64.0.1",
        "fd00::1",  # ULA
        "fe80::1",  # link-local
    ],
)
def test_an_unroutable_address_is_allowed_because_a_vpn_lives_there(host):
    assert check_bind(host) == host


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "[::]"])
def test_the_wildcard_is_refused_not_warned_about(host):
    """A relay binds :: happily. A relay forwards frames it cannot read; this
    one reads them, so the same bind is a different decision."""
    with pytest.raises(BindRefused, match="open proxy"):
        check_bind(host)


@pytest.mark.parametrize("host", ["8.8.8.8", "2606:4700::1111", "185.199.108.153"])
def test_a_routable_address_is_refused(host):
    with pytest.raises(BindRefused, match="globally routable"):
        check_bind(host)


@pytest.mark.parametrize("host", ["203.0.113.9", "2001:db8::1", "192.0.2.1"])
def test_the_documentation_ranges_land_on_the_permissive_side(host):
    """And that is correct, not a hole: nothing routes them, so a bind there
    reaches nobody. Worth asserting because they are `is_private` — the reason
    this check tests reachability rather than a list of ranges."""
    assert check_bind(host) == host


@pytest.mark.parametrize("host", ["mivps.com", "localhost", "", "   "])
def test_a_name_is_refused_rather_than_resolved(host):
    """Resolving it would check a reading of the DNS that need not still hold
    when the bind happens — and `localhost` is the trap: it looks like the
    safe answer and is the one case where the safe answer has a spelling."""
    with pytest.raises(BindRefused):
        check_bind(host)


def test_the_error_says_what_to_do_instead():
    with pytest.raises(BindRefused) as refused:
        check_bind("0.0.0.0")
    assert "10.8.0.1" in str(refused.value)
    assert "docs/VPS.md" in str(refused.value)


def test_loopback_is_distinguishable_for_the_warning():
    assert is_loopback("127.0.0.1")
    assert is_loopback("::1")
    assert not is_loopback("10.8.0.1")
    assert not is_loopback("mivps.com")
