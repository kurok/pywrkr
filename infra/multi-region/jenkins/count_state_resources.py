#!/usr/bin/env python3
"""Print how many managed resources a terraform state holds.

Reads ``terraform state pull`` output (or a state file) on stdin; empty input,
which is what ``state pull`` prints when there is no state yet, counts as 0.
Data sources are not counted: they create nothing a destroy would remove.

Used by the multi-region Jenkinsfile (#315), which must tell "no state" from
"could not read the state" (the caller runs ``state pull`` on its own, so a
read error fails the step) and from "state that describes running resources".
With ``--name-prefix``, also exits 3 when any managed resource lacks that
prefix in its attributes, i.e. the state belongs to another environment.
"""

from __future__ import annotations

import argparse
import json
import sys


def count_managed(state: dict) -> int:
    return sum(
        len(resource.get("instances", [])) or 1
        for resource in state.get("resources", [])
        if resource.get("mode") == "managed"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name-prefix", help="every managed resource must mention this")
    args = parser.parse_args(argv)
    text = sys.stdin.read().strip()
    state = json.loads(text) if text else {}
    count = count_managed(state)
    print(count)
    if args.name_prefix and count:
        managed = [r for r in state.get("resources", []) if r.get("mode") == "managed"]
        # Names such as "pywrkr-loadtest-use1-..." appear in each resource's
        # attributes; a state from another environment carries none of them.
        if not any(args.name_prefix in json.dumps(r) for r in managed):
            print(f"state does not belong to {args.name_prefix!r}", file=sys.stderr)
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
