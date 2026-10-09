#!/usr/bin/env python3
"""Read-only, fixed original detector-input manifest. Never invokes a model."""
import hashlib
import io
import json
import os
import re
import stat
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPOSITORY = "DREAM-XIN/ai-sdlc"
BASE = "https://api.github.com/repos/" + REPOSITORY
RUN = 37927328438
SOURCE = "193d96474529556cc0d805bb9be2b0a96909777b"
REPOSITORY_ID = 1326302284
WORKFLOW = ".github/workflows/ai-sdlc-gh-aw-reviewer-deepseek-v03-release-local.lock.yml"
ARTIFACTS = (
    (11614602643, "activation", 1083418, "473677789b3d60cefe6f821ce470a81570d3d8d46dbd917b6d16f71beaf832e0"),
    (11614239306, "agent", 1064768, "41906db5ed959e854f7466925c9e594f3519fd2e58f11985b0ddbbe98f8b42e5"),
    (11614806653, "agent-output-fallback", 7320, "8aaa58dd2068255f44ce825c677f31a4d9805dcddef34fc3d9c57bfeb9807641"),
)
FILES = {
    "agent_output.json": 6923,
    "aw-prompts/prompt-import-tree.json": 15028,
    "aw-prompts/prompt-template.txt": 10152,
    "aw-prompts/prompt.txt": 4328,
    "aw_info.json": 892,
}

class ManifestError(ValueError):
    pass

def require(value, reason):
    if not value:
        raise ManifestError(reason)

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def reader(token):
    require(isinstance(token, str) and bool(token), "read_authority_missing")
    opener = urllib.request.build_opener(NoRedirect)
    def get(url, authenticated=False, limit=2097152):
        parsed = urllib.parse.urlsplit(url)
        require(parsed.scheme == "https" and not parsed.username and not parsed.password
                and parsed.port in (None, 443), "transport_boundary")
        if authenticated:
            require(parsed.netloc == "api.github.com" and url.startswith(BASE + "/actions/"),
                    "authenticated_destination")
        else:
            require(bool(re.fullmatch(r"productionresultssa[a-z0-9]+\.blob\.core\.windows\.net", parsed.hostname or "")
                         or re.fullmatch(r"[a-z0-9-]+\.actions\.githubusercontent\.com", parsed.hostname or "")),
                    "storage_destination")
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if authenticated:
            headers["Authorization"] = "Bearer " + token
        try:
            with opener.open(urllib.request.Request(url, headers=headers, method="GET"), timeout=30) as response:
                data = response.read(limit + 1)
                require(len(data) <= limit, "response_bound")
                return response.status, data, None
        except urllib.error.HTTPError as error:
            if error.code in (301, 302, 303, 307, 308):
                return error.code, b"", error.headers.get("Location")
            raise ManifestError("http_failure") from None
    return get

def validate_run(get):
    status, data, location = get(BASE + "/actions/runs/" + str(RUN), True)
    require(status == 200 and location is None, "run_response")
    run = json.loads(data)
    require(type(run.get("id")) is int and run["id"] == RUN
            and type(run.get("run_attempt")) is int and run["run_attempt"] == 1
            and run.get("event") == "workflow_dispatch" and run.get("path") == WORKFLOW
            and run.get("head_sha") == SOURCE and run.get("head_branch") == "main"
            and run.get("status") == "completed" and run.get("conclusion") == "failure", "run_binding")
    for field in ("repository", "head_repository"):
        repo = run.get(field, {})
        require(type(repo.get("id")) is int and repo["id"] == REPOSITORY_ID
                and repo.get("full_name") == REPOSITORY, "run_repository")

def fetch_fixed_inputs(read_token):
    get = reader(read_token)
    validate_run(get)
    status, data, location = get(BASE + "/actions/runs/" + str(RUN) + "/artifacts?per_page=100", True)
    require(status == 200 and location is None, "listing_response")
    listing = json.loads(data)
    rows = listing.get("artifacts")
    require(type(listing.get("total_count")) is int and listing["total_count"] == 6
            and isinstance(rows, list) and len(rows) == 6, "listing_complete")
    require(all(isinstance(row, dict) and type(row.get("id")) is int and isinstance(row.get("name"), str) for row in rows),
            "listing_shape")
    require(len({row["id"] for row in rows}) == 6 and len({row["name"] for row in rows}) == 6, "listing_unique")
    contents = {}
    origins = {name: [] for name in FILES}
    artifact_manifest = []
    for artifact_id, artifact_name, archive_size, archive_digest in ARTIFACTS:
        selected = [row for row in rows if row["id"] == artifact_id]
        require(len(selected) == 1, "artifact_identity")
        artifact = selected[0]
        workflow = artifact.get("workflow_run", {})
        require(artifact.get("name") == artifact_name and type(artifact.get("size_in_bytes")) is int
                and artifact["size_in_bytes"] == archive_size and artifact.get("expired") is False
                and artifact.get("digest") == "sha256:" + archive_digest
                and all(type(workflow.get(key)) is int for key in ("id", "repository_id", "head_repository_id"))
                and workflow["id"] == RUN and workflow["repository_id"] == REPOSITORY_ID
                and workflow["head_repository_id"] == REPOSITORY_ID and workflow.get("head_sha") == SOURCE
                and workflow.get("head_branch") == "main", "artifact_binding")
        status, archive_bytes, redirect = get(BASE + "/actions/artifacts/" + str(artifact_id) + "/zip", True)
        for _ in range(3):
            if status == 200:
                break
            require(status in (301, 302, 303, 307, 308) and isinstance(redirect, str), "archive_redirect")
            status, archive_bytes, redirect = get(redirect, False)
        require(status == 200 and len(archive_bytes) == archive_size
                and hashlib.sha256(archive_bytes).hexdigest() == archive_digest, "archive_digest")
        consumed = []
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            entries = archive.infolist()
            require(0 < len(entries) <= 1024 and len({entry.filename for entry in entries}) == len(entries), "zip_count")
            require(sum(entry.file_size for entry in entries) <= 67108864, "zip_total")
            for entry in entries:
                name = entry.filename
                require(isinstance(name, str) and name and not name.startswith("/") and chr(92) not in name
                        and all(part not in ("", ".", "..") for part in name.rstrip("/").split("/"))
                        and not any(ord(char) < 32 or ord(char) == 127 for char in name), "zip_path")
                mode = entry.external_attr >> 16
                require(not entry.flag_bits & 1 and not stat.S_ISLNK(mode)
                        and stat.S_IFMT(mode) in (0, stat.S_IFREG, stat.S_IFDIR)
                        and entry.file_size <= 16777216
                        and entry.file_size <= max(entry.compress_size, 1) * 1000, "zip_member")
                if entry.is_dir():
                    require(entry.file_size == 0, "zip_directory")
                    continue
                require(not name.endswith((".patch", ".bundle"))
                        and "comment-memory" not in name.split("/"), "unexpected_detector_input")
                for expected in FILES:
                    require(name == expected or not name.endswith("/" + expected), "ambiguous_consumed_path")
                if name not in FILES:
                    continue
                require(entry.file_size == FILES[name], "consumed_size")
                value = archive.read(entry)
                require(len(value) == FILES[name], "consumed_length")
                require(name not in contents or contents[name] == value, "consumed_overlap_mismatch")
                contents[name] = value
                origins[name].append(artifact_id)
                consumed.append(name)
        artifact_manifest.append({"id": artifact_id, "name": artifact_name, "size_bytes": archive_size,
                                  "sha256": archive_digest, "consumed_paths": sorted(consumed)})
    require(set(contents) == set(FILES), "consumed_missing")
    validate_run(get)
    manifest = {"schema": "v03-fixed-detector-input-manifest/v1", "repository": REPOSITORY,
                "run_id": RUN, "run_attempt": 1, "source_head": SOURCE, "artifacts": artifact_manifest,
                "patch_files": 0, "files": [
                    {"path": name, "size_bytes": len(contents[name]),
                     "sha256": hashlib.sha256(contents[name]).hexdigest(), "artifact_ids": sorted(origins[name])}
                    for name in sorted(FILES)]}
    manifest["manifest_sha256"] = hashlib.sha256(canonical(manifest)).hexdigest()
    return manifest, contents

if __name__ == "__main__":
    try:
        manifest, _ = fetch_fixed_inputs(os.environ.get("GH_TOKEN", ""))
        print(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    except Exception as error:
        reason = str(error) if isinstance(error, ManifestError) else "manifest_validation_failed"
        print(json.dumps({"schema": "v03-fixed-detector-input-manifest/v1", "error": reason}, sort_keys=True))
        raise SystemExit(1)
