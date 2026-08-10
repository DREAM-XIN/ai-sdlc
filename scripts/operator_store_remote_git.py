#!/usr/bin/env python3
"""Remote repository CAS backend for the durable AI-SDLC Operator Store."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from operator_store_git import CasConflict, CommitResult, GitStateRefBackend
from operator_store_model import StoreMutationPlan, StoreSnapshot, canonical_json
from operator_store_protection import ProtectionReceipt, require_protected


class RemoteGitStateRefBackend(GitStateRefBackend):
    """Persist Store commits to one shared remote ref using fast-forward CAS semantics.

    Every Store commit is parented by the exact remote SHA read for planning. A normal
    (non-force) push can therefore succeed only while the remote ref is still at that
    SHA, assuming the positively verified protection policy forbids ref rewind/force.
    """

    def __init__(self, *, repo_path: str | Path, repository: str, state_ref: str, remote_name: str = "origin"):
        super().__init__(repo_path=repo_path, repository=repository, state_ref=state_ref)
        self.remote_name = remote_name
        self.tracking_ref = "refs/ai-sdlc/operator-store/remote"
        if not remote_name or any(ch.isspace() for ch in remote_name):
            raise ValueError("trusted Operator Store remote name is invalid")

    def _remote_sha(self) -> str | None:
        result = self._git("ls-remote", "--refs", self.remote_name, self.state_ref, check=False)
        if result.returncode != 0:
            raise CasConflict("unable to read remote Operator state ref")
        line = result.stdout.strip()
        if not line:
            return None
        sha, _, ref = line.partition("\t")
        if ref != self.state_ref or len(sha) != 40:
            raise CasConflict("unexpected remote Operator state-ref response")
        return sha

    def _snapshot_for_sha(self, sha: str | None) -> StoreSnapshot:
        if sha is None:
            return StoreSnapshot(ref_sha=None, files={})
        listed = self._git("ls-tree", "-r", "--name-only", sha, "--", "state/operator/v1").stdout.splitlines()
        files = {}
        for path in listed:
            if path.endswith(".json"):
                files[path] = json.loads(self._git("show", f"{sha}:{path}").stdout)
        return StoreSnapshot(ref_sha=sha, files=files)

    def read_snapshot(self) -> StoreSnapshot:
        sha = self._remote_sha()
        if sha is None:
            self._git("update-ref", "-d", self.tracking_ref, check=False)
            return StoreSnapshot(ref_sha=None, files={})
        fetched = self._git(
            "fetch", "--no-tags", self.remote_name,
            f"+{self.state_ref}:{self.tracking_ref}", check=False,
        )
        if fetched.returncode != 0:
            raise CasConflict("unable to fetch remote Operator state ref")
        local_sha = self._git("rev-parse", "--verify", self.tracking_ref).stdout.strip()
        if local_sha != sha:
            raise CasConflict("remote Operator state ref changed during fetch")
        return self._snapshot_for_sha(sha)

    def _build_commit(self, snapshot: StoreSnapshot, plan: StoreMutationPlan) -> str:
        fd, index_path = tempfile.mkstemp(prefix="ai-sdlc-operator-remote-index-")
        os.close(fd)
        os.unlink(index_path)
        try:
            env = {"GIT_INDEX_FILE": index_path}
            if snapshot.ref_sha is None:
                self._git("read-tree", "--empty", env=env)
            else:
                self._git("read-tree", snapshot.ref_sha, env=env)
            for mutation in plan.mutations:
                blob = self._git(
                    "hash-object", "-w", "--stdin",
                    input_text=canonical_json(mutation.value) + "\n",
                ).stdout.strip()
                self._git(
                    "update-index", "--add", "--cacheinfo",
                    f"100644,{blob},{mutation.path}", env=env,
                )
            tree = self._git("write-tree", env=env).stdout.strip()
            args = ["commit-tree", tree]
            if snapshot.ref_sha is not None:
                args += ["-p", snapshot.ref_sha]
            return self._git(*args, input_text="AI-SDLC Operator Store remote CAS update\n").stdout.strip()
        finally:
            try:
                os.unlink(index_path)
            except FileNotFoundError:
                pass

    def commit(self, plan: StoreMutationPlan, receipt: ProtectionReceipt | None) -> CommitResult:
        require_protected(receipt, repository=self.repository, state_ref=self.state_ref)
        snapshot = self.read_snapshot()
        self._validate_plan(snapshot, plan)
        commit = self._build_commit(snapshot, plan)

        # Re-read immediately before the remote write. This catches conflicts before
        # transport; a race after this read is still rejected by non-fast-forward push
        # because the new commit is parented by the exact expected SHA.
        if self._remote_sha() != snapshot.ref_sha:
            raise CasConflict("remote Operator state ref changed before push")
        pushed = self._git(
            "push", "--porcelain", self.remote_name,
            f"{commit}:{self.state_ref}", check=False,
        )
        if pushed.returncode != 0:
            raise CasConflict("remote Operator state ref CAS push rejected")

        durable = self.read_snapshot()
        if durable.ref_sha != commit:
            raise CasConflict("remote Operator state ref did not confirm committed SHA")
        return CommitResult(commit, durable, dict(plan.result))
