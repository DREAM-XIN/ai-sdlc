#!/usr/bin/env python3
import subprocess
import tempfile
from pathlib import Path

from verify_git_write_precondition import verify_write_precondition


def run(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        origin = root / "origin.git"
        seed = root / "seed"
        worker_a = root / "worker-a"
        worker_b = root / "worker-b"

        run("git", "init", "--bare", str(origin))
        run("git", "clone", str(origin), str(seed))
        run("git", "config", "user.name", "AI-SDLC Test", cwd=seed)
        run("git", "config", "user.email", "test@example.invalid", cwd=seed)
        (seed / "README.md").write_text("seed\n", encoding="utf-8")
        run("git", "add", "README.md", cwd=seed)
        run("git", "commit", "-m", "seed", cwd=seed)
        run("git", "branch", "-M", "main", cwd=seed)
        run("git", "push", "-u", "origin", "main", cwd=seed)
        run("git", "checkout", "-b", "feature/concurrency", cwd=seed)
        run("git", "push", "-u", "origin", "feature/concurrency", cwd=seed)

        run("git", "clone", "--branch", "feature/concurrency", str(origin), str(worker_a))
        run("git", "clone", "--branch", "feature/concurrency", str(origin), str(worker_b))
        for worker in (worker_a, worker_b):
            run("git", "config", "user.name", "AI-SDLC Test", cwd=worker)
            run("git", "config", "user.email", "test@example.invalid", cwd=worker)

        ready = verify_write_precondition(worker_a, "feature/concurrency", "main")
        require(ready["outcome"] == "READY", f"fresh workspace unexpectedly rejected: {ready}")

        default_denied = verify_write_precondition(worker_a, "main", "main")
        require(default_denied["outcome"] == "INVALID", "default branch write unexpectedly allowed")

        (worker_a / "state.txt").write_text("worker a\n", encoding="utf-8")
        run("git", "add", "state.txt", cwd=worker_a)
        run("git", "commit", "-m", "advance remote", cwd=worker_a)
        run("git", "push", "origin", "HEAD:feature/concurrency", cwd=worker_a)

        stale = verify_write_precondition(worker_b, "feature/concurrency", "main")
        require(stale["outcome"] == "STALE", f"stale workspace was not detected: {stale}")
        require(stale["base_sha"] != stale["remote_sha"], "stale result lacks divergent SHAs")

        missing = verify_write_precondition(worker_b, "feature/missing", "main")
        require(missing["outcome"] == "INVALID", "missing target branch unexpectedly passed")

        invalid = verify_write_precondition(worker_b, "bad ref", "main")
        require(invalid["outcome"] == "INVALID", "invalid branch name unexpectedly passed")

    print("Git optimistic write precondition scenarios passed")


if __name__ == "__main__":
    main()
