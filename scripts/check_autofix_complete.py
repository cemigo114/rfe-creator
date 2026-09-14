#!/usr/bin/env python3
"""Check whether auto-fix processed all items.

Reads the pipeline state and verifies every item has a review file.
Exits 0 if all complete, exits 1 with the list of missing IDs if not.

Usage:
    python3 scripts/check_autofix_complete.py [--type rfe|initiative]

An unregistered --type exits 2 with the registered type list.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import type_registry

# The registry validates --type; _TYPE_CONFIG stays literal because its ids_file values are
# pipeline facts (the speedrun state prefix) pinned to the descriptors by test, not derived.
_TYPES = type_registry.load()

_TYPE_CONFIG = {
    "rfe": {
        "ids_file": "tmp/speedrun-all-ids.txt",
        "reviews_dir": "artifacts/rfe-reviews",
    },
    "initiative": {
        "ids_file": "tmp/initiative-speedrun-all-ids.txt",
        "reviews_dir": "artifacts/initiative-reviews",
    },
}


def main():
    # --type is hand-parsed by the registry's shared helper (design §5 rung 1): an unregistered
    # name, or a trailing flag, exits 2 with the registered list; rfe when the flag is absent.
    try:
        pipeline_type, _ = type_registry.parse_type_arg(_TYPES, sys.argv[1:])
    except type_registry.ResolveError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(exc.exit_code)

    # Validated above; a registered name that is not in this literal table would be a pin
    # failure (tests/test_type_registry_pins.py), never a CLI error.
    tc = _TYPE_CONFIG[pipeline_type]

    ids_file = tc["ids_file"]
    if not os.path.exists(ids_file):
        print("ERROR: no speedrun ID list found", file=sys.stderr)
        sys.exit(1)

    with open(ids_file) as f:
        all_ids = [line.strip() for line in f if line.strip()]

    if not all_ids:
        print("ERROR: empty ID list", file=sys.stderr)
        sys.exit(1)

    reviews_dir = tc["reviews_dir"]
    missing = []
    for rfe_id in all_ids:
        review_path = os.path.join(reviews_dir, f"{rfe_id}-review.md")
        if not os.path.exists(review_path):
            missing.append(rfe_id)

    if missing:
        print(f"INCOMPLETE: {len(missing)}/{len(all_ids)} missing reviews")
        print(f"MISSING_IDS={','.join(missing)}")
        sys.exit(1)
    else:
        print(f"COMPLETE: {len(all_ids)}/{len(all_ids)} reviewed")
        sys.exit(0)


if __name__ == "__main__":
    main()
