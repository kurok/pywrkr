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
    assert OPENSSL is not None
    subprocess.run([OPENSSL, *args], cwd=cwd, check=True, capture_output=True)  # nosec B603


def make_test_pki(directory: Path) -> tuple[Path, Path, Path]:
    """A throwaway CA and a 127.0.0.1 server certificate it signs.

    Returns:
        ``(ca_pem, server_pem, server_key)``.
    """
    (directory / "openssl.cnf").write_text(_OPENSSL_CONFIG)
    cfg = "openssl.cnf"
    _openssl(
        "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
        "-keyout", "ca.key", "-out", "ca.pem",
        "-subj", "/CN=pywrkr test CA - test only", "-config", cfg, "-extensions", "v3_ca",
        cwd=directory,
    )  # fmt: skip
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
        cls._tmp = TemporaryDirectory()
        cls.ca_pem, cls.server_pem, cls.server_key = make_test_pki(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    async def asyncSetUp(self) -> None:
        async def ok(request: web.Request) -> web.Response:
            return web.Response(text="ok")

        app = web.Application()
        app.router.add_get("/", ok)
        server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        server_ctx.load_cert_chain(self.server_pem, self.server_key)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0, ssl_context=server_ctx)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        self.url = f"https://127.0.0.1:{port}/"

    async def asyncTearDown(self) -> None:
        await self._runner.cleanup()

    async def _run(self, ssl_config: SSLConfig, **extra):
        config = BenchmarkConfig(
            url=self.url,
            connections=1,
            num_requests=3,
            timeout_sec=5,
            ssl_config=ssl_config,
            **extra,
        )
        with patch("sys.stdout", new_callable=StringIO):
            stats, _ = await pywrkr.run_benchmark(config)
        return stats

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

    async def test_ssl_verify_without_the_ca_fails(self):
        """An untrusted certificate is an error on every request, never a silent downgrade."""
        stats = await self._run(SSLConfig(verify=True))
        self.assertEqual(stats.errors, stats.total_requests)
        self.assertGreater(stats.errors, 0)
        self.assertTrue(
            any("certificate" in key.lower() or "ssl" in key.lower() for key in stats.error_types),
            dict(stats.error_types),
        )

    async def test_ssl_verify_with_the_wrong_ca_fails(self):
        with TemporaryDirectory() as other:
            other_ca, _, _ = make_test_pki(Path(other))
            stats = await self._run(SSLConfig(verify=True, ca_bundle=str(other_ca)))
        self.assertEqual(stats.errors, stats.total_requests)

    async def test_default_skips_verification_against_a_self_signed_server(self):
        """Verification is off by default; this pins that documented behaviour."""
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
        self.assertEqual(stats.errors, stats.total_requests)


if __name__ == "__main__":
    unittest.main()
