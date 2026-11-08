"""Certificate verification against a real TLS handshake (#321).

``--ssl-verify`` / ``--ca-bundle`` used to be tested only by inspecting the
``SSLContext`` object. These tests run a request through pywrkr against a local
HTTPS server and check what a user depends on: whether the handshake is
accepted or refused.

TEST-ONLY KEY MATERIAL: the CA and server certificate are generated per test
session with the ``openssl`` command into a temporary directory, valid for two
days, for 127.0.0.1 only, and deleted afterwards. Nothing is checked in.
"""

from __future__ import annotations

import os
import shutil
import ssl
import subprocess  # nosec B404 - runs the local openssl binary with fixed arguments
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from aiohttp import web

import pywrkr
from pywrkr.config import BenchmarkConfig, SSLConfig

OPENSSL = shutil.which("openssl")

_OPENSSL_CONFIG = """\
[req]
distinguished_name = dn
prompt = no
[dn]
CN = pywrkr test only
[v3_ca]
basicConstraints = critical, CA:TRUE
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
[v3_leaf]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = IP:127.0.0.1
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
"""


def _openssl(*args: str, cwd: Path) -> None:
    if OPENSSL is None:
        raise RuntimeError("the openssl command is not available")
    try:
        subprocess.run([OPENSSL, *args], cwd=cwd, check=True, capture_output=True)  # nosec B603
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"openssl {' '.join(args[:2])} failed: {exc.stderr.decode(errors='replace')}"
        ) from exc


def make_test_ca(directory: Path, name: str = "ca") -> Path:
    """A throwaway self-signed CA certificate (and key) in *directory*."""
    (directory / "openssl.cnf").write_text(_OPENSSL_CONFIG)
    _openssl(
        "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
        "-keyout", f"{name}.key", "-out", f"{name}.pem",
        "-subj", "/CN=pywrkr test CA - test only", "-config", "openssl.cnf",
        "-extensions", "v3_ca",
        cwd=directory,
    )  # fmt: skip
    return directory / f"{name}.pem"


def make_test_pki(directory: Path) -> tuple[Path, Path, Path]:
    """A throwaway CA and a 127.0.0.1 server certificate it signs.

    Returns:
        ``(ca_pem, server_pem, server_key)``.
    """
    make_test_ca(directory)
    cfg = "openssl.cnf"
    _openssl(
        "req", "-new", "-newkey", "rsa:2048", "-nodes",
        "-keyout", "server.key", "-out", "server.csr",
        "-subj", "/CN=127.0.0.1", "-config", cfg,
        cwd=directory,
    )  # fmt: skip
    _openssl(
        "x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key",
        "-CAcreateserial", "-days", "2", "-out", "server.pem",
        "-extfile", cfg, "-extensions", "v3_leaf",
        cwd=directory,
    )  # fmt: skip
    return directory / "ca.pem", directory / "server.pem", directory / "server.key"


@unittest.skipIf(OPENSSL is None, "the openssl command is needed to make a test certificate")
class TestTlsHandshake(unittest.IsolatedAsyncioTestCase):
    """pywrkr against a local HTTPS server whose certificate a test CA signs."""

    @classmethod
    def setUpClass(cls) -> None:
        tmp = TemporaryDirectory()
        # Registered first, so the key material goes even if generation fails.
        cls.addClassCleanup(tmp.cleanup)
        directory = Path(tmp.name)
        cls.ca_pem, cls.server_pem, cls.server_key = make_test_pki(directory)
        cls.other_ca_pem = make_test_ca(directory, name="other-ca")

    async def asyncSetUp(self) -> None:
        self.received: list[tuple[str | None, str | None]] = []

        async def ok(request: web.Request) -> web.Response:
            self.received.append(
                (request.headers.get("Authorization"), request.cookies.get("session"))
            )
            return web.Response(text="ok")

        app = web.Application()
        app.router.add_get("/", ok)
        server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        server_ctx.load_cert_chain(self.server_pem, self.server_key)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0, ssl_context=server_ctx)
        await site.start()
        self.port = self._runner.addresses[0][1]
        self.url = f"https://127.0.0.1:{self.port}/"

    async def asyncTearDown(self) -> None:
        await self._runner.cleanup()

    async def _run(self, ssl_config: SSLConfig, url: str | None = None, **extra):
        config = BenchmarkConfig(
            url=url or self.url,
            connections=1,
            num_requests=3,
            timeout_sec=5,
            ssl_config=ssl_config,
            **extra,
        )
        with patch("sys.stdout", new_callable=StringIO), patch("sys.stderr", new_callable=StringIO):
            stats, _ = await pywrkr.run_benchmark(config)
        return stats

    def assertRefusedByCertificate(self, stats) -> None:  # noqa: N802 - unittest style
        """Every request failed, and failed on certificate validation -- not a
        refused connection or some other error that would satisfy a count."""
        self.assertEqual(stats.total_requests, 3)
        self.assertEqual(dict(stats.error_types), {"ClientConnectorCertificateError": 3})

    async def test_ssl_verify_with_the_test_ca_succeeds(self):
        stats = await self._run(SSLConfig(verify=True, ca_bundle=str(self.ca_pem)))
        self.assertEqual(stats.errors, 0, dict(stats.error_types))
        self.assertEqual(stats.total_requests, 3)

    async def test_ssl_verify_with_credentials_and_the_test_ca_succeeds(self):
        """The case the README recommends verification for."""
        stats = await self._run(
            SSLConfig(verify=True, ca_bundle=str(self.ca_pem)),
            basic_auth="user:pass",
            cookies=["session=abc"],
        )
        self.assertEqual(stats.errors, 0, dict(stats.error_types))
        self.assertEqual(self.received, [("Basic dXNlcjpwYXNz", "abc")] * 3)

    async def test_ssl_verify_without_the_ca_fails(self):
        """An untrusted certificate is an error on every request, never a silent downgrade."""
        stats = await self._run(SSLConfig(verify=True))
        self.assertRefusedByCertificate(stats)
        self.assertEqual(self.received, [])

    async def test_ssl_verify_checks_the_hostname(self):
        """The right CA is not enough: the certificate is for 127.0.0.1 only."""
        stats = await self._run(
            SSLConfig(verify=True, ca_bundle=str(self.ca_pem)),
            url=f"https://localhost:{self.port}/",
        )
        if dict(stats.error_types) == {"ClientConnectorError": 3}:
            self.skipTest("localhost resolved to an address the server does not listen on")
        self.assertRefusedByCertificate(stats)

    async def test_ssl_verify_with_the_wrong_ca_fails(self):
        stats = await self._run(SSLConfig(verify=True, ca_bundle=str(self.other_ca_pem)))
        self.assertRefusedByCertificate(stats)

    async def test_default_skips_verification_against_a_self_signed_server(self):
        """Verification is off by default; this pins that documented behaviour.

        The server's certificate is signed by a CA nothing trusts, which is
        what a self-signed one amounts to for the client."""
        stats = await self._run(SSLConfig())
        self.assertEqual(stats.errors, 0, dict(stats.error_types))

    async def test_ca_bundle_from_the_environment_is_honoured(self):
        env = {"PYWRKR_SSL_VERIFY": "1", "PYWRKR_CA_BUNDLE": str(self.ca_pem)}
        with patch.dict(os.environ, env):
            ssl_config = SSLConfig.from_env()
        self.assertEqual(ssl_config, SSLConfig(verify=True, ca_bundle=str(self.ca_pem)))
        stats = await self._run(ssl_config)
        self.assertEqual(stats.errors, 0, dict(stats.error_types))

        with patch.dict(os.environ, {"PYWRKR_SSL_VERIFY": "1", "PYWRKR_CA_BUNDLE": ""}):
            ssl_config = SSLConfig.from_env()
        stats = await self._run(ssl_config)
        self.assertRefusedByCertificate(stats)

    async def test_the_cli_merges_flags_and_environment(self):
        """The CLI's flag/env merge, not just SSLConfig.from_env, feeds the run."""
        from pywrkr.main import _build_parser, _parse_and_validate_args

        parser = _build_parser()
        env = {"PYWRKR_SSL_VERIFY": "1", "PYWRKR_CA_BUNDLE": str(self.ca_pem)}
        with patch.dict(os.environ, env), patch("sys.stderr", new_callable=StringIO):
            config, _ = _parse_and_validate_args(parser, parser.parse_args(["-n", "3", self.url]))
        self.assertEqual(config.ssl_config, SSLConfig(verify=True, ca_bundle=str(self.ca_pem)))
        stats = await self._run(config.ssl_config)
        self.assertEqual(stats.errors, 0, dict(stats.error_types))

        with patch.dict(os.environ, {}, clear=False), patch("sys.stderr", new_callable=StringIO):
            os.environ.pop("PYWRKR_SSL_VERIFY", None)
            os.environ.pop("PYWRKR_CA_BUNDLE", None)
            args = parser.parse_args(
                ["--ssl-verify", "--ca-bundle", str(self.other_ca_pem), self.url]
            )
            config, _ = _parse_and_validate_args(parser, args)
        self.assertRefusedByCertificate(await self._run(config.ssl_config))


if __name__ == "__main__":
    unittest.main()
