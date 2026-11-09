#!/usr/bin/env python3
"""Print how many managed resources a terraform state holds.

Reads ``terraform state pull`` output (or a state file) on stdin; empty input,
which is what ``state pull`` prints when there is no state yet, counts as 0.
Data sources are not counted: they create nothing a destroy would remove.

Used by the multi-region Jenkinsfile (#315), which must tell "no state" from
"could not read the state" (the caller runs ``state pull`` on its own, so a
read error fails the step) and from "state that describes running resources".
With ``--name-prefix`` (``<project>-<environment>-``), also exits 3 unless a
managed resource carries a regional name under exactly that prefix, such as
``pywrkr-dev-use1``: a state written for another environment, including one
whose name merely starts with this one (``dev-eu`` for ``dev``), has none,
unless that name continues with a region code (``dev-use1``).
With ``--module-prefix``, only resources in matching modules are counted:
the pipeline's guard counts the regional test cells (``module.region_``), not
the environment's ECR repository, which the image push needs to exist.
With ``--legacy-secret-addresses``, prints instead the addresses of the
worker-secret resources earlier versions managed (``aws_ssm_parameter`` and
``random_password`` named ``worker_secret``, with their ``[0]`` index), one
per line, for ``terraform state rm``; the data source of that name is not one.
An unreadable state exits 2.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# What follows <project>-<environment>- in the stack's names: a region code
# (infra/multi-region/terraform/main.tf) or the shared ECR repository's "pywrkr".
NAME_SUFFIXES = ("use1", "euw1", "apse1", "pywrkr")


def count_managed(state: dict, module_prefix: str = "") -> int:
    return sum(
        len(resource.get("instances", []))
        for resource in state.get("resources", [])
        if resource.get("mode") == "managed"
        and resource.get("module", "").startswith(module_prefix)
    )


def legacy_secret_addresses(state: dict) -> list[str]:
    addresses = []
    for resource in state.get("resources", []):
        if resource.get("mode") != "managed" or resource.get("name") != "worker_secret":
            continue
        if resource.get("type") not in ("aws_ssm_parameter", "random_password"):
            continue
        base = ".".join(p for p in (resource.get("module"), resource["type"], "worker_secret") if p)
        for instance in resource.get("instances", []):
            key = instance.get("index_key")
            suffix = "" if key is None else (f"[{key}]" if isinstance(key, int) else f'["{key}"]')
            addresses.append(base + suffix)
    return addresses


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--name-prefix", help="<project>-<environment>- the state's regional names must use"
    )
    parser.add_argument("--module-prefix", default="", help="count only resources in these modules")
    parser.add_argument(
        "--legacy-secret-addresses",
        action="store_true",
        help="print the addresses of the formerly managed worker-secret resources",
    )
    args = parser.parse_args(argv)
    text = sys.stdin.read().strip()
    try:
        state = json.loads(text) if text else {}
    except json.JSONDecodeError as exc:
        print(f"not a terraform state: {exc}", file=sys.stderr)
        return 2
    if args.legacy_secret_addresses:
        for address in legacy_secret_addresses(state):
            print(address)
        return 0
    print(count_managed(state, args.module_prefix))
    if args.name_prefix and count_managed(state):
        managed = [r for r in state.get("resources", []) if r.get("mode") == "managed"]
        # The region code must follow the prefix directly, so "pywrkr-dev-"
        # does not accept "pywrkr-dev-eu-use1".
        regional = re.compile(
            re.escape(args.name_prefix) + "(?:" + "|".join(NAME_SUFFIXES) + ")(?![a-z0-9])"
        )
        if not any(regional.search(json.dumps(r)) for r in managed):
            print(f"state does not belong to {args.name_prefix!r}", file=sys.stderr)
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
