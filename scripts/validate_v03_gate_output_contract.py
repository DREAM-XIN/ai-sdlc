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

OBSERVED_HISTORICAL_SOURCE = "7d7f0b8c92ae06b0091d3620ccb60c71114e6bad"
OBSERVED_HISTORICAL_BLOBS = {
    ".github/workflows/ai-sdlc-gh-aw-qa-deepseek-v03-observed-inline-local.md": "158b4d78ad47f5ceeaf4e198968d0d225fb74f67",
    ".github/workflows/ai-sdlc-gh-aw-qa-deepseek-v03-observed-inline-local.lock.yml": "1f0928c78d98845d000388dce6eef3a347fbf082",
    "scripts/v03_detector_observation.mjs": "0468fd225c9637a629adea8a94545f22f37e302f",
}

def observed_historical_bytes(relative):
    check(relative in OBSERVED_HISTORICAL_BLOBS, "historical observed read escaped fixed source closure")
    raw = subprocess.run(["git", "show", OBSERVED_HISTORICAL_SOURCE + ":" + relative],
                         cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         timeout=30, check=True).stdout
    check(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
          == OBSERVED_HISTORICAL_BLOBS[relative], "historical observed blob differs")
    return raw

QA2_OBSERVED_SOURCE = "461f465ffc6788c612c290c747d1190558a9a833"
QA2_OBSERVED_BLOBS = {
    ".github/workflows/ai-sdlc-gh-aw-qa-deepseek-v03-observed-inline-local.md": "5c577b5073df0e70da17570b261338c91535df6c",
    ".github/workflows/ai-sdlc-gh-aw-qa-deepseek-v03-observed-inline-local.lock.yml": "0752ac822bd7362a666d60a8898d4d748765d4c4",
    "scripts/v03_detector_observation.mjs": "85d792241d3d20b0f1051d01e1054c608bfcabdc",
}

def qa2_observed_bytes(relative):
    check(relative in QA2_OBSERVED_BLOBS, "QA2 read escaped fixed source closure")
    raw = subprocess.run(["git", "show", QA2_OBSERVED_SOURCE + ":" + relative],
                         cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         timeout=30, check=True).stdout
    check(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
          == QA2_OBSERVED_BLOBS[relative], "QA2 observed source blob differs")
    return raw

def roundtrip_workflow_stem(role, variant):
    check(variant in {"structured-local", "structured-inline-local", "observed-inline-local", "observed-qa2-local"},
          "unsupported roundtrip source variant")
    check(not variant.startswith("observed-") or role == "qa", "observed source is QA-only")
    actual = "observed-inline-local" if variant == "observed-qa2-local" else variant
    return f"ai-sdlc-gh-aw-{role}-deepseek-v03-{actual}"

def selected_config(role, candidate=577, *, variant="structured-local"):
    check(variant in ("structured-local", "structured-inline-local", "observed-inline-local", "observed-qa2-local"), "unsupported test workflow variant")
    check(variant not in {"observed-inline-local", "observed-qa2-local"} or role == "qa", "observed workflow is QA-only")
    stem = roundtrip_workflow_stem(role, variant)
    lock = yaml.safe_load(qa2_observed_bytes(".github/workflows/" + stem + ".lock.yml")
                          if variant == "observed-qa2-local" else observed_historical_bytes(".github/workflows/" + stem + ".lock.yml")
                          if variant == "observed-inline-local" else
                          (ROOT / ".github/workflows" / (stem + ".lock.yml")).read_bytes())
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
    def __init__(self, actions_root, fixture_root, config, validation, metadata, *,
                 run_id=9001, comment_id=9002, workflow_sha="e" * 40):
        check(type(run_id) is int and run_id > 0, "invalid fixture run identity")
        check(type(comment_id) is int and comment_id > 0, "invalid fixture comment identity")
        check(isinstance(workflow_sha, str) and len(workflow_sha) == 40
              and all(c in "0123456789abcdef" for c in workflow_sha),
              "invalid fixture workflow source")
        self.run_id = run_id
        self.comment_id = comment_id
        self.workflow_sha = workflow_sha
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
            "GITHUB_RUN_ID": str(run_id), "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_WORKFLOW_SHA": workflow_sha,
            "GH_AW_SAFE_OUTPUTS": str(fixture_root / "safeoutputs.jsonl"),
            "GH_AW_SAFE_OUTPUTS_CONFIG_PATH": str(fixture_root / "config.json"),
            "GH_AW_VALIDATION_CONFIG_PATH": str(fixture_root / "validation.json"),
            **metadata,
        }
        (fixture_root / "home").mkdir()
    def call(self, request):
        completed = subprocess.run(
            ["node", "-e", NODE_HARNESS, str(self.actions)],
            input=packed({**request, "fixture_run_id": self.run_id,
                          "fixture_comment_id": self.comment_id}),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
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
const runId = request.fixture_run_id;
const commentId = request.fixture_comment_id;
assert.ok(Number.isSafeInteger(runId) && runId > 0);
assert.ok(Number.isSafeInteger(commentId) && commentId > 0);
assert.equal(String(runId),process.env.GITHUB_RUN_ID);
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
  runId, runNumber:1, actor:'fixture-user',
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
        return {data:{id:commentId,html_url:'https://github.com/dream-xin/ai-sdlc/pull/'+candidate+'#issuecomment-'+commentId}};
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
    const runUrl = 'https://github.com/dream-xin/ai-sdlc/actions/runs/'+runId;
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

def scan_receipt(digest, *, run_id=9001, workflow_sha="e" * 40):
    return {"schema_version": "ai-sdlc.v03-gate-scanned-bytes/v1", "run_id": run_id,
            "run_attempt": 1, "workflow_sha": workflow_sha, "sha256": digest}

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

def cli_validation(official, context, output, should_pass, *, helper_path=None):
    helper_path = helper_path or ROOT / "scripts/v03_dogfood_gate_output.py"
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
           "DETECTION_RESULT": "success", "SOURCE_RUN_ID": str(official.run_id),
           "SOURCE_WORKFLOW_SHA": official.workflow_sha}
    mapping = {"ROLE": "role", "DISPATCH_KEY": "external_dispatch_key",
               "FEATURE_ID": "feature_id", "STAGE": "stage",
               "EXPECTED_REVISION": "expected_revision", "TARGET_REPOSITORY": "target_repository",
               "TARGET_REF": "target_ref", "CANDIDATE_PR_NUMBER": "candidate_pr_number",
               "CANDIDATE_HEAD_SHA": "candidate_head_sha"}
    env.update({name: str(identity[key]) for name, key in mapping.items()})
    receipt_path = Path(official.env["RUNNER_TEMP"]) / "ai-sdlc-gate-scan-receipt/receipt.json"
    receipt_path.parent.mkdir(exist_ok=True)
    receipt_path.write_bytes(canonical(scan_receipt(
        hashlib.sha256(output).hexdigest(), run_id=official.run_id,
        workflow_sha=official.workflow_sha)))
    env["SCANNED_RECEIPT_PATH"] = str(receipt_path)
    path = Path("/tmp/gh-aw/agent_output.json")
    path.write_bytes(output)
    result = subprocess.run([sys.executable, str(helper_path),
                             "validate"], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=30, check=False)
    check((result.returncode == 0) is should_pass,
          "actual pre-effect CLI accepted failed render or rejected canonical proof")
    check(path.read_bytes() == output, "validation mutated scanned artifact")

    if should_pass:
        for changes in ({"DETECTION_RESULT": "failure"}, {"DETECTION_RESULT": "skipped"},
                        {"SCANNED_RECEIPT_PATH": str(receipt_path.parent / "missing.json")}):
            failed = subprocess.run(
                [sys.executable, str(helper_path), "validate"],
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
def _roundtrip(official, payload, context, role, *, raw_ledger=None, renderer=subject):
    raw = raw_item(payload) if raw_ledger is None else raw_ledger
    collected = official.ingest(raw)
    ingested = json.loads(collected)
    check(not ingested["errors"] and len(ingested["items"]) == 1, "official ingestion failed")
    check(ingested["items"][0]["data"] == payload, "official data semantic drift")
    output, proof = renderer.render_gate_output(
        raw, collected, context, role,
        sanitize=lambda value: official.call({"mode": "sanitize", "body": value})["body"])
    cli_validation(official, context, output, True, helper_path=Path(renderer.__file__))
    cli_validation(official, context, collected, False, helper_path=Path(renderer.__file__))
    check(renderer.validate_scanned_output(output, proof, context, role,
                                           scanned_sha256=hashlib.sha256(output).hexdigest()) == payload,
          "scan-input payload drift")
    publication = official.publish(output)
    body = json.loads(output)["items"][0]["body"]
    check(publication["sanitized"] == body, "official sanitizer changed rendered bytes")
    check(publication["body"] == body + "\n\n" + publication["suffix"],
          "official metadata assembly drift")
    check(renderer.parse_published_gate(publication["body"], context, role,
                                       publication["suffix"]) == payload,
          "published recommendation drift")
    check("data" not in json.loads(output)["items"][0],
          "renderer left a duplicate data carrier")

    return raw, collected, output, proof, publication, body

def roundtrip_source_contract(role, variant, lock, metadata):
    stem = roundtrip_workflow_stem(role, variant)
    check(metadata["GH_AW_WORKFLOW_ID"] == stem, "roundtrip used a different selected workflow")
    check(metadata["GH_AW_CALLER_WORKFLOW_ID"] == "dream-xin/ai-sdlc/" + stem,
          "roundtrip caller footer differs from selected workflow")
    check(metadata["GH_AW_WORKFLOW_SOURCE_URL"] ==
          "https://github.com/dream-xin/ai-sdlc/blob/main/.github/workflows/" + stem + ".md",
          "roundtrip source footer differs")
    if variant == "observed-qa2-local":
        for relative in QA2_OBSERVED_BLOBS:
            qa2_observed_bytes(relative)
        exact_lock = yaml.safe_load(qa2_observed_bytes(".github/workflows/" + stem + ".lock.yml"))
        check(lock == exact_lock, "QA2 actual lock differs from frozen corrected producer")
        detector = lock["jobs"]["detection"]
        execution = [step for step in detector["steps"] if step.get("id") == "detection_agentic_execution"]
        check(len(execution) == 1 and execution[0]["env"]["TMPDIR"] == "/tmp",
              "QA2 detector lost original temp permission baseline")
    if variant == "observed-inline-local":
        check(role == "qa", "observed source is QA-only")
        for relative, expected in (
            (".github/workflows/" + stem + ".md", "158b4d78ad47f5ceeaf4e198968d0d225fb74f67"),
            (".github/workflows/" + stem + ".lock.yml", "1f0928c78d98845d000388dce6eef3a347fbf082"),
            ("scripts/v03_detector_observation.mjs", "0468fd225c9637a629adea8a94545f22f37e302f"),
        ):
            raw = observed_historical_bytes(relative)
            actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            check(actual == expected, "frozen observed source/helper pin differs")
        identifiers = [step.get("id") for step in lock["jobs"]["detection"]["steps"]]
        for required in ("detector_observation_prepare", "detector_observation_observe", "detector_observation_report"):
            check(identifiers.count(required) == 1, "observed detector step absent or ambiguous")


def verify_context_roundtrip(role, context, *, root, actions_root,
                             verdict="PASS", run_id=9001, comment_id=9002,
                             workflow_sha="e" * 40, variant="structured-local"):
    """Carry an actual producer-built context through the official output boundary."""
    check(Path(root).resolve() == ROOT, "roundtrip repository root differs")
    subject.validate_context(context)
    check(context["identity"]["role"] == role, "context role differs")
    original = canonical(context)
    check(variant in {"structured-local", "structured-inline-local", "observed-inline-local", "observed-qa2-local"}, "roundtrip variant differs")
    check(variant not in {"observed-inline-local", "observed-qa2-local"} or role == "qa", "observed roundtrip is QA-only")
    payload = (inline_payload(role, verdict) if variant != "structured-local"
               else role_payload(role, verdict))
    for key in subject.PAYLOAD_IDENTITY_KEYS:
        payload[key] = context["identity"][key]
    payload["evidence"][0]["uri"] = (
        "https://github.com/dream-xin/ai-sdlc/pull/" + str(payload["candidate_pr_number"]))
    if role == "qa" and variant != "structured-local":
        payload["coverage"][0]["evidence"] = payload["evidence"][0]["uri"]
    lock, config, validation, metadata = selected_config(
        role, payload["candidate_pr_number"], variant=variant)
    roundtrip_source_contract(role, variant, lock, metadata)
    with tempfile.TemporaryDirectory(prefix="v03-produced-gate-") as directory:
        official = Official(resolve_actions_root(actions_root), Path(directory), config, validation, metadata,
                            run_id=run_id, comment_id=comment_id, workflow_sha=workflow_sha)
        raw, collected, artifact, proof, publication, body = _roundtrip(
            official, payload, context, role)
    check(canonical(context) == original, "roundtrip mutated producer context")
    return {"payload": payload, "raw_ndjson": raw, "collected": collected,
            "artifact": artifact, "proof": proof, "published_body": publication["body"],
            "metadata_suffix": publication["suffix"]}

def verify_joined_context_roundtrip(role, context, *, root, actions_root,
                                    verdict="PASS", run_id=9001, comment_id=9002,
                                    workflow_sha="e" * 40, variant="structured-inline-local"):
    """Synthetic recommendation through actual CLI/MCP bytes and official publication.

    The caller must supply the context frozen by its real production dispatch
    planner. Detection success is synthetic here; scan-byte identity is real.
    """
    check(variant in {"structured-inline-local", "observed-inline-local", "observed-qa2-local"}, "joined variant differs")
    check(variant not in {"observed-inline-local", "observed-qa2-local"} or role == "qa", "observed joined workflow is QA-only")
    import base64
    check(Path(root).resolve() == ROOT, "joined repository root differs")
    subject.validate_context(context)
    check(context["identity"]["role"] == role, "joined context role differs")
    original = canonical(context)
    payload = inline_payload(role, verdict)
    for key in subject.PAYLOAD_IDENTITY_KEYS:
        payload[key] = context["identity"][key]
    uri = "https://github.com/dream-xin/ai-sdlc/pull/" + str(payload["candidate_pr_number"])
    payload["evidence"][0]["uri"] = uri
    if role == "qa":
        payload["coverage"][0]["evidence"] = uri
    subject.validate_payload(payload, context, role)
    lock, config, validation, metadata = selected_config(
        role, payload["candidate_pr_number"], variant=variant)
    roundtrip_source_contract(role, variant, lock, metadata)
    compiled_contract(lock)
    actions = resolve_actions_root(actions_root).resolve(strict=True)
    cli_root = Path(os.environ["V03_SCHEMA_CLI_ROOT"]).resolve(strict=True)
    check((cli_root / "copilot").is_file(), "joined pinned CLI package absent")
    image = ("ghcr.io/github/gh-aw-firewall/agent:0.28.23@sha256:"
             "2c78aaba1c108e130e2d6d01e4f2cca334ea04c53e6f258913ac34173fe7e3b2")
    with tempfile.TemporaryDirectory(prefix="v03-joined-gate-") as name:
        temporary = Path(name)
        (temporary / "home").mkdir()
        (temporary / "actions").mkdir()
        (temporary / "copilot").mkdir()
        (temporary / "capture.mjs").write_text(SCHEMA_CAPTURE_NODE)
        row = {"label": role + "-joined", "legacy": False, "joined": True,
               "config": config, "meta": compiled_tools_metadata(lock, payload["candidate_pr_number"]),
               "observed_projection": OBSERVED_CLI_PROJECTION[role], "payload": payload}
        (temporary / "cases.json").write_bytes(canonical([row]))
        container = "v03-joined-" + temporary.name
        env = {"PATH": os.environ["PATH"], "HOME": str(temporary / "home")}
        command = ["docker", "run", "--rm", "--init", "--pull=never", "--name", container,
                   "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                   "--user", f"{os.getuid()}:{os.getgid()}", "--read-only", "--workdir", "/tmp",
                   "--tmpfs", f"/tmp:rw,nosuid,nodev,exec,uid={os.getuid()},gid={os.getgid()},mode=700",
                   "--mount", f"type=bind,src={temporary},dst=/inputs,readonly",
                   "--mount", f"type=bind,src={actions},dst=/inputs/actions,readonly",
                   "--mount", f"type=bind,src={cli_root},dst=/inputs/copilot,readonly",
                   "--entrypoint", "node", image,
                   "/inputs/capture.mjs", "/inputs/actions", "/inputs/copilot/copilot", "/inputs/cases.json"]
        try:
            completed = subprocess.run(command, env=env, cwd=temporary,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=110)
        finally:
            subprocess.run(["docker", "rm", "-f", container], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        check(len(completed.stdout) <= 262144 and len(completed.stderr) <= 1048576,
              "joined CLI diagnostics exceeded bound")
        check(completed.returncode == 0, "joined actual CLI/MCP execution failed")
        lines = completed.stdout.splitlines()
        check(len(lines) == 1, "joined CLI result count differs")
        result = json.loads(lines[0])
        check(result["status"] == "PASS" and result["provider_requests"] == 2 and
              result["tool_calls"] == 1 and result["linked_feedback"] is True and
              result["cli_exit"] == 0, "joined CLI tool/completion contract failed")
        raw = base64.b64decode(result["raw_ndjson_base64"], validate=True)
        check(0 < len(raw) <= subject.MAX_OUTPUT_BYTES and
              hashlib.sha256(raw).hexdigest() == result["raw_ndjson_sha256"],
              "joined MCP ledger bytes changed")
        rows = [line for line in raw.splitlines() if line.strip()]
        check(len(rows) == 1 and subject.strict_json(rows[0])["data"] == payload,
              "joined MCP result differs from dispatched identity/recommendation")
        official_dir = temporary / "official"
        official_dir.mkdir()
        official = Official(actions, official_dir, config, validation, metadata,
                            run_id=run_id, comment_id=comment_id, workflow_sha=workflow_sha)
        returned_raw, collected, artifact, proof, publication, body = _roundtrip(
            official, payload, context, role, raw_ledger=raw)
        check(returned_raw == raw and proof["raw_ndjson_sha256"] == result["raw_ndjson_sha256"],
              "official boundary substituted genuine MCP ledger")
    check(canonical(context) == original, "joined test mutated authenticated context")
    return {"payload": payload, "raw_ndjson": raw, "collected": collected,
            "artifact": artifact, "proof": proof, "published_body": publication["body"],
            "metadata_suffix": publication["suffix"],
            "cli_evidence": {"workflow_file": roundtrip_workflow_stem(role, variant) + ".lock.yml",
                             "source_variant": variant,
                             "fixed_source_sha": QA2_OBSERVED_SOURCE if variant == "observed-qa2-local" else None,
                             "provider_requests": 2, "tool_calls": 1, "linked_feedback": True,
                             "raw_ndjson_sha256": result["raw_ndjson_sha256"],
                             "threat_verdict": "synthetic-offline-only"}}


def item_number_contract_tests(role, actions_root, temporary):
    """Official ingestion preserves target types; rendering emits only the bound integer."""
    import v03_dogfood_gate_output_v2 as current
    def reject_current(call, label):
        try:
            call()
        except current.GateOutputContractError:
            return
        raise AssertionError("accepted " + label)
    candidate = 552
    _, config, validation, metadata = selected_config(
        role, candidate=candidate, variant="structured-inline-local")
    directory = temporary / (role + "-item-number")
    directory.mkdir()
    official = Official(actions_root, directory, config, validation, metadata)
    payload = inline_payload(role, "PASS")
    payload["candidate_pr_number"] = candidate
    payload["evidence"][0]["uri"] = "https://github.com/dream-xin/ai-sdlc/pull/552"
    if role == "qa":
        payload["coverage"][0]["evidence"] = payload["evidence"][0]["uri"]
    context = fixture_context(payload)
    sanitize = lambda value: official.call({"mode": "sanitize", "body": value})["body"]
    artifacts = []
    for target in (candidate, str(candidate)):
        item = json.loads(raw_item(payload))
        item["item_number"] = target
        raw = packed(item) + b"\n"
        _, collected, artifact, proof, publication, body = _roundtrip(
            official, payload, context, role, raw_ledger=raw, renderer=current)
        ingested = json.loads(collected)["items"][0]["item_number"]
        check(type(ingested) is type(target) and ingested == target,
              "official ingestion changed item_number representation")
        canonical_target = json.loads(artifact)["items"][0]["item_number"]
        check(type(canonical_target) is int and canonical_target == candidate,
              "renderer did not emit the authenticated integer candidate")
        check(publication["posts"] == 1, "target roundtrip duplicated publication")
        # Exercise only the unchanged production collector parser/context boundary.
        # Source authentication and full factory replay remain separate regressions.
        from v03_dogfood_full_composition import DogfoodStructuredGateResultSource
        from operator_vertical import VerticalInvariantError
        parser = object.__new__(DogfoodStructuredGateResultSource)
        parser._structured_parse = (context, role, publication["suffix"])
        check(parser._gate_payload(publication["body"]) == payload,
              "unchanged collector parser rejected normalized publication")
        for changed_body, expected_suffix in (
            (publication["body"] + "\nforged", publication["suffix"]),
            (publication["body"], publication["suffix"] + "\nforged"),
        ):
            parser._structured_parse = (context, role, expected_suffix)
            try:
                parser._gate_payload(changed_body)
            except VerticalInvariantError:
                pass
            else:
                raise AssertionError("collector parser accepted modified body/suffix")
        check(proof["raw_ndjson_sha256"] == hashlib.sha256(raw).hexdigest(),
              "target normalization lost the actual raw ledger digest")
        artifacts.append(artifact)
        # A representation substitution is rejected even if both target the same PR.
        altered = json.loads(collected)
        altered["items"][0]["item_number"] = str(candidate) if type(target) is int else candidate
        reject_current(lambda: current.render_gate_output(
            raw, packed(altered), context, role, sanitize=sanitize),
            "raw/ingested candidate representation substitution")
    integer_artifact, string_artifact = map(json.loads, artifacts)
    check(integer_artifact["items"] == string_artifact["items"],
          "accepted target representations changed canonical publication items")
    check(integer_artifact["gate_render_proof"]["raw_ndjson_sha256"] !=
          string_artifact["gate_render_proof"]["raw_ndjson_sha256"],
          "different raw target representations lost their distinct provenance")
    for proof in (integer_artifact["gate_render_proof"], string_artifact["gate_render_proof"]):
        proof.pop("raw_ndjson_sha256")
    check(integer_artifact == string_artifact,
          "target normalization changed fields beyond the retained raw digest")

    valid_raw = raw_item(payload)
    valid_collected = official.ingest(valid_raw)
    for target in (True, False, 552.0, 0, -552, None, "", "+552", "-552",
                   " 552", "552 ", "552\n", "0552", "000552", "552.0",
                   "5.52e2", "５５２", "٥٥٢", "aw_gate_001", "#552", 553, "553"):
        item = json.loads(valid_raw)
        item["item_number"] = target
        collected = json.loads(valid_collected)
        collected["items"][0]["item_number"] = target
        raw = packed(item) + b"\n"
        reject_current(lambda raw=raw, collected=collected: current.render_gate_output(
            raw, packed(collected), context, role, sanitize=sanitize),
            "noncanonical or foreign item_number")
    # Even a legitimate raw string is forbidden in the post-scan canonical artifact.
    scanned = json.loads(artifacts[0])
    proof = scanned["gate_render_proof"]
    for target in (str(candidate), True, 552.0, "0552", "553"):
        changed = copy.deepcopy(scanned)
        changed["items"][0]["item_number"] = target
        encoded = canonical(changed)
        reject_current(lambda encoded=encoded: current.validate_scanned_output(
            encoded, proof, context, role,
            scanned_sha256=hashlib.sha256(encoded).hexdigest()),
            "noninteger scanned candidate")
    print(role + ": official integer/canonical-string targets preserve exact ingestion and scan boundaries")


def temporary_id_contract_tests(role, actions_root, temporary):
    _, config, validation, metadata = selected_config(role, variant="structured-inline-local")
    directory = temporary / (role + "-temporary-id")
    directory.mkdir()
    official = Official(actions_root, directory, config, validation, metadata)
    payload = inline_payload(role, "PASS")
    context = fixture_context(payload)
    item = json.loads(raw_item(payload))
    item["temporary_id"] = "aw_gate_001"
    raw = packed(item) + b"\n"
    collected = official.ingest(raw)
    raw_seen, _, artifact, proof, publication, body = _roundtrip(
        official, payload, context, role, raw_ledger=raw)
    check(raw_seen == raw and json.loads(artifact)["items"][0]["temporary_id"] == item["temporary_id"],
          "official temporary_id lost before independent scan")
    check(publication["body"] == body + "\n\n" + publication["suffix"],
          "official temporary_id altered Gate body")
    sanitize = lambda value: official.call({"mode": "sanitize", "body": value})["body"]
    for value in (None, "", 123, True, "aw_ab", "aw_" + "a" * 13, "#aw_valid", "aw_bad-dash"):
        changed = {**item, "temporary_id": value}
        changed_raw = packed(changed) + b"\n"
        reject(lambda changed_raw=changed_raw: subject.render_gate_output(
            changed_raw, collected, context, role, sanitize=sanitize), "malformed raw temporary_id")
        changed_artifact = json.loads(artifact)
        changed_artifact["items"][0]["temporary_id"] = value
        encoded = canonical(changed_artifact)
        reject(lambda encoded=encoded: subject.validate_scanned_output(
            encoded, proof, context, role, scanned_sha256=hashlib.sha256(encoded).hexdigest()),
            "malformed scanned temporary_id")
    for mode in ("changed", "removed"):
        altered = json.loads(collected)
        if mode == "changed":
            altered["items"][0]["temporary_id"] = "aw_other001"
        else:
            altered["items"][0].pop("temporary_id")
        reject(lambda altered=altered: subject.render_gate_output(
            raw, packed(altered), context, role, sanitize=sanitize), "ingested temporary_id " + mode)
    no_id_raw = raw_item(payload)
    reject(lambda: subject.render_gate_output(no_id_raw, collected, context, role, sanitize=sanitize),
           "ingestion injected temporary_id")
    extra = {**item, "unexpected_metadata": "forbidden"}
    reject(lambda: subject.render_gate_output(packed(extra), collected, context, role, sanitize=sanitize),
           "temporary_id allowance widened raw keys")
    drift = json.loads(artifact)
    drift["items"][0]["temporary_id"] = "aw_other001"
    reject(lambda: subject.validate_scanned_output(canonical(drift), proof, context, role,
        scanned_sha256=hashlib.sha256(artifact).hexdigest()), "postscan temporary_id mutation")
    print(role + ": official temporary_id retained with strict shape and scan identity")


def exercise_role(role, actions_root, temporary, *, variant="structured-local"):
    lock, config, validation, metadata = selected_config(role, variant=variant)
    make_payload = inline_payload if variant != "structured-local" else role_payload
    roundtrip_source_contract(role, variant, lock, metadata)
    compiled_contract(lock)
    fixture_dir = temporary / (role + "-" + variant)
    fixture_dir.mkdir()
    detector_digest_contract(lock, fixture_dir)
    official = Official(actions_root, fixture_dir, config, validation, metadata)
    def sanitize(body):
        return official.call({"mode": "sanitize", "body": body})["body"]
    def render(raw, collected, context):
        return subject.render_gate_output(raw, collected, context, role, sanitize=sanitize)
    verdicts = ("PASS", "REWORK", "BLOCKED") if role == "reviewer" else ("PASS", "FAIL", "BLOCKED")
    for verdict in verdicts:
        payload = make_payload(role, verdict)
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

    payload = make_payload(role, "PASS")
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

SCHEMA_CAPTURE_NODE = r"""import fs from "node:fs";
import path from "node:path";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import crypto from "node:crypto";
import readline from "node:readline";
import {spawn, spawnSync} from "node:child_process";
const [actions, cli, casesFile] = process.argv.slice(2);
let schemaDifference = null;
const check = (value, code) => { if (!value) throw new Error(code); };
const canonical = value => JSON.stringify(value && typeof value === "object"
  ? Array.isArray(value) ? value.map(v => JSON.parse(canonical(v)))
    : Object.fromEntries(Object.keys(value).sort().map(k => [k, JSON.parse(canonical(value[k]))])) : value);
const digest = value => crypto.createHash("sha256").update(canonical(value)).digest("hex");
const blob = file => { const b=fs.readFileSync(file); return crypto.createHash("sha1")
  .update(Buffer.from("blob "+b.length+"\0")).update(b).digest("hex"); };
const pins = {
 "safe_outputs_tools.json":"11b7ee6b95b13f4f5eaa38999f0d66f762cd6baf",
 "generate_safe_outputs_tools.cjs":"ae09ab0a9d637035d4442f72475abcafe5e50ea4",
 "safe_outputs_mcp_server.cjs":"efb24c34fec875ffcd9d997d93f043c78fe16f8b",
 "mcp_server_core.cjs":"21dda2c06354d7c100350fca89f6143d6bf4e0d6",
 "copilot_harness.cjs":"7132b67bbef15083cf11d183a987eab668eec62c"
};
function badRefs(schema) {
 const bad=[];
 function walk(value) {
  if (!value || typeof value!=="object") return;
  if (typeof value.$ref==="string") {
   let target=schema;
   if (!value.$ref.startsWith("#/")) bad.push(value.$ref);
   else {
    for (const part of value.$ref.slice(2).split("/").map(p=>p.replace(/~1/g,"/").replace(/~0/g,"~")))
     target=target && Object.prototype.hasOwnProperty.call(target,part) ? target[part] : undefined;
    if (target===undefined) bad.push(value.$ref);
   }
  }
  Object.values(value).forEach(walk);
 }
 walk(schema); return bad;
}

function schemaDiff(expected, actual) {
 const rows=[];let total=0;
 const allowed=new Set(["type","required","additionalProperties","enum","const","minimum","maximum",
  "exclusiveMinimum","exclusiveMaximum","minLength","maxLength","minItems","maxItems","format","pattern","$ref","description"]);
 const kind=v=>v===undefined?"missing":v===null?"null":Array.isArray(v)?"array":typeof v;
 function walk(a,b,parts=[],keyword="") {
  if(canonical(a)===canonical(b))return;
  if(a && b && typeof a==="object" && typeof b==="object" && Array.isArray(a)===Array.isArray(b)) {
   for(const key of [...new Set([...Object.keys(a),...Object.keys(b)])].sort()) {
    if(!/^[A-Za-z0-9_$-]{1,80}$/.test(key)){total++;if(rows.length<128)rows.push({path:"UNEXPECTED_SCHEMA_KEY",expected:kind(a),actual:kind(b)});return;}
    walk(a[key],b[key],[...parts,key],Array.isArray(a)?keyword:key);
   }
   return;
  }
  const row={path:"/"+parts.map(p=>p.replace(/~/g,"~0").replace(/\//g,"~1")).join("/"),
   expected_type:kind(a),actual_type:kind(b)};
  if(allowed.has(keyword))for(const [name,value] of [["expected",a],["actual",b]]) {
   if(value===null || typeof value==="boolean" || typeof value==="number")row[name]=value;
   else if(typeof value==="string" && /^[\x20-\x7e]{0,256}$/.test(value))row[name]=value;
  }
  total++;if(rows.length<128)rows.push(row);
 }
 walk(expected,actual);return {total,truncated:total>rows.length,rows};
}


function observedCLIProjection(schema, changes) {
 const result=structuredClone(schema);
 check(changes && typeof changes==="object" && !Array.isArray(changes),"PROJECTION_FIXTURE");
 for(const [pointer,change] of Object.entries(changes)) {
  check(/^\/(?:properties\/[A-Za-z0-9_]+\/|items\/)*properties\/[A-Za-z0-9_]+$/.test(pointer),
        "PROJECTION_PATH");
  let node=result;
  for(const part of pointer.slice(1).split("/"))node=node?.[part];
  check(node && typeof node==="object" && !("description" in node),"PROJECTION_SOURCE");
  for(const [key,value] of Object.entries(change.removed)) {
   check(["pattern","minimum","minLength","maxLength"].includes(key) &&
         canonical(node[key])===canonical(value),"PROJECTION_SOURCE");
   delete node[key];
  }
  node.description=change.description;
 }
 return result;
}

function stopGroup(child) {
 if (!child?.pid) return;
 try { process.kill(-child.pid,"SIGKILL"); } catch {}
}
function run(command,args,env,cwd,timeout=90000) {
 return new Promise((resolve,reject)=>{
  const child=spawn(command,args,{env,cwd,detached:true,stdio:["ignore","pipe","pipe"]});
  let size=0;
  for (const stream of [child.stdout,child.stderr]) stream.on("data",b=>{
   size+=b.length; if(size>4*1024*1024) {stopGroup(child);reject(new Error("CHILD_OUTPUT_BOUND"));}
  });
  const timer=setTimeout(()=>{stopGroup(child);reject(new Error("CLI_WATCHDOG"));},timeout);
  child.once("error",()=>{clearTimeout(timer);stopGroup(child);reject(new Error("CHILD_START"));});
  child.once("close",code=>{clearTimeout(timer);stopGroup(child);resolve(code);});
 });
}
async function listTools(env,cwd) {
 const child=spawn(process.execPath,[path.join(actions,"safe_outputs_mcp_server.cjs")],
  {env,cwd,detached:true,stdio:["pipe","pipe","pipe"]});
 let count=0;
 child.stderr.on("data",b=>{count+=b.length;if(count>2*1024*1024)stopGroup(child);});
 const lines=readline.createInterface({input:child.stdout});
 const pending=new Map();
 lines.on("line",line=>{
  try {const m=JSON.parse(line);const done=pending.get(m.id);if(done){pending.delete(m.id);done(m);}}
  catch {stopGroup(child);}
 });
 function rpc(id,method,params) {
  return new Promise((resolve,reject)=>{
   const timer=setTimeout(()=>{pending.delete(id);reject(new Error("MCP_TIMEOUT"));},15000);
   pending.set(id,m=>{clearTimeout(timer);m.error?reject(new Error("MCP_ERROR")):resolve(m.result);});
   child.stdin.write(JSON.stringify({jsonrpc:"2.0",id,method,params})+"\n");
  });
 }
 try {
  await rpc(1,"initialize",{protocolVersion:"2024-11-05",capabilities:{},
                          clientInfo:{name:"schema-regression",version:"1"}});
  child.stdin.write(JSON.stringify({jsonrpc:"2.0",method:"notifications/initialized"})+"\n");
  return (await rpc(2,"tools/list",{})).tools;
 } finally {lines.close();stopGroup(child);}
}
function stopResponse(res,request) {
 const base={id:"offline-schema-stop",object:"chat.completion",created:1,model:"deepseek-chat"};
 if(request.stream) {
  res.writeHead(200,{"Content-Type":"text/event-stream"});
  res.write("data: "+JSON.stringify({...base,object:"chat.completion.chunk",
   choices:[{index:0,delta:{role:"assistant",content:"Schema capture complete."},finish_reason:null}]})+"\n\n");
  res.write("data: "+JSON.stringify({...base,object:"chat.completion.chunk",
   choices:[{index:0,delta:{},finish_reason:"stop"}]})+"\n\n");
  res.end("data: [DONE]\n\n");
 } else {
  res.writeHead(200,{"Content-Type":"application/json"});
  res.end(JSON.stringify({...base,choices:[{index:0,message:{role:"assistant",
   content:"Schema capture complete."},finish_reason:"stop"}]}));
 }
}

function toolCallResponse(res,request,name,args) {
 const tool={id:"offline-gate-call-1",type:"function",function:{name,arguments:JSON.stringify(args)}};
 const base={id:"offline-gate-recommendation",object:"chat.completion",created:1,model:"deepseek-chat"};
 if(request.stream) {
  res.writeHead(200,{"Content-Type":"text/event-stream"});
  res.write("data: "+JSON.stringify({...base,object:"chat.completion.chunk",
   choices:[{index:0,delta:{role:"assistant",tool_calls:[{index:0,...tool}]},finish_reason:null}]})+"\n\n");
  res.write("data: "+JSON.stringify({...base,object:"chat.completion.chunk",
   choices:[{index:0,delta:{},finish_reason:"tool_calls"}]})+"\n\n");
  res.end("data: [DONE]\n\n");
 } else {
  res.writeHead(200,{"Content-Type":"application/json"});
  res.end(JSON.stringify({...base,choices:[{index:0,message:{role:"assistant",content:null,
   tool_calls:[tool]},finish_reason:"tool_calls"}]}));
 }
}
function successfulFeedback(value, temporaryId) {
 if(typeof value==="string") {try{return successfulFeedback(JSON.parse(value),temporaryId);}catch{return false;}}
 if(!value||typeof value!=="object")return false;
 if(value.isError===true)return false;
 if(value.result==="success"&&value.temporary_id===temporaryId)return true;
 return Object.values(value).some(v=>successfulFeedback(v,temporaryId));
}

async function capture(row,index) {
 const root="/tmp/schema-capture/case-"+index;
 fs.mkdirSync(root,{recursive:true});
 const home=path.join(root,"home"); fs.mkdirSync(path.join(home,".copilot"),{recursive:true});
 const output=path.join(root,"outputs.jsonl");
 const env={PATH:path.dirname(cli)+":/usr/local/bin:/usr/bin:/bin",HOME:home,LANG:"C.UTF-8",
  RUNNER_TEMP:root,GITHUB_WORKSPACE:root,GH_AW_SAFE_OUTPUTS:output,
  GH_AW_SAFE_OUTPUTS_CONFIG_PATH:path.join(root,"config.json"),
  GH_AW_SAFE_OUTPUTS_TOOLS_PATH:path.join(root,"tools.json"),
  GH_AW_SAFE_OUTPUTS_TOOLS_SOURCE_PATH:path.join(actions,"safe_outputs_tools.json"),
  GH_AW_SAFE_OUTPUTS_TOOLS_META_PATH:path.join(root,"tools_meta.json"),
  GITHUB_REPOSITORY:"dream-xin/ai-sdlc",GITHUB_SERVER_URL:"https://github.com",
  GH_AW_POLICY_ALLOW_CREATE_PULL_REQUEST:"false"};
 fs.writeFileSync(env.GH_AW_SAFE_OUTPUTS_CONFIG_PATH,JSON.stringify(row.config));
 fs.writeFileSync(env.GH_AW_SAFE_OUTPUTS_TOOLS_META_PATH,JSON.stringify(row.meta));
 const generated=spawnSync(process.execPath,[path.join(actions,"generate_safe_outputs_tools.cjs")],
   {env,cwd:root,timeout:15000,maxBuffer:2*1024*1024});
 check(generated.status===0,"GENERATOR_FAILED");
 const tools=await listTools(env,root);
 const advertised=tools.filter(t=>t.name==="add_comment");
 check(advertised.length===1,"MCP_ADD_COMMENT_MISSING");
 const schema=advertised[0].inputSchema;
 if(!row.legacy)check(tools.every(t=>badRefs(t.inputSchema).length===0),"MCP_ALL_REFERENCE_CLOSURE");
 const broken=badRefs(schema);
 check(row.legacy ? broken.length===1 && broken[0]==="#/0/inputSchema/$defs/structured_data"
                  : broken.length===0,"MCP_REFERENCE_EXPECTATION");
 const generatedTools=JSON.parse(fs.readFileSync(env.GH_AW_SAFE_OUTPUTS_TOOLS_PATH));
 const generatedData=generatedTools.find(t=>t.name==="add_comment").inputSchema.properties.data;
 check(canonical(schema.properties.data)===canonical(generatedData),"MCP_DATA_DRIFT");
 let captured=null, fault=null, posts=0, linkedFeedback=false;
 const server=http.createServer((req,res)=>{
  if(req.method==="GET" && /\/models$/.test(req.url)){
   res.writeHead(200,{"Content-Type":"application/json"});
   res.end(JSON.stringify({object:"list",data:[{id:"deepseek-chat",object:"model",owned_by:"offline"}]}));return;
  }
  let bytes=0,raw="";
  req.on("data",b=>{bytes+=b.length;if(bytes>4*1024*1024){fault="REQUEST_BOUND";req.destroy();}else raw+=b;});
  req.on("end",()=>{
   try {
    check(req.method==="POST" && /\/chat\/completions$/.test(req.url),"PROVIDER_ROUTE");
    const request=JSON.parse(raw); check(request.model==="deepseek-chat","MODEL_ROUTE");
    check(++posts<=(row.joined?2:3),"REQUEST_COUNT");
    const requestTools=(request.tools||[]).map(t=>t.function||t);
    if(!row.legacy)check(requestTools.every(t=>t.parameters && badRefs(t.parameters).length===0),
      "CLI_ALL_REFERENCE_CLOSURE");
    const matches=requestTools.filter(t=>/^(?:safeoutputs[_-]+)?add_comment$/.test(t.name));
    check(matches.length===1,"CLI_ADD_COMMENT_MISSING");
    const parameters=matches[0].parameters;
    const expectedData=row.legacy ? schema.properties.data :
      observedCLIProjection(schema.properties.data,row.observed_projection);
    if(canonical(parameters.properties.data)!==canonical(expectedData)) {
     schemaDifference=schemaDiff(expectedData,parameters.properties.data);
     throw new Error("CLI_DATA_DRIFT");
    }
    const refs=badRefs(parameters);
    check(row.legacy ? refs.length===1 && refs[0]==="#/0/inputSchema/$defs/structured_data"
                     : refs.length===0,"CLI_REFERENCE_EXPECTATION");
    const current={schema_sha256:digest(parameters),data_sha256:digest(parameters.properties.data)};
    if(captured)check(canonical(captured)===canonical(current),"CLI_SCHEMA_CHANGED");
    captured=current;
    if(row.joined && posts===1) {
     check(row.payload && row.payload.candidate_pr_number===Number(row.config.add_comment.target),
       "JOINED_DISPATCH_TARGET");
     toolCallResponse(res,request,matches[0].name,{body:"AI-SDLC structured Gate recommendation.",
       item_number:row.payload.candidate_pr_number,data:row.payload});
     return;
    }
    if(row.joined) {
     check(fs.existsSync(output)&&fs.statSync(output).size<=131072,"JOINED_LEDGER_MISSING");
     const lines=fs.readFileSync(output,"utf8").trim().split("\n");
     check(lines.length===1,"JOINED_LEDGER_COUNT");
     const entry=JSON.parse(lines[0]);
     check(entry.type==="add_comment"&&entry.body==="AI-SDLC structured Gate recommendation."&&
       entry.item_number===row.payload.candidate_pr_number&&canonical(entry.data)===canonical(row.payload),
       "JOINED_LEDGER_DRIFT");
     const feedback=(request.messages||[]).filter(m=>m.role==="tool"&&m.tool_call_id==="offline-gate-call-1");
     check(feedback.length===1&&successfulFeedback(feedback[0].content,entry.temporary_id),
       "JOINED_TOOL_FEEDBACK");
     linkedFeedback=true;
    }
    stopResponse(res,request);
   } catch(error) {
    fault=/^[A-Z_]+$/.test(error.message)?error.message:"PROVIDER_PARSE";
    res.writeHead(400,{"Content-Type":"application/json"});
    res.end(JSON.stringify({error:{type:"offline_schema_capture_failure"}}));
   }
  });
 });
 await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
 const config={mcpServers:{safeoutputs:{type:"local",command:process.execPath,
  args:[path.join(actions,"safe_outputs_mcp_server.cjs")],tools:["*"],env}}};
 fs.writeFileSync(path.join(home,".copilot","mcp-config.json"),JSON.stringify(config));
 const cliEnv={...env,COPILOT_MODEL:"deepseek-chat",COPILOT_PROVIDER_TYPE:"openai",
  COPILOT_PROVIDER_WIRE_API:"completions",COPILOT_PROVIDER_API_KEY:"offline-dummy",
  COPILOT_PROVIDER_BASE_URL:"http://127.0.0.1:"+server.address().port};
 try {
  const code=await run(cli,["--model","deepseek-chat","--disable-builtin-mcps","--no-ask-user",
   "--allow-tool","safeoutputs","--prompt",row.joined ? "Record the supplied synthetic Gate recommendation once using add_comment, then finish." : "Return a short completion. Do not call any tools."],cliEnv,root);
  check(!fault,fault||"CAPTURE_FAILED");check(captured,"NO_PROVIDER_CAPTURE");
  check(code===0,"CLI_EXIT");
  if(row.joined) {
   check(posts===2&&linkedFeedback,"JOINED_COMPLETION");
   const raw=fs.readFileSync(output);
   return {case:row.label,status:"PASS",scope:"synthetic recommendation; genuine CLI/MCP ledger",
    provider_requests:posts,tool_calls:1,linked_feedback:true,cli_exit:code,
    raw_ndjson_base64:raw.toString("base64"),
    raw_ndjson_sha256:crypto.createHash("sha256").update(raw).digest("hex"),...captured};
  }
  check(!fs.existsSync(output)||fs.statSync(output).size===0,"UNEXPECTED_TOOL_EFFECT");
  return {case:row.label,status:"PASS",scope:"exact observed CLI data projection and all tool reference closure; trusted helper retains constraints",provider_requests:posts,local_reference_closure:!row.legacy,
          expected_legacy_rejection:row.legacy,...captured};
 } finally {server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
}
async function main() {
 check(actions==="/inputs/actions"&&cli==="/inputs/copilot/copilot"&&casesFile==="/inputs/cases.json","INPUT_PATH");
 check(process.getuid()!==0,"NONROOT_REQUIRED");
 check(Object.values(os.networkInterfaces()).flat().every(n=>n.internal),"NO_EGRESS");
 check(!Object.keys(process.env).some(k=>/TOKEN|SECRET|PASSWORD|PRIVATE_KEY|CREDENTIAL/i.test(k)),"INHERITED_AUTHORITY");
 await new Promise((resolve,reject)=>{
  const socket=net.connect({host:"192.0.2.1",port:9});
  const timer=setTimeout(()=>{socket.destroy();reject(new Error("EGRESS_INCONCLUSIVE"));},1000);
  socket.once("connect",()=>{clearTimeout(timer);socket.destroy();reject(new Error("EGRESS_AVAILABLE"));});
  socket.once("error",e=>{clearTimeout(timer);socket.destroy();
   ["ENETUNREACH","EHOSTUNREACH","EACCES","EPERM"].includes(e.code)?resolve():reject(new Error("EGRESS_INCONCLUSIVE"));});
 });
 for(const [file,sha] of Object.entries(pins))check(blob(path.join(actions,file))===sha,"OFFICIAL_PIN");
 check(!fs.existsSync("/tmp/schema-capture"),"STALE_WORKSPACE");
 fs.mkdirSync("/tmp/schema-capture");
 const version=spawnSync(cli,["--version"],{env:{PATH:"/usr/local/bin:/usr/bin:/bin",HOME:"/tmp/schema-capture"},
  encoding:"utf8",timeout:15000});
 check(version.status===0&&/\b1\.0\.90\b/.test(version.stdout),"CLI_VERSION");
 const cases=JSON.parse(fs.readFileSync(casesFile));
 check((cases.length===3&&cases[0].legacy===true&&cases.slice(1).every(c=>c.legacy===false&&!c.joined)) ||
       (cases.length===1&&cases[0].legacy===false&&cases[0].joined===true),"CASE_MATRIX");
 for(let i=0;i<cases.length;i++) {
  schemaDifference=null;
  try {console.log(JSON.stringify(await capture(cases[i],i)));}
  catch(e) {
   console.log(JSON.stringify({case:cases[i].label,status:"FAIL",
    stage:/^[A-Z_]+$/.test(e.message)?e.message:"INFRASTRUCTURE",schema_differences:schemaDifference}));
   process.exitCode=1;
  }
 }
}
main().catch(e=>{console.log(JSON.stringify({status:"FAIL",stage:/^[A-Z_]+$/.test(e.message)?e.message:"INFRASTRUCTURE",schema_differences:schemaDifference}));process.exitCode=1;});
"""

# Exact schema-only observations from offline job 114244633858; not semantic equivalence.
OBSERVED_CLI_PROJECTION = json.loads("{\"reviewer\":{\"/properties/candidate_head_sha\":{\"description\":\"{pattern: \\\"^[0-9a-f]{40}$\\\"}\",\"removed\":{\"pattern\":\"^[0-9a-f]{40}$\"}},\"/properties/candidate_pr_number\":{\"description\":\"{minimum: 1}\",\"removed\":{\"minimum\":1}},\"/properties/evidence/items/properties/id\":{\"description\":\"{minLength: 1}\",\"removed\":{\"minLength\":1}},\"/properties/evidence/items/properties/uri\":{\"description\":\"{minLength: 1}\",\"removed\":{\"minLength\":1}},\"/properties/expected_revision\":{\"description\":\"{minimum: 0}\",\"removed\":{\"minimum\":0}},\"/properties/feature_id\":{\"description\":\"{minLength: 1}\",\"removed\":{\"minLength\":1}},\"/properties/findings/items/properties/code\":{\"description\":\"{minLength: 1, maxLength: 80}\",\"removed\":{\"maxLength\":80,\"minLength\":1}},\"/properties/findings/items/properties/message\":{\"description\":\"{minLength: 1, maxLength: 4000}\",\"removed\":{\"maxLength\":4000,\"minLength\":1}},\"/properties/id\":{\"description\":\"{minLength: 1, pattern: \\\"^[A-Za-z0-9._:-]+$\\\"}\",\"removed\":{\"minLength\":1,\"pattern\":\"^[A-Za-z0-9._:-]+$\"}},\"/properties/reason\":{\"description\":\"{minLength: 1, maxLength: 4000}\",\"removed\":{\"maxLength\":4000,\"minLength\":1}},\"/properties/target_ref\":{\"description\":\"{minLength: 1}\",\"removed\":{\"minLength\":1}},\"/properties/target_repository\":{\"description\":\"{pattern: \\\"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$\\\"}\",\"removed\":{\"pattern\":\"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$\"}},\"/properties/task_id\":{\"description\":\"{minLength: 1}\",\"removed\":{\"minLength\":1}}},\"qa\":{\"/properties/candidate_head_sha\":{\"description\":\"{pattern: \\\"^[0-9a-f]{40}$\\\"}\",\"removed\":{\"pattern\":\"^[0-9a-f]{40}$\"}},\"/properties/candidate_pr_number\":{\"description\":\"{minimum: 1}\",\"removed\":{\"minimum\":1}},\"/properties/checks/items/properties/detail\":{\"description\":\"{maxLength: 4000}\",\"removed\":{\"maxLength\":4000}},\"/properties/checks/items/properties/name\":{\"description\":\"{minLength: 1, maxLength: 200}\",\"removed\":{\"maxLength\":200,\"minLength\":1}},\"/properties/coverage/items/properties/criterion\":{\"description\":\"{minLength: 1, maxLength: 500}\",\"removed\":{\"maxLength\":500,\"minLength\":1}},\"/properties/coverage/items/properties/evidence\":{\"description\":\"{maxLength: 4000}\",\"removed\":{\"maxLength\":4000}},\"/properties/evidence/items/properties/id\":{\"description\":\"{minLength: 1}\",\"removed\":{\"minLength\":1}},\"/properties/evidence/items/properties/uri\":{\"description\":\"{minLength: 1}\",\"removed\":{\"minLength\":1}},\"/properties/expected_revision\":{\"description\":\"{minimum: 0}\",\"removed\":{\"minimum\":0}},\"/properties/feature_id\":{\"description\":\"{minLength: 1}\",\"removed\":{\"minLength\":1}},\"/properties/id\":{\"description\":\"{minLength: 1, pattern: \\\"^[A-Za-z0-9._:-]+$\\\"}\",\"removed\":{\"minLength\":1,\"pattern\":\"^[A-Za-z0-9._:-]+$\"}},\"/properties/reason\":{\"description\":\"{minLength: 1, maxLength: 4000}\",\"removed\":{\"maxLength\":4000,\"minLength\":1}},\"/properties/target_ref\":{\"description\":\"{minLength: 1}\",\"removed\":{\"minLength\":1}},\"/properties/target_repository\":{\"description\":\"{pattern: \\\"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$\\\"}\",\"removed\":{\"pattern\":\"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$\"}},\"/properties/task_id\":{\"description\":\"{minLength: 1}\",\"removed\":{\"minLength\":1}}}}")

def compiled_tools_metadata(lock, candidate=577):
    rows = [step["env"]["GH_AW_TOOLS_META_JSON"] for step in lock["jobs"]["agent"]["steps"]
            if "GH_AW_TOOLS_META_JSON" in step.get("env", {})]
    check(len(rows) == 1, "missing or ambiguous actual compiler tools metadata")
    text = rows[0].replace("${GH_AW_INPUTS_CANDIDATE_PR_NUMBER}", str(candidate))
    text = text.replace("${GH_AW_INPUT_CANDIDATE_PR_NUMBER}", str(candidate))
    check("${" not in text, "unresolved native tool metadata")
    return json.loads(text)

def inline_payload(role, verdict):
    payload = role_payload(role, verdict)
    if verdict == "PASS":
        payload["reason"] = "The synthetic candidate satisfies the assigned checks."
    if role == "qa":
        payload["checks"][0]["detail"] = "The assigned synthetic candidate check was evaluated."
        payload["coverage"][0]["evidence"] = "https://github.com/dream-xin/ai-sdlc/pull/577"
    return payload

def dropped_constraint_rejections(role, schema, native, actions_root, temporary):
    lock, config, validation, metadata = selected_config(role, variant="structured-inline-local")
    directory = temporary / (role + "-cli-dropped-constraints")
    directory.mkdir()
    official = Official(actions_root, directory, config, validation, metadata)
    base = inline_payload(role, "REWORK" if role == "reviewer" else "PASS")
    context = fixture_context(base)
    count = 0
    for pointer, change in OBSERVED_CLI_PROJECTION[role].items():
        tokens = pointer.lstrip("/").split("/")
        location = []
        schema_node = schema
        for token in tokens:
            schema_node = schema_node[token]
        index = 0
        while index < len(tokens):
            if tokens[index] == "properties":
                location.append(tokens[index + 1])
                index += 2
            else:
                check(tokens[index] == "items", "unexpected fixed projection path")
                location.append(0)
                index += 1
        for keyword, bound in change["removed"].items():
            check(schema_node[keyword] == bound, "observed constraint source changed")
            changed = copy.deepcopy(base)
            target = changed
            for part in location[:-1]:
                target = target[part]
            if keyword == "minLength":
                value = ""
            elif keyword == "maxLength":
                value = "x" * (bound + 1)
            elif keyword == "minimum":
                value = bound - 1
            else:
                check(keyword == "pattern", "unexpected dropped keyword")
                value = "g" * 40 if location == ["candidate_head_sha"] else "invalid value"
            target[location[-1]] = value
            check(any(error.validator == keyword and list(error.path) == location
                      for error in native.iter_errors(changed)),
                  "ineffective exact dropped-constraint mutation")
            reject(lambda changed=changed: subject.validate_payload(changed, context, role),
                   "strict acceptance lost CLI-dropped constraint " + pointer + "/" + keyword)
            raw = raw_item(changed)
            collected = official.ingest(raw)
            reject(lambda raw=raw, collected=collected: subject.render_gate_output(
                raw, collected, context, role,
                sanitize=lambda body: official.call({"mode": "sanitize", "body": body})["body"]),
                "renderer accepted CLI-dropped constraint " + pointer + "/" + keyword)
            count += 1
    check(count == (17 if role == "reviewer" else 19), "finite dropped-constraint matrix changed")
    print(role + ": all " + str(count) + " CLI-dropped constraints rejected by trusted helper/render")


def inline_native_schema_tests(role, actions_root, temporary):
    import jsonschema
    lock, config, _, _ = selected_config(role, variant="structured-inline-local")
    meta = compiled_tools_metadata(lock)
    schema = meta["property_injections"]["add_comment"]["data"]
    source = (ROOT / ".github/workflows" /
              f"ai-sdlc-gh-aw-{role}-deepseek-v03-structured-inline-local.md").read_text()
    front = yaml.safe_load(source.split("---", 2)[1])
    check(schema == front["safe-outputs"]["data"], "compiler changed complete inline data contract")
    def closed(value):
        if isinstance(value, dict):
            check("$ref" not in value and "$defs" not in value, "inline schema contains reference indirection")
            if value.get("type") == "object":
                check(value.get("additionalProperties") is False and
                      set(value.get("required", [])) == set(value.get("properties", {})),
                      "native producer object is not closed/all-required")
            for child in value.values():
                closed(child)
        elif isinstance(value, list):
            for child in value:
                closed(child)
    closed(schema)
    jsonschema.Draft202012Validator.check_schema(schema)
    native = jsonschema.Draft202012Validator(schema)
    for verdict in (("PASS", "REWORK", "BLOCKED") if role == "reviewer" else ("PASS", "FAIL", "BLOCKED")):
        payload = inline_payload(role, verdict)
        native.validate(payload)
        subject.validate_payload(payload, fixture_context(payload), role)
    payload = inline_payload(role, "PASS")
    omissions = [("reason", lambda p: p.pop("reason"))]
    if role == "qa":
        omissions += [("checks.detail", lambda p: p["checks"][0].pop("detail")),
                      ("coverage.evidence", lambda p: p["coverage"][0].pop("evidence"))]
    for label, mutate in omissions:
        missing = copy.deepcopy(payload)
        mutate(missing)
        check(list(native.iter_errors(missing)), "native producer accepted omitted " + label)
        subject.validate_payload(missing, fixture_context(missing), role)
    dropped_constraint_rejections(role, schema, native, actions_root, temporary)
    print(role + ": inline native schema and unchanged strict acceptance verified")

def prepare_schema_capture(destination, actions_root):
    destination.mkdir(mode=0o700)
    cases = []
    for role, variant, legacy in (
        ("reviewer", "structured-local", True),
        ("reviewer", "structured-inline-local", False),
        ("qa", "structured-inline-local", False),
    ):
        lock, config, _, _ = selected_config(role, variant=variant)
        cases.append({"label": role + "-" + variant, "legacy": legacy,
                      "config": config, "meta": compiled_tools_metadata(lock),
                      "observed_projection": None if legacy else OBSERVED_CLI_PROJECTION[role]})
    (destination / "cases.json").write_bytes(canonical(cases))
    (destination / "capture.mjs").write_text(SCHEMA_CAPTURE_NODE)
    print("Prepared three actual compiled schema cases; no model or provider invoked.")



OBSERVER_BOUNDARY_NODE = r'''
import fs from "node:fs";
import path from "node:path";
import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {pathToFileURL} from "node:url";
const helper=path.resolve(process.argv[2]);
const subject=await import(pathToFileURL(helper).href);
const reportScript=fs.readFileSync(0,"utf8");assert.ok(reportScript.length>0&&reportScript.length<16384);
assert.equal(process.platform,"linux");
fs.mkdirSync("/tmp/gh-aw",{recursive:true});
const startFd=fs.readdirSync("/proc/self/fd").length;
const reports=[];
let currentCase="initialization";
process.on("uncaughtException",error=>{console.log(JSON.stringify({schema:"v03-observer-boundary-tests/v1",status:"FAIL",case:currentCase,error_class:["AssertionError","TypeError","SyntaxError","Error"].includes(error.name)?error.name:"Other",raw_exported:false}));process.exitCode=1;});
let serial=0;
const original={openSync:fs.openSync,readSync:fs.readSync,opendirSync:fs.opendirSync};
function reset(){Object.assign(fs,original);}
const records=[
 {type:"tool.execution_start",data:{toolCallId:"synthetic-private-1",toolName:"bash",arguments:{command:"printf SYNTHETIC_PRIVATE_BODY"}}},
 {type:"tool.execution_complete",data:{toolCallId:"synthetic-private-1",success:true,result:{content:"SYNTHETIC_PRIVATE_RESULT"},shellExecution:{exitCode:7}}},
 {type:"tool.execution_start",data:{toolCallId:"synthetic-private-2",toolName:"bash",arguments:{command:"printf SYNTHETIC_PRIVATE_BODY"}}},
 {type:"tool.execution_complete",data:{toolCallId:"synthetic-private-2",success:true,result:{content:"SYNTHETIC_PRIVATE_RESULT"},shellExecution:{exitCode:0}}}
];
const encode=x=>Buffer.from(x.map(e=>JSON.stringify(e)).join("\n")+"\n");
function fixture(){
 const temp=fs.mkdtempSync("/tmp/observer-boundary-");
 fs.mkdirSync(path.join(temp,"gh-aw"));
 const id=String(910000000000+process.pid*100+(++serial));
 const env={PATH:process.env.PATH,RUNNER_TEMP:temp,SOURCE_RUN_ID:id,RUN_ATTEMPT:"1",SOURCE_WORKFLOW_SHA:"a".repeat(40),OBSERVER_RUNNER_ENVIRONMENT:"github-hosted",OBSERVER_RUNNER_OS:"Linux",GITHUB_OUTPUT:path.join(temp,"output"),GITHUB_ENV:path.join(temp,"env"),DETECTION_RESULT:"failure",ORIGINAL_EXIT_CODE:"124"};
 fs.writeFileSync(env.GITHUB_ENV,"EXISTING=synthetic\n");
 const root=subject.prepare(env);
 assert.equal(fs.readFileSync(env.GITHUB_OUTPUT,"utf8"),"parent="+root+"\n"+"copilot_home="+path.join(root,"copilot")+"\n");
 assert.equal(fs.statSync(path.join(root,"copilot")).mode&0o777,0o700);
 assert.equal(fs.readFileSync(env.GITHUB_ENV,"utf8"),"EXISTING=synthetic\nCOPILOT_HOME="+path.join(root,"copilot")+"\n");
 const session=path.join(root,"copilot","session-state");
 const dir=path.join(session,"00000000-0000-0000-0000-000000000001");
 fs.mkdirSync(dir,{recursive:true});const events=path.join(dir,"events.jsonl");fs.writeFileSync(events,encode(records));
 return {temp,env,root,session,dir,events,stamp:path.join(temp,"gh-aw/detector-observation-owner.json"),summary:path.join(temp,"gh-aw/detector-observation-summary.json")};
}
function safeSummary(s){
 assert.equal(s.coverage,"partial");assert.equal(s.safety_authority,false);
 const text=JSON.stringify(s);
 for(const needle of ["SYNTHETIC_PRIVATE","synthetic-private-","events.jsonl","/tmp/"])assert.equal(text.includes(needle),false);
 assert.deepEqual(Object.keys(s).sort(),["cleanup","counts","coverage","reason","run_attempt","run_id","safety_authority","schema_version","status","workflow_sha"].sort());
}
function unavailable(s,reason=null){
 safeSummary(s);assert.equal(s.status,"unavailable");assert.equal(s.counts,null);
 if(reason)assert.equal(s.reason,reason);
}
function test(name,action){
 currentCase=name;const f=fixture();const before={...f.env};
 try{action(f);assert.deepEqual(f.env,before);reports.push(name);}
 finally{reset();fs.rmSync(f.root,{recursive:true,force:true});fs.rmSync(f.root+".moved",{recursive:true,force:true});fs.rmSync(f.temp,{recursive:true,force:true});}
}
test("observed_partial_readonly",f=>{
 const bytes=fs.readFileSync(f.events);
 const cli=spawnSync(process.execPath,[helper,"observe"],{env:f.env,encoding:"utf8",timeout:5000,maxBuffer:8192});assert.equal(cli.status,0);assert.equal(cli.signal,null);assert.equal(cli.stdout,"");assert.equal(cli.stderr,"");
 const s=JSON.parse(fs.readFileSync(f.summary));safeSummary(s);
 assert.equal(s.status,"observed");assert.equal(s.cleanup,"deferred_to_ephemeral_runner");
 assert.equal(s.counts.linked_calls,2);assert.equal(s.counts.native_tool_success,2);
 assert.equal(s.counts.shell_exit_zero,1);assert.equal(s.counts.shell_exit_nonzero,1);
 assert.equal(s.counts.repeated_command_occurrences,1);assert.equal(s.counts.repeated_command_and_result_occurrences,1);
 assert.deepEqual(fs.readFileSync(f.events),bytes);assert.ok(fs.existsSync(f.root));
});
test("missing_events_cancel_shape",f=>{
 fs.unlinkSync(f.events);fs.mkdirSync(path.join(f.session,"synthetic-metadata"));
 const p=spawnSync(process.execPath,[helper,"observe"],{env:f.env,encoding:"utf8",timeout:5000,maxBuffer:8192});
 assert.equal(p.status,1);assert.equal(p.signal,null);assert.equal(p.stdout,"");assert.equal(p.stderr,"");
 const s=JSON.parse(fs.readFileSync(f.summary));unavailable(s,"SESSION");assert.equal(s.cleanup,"deferred_to_ephemeral_runner");
 assert.equal(f.env.ORIGINAL_EXIT_CODE,"124");assert.equal(f.env.DETECTION_RESULT,"failure");
 assert.ok(fs.existsSync(f.dir));
});
test("unmatched_records_remain_partial",f=>{
 fs.writeFileSync(f.events,encode([records[0]]));const s=subject.observe(f.env);
 assert.equal(s.status,"observed");safeSummary(s);assert.equal(s.counts.linked_calls,0);assert.equal(s.counts.unmatched_starts,1);
});
for(const [name,raw,reason] of [
 ["truncated",encode(records).subarray(0,-1),"EVENT_TRUNCATED"],
 ["malformed",Buffer.from("{bad}\n"),"EVENT_JSON"],
 ["duplicate_start",encode([records[0],records[0]]),"LINKAGE"],
 ["untyped_status",encode([records[0],{...records[1],data:{...records[1].data,success:"true"}}]),"LINKAGE"],
 ["invalid_utf8",Buffer.from([255,10]),"EVENT_JSON"]
])test(name,f=>{fs.writeFileSync(f.events,raw);unavailable(subject.observe(f.env),reason);});
test("file_growth_during_read",f=>{
 let hit=false;fs.readSync=function(fd,...args){const n=original.readSync.call(fs,fd,...args);if(!hit&&fs.readlinkSync("/proc/self/fd/"+fd)===f.events){hit=true;fs.appendFileSync(f.events," \n");}return n;};
 unavailable(subject.observe(f.env),"BOUND");assert.equal(hit,true);
});
test("file_max_plus_one",f=>{fs.truncateSync(f.events,8*1024*1024+1);unavailable(subject.observe(f.env),"BOUND");});
test("directory_max_plus_one",f=>{
 for(let i=0;i<129;i++)fs.writeFileSync(path.join(f.root,"synthetic-"+i),"x");
 let reads=0;fs.opendirSync=function(...args){const d=original.opendirSync.apply(fs,args),read=d.readSync.bind(d);d.readSync=()=>{reads++;return read();};return d;};
 unavailable(subject.observe(f.env),"BOUND");assert.equal(reads,129);
});
test("child_directory_max_plus_one",f=>{
 for(let i=0;i<17;i++)fs.mkdirSync(path.join(f.session,"synthetic-"+i));
 unavailable(subject.observe(f.env),"BOUND");
});
test("fifo_nonblocking",f=>{
 fs.unlinkSync(f.events);const p=spawnSync("mkfifo",[f.events],{env:{PATH:process.env.PATH},timeout:3000});assert.equal(p.status,0);
 const began=Date.now(),s=subject.observe(f.env);unavailable(s,"PATH");assert.ok(Date.now()-began<2000);
});
test("stamp_fifo_nonblocking",f=>{
 fs.unlinkSync(f.stamp);const p=spawnSync("mkfifo",[f.stamp],{env:{PATH:process.env.PATH},timeout:3000});assert.equal(p.status,0);
 const began=Date.now(),s=subject.observe(f.env);unavailable(s,"BOUND");assert.equal(s.cleanup,"not_attempted");assert.ok(Date.now()-began<2000);
});
test("record_count_bound",f=>{fs.writeFileSync(f.events,encode(Array.from({length:4097},()=>({type:"synthetic",data:{}}))));unavailable(subject.observe(f.env),"BOUND");});
test("regular_file_hardlink",f=>{fs.linkSync(f.events,path.join(f.temp,"extra-link"));unavailable(subject.observe(f.env),"PATH");});
test("event_symlink",f=>{fs.unlinkSync(f.events);fs.symlinkSync(path.join(f.temp,"missing"),f.events);unavailable(subject.observe(f.env),"PATH");});
test("ancestor_symlink",f=>{
 const foreign=path.join(f.temp,"foreign");fs.renameSync(f.session,foreign);fs.symlinkSync(foreign,f.session);
 unavailable(subject.observe(f.env),"PATH");assert.ok(fs.existsSync(path.join(foreign,path.basename(f.dir),"events.jsonl")));
});
test("legacy_preservation_cannot_replace_fixed_home",f=>{fs.renameSync(f.session,path.join(f.root,"awf-agent-session-state-DEF456"));unavailable(subject.observe(f.env));});
test("ambiguous_uuid_directories",f=>{fs.mkdirSync(path.join(f.session,"00000000-0000-0000-0000-000000000002"));unavailable(subject.observe(f.env),"SESSION");});
test("surviving_work_directory",f=>{fs.mkdirSync(path.join(f.root,"awf-ABC123"));unavailable(subject.observe(f.env),"SESSION");});
test("wrong_workflow_identity",f=>{
 const stamp=JSON.parse(fs.readFileSync(f.stamp));stamp.workflow_sha="b".repeat(40);fs.writeFileSync(f.stamp,JSON.stringify(stamp));
 const s=subject.observe(f.env);unavailable(s,"OWNERSHIP");assert.equal(s.cleanup,"not_attempted");
});
function replaceRoot(f){fs.renameSync(f.root,f.root+".moved");fs.mkdirSync(f.root,{mode:0o700});fs.writeFileSync(path.join(f.root,"foreign-marker"),"KEEP");}
test("root_replaced_after_event_read",f=>{
 let hit=false;fs.readSync=function(fd,...args){const n=original.readSync.call(fs,fd,...args);if(!hit&&fs.readlinkSync("/proc/self/fd/"+fd)===f.events){hit=true;replaceRoot(f);}return n;};
 const s=subject.observe(f.env);unavailable(s,"OWNERSHIP");assert.equal(s.cleanup,"refused_ownership_changed");assert.equal(hit,true);assert.equal(fs.readFileSync(path.join(f.root,"foreign-marker"),"utf8"),"KEEP");
});
test("root_replaced_at_final_ownership",f=>{
 let stamps=0;fs.openSync=function(file,...args){if(String(file).endsWith("/detector-observation-owner.json")&&++stamps===3)replaceRoot(f);return original.openSync.call(fs,file,...args);};
 const s=subject.observe(f.env);unavailable(s,"OWNERSHIP");assert.equal(stamps,3);assert.equal(s.cleanup,"refused_ownership_changed");assert.equal(fs.readFileSync(path.join(f.root,"foreign-marker"),"utf8"),"KEEP");
});
test("summary_symlink_destination",f=>{
 const target=path.join(f.temp,"foreign");fs.writeFileSync(target,"KEEP");fs.symlinkSync(target,f.summary);
 assert.throws(()=>subject.observe(f.env));assert.equal(fs.readFileSync(target,"utf8"),"KEEP");
});
test("failed_prepare_leaves_environment_unchanged",f=>{
 const bytes=fs.readFileSync(f.env.GITHUB_ENV);
 const p=spawnSync(process.execPath,[helper,"prepare"],{env:f.env,encoding:"utf8",timeout:5000,maxBuffer:8192});
 assert.equal(p.status,1);assert.equal(p.signal,null);assert.deepEqual(fs.readFileSync(f.env.GITHUB_ENV),bytes);
});
test("untrusted_runner_identity",f=>{assert.throws(()=>subject.observe({...f.env,OBSERVER_RUNNER_ENVIRONMENT:"self-hosted"}),/IDENTITY/);assert.equal(fs.existsSync(f.summary),false);});

function deliver(f){
 const p=spawnSync("bash",["-c",reportScript],{env:f.env,encoding:"utf8",timeout:5000,maxBuffer:32768});
 assert.equal(p.status,0);assert.equal(p.stderr,"");
 for(const secret of ["SYNTHETIC_PRIVATE","synthetic-private-","/tmp/"])assert.equal(p.stdout.includes(secret),false);
 return JSON.parse(p.stdout);
}
const unavailableDelivery={schema_version:"ai-sdlc.detector-observation.delivery/v1",status:"unavailable",coverage:"partial",safety_authority:false};
test("actual_report_observed",f=>{const s=subject.observe(f.env);assert.equal(s.status,"observed");assert.deepEqual(deliver(f),s);});
test("actual_report_unavailable",f=>{fs.unlinkSync(f.events);const s=subject.observe(f.env);unavailable(s,"SESSION");assert.deepEqual(deliver(f),s);});
test("actual_report_rejects_raw_extra",f=>{const s=subject.observe(f.env);s.raw="SYNTHETIC_PRIVATE_BODY";fs.writeFileSync(f.summary,JSON.stringify(s));assert.deepEqual(deliver(f),unavailableDelivery);});
test("actual_report_rejects_malformed",f=>{fs.writeFileSync(f.summary,"{broken SYNTHETIC_PRIVATE_BODY");assert.deepEqual(deliver(f),unavailableDelivery);});

reset();assert.ok(fs.readdirSync("/proc/self/fd").length<=startFd+1);
console.log(JSON.stringify({schema:"v03-observer-boundary-tests/v1",status:"PASS",cases:reports,case_count:reports.length,scope:"synthetic helper boundaries; cancellation record unavailable, original status unchanged",raw_exported:false}));
'''

def observer_boundary_tests():
    source_path = ROOT / ".github/workflows/ai-sdlc-gh-aw-qa-deepseek-v03-observed-inline-local.md"
    source = yaml.safe_load(source_path.read_text().split("---", 2)[1])
    detector = source["safe-outputs"]["threat-detection"]
    rows = [step for step in detector["post-steps"] if step.get("id") == "detector_observation_report"]
    check(len(rows) == 1, "missing actual observer report step")
    report_step = rows[0]
    check(report_step.get("continue-on-error") is True and report_step.get("if") == "${{ always() }}",
          "observer report must not replace original detector outcome")
    report_script = report_step["run"]
    lock_path = source_path.with_suffix(".lock.yml")
    if lock_path.exists():
        compiled = yaml.safe_load(lock_path.read_text())
        compiled_rows = [step for step in compiled["jobs"]["detection"]["steps"]
                         if step.get("id") == "detector_observation_report"]
        check(len(compiled_rows) == 1 and compiled_rows[0]["run"] == report_script,
              "compiled observer delivery changed actual source script")
    conclusion_rows = [step for step in source["jobs"]["conclusion"]["pre-steps"]
                       if step.get("name") == "Record non-authoritative Gate execution identity"]
    check(len(conclusion_rows) == 1, "conclusion identity step ambiguous")
    conclusion_step = conclusion_rows[0]
    check(conclusion_step.get("if") == "${{ needs.safe_outputs.result == 'success' }}",
          "conclusion identity must require actual Safe Outputs success")
    check(detector["engine"]["env"]["TMPDIR"] == "/tmp"
          and "COPILOT_HOME" not in detector["engine"]["env"],
          "observer changed detector temp permissions or unconditional home fallback")
    if lock_path.exists():
        identity_steps = [step for step in compiled["jobs"]["conclusion"]["steps"]
                          if step.get("name") == conclusion_step["name"]]
        check(len(identity_steps) == 1 and identity_steps[0]["run"] == conclusion_step["run"]
              and identity_steps[0]["if"] == conclusion_step["if"],
              "compiled conclusion identity differs from source")
        execution_steps = [step for step in compiled["jobs"]["detection"]["steps"]
                           if step.get("id") == "detection_agentic_execution"]
        check(len(execution_steps) == 1 and execution_steps[0]["env"]["TMPDIR"] == "/tmp",
              "compiled detector temp permission baseline changed")
    identity_env = {"PATH": os.environ["PATH"], "SOURCE_RUN_ID": "9001",
        "SOURCE_WORKFLOW_REF": "dream-xin/ai-sdlc/.github/workflows/ai-sdlc-gh-aw-qa-deepseek-v03-observed-inline-local.lock.yml@refs/heads/main",
        "SOURCE_HEAD_SHA": "a" * 40, "TARGET_REPOSITORY": "dream-xin/ai-sdlc",
        "TARGET_REF": "synthetic", "FEATURE_ID": "synthetic", "EXPECTED_REVISION": "5",
        "STAGE": "verification", "ROLE": "qa", "CANDIDATE_PR_NUMBER": "552",
        "CANDIDATE_HEAD_SHA": "b" * 40, "TRUSTED_TASK_ID": "synthetic-task",
        "TASK_PAYLOAD": json.dumps({"task": {"id": "synthetic-task"}}),
        "DISPATCH_KEY": "dispatch-" + "c" * 40, "COMMENT_ID": "9002",
        "COMMENT_URL": "https://github.com/dream-xin/ai-sdlc/pull/552#issuecomment-9002"}
    for state in ("success", "failure", "skipped", "cancelled"):
        # The source expression is exactly the single equality asserted above.
        eligible = state == "success"
        check(eligible == (state not in {"failure", "skipped", "cancelled"}),
              "conclusion eligibility matrix differs")
        if not eligible:
            continue
        completed_identity = subprocess.run(["bash", "-c", conclusion_step["run"]],
            env=identity_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        check(completed_identity.returncode == 0, "actual successful conclusion script rejected identity")
        missing = dict(identity_env, COMMENT_ID="")
        rejected_identity = subprocess.run(["bash", "-c", conclusion_step["run"]],
            env=missing, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        check(rejected_identity.returncode != 0, "successful Safe Outputs without comment ID passed identity")
    print("Current observer temp/home and success-only conclusion source/compiled boundary verified")
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", OBSERVER_BOUNDARY_NODE,
         "observer-boundary-fixture", str(ROOT / "scripts/v03_detector_observation.mjs")],
        input=report_script.encode(), env={"PATH": os.environ["PATH"]}, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=60, check=False,
    )
    report = json.loads(completed.stdout)
    print(json.dumps(report, sort_keys=True))
    check(completed.returncode == 0, "observer boundary suite failed: " + report.get("case", "unknown"))
    check(report["status"] == "PASS" and report["case_count"] == 31,
          "observer finite case matrix drift")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--actions-root", type=Path)
    parser.add_argument("--observer-only", action="store_true")
    parser.add_argument("--prepare-schema-capture", type=Path)
    args = parser.parse_args()
    if args.observer_only:
        observer_boundary_tests()
        return
    check(args.actions_root is not None, "--actions-root is required for official handler tests")
    actions_root = resolve_actions_root(args.actions_root)
    if args.prepare_schema_capture is not None:
        prepare_schema_capture(args.prepare_schema_capture, actions_root)
        return
    observer_boundary_tests()
    scanned_receipt_tests()
    with tempfile.TemporaryDirectory(prefix="v03-gate-output-") as directory:
        sanitizer_closure_tests(actions_root, Path(directory))
        for role in ("reviewer", "qa"):
            exercise_role(role, actions_root, Path(directory))
            inline_native_schema_tests(role, actions_root, Path(directory))
            temporary_id_contract_tests(role, actions_root, Path(directory))
            item_number_contract_tests(role, actions_root, Path(directory))
            exercise_role(role, actions_root, Path(directory), variant="structured-inline-local")
        exercise_role("qa", actions_root, Path(directory), variant="observed-inline-local")
        exercise_role("qa", actions_root, Path(directory), variant="observed-qa2-local")
    qa1_lock, _, _, qa1_metadata = selected_config("qa", variant="observed-inline-local")
    try:
        roundtrip_source_contract("qa", "observed-qa2-local", qa1_lock, qa1_metadata)
    except AssertionError as exc:
        check(str(exc) == "QA2 actual lock differs from frozen corrected producer",
              "QA2 historical-lock negative failed at an unexpected boundary")
    else:
        raise AssertionError("QA2 accepted same-filename historical QA1 lock")
    try:
        selected_config("reviewer", variant="observed-qa2-local")
    except AssertionError as exc:
        check(str(exc) == "observed workflow is QA-only",
              "QA2 wrong-role negative failed at an unexpected boundary")
    else:
        raise AssertionError("QA2 variant accepted another role")
    old_lock, _, _, old_metadata = selected_config("qa", variant="structured-inline-local")
    try:
        roundtrip_source_contract("qa", "observed-inline-local", old_lock, old_metadata)
    except AssertionError:
        pass
    else:
        raise AssertionError("old QA source silently substituted for observed source")
    try:
        selected_config("reviewer", variant="observed-inline-local")
    except AssertionError:
        pass
    else:
        raise AssertionError("observed QA source accepted Reviewer role")
    print("Structured Gate output contract passed with fake provider boundaries only.")

if __name__ == "__main__":
    main()
