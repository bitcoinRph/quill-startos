"""URL parsing and the host allowlist: the security boundary of the bot."""

from __future__ import annotations

import pytest

from quill_server.bot.urls import AllowedHost, BotError, BotUrlError, parse_allowlist, parse_jitsi_url

ALLOW = parse_allowlist("meet.example.com, jitsi.lan:8443, 192.168.1.20, [::1]:8443,Mixed.Case.Org")


def ok(url: str):
    return parse_jitsi_url(url, ALLOW)


def rejected(url: str, code: str | None = None):
    with pytest.raises(BotError) as exc:
        parse_jitsi_url(url, ALLOW)
    if code:
        assert exc.value.code == code
    return exc.value


class TestAllowlistParsing:
    def test_forms(self):
        assert AllowedHost("meet.example.com", 443) in ALLOW
        assert AllowedHost("jitsi.lan", 8443) in ALLOW
        assert AllowedHost("192.168.1.20", 443) in ALLOW
        assert AllowedHost("::1", 8443) in ALLOW
        assert AllowedHost("mixed.case.org", 443) in ALLOW

    def test_empty_values(self):
        assert parse_allowlist("") == ()
        assert parse_allowlist(None) == ()
        assert parse_allowlist(" , ,") == ()

    @pytest.mark.parametrize(
        "bad",
        ["https://meet.example.com", "meet.example.com/room", "user@meet.example.com", "*.example.com",
         "meet.example.com:", "meet.example.com:abc", "meet.example.com:0", "meet.example.com:70000",
         "[::1", "-bad.example.com", "a b.example.com", "meet.example.com?x"],
    )
    def test_invalid_entries_are_dropped_not_widened(self, bad):
        assert parse_allowlist(bad) == ()

    def test_iterable_and_dedupe(self):
        assert parse_allowlist(["a.example", "A.EXAMPLE", "b.example"]) == (
            AllowedHost("a.example", 443),
            AllowedHost("b.example", 443),
        )

    def test_ipv6_normalised(self):
        assert parse_allowlist("[0:0:0:0:0:0:0:1]") == (AllowedHost("::1", 443),)


class TestAccepted:
    def test_basic(self):
        t = ok("https://meet.example.com/MyRoom")
        assert (t.hostname, t.port, t.room, t.jwt) == ("meet.example.com", 443, "MyRoom", None)
        assert t.base_url == "https://meet.example.com"
        assert t.netloc == "meet.example.com"

    def test_case_insensitive_host_and_trailing_slash(self):
        assert ok("HTTPS://Meet.Example.COM/room/").hostname == "meet.example.com"

    def test_explicit_default_port(self):
        assert ok("https://meet.example.com:443/room").port == 443

    def test_listed_port(self):
        t = ok("https://jitsi.lan:8443/standup")
        assert t.port == 8443 and t.netloc == "jitsi.lan:8443"

    def test_listed_ips(self):
        assert ok("https://192.168.1.20/room").hostname == "192.168.1.20"
        t = ok("https://[::1]:8443/room")
        assert t.hostname == "::1" and t.netloc == "[::1]:8443"
        assert ok("https://[0:0:0:0:0:0:0:1]:8443/room").hostname == "::1"

    def test_percent_encoded_room_and_unicode(self):
        assert ok("https://meet.example.com/Team%20Sync").room == "Team Sync"
        assert ok("https://meet.example.com/r%C3%A9union").room == "réunion"

    def test_jwt_extracted_and_other_query_ignored(self):
        t = ok("https://meet.example.com/room?jwt=abc.def.ghi&lang=en#config.startWithAudioMuted=true")
        assert t.jwt == "abc.def.ghi"
        assert ok("https://meet.example.com/room?jwt=").jwt is None

    def test_key_is_case_insensitive_room(self):
        assert ok("https://meet.example.com/Room").key == ok("https://meet.example.com/ROOM").key


class TestRejected:
    @pytest.mark.parametrize("url", ["http://meet.example.com/r", "ftp://meet.example.com/r", "wss://meet.example.com/r",
                                     "//meet.example.com/r", "meet.example.com/r", "javascript:alert(1)", "file:///etc/passwd"])
    def test_scheme(self, url):
        rejected(url)

    def test_host_not_listed(self):
        e = rejected("https://evil.example.org/room", "host_not_allowed")
        assert "evil.example.org" in e.message

    @pytest.mark.parametrize("url", [
        "https://example.com/r",                    # parent domain
        "https://sub.meet.example.com/r",           # subdomain
        "https://meet.example.com.evil.org/r",      # suffix trick
        "https://evilmeet.example.com/r",
        "https://meet.example.com./r",              # trailing dot is a different name
    ])
    def test_exact_match_only(self, url):
        rejected(url, "host_not_allowed")

    def test_userinfo(self):
        rejected("https://user:pw@meet.example.com/r", "invalid_url")
        rejected("https://meet.example.com@evil.example.org/r")
        rejected("https://evil.example.org@meet.example.com/r", "invalid_url")
        rejected("https://@meet.example.com/r", "invalid_url")

    def test_backslash_parser_differential(self):
        rejected("https://evil.example.org\\@meet.example.com/r", "invalid_url")
        rejected("https://meet.example.com\\.evil.org/r", "invalid_url")

    def test_port_not_listed(self):
        e = rejected("https://meet.example.com:8443/r", "host_not_allowed")
        assert "8443" in e.message
        rejected("https://meet.example.com:80/r", "host_not_allowed")
        rejected("https://jitsi.lan/r", "host_not_allowed")        # listed only with :8443
        rejected("https://jitsi.lan:9443/r", "host_not_allowed")
        rejected("https://192.168.1.20:8443/r", "host_not_allowed")

    @pytest.mark.parametrize("url", ["https://meet.example.com:99999/r", "https://meet.example.com:abc/r", "https://meet.example.com:-1/r"])
    def test_bad_port(self, url):
        rejected(url)

    @pytest.mark.parametrize("url", [
        "https://127.0.0.1/r", "https://127.0.0.1:8080/r", "https://localhost/r", "https://[::1]/r",   # no port 443 entry
        "https://192.168.1.21/r", "https://10.0.0.1/r", "https://169.254.169.254/latest/meta-data",
        "https://2130706433/r", "https://0x7f.1/r", "https://127.1/r", "https://0177.0.0.1/r",
    ])
    def test_ips_and_loopback_not_in_list(self, url):
        rejected(url, "host_not_allowed")

    def test_percent_encoded_host(self):
        rejected("https://meet.exam%70le.com/r")

    @pytest.mark.parametrize("url", [
        "https://meet.example.com", "https://meet.example.com/", "https://meet.example.com//",
        "https://meet.example.com/tenant/room", "https://meet.example.com//room", "https://meet.example.com/a//b",
        "https://meet.example.com/..", "https://meet.example.com/%2e%2e", "https://meet.example.com/a%2Fb",
    ])
    def test_path_must_be_one_room(self, url):
        rejected(url, "invalid_url")

    @pytest.mark.parametrize("url", ["", "   ", None, 123])
    def test_empty(self, url):
        rejected(url, "invalid_url")

    @pytest.mark.parametrize("url", ["https://meet.example.com/ro\x00om", "https://meet.example.com/ro om",
                                     "https://meet.example.com/room\n", "https://meet.example.com/r\tm", "https://meet.example.com/ro%00om"])
    def test_control_characters(self, url):
        if url.endswith("\n"):
            # strip() removes a trailing newline: that one is fine
            assert ok(url).room == "room"
        else:
            rejected(url, "invalid_url")

    def test_too_long(self):
        rejected("https://meet.example.com/" + "a" * 3000, "invalid_url")
        rejected("https://meet.example.com/" + "a" * 250, "invalid_url")

    def test_disabled_when_allowlist_empty(self):
        with pytest.raises(BotError) as exc:
            parse_jitsi_url("https://meet.example.com/r", ())
        assert exc.value.code == "bot_disabled"

    def test_error_does_not_echo_jwt(self):
        e = rejected("https://evil.example.org/room?jwt=SECRET.TOKEN.VALUE")
        assert "SECRET" not in e.message
        e = rejected("http://meet.example.com/room?jwt=SECRET.TOKEN.VALUE")
        assert "SECRET" not in e.message

    def test_url_error_type(self):
        assert isinstance(rejected("http://meet.example.com/r"), BotUrlError)
