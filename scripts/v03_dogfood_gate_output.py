#!/usr/bin/env python3
"""Unselected structured Gate transport; grants no execution or lifecycle authority."""
from __future__ import annotations
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import sys

class GateOutputContractError(ValueError):
    pass

START = "AI-SDLC-GATE-RESULT-V2"
END = "END-AI-SDLC-GATE-RESULT-V2"
FENCE = chr(96) * 3
SENTINEL = "AI-SDLC structured Gate recommendation."
CONTEXT_SCHEMA = "ai-sdlc.v03-gate-context/v1"
PROOF_SCHEMA = "ai-sdlc.v03-gate-render/v1"
SANITIZER_BLOBS = {"sanitize_content.cjs":"ddf832a66f6ebb02dcee0644c9857f0333229914","sanitize_content_core.cjs":"50c228a9a83eeb96e46a2c859c9b5fab765663af","markdown_code_region_balancer.cjs":"6e743cfbb540e4a368156d605a39566169c650a7","repo_helpers.cjs":"c6dbf209b8ddcaf0976afca8a535de42920d05eb","slash_command_matcher.cjs":"07c0c865f9ef2481ba665a0174c9fcde3662a7f3","glob_pattern_helpers.cjs":"7f8f41db39abc0d1fdecc5a04fda79123fd7f9e1","error_codes.cjs":"65f8e5e80f87130c26e59f09156721686cc61a60"}
MAX_CONTEXT_BYTES = 24576
MAX_DOCUMENT_BYTES = 12288
MAX_OUTPUT_BYTES = 131072
IDENTITY_KEYS = {"operation_id", "operation_generation", "external_dispatch_key",
    "semantic_effect_key", "dispatch_id", "feature_id", "task_id", "role", "stage",
    "expected_revision", "target_repository", "target_ref", "candidate_pr_number", "candidate_head_sha"}
PAYLOAD_IDENTITY_KEYS = ("feature_id", "task_id", "role", "stage", "expected_revision",
    "target_repository", "target_ref", "candidate_pr_number", "candidate_head_sha")
SCHEMAS = json.loads("{\"reviewer\":{\"$schema\":\"https://json-schema.org/draft/2020-12/schema\",\"$id\":\"https://ai-sdlc.dev/runtime/gh-aw/reviewer-result.schema.json\",\"type\":\"object\",\"required\":[\"version\",\"contract\",\"id\",\"feature_id\",\"task_id\",\"stage\",\"role\",\"expected_revision\",\"target_repository\",\"target_ref\",\"candidate_pr_number\",\"candidate_head_sha\",\"verdict\",\"findings\",\"evidence\",\"occurred_at\"],\"properties\":{\"version\":{\"const\":\"0.1.0\"},\"contract\":{\"const\":\"ai-sdlc-gh-aw-reviewer-result-v0.1\"},\"id\":{\"type\":\"string\",\"minLength\":1,\"pattern\":\"^[A-Za-z0-9._:-]+$\"},\"feature_id\":{\"type\":\"string\",\"minLength\":1},\"task_id\":{\"type\":\"string\",\"minLength\":1},\"stage\":{\"const\":\"code-review\"},\"role\":{\"const\":\"reviewer\"},\"expected_revision\":{\"type\":\"integer\",\"minimum\":0},\"target_repository\":{\"type\":\"string\",\"pattern\":\"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$\"},\"target_ref\":{\"type\":\"string\",\"minLength\":1},\"candidate_pr_number\":{\"type\":\"integer\",\"minimum\":1},\"candidate_head_sha\":{\"type\":\"string\",\"pattern\":\"^[0-9a-f]{40}$\"},\"verdict\":{\"enum\":[\"PASS\",\"REWORK\",\"BLOCKED\"]},\"findings\":{\"type\":\"array\",\"maxItems\":100,\"items\":{\"type\":\"object\",\"required\":[\"code\",\"severity\",\"message\"],\"properties\":{\"code\":{\"type\":\"string\",\"minLength\":1,\"maxLength\":80},\"severity\":{\"enum\":[\"BLOCKER\",\"MAJOR\",\"MINOR\",\"NOTE\"]},\"message\":{\"type\":\"string\",\"minLength\":1,\"maxLength\":4000}},\"additionalProperties\":false}},\"evidence\":{\"type\":\"array\",\"minItems\":1,\"items\":{\"type\":\"object\",\"required\":[\"id\",\"type\",\"status\",\"uri\"],\"properties\":{\"id\":{\"type\":\"string\",\"minLength\":1},\"type\":{\"const\":\"review\"},\"status\":{\"enum\":[\"pass\",\"fail\",\"warning\"]},\"uri\":{\"type\":\"string\",\"minLength\":1}},\"additionalProperties\":false}},\"occurred_at\":{\"type\":\"string\",\"format\":\"date-time\"},\"reason\":{\"type\":\"string\",\"minLength\":1,\"maxLength\":4000}},\"allOf\":[{\"if\":{\"properties\":{\"verdict\":{\"enum\":[\"REWORK\",\"BLOCKED\"]}}},\"then\":{\"required\":[\"reason\"]}}],\"additionalProperties\":false},\"qa\":{\"$schema\":\"https://json-schema.org/draft/2020-12/schema\",\"$id\":\"https://ai-sdlc.dev/runtime/gh-aw/qa-result.schema.json\",\"type\":\"object\",\"required\":[\"version\",\"contract\",\"id\",\"feature_id\",\"task_id\",\"stage\",\"role\",\"expected_revision\",\"target_repository\",\"target_ref\",\"candidate_pr_number\",\"candidate_head_sha\",\"verdict\",\"checks\",\"coverage\",\"evidence\",\"occurred_at\"],\"properties\":{\"version\":{\"const\":\"0.1.0\"},\"contract\":{\"const\":\"ai-sdlc-gh-aw-qa-result-v0.1\"},\"id\":{\"type\":\"string\",\"minLength\":1,\"pattern\":\"^[A-Za-z0-9._:-]+$\"},\"feature_id\":{\"type\":\"string\",\"minLength\":1},\"task_id\":{\"type\":\"string\",\"minLength\":1},\"stage\":{\"const\":\"verification\"},\"role\":{\"const\":\"qa\"},\"expected_revision\":{\"type\":\"integer\",\"minimum\":0},\"target_repository\":{\"type\":\"string\",\"pattern\":\"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$\"},\"target_ref\":{\"type\":\"string\",\"minLength\":1},\"candidate_pr_number\":{\"type\":\"integer\",\"minimum\":1},\"candidate_head_sha\":{\"type\":\"string\",\"pattern\":\"^[0-9a-f]{40}$\"},\"verdict\":{\"enum\":[\"PASS\",\"FAIL\",\"BLOCKED\"]},\"checks\":{\"type\":\"array\",\"minItems\":1,\"maxItems\":200,\"items\":{\"type\":\"object\",\"required\":[\"name\",\"status\"],\"properties\":{\"name\":{\"type\":\"string\",\"minLength\":1,\"maxLength\":200},\"status\":{\"enum\":[\"pass\",\"fail\",\"blocked\"]},\"detail\":{\"type\":\"string\",\"maxLength\":4000}},\"additionalProperties\":false}},\"coverage\":{\"type\":\"array\",\"minItems\":1,\"maxItems\":200,\"items\":{\"type\":\"object\",\"required\":[\"criterion\",\"status\"],\"properties\":{\"criterion\":{\"type\":\"string\",\"minLength\":1,\"maxLength\":500},\"status\":{\"enum\":[\"pass\",\"fail\",\"blocked\"]},\"evidence\":{\"type\":\"string\",\"maxLength\":4000}},\"additionalProperties\":false}},\"evidence\":{\"type\":\"array\",\"minItems\":1,\"items\":{\"type\":\"object\",\"required\":[\"id\",\"type\",\"status\",\"uri\"],\"properties\":{\"id\":{\"type\":\"string\",\"minLength\":1},\"type\":{\"const\":\"verification\"},\"status\":{\"enum\":[\"pass\",\"fail\",\"warning\"]},\"uri\":{\"type\":\"string\",\"minLength\":1}},\"additionalProperties\":false}},\"occurred_at\":{\"type\":\"string\",\"format\":\"date-time\"},\"reason\":{\"type\":\"string\",\"minLength\":1,\"maxLength\":4000}},\"allOf\":[{\"if\":{\"properties\":{\"verdict\":{\"enum\":[\"FAIL\",\"BLOCKED\"]}}},\"then\":{\"required\":[\"reason\"]}}],\"additionalProperties\":false}}")

def fail(message):
    raise GateOutputContractError(message)

def canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise GateOutputContractError("invalid canonical JSON") from exc

def sha256(raw):
    return hashlib.sha256(raw).hexdigest()

def strict_json(raw, limit=MAX_OUTPUT_BYTES):
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, bytes) or not 0 < len(raw) <= limit:
        fail("JSON size/type differs")
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                fail("duplicate JSON key")
            result[key] = value
        return result
    def reject_constant(value):
        fail("nonfinite JSON number")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GateOutputContractError("strict JSON required") from exc

def exact_keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        fail(label + " fields differ")

def hex_value(value, size, label):
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{" + str(size) + "}", value):
        fail(label + " digest differs")

def positive_int(value, label, minimum=1):
    if type(value) is not int or value < minimum:
        fail(label + " integer differs")

def validate_context(context, expected_identity=None):
    exact_keys(context, {"schema_version", "identity", "provenance", "documents", "context_sha256"}, "context")
    if context["schema_version"] != CONTEXT_SCHEMA or len(canonical(context)) > MAX_CONTEXT_BYTES:
        fail("context version/size differs")
    identity = context["identity"]
    exact_keys(identity, IDENTITY_KEYS, "context identity")
    for key in IDENTITY_KEYS - {"operation_generation", "expected_revision", "candidate_pr_number"}:
        if not isinstance(identity[key], str) or not identity[key] or len(identity[key]) > 500:
            fail("context identity string differs")
    positive_int(identity["operation_generation"], "generation", 0)
    positive_int(identity["expected_revision"], "revision", 0)
    positive_int(identity["candidate_pr_number"], "candidate")
    hex_value(identity["candidate_head_sha"], 40, "candidate")
    hex_value(identity["semantic_effect_key"], 64, "semantic effect")
    if (not re.fullmatch(r"op-[0-9a-f]{40}", identity["operation_id"])
            or not re.fullmatch(r"dispatch-[0-9a-f]{40}", identity["external_dispatch_key"])
            or identity["target_repository"].lower() != "dream-xin/ai-sdlc"
            or (identity["role"], identity["stage"]) not in {("reviewer", "code-review"), ("qa", "verification")}):
        fail("context scope differs")
    if expected_identity is not None and canonical(identity) != canonical(expected_identity):
        fail("context differs from dispatch identity")
    provenance = context["provenance"]
    exact_keys(provenance, {"store_commit_sha", "producer_source_sha", "producer_policy_digest"}, "provenance")
    for key in ("store_commit_sha", "producer_source_sha"):
        hex_value(provenance[key], 40, key)
    hex_value(provenance["producer_policy_digest"], 64, "policy")
    documents = context["documents"]
    if not isinstance(documents, list) or not 2 <= len(documents) <= 8:
        fail("context documents missing/bounded")
    kinds, uris = set(), set()
    for document in documents:
        exact_keys(document, {"kind", "uri", "content", "sha256", "source_head_sha",
                              "run_id", "receipt_sha256"}, "document")
        if document["kind"] not in {"approved_task", "candidate_document", "implementation", "review"}:
            fail("document kind differs")
        if (not isinstance(document["uri"], str) or not document["uri"]
                or len(document["uri"]) > 2048 or document["uri"] in uris):
            fail("document URI duplicate/invalid")
        if not isinstance(document["content"], str):
            fail("document content missing")
        raw = document["content"].encode("utf-8")
        if not 0 < len(raw) <= MAX_DOCUMENT_BYTES or sha256(raw) != document["sha256"]:
            fail("document content/hash differs")
        hex_value(document["source_head_sha"], 40, "document source")
        if document["kind"] in {"approved_task", "candidate_document"}:
            if document["source_head_sha"] != identity["candidate_head_sha"]:
                fail("Git-backed evidence candidate source differs")
            if document["run_id"] is not None or document["receipt_sha256"] is not None:
                fail("approved task must not invent a Worker receipt")
        else:
            positive_int(document["run_id"], "document run")
            hex_value(document["receipt_sha256"], 64, "document receipt")
        kinds.add(document["kind"])
        uris.add(document["uri"])
    required = {"approved_task", "candidate_document", "implementation"} | ({"review"} if identity["role"] == "qa" else set())
    if not required <= kinds:
        fail("required authenticated context is missing")
    body = {key: value for key, value in context.items() if key != "context_sha256"}
    if sha256(canonical(body)) != context["context_sha256"]:
        fail("context digest differs")
    return context

def _schema_check(value, schema, path="result"):
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int}
    if kind and type(value) is not types[kind]:
        fail(path + " type differs")
    if "const" in schema and canonical(value) != canonical(schema["const"]):
        fail(path + " constant differs")
    if "enum" in schema and canonical(value) not in [canonical(v) for v in schema["enum"]]:
        fail(path + " enum differs")
    if kind == "object":
        if not set(schema.get("required", ())) <= set(value):
            fail(path + " required field missing")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and not set(value) <= set(properties):
            fail(path + " unknown field")
        for key, child in value.items():
            if key in properties:
                _schema_check(child, properties[key], path + "." + key)
    if kind == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 256):
            fail(path + " array bound differs")
        for child in value:
            _schema_check(child, schema["items"], path + "[]")
    if kind == "string":
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 8192):
            fail(path + " string bound differs")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            fail(path + " string pattern differs")
        if schema.get("format") == "date-time":
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value):
                    fail(path + " date-time differs")
                datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise GateOutputContractError(path + " invalid date-time") from exc
    if kind == "integer" and value < schema.get("minimum", value):
        fail(path + " number bound differs")

def validate_payload(payload, context, role):
    validate_context(context)
    if role not in SCHEMAS or context["identity"]["role"] != role:
        fail("role differs")
    _schema_check(payload, SCHEMAS[role])
    if payload["verdict"] != "PASS" and not payload.get("reason"):
        fail("non-PASS result requires reason")
    if canonical({key: payload[key] for key in PAYLOAD_IDENTITY_KEYS}) != canonical(
            {key: context["identity"][key] for key in PAYLOAD_IDENTITY_KEYS}):
        fail("result identity differs")
    if role == "reviewer" and payload["verdict"] == "PASS" and any(
            row["severity"] in {"BLOCKER", "MAJOR"} for row in payload["findings"]):
        fail("PASS conflicts with material findings")
    if role == "qa" and payload["verdict"] == "PASS" and any(
            row["status"] != "pass" for row in payload["checks"] + payload["coverage"]):
        fail("PASS conflicts with verification results")
    return payload

def envelope(payload):
    return START + "\n" + FENCE + "json\n" + json.dumps(
        payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n" + FENCE + "\n" + END

def render_gate_output(raw_ndjson, collected_json, context, role, *, sanitize):
    validate_context(context)
    if not isinstance(raw_ndjson, bytes) or len(raw_ndjson) > MAX_OUTPUT_BYTES:
        fail("raw output size differs")
    rows = [line for line in raw_ndjson.splitlines() if line.strip()]
    if len(rows) != 1:
        fail("exactly one raw Gate item required")
    raw = strict_json(rows[0])
    exact_keys(raw, {"type", "body", "item_number", "data"}, "raw item")
    if raw["type"] != "add_comment" or raw["body"] != SENTINEL:
        fail("raw Gate body/type differs")
    if type(raw["item_number"]) is not int or raw["item_number"] != context["identity"]["candidate_pr_number"]:
        fail("raw Gate target differs")
    payload = validate_payload(raw["data"], context, role)
    collected = strict_json(collected_json)
    exact_keys(collected, {"items", "errors"}, "collected output")
    if collected["errors"] != [] or not isinstance(collected["items"], list) or len(collected["items"]) != 1:
        fail("ingestion errors or item count differs")
    item = collected["items"][0]
    exact_keys(item, {"type", "body", "item_number", "data"}, "ingested item")
    if (canonical(item["data"]) != canonical(payload) or item["type"] != raw["type"]
            or canonical(item["item_number"]) != canonical(raw["item_number"])):
        fail("ingestion altered structured fields")
    native_body = SENTINEL + "\n\nStructured data:\n" + FENCE + "json\n" + json.dumps(
        payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n" + FENCE
    if item["body"] != native_body:
        fail("ingested body conflicts with structured data")
    body = envelope(payload)
    if not callable(sanitize) or sanitize(body) != body:
        fail("official sanitizer changes canonical Gate body")
    proof = {"schema_version": PROOF_SCHEMA, "context_sha256": context["context_sha256"],
             "payload_sha256": sha256(canonical(payload)), "body_sha256": sha256(body.encode("utf-8")),
             "raw_ndjson_sha256": sha256(raw_ndjson), "role": role}
    output = {"items": [{"type": "add_comment", "body": body, "item_number": raw["item_number"]}],
              "errors": [], "gate_render_proof": proof}
    encoded = canonical(output)
    if len(encoded) > MAX_OUTPUT_BYTES:
        fail("rendered output too large")
    return encoded, proof

def validate_scanned_output(collected_json, proof, context, role, *, scanned_sha256):
    validate_context(context)
    hex_value(scanned_sha256, 64, "independent scanned output")
    if not isinstance(collected_json, bytes) or sha256(collected_json) != scanned_sha256:
        fail("artifact differs from independently bound detector input")
    output = strict_json(collected_json)
    exact_keys(output, {"items", "errors", "gate_render_proof"}, "scanned output")
    exact_keys(proof, {"schema_version", "context_sha256", "payload_sha256", "body_sha256",
                      "raw_ndjson_sha256", "role"}, "render proof")
    if (proof["schema_version"] != PROOF_SCHEMA or proof["role"] != role
            or proof["context_sha256"] != context["context_sha256"]
            or canonical(output["gate_render_proof"]) != canonical(proof)
            or output["errors"] != [] or not isinstance(output["items"], list)
            or len(output["items"]) != 1 or canonical(output) != collected_json):
        fail("scanned canonical artifact/proof differs")
    hex_value(proof["raw_ndjson_sha256"], 64, "raw output")
    item = output["items"][0]
    exact_keys(item, {"type", "body", "item_number"}, "scanned item")
    if (item["type"] != "add_comment" or type(item["item_number"]) is not int
            or item["item_number"] != context["identity"]["candidate_pr_number"]):
        fail("scanned target differs")
    payload = parse_published_gate(item["body"], context, role, metadata_suffix="")
    if (proof["payload_sha256"] != sha256(canonical(payload))
            or proof["body_sha256"] != sha256(item["body"].encode("utf-8"))):
        fail("scanned body digest differs")
    return payload

def parse_published_gate(body, context, role, metadata_suffix):
    if not isinstance(body, str) or not isinstance(metadata_suffix, str):
        fail("published body type differs")
    prefix = START + "\n" + FENCE + "json\n"
    ending = "\n" + FENCE + "\n" + END
    suffix = "\n\n" + metadata_suffix if metadata_suffix else ""
    if (not body.startswith(prefix) or not body.endswith(ending + suffix)
            or body.splitlines().count(START) != 1 or body.splitlines().count(END) != 1
            or body.splitlines().count(FENCE + "json") != 1 or body.splitlines().count(FENCE) != 1):
        fail("published envelope/metadata differs")
    payload_text = body[len(prefix):len(body) - len(ending + suffix)]
    payload = validate_payload(strict_json(payload_text), context, role)
    if body != envelope(payload) + suffix:
        fail("published canonical bytes differ")
    return payload

def context_from_environment(environ):
    payload = strict_json(environ.get("TASK_PAYLOAD", "").encode("utf-8"), limit=65536)
    try:
        feature, task = payload["feature_context"], payload["task"]
        vertical = feature["vertical"]
        role = environ["ROLE"]
        if (not re.fullmatch(r"0|[1-9][0-9]*", environ["EXPECTED_REVISION"])
                or not re.fullmatch(r"[1-9][0-9]*", environ["CANDIDATE_PR_NUMBER"])):
            fail("dispatch number spelling differs")
        expected = {
            "operation_id": vertical["operation_id"], "operation_generation": vertical["operation_generation"],
            "external_dispatch_key": environ["DISPATCH_KEY"], "semantic_effect_key": vertical["semantic_effect_key"],
            "dispatch_id": vertical["dispatch_id"], "feature_id": environ["FEATURE_ID"],
            "task_id": task["id"], "role": role, "stage": environ["STAGE"],
            "expected_revision": int(environ["EXPECTED_REVISION"]),
            "target_repository": environ["TARGET_REPOSITORY"], "target_ref": environ["TARGET_REF"],
            "candidate_pr_number": int(environ["CANDIDATE_PR_NUMBER"]),
            "candidate_head_sha": environ["CANDIDATE_HEAD_SHA"]}
        if (payload["contract"] != "ai-sdlc-task-v0.1"
                or vertical["profile"] != "vertical-implementation-review-qa/v1"
                or task["feature_id"] != expected["feature_id"] or task["role"] != role
                or feature["id"] != expected["feature_id"] or feature["repository"] != expected["target_repository"]
                or vertical["external_dispatch_key"] != expected["external_dispatch_key"]
                or canonical(vertical["expected_revision"]) != canonical(expected["expected_revision"])
                or vertical["candidate_head_sha"] != expected["candidate_head_sha"]):
            fail("trusted task identity differs")
        return validate_context(feature["gate_context"], expected)
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, GateOutputContractError):
            raise
        raise GateOutputContractError("authenticated Gate context missing/invalid") from exc


def scanned_receipt_digest(raw, *, run_id, run_attempt, workflow_sha):
    receipt = strict_json(raw, limit=4096)
    exact_keys(receipt, {"schema_version", "run_id", "run_attempt", "workflow_sha", "sha256"}, "scan byte receipt")
    positive_int(run_id, "current run")
    positive_int(run_attempt, "current attempt")
    hex_value(workflow_sha, 40, "current workflow")
    if (receipt["schema_version"] != "ai-sdlc.v03-gate-scanned-bytes/v1"
            or type(receipt["run_id"]) is not int or receipt["run_id"] != run_id
            or type(receipt["run_attempt"]) is not int or receipt["run_attempt"] != 1 or run_attempt != 1
            or receipt["workflow_sha"] != workflow_sha):
        fail("scan byte receipt run/source binding differs")
    hex_value(receipt["sha256"], 64, "scan byte receipt")
    return receipt["sha256"]


def _read_regular(path, maximum):
    path = Path(path)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents) or not path.is_file():
        fail("input is not a regular nonsymlink file")
    if path.stat().st_size > maximum:
        fail("input exceeds bound")
    return path.read_bytes()

def _official_sanitizer(actions_root):
    verified = {}
    for name, expected in SANITIZER_BLOBS.items():
        raw = _read_regular(Path(actions_root) / name, 1024 * 1024)
        if hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\x00" + raw).hexdigest() != expected:
            fail("official sanitizer dependency differs")
        verified[name] = raw
    script = (
        "const fs=require('fs');global.core={info(){},warning(){},debug(){},error(){}};"
        "const {sanitizeContent}=require(process.argv[1]);"
        "process.stdout.write(JSON.stringify(sanitizeContent(JSON.parse(fs.readFileSync(0,'utf8')))));")
    allowed = ("PATH", "GH_AW_ALLOWED_DOMAINS", "GH_AW_SAFE_OUTPUTS_URLS", "GITHUB_SERVER_URL", "GITHUB_API_URL",
               "GH_AW_COMMANDS", "GH_AW_ALLOWED_GITHUB_REFS", "GITHUB_REPOSITORY",
               "GH_AW_TARGET_REPO_SLUG", "GH_AW_FAILURE_ISSUE_REPO",
               "GH_AW_FAILURE_ISSUE_REPO_FROM_EXPRESSION")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    def sanitize(body):
        # Verify first, then execute a fresh private copy, never model-visible source.
        if Path("/tmp").is_symlink():
            fail("sanitizer temporary root differs")
        with tempfile.TemporaryDirectory(prefix="ai-sdlc-gate-sanitizer-", dir="/tmp") as directory:
            for name, raw in verified.items():
                descriptor = os.open(Path(directory) / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(raw)
            result = subprocess.run(["node", "-e", script, str(Path(directory) / "sanitize_content.cjs")],
                input=json.dumps(body, ensure_ascii=False).encode("utf-8"), stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=15, check=False, env=environment)
        if result.returncode != 0 or len(result.stdout) > MAX_OUTPUT_BYTES:
            fail("official sanitizer failed")
        value = strict_json(result.stdout)
        if not isinstance(value, str):
            fail("official sanitizer returned invalid output")
        return value
    return sanitize

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("context", "render", "validate"))
    parser.add_argument("--actions-root")
    args = parser.parse_args()
    context = context_from_environment(os.environ)
    role = context["identity"]["role"]
    if os.environ.get("RUN_ATTEMPT") != "1":
        fail("first attempt required")
    if args.mode == "context":
        value = canonical(context).decode("utf-8")
        output = os.environ.get("GITHUB_OUTPUT")
        if not output:
            fail("context output channel missing")
        with open(output, "a", encoding="utf-8") as handle:
            handle.write("context_json=" + value + "\n")
        print("Authenticated dispatch context validated.")
        return
    path = Path("/tmp/gh-aw/agent_output.json")
    collected = _read_regular(path, MAX_OUTPUT_BYTES)
    if args.mode == "render":
        if not args.actions_root:
            fail("pinned official sanitizer is required")
        raw = _read_regular("/tmp/gh-aw/safeoutputs.jsonl", MAX_OUTPUT_BYTES)
        result, _ = render_gate_output(raw, collected, context, role, sanitize=_official_sanitizer(args.actions_root))
        temporary = path.with_name("agent_output.ai-sdlc-render.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(result)
        os.replace(temporary, path)
        print("Canonical Gate output rendered before detection.")
        return
    if (os.environ.get("AGENT_RESULT") != "success" or os.environ.get("DETECTION_RESULT") != "success"
            or os.environ.get("DETECTION_SUCCESS") != "true"
            or os.environ.get("DETECTION_CONCLUSION") != "success"):
        fail("agent success and affirmative detection required")
    receipt_path = Path(os.environ.get("SCANNED_RECEIPT_PATH", ""))
    expected_path = Path(os.environ.get("RUNNER_TEMP", "")) / "ai-sdlc-gate-scan-receipt" / "receipt.json"
    if not expected_path.is_absolute() or receipt_path != expected_path:
        fail("scan byte receipt path differs")
    if (receipt_path.parent.is_symlink() or not receipt_path.parent.is_dir()
            or set(receipt_path.parent.iterdir()) != {receipt_path}):
        fail("scan byte receipt directory differs")
    run_id = os.environ.get("SOURCE_RUN_ID", "")
    if not re.fullmatch(r"[1-9][0-9]*", run_id):
        fail("current run identity missing")
    scanned_digest = scanned_receipt_digest(_read_regular(receipt_path, 4096),
        run_id=int(run_id), run_attempt=1, workflow_sha=os.environ.get("SOURCE_WORKFLOW_SHA"))
    output = strict_json(collected)
    validate_scanned_output(collected, output.get("gate_render_proof"), context, role,
                            scanned_sha256=scanned_digest)
    print("Scanned Gate output identity, schema and bytes validated.")

if __name__ == "__main__":
    try:
        main()
    except (GateOutputContractError, OSError, subprocess.SubprocessError):
        print("Structured Gate contract blocked.", file=sys.stderr)
        raise SystemExit(1)
