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
whose name merely starts with this one (``dev-eu`` for ``dev``), has none.
With ``--module-prefix``, only resources in matching modules are counted:
the pipeline's guard counts the regional test cells (``module.region_``), not
the environment's ECR repository, which the image push needs to exist.
An unreadable state exits 2.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# The region codes the stack's names carry (infra/multi-region/terraform/main.tf).
REGION_CODES = ("use1", "euw1", "apse1")


def count_managed(state: dict, module_prefix: str = "") -> int:
    return sum(
        len(resource.get("instances", []))
        for resource in state.get("resources", [])
        if resource.get("mode") == "managed"
        and resource.get("module", "").startswith(module_prefix)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--name-prefix", help="<project>-<environment>- the state's regional names must use"
    )
    parser.add_argument("--module-prefix", default="", help="count only resources in these modules")
    args = parser.parse_args(argv)
    text = sys.stdin.read().strip()
    try:
        state = json.loads(text) if text else {}
    except json.JSONDecodeError as exc:
        print(f"not a terraform state: {exc}", file=sys.stderr)
        return 2
    print(count_managed(state, args.module_prefix))
    if args.name_prefix and count_managed(state):
        managed = [r for r in state.get("resources", []) if r.get("mode") == "managed"]
        # The region code must follow the prefix directly, so "pywrkr-dev-"
        # does not accept "pywrkr-dev-eu-use1".
        regional = re.compile(
            re.escape(args.name_prefix) + "(?:" + "|".join(REGION_CODES) + ")(?![a-z0-9])"
        )
        if not any(regional.search(json.dumps(r)) for r in managed):
            print(f"state does not belong to {args.name_prefix!r}", file=sys.stderr)
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
