#!/usr/bin/env python3
"""One fixed detector-only diagnostic admission. No repository writes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request
from datetime import datetime, timezone

REPOSITORY = "DREAM-XIN/ai-sdlc"
PR = 581
BRANCH = "diagnose/v03-detector-tool-feedback"
BASE_SHA = "193d96474529556cc0d805bb9be2b0a96909777b"
JOB = "Fixed failed Reviewer detector diagnostic"
WORKFLOW = ".github/workflows/validate-v03-dogfood-live-gate.yml"
HELPER = "scripts/v03_detector_diagnostic_admission.py"
MANIFEST_HELPER = "scripts/v03_detector_input_manifest.py"
CONFIG = "scripts/v03_detector_diagnostic_config.json"
EXECUTION = "scripts/v03_detector_diagnostic_execution.sh"
NATIVE_CONFIG = "scripts/v03_detector_awf_config.json"
PREFIX = "AI-SDLC assistant-issued detector-only admission v1\n"
SCHEMA = "v03-assistant-detector-diagnostic-admission/v1"
AUTHOR = {"id": 33620907, "login": "DREAM-XIN"}
PURPOSE = "diagnostic-only: failed Reviewer 37927328438; no release authority"
ROOT = Path("/tmp/v03-fixed-detector-diagnostic")
INPUT_ROOT = Path("/tmp/gh-aw/threat-detection")
BASE = "https://api.github.com/repos/" + REPOSITORY
SHA40 = re.compile(r"^[0-9a-f]{40}$")

# Exactly one terminal pre-model failure may precede this diagnostic. This is not a retry budget.
PREDECESSOR_RUN = 38011769045
PREDECESSOR_JOB = 114093376527
PREDECESSOR_COMMENT = 6092102097
PREDECESSOR_HEAD = "4e0a2d13ab816e0772f526fe9bd435df9eb52433"
PREDECESSOR_CHECKOUT = "78854123516ee7cf8b98b99f8b520dea4d333123"
PREDECESSOR_TREE = "584d30a13b66fbfb572f3365e9d27fff4ddd3f3c"
PREDECESSOR_BODY_SHA256 = "1804ddf1076e332e4bb36c90101103cb2940eccfc8fb3ac84c9a1fa807a779fe"
PREDECESSOR_AUTHORITY_SHA256 = "d71a00f93965cb2114a66fbc10dc853d3660617fcdaa0f08c11621e9a225d515"
PREDECESSOR_PROOF_SHA256 = "ca292d58409d283a8e71a3c5287a7220be0bd32339b20c6779889f4d0e7becce"
PREDECESSOR_SOURCES = json.loads("{\".github/workflows/validate-v03-dogfood-live-gate.yml\":\"a2ecf4a19b62a976188126b97f971dedb0baa5bc\",\"scripts/v03_detector_awf_config.json\":\"966b092676b2528f741d8f9f74457f93cc3d85b9\",\"scripts/v03_detector_diagnostic_admission.py\":\"aace690d675718989f7f2020f87f19a22a26ffc1\",\"scripts/v03_detector_diagnostic_config.json\":\"8cacb335614004791fdca545bb2304519e3f282c\",\"scripts/v03_detector_diagnostic_execution.sh\":\"cd3af666afd3665f6671382c3efea28db8bca34f\",\"scripts/v03_detector_input_manifest.py\":\"74deb06375b96ac11ea4c3bee58e7427e0d18a12\"}")

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def strict_json(text):
    def unique(pairs):
        result = {}
        for key,value in pairs:
            if key in result:
                raise ValueError("duplicate JSON authority key")
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=unique)


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def read(path):
    if not path.startswith("/") or ".." in path:
        raise ValueError("invalid API path")
    req = urllib.request.Request(BASE + path, headers={
        "Authorization": "Bearer " + os.environ["GH_TOKEN"],
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.build_opener(NoRedirect).open(req, timeout=30) as response:
        raw = response.read(2097153)
        if response.status != 200 or len(raw) > 2097152:
            raise ValueError("bounded API read failed")
    return json.loads(raw)

def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()

def blob(path, checkout):
    if Path(path).is_symlink() or not Path(path).is_file():
        raise ValueError("source path missing or symlink")
    actual = git("hash-object", "--", path)
    pinned = git("rev-parse", checkout + ":" + path)
    if actual != pinned:
        raise ValueError("checked-out diagnostic source changed")
    return pinned

def validate_execution_script():
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result: raise ValueError("duplicate native config key")
            result[key]=value
        return result
    config=json.loads(Path(NATIVE_CONFIG).read_text(),object_pairs_hook=unique)
    cap=config.get("apiProxy",{}).get("maxRuns")
    if type(cap) is not int or cap!=50 or "maxTurns" in config.get("apiProxy",{}):
        raise ValueError("native AWF maxRuns is not exactly 50")
    script=Path(EXECUTION).read_text()
    copy_line='cp -- "scripts/v03_detector_awf_config.json" "${RUNNER_TEMP}/gh-aw/awf-config.json"'
    if (script.splitlines().count(copy_line)!=1
            or sum(line.startswith("awf --config ") for line in script.splitlines())!=1
            or script.count("threat-detect --engine copilot --retries 0 --output ")!=1):
        raise ValueError("authored native config is not consumed once")
    return config

def source_identity():
    validate_execution_script()
    if (os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
            or os.environ.get("GITHUB_EVENT_NAME") != "pull_request"
            or os.environ.get("GITHUB_RUN_ATTEMPT") != "1"
            or os.environ.get("GITHUB_REF") != "refs/pull/581/merge"):
        raise ValueError("diagnostic invocation context differs")
    run_id = int(os.environ["GITHUB_RUN_ID"])
    run = read(f"/actions/runs/{run_id}")
    pr = read(f"/pulls/{PR}")
    checkout = git("rev-parse", "HEAD")
    workflow_sha = os.environ["GITHUB_WORKFLOW_SHA"]
    if (not SHA40.fullmatch(checkout) or checkout != workflow_sha
            or checkout != os.environ["GITHUB_SHA"]
            or checkout != pr.get("merge_commit_sha")
            or pr.get("state") != "open" or pr.get("draft") is not True
            or pr.get("head", {}).get("ref") != BRANCH
            or pr.get("base", {}).get("ref") != "main"
            or pr.get("base", {}).get("sha") != BASE_SHA
            or pr.get("head", {}).get("repo", {}).get("full_name") != REPOSITORY
            or pr.get("base", {}).get("repo", {}).get("full_name") != REPOSITORY
            or type(run.get("id")) is not int or run["id"] != run_id
            or type(run.get("run_attempt")) is not int or run["run_attempt"] != 1
            or run.get("event") != "pull_request" or run.get("status") != "in_progress"
            or run.get("head_sha") != pr.get("head", {}).get("sha")
            or run.get("path") != WORKFLOW
            or run.get("repository", {}).get("full_name") != REPOSITORY
            or os.environ["GITHUB_WORKFLOW_REF"] != REPOSITORY + "/" + WORKFLOW + "@refs/pull/581/merge"):
        raise ValueError("fresh run, workflow, or PR source differs")
    listing = read(f"/actions/runs/{run_id}/attempts/1/jobs?per_page=100")
    jobs = listing.get("jobs")
    if not isinstance(jobs, list) or type(listing.get("total_count")) is not int or listing["total_count"] != len(jobs):
        raise ValueError("job listing incomplete")
    matches = [j for j in jobs if j.get("name") == JOB]
    if len(matches) != 1:
        raise ValueError("diagnostic job is not unique")
    job = matches[0]
    if (type(job.get("id")) is not int or job.get("run_id") != run_id
            or type(job.get("run_id")) is not int
            or type(job.get("run_attempt")) is not int or job["run_attempt"] != 1
            or job.get("head_sha") != run["head_sha"]
            or job.get("status") != "in_progress"):
        raise ValueError("diagnostic job identity differs")
    config = json.loads(Path(CONFIG).read_text())
    if config.get("purpose") != PURPOSE or canonical(config.get("limits")) != canonical({
            "max_runs": 50, "harness_retries": 0, "detector_retries": 0,
            "engine_timeout_seconds": 300, "local_invocations": 1}):
        raise ValueError("diagnostic configuration differs")
    if (blob(EXECUTION,checkout)!=config.get("execution_git_blob")
            or blob(NATIVE_CONFIG,checkout)!=config.get("native_config_git_blob")):
        raise ValueError("compiled execution script differs")
    return {
        "repository": REPOSITORY, "pull_request": PR, "run_id": run_id, "run_attempt": 1,
        "job_id": job["id"], "job_name": JOB, "job_started_at": job["started_at"],
        "pr_head_sha": run["head_sha"], "workflow_sha": workflow_sha,
        "checkout_sha": checkout, "checkout_tree": git("rev-parse", checkout + "^{tree}"),
        "source_blobs": {p: blob(p, checkout) for p in (WORKFLOW, HELPER, MANIFEST_HELPER, CONFIG, EXECUTION, NATIVE_CONFIG)},
        "configuration_sha256": digest(config), "limits": config["limits"],
        "uninvoked_predecessor": verify_predecessor(),
    }


def comment_authority(comment):
    """Compare authority, not endpoint-specific GitHub presentation decorations."""
    if not isinstance(comment, dict) or type(comment.get("id")) is not int or comment["id"] <= 0:
        raise ValueError("comment identity malformed")
    user = comment.get("user")
    if (not isinstance(user, dict) or type(user.get("id")) is not int
            or user["id"] != AUTHOR["id"] or user.get("login") != AUTHOR["login"]
            or comment.get("author_association") != "OWNER"):
        raise ValueError("comment author authority differs")
    comment_id = comment["id"]
    expected_urls = {
        "url": BASE + f"/issues/comments/{comment_id}",
        "html_url": f"https://github.com/{REPOSITORY}/pull/{PR}#issuecomment-{comment_id}",
        "issue_url": BASE + f"/issues/{PR}",
    }
    if any(comment.get(key) != value for key, value in expected_urls.items()):
        raise ValueError("comment repository or URL differs")
    for field in ("node_id", "body", "created_at", "updated_at"):
        if not isinstance(comment.get(field), str) or not comment[field]:
            raise ValueError("comment authority field missing")
    if comment["created_at"] != comment["updated_at"]:
        raise ValueError("comment was edited")
    timestamp(comment["created_at"])
    return dict(expected_urls, id=comment_id, node_id=comment["node_id"],
                user={"id":user["id"],"login":user["login"]},
                author_association=comment["author_association"], body=comment["body"],
                created_at=comment["created_at"], updated_at=comment["updated_at"])

def same_comment_authority(first, second):
    return canonical(comment_authority(first)) == canonical(comment_authority(second))

def predecessor_logs():
    # Reuse the already reviewed bounded, token-stripping GitHub storage transport.
    from v03_detector_input_manifest import reader
    get = reader(os.environ["GH_TOKEN"])
    status, data, redirect = get(BASE + f"/actions/jobs/{PREDECESSOR_JOB}/logs", True, limit=262144)
    for _ in range(3):
        if status == 200:
            break
        if status not in (301,302,303,307,308) or not isinstance(redirect,str):
            raise ValueError("predecessor log redirect differs")
        status, data, redirect = get(redirect, False, limit=262144)
    if status != 200 or redirect is not None or len(data) > 262144:
        raise ValueError("predecessor log unavailable")
    return data.decode("utf-8-sig").replace("\r\n","\n")

def predecessor_log_proof(log):
    if not isinstance(log,str) or len(log.encode()) > 262144:
        raise ValueError("predecessor log bound differs")
    records = {}
    lines = log.splitlines()
    order = []
    for index,line in enumerate(lines):
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{1,9}Z) (\{.*\})",line)
        if match:
            try:
                value = strict_json(match[2])
            except ValueError:
                raise ValueError("predecessor JSON record malformed") from None
            schema = value.get("schema") if isinstance(value,dict) else None
            key = {"v03-detector-admission-request/v1":"request",
                   "v03-fixed-detector-diagnostic-summary/v1":"summary"}.get(schema)
            if isinstance(schema,str) and schema.startswith(("v03-detector-admission-request/","v03-fixed-detector-diagnostic-summary/")) and key is None:
                raise ValueError("unknown predecessor evidence schema")
            if key:
                if key in records:
                    raise ValueError("duplicate predecessor evidence")
                records[key] = {"at":match[1],"value":value}
                order.append((index,key))
        if "fixed detector admission blocked:" in line:
            if "blocked" in records or not line.endswith(" fixed detector admission blocked: ValueError"):
                raise ValueError("predecessor failure boundary differs")
            records["blocked"] = line
            if index+1 >= len(lines) or not lines[index+1].endswith(" ##[error]Process completed with exit code 1."):
                raise ValueError("predecessor invoke exit missing")
            records["exit"] = lines[index+1]
            order.extend(((index,"blocked"),(index+1,"exit")))
    if [key for _,key in sorted(order)] != ["request","blocked","exit","summary"]:
        raise ValueError("predecessor evidence ordering differs")
    if digest(records) != PREDECESSOR_PROOF_SHA256:
        raise ValueError("predecessor source-bound log proof differs")
    summary = records["summary"]["value"]
    expected = {"schema":"v03-fixed-detector-diagnostic-summary/v1",
                "diagnostic_only":True,"release_authority":False,
                "invocation_consumed":False,"scanner_outcome":"failure",
                "verdict_valid":False,"diagnostic_success":False}
    if canonical(summary) != canonical(expected):
        raise ValueError("predecessor may have invoked scanner")
    return records

def predecessor_metadata():
    run = read(f"/actions/runs/{PREDECESSOR_RUN}")
    expected = {"id":PREDECESSOR_RUN,"run_attempt":1,"status":"completed","conclusion":"failure",
                "head_sha":PREDECESSOR_HEAD,"head_branch":BRANCH,"event":"pull_request","path":WORKFLOW}
    if canonical({key:run.get(key) for key in expected}) != canonical(expected):
        raise ValueError("predecessor terminal run differs")
    if run.get("repository",{}).get("full_name") != REPOSITORY:
        raise ValueError("predecessor repository differs")
    listing = read(f"/actions/runs/{PREDECESSOR_RUN}/attempts/1/jobs?per_page=100")
    jobs = listing.get("jobs")
    if (not isinstance(jobs,list) or type(listing.get("total_count")) is not int
            or listing["total_count"] != len(jobs)):
        raise ValueError("predecessor jobs listing incomplete")
    selected = [job for job in jobs if job.get("name") == JOB or job.get("id") == PREDECESSOR_JOB]
    if len(selected) != 1:
        raise ValueError("predecessor scanner job ambiguous")
    job = selected[0]
    fields = dict(id=PREDECESSOR_JOB,run_id=PREDECESSOR_RUN,run_attempt=1,
                  head_sha=PREDECESSOR_HEAD,name=JOB,status="completed",conclusion="failure",
                  started_at="2026-10-10T01:06:17Z",completed_at="2026-10-10T01:19:49Z")
    if canonical({key:job.get(key) for key in fields}) != canonical(fields):
        raise ValueError("predecessor job differs")
    steps = job.get("steps")
    expected_steps = [
        (15,"Wait for one exact assistant-issued diagnostic admission","success","2026-10-10T01:06:59Z","2026-10-10T01:19:40Z"),
        (16,"Invoke admitted detector once with private output","success","2026-10-10T01:19:40Z","2026-10-10T01:19:47Z"),
        (17,"Parse pinned verdict privately and report bounded diagnostic result","failure","2026-10-10T01:19:47Z","2026-10-10T01:19:47Z"),
    ]
    if not isinstance(steps,list):
        raise ValueError("predecessor steps missing")
    selected_steps=[]
    for number,name,conclusion,started,completed in expected_steps:
        matches=[step for step in steps if step.get("number")==number or step.get("name")==name]
        expected_step=dict(number=number,name=name,status="completed",conclusion=conclusion,
                           started_at=started,completed_at=completed)
        if len(matches)!=1 or canonical(matches[0])!=canonical(expected_step):
            raise ValueError("predecessor step boundary differs")
        selected_steps.append(expected_step)
    return {"run":expected,"job":fields,"steps":selected_steps}

def verify_predecessor():
    before = predecessor_metadata()
    old_comment = read(f"/issues/comments/{PREDECESSOR_COMMENT}")
    authority = comment_authority(old_comment)
    if (digest(authority) != PREDECESSOR_AUTHORITY_SHA256
            or authority["id"] != PREDECESSOR_COMMENT
            or hashlib.sha256(authority["body"].encode()).hexdigest()!=PREDECESSOR_BODY_SHA256):
        raise ValueError("retained predecessor admission changed")
    old_marker=strict_json(authority["body"][len(PREFIX):])
    commit=read(f"/git/commits/{PREDECESSOR_CHECKOUT}")
    if commit.get("sha")!=PREDECESSOR_CHECKOUT or commit.get("tree",{}).get("sha")!=PREDECESSOR_TREE:
        raise ValueError("predecessor source commit differs")
    tree=read(f"/git/trees/{PREDECESSOR_TREE}?recursive=1")
    rows=tree.get("tree")
    if tree.get("sha")!=PREDECESSOR_TREE or tree.get("truncated") is not False or not isinstance(rows,list):
        raise ValueError("predecessor source tree incomplete")
    for path,sha in PREDECESSOR_SOURCES.items():
        matches=[row for row in rows if row.get("path")==path]
        if len(matches)!=1 or matches[0].get("type")!="blob" or matches[0].get("sha")!=sha:
            raise ValueError("predecessor executed source differs")
    proof=predecessor_log_proof(predecessor_logs())
    request=proof["request"]["value"]
    expected_marker=dict(request["identity"],schema=SCHEMA,purpose=PURPOSE,assistant_issued=True,
                         input_manifest=request["input_manifest"],expires_at="2026-10-10T01:26:00Z")
    if canonical(old_marker)!=canonical(expected_marker):
        raise ValueError("predecessor admission/request binding differs")
    after=predecessor_metadata()
    direct=read(f"/issues/comments/{PREDECESSOR_COMMENT}")
    if canonical(before)!=canonical(after) or not same_comment_authority(old_comment,direct):
        raise ValueError("predecessor changed during proof read")
    return {"schema":"v03-fixed-uninvoked-predecessor/v1","run_id":PREDECESSOR_RUN,
            "run_attempt":1,"job_id":PREDECESSOR_JOB,"comment_id":PREDECESSOR_COMMENT,
            "source_head":PREDECESSOR_HEAD,"checkout_sha":PREDECESSOR_CHECKOUT,
            "checkout_tree":PREDECESSOR_TREE,"source_blobs":PREDECESSOR_SOURCES,
            "marker_body_sha256":PREDECESSOR_BODY_SHA256,"marker_authority_sha256":PREDECESSOR_AUTHORITY_SHA256,"normalized_log_proof_sha256":PREDECESSOR_PROOF_SHA256,
            "summary":proof["summary"]["value"]}


def marker(identity, manifest, now, wait_deadline):
    matches = []
    predecessor_count = 0
    for page in range(1, 21):
        rows = read(f"/issues/{PR}/comments?per_page=100&page={page}")
        if not isinstance(rows, list):
            raise ValueError("comment listing malformed")
        for comment in rows:
            body = comment.get("body", "")
            if not isinstance(body, str) or not body.startswith(PREFIX):
                continue
            authority = comment_authority(comment)
            if authority["id"] == PREDECESSOR_COMMENT:
                if digest(authority) != PREDECESSOR_AUTHORITY_SHA256 or hashlib.sha256(body.encode()).hexdigest() != PREDECESSOR_BODY_SHA256:
                    raise ValueError("retained predecessor marker differs")
                predecessor_count += 1
                continue
            try:
                value = strict_json(body[len(PREFIX):])
            except (ValueError, TypeError):
                raise ValueError("malformed diagnostic marker") from None
            if not isinstance(value, dict):
                raise ValueError("malformed diagnostic marker")
            if value.get("run_id") != identity["run_id"]:
                raise ValueError("fixed diagnostic already names another run")
            if (comment.get("user", {}).get("id") != AUTHOR["id"]
                    or comment.get("user", {}).get("login") != AUTHOR["login"]
                    or comment.get("created_at") != comment.get("updated_at")):
                raise ValueError("marker author or edit history differs")
            expected = dict(identity, schema=SCHEMA, purpose=PURPOSE, assistant_issued=True,
                            input_manifest=manifest)
            if set(value) != set(expected) | {"expires_at"} or canonical({k:value[k] for k in expected}) != canonical(expected):
                raise ValueError("marker binding differs")
            now = time.time()
            expires = timestamp(value["expires_at"])
            if (not now < expires <= wait_deadline
                    or not timestamp(identity["job_started_at"]) <= timestamp(comment["created_at"]) <= now):
                raise ValueError("marker expired or outside job window")
            matches.append((comment, value))
        if len(rows) < 100:
            break
    else:
        raise ValueError("comment listing exceeded exhaustive bound")
    if predecessor_count != 1:
        raise ValueError("retained predecessor admission absent or duplicated")
    if len(matches) > 1:
        raise ValueError("duplicate diagnostic admission")
    return matches[0] if matches else None

def inspect_inputs():
    from v03_detector_input_manifest import fetch_fixed_inputs
    manifest, source_contents, contents = fetch_fixed_inputs(os.environ["GH_TOKEN"],
        actions_root=Path(os.environ["RUNNER_TEMP"])/"gh-aw/actions")
    if len(contents) != 5 or len(source_contents) != 5:
        raise ValueError("input file count differs")
    return manifest, source_contents, contents

def prepare():
    source_identity()
    manifest,contents,_=inspect_inputs()
    expected={"agent_output.json","aw_info.json","aw-prompts/prompt.txt",
              "aw-prompts/prompt-template.txt","aw-prompts/prompt-import-tree.json"}
    if set(contents)!=expected:
        raise ValueError("fixed input destinations differ")
    ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    if ROOT.is_symlink():
        raise ValueError("private directory is a symlink")
    for relative,data in contents.items():
        target=Path("/tmp/gh-aw")/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.is_symlink() or any(p.is_symlink() for p in target.parents):
            raise ValueError("input destination symlink")
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,"wb") as out:
            out.write(data)
        os.chmod(target,0o600)
    print(json.dumps(manifest,sort_keys=True))

def wait():
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    if ROOT.is_symlink():
        raise ValueError("diagnostic private directory is a symlink")
    fd=os.open(ROOT/"wait-started",os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    os.close(fd)
    identity = source_identity()
    manifest, _, _ = inspect_inputs()
    started = time.time()
    deadline = timestamp(identity["job_started_at"]) + 1200
    if deadline <= started:
        raise ValueError("job lacks admission window")
    print(json.dumps({"schema":"v03-detector-admission-request/v1", "identity":identity,
                      "input_manifest":manifest, "marker_prefix":PREFIX, "marker_schema":SCHEMA,
                      "purpose":PURPOSE, "assistant_issued":True,
                      "wait_deadline":datetime.fromtimestamp(deadline,timezone.utc).isoformat()}), flush=True)
    while time.time() < deadline:
        current = source_identity()
        if current != identity:
            raise ValueError("source changed while awaiting admission")
        found = marker(identity, manifest, time.time(), deadline)
        if found:
            comment, value = found
            saved = {"identity":identity, "manifest":manifest, "comment_id":comment["id"],
                     "body_sha256":hashlib.sha256(comment["body"].encode()).hexdigest(),
                     "marker":value, "wait_deadline":deadline}
            (ROOT/"admission.json").write_bytes(canonical(saved))
            return
        time.sleep(15)
    raise ValueError("admission absent before bounded deadline")

def invoke():
    saved = json.loads((ROOT/"admission.json").read_text())
    identity = source_identity()
    manifest, _, contents = inspect_inputs()
    if identity != saved["identity"] or manifest != saved["manifest"]:
        raise ValueError("fresh source or immutable input manifest differs")
    found = marker(identity, manifest, time.time(), saved["wait_deadline"])
    if not found:
        raise ValueError("admission deleted")
    comment, value = found
    direct = read(f"/issues/comments/{saved['comment_id']}")
    if (comment["id"] != saved["comment_id"] or not same_comment_authority(direct, comment)
            or hashlib.sha256(comment["body"].encode()).hexdigest() != saved["body_sha256"]
            or canonical(value) != canonical(saved["marker"])):
        raise ValueError("admission changed")
    # Exact input destinations and immutable hashes are enforced by the manifest helper.
    input_root = INPUT_ROOT
    if input_root.is_symlink() or any(p.is_symlink() for p in input_root.parents):
        raise ValueError("detector root or parent is a symlink")
    expected_inputs = set(contents)
    actual_inputs = {p.relative_to(input_root).as_posix() for p in input_root.rglob("*") if p.is_file()}
    if actual_inputs != expected_inputs | {"execution.json", "detection.log"}:
        raise ValueError("unexpected detector input members")
    if any(p.is_symlink() for p in input_root.rglob("*")):
        raise ValueError("symlink in detector input root")
    expected_execution = {"version":1,"component":"detection","run_id":identity["run_id"],
                          "run_attempt":1,"state":"not_started"}
    if canonical(json.loads((input_root/"execution.json").read_text())) != canonical(expected_execution):
        raise ValueError("detector already started or execution evidence differs")
    if (input_root/"detection.log").stat().st_size != 0:
        raise ValueError("detector log was not initially empty")
    for relative, data in contents.items():
        target = INPUT_ROOT / relative
        if target.is_symlink() or not target.is_file() or target.read_bytes() != data:
            raise ValueError("prepared detector input bytes differ")
    if time.time() + 300 > timestamp(identity["job_started_at"]) + 1800:
        raise ValueError("job lacks full fixed scanner window")
    fd = os.open(ROOT/"consumed", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(fd, b"one diagnostic invocation\n")
    os.close(fd)
    # Recheck provider attempt/source immediately before entering the pinned execution script.
    if source_identity() != identity:
        raise ValueError("run changed immediately before scanner")
    if not time.time() < timestamp(value["expires_at"]) <= saved["wait_deadline"]:
        raise ValueError("admission expired immediately before scanner")
    config=json.loads(Path(CONFIG).read_text())
    if any(os.environ.get(k)!=v for k,v in config["fixed_execution_env"].items()):
        raise ValueError("actual scanner configuration differs")
    if not os.environ.get("COPILOT_PROVIDER_API_KEY"):
        raise ValueError("existing scanner provider credential unavailable")
    env = dict(os.environ)
    env.pop("GH_TOKEN", None)
    for variable in ("GITHUB_OUTPUT","GITHUB_ENV","GITHUB_STEP_SUMMARY","GITHUB_PATH","GITHUB_STATE"):
        path=ROOT/("scanner-"+variable.lower())
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); os.close(fd)
        env[variable]=str(path)
    capture = os.open(ROOT/"scanner-private.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.dup2(capture, 1); os.dup2(capture, 2); os.close(capture)
    os.execve("/bin/bash", ["bash", "-e", "-o", "pipefail", EXECUTION], env)

def summarize():
    result = Path("/tmp/gh-aw/threat-detection/detection_result.json")
    status = {"schema":"v03-fixed-detector-diagnostic-summary/v1",
              "diagnostic_only":True, "release_authority":False,
              "invocation_consumed":(ROOT/"consumed").is_file(),
              "scanner_outcome":os.environ.get("SCANNER_OUTCOME", "unknown"),
              "verdict_valid":False, "diagnostic_success":False}
    if status["scanner_outcome"] not in {"success","failure","skipped","cancelled","unknown"}:
        raise ValueError("runner scanner outcome differs")
    if not status["invocation_consumed"]:
        print(json.dumps(status,sort_keys=True))
        raise SystemExit(1)
    wrapper = Path(os.environ["RUNNER_TEMP"])/"gh-aw/actions/conclude_threat_detection.sh"
    raw = wrapper.read_bytes()
    if hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\x00"+raw).hexdigest() != "c72df00b31d59b67968c6e578bf069c616b0421e":
        raise ValueError("official conclusion wrapper differs")
    import shutil
    binary = Path(shutil.which("threat-detect") or "")
    if not binary.is_file() or hashlib.sha256(binary.read_bytes()).hexdigest() != "b4ecda6a8f1ee09913c40b58e5e9d3337d2173618d41b1bfdef9207e4e7959b9":
        raise ValueError("pinned conclusion binary differs")
    env = dict(os.environ)
    env.pop("GH_TOKEN",None)
    env.update(RUN_DETECTION="true", GH_AW_DETECTION_CONTINUE_ON_ERROR="false",
               DETECTION_AGENTIC_EXECUTION_OUTCOME=status["scanner_outcome"],
               THREAT_DETECT_INSTALL_OUTCOME=os.environ.get("INSTALL_OUTCOME","unknown"))
    for variable,name in (("GITHUB_OUTPUT","conclusion.outputs"),("GITHUB_ENV","conclusion.env"),
                          ("GITHUB_STEP_SUMMARY","conclusion.summary")):
        path=ROOT/name
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); os.close(fd)
        env[variable]=str(path)
    capture=os.open(ROOT/"conclusion-private.log",os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(capture,"wb") as log:
        completed=subprocess.run(["bash",str(wrapper),str(result)],env=env,
                                 stdout=log,stderr=log,timeout=30,check=False)
    outputs={}
    for line in (ROOT/"conclusion.outputs").read_text().splitlines():
        key,separator,value=line.partition("=")
        if not separator or key not in {"success","conclusion","reason"} or key in outputs:
            raise ValueError("official conclusion output shape differs")
        outputs[key]=value
    if set(outputs)!={"success","conclusion","reason"}:
        raise ValueError("official conclusion outputs incomplete")
    if outputs["success"] not in {"true","false"} or outputs["conclusion"] not in {"success","failure","warning","skipped"}:
        raise ValueError("unknown official conclusion value")
    status["parser_exit_code"]=completed.returncode
    status["conclusion"]=outputs["conclusion"] if outputs["conclusion"] in {
        "success","failure","warning","skipped"} else "unknown"
    status["parser_success"]=outputs["success"]=="true"
    if result.is_file() and not result.is_symlink() and result.stat().st_size<=1048576:
        parsed=json.loads(result.read_text())
        fields=("prompt_injection","secret_leak","malicious_patch")
        valid=all(type(parsed.get(k)) is bool for k in fields)
        valid=valid and ((completed.returncode==0 and outputs=={
            "success":"true","conclusion":"success","reason":""}) or
            (completed.returncode==1 and outputs["reason"]=="threat_detected"
             and outputs["conclusion"]=="failure" and outputs["success"]=="false"))
        if valid:
            status["verdict_valid"]=True
            status["threats"]={k:parsed[k] for k in fields}
            status["warning_count"]=len(parsed.get("warnings",[]))
    status["diagnostic_success"]=(status["verdict_valid"] and status["scanner_outcome"]=="success"
        and os.environ.get("INSTALL_OUTCOME")=="success" and completed.returncode==0
        and status["conclusion"]=="success" and status["parser_success"])
    print(json.dumps(status,sort_keys=True))
    (ROOT/"summary.json").write_bytes(canonical(status))
    if not status["diagnostic_success"]:
        raise SystemExit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare","wait","invoke","summarize"))
    args = parser.parse_args()
    try:
        {"prepare":prepare,"wait":wait,"invoke":invoke,"summarize":summarize}[args.command]()
    except Exception as exc:
        raise SystemExit("fixed detector admission blocked: " + type(exc).__name__) from None
