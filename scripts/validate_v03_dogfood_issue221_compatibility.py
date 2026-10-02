#!/usr/bin/env python3
"""Reject reuse when any existing tested authority changes."""
from __future__ import annotations
from copy import deepcopy
from v03_dogfood_issue221_compatibility import (
    ADDED_PATHS, SOURCE_MAIN, SOURCE_CONTROL_BLOBS, DOGFOOD_CONTROL_BLOBS,
    Issue221CompatibilityError, validate_delta,
)


def main():
    rows = [
        dict(path=path, status="A", old_sha="0"*40, new_sha="a"*40, old_mode="000000", new_mode="100644")
        for path in sorted(ADDED_PATHS)
    ] + [
        dict(path=path, status="M", old_sha=old, new_sha=DOGFOOD_CONTROL_BLOBS[path],
             old_mode="100644", new_mode="100644")
        for path, old in SOURCE_CONTROL_BLOBS.items()
    ]
    def check(candidate, **overrides):
        args = dict(source_sha=SOURCE_MAIN, installation_sha="b"*40, ancestor=True)
        args.update(overrides)
        return validate_delta(candidate, **args)
    proof = check(rows)
    assert proof["source_main_sha"] == SOURCE_MAIN
    assert proof["existing_runtime_tree_unchanged"] is True
    assert proof["installation_commit_sha"] != SOURCE_MAIN
    assert check([], installation_sha=SOURCE_MAIN)["tree_delta"] == []
    count = 0
    def reject(candidate, **overrides):
        nonlocal count
        try:
            check(candidate, **overrides)
        except Issue221CompatibilityError:
            count += 1
            return
        raise AssertionError("unsafe reuse accepted")
    for path in ("scripts/operator_vertical_callback.py", "scripts/v03_effect_safety_final_live_ledger.py",
                 "scripts/v03_scenario_fixture_pool.py", "requirements-dev.txt", "runtimes/gh-aw/profile-routing.yaml"):
        reject(rows + [dict(path=path, status="M", old_sha="a"*40, new_sha="c"*40,
                           old_mode="100644", new_mode="100644")])
    reject(rows, ancestor=False)
    reject(rows, source_sha="c"*40)
    reject(rows, installation_sha="main")
    for key, value in (("status", "D"), ("old_sha", "a"*40), ("new_mode", "120000")):
        changed = deepcopy(rows)
        changed[0][key] = value
        reject(changed)
    changed = deepcopy(rows)
    changed[-1]["new_sha"] = "d"*40
    reject(changed)
    reject(rows + [deepcopy(rows[0])])
    # Check the real CI checkout tree as well as adversarial supplied deltas.
    import subprocess
    from v03_dogfood_issue221_compatibility import verify_installation
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    actual = verify_installation(head)
    assert actual["source_main_sha"] == SOURCE_MAIN
    print(f"v0.3 #221 dogfood tree compatibility: PASS ({count} unsafe deltas rejected)")


if __name__ == "__main__":
    main()
