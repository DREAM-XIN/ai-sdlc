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
    state={"run":run,"pr":pr,"jobs":{"total_count":1,"jobs":[job]},"comments":[]}
    def read(path):
        if path==f"/actions/runs/{run_id}": return copy.deepcopy(state["run"])
        if path==f"/pulls/{a.PR}": return copy.deepcopy(state["pr"])
        if path==f"/actions/runs/{run_id}/attempts/1/jobs?per_page=100": return copy.deepcopy(state["jobs"])
        if path==f"/issues/{a.PR}/comments?per_page=100&page=1": return copy.deepcopy(state["comments"])
        if path=="/issues/comments/900000001": return copy.deepcopy(state["comments"][0])
        raise AssertionError("unexpected fake provider read "+path)
    env={"GITHUB_REPOSITORY":a.REPOSITORY,"GITHUB_EVENT_NAME":"pull_request","GITHUB_RUN_ATTEMPT":"1",
         "GITHUB_REF":"refs/pull/581/merge","GITHUB_RUN_ID":str(run_id),
         "GITHUB_SHA":checkout,"GITHUB_WORKFLOW_SHA":checkout,
         "GITHUB_WORKFLOW_REF":a.REPOSITORY+"/"+a.WORKFLOW+"@refs/pull/581/merge",
         "GH_TOKEN":"synthetic-read-token"}
    with patch.dict(os.environ,env),patch.object(a,"read",side_effect=read):
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

        contents={name:("synthetic:"+name).encode() for name in (
            "agent_output.json","aw_info.json","aw-prompts/prompt.txt",
            "aw-prompts/prompt-template.txt","aw-prompts/prompt-import-tree.json")}
        manifest={"schema":"synthetic-admission-input/v1","files":[
            {"path":name,"size_bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()}
            for name,data in sorted(contents.items())]}
        deadline=now+600
        marker=dict(identity,schema=a.SCHEMA,purpose=a.PURPOSE,assistant_issued=True,
                    input_manifest=manifest,expires_at=iso(now+500))
        comment={"id":900000001,"user":copy.deepcopy(a.AUTHOR),"created_at":iso(now-5),
                 "updated_at":iso(now-5),"body":a.PREFIX+json.dumps(marker)}
        expect(a.marker(identity,manifest,time.time(),deadline) is None,"missing marker became authority")
        state["comments"]=[copy.deepcopy(comment)]
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
            state["comments"]=[dict(comment,body=a.PREFIX+json.dumps(changed))]
            reject(lambda:a.marker(identity,manifest,time.time(),deadline),label)
        state["comments"]=[copy.deepcopy(comment),dict(comment,id=900000002)]
        reject(lambda:a.marker(identity,manifest,time.time(),deadline),"duplicate marker")
        state["comments"]=[dict(comment,updated_at=iso(now))]
        reject(lambda:a.marker(identity,manifest,time.time(),deadline),"edited marker")
        state["comments"]=[dict(comment,user={"id":1,"login":"DREAM-XIN"})]
        reject(lambda:a.marker(identity,manifest,time.time(),deadline),"unverified author")
        state["comments"]=[copy.deepcopy(comment)]

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
                state["comments"]=[];reject(a.invoke,"deleted marker");state["comments"]=[copy.deepcopy(comment)]
                (inputs/"agent_output.json").write_bytes(b"changed")
                reject(a.invoke,"changed consumed input")
                (inputs/"agent_output.json").write_bytes(contents["agent_output.json"])
                (inputs/"extra.txt").write_bytes(b"extra");reject(a.invoke,"extra consumed member");(inputs/"extra.txt").unlink()
                expect(not invoked and not(root/"consumed").exists(),"negative input crossed scanner boundary")
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
