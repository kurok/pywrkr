#!/usr/bin/env python3
"""Refuse a TARGET_URL that points anywhere internal.

An operator with Jenkins build access can otherwise aim the Fargate task at
the instance metadata service or a host inside the VPC, and the task role is
what makes that worth doing.

This is a script rather than Groovy in the Jenkinsfile for two reasons: the
checks below have unit tests, which pipeline code in this repository does not;
and resolving a hostname from Groovy needs script approval on a sandboxed
controller, which is a thing people grant once and forget.

Exits 0 when the URL is acceptable, 1 with a reason on stderr when it is not.
"""

from __future__ import annotations

import ipaddress
import socket
import sys
from urllib.parse import urlsplit

ALLOWED_SCHEMES = ("http", "https")


class Rejected(Exception):
    """The URL must not be used."""


def _addresses(host: str) -> list[str]:
    """Every address the host resolves to.

    Every one of them, not the first: a name that returns a public address and
    a private one is the standard way past a check that looks at a single
    answer.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise Rejected(f"host {host!r} does not resolve: {exc}") from None
    return sorted({info[4][0] for info in infos})


def _reject_if_internal(address: str, host: str) -> None:
    ip = ipaddress.ip_address(address)
    # is_global is the inverse of the whole special-purpose registry, so this
    # covers loopback, link-local (including 169.254.169.254), RFC 1918,
    # carrier-grade NAT, 0.0.0.0/8, multicast and the IPv6 equivalents --
    # rather than the handful of ranges anyone remembers to write out.
    if not ip.is_global:
        raise Rejected(
            f"host {host!r} resolves to {address}, which is not a public address. "
            "This check prevents SSRF via the Fargate task role."
        )


def validate(url: str) -> None:
    """Raise :class:`Rejected` if *url* must not be benchmarked."""
    parts = urlsplit(url)

    if parts.scheme not in ALLOWED_SCHEMES:
        raise Rejected(f"TARGET_URL must start with http:// or https://. Got: {url!r}")

    # Parsed, not pattern-matched. The previous check ran regexes over the
    # whole string, so http://user@10.0.0.1/ passed: the regex saw
    # "http://user" where it wanted an address.
    if parts.username or parts.password:
        raise Rejected(
            f"TARGET_URL {url!r} carries userinfo before the host, which hides "
            "the real destination from a reader and from a simple check."
        )

    host = parts.hostname
    if not host:
        raise Rejected(f"TARGET_URL {url!r} has no host")

    for address in _addresses(host):
        _reject_if_internal(address, host)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <url>", file=sys.stderr)
        return 2
    try:
        validate(argv[1])
    except Rejected as exc:
        print(f"TARGET_URL rejected: {exc}", file=sys.stderr)
        return 1
    print(f"TARGET_URL validated: {argv[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
