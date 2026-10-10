#!/usr/bin/env python3
"""No-model tests for the fixed diagnostic admission; fake provider boundaries only."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from unittest.mock import patch
import v03_detector_diagnostic_admission as a


OLD_COMMENT=json.loads("{\"id\":6092102097,\"node_id\":\"IC_kwDOTw3ETM8AAAABax4Z0Q\",\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/issues/comments/6092102097\",\"html_url\":\"https://github.com/DREAM-XIN/ai-sdlc/pull/581#issuecomment-6092102097\",\"issue_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/issues/581\",\"author_association\":\"OWNER\",\"body\":\"AI-SDLC assistant-issued detector-only admission v1\\n{\\\"assistant_issued\\\":true,\\\"checkout_sha\\\":\\\"78854123516ee7cf8b98b99f8b520dea4d333123\\\",\\\"checkout_tree\\\":\\\"584d30a13b66fbfb572f3365e9d27fff4ddd3f3c\\\",\\\"configuration_sha256\\\":\\\"566d526832f2596ef5b4772378968f17fbb1e18c5b1be0a506efdb515c812933\\\",\\\"expires_at\\\":\\\"2026-10-10T01:26:00Z\\\",\\\"input_manifest\\\":{\\\"artifacts\\\":[{\\\"consumed_paths\\\":[\\\"aw-prompts/prompt-import-tree.json\\\",\\\"aw-prompts/prompt-template.txt\\\",\\\"aw-prompts/prompt.txt\\\",\\\"aw_info.json\\\"],\\\"id\\\":11614602643,\\\"name\\\":\\\"activation\\\",\\\"sha256\\\":\\\"473677789b3d60cefe6f821ce470a81570d3d8d46dbd917b6d16f71beaf832e0\\\",\\\"size_bytes\\\":1083418},{\\\"consumed_paths\\\":[\\\"agent_output.json\\\",\\\"aw-prompts/prompt.txt\\\"],\\\"id\\\":11614239306,\\\"name\\\":\\\"agent\\\",\\\"sha256\\\":\\\"41906db5ed959e854f7466925c9e594f3519fd2e58f11985b0ddbbe98f8b42e5\\\",\\\"size_bytes\\\":1064768},{\\\"consumed_paths\\\":[\\\"agent_output.json\\\"],\\\"id\\\":11614806653,\\\"name\\\":\\\"agent-output-fallback\\\",\\\"sha256\\\":\\\"8aaa58dd2068255f44ce825c677f31a4d9805dcddef34fc3d9c57bfeb9807641\\\",\\\"size_bytes\\\":7320}],\\\"files\\\":[{\\\"artifact_ids\\\":[11614239306,11614806653],\\\"path\\\":\\\"agent_output.json\\\",\\\"sha256\\\":\\\"6a3cbd681a29a093c3e968e89943bdf912ac739ff79090433a6fdcc48d7b4f34\\\",\\\"size_bytes\\\":6923},{\\\"artifact_ids\\\":[11614602643],\\\"path\\\":\\\"aw-prompts/prompt-import-tree.json\\\",\\\"sha256\\\":\\\"0b6c5848331da0e1207a432732e2e93c2ed5e4dd3cd6e9fc9547611d2662f290\\\",\\\"size_bytes\\\":15028},{\\\"artifact_ids\\\":[11614602643],\\\"path\\\":\\\"aw-prompts/prompt-template.txt\\\",\\\"sha256\\\":\\\"70b28d8ffa983163f50628bc4e217dd3f8a17617887ddc1b1e94eb175a63876b\\\",\\\"size_bytes\\\":10152},{\\\"artifact_ids\\\":[11614239306,11614602643],\\\"path\\\":\\\"aw-prompts/prompt.txt\\\",\\\"sha256\\\":\\\"d835491212be4a2464245d6dfaf047a2bf29a7f089eed07da9532ab3397d910f\\\",\\\"size_bytes\\\":4328},{\\\"artifact_ids\\\":[11614602643],\\\"path\\\":\\\"aw_info.json\\\",\\\"sha256\\\":\\\"f0c9b09177617e6b3534fc18ce20c998281bec8cf697b274d2450b5080c9afea\\\",\\\"size_bytes\\\":892}],\\\"manifest_sha256\\\":\\\"893f5362e3e35ba955c9fe53c9317fc529132349f06b4beab6c954d693087703\\\",\\\"patch_files\\\":0,\\\"reconstruction\\\":{\\\"actions_commit\\\":\\\"924af5fdc64061cfbf66fb584c8b07e2ac230c60\\\",\\\"export\\\":\\\"stripFrameworkSystemBlock\\\",\\\"mode\\\":\\\"pinned-official-setup-transform\\\",\\\"original_consumed_sizes_corroborated\\\":true,\\\"original_post_transform_hash_available\\\":false,\\\"setup_module_blob\\\":\\\"0f5803962654d4e856a2515add11df7b2d43a870\\\"},\\\"repository\\\":\\\"DREAM-XIN/ai-sdlc\\\",\\\"run_attempt\\\":1,\\\"run_id\\\":37927328438,\\\"schema\\\":\\\"v03-fixed-detector-input-manifest/v1\\\",\\\"source_files\\\":[{\\\"artifact_ids\\\":[11614239306,11614806653],\\\"path\\\":\\\"agent_output.json\\\",\\\"sha256\\\":\\\"6a3cbd681a29a093c3e968e89943bdf912ac739ff79090433a6fdcc48d7b4f34\\\",\\\"size_bytes\\\":6923},{\\\"artifact_ids\\\":[11614602643],\\\"path\\\":\\\"aw-prompts/prompt-import-tree.json\\\",\\\"sha256\\\":\\\"0b6c5848331da0e1207a432732e2e93c2ed5e4dd3cd6e9fc9547611d2662f290\\\",\\\"size_bytes\\\":15028},{\\\"artifact_ids\\\":[11614602643],\\\"path\\\":\\\"aw-prompts/prompt-template.txt\\\",\\\"sha256\\\":\\\"70b28d8ffa983163f50628bc4e217dd3f8a17617887ddc1b1e94eb175a63876b\\\",\\\"size_bytes\\\":10152},{\\\"artifact_ids\\\":[11614239306,11614602643],\\\"path\\\":\\\"aw-prompts/prompt.txt\\\",\\\"sha256\\\":\\\"39a506b0584876ebed05bc1ef43a64a679585a57365f51847916b167e1d929fc\\\",\\\"size_bytes\\\":13660},{\\\"artifact_ids\\\":[11614602643],\\\"path\\\":\\\"aw_info.json\\\",\\\"sha256\\\":\\\"f0c9b09177617e6b3534fc18ce20c998281bec8cf697b274d2450b5080c9afea\\\",\\\"size_bytes\\\":892}],\\\"source_head\\\":\\\"193d96474529556cc0d805bb9be2b0a96909777b\\\"},\\\"job_id\\\":114093376527,\\\"job_name\\\":\\\"Fixed failed Reviewer detector diagnostic\\\",\\\"job_started_at\\\":\\\"2026-10-10T01:06:17Z\\\",\\\"limits\\\":{\\\"detector_retries\\\":0,\\\"engine_timeout_seconds\\\":300,\\\"harness_retries\\\":0,\\\"local_invocations\\\":1,\\\"max_runs\\\":50},\\\"pr_head_sha\\\":\\\"4e0a2d13ab816e0772f526fe9bd435df9eb52433\\\",\\\"pull_request\\\":581,\\\"purpose\\\":\\\"diagnostic-only: failed Reviewer 37927328438; no release authority\\\",\\\"repository\\\":\\\"DREAM-XIN/ai-sdlc\\\",\\\"run_attempt\\\":1,\\\"run_id\\\":38011769045,\\\"schema\\\":\\\"v03-assistant-detector-diagnostic-admission/v1\\\",\\\"source_blobs\\\":{\\\".github/workflows/validate-v03-dogfood-live-gate.yml\\\":\\\"a2ecf4a19b62a976188126b97f971dedb0baa5bc\\\",\\\"scripts/v03_detector_awf_config.json\\\":\\\"966b092676b2528f741d8f9f74457f93cc3d85b9\\\",\\\"scripts/v03_detector_diagnostic_admission.py\\\":\\\"aace690d675718989f7f2020f87f19a22a26ffc1\\\",\\\"scripts/v03_detector_diagnostic_config.json\\\":\\\"8cacb335614004791fdca545bb2304519e3f282c\\\",\\\"scripts/v03_detector_diagnostic_execution.sh\\\":\\\"cd3af666afd3665f6671382c3efea28db8bca34f\\\",\\\"scripts/v03_detector_input_manifest.py\\\":\\\"74deb06375b96ac11ea4c3bee58e7427e0d18a12\\\"},\\\"workflow_sha\\\":\\\"78854123516ee7cf8b98b99f8b520dea4d333123\\\"}\",\"created_at\":\"2026-10-10T01:19:29Z\",\"updated_at\":\"2026-10-10T01:19:29Z\",\"user\":{\"id\":33620907,\"login\":\"DREAM-XIN\"}}")
OLD_PROOF=json.loads("{\"request\":{\"at\":\"2026-10-10T01:07:05.2650596Z\",\"value\":{\"schema\":\"v03-detector-admission-request/v1\",\"identity\":{\"repository\":\"DREAM-XIN/ai-sdlc\",\"pull_request\":581,\"run_id\":38011769045,\"run_attempt\":1,\"job_id\":114093376527,\"job_name\":\"Fixed failed Reviewer detector diagnostic\",\"job_started_at\":\"2026-10-10T01:06:17Z\",\"pr_head_sha\":\"4e0a2d13ab816e0772f526fe9bd435df9eb52433\",\"workflow_sha\":\"78854123516ee7cf8b98b99f8b520dea4d333123\",\"checkout_sha\":\"78854123516ee7cf8b98b99f8b520dea4d333123\",\"checkout_tree\":\"584d30a13b66fbfb572f3365e9d27fff4ddd3f3c\",\"source_blobs\":{\".github/workflows/validate-v03-dogfood-live-gate.yml\":\"a2ecf4a19b62a976188126b97f971dedb0baa5bc\",\"scripts/v03_detector_diagnostic_admission.py\":\"aace690d675718989f7f2020f87f19a22a26ffc1\",\"scripts/v03_detector_input_manifest.py\":\"74deb06375b96ac11ea4c3bee58e7427e0d18a12\",\"scripts/v03_detector_diagnostic_config.json\":\"8cacb335614004791fdca545bb2304519e3f282c\",\"scripts/v03_detector_diagnostic_execution.sh\":\"cd3af666afd3665f6671382c3efea28db8bca34f\",\"scripts/v03_detector_awf_config.json\":\"966b092676b2528f741d8f9f74457f93cc3d85b9\"},\"configuration_sha256\":\"566d526832f2596ef5b4772378968f17fbb1e18c5b1be0a506efdb515c812933\",\"limits\":{\"max_runs\":50,\"harness_retries\":0,\"detector_retries\":0,\"engine_timeout_seconds\":300,\"local_invocations\":1}},\"input_manifest\":{\"schema\":\"v03-fixed-detector-input-manifest/v1\",\"repository\":\"DREAM-XIN/ai-sdlc\",\"run_id\":37927328438,\"run_attempt\":1,\"source_head\":\"193d96474529556cc0d805bb9be2b0a96909777b\",\"artifacts\":[{\"id\":11614602643,\"name\":\"activation\",\"size_bytes\":1083418,\"sha256\":\"473677789b3d60cefe6f821ce470a81570d3d8d46dbd917b6d16f71beaf832e0\",\"consumed_paths\":[\"aw-prompts/prompt-import-tree.json\",\"aw-prompts/prompt-template.txt\",\"aw-prompts/prompt.txt\",\"aw_info.json\"]},{\"id\":11614239306,\"name\":\"agent\",\"size_bytes\":1064768,\"sha256\":\"41906db5ed959e854f7466925c9e594f3519fd2e58f11985b0ddbbe98f8b42e5\",\"consumed_paths\":[\"agent_output.json\",\"aw-prompts/prompt.txt\"]},{\"id\":11614806653,\"name\":\"agent-output-fallback\",\"size_bytes\":7320,\"sha256\":\"8aaa58dd2068255f44ce825c677f31a4d9805dcddef34fc3d9c57bfeb9807641\",\"consumed_paths\":[\"agent_output.json\"]}],\"patch_files\":0,\"reconstruction\":{\"mode\":\"pinned-official-setup-transform\",\"actions_commit\":\"924af5fdc64061cfbf66fb584c8b07e2ac230c60\",\"setup_module_blob\":\"0f5803962654d4e856a2515add11df7b2d43a870\",\"export\":\"stripFrameworkSystemBlock\",\"original_consumed_sizes_corroborated\":true,\"original_post_transform_hash_available\":false},\"source_files\":[{\"path\":\"agent_output.json\",\"size_bytes\":6923,\"sha256\":\"6a3cbd681a29a093c3e968e89943bdf912ac739ff79090433a6fdcc48d7b4f34\",\"artifact_ids\":[11614239306,11614806653]},{\"path\":\"aw-prompts/prompt-import-tree.json\",\"size_bytes\":15028,\"sha256\":\"0b6c5848331da0e1207a432732e2e93c2ed5e4dd3cd6e9fc9547611d2662f290\",\"artifact_ids\":[11614602643]},{\"path\":\"aw-prompts/prompt-template.txt\",\"size_bytes\":10152,\"sha256\":\"70b28d8ffa983163f50628bc4e217dd3f8a17617887ddc1b1e94eb175a63876b\",\"artifact_ids\":[11614602643]},{\"path\":\"aw-prompts/prompt.txt\",\"size_bytes\":13660,\"sha256\":\"39a506b0584876ebed05bc1ef43a64a679585a57365f51847916b167e1d929fc\",\"artifact_ids\":[11614239306,11614602643]},{\"path\":\"aw_info.json\",\"size_bytes\":892,\"sha256\":\"f0c9b09177617e6b3534fc18ce20c998281bec8cf697b274d2450b5080c9afea\",\"artifact_ids\":[11614602643]}],\"files\":[{\"path\":\"agent_output.json\",\"size_bytes\":6923,\"sha256\":\"6a3cbd681a29a093c3e968e89943bdf912ac739ff79090433a6fdcc48d7b4f34\",\"artifact_ids\":[11614239306,11614806653]},{\"path\":\"aw-prompts/prompt-import-tree.json\",\"size_bytes\":15028,\"sha256\":\"0b6c5848331da0e1207a432732e2e93c2ed5e4dd3cd6e9fc9547611d2662f290\",\"artifact_ids\":[11614602643]},{\"path\":\"aw-prompts/prompt-template.txt\",\"size_bytes\":10152,\"sha256\":\"70b28d8ffa983163f50628bc4e217dd3f8a17617887ddc1b1e94eb175a63876b\",\"artifact_ids\":[11614602643]},{\"path\":\"aw-prompts/prompt.txt\",\"size_bytes\":4328,\"sha256\":\"d835491212be4a2464245d6dfaf047a2bf29a7f089eed07da9532ab3397d910f\",\"artifact_ids\":[11614239306,11614602643]},{\"path\":\"aw_info.json\",\"size_bytes\":892,\"sha256\":\"f0c9b09177617e6b3534fc18ce20c998281bec8cf697b274d2450b5080c9afea\",\"artifact_ids\":[11614602643]}],\"manifest_sha256\":\"893f5362e3e35ba955c9fe53c9317fc529132349f06b4beab6c954d693087703\"},\"marker_prefix\":\"AI-SDLC assistant-issued detector-only admission v1\\n\",\"marker_schema\":\"v03-assistant-detector-diagnostic-admission/v1\",\"purpose\":\"diagnostic-only: failed Reviewer 37927328438; no release authority\",\"assistant_issued\":true,\"wait_deadline\":\"2026-10-10T01:26:17+00:00\"}},\"blocked\":\"2026-10-10T01:19:47.0339355Z fixed detector admission blocked: ValueError\",\"summary\":{\"at\":\"2026-10-10T01:19:47.1437429Z\",\"value\":{\"diagnostic_only\":true,\"diagnostic_success\":false,\"invocation_consumed\":false,\"release_authority\":false,\"scanner_outcome\":\"failure\",\"schema\":\"v03-fixed-detector-diagnostic-summary/v1\",\"verdict_valid\":false}},\"exit\":\"2026-10-10T01:19:47.0477710Z ##[error]Process completed with exit code 1.\"}")
def old_log():
    return "\n".join([
        "finally {process.stdout.write=out;process.stderr.write=err;fs.closeSync(fd);}",
        OLD_PROOF["request"]["at"]+" "+json.dumps(OLD_PROOF["request"]["value"]),
        OLD_PROOF["blocked"],OLD_PROOF["exit"],
        OLD_PROOF["summary"]["at"]+" "+json.dumps(OLD_PROOF["summary"]["value"]),
    ])+"\n"


def iso(seconds):
    return datetime.fromtimestamp(seconds,timezone.utc).isoformat()

def expect(value,message):
    if not value:
        raise AssertionError(message)

def reject(action,label):
    try:
        action()
    except (ValueError, FileExistsError):
        return
    raise AssertionError("accepted invalid diagnostic admission: "+label)

def main():
    import argparse
    import yaml
    import jsonschema
    parser=argparse.ArgumentParser()
    parser.add_argument("--compiled-lock",required=True)
    parser.add_argument("--awf-schema",required=True)
    args=parser.parse_args()
    compiled_raw=Path(args.compiled_lock).read_bytes()
    metadata=json.loads(Path(a.CONFIG).read_text())
    expect(hashlib.sha256(compiled_raw).hexdigest()==metadata["compiled_lock_sha256"],
           "compiled detector reference bytes differ")
    generated=yaml.safe_load(compiled_raw)
    engine=next(step for step in generated["jobs"]["detection"]["steps"]
                if step.get("id")=="detection_agentic_execution")
    prefix="printf '%s\\n' \""
    suffix="\" > \"${RUNNER_TEMP}/gh-aw/awf-config.json\""
    lines=[line for line in engine["run"].splitlines() if line.startswith(prefix) and line.endswith(suffix)]
    expect(len(lines)==1,"generated native config is not unique")
    literal=lines[0][len(prefix):-len(suffix)].replace("${GH_AW_MAX_AI_CREDITS}","400")
    literal=literal.replace('\\"','"').replace('\\$','$')
    original=json.loads(literal)
    expect(type(original["apiProxy"]["maxRuns"]) is int and original["apiProxy"]["maxRuns"]==500
           and "maxTurns" not in original["apiProxy"],"compiler limitation incorrectly represented")
    expected=copy.deepcopy(original);expected["apiProxy"]["maxRuns"]=50
    actual=json.loads(Path(a.NATIVE_CONFIG).read_text())
    expect(a.canonical(actual)==a.canonical(expected),"authored config changed more than native cap")
    jsonschema.validate(actual,json.loads(Path(args.awf_schema).read_text()))
    copy_line='cp -- "scripts/v03_detector_awf_config.json" "${RUNNER_TEMP}/gh-aw/awf-config.json"'
    expect(Path(a.EXECUTION).read_text()==engine["run"].replace(lines[0],copy_line),
           "authored execution changed more than fixed configuration copy")
    expect(a.validate_execution_script()==actual,"native 50 config rejected")
    with tempfile.TemporaryDirectory() as directory:
        config_path=Path(directory)/"native.json"
        for cap in (500,5000,51,0,50.0,True,"50"):
            bad=copy.deepcopy(actual);bad["apiProxy"]["maxRuns"]=cap
            config_path.write_text(json.dumps(bad))
            with patch.object(a,"NATIVE_CONFIG",str(config_path)):
                reject(a.validate_execution_script,"native cap "+str(cap))
        config_path.write_text('{"apiProxy":{"maxRuns":50,"maxRuns":500}}\n')
        with patch.object(a,"NATIVE_CONFIG",str(config_path)):
            reject(a.validate_execution_script,"duplicate native maxRuns")
        bad=copy.deepcopy(actual);bad["apiProxy"]["maxTurns"]=500
        config_path.write_text(json.dumps(bad))
        with patch.object(a,"NATIVE_CONFIG",str(config_path)):
            reject(a.validate_execution_script,"conflicting maxTurns")
    checkout=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
    now=time.time()
    run_id=700000001
    run={"id":run_id,"run_attempt":1,"event":"pull_request","status":"in_progress",
         "head_sha":"c"*40,"path":a.WORKFLOW,"repository":{"full_name":a.REPOSITORY}}
    pr={"merge_commit_sha":checkout,"state":"open","draft":True,
        "head":{"sha":"c"*40,"ref":a.BRANCH,"repo":{"full_name":a.REPOSITORY}},
        "base":{"sha":a.BASE_SHA,"ref":"main","repo":{"full_name":a.REPOSITORY}}}
    job={"id":800000001,"run_id":run_id,"run_attempt":1,"head_sha":"c"*40,
         "name":a.JOB,"status":"in_progress","started_at":iso(now-60)}
    old_run={"id":a.PREDECESSOR_RUN,"run_attempt":1,"status":"completed","conclusion":"failure",
             "head_sha":a.PREDECESSOR_HEAD,"head_branch":a.BRANCH,"event":"pull_request",
             "path":a.WORKFLOW,"repository":{"full_name":a.REPOSITORY}}
    old_job={"id":114093376527,"run_id":38011769045,"workflow_name":"Validate v0.3 Dogfood Live Gate","head_branch":"diagnose/v03-detector-tool-feedback","run_url":"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/runs/38011769045","run_attempt":1,"node_id":"CR_kwDOTw3ETM8AAAAakH7EDw","head_sha":"4e0a2d13ab816e0772f526fe9bd435df9eb52433","url":"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/jobs/114093376527","html_url":"https://github.com/DREAM-XIN/ai-sdlc/actions/runs/38011769045/job/114093376527","status":"completed","conclusion":"failure","created_at":"2026-10-10T01:06:16Z","started_at":"2026-10-10T01:06:17Z","completed_at":"2026-10-10T01:19:49Z","name":"Fixed failed Reviewer detector diagnostic","steps":[{"name":"Set up job","status":"completed","conclusion":"success","number":1,"started_at":"2026-10-10T01:06:19Z","completed_at":"2026-10-10T01:06:20Z"},{"name":"Run actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0","status":"completed","conclusion":"success","number":2,"started_at":"2026-10-10T01:06:20Z","completed_at":"2026-10-10T01:06:22Z"},{"name":"Setup pinned detector scripts","status":"completed","conclusion":"success","number":3,"started_at":"2026-10-10T01:06:22Z","completed_at":"2026-10-10T01:06:26Z"},{"name":"Materialize authenticated original inputs","status":"completed","conclusion":"success","number":4,"started_at":"2026-10-10T01:06:26Z","completed_at":"2026-10-10T01:06:32Z"},{"name":"Clear inherited MCP configuration for isolated detection","status":"completed","conclusion":"success","number":5,"started_at":"2026-10-10T01:06:32Z","completed_at":"2026-10-10T01:06:32Z"},{"name":"Prepare original inputs through pinned official script privately","status":"completed","conclusion":"success","number":6,"started_at":"2026-10-10T01:06:32Z","completed_at":"2026-10-10T01:06:32Z"},{"name":"Setup threat detection privately","status":"completed","conclusion":"success","number":7,"started_at":"2026-10-10T01:06:32Z","completed_at":"2026-10-10T01:06:32Z"},{"name":"Initialize empty diagnostic execution evidence","status":"completed","conclusion":"success","number":8,"started_at":"2026-10-10T01:06:32Z","completed_at":"2026-10-10T01:06:32Z"},{"name":"Download pinned detector container images","status":"completed","conclusion":"success","number":9,"started_at":"2026-10-10T01:06:32Z","completed_at":"2026-10-10T01:06:41Z"},{"name":"Install pinned AWF","status":"completed","conclusion":"success","number":10,"started_at":"2026-10-10T01:06:41Z","completed_at":"2026-10-10T01:06:42Z"},{"name":"Run actions/setup-node@820762786026740c76f36085b0efc47a31fe5020","status":"completed","conclusion":"success","number":11,"started_at":"2026-10-10T01:06:42Z","completed_at":"2026-10-10T01:06:45Z"},{"name":"Install exact observed Copilot CLI","status":"completed","conclusion":"success","number":12,"started_at":"2026-10-10T01:06:45Z","completed_at":"2026-10-10T01:06:54Z"},{"name":"Verify CLI bytes against authenticated official archive","status":"completed","conclusion":"success","number":13,"started_at":"2026-10-10T01:06:54Z","completed_at":"2026-10-10T01:06:59Z"},{"name":"Install pinned threat detector","status":"completed","conclusion":"success","number":14,"started_at":"2026-10-10T01:06:59Z","completed_at":"2026-10-10T01:06:59Z"},{"name":"Wait for one exact assistant-issued diagnostic admission","status":"completed","conclusion":"success","number":15,"started_at":"2026-10-10T01:06:59Z","completed_at":"2026-10-10T01:19:40Z"},{"name":"Invoke admitted detector once with private output","status":"completed","conclusion":"success","number":16,"started_at":"2026-10-10T01:19:40Z","completed_at":"2026-10-10T01:19:47Z"},{"name":"Parse pinned verdict privately and report bounded diagnostic result","status":"completed","conclusion":"failure","number":17,"started_at":"2026-10-10T01:19:47Z","completed_at":"2026-10-10T01:19:47Z"},{"name":"Post Run actions/setup-node@820762786026740c76f36085b0efc47a31fe5020","status":"completed","conclusion":"skipped","number":32,"started_at":"2026-10-10T01:19:47Z","completed_at":"2026-10-10T01:19:47Z"},{"name":"Post Setup pinned detector scripts","status":"completed","conclusion":"success","number":33,"started_at":"2026-10-10T01:19:47Z","completed_at":"2026-10-10T01:19:47Z"},{"name":"Post Run actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0","status":"completed","conclusion":"success","number":34,"started_at":"2026-10-10T01:19:47Z","completed_at":"2026-10-10T01:19:47Z"},{"name":"Complete job","status":"completed","conclusion":"success","number":35,"started_at":"2026-10-10T01:19:47Z","completed_at":"2026-10-10T01:19:47Z"}],"check_run_url":"https://api.github.com/repos/DREAM-XIN/ai-sdlc/check-runs/114093376527","labels":["ubuntu-latest"],"runner_id":1000026956,"runner_name":"GitHub Actions 1000026956","runner_group_id":0,"runner_group_name":"GitHub Actions"}
    state={"run":run,"pr":pr,"jobs":{"total_count":1,"jobs":[job]},"comments":[copy.deepcopy(OLD_COMMENT)],
           "old_run":old_run,"old_jobs":{"total_count":1,"jobs":[old_job]},
           "old_comment":copy.deepcopy(OLD_COMMENT),"old_log":old_log(),"direct_override":None,
           "old_commit":{"sha":a.PREDECESSOR_CHECKOUT,"tree":{"sha":a.PREDECESSOR_TREE}},
           "old_tree":{"sha":a.PREDECESSOR_TREE,"truncated":False,"tree":[
               {"path":path,"type":"blob","sha":sha} for path,sha in a.PREDECESSOR_SOURCES.items()]}}
    def read(path):
        if path==f"/actions/runs/{a.PREDECESSOR_RUN}": return copy.deepcopy(state["old_run"])
        if path==f"/actions/runs/{a.PREDECESSOR_RUN}/attempts/1/jobs?per_page=100": return copy.deepcopy(state["old_jobs"])
        if path==f"/issues/comments/{a.PREDECESSOR_COMMENT}": return copy.deepcopy(state["old_comment"])
        if path==f"/git/commits/{a.PREDECESSOR_CHECKOUT}": return copy.deepcopy(state["old_commit"])
        if path==f"/git/trees/{a.PREDECESSOR_TREE}?recursive=1": return copy.deepcopy(state["old_tree"])
        if path==f"/actions/runs/{run_id}": return copy.deepcopy(state["run"])
        if path==f"/pulls/{a.PR}": return copy.deepcopy(state["pr"])
        if path==f"/actions/runs/{run_id}/attempts/1/jobs?per_page=100": return copy.deepcopy(state["jobs"])
        if path==f"/issues/{a.PR}/comments?per_page=100&page=1": return copy.deepcopy(state["comments"])
        if path=="/issues/comments/900000001": return copy.deepcopy(state["direct_override"] if state["direct_override"] is not None else state["comments"][-1])
        raise AssertionError("unexpected fake provider read "+path)
    env={"GITHUB_REPOSITORY":a.REPOSITORY,"GITHUB_EVENT_NAME":"pull_request","GITHUB_RUN_ATTEMPT":"1",
         "GITHUB_REF":"refs/pull/581/merge","GITHUB_RUN_ID":str(run_id),
         "GITHUB_SHA":checkout,"GITHUB_WORKFLOW_SHA":checkout,
         "GITHUB_WORKFLOW_REF":a.REPOSITORY+"/"+a.WORKFLOW+"@refs/pull/581/merge",
         "GH_TOKEN":"synthetic-read-token"}
    with (patch.dict(os.environ,env),patch.object(a,"read",side_effect=read),
          patch.object(a,"predecessor_logs",side_effect=lambda:state["old_log"])):
        identity=a.source_identity()
        expect(identity["checkout_sha"]==checkout and identity["job_id"]==job["id"],"actual source admission failed")
        original=copy.deepcopy(state)
        for label,mutate in (
            ("run attempt two",lambda:state["run"].update(run_attempt=2)),
            ("boolean run attempt",lambda:state["run"].update(run_attempt=True)),
            ("boolean job attempt",lambda:state["jobs"]["jobs"][0].update(run_attempt=True)),
            ("wrong branch",lambda:state["pr"]["head"].update(ref="unreviewed")),
            ("wrong base",lambda:state["pr"]["base"].update(sha="d"*40)),
            ("changed merge",lambda:state["pr"].update(merge_commit_sha="d"*40)),
            ("duplicate job",lambda:state["jobs"].update(total_count=2,jobs=[copy.deepcopy(job),copy.deepcopy(job)])),
        ):
            mutate(); reject(a.source_identity,label)
            state.clear(); state.update(copy.deepcopy(original))
        with patch.dict(os.environ,{"GITHUB_RUN_ATTEMPT":"2"}):
            reject(a.source_identity,"environment rerun")


        # Exact retired attempt is authenticated afresh; no arbitrary old marker is excused.
        expect(a.verify_predecessor()["summary"]["invocation_consumed"] is False,"uninvoked proof missing")
        for label,mutate in (
            ("predecessor attempt two",lambda:state["old_run"].update(run_attempt=2)),
            ("predecessor boolean attempt",lambda:state["old_run"].update(run_attempt=True)),
            ("predecessor active",lambda:state["old_run"].update(status="in_progress")),
            ("predecessor success",lambda:state["old_run"].update(conclusion="success")),
            ("predecessor source",lambda:state["old_run"].update(head_sha="e"*40)),
            ("predecessor job identity",lambda:state["old_jobs"]["jobs"][0].update(id=1)),
            ("predecessor step",lambda:state["old_jobs"]["jobs"][0]["steps"][14].update(conclusion="failure")),
            ("predecessor marker edit",lambda:state["old_comment"].update(updated_at=iso(now))),
            ("predecessor marker body",lambda:state["old_comment"].update(body=OLD_COMMENT["body"]+" ")),
            ("predecessor source tree",lambda:state["old_tree"]["tree"][0].update(sha="e"*40)),
            ("predecessor source truncation",lambda:state["old_tree"].update(truncated=True)),
            ("predecessor missing log",lambda:state.update(old_log="")),
        ):
            mutate();reject(a.verify_predecessor,label)
            state.clear();state.update(copy.deepcopy(original))
        for key,value in (("invocation_consumed",True),("invocation_consumed",0),
                          ("verdict_valid",True),("diagnostic_success",True),("release_authority",True),
                          ("scanner_outcome","unknown")):
            changed=copy.deepcopy(OLD_PROOF);changed["summary"]["value"][key]=value
            bad="\n".join([changed["request"]["at"]+" "+json.dumps(changed["request"]["value"]),
                changed["blocked"],changed["exit"],changed["summary"]["at"]+" "+json.dumps(changed["summary"]["value"])])+"\n"
            reject(lambda:a.predecessor_log_proof(bad),"predecessor summary "+key)
        reject(lambda:a.predecessor_log_proof(old_log().replace('"invocation_consumed": false',
               '"invocation_consumed": true, "invocation_consumed": false')),"duplicate summary key")
        reject(lambda:a.predecessor_log_proof(old_log()+old_log()),"duplicate predecessor records")
        reject(lambda:a.predecessor_log_proof(old_log().replace(OLD_PROOF["exit"]+"\n","")),"missing invoke exit")
        reject(lambda:a.predecessor_log_proof(old_log().replace("01:19:47.1437429Z","01:19:48.1437429Z")),"summary outside pinned interval")
        reject(lambda:a.predecessor_log_proof(old_log().replace(a.PREDECESSOR_CHECKOUT,"e"*40)),"observed source differs")
        # GitHub list/direct endpoints differ in these observed presentation fields only.
        listed=copy.deepcopy(OLD_COMMENT)
        listed["performed_via_github_app"]={"id":1144995,"client_id":"observed-list-only"}
        direct=copy.deepcopy(OLD_COMMENT)
        direct["performed_via_github_app"]={"id":1144995};direct["pin"]=None
        expect(a.same_comment_authority(listed,direct),"real endpoint decoration asymmetry rejected")
        for label,mutate in (
            ("author ID",lambda c:c["user"].update(id=1)),
            ("author login",lambda c:c["user"].update(login="other")),
            ("association",lambda c:c.update(author_association="NONE")),
            ("body",lambda c:c.update(body=c["body"]+" ")),
            ("created",lambda c:c.update(created_at=iso(now))),
            ("updated",lambda c:c.update(updated_at=iso(now))),
            ("ID",lambda c:c.update(id=c["id"]+1)),
            ("boolean ID",lambda c:c.update(id=True)),
            ("node ID",lambda c:c.update(node_id="other")),
            ("API URL",lambda c:c.update(url=c["url"]+"?other")),
            ("HTML URL",lambda c:c.update(html_url=c["html_url"]+"other")),
            ("issue URL",lambda c:c.update(issue_url=c["issue_url"].replace("581","582"))),
        ):
            changed=copy.deepcopy(direct);mutate(changed)
            def comparison():
                if not a.same_comment_authority(listed,changed):
                    raise ValueError("authority mismatch")
            reject(comparison,"direct "+label)

        contents={name:("synthetic:"+name).encode() for name in (
            "agent_output.json","aw_info.json","aw-prompts/prompt.txt",
            "aw-prompts/prompt-template.txt","aw-prompts/prompt-import-tree.json")}
        manifest={"schema":"synthetic-admission-input/v1","files":[
            {"path":name,"size_bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()}
            for name,data in sorted(contents.items())]}
        deadline=now+600
        marker=dict(identity,schema=a.SCHEMA,purpose=a.PURPOSE,assistant_issued=True,
                    input_manifest=manifest,expires_at=iso(now+500))
        comment={"id":900000001,"node_id":"synthetic-comment-node","author_association":"OWNER",
                 "url":a.BASE+"/issues/comments/900000001",
                 "html_url":f"https://github.com/{a.REPOSITORY}/pull/{a.PR}#issuecomment-900000001",
                 "issue_url":a.BASE+f"/issues/{a.PR}",
                 "user":copy.deepcopy(a.AUTHOR),"created_at":iso(now-5),
                 "updated_at":iso(now-5),"body":a.PREFIX+json.dumps(marker)}
        expect(a.marker(identity,manifest,time.time(),deadline) is None,"missing marker became authority")
        state["comments"]=[copy.deepcopy(OLD_COMMENT),copy.deepcopy(comment)]
        expect(a.marker(identity,manifest,time.time(),deadline)[0]["id"]==comment["id"],"valid marker rejected")
        for label,mutate in (
            ("different run",lambda value:value.update(run_id=run_id+1)),
            ("boolean attempt",lambda value:value.update(run_attempt=True)),
            ("boolean zero retry",lambda value:value["limits"].update(harness_retries=False)),
            ("expired",lambda value:value.update(expires_at=iso(now-1))),
            ("source drift",lambda value:value.update(checkout_sha="d"*40)),
            ("manifest drift",lambda value:value["input_manifest"].update(files=[])),
            ("extra field",lambda value:value.update(extra=True)),
        ):
            changed=copy.deepcopy(marker); mutate(changed)
            state["comments"]=[copy.deepcopy(OLD_COMMENT),dict(comment,body=a.PREFIX+json.dumps(changed))]
            reject(lambda:a.marker(identity,manifest,time.time(),deadline),label)
        state["comments"]=[copy.deepcopy(OLD_COMMENT),copy.deepcopy(comment),copy.deepcopy(comment)]
        reject(lambda:a.marker(identity,manifest,time.time(),deadline),"duplicate marker")
        state["comments"]=[copy.deepcopy(OLD_COMMENT),dict(comment,updated_at=iso(now))]
        reject(lambda:a.marker(identity,manifest,time.time(),deadline),"edited marker")
        state["comments"]=[copy.deepcopy(OLD_COMMENT),dict(comment,user={"id":1,"login":"DREAM-XIN"})]
        reject(lambda:a.marker(identity,manifest,time.time(),deadline),"unverified author")
        state["comments"]=[copy.deepcopy(OLD_COMMENT),copy.deepcopy(comment)]


        for old_rows,label in (([],"missing retired admission"),
                               ([copy.deepcopy(OLD_COMMENT),copy.deepcopy(OLD_COMMENT)],"duplicate retired admission")):
            state["comments"]=old_rows
            reject(lambda:a.marker(identity,manifest,time.time(),deadline),label)
        foreign=copy.deepcopy(comment)
        foreign["id"]=900000002
        foreign["url"]=a.BASE+"/issues/comments/900000002"
        foreign["html_url"]=f"https://github.com/{a.REPOSITORY}/pull/{a.PR}#issuecomment-900000002"
        foreign_marker=copy.deepcopy(marker);foreign_marker["run_id"]=run_id+2
        foreign["body"]=a.PREFIX+json.dumps(foreign_marker)
        state["comments"]=[copy.deepcopy(OLD_COMMENT),foreign]
        reject(lambda:a.marker(identity,manifest,time.time(),deadline),"unrecognized other-run admission")
        state["comments"]=[copy.deepcopy(OLD_COMMENT),copy.deepcopy(comment)]

        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/"private";root.mkdir()
            inputs=Path(directory)/"inputs";inputs.mkdir()
            for name,data in contents.items():
                target=inputs/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
            execution={"version":1,"component":"detection","run_id":run_id,"run_attempt":1,"state":"not_started"}
            (inputs/"execution.json").write_text(json.dumps(execution))
            (inputs/"detection.log").write_bytes(b"")
            saved={"identity":identity,"manifest":manifest,"comment_id":comment["id"],
                   "body_sha256":hashlib.sha256(comment["body"].encode()).hexdigest(),
                   "marker":marker,"wait_deadline":deadline}
            (root/"admission.json").write_bytes(a.canonical(saved))
            invoked=[]
            class InvocationObserved(Exception): pass
            def execve(binary,args,actual_env):
                invoked.append((binary,args,actual_env))
                raise InvocationObserved()
            fixed=json.loads(Path(a.CONFIG).read_text())["fixed_execution_env"]
            with (patch.object(a,"ROOT",root),patch.object(a,"INPUT_ROOT",inputs),
                  patch.object(a,"source_identity",return_value=identity),
                  patch.object(a,"inspect_inputs",return_value=(manifest,contents,contents)),
                  patch.object(a.os,"execve",side_effect=execve),patch.object(a.os,"dup2"),
                  patch.dict(os.environ,dict(fixed,COPILOT_PROVIDER_API_KEY="synthetic-not-a-credential"))):
                # All denials occur before the subprocess boundary.
                state["comments"]=[copy.deepcopy(OLD_COMMENT)];reject(a.invoke,"deleted marker");state["comments"]=[copy.deepcopy(OLD_COMMENT),copy.deepcopy(comment)]
                (inputs/"agent_output.json").write_bytes(b"changed")
                reject(a.invoke,"changed consumed input")
                (inputs/"agent_output.json").write_bytes(contents["agent_output.json"])
                (inputs/"extra.txt").write_bytes(b"extra");reject(a.invoke,"extra consumed member");(inputs/"extra.txt").unlink()
                expect(not invoked and not(root/"consumed").exists(),"negative input crossed scanner boundary")

                for label,mutate in (
                    ("direct author",lambda c:c["user"].update(id=1)),
                    ("direct association",lambda c:c.update(author_association="NONE")),
                    ("direct body",lambda c:c.update(body=c["body"]+" ")),
                    ("direct timestamp",lambda c:c.update(updated_at=iso(now))),
                    ("direct ID",lambda c:c.update(id=True)),
                    ("direct URL",lambda c:c.update(issue_url=a.BASE+"/issues/582")),
                ):
                    changed=copy.deepcopy(comment);mutate(changed);state["direct_override"]=changed
                    reject(a.invoke,label)
                    expect(not invoked and not(root/"consumed").exists(),"comment drift crossed execution boundary")
                state["direct_override"]=dict(copy.deepcopy(comment),pin=None,performed_via_github_app={"id":1144995})
                state["comments"][-1]["performed_via_github_app"]={"id":1144995,"client_id":"observed-list-only"}

                try: a.invoke()
                except InvocationObserved: pass
                else: raise AssertionError("valid admission never reached mocked execution boundary")
                expect(len(invoked)==1 and invoked[0][0]=="/bin/bash"
                       and invoked[0][1]==["bash","-e","-o","pipefail",a.EXECUTION],
                       "valid invocation did not preserve runner bash -e semantics")
                passed=invoked[0][2]
                expect("GH_TOKEN" not in passed,"read token entered scanner")
                for key in ("GITHUB_OUTPUT","GITHUB_ENV","GITHUB_STEP_SUMMARY","GITHUB_PATH","GITHUB_STATE"):
                    expect(Path(passed[key]).parent==root and Path(passed[key]).stat().st_mode & 0o777==0o600,
                           "runner command file leaked into scanner")
                reject(a.invoke,"duplicate local invocation")
                expect(len(invoked)==1,"consumed diagnostic invoked again")
    print("PASS: exact source/marker/input/attempt/duplicate-consume denials; one mocked invocation; no model")
if __name__=="__main__":
    main()
