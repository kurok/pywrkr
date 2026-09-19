"""The SSRF guard on the Jenkins pipeline's TARGET_URL.

The previous check ran regexes over the whole URL string. Every case below
except the plain public one got past it.
"""

from __future__ import annotations

import importlib.util
import pathlib
import socket
import unittest
from unittest.mock import patch

_MODULE = (
    pathlib.Path(__file__).resolve().parent.parent / "infra" / "jenkins" / "validate_target_url.py"
)
_spec = importlib.util.spec_from_file_location("validate_target_url", _MODULE)
assert _spec and _spec.loader
validate_target_url = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate_target_url)

Rejected = validate_target_url.Rejected
validate = validate_target_url.validate


def _resolves_to(*addresses: str):
    """Stub getaddrinfo so these tests never touch DNS."""

    def fake(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 0)) for addr in addresses]

    return patch.object(socket, "getaddrinfo", side_effect=fake)


class TestValidateTargetUrl(unittest.TestCase):
    def test_a_public_host_is_accepted(self):
        with _resolves_to("93.184.216.34"):
            validate("https://example.com/path?q=1")

    def test_userinfo_is_rejected(self):
        """http://user@10.0.0.1/ passed the old regexes.

        They matched against the whole string, so the pattern looking for an
        address saw "http://user" and moved on.
        """
        with self.assertRaises(Rejected) as ctx:
            validate("http://user@10.0.0.1/")
        self.assertIn("userinfo", str(ctx.exception))

    def test_the_addresses_the_old_regexes_missed(self):
        for url, resolved in (
            ("http://0.0.0.0/", "0.0.0.0"),
            ("http://[::1]/", "::1"),
            ("http://2130706433/", "127.0.0.1"),
            ("http://127.0.0.1/", "127.0.0.1"),
            ("http://169.254.169.254/", "169.254.169.254"),
            ("http://10.0.0.1/", "10.0.0.1"),
            ("http://172.16.0.1/", "172.16.0.1"),
            ("http://192.168.1.1/", "192.168.1.1"),
        ):
            with self.subTest(url=url), _resolves_to(resolved):
                with self.assertRaises(Rejected):
                    validate(url)

    def test_a_name_resolving_to_the_metadata_service_is_rejected(self):
        """The regexes only ever saw the literal text of the URL, so any DNS
        name pointing at 169.254.169.254 went straight through."""
        with _resolves_to("169.254.169.254"):
            with self.assertRaises(Rejected) as ctx:
                validate("http://metadata.example.com/latest/meta-data/")
        self.assertIn("169.254.169.254", str(ctx.exception))

    def test_every_address_is_checked_not_just_the_first(self):
        """A name answering with a public address and a private one is the
        standard way past a check that looks at one answer."""
        with _resolves_to("93.184.216.34", "10.0.0.5"):
            with self.assertRaises(Rejected) as ctx:
                validate("http://split-horizon.example.com/")
        self.assertIn("10.0.0.5", str(ctx.exception))

    def test_non_http_schemes_are_rejected(self):
        for url in ("ftp://example.com/", "file:///etc/passwd", "gopher://example.com/"):
            with self.subTest(url=url):
                with self.assertRaises(Rejected):
                    validate(url)

    def test_a_host_that_does_not_resolve_is_rejected(self):
        with patch.object(socket, "getaddrinfo", side_effect=socket.gaierror("nope")):
            with self.assertRaises(Rejected) as ctx:
                validate("http://no-such-host.invalid/")
        self.assertIn("does not resolve", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
