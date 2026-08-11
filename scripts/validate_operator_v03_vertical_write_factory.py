#!/usr/bin/env python3
"""Validate authoritative routing for v0.3 write-ready composition factories."""
from __future__ import annotations

import ast
import inspect

from operator_v03_write_runtime import (
    build_v03_vertical_write_ready_operator_bundle,
    build_v03_write_ready_operator_bundle,
)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def called_names(function):
    tree = ast.parse(inspect.getsource(function))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.append(node.func.attr)
    return names, tree


def main():
    legacy_calls, _ = called_names(build_v03_write_ready_operator_bundle)
    full_calls, full_tree = called_names(build_v03_vertical_write_ready_operator_bundle)

    require("build_v03_vertical_production_bundle" not in legacy_calls, "semantic-only compatibility helper silently changed into full Vertical authority")
    require("build_trusted_vertical_runtime" not in legacy_calls, "semantic-only compatibility helper creates Vertical runtime authority")
    require(full_calls.count("build_v03_vertical_production_bundle") == 1, "full write-ready factory must delegate to exactly one integrated Vertical production builder")
    require("build_trusted_operator_backend_bundle" not in full_calls, "full write-ready factory creates an independent Store/backend bundle")
    require("build_trusted_vertical_runtime" not in full_calls, "full write-ready factory duplicates the integrated Vertical builder")

    lazy_imports = [
        node
        for node in ast.walk(full_tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "operator_v03_vertical_production_runtime"
        and any(alias.name == "build_v03_vertical_production_bundle" for alias in node.names)
    ]
    require(len(lazy_imports) == 1, "full factory must lazy-load the production builder exactly once")

    legacy_doc = inspect.getdoc(build_v03_write_ready_operator_bundle) or ""
    full_doc = inspect.getdoc(build_v03_vertical_write_ready_operator_bundle) or ""
    require("does not create the trusted Vertical executor" in legacy_doc, "compatibility helper does not disclose its semantic-only boundary")
    require("authoritative full Vertical production bundle" in full_doc, "full production factory lacks an explicit authority boundary")

    print("v0.3 Vertical write-ready factory routing validation passed")
    print("- legacy build_v03_write_ready_operator_bundle remains semantic-write-only")
    print("- full factory lazy-loads and delegates once to shared-runtime composition")
    print("- no alternate Store or Vertical builder exists in launcher-facing full factory")


if __name__ == "__main__":
    main()
