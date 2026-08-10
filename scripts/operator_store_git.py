#!/usr/bin/env python3
"""Trusted CAS persistence adapters for the repository-backed Operator Store."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Callable

from operator_store_model import (
    StoreInvariantError,
    StoreMutationPlan,
    StoreSnapshot,
    apply_plan_to_snapshot,
    canonical_json,
    is_immutable_path,
    is_projection_path,
    validate_store_path,
)
from operator_store_protection import ProtectionReceipt, require_protected

ZERO_SHA = "0" * 40


class CasConflict(RuntimeError):
    pass


@dataclass
class CommitResult:
    ref_sha: str
    snapshot: StoreSnapshot
    result: dict


class MemoryStateRefBackend:
    """Deterministic test backend with explicit one-shot CAS conflict injection."""
    def __init__(self, *, repository: str, state_ref: str, snapshot: StoreSnapshot | None = None):
        self.repository = repository
        self.state_ref = state_ref
        self.snapshot = snapshot or StoreSnapshot(ref_sha=None, files={})
        self._counter = 0
        self._inject_conflict = False

    def read_snapshot(self) -> StoreSnapshot:
        return StoreSnapshot(ref_sha=self.snapshot.ref_sha, files=dict(self.snapshot.files))

    def inject_conflict_once(self) -> None:
        self._inject_conflict = True

    def commit(self, plan: StoreMutationPlan, receipt: ProtectionReceipt | None) -> CommitResult:
        require_protected(receipt, repository=self.repository, state_ref=self.state_ref)
        if self._inject_conflict:
            self._inject_conflict = False
            self._counter += 1
            self.snapshot = StoreSnapshot(ref_sha=f"conflict-{self._counter}", files=dict(self.snapshot.files))
            raise CasConflict("injected state ref conflict")
        if plan.expected_ref_sha != self.snapshot.ref_sha:
            raise CasConflict("operator state ref changed")
        self._counter += 1
        next_sha = f"memory-{self._counter}"
        self.snapshot = apply_plan_to_snapshot(self.snapshot, plan, new_ref_sha=next_sha)
        return CommitResult(next_sha, self.read_snapshot(), dict(plan.result))

    def commit_replanned(self, planner: Callable[[StoreSnapshot], StoreMutationPlan], receipt: ProtectionReceipt | None, *, max_attempts: int = 4) -> CommitResult:
        last_error: Exception | None = None
        for _ in range(max_attempts):
            snapshot = self.read_snapshot()
            plan = planner(snapshot)
            try:
                return self.commit(plan, receipt)
            except CasConflict as exc:
                last_error = exc
        raise CasConflict("operator state ref CAS retries exhausted") from last_error


class GitStateRefBackend:
    """Local trusted Git adapter using update-ref old-SHA compare-and-set."""
    def __init__(self, *, repo_path: str | Path, repository: str, state_ref: str):
        self.repo_path = Path(repo_path)
        self.repository = repository
        self.state_ref = state_ref
        if not state_ref.startswith("refs/heads/"):
            raise ValueError("Operator state ref must be a branch ref")

    def _git(self, *args: str, input_text: str | None = None, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_path,
            input=input_text,
            text=True,
            capture_output=True,
            env=merged_env,
            check=check,
        )

    def _ref_sha(self) -> str | None:
        result = self._git("rev-parse", "--verify", self.state_ref, check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def read_snapshot(self) -> StoreSnapshot:
        sha = self._ref_sha()
        if sha is None:
            return StoreSnapshot(ref_sha=None, files={})
        listed = self._git("ls-tree", "-r", "--name-only", sha, "--", "state/operator/v1").stdout.splitlines()
        files = {}
        for path in listed:
            if not path.endswith(".json"):
                continue
            raw = self._git("show", f"{sha}:{path}").stdout
            files[path] = json.loads(raw)
        return StoreSnapshot(ref_sha=sha, files=files)

    def _validate_plan(self, snapshot: StoreSnapshot, plan: StoreMutationPlan) -> None:
        if plan.expected_ref_sha != snapshot.ref_sha:
            raise CasConflict("operator state ref changed before commit")
        for mutation in plan.mutations:
            validate_store_path(mutation.path)
            existing = snapshot.files.get(mutation.path)
            if mutation.kind == "create_immutable":
                if not is_immutable_path(mutation.path):
                    raise StoreInvariantError("immutable mutation targets mutable path")
                if existing is not None and canonical_json(existing) != canonical_json(mutation.value):
                    raise StoreInvariantError("immutable Store artifact overwrite rejected")
            elif mutation.kind == "replace_projection":
                if not is_projection_path(mutation.path):
                    raise StoreInvariantError("only projection cache may be replaced")
            else:
                raise StoreInvariantError("unsupported Store mutation kind")

    def commit(self, plan: StoreMutationPlan, receipt: ProtectionReceipt | None) -> CommitResult:
        require_protected(receipt, repository=self.repository, state_ref=self.state_ref)
        snapshot = self.read_snapshot()
        self._validate_plan(snapshot, plan)
        with tempfile.NamedTemporaryFile(prefix="ai-sdlc-operator-index-", delete=False) as handle:
            index_path = handle.name
        try:
            env = {"GIT_INDEX_FILE": index_path}
            if snapshot.ref_sha is None:
                self._git("read-tree", "--empty", env=env)
            else:
                self._git("read-tree", snapshot.ref_sha, env=env)
            for mutation in plan.mutations:
                blob = self._git("hash-object", "-w", "--stdin", input_text=canonical_json(mutation.value) + "\n").stdout.strip()
                self._git("update-index", "--add", "--cacheinfo", f"100644,{blob},{mutation.path}", env=env)
            tree_sha = self._git("write-tree", env=env).stdout.strip()
            commit_args = ["commit-tree", tree_sha]
            if snapshot.ref_sha is not None:
                commit_args.extend(["-p", snapshot.ref_sha])
            commit = self._git(*commit_args, input_text="AI-SDLC Operator Store CAS update\n").stdout.strip()
            expected = snapshot.ref_sha or ZERO_SHA
            updated = self._git("update-ref", self.state_ref, commit, expected, check=False)
            if updated.returncode != 0:
                raise CasConflict("operator state ref CAS failed")
            materialized = self.read_snapshot()
            return CommitResult(commit, materialized, dict(plan.result))
        finally:
            try:
                os.unlink(index_path)
            except FileNotFoundError:
                pass

    def commit_replanned(self, planner: Callable[[StoreSnapshot], StoreMutationPlan], receipt: ProtectionReceipt | None, *, max_attempts: int = 4) -> CommitResult:
        last_error: Exception | None = None
        for _ in range(max_attempts):
            plan = planner(self.read_snapshot())
            try:
                return self.commit(plan, receipt)
            except CasConflict as exc:
                last_error = exc
        raise CasConflict("operator state ref CAS retries exhausted") from last_error
