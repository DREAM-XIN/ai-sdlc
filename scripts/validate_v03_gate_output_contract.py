#!/usr/bin/env python3
"""Offline checks using pinned official ingestion and comment publication."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import yaml
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import v03_dogfood_gate_output as subject
PINS = {
    "add_comment.cjs": "71d24f71eebf18af249c967dc0130a11bad9f0c1",
    "collect_ndjson_output.cjs": "c1e6a67e0703fa123275d04e46f3708bd2e4b5db",
    "generate_footer.cjs": "7c1413d7c6422a456e2f505a67fcb8b2c06dd090",
    "messages_footer.cjs": "8f6314855b85995d4f3cf82da36b1b72db1d7575",
    "safe_output_type_validator.cjs": "57af50a8a7555109ef341f9ca6bbd1058a443e77",
    "sanitize_content.cjs": "ddf832a66f6ebb02dcee0644c9857f0333229914",
    "sanitize_content_core.cjs": "50c228a9a83eeb96e46a2c859c9b5fab765663af",
}
SENTINEL = "AI-SDLC structured Gate recommendation."

def packed(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

def check(value, label):
    if not value:
        raise AssertionError(label)

def reject(call, label):
    try:
        call()
    except subject.GateOutputContractError:
        return
    raise AssertionError("accepted " + label)

def selected_config(role, candidate=577):
    stem = f"ai-sdlc-gh-aw-{role}-deepseek-v03-structured-local"
    lock = yaml.safe_load((ROOT / ".github/workflows" / (stem + ".lock.yml")).read_text())
    steps = lock["jobs"]["agent"]["steps"]
    def unique_env(name):
        values = [step["env"][name] for step in steps if name in step.get("env", {})]
        check(len(values) == 1, "ambiguous compiled " + name)
        return values[0]
    config_text = unique_env("GH_AW_SAFE_OUTPUTS_CONFIG")
    config_text = config_text.replace("${GH_AW_INPUT_CANDIDATE_PR_NUMBER}", str(candidate))
    check("${" not in config_text, "unresolved Safe Outputs config")
    config = json.loads(config_text)
    validation = json.loads(unique_env("GH_AW_VALIDATION_JSON"))
    safe = lock["jobs"]["safe_outputs"]
    substitutions = {
        "${{ github.repository }}": "dream-xin/ai-sdlc",
        "${{ github.server_url }}": "https://github.com",
        "${{ github.ref_name }}": "main",
        "${{ needs.detection.outputs.detection_conclusion }}": "success",
        "${{ needs.detection.outputs.detection_reason }}": "",
    }
    names = ("GH_AW_CALLER_WORKFLOW_ID", "GH_AW_DETECTION_CONCLUSION",
             "GH_AW_DETECTION_REASON", "GH_AW_ENGINE_ID", "GH_AW_ENGINE_MODEL",
             "GH_AW_WORKFLOW_ID", "GH_AW_WORKFLOW_NAME", "GH_AW_WORKFLOW_SOURCE_URL")
    metadata = {}
    for name in names:
        value = str(safe["env"][name])
        for key, replacement in substitutions.items():
            value = value.replace(key, replacement)
        check("${" not in value, "unresolved compiled metadata")
        metadata[name] = value
    ingest = [step for step in steps if step.get("id") == "collect_output"]
    check(len(ingest) == 1 and "collect_ndjson_output.cjs" in ingest[0]["with"]["script"],
          "actual official ingestion missing")
    metadata["GH_AW_ALLOWED_DOMAINS"] = ingest[0]["env"]["GH_AW_ALLOWED_DOMAINS"]
    render_steps = [step for step in steps if step.get("id") == "gate_render"]
    handlers = [step for step in safe["steps"] if step.get("id") == "process_safe_outputs"]
    check(len(render_steps) == len(handlers) == 1, "missing render/handler policy")
    for name in ("GH_AW_ALLOWED_DOMAINS", "GH_AW_SAFE_OUTPUTS_URLS"):
        before = render_steps[0].get("env", {}).get(name)
        after = handlers[0].get("env", {}).get(name)
        check(before == after, "render and publication sanitizer policy differs: " + name)
        if after is not None:
            check("${" not in str(after), "unresolved sanitizer policy")
            metadata[name] = str(after)

    return lock, config, validation, metadata

class Official:
    def __init__(self, actions_root, fixture_root, config, validation, metadata):
        self.actions = actions_root
        self.config = config
        self.validation = validation
        self.env = {
            "PATH": os.environ["PATH"],
            "HOME": str(fixture_root / "home"),
            "RUNNER_TEMP": str(fixture_root),
            "GITHUB_REPOSITORY": "dream-xin/ai-sdlc",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_API_URL": "https://api.github.com",
            "GITHUB_RUN_ID": "9001", "GITHUB_RUN_ATTEMPT": "1",
            "GH_AW_SAFE_OUTPUTS": str(fixture_root / "safeoutputs.jsonl"),
            "GH_AW_SAFE_OUTPUTS_CONFIG_PATH": str(fixture_root / "config.json"),
            "GH_AW_VALIDATION_CONFIG_PATH": str(fixture_root / "validation.json"),
            **metadata,
        }
        (fixture_root / "home").mkdir()
    def call(self, request):
        completed = subprocess.run(
            ["node", "-e", NODE_HARNESS, str(self.actions)],
            input=packed(request), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self.env, timeout=30, check=False,
        )
        check(completed.returncode == 0, "pinned official module execution failed")
        return json.loads(completed.stdout)
    def ingest(self, raw):
        return self.call({"mode": "ingest", "raw": raw.decode(),
                          "config": self.config, "validation": self.validation})["collected"].encode()
    def publish(self, output):
        return self.call({"mode": "publish", "item": json.loads(output)["items"][0],
                          "config": self.config})

def role_payload(role, verdict):
    value = {
        "version": "0.1.0", "contract": f"ai-sdlc-gh-aw-{role}-result-v0.1",
        "id": f"structured-{role}-{verdict.lower()}",
        "feature_id": "F-OPERATOR-V03-DOGFOOD-HAPPY-0001",
        "task_id": f"fixture-{role}-task",
        "stage": "code-review" if role == "reviewer" else "verification",
        "role": role, "expected_revision": 2, "target_repository": "dream-xin/ai-sdlc",
        "target_ref": "dogfood/v0.3-happy-path-0001", "candidate_pr_number": 577,
        "candidate_head_sha": "a" * 40, "verdict": verdict,
        "evidence": [{"id": "candidate", "type": "review" if role == "reviewer" else "verification",
                      "status": "pass" if verdict == "PASS" else ("fail" if verdict in ("REWORK", "FAIL") else "warning"),
                      "uri": "https://github.com/dream-xin/ai-sdlc/pull/577"}],
        "occurred_at": "2026-10-10T00:00:00Z",
    }
    if role == "reviewer":
        value["findings"] = ([{"code": "FIX_VALIDATION", "severity": "MAJOR",
                              "message": "Add the missing candidate input validation before approval."}]
                             if verdict == "REWORK" else [])
    else:
        status = {"PASS": "pass", "FAIL": "fail", "BLOCKED": "blocked"}[verdict]
        value["checks"] = [{"name": "candidate verification", "status": status}]
        value["coverage"] = [{"criterion": "assigned verification", "status": status}]
    if verdict != "PASS":
        value["reason"] = "Synthetic evidence requires a non-PASS recommendation."
    return value

def raw_item(payload):
    return packed({"type": "add_comment", "body": SENTINEL, "item_number": payload["candidate_pr_number"], "data": payload}) + b"\n"

def fixture_context(payload):
    fields = ("feature_id", "task_id", "role", "stage", "expected_revision",
              "target_repository", "target_ref", "candidate_pr_number", "candidate_head_sha")
    identity = {key: payload[key] for key in fields}
    identity.update(operation_id="op-" + "1" * 40, operation_generation=1,
                    external_dispatch_key="dispatch-" + "b" * 40,
                    semantic_effect_key="2" * 64, dispatch_id="dispatch-fixture")
    kinds = ["approved_task", "candidate_document", "implementation"]
    if payload["role"] == "qa":
        kinds.append("review")
    documents = []
    for kind in kinds:
        content = "Synthetic " + kind + " evidence."
        documents.append({
            "kind": kind, "uri": "https://github.com/dream-xin/ai-sdlc/pull/577#" + kind,
            "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "source_head_sha": "a" * 40,
            "run_id": None if kind in ("approved_task", "candidate_document") else 9000,
            "receipt_sha256": None if kind in ("approved_task", "candidate_document") else "c" * 64,
        })
    context = {
        "schema_version": "ai-sdlc.v03-gate-context/v1",
        "identity": identity,
        "provenance": {"store_commit_sha": "d" * 40, "producer_source_sha": "e" * 40,
                       "producer_policy_digest": "f" * 64},
        "documents": documents,
    }
    context["context_sha256"] = hashlib.sha256(canonical(context)).hexdigest()
    return context

NODE_HARNESS = r'''
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const request = JSON.parse(fs.readFileSync(0, 'utf8'));
const actions = process.argv[1];
const candidate = Number(request.config?.add_comment?.target || 577);
assert.ok(Number.isSafeInteger(candidate) && candidate > 0);
const outputs = {};
const posts = [];
global.core = {
  info() {}, warning() {}, error() {}, debug() {},
  startGroup() {}, endGroup() {},
  setOutput(k,v) { outputs[k] = v; },
  exportVariable(k,v) { process.env[k] = String(v); },
  setFailed() { throw Error('OFFICIAL_MODULE_FAILED'); },
  getInput() { return ''; },
  isDebug() { return false; }
};
global.context = {
  repo: {owner:'dream-xin',repo:'ai-sdlc'},
  payload: {inputs:{}}, eventName:'workflow_dispatch',
  serverUrl:'https://github.com', apiUrl:'https://api.github.com',
  runId:9001, runNumber:1, actor:'fixture-user',
  workflow:process.env.GH_AW_WORKFLOW_NAME,
};
global.github = {
  rest: {
    issues: {
      async get() { return {data:{number:candidate,user:{login:'fixture-user',type:'User'},labels:[]}}; },
      async createComment(p) {
        assert.equal(p.owner,'dream-xin'); assert.equal(p.repo,'ai-sdlc');
        assert.equal(p.issue_number,candidate);
        posts.push(p);
        return {data:{id:9002,html_url:'https://github.com/dream-xin/ai-sdlc/pull/'+candidate+'#issuecomment-9002'}};
      }
    }
  }
};
global.getOctokit = () => { throw Error('FORBIDDEN_AUTH_CLIENT'); };
global.fetch = () => { throw Error('FORBIDDEN_NETWORK'); };
(async () => {
  if (request.mode === 'ingest') {
    fs.writeFileSync(process.env.GH_AW_SAFE_OUTPUTS,request.raw,'utf8');
    fs.writeFileSync(process.env.GH_AW_SAFE_OUTPUTS_CONFIG_PATH,JSON.stringify(request.config));
    fs.writeFileSync(process.env.GH_AW_VALIDATION_CONFIG_PATH,JSON.stringify(request.validation));
    await require(path.join(actions,'collect_ndjson_output.cjs')).main();
    const actual = fs.readFileSync('/tmp/gh-aw/agent_output.json','utf8');
    assert.equal(actual,outputs.output);
    process.stdout.write(JSON.stringify({collected:actual}));
  } else if (request.mode === 'sanitize') {
    const {sanitizeContent} = require(path.join(actions,'sanitize_content.cjs'));
    process.stdout.write(JSON.stringify({body:sanitizeContent(request.body)}));
  } else if (request.mode === 'publish') {
    const {sanitizeContent} = require(path.join(actions,'sanitize_content.cjs'));
    const handler = await require(path.join(actions,'add_comment.cjs')).main(request.config.add_comment);
    const result = await handler(request.item);
    assert.equal(result.success,true); assert.equal(posts.length,1);
    const runUrl = 'https://github.com/dream-xin/ai-sdlc/actions/runs/9001';
    const marker = require(path.join(actions,'messages_footer.cjs')).generateXMLMarker(
      process.env.GH_AW_WORKFLOW_NAME,runUrl);
    const caller = require(path.join(actions,'generate_footer.cjs')).generateWorkflowCallIdMarker(
      process.env.GH_AW_CALLER_WORKFLOW_ID);
    process.stdout.write(JSON.stringify({
      body:posts[0].body, sanitized:sanitizeContent(request.item.body),
      suffix:marker+'\n'+caller, posts:posts.length
    }));
  } else {
    throw Error('UNKNOWN_FIXTURE_MODE');
  }
})().catch(() => { process.stderr.write('OFFICIAL_GATE_FIXTURE_FAILED\n'); process.exitCode=1; });
'''

def resolve_actions_root(value):
    base = Path(value).resolve()
    candidates = [base, base / "setup" / "js"]
    found = [directory for directory in candidates if (directory / "sanitize_content.cjs").is_file()]
    check(len(found) == 1, "ambiguous or missing official actions directory")
    for name, expected in PINS.items():
        data = (found[0] / name).read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        check(actual == expected, "official source pin mismatch: " + name)
    return found[0]

def compiled_contract(lock):
    agent = lock["jobs"]["agent"]["steps"]
    safe = lock["jobs"]["safe_outputs"]["steps"]
    def index(steps, identifier):
        found = [i for i, step in enumerate(steps) if step.get("id") == identifier]
        check(len(found) == 1, "missing or duplicate step " + identifier)
        return found[0]
    context_index = index(agent, "gate_context")
    engine_index = index(agent, "agentic_execution")
    ingest_index = index(agent, "collect_output")
    render_index = index(agent, "gate_render")
    check(context_index < engine_index < ingest_index < render_index,
          "context/model/ingest/render ordering")
    uploads = [i for i, step in enumerate(agent)
               if str(step.get("uses", "")).startswith("actions/upload-artifact@")
               and step.get("with", {}).get("name") in ("agent", "agent-output-fallback")]
    check(len(uploads) == 2 and all(render_index < i for i in uploads),
          "render does not precede both actual agent artifacts")
    validate_index = index(safe, "gate_validate")
    process_index = index(safe, "process_safe_outputs")
    check(validate_index + 1 == process_index, "exact byte validation must immediately precede effects")
    guards = [step for step in safe if step.get("name") ==
              "Require first attempt and affirmative detection before Safe Outputs effects"]
    check(len(guards) == 1, "missing effect guard")
    guard = guards[0]
    check(safe.index(guard) < validate_index, "effect guard ordering")
    check(guard["env"]["AGENT_RESULT"] == "${{ needs.agent.result }}", "agent result source drift")
    check(guard["env"]["DETECTION_RESULT"] == "${{ needs.detection.result }}",
          "whole detector job result source drift")
    for step in (agent[context_index], agent[render_index], guard, safe[validate_index], safe[process_index]):
        check(step.get("continue-on-error", False) is False, "critical step ignores failures")
        check(step.get("if") in (None, "success()", "${{ success() }}"),
              "critical step bypasses prior failure")
    # Execute the real generated shell guard; implicit GitHub success semantics
    # then gate the validation/handler steps, whose no-bypass conditions are above.
    base = {"PATH": os.environ["PATH"], "RUN_ATTEMPT": "1", "AGENT_RESULT": "success",
            "DETECTION_SUCCESS": "true", "DETECTION_CONCLUSION": "success", "DETECTION_RESULT": "success"}
    cases = [({}, True)]
    cases += [({"AGENT_RESULT": status}, False) for status in ("failure", "cancelled", "skipped", "")]
    cases += [({"DETECTION_RESULT": status}, False) for status in ("failure", "cancelled", "skipped", "")]
    cases += [({"RUN_ATTEMPT": "2"}, False), ({"DETECTION_SUCCESS": "false"}, False),
              ({"DETECTION_CONCLUSION": "skipped"}, False)]
    for changes, expected in cases:
        completed = subprocess.run(["bash", "-c", guard["run"]], env={**base, **changes},
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        check((completed.returncode == 0) is expected,
              "actual generated effect guard accepted failed renderer/detector/attempt")

def scan_receipt(digest):
    return {"schema_version": "ai-sdlc.v03-gate-scanned-bytes/v1", "run_id": 9001,
            "run_attempt": 1, "workflow_sha": "e" * 40, "sha256": digest}

def scanned_receipt_tests():
    digest = hashlib.sha256(b"synthetic scanned bytes").hexdigest()
    value = scan_receipt(digest)
    def validate(raw):
        return subject.scanned_receipt_digest(
            raw, run_id=9001, run_attempt=1, workflow_sha="e" * 40)
    check(validate(canonical(value)) == digest, "same-run receipt rejected")
    for key, changed in (("run_id", 9002), ("run_id", True), ("run_attempt", 2),
                         ("run_attempt", True), ("workflow_sha", "f" * 40),
                         ("sha256", "bad"), ("schema_version", "foreign")):
        altered = {**value, key: changed}
        reject(lambda altered=altered: validate(canonical(altered)), "receipt " + key)
    reject(lambda: validate(canonical({**value, "extra": 1})), "extra receipt field")
    reject(lambda: validate(b""), "missing receipt")
    reject(lambda: validate(b" " * 4097), "oversize receipt")
    raw = canonical(value).replace(b'"run_id":9001', b'"run_id":9002,"run_id":9001')
    reject(lambda: validate(raw), "duplicate receipt identity")

def detector_digest_contract(lock, temporary):
    steps = lock["jobs"]["detection"]["steps"]
    def unique(rows, key, value):
        found = [(i, step) for i, step in enumerate(rows) if step.get(key) == value]
        check(len(found) == 1, "missing or ambiguous step " + value)
        return found[0]
    prepare, _ = unique(steps, "name", "Prepare threat detection files")
    setup, _ = unique(steps, "name", "Setup threat detection")
    before_index, before = unique(steps, "id", "gate_scan_input")
    engine, _ = unique(steps, "id", "detection_agentic_execution")
    after_index, after = unique(steps, "id", "gate_scanned_digest")
    upload_index, upload = unique(steps, "id", "gate_scan_receipt_upload")
    conclude, _ = unique(steps, "id", "detection_conclusion")
    check(prepare < before_index < setup < engine < after_index < upload_index < conclude,
          "actual detector and receipt ordering differs")
    condition = "success() && steps.detection_guard.outputs.run_detection == 'true'"
    for step, expected_condition in (
        (before, condition),
        (after, condition + " && steps.detection_agentic_execution.outcome == 'success'"),
    ):
        check(step.get("continue-on-error", False) is False, "digest failure ignored")
        check(step.get("if") in (expected_condition, "${{ " + expected_condition + " }}"),
              "digest step bypasses prior failure or detector outcome")
    check(before["env"]["GATE_DIGEST_MODE"] == "before" and
          after["env"]["GATE_DIGEST_MODE"] == "after", "digest mode source drift")
    check(after["env"]["EXPECTED_SCAN_INPUT_SHA256"] ==
          "${{ steps.gate_scan_input.outputs.sha256 }}", "post-scan digest self-binds")
    check(after["env"]["SOURCE_RUN_ID"] == "${{ github.run_id }}" and
          after["env"]["RUN_ATTEMPT"] == "${{ github.run_attempt }}" and
          after["env"]["SOURCE_WORKFLOW_SHA"] == "${{ github.workflow_sha }}",
          "receipt source identity is not bound to current execution")
    name = "ai-sdlc-gate-scanned-bytes-${{ github.run_id }}-attempt-${{ github.run_attempt }}"
    check(upload["uses"] == "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
          "receipt upload action pin differs")
    check(upload["with"]["name"] == name and upload["with"]["overwrite"] is False and
          upload["with"]["archive"] is True, "receipt upload is mutable or misnamed")
    check(upload["with"]["path"] == "${{ runner.temp }}/ai-sdlc-gate-scan-publication/receipt.json",
          "receipt upload path differs")
    effect_steps = lock["jobs"]["safe_outputs"]["steps"]
    directory_index, directory = unique(effect_steps, "id", "gate_scan_receipt_directory")
    download_index, download = unique(effect_steps, "id", "gate_scan_receipt_download")
    validate_index, validate = unique(effect_steps, "id", "gate_validate")
    guard_index, _ = unique(effect_steps, "name",
        "Require first attempt and affirmative detection before Safe Outputs effects")
    check(guard_index < directory_index < download_index < validate_index,
          "receipt effects bypass job/semantic guard")
    check(download["uses"] == "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
          "receipt download action pin differs")
    check(download["with"]["name"] == name and
          download["with"]["path"] == "${{ runner.temp }}/ai-sdlc-gate-scan-receipt",
          "receipt is not exact same-run/attempt artifact")
    check(not ({"run-id", "repository", "github-token", "pattern", "merge-multiple"} &
               set(download["with"])), "receipt download permits alternate source")
    check(validate["env"]["SCANNED_RECEIPT_PATH"] ==
          "${{ runner.temp }}/ai-sdlc-gate-scan-receipt/receipt.json",
          "helper receipt path differs")
    for step in (directory, download):
        check(step.get("continue-on-error", False) is False and
              step.get("if") in (None, "success()", "${{ success() }}"),
              "receipt step bypasses failure")
    path = Path("/tmp/gh-aw/threat-detection/agent_output.json")
    check(not path.exists() and not path.is_symlink(), "unexpected preexisting detector input")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical({"items": [{"type": "add_comment", "body": "synthetic scanned content"}]})
    path.write_bytes(data)
    expected = hashlib.sha256(data).hexdigest()
    output = temporary / "digest-output"
    receipt_path = temporary / "ai-sdlc-gate-scan-publication/receipt.json"
    def run(step, mode, expected_input=None):
        output.write_text("")
        env = {"PATH": os.environ["PATH"], "GITHUB_OUTPUT": str(output), "GATE_DIGEST_MODE": mode,
               "RUNNER_TEMP": str(temporary), "SOURCE_RUN_ID": "9001", "RUN_ATTEMPT": "1",
               "SOURCE_WORKFLOW_SHA": "e" * 40}
        if expected_input is not None:
            env["EXPECTED_SCAN_INPUT_SHA256"] = expected_input
        result = subprocess.run(["bash", "-c", step["run"]], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        return result.returncode, output.read_text()
    try:
        check(run(before, "before") == (0, "sha256=" + expected + "\n"), "actual pre-scan digest failed")
        code, _ = run(after, "after", expected)
        check(code == 0 and json.loads(receipt_path.read_bytes()) == scan_receipt(expected),
              "actual post-scan receipt differs")
        check(subject.scanned_receipt_digest(receipt_path.read_bytes(), run_id=9001,
              run_attempt=1, workflow_sha="e" * 40) == expected, "actual receipt parser rejected")
        receipt_path.unlink()
        receipt_path.parent.rmdir()
        path.write_bytes(data + b"\n")
        code, _ = run(after, "after", expected)
        check(code != 0 and not receipt_path.exists(), "changed scanned bytes produced receipt")
    finally:
        path.unlink()

def cli_validation(official, context, output, should_pass):
    identity = context["identity"]
    task = {"contract": "ai-sdlc-task-v0.1",
            "task": {"id": identity["task_id"], "feature_id": identity["feature_id"],
                     "role": identity["role"]},
            "feature_context": {"id": identity["feature_id"],
                                "repository": identity["target_repository"],
                                "vertical": {**identity, "profile": "vertical-implementation-review-qa/v1"},
                                "gate_context": context}}
    env = {**official.env, "TASK_PAYLOAD": packed(task).decode(),
           "RUN_ATTEMPT": "1", "AGENT_RESULT": "success",
           "DETECTION_SUCCESS": "true", "DETECTION_CONCLUSION": "success",
           "DETECTION_RESULT": "success", "SOURCE_RUN_ID": "9001",
           "SOURCE_WORKFLOW_SHA": "e" * 40}
    mapping = {"ROLE": "role", "DISPATCH_KEY": "external_dispatch_key",
               "FEATURE_ID": "feature_id", "STAGE": "stage",
               "EXPECTED_REVISION": "expected_revision", "TARGET_REPOSITORY": "target_repository",
               "TARGET_REF": "target_ref", "CANDIDATE_PR_NUMBER": "candidate_pr_number",
               "CANDIDATE_HEAD_SHA": "candidate_head_sha"}
    env.update({name: str(identity[key]) for name, key in mapping.items()})
    receipt_path = Path(official.env["RUNNER_TEMP"]) / "ai-sdlc-gate-scan-receipt/receipt.json"
    receipt_path.parent.mkdir(exist_ok=True)
    receipt_path.write_bytes(canonical(scan_receipt(hashlib.sha256(output).hexdigest())))
    env["SCANNED_RECEIPT_PATH"] = str(receipt_path)
    path = Path("/tmp/gh-aw/agent_output.json")
    path.write_bytes(output)
    result = subprocess.run([sys.executable, str(ROOT / "scripts/v03_dogfood_gate_output.py"),
                             "validate"], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=30, check=False)
    check((result.returncode == 0) is should_pass,
          "actual pre-effect CLI accepted failed render or rejected canonical proof")
    check(path.read_bytes() == output, "validation mutated scanned artifact")

    if should_pass:
        for changes in ({"DETECTION_RESULT": "failure"}, {"DETECTION_RESULT": "skipped"},
                        {"SCANNED_RECEIPT_PATH": str(receipt_path.parent / "missing.json")}):
            failed = subprocess.run(
                [sys.executable, str(ROOT / "scripts/v03_dogfood_gate_output.py"), "validate"],
                env={**env, **changes}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            check(failed.returncode != 0, "CLI accepted failed detector or missing receipt")

def sanitizer_closure_tests(actions_root, temporary):
    source = subject.envelope(role_payload("reviewer", "PASS"))
    check(subject._official_sanitizer(actions_root)(source) == source,
          "pinned private sanitizer closure changed canonical body")
    copied = temporary / "altered-actions"
    copied.mkdir()
    for name in subject.SANITIZER_BLOBS:
        (copied / name).write_bytes((actions_root / name).read_bytes())
    core = copied / "sanitize_content_core.cjs"
    core.write_bytes(core.read_bytes() + b"\n// synthetic transitive tamper\n")
    reject(lambda: subject._official_sanitizer(copied),
           "changed transitive sanitizer dependency")
def _roundtrip(official, payload, context, role):
    raw = raw_item(payload)
    collected = official.ingest(raw)
    ingested = json.loads(collected)
    check(not ingested["errors"] and len(ingested["items"]) == 1, "official ingestion failed")
    check(ingested["items"][0]["data"] == payload, "official data semantic drift")
    output, proof = subject.render_gate_output(
        raw, collected, context, role,
        sanitize=lambda value: official.call({"mode": "sanitize", "body": value})["body"])
    cli_validation(official, context, output, True)
    cli_validation(official, context, collected, False)
    check(subject.validate_scanned_output(output, proof, context, role,
                                           scanned_sha256=hashlib.sha256(output).hexdigest()) == payload,
          "scan-input payload drift")
    publication = official.publish(output)
    body = json.loads(output)["items"][0]["body"]
    check(publication["sanitized"] == body, "official sanitizer changed rendered bytes")
    check(publication["body"] == body + "\n\n" + publication["suffix"],
          "official metadata assembly drift")
    check(subject.parse_published_gate(publication["body"], context, role,
                                       publication["suffix"]) == payload,
          "published recommendation drift")
    check("data" not in json.loads(output)["items"][0],
          "renderer left a duplicate data carrier")

    return raw, collected, output, proof, publication, body

def verify_context_roundtrip(role, context, *, root, actions_root):
    """Carry an actual producer-built context through the official output boundary."""
    check(Path(root).resolve() == ROOT, "roundtrip repository root differs")
    subject.validate_context(context)
    check(context["identity"]["role"] == role, "context role differs")
    original = canonical(context)
    payload = role_payload(role, "PASS")
    for key in subject.PAYLOAD_IDENTITY_KEYS:
        payload[key] = context["identity"][key]
    payload["evidence"][0]["uri"] = (
        "https://github.com/dream-xin/ai-sdlc/pull/" + str(payload["candidate_pr_number"]))
    _, config, validation, metadata = selected_config(role, payload["candidate_pr_number"])
    with tempfile.TemporaryDirectory(prefix="v03-produced-gate-") as directory:
        official = Official(resolve_actions_root(actions_root), Path(directory), config, validation, metadata)
        raw, collected, artifact, proof, publication, body = _roundtrip(
            official, payload, context, role)
    check(canonical(context) == original, "roundtrip mutated producer context")
    return {"payload": payload, "raw_ndjson": raw, "collected": collected,
            "artifact": artifact, "proof": proof, "published_body": publication["body"],
            "metadata_suffix": publication["suffix"]}

def exercise_role(role, actions_root, temporary):
    lock, config, validation, metadata = selected_config(role)
    compiled_contract(lock)
    fixture_dir = temporary / role
    fixture_dir.mkdir()
    detector_digest_contract(lock, fixture_dir)
    official = Official(actions_root, fixture_dir, config, validation, metadata)
    def sanitize(body):
        return official.call({"mode": "sanitize", "body": body})["body"]
    def render(raw, collected, context):
        return subject.render_gate_output(raw, collected, context, role, sanitize=sanitize)
    verdicts = ("PASS", "REWORK", "BLOCKED") if role == "reviewer" else ("PASS", "FAIL", "BLOCKED")
    for verdict in verdicts:
        payload = role_payload(role, verdict)
        context = fixture_context(payload)
        raw, collected, output, proof, publication, body = _roundtrip(official, payload, context, role)

        # Exactly the final scanned bytes are accepted; serialization changes are drift.
        reject(lambda: subject.validate_scanned_output(output + b"\n", proof, context, role,
                         scanned_sha256=hashlib.sha256(output).hexdigest()),
               "post-scan byte append")
        altered = json.loads(output)
        altered["items"][0]["body"] += "\nforged"
        reject(lambda: subject.validate_scanned_output(packed(altered), proof, context, role,
                         scanned_sha256=hashlib.sha256(output).hexdigest()),
               "post-scan body mutation")
        bad_proof = copy.deepcopy(proof)
        bad_proof["body_sha256"] = "0" * 64
        reject(lambda: subject.validate_scanned_output(output, bad_proof, context, role,
                         scanned_sha256=hashlib.sha256(output).hexdigest()),
               "proof body digest mutation")

        forged_payload = copy.deepcopy(payload)
        forged_payload["id"] = "coordinated-forgery"
        forged_body = subject.envelope(forged_payload)
        forged_proof = copy.deepcopy(proof)
        forged_proof["payload_sha256"] = hashlib.sha256(canonical(forged_payload)).hexdigest()
        forged_proof["body_sha256"] = hashlib.sha256(forged_body.encode()).hexdigest()
        forged_artifact = json.loads(output)
        forged_artifact["items"][0]["body"] = forged_body
        forged_artifact["gate_render_proof"] = forged_proof
        reject(lambda: subject.validate_scanned_output(
            canonical(forged_artifact), forged_proof, context, role,
            scanned_sha256=hashlib.sha256(output).hexdigest()),
            "coordinated body and proof rehash after scan")
        for suffix_name, changed in (
            ("duplicate official suffix", publication["body"] + "\n\n" + publication["suffix"]),
            ("wrong official run", publication["body"].replace("id: 9001", "id: 9003")),
            ("wrong official model", publication["body"].replace("model: deepseek-chat", "model: foreign")),
            ("wrong caller", publication["body"].replace("gh-aw-workflow-call-id: dream-xin/",
                                                         "gh-aw-workflow-call-id: foreign/")),
            ("body mutation", publication["body"].replace('"verdict": "' + verdict + '"',
                                                          '"verdict": "FORGED"')),
            ("unexpected prefix", "extra\n" + publication["body"]),
        ):
            check(changed != publication["body"], "ineffective mutation " + suffix_name)
            reject(lambda changed=changed: subject.parse_published_gate(
                changed, context, role, publication["suffix"]), suffix_name)

    payload = role_payload(role, "PASS")
    context = fixture_context(payload)
    raw = raw_item(payload)
    collected = official.ingest(raw)

    # Demonstrate real framework behavior, without claiming what any real model emitted.
    legacy = "<!-- AI-SDLC-GATE-RESULT\n" + json.dumps(payload) + "\nAI-SDLC-GATE-RESULT -->\n\nSynthetic summary."
    check(sanitize(legacy) == "Synthetic summary.", "legacy HTML envelope unexpectedly survives")
    legacy_raw = packed({"type": "add_comment", "body": legacy}) + b"\n"
    legacy_collected = official.ingest(legacy_raw)
    reject(lambda: render(legacy_raw, legacy_collected, context), "legacy body without structured data")

    for label, candidate in (
        ("missing item", b""),
        ("duplicate items", raw + raw),
        ("invalid companion", raw + b'{"type":"unknown_output"}\n'),
        ("repair-required JSON", raw.rstrip()[:-1] + b",}\n"),
        ("duplicate JSON key", raw.replace(b'"verdict":"PASS"', b'"verdict":"REWORK","verdict":"PASS"')),
    ):
        candidate_collected = official.ingest(candidate)
        reject(lambda candidate=candidate, candidate_collected=candidate_collected:
               render(candidate, candidate_collected, context), label)

    def reject_payload(candidate, label):
        candidate_raw = raw_item(candidate)
        candidate_collected = official.ingest(candidate_raw)
        reject(lambda: render(candidate_raw, candidate_collected, context), label)
    changed = copy.deepcopy(payload)
    changed["evidence"][0]["uri"] = "https://example.github.io/evidence"
    reject_payload(changed, "official sanitizer changes disallowed evidence URL")
    changed = copy.deepcopy(payload)
    del changed["candidate_head_sha"]
    reject_payload(changed, "missing required identity")
    changed = copy.deepcopy(payload)
    changed["unexpected"] = "field"
    reject_payload(changed, "additional schema field")
    for field, value in {
        "feature_id": "foreign-feature", "task_id": "foreign-task",
        "stage": "verification" if role == "reviewer" else "code-review",
        "role": "qa" if role == "reviewer" else "reviewer",
        "expected_revision": 3, "target_repository": "foreign/repo",
        "target_ref": "foreign/ref", "candidate_pr_number": 578,
        "candidate_head_sha": "b" * 40,
    }.items():
        changed = copy.deepcopy(payload)
        changed[field] = value
        reject_payload(changed, "wrong " + field)
    changed = copy.deepcopy(payload)
    changed["expected_revision"] = True
    reject_payload(changed, "boolean revision")
    changed = copy.deepcopy(payload)
    changed["verdict"] = "REWORK" if role == "qa" else "FAIL"
    reject_payload(changed, "other role verdict")
    changed = copy.deepcopy(payload)
    if role == "reviewer":
        changed["findings"] = [{"code": "BLOCK", "severity": "BLOCKER", "message": "A blocker."}]
    else:
        changed["checks"][0]["status"] = "fail"
    reject_payload(changed, "PASS contradicting findings/checks")
    for label, mutate in (
        ("collected data drift", lambda c: c["items"][0]["data"].update(id="changed")),
        ("collected partial errors", lambda c: c["errors"].append("synthetic ingestion failure")),
        ("collected body drift", lambda c: c["items"][0].update(body="foreign body")),
    ):
        changed = json.loads(collected)
        mutate(changed)
        reject(lambda changed=changed: render(raw, packed(changed), context), label)
    altered_context = copy.deepcopy(context)
    altered_context["documents"][0]["content"] += " changed"
    reject(lambda: render(raw, collected, altered_context), "context document digest drift")

    missing_candidate = copy.deepcopy(context)
    missing_candidate["documents"] = [
        doc for doc in missing_candidate["documents"] if doc["kind"] != "candidate_document"]
    missing_candidate["context_sha256"] = hashlib.sha256(canonical(
        {key: value for key, value in missing_candidate.items() if key != "context_sha256"})).hexdigest()
    reject(lambda: render(raw, collected, missing_candidate), "missing actual candidate document")

    for kind in ("approved_task", "candidate_document"):
        source_mismatch = copy.deepcopy(context)
        next(doc for doc in source_mismatch["documents"] if doc["kind"] == kind)["source_head_sha"] = "b" * 40
        source_mismatch["context_sha256"] = hashlib.sha256(canonical(
            {key: value for key, value in source_mismatch.items() if key != "context_sha256"})).hexdigest()
        reject(lambda source_mismatch=source_mismatch:
               render(raw, collected, source_mismatch), "rehashed wrong source head: " + kind)
    print(role + ": official ingestion, sanitizer, publication and exact parser checks passed")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--actions-root", type=Path, required=True)
    args = parser.parse_args()
    actions_root = resolve_actions_root(args.actions_root)
    scanned_receipt_tests()
    with tempfile.TemporaryDirectory(prefix="v03-gate-output-") as directory:
        sanitizer_closure_tests(actions_root, Path(directory))
        for role in ("reviewer", "qa"):
            exercise_role(role, actions_root, Path(directory))
    print("Structured Gate output contract passed with fake provider boundaries only.")

if __name__ == "__main__":
    main()
