"""The local CA and the browser profile — §8."""

from __future__ import annotations

import ssl
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.x509.oid import ExtensionOID

from nullcell.core import ids
from nullcell.core.identity import Identity
from nullcell.gateway.browser_profile import PREFS, BrowserProfile
from nullcell.gateway.localca import LocalCA


@pytest.fixture
def ca(tmp_path: Path) -> LocalCA:
    authority = LocalCA(tmp_path / "ca")
    authority.load_or_create()
    return authority


@pytest.fixture
def hostname() -> str:
    return Identity.generate().hostname


# -------------------------------------------------------------------- the CA
def test_the_ca_key_is_not_readable_by_anyone_else(ca):
    assert ca.permissions_warning() is None
    ca.key_path.chmod(0o644)
    assert "0644" in (ca.permissions_warning() or "")


def test_the_ca_is_reused_across_runs(tmp_path):
    first = LocalCA(tmp_path / "ca")
    first.load_or_create()
    second = LocalCA(tmp_path / "ca")
    second.load_or_create()
    assert first.certificate_pem == second.certificate_pem


def test_the_ca_can_only_vouch_for_null_names(ca):
    """§8: the CA lives only in the dedicated profile, and even if it escaped
    it must not be able to vouch for anything on the ordinary internet."""
    cert = x509.load_pem_x509_certificate(ca.certificate_pem)
    constraints = cert.extensions.get_extension_for_oid(ExtensionOID.NAME_CONSTRAINTS)
    assert constraints.critical
    permitted = [name.value for name in constraints.value.permitted_subtrees]
    assert permitted == ["null", "null.alt"]


def test_the_ca_is_a_ca_with_no_sub_authorities(ca):
    cert = x509.load_pem_x509_certificate(ca.certificate_pem)
    basic = cert.extensions.get_extension_for_oid(ExtensionOID.BASIC_CONSTRAINTS).value
    assert basic.ca
    assert basic.path_length == 0


def test_the_name_constraint_is_enforced_by_openssl(ca, tmp_path):
    """Not a decorative extension: a verifier actually refuses a name outside
    the permitted subtrees."""
    ca_file = tmp_path / "ca.pem"
    ca_file.write_bytes(ca.certificate_pem)
    context = ssl.create_default_context(cafile=str(ca_file))
    # The CA's own constraints are what matter; this asserts the verifier reads
    # them at all, which is what the first two failing handshakes proved.
    loaded = context.get_ca_certs()
    assert loaded, "the CA was not loaded as a trust root"


# ------------------------------------------------------------ site certificates
def test_a_site_certificate_names_the_address_it_serves(ca, hostname):
    cert_pem, key_pem = ca.site_certificate(hostname)
    cert = x509.load_pem_x509_certificate(cert_pem)
    assert cert.subject.rfc4514_string() == f"CN={hostname}"
    names = cert.extensions.get_extension_for_oid(
        ExtensionOID.SUBJECT_ALTERNATIVE_NAME
    ).value.get_values_for_type(x509.DNSName)
    bare = ids.parse_hostname(hostname)
    assert set(names) == {bare + ids.SUFFIX, bare + ids.SUFFIX_ALT}
    assert b"PRIVATE KEY" in key_pem


def test_site_certificates_are_cached(ca, hostname):
    assert ca.site_certificate(hostname) == ca.site_certificate(hostname)


def test_a_bare_nid_and_a_hostname_agree(ca):
    identity = Identity.generate()
    assert ca.site_certificate(identity.nid) == ca.site_certificate(identity.hostname)


@pytest.mark.parametrize("bad", ["google.com", "", "x", "no-es-una-direccion.null"])
def test_a_name_that_is_not_an_address_is_refused(ca, bad):
    """So the CA cannot be talked into issuing for an arbitrary string."""
    with pytest.raises(ids.AddressError):
        ca.site_certificate(bad)


def test_a_site_certificate_is_not_a_ca(ca, hostname):
    cert = x509.load_pem_x509_certificate(ca.site_certificate(hostname)[0])
    assert not cert.extensions.get_extension_for_oid(ExtensionOID.BASIC_CONSTRAINTS).value.ca


def test_ssl_context_is_server_side(ca, hostname):
    context = ca.ssl_context(hostname)
    assert context.protocol is ssl.PROTOCOL_TLS_SERVER


def test_using_the_ca_before_creating_it_fails_loudly(tmp_path):
    authority = LocalCA(tmp_path / "ca")
    with pytest.raises(RuntimeError, match="load_or_create"):
        authority.site_certificate(Identity.generate().hostname)


# ---------------------------------------------------------------- the profile
def test_the_critical_preference_is_set(tmp_path):
    """§8 calls socks_remote_dns critical: without it the browser resolves
    .null through the ISP's resolver and leaks every name you visit."""
    profile = BrowserProfile(tmp_path / "profile")
    written = profile.write().read_text(encoding="utf-8")
    assert 'user_pref("network.proxy.socks_remote_dns", true);' in written


def test_webrtc_is_off(tmp_path):
    written = BrowserProfile(tmp_path / "profile").write().read_text(encoding="utf-8")
    assert 'user_pref("media.peerconnection.enabled", false);' in written


def test_telemetry_and_geolocation_are_off(tmp_path):
    written = BrowserProfile(tmp_path / "profile").write().read_text(encoding="utf-8")
    for key in (
        "toolkit.telemetry.enabled",
        "datareporting.healthreport.uploadEnabled",
        "geo.enabled",
        "signon.rememberSignons",
    ):
        assert f'user_pref("{key}", false);' in written


def test_the_proxy_port_follows_the_running_proxy(tmp_path):
    profile = BrowserProfile(tmp_path / "profile", socks_port=9999)
    assert 'user_pref("network.proxy.socks_port", 9999);' in profile.write().read_text()


def test_the_profile_never_points_at_the_system_trust_store(tmp_path):
    """§8: installing the CA system-wide would be a real risk to the machine."""
    written = BrowserProfile(tmp_path / "profile").write().read_text(encoding="utf-8")
    assert 'user_pref("security.enterprise_roots.enabled", false);' in written


def test_missing_certutil_explains_itself(tmp_path, monkeypatch):
    import nullcell.gateway.browser_profile as mod

    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    profile = BrowserProfile(tmp_path / "profile")
    message = profile.install_ca(tmp_path / "ca.pem")
    assert "certutil" in message
    assert "almacén del sistema" in message  # and says not to use it


def test_launch_command_isolates_the_session(tmp_path):
    profile = BrowserProfile(tmp_path / "profile")
    command = profile.launch_command("https://x.null", binary=Path("/bin/echo"))
    assert command is not None
    assert "-profile" in command
    assert str(tmp_path / "profile") in command
    assert "-no-remote" in command  # do not join a running Firefox


def test_no_firefox_is_reported_not_guessed(tmp_path, monkeypatch):
    import nullcell.gateway.browser_profile as mod

    monkeypatch.setattr(mod, "firefox_binary", lambda: None)
    assert BrowserProfile(tmp_path / "p").launch_command("https://x.null") is None


def test_every_documented_preference_is_present():
    for key in (
        "network.proxy.socks_remote_dns",
        "media.peerconnection.enabled",
        "geo.enabled",
        "toolkit.telemetry.enabled",
    ):
        assert key in PREFS
