#!/usr/bin/env node
// Temporary offline observability regression. No model, production detector, or live effect.
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import net from "node:net";
import http from "node:http";
import crypto from "node:crypto";
import {spawn} from "node:child_process";

const [cli, root] = process.argv.slice(2);
let stage = "ISOLATION";
let lastProcess = null;
let nativeDiagnostics = null;
let nativeFieldDiagnostics = null;
const ensure = (ok, code) => { if (!ok) throw new Error(code); };
const allowedErrors = new Set([
 "INPUT_PATH","NONROOT","NETWORK_INTERFACE","INHERITED_AUTHORITY","EGRESS_AVAILABLE","EGRESS_INCONCLUSIVE",
 "CLI_START","CLI_WATCHDOG","CLI_OUTPUT_BOUND","CLI_EXIT","REQUEST_BOUND","REQUEST_SHAPE","REQUEST_COUNT",
 "REQUEST_ROUTE","SHELL_SCHEMA","SHELL_TOOL","TOOL_FEEDBACK","PROVIDER_FAILURE","SESSION_DIRECTORY",
 "EVENT_FILE","EVENT_BOUND","EVENT_TRUNCATED","EVENT_JSON","EVENT_SHAPE","START_SHAPE","COMPLETE_SHAPE",
 "AWF_EXIT","TOOL_LINKAGE","COMMAND_IDENTITY","EXPLICIT_STATUS","RESULT_VALUE","TERMINATION","NEGATIVE_NOT_REJECTED"
]);
const commands = [
 "printf 'OBS_SUCCESS_4d73\\n'",
 "printf 'OBS_FAILURE_4d73\\n' >&2; exit 7",
 "printf 'OBS_SUCCESS_4d73\\n'"
];
const markers = ["OBS_SUCCESS_4d73", "OBS_FAILURE_4d73", "OBS_SUCCESS_4d73"];
const ids = ["offline-observe-1","offline-observe-2","offline-observe-3"];
const opaqueKey = crypto.randomBytes(32);
const fingerprint = value => crypto.createHmac("sha256", opaqueKey).update(value).digest("hex");
function stop(child) {
 if (!child?.pid) return;
 try { process.kill(-child.pid, "SIGKILL"); } catch {}
}
function run(command, args, env, cwd, timeoutMs=90000, onSpawn=null) {
 return new Promise((resolve, reject) => {
  const child = spawn(command, args, {env,cwd,detached:true,stdio:["ignore","pipe","pipe"]});
  if(onSpawn)onSpawn(child);
  let bytes=0, settled=false, captured="";
  lastProcess={exit_code:null,close_signal:null,error_classes:[],stdout_bytes:0,stderr_bytes:0,sanitized_startup_errors:[],runtime_probe:null,child_failure:null};
  const classify=()=>{const failures=[];let classifiedText=captured;
   if(["awf-host","awf-cancel"].includes(cli)){classifiedText=captured.split(/\r?\n/).filter(line=>{const at=line.indexOf('{"schema":"v03-native-cli-observability/v1"');if(at<0)return true;try{const x=JSON.parse(line.slice(at));if(Object.keys(x).sort().join(",")==="error,process,raw_exported,schema,stage,status"&&x.schema==="v03-native-cli-observability/v1"&&x.status==="FAIL"&&["ISOLATION","CLI_PROTOCOL","NATIVE_EVENTS","INCOMPLETE_NEGATIVES"].includes(x.stage)&&allowedErrors.has(x.error)&&x.process===null&&x.raw_exported===false){failures.push({stage:x.stage,error:x.error});return false;}}catch{}return true;}).join("\n");if(failures.length===1)lastProcess.child_failure=failures[0];}
   const checks={CONFIG_SCHEMA:/schema|additional propert|unrecognized propert|unknown propert|must NOT have/i,ARGUMENT:/unknown option|invalid option|invalid argument|no command specified/i,MISSING_MODULE:/cannot find module|module_not_found/i,DOCKER:/cannot connect to.*docker|docker daemon|docker.*not found/i,PERMISSION:/permission denied|eacces|operation not permitted/i,IMAGE:/manifest unknown|image.*not found|pull access denied/i,MOUNT:/invalid mount|mount.*denied|mount.*not exist/i,NETWORK_SETUP:/iptables.*failed|failed.*iptables|network.*conflict/i,AUTH_REQUIRED:/missing.*(?:token|key|credential)|(?:token|key|credential).*required/i};lastProcess.error_classes=Object.entries(checks).filter(([,r])=>r.test(classifiedText)).map(([k])=>k);
   if(["awf-host","awf-cancel"].includes(cli)){
    const probes=[];
    for(const l of captured.split(/\r?\n/)){const at=l.indexOf("AWF_NATIVE_RUNTIME_PROBE ");if(at<0)continue;try{const p=JSON.parse(l.slice(at+25));if(Object.keys(p).sort().join(",")==="path_node,usr_bin_node,usr_local_bin_node"&&Object.values(p).every(x=>typeof x==="boolean"))probes.push(p);}catch{}}
    if(probes.length===1)lastProcess.runtime_probe=probes[0];
    const lines=classifiedText.replace(/\x1b\[[0-9;]*[A-Za-z]/g,"").split(/\r?\n/);
    for(let line of lines){
     if(lastProcess.sanitized_startup_errors.length===4)break;
     if(line.length>400||!/(?:error|fatal|invalid|failed|missing|not found|no such file|cannot execute|ENOENT|EACCES|TypeError|ReferenceError|SyntaxError)/i.test(line))continue;
     if(/(?:prompt|messages|arguments|content|token|secret|password|api.?key|credential)/i.test(line))continue;
     line=line.replace(/https?:\/\/[^\s]+/g,"<url>")
      .replace(/(?:[A-Za-z]:)?\/[^\s,;:)]+/g,"<path>")
      .replace(/["'`][^"'\`]*["'`]/g,"<quoted>")
      .replace(/\b[A-Z_][A-Z0-9_]*=[^\s]+/g,"<assignment>")
      .replace(/\b[0-9a-f]{16,}\b/gi,"<id>").replace(/\b[0-9]{6,}\b/g,"<number>")
      .replace(/\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b/g,"<ip>")
      .replace(/[^\x20-\x7e]/g,"").trim().slice(0,200);
     if(line)lastProcess.sanitized_startup_errors.push(line);
    }
   }
   captured="";};
  const fail=code=>{ if(!settled){settled=true;stop(child);clearTimeout(timer);classify();reject(new Error(code));} };
  const timer=setTimeout(()=>fail("CLI_WATCHDOG"),timeoutMs);
  for(const output of [child.stdout,child.stderr]) output.on("data",b=>{
   bytes+=b.length; lastProcess[output===child.stdout?"stdout_bytes":"stderr_bytes"]+=b.length; if(captured.length<65536)captured+=b.toString("utf8").slice(0,65536-captured.length); if(bytes>4*1024*1024)fail("CLI_OUTPUT_BOUND");
  });
  child.once("error",()=>fail("CLI_START"));
  child.once("close",(code,signal)=>{if(!settled){settled=true;clearTimeout(timer);stop(child);lastProcess.exit_code=code;lastProcess.close_signal=signal;classify();resolve(code);}});
 });
}
function shellArguments(definition, command) {
  const schema = definition.parameters || definition.input_schema;
  ensure(schema && schema.properties && schema.properties.command, "SHELL_SCHEMA");
  const result = {command};
  for (const key of schema.required || []) {
    if (key === "command") continue;
    if (key === "description") result[key] = "Read the constant offline detector fixture";
    else throw new Error("SHELL_SCHEMA");
  }
  return result;
}
function completion(response, request, callId, name, args) {
  const tool = {id: callId, type: "function", function: {name, arguments: JSON.stringify(args)}};
  const base = {id: "offline-" + callId, object: "chat.completion", created: 1, model: "deepseek-chat"};
  const usage = {prompt_tokens: 10, completion_tokens: 1, total_tokens: 11,
                 prompt_tokens_details: {cached_tokens: 10}};
  if (request.stream) {
    response.writeHead(200, {"Content-Type": "text/event-stream", "Cache-Control": "no-cache"});
    response.write("data: " + JSON.stringify({...base, object: "chat.completion.chunk",
      choices: [{index: 0, delta: {role: "assistant", tool_calls: [{index: 0, ...tool}]}, finish_reason: null}]}) + "\n\n");
    response.write("data: " + JSON.stringify({...base, object: "chat.completion.chunk",
      choices: [{index: 0, delta: {}, finish_reason: "tool_calls"}]}) + "\n\n");
    response.write("data: " + JSON.stringify({...base, object: "chat.completion.chunk", choices: [], usage}) + "\n\n");
    response.end("data: [DONE]\n\n");
  } else {
    response.writeHead(200, {"Content-Type": "application/json"});
    response.end(JSON.stringify({...base, choices: [{index: 0,
      message: {role: "assistant", content: null, tool_calls: [tool]}, finish_reason: "tool_calls"}], usage}));
  }
}
function done(response, request) {
 const base={id:"offline-observe-stop",object:"chat.completion",created:1,model:"deepseek-chat"};
 if(request.stream){
  response.writeHead(200,{"Content-Type":"text/event-stream"});
  response.write("data: "+JSON.stringify({...base,object:"chat.completion.chunk",
   choices:[{index:0,delta:{role:"assistant",content:"Synthetic observation complete."},finish_reason:null}]})+"\n\n");
  response.write("data: "+JSON.stringify({...base,object:"chat.completion.chunk",
   choices:[{index:0,delta:{},finish_reason:"stop"}]})+"\n\n");
  response.end("data: [DONE]\n\n");
 }else{
  response.writeHead(200,{"Content-Type":"application/json"});
  response.end(JSON.stringify({...base,choices:[{index:0,message:{role:"assistant",
   content:"Synthetic observation complete."},finish_reason:"stop"}],
   usage:{prompt_tokens:10,completion_tokens:1,total_tokens:11}}));
 }
}
function parseEvents(raw) {
 ensure(raw.length>0&&raw.length<=8*1024*1024,"EVENT_BOUND");
 ensure(raw.at(-1)===10,"EVENT_TRUNCATED");
 const text=new TextDecoder("utf-8",{fatal:true}).decode(raw);
 const lines=text.split("\n").filter(x=>x.trim());
 ensure(lines.length<=4096,"EVENT_BOUND");
 return lines.map(line=>{
  let event;try{event=JSON.parse(line);}catch{throw new Error("EVENT_JSON");}
  ensure(event&&typeof event==="object"&&!Array.isArray(event)&&typeof event.type==="string"
   &&event.data&&typeof event.data==="object"&&!Array.isArray(event.data),"EVENT_SHAPE");
  return event;
 });
}
function analyze(events,partial=false) {
 const starts=new Map(), completes=new Map(), categories={start:0,complete:0,assistant:0,session_end:0,other:0};
 for(const event of events){
  const d=event.data;
  if(event.type==="tool.execution_start"){
   categories.start++;
   ensure(typeof d.toolCallId==="string"&&ids.includes(d.toolCallId)
    &&d.toolName==="bash"&&!starts.has(d.toolCallId),"START_SHAPE");
   const command=d.arguments&&typeof d.arguments.command==="string"?d.arguments.command:undefined;
   ensure(typeof command==="string","START_SHAPE");
   starts.set(d.toolCallId,{command});
  }else if(event.type==="tool.execution_complete"){
   categories.complete++;
   ensure(typeof d.toolCallId==="string"&&ids.includes(d.toolCallId)
    &&!completes.has(d.toolCallId),"COMPLETE_SHAPE");
   ensure(typeof d.success==="boolean","EXPLICIT_STATUS");
   const output=d.result&&typeof d.result.content==="string"?d.result.content:undefined;
   ensure(typeof output==="string","RESULT_VALUE");
   const shellExit=d.shellExecution?.exitCode;
   ensure(shellExit===undefined||Number.isInteger(shellExit),"EXPLICIT_STATUS");
   completes.set(d.toolCallId,{success:d.success,output,shellExit});
  }else if(event.type==="assistant.message"){
   categories.assistant++;
  }else if(["session.result","session.shutdown","session.end"].includes(event.type)){
   categories.session_end++;
  }else categories.other++;
 }
 const count=partial?1:3;ensure(starts.size===count&&completes.size===count,"TOOL_LINKAGE");
 const commandHashes=[],resultHashes=[],nativeShellExits=[];
 for(let i=0;i<count;i++){
  ensure(starts.has(ids[i])&&completes.has(ids[i]),"TOOL_LINKAGE");
  const start=starts.get(ids[i]),complete=completes.get(ids[i]);
  ensure(start.command===commands[i],"COMMAND_IDENTITY");
  // Native success is tool completion, not the shell process exit status.
  ensure(complete.success===true,"EXPLICIT_STATUS");
  if(complete.shellExit!==undefined)ensure(complete.shellExit===(i===1?7:0),"EXPLICIT_STATUS");
  nativeShellExits.push(complete.shellExit===undefined?"unknown":complete.shellExit);
  ensure(complete.output.includes(markers[i]),"RESULT_VALUE");
  commandHashes.push(fingerprint(Buffer.from(start.command,"utf8")));
  resultHashes.push(fingerprint(Buffer.from(complete.output,"utf8")));
 }
 if(partial)return {coverage:"partial",categories,linked_calls:1,native_tool_success:1,native_tool_failure:0,native_shell_exit_codes:nativeShellExits,native_terminal_event_present:categories.session_end>0};
 ensure(commandHashes[0]===commandHashes[2]&&commandHashes[0]!==commandHashes[1],"COMMAND_IDENTITY");
 // Completion is additionally authenticated by actual CLI exit0 and the final
 // fake-provider assistant stop. Native end-event absence is reported, never inferred.
 return {categories,linked_calls:3,native_tool_status_source:"explicit_data_success",
  native_tool_success:3,native_tool_failure:0,native_shell_exit_source:"optional_typed_shellExecution_exitCode",
  native_shell_exit_codes:nativeShellExits,synthetic_nonzero_exit_requested:true,
  identical_command_pair:true,identical_result_pair:resultHashes[0]===resultHashes[2],
  native_terminal_event_present:categories.session_end>0};
}
async function main(){
 ensure(cli==="/inputs/copilot/copilot"&&root==="/tmp/observe"&&!fs.existsSync(root),"INPUT_PATH");
 ensure(process.getuid()!==0,"NONROOT");
 ensure(Object.values(os.networkInterfaces()).flat().every(x=>x.internal),"NETWORK_INTERFACE");
 ensure(!Object.keys(process.env).some(k=>/TOKEN|SECRET|PASSWORD|PRIVATE_KEY|CREDENTIAL|API_KEY/i.test(k)),
        "INHERITED_AUTHORITY");
 await new Promise((resolve,reject)=>{
  const socket=net.connect({host:"192.0.2.1",port:9});
  const timer=setTimeout(()=>{socket.destroy();reject(new Error("EGRESS_INCONCLUSIVE"));},1000);
  socket.once("connect",()=>{clearTimeout(timer);socket.destroy();reject(new Error("EGRESS_AVAILABLE"));});
  socket.once("error",e=>{clearTimeout(timer);socket.destroy();
   ["ENETUNREACH","EHOSTUNREACH","EACCES","EPERM"].includes(e.code)?resolve():reject(new Error("EGRESS_INCONCLUSIVE"));});
 });
 fs.mkdirSync(root,{mode:0o700});
 const home=path.join(root,"home");fs.mkdirSync(home,{mode:0o700});
 let server,providerError=null,requests=0,feedbacks=0;
 try{
  stage="CLI_PROTOCOL";
  server=http.createServer((req,res)=>{
   let data=Buffer.alloc(0);
   req.on("data",chunk=>{
    data=Buffer.concat([data,chunk]);
    if(data.length>4*1024*1024){providerError="REQUEST_BOUND";req.destroy();}
   });
   req.on("end",()=>{
    try{
     if(req.method==="GET"&&/\/models(?:\/[^?]*)?$/.test(req.url)){
      res.writeHead(200,{"Content-Type":"application/json"});
      res.end(JSON.stringify({object:"list",data:[{id:"deepseek-chat",object:"model",owned_by:"offline"}]}));return;
     }
     ensure(req.method==="POST"&&/\/chat\/completions$/.test(req.url),"REQUEST_ROUTE");
     const request=JSON.parse(data.toString("utf8"));
     ensure(Array.isArray(request.messages)&&Array.isArray(request.tools),"REQUEST_SHAPE");
     requests++;ensure(requests<=4,"REQUEST_COUNT");
     if(requests>1){
      const previous=requests-2;
      const matches=request.messages.filter(m=>m.role==="tool"&&m.tool_call_id===ids[previous]);
      ensure(matches.length===1&&JSON.stringify(matches[0].content).includes(markers[previous]),"TOOL_FEEDBACK");
      feedbacks++;
     }
     if(requests===4){done(res,request);return;}
     const shell=request.tools.map(x=>x.function||x).find(x=>x.name==="bash");
     ensure(shell,"SHELL_TOOL");
     completion(res,request,ids[requests-1],shell.name,shellArguments(shell,commands[requests-1]));
    }catch(e){
     providerError=allowedErrors.has(e.message)?e.message:"PROVIDER_FAILURE";
     res.writeHead(400,{"Content-Type":"application/json"});
     res.end('{"error":{"message":"offline protocol rejected","type":"offline_protocol"}}');
    }
   });
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  const env={HOME:home,PATH:"/inputs/copilot:/usr/local/bin:/usr/bin:/bin",LANG:"C.UTF-8",
   COPILOT_MODEL:"deepseek-chat",COPILOT_PROVIDER_TYPE:"openai",COPILOT_PROVIDER_WIRE_API:"completions",
   COPILOT_PROVIDER_API_KEY:"offline-dummy",COPILOT_PROVIDER_BASE_URL:"http://127.0.0.1:"+server.address().port};
  const exit=await run(cli,["--model","deepseek-chat","--disable-builtin-mcps","--no-ask-user",
   "--allow-all-tools","--log-level","all","--prompt","Run the three prescribed synthetic read-only shell checks, then stop."],env,root);
  ensure(providerError===null,providerError||"PROVIDER_FAILURE");
  ensure(exit===0,"CLI_EXIT");
  ensure(requests===4&&feedbacks===3,"TOOL_FEEDBACK");
  stage="NATIVE_EVENTS";
  const base=path.join(home,".copilot","session-state");
  nativeDiagnostics={copilot_directory_present:fs.existsSync(path.join(home,".copilot")),
   session_base_present:fs.existsSync(base),session_base_directory:false,session_base_symlink:false,
   entry_count:0,directory_count:0,regular_file_count:0,symlink_count:0,
   uuid_directory_count:0,event_file_count:0,event_symlink_count:0};
  if(fs.existsSync(base)){
   const info=fs.lstatSync(base);
   nativeDiagnostics.session_base_directory=info.isDirectory();
   nativeDiagnostics.session_base_symlink=info.isSymbolicLink();
   if(info.isDirectory()&&!info.isSymbolicLink()){
    const listed=fs.readdirSync(base,{withFileTypes:true});
    nativeDiagnostics.entry_count=listed.length;
    ensure(listed.length<=16,"SESSION_DIRECTORY");
    for(const entry of listed){
     if(entry.isSymbolicLink()){nativeDiagnostics.symlink_count++;continue;}
     if(entry.isFile())nativeDiagnostics.regular_file_count++;
     if(entry.isDirectory()){
      nativeDiagnostics.directory_count++;
      if(/^[0-9a-f-]{36}$/.test(entry.name))nativeDiagnostics.uuid_directory_count++;
      const candidate=path.join(base,entry.name,"events.jsonl");
      if(fs.existsSync(candidate)){
       const candidateInfo=fs.lstatSync(candidate);
       if(candidateInfo.isSymbolicLink())nativeDiagnostics.event_symlink_count++;
       else if(candidateInfo.isFile())nativeDiagnostics.event_file_count++;
      }
     }
    }
   }
  }
  ensure(fs.existsSync(base)&&fs.lstatSync(base).isDirectory()&&!fs.lstatSync(base).isSymbolicLink(),"SESSION_DIRECTORY");
  const entries=fs.readdirSync(base,{withFileTypes:true});
  ensure(entries.length<=16&&!entries.some(x=>x.isSymbolicLink()),"SESSION_DIRECTORY");
  const dirs=entries.filter(x=>x.isDirectory()&&/^[0-9a-f-]{36}$/.test(x.name));
  ensure(dirs.length===1&&nativeDiagnostics.event_file_count===1
   &&nativeDiagnostics.event_symlink_count===0,"SESSION_DIRECTORY");
  const file=path.join(base,dirs[0].name,"events.jsonl");
  const info=fs.lstatSync(file);
  ensure(info.isFile()&&!info.isSymbolicLink()&&info.size<=8*1024*1024,"EVENT_FILE");
  const raw=fs.readFileSync(file),events=parseEvents(raw);
  nativeFieldDiagnostics={start_count:0,complete_count:0,start_fields:{},complete_fields:{},
   tool_categories:{bash:0,shell:0,other:0},provider_id_matches:0,
   unique_start_ids:0,unique_complete_ids:0,paired_ids:0,duplicate_start_ids:0,duplicate_complete_ids:0,
   explicit_status:{success:0,failure:0,unknown:0}};
  const startIds=new Set(),completeIds=new Set();
  const shape=value=>value===undefined?"absent":value===null?"null":Array.isArray(value)?"array":typeof value;
  const fields=["toolCallId","toolName","command","input","parameters","arguments","success","output","result"];
  const nested=[["input","command"],["parameters","command"],["arguments","command"],
                ["result","content"],["result","resultType"],["result","success"],["result","error"]];
  for(const event of events){
   if(!["tool.execution_start","tool.execution_complete"].includes(event.type))continue;
   const isStart=event.type==="tool.execution_start",data=event.data;
   nativeFieldDiagnostics[isStart?"start_count":"complete_count"]++;
   const output=nativeFieldDiagnostics[isStart?"start_fields":"complete_fields"];
   for(const field of fields){
    const key=field+":"+shape(data[field]);output[key]=(output[key]||0)+1;
   }
   for(const [parent,child] of nested){
    const value=data[parent]&&typeof data[parent]==="object"?data[parent][child]:undefined;
    const key=parent+"."+child+":"+shape(value);output[key]=(output[key]||0)+1;
   }
   nativeFieldDiagnostics.tool_categories[["bash","shell"].includes(data.toolName)?data.toolName:"other"]++;
   if(ids.includes(data.toolCallId))nativeFieldDiagnostics.provider_id_matches++;
   const idSet=isStart?startIds:completeIds;
   if(typeof data.toolCallId==="string"){
    if(idSet.has(data.toolCallId))nativeFieldDiagnostics[isStart?"duplicate_start_ids":"duplicate_complete_ids"]++;
    idSet.add(data.toolCallId);
   }
   if(!isStart)nativeFieldDiagnostics.explicit_status[
    data.success===true?"success":data.success===false?"failure":"unknown"]++;
  }
  nativeFieldDiagnostics.unique_start_ids=startIds.size;
  nativeFieldDiagnostics.unique_complete_ids=completeIds.size;
  nativeFieldDiagnostics.paired_ids=[...startIds].filter(id=>completeIds.has(id)).length;
  const report=analyze(events);
  stage="INCOMPLETE_NEGATIVES";
  let truncated=false,incomplete=false;
  try{parseEvents(raw.subarray(0,raw.length-1));}catch(e){truncated=e.message==="EVENT_TRUNCATED";}
  try{analyze(events.filter(e=>!(e.type==="tool.execution_complete"&&e.data.toolCallId===ids[2])));}
  catch(e){incomplete=e.message==="TOOL_LINKAGE";}
  ensure(truncated&&incomplete,"NEGATIVE_NOT_REJECTED");
  return {schema:"v03-native-cli-observability/v1",status:"PASS",scope:"synthetic_observability_only",
   cli_exit:"success",provider_requests:requests,provider_feedbacks:feedbacks,
   protocol:report,truncated_rejected:true,incomplete_rejected:true,raw_exported:false};
 }finally{
  if(server){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
  fs.rmSync(root,{recursive:true,force:true});
  opaqueKey.fill(0);
 }
}

const proofRoot="/tmp/gh-aw/awf-native-proof";
const proofCli=proofRoot+"/copilot/copilot";
const proofScript=proofRoot+"/replay.mjs";
const imageTag="0.28.23,squid=sha256:02ffc56dd40158223064ef03a78c2d9717473c93723b4e403d455ea6f0b09ae0,agent=sha256:2c78aaba1c108e130e2d6d01e4f2cca334ea04c53e6f258913ac34173fe7e3b2,api-proxy=sha256:c15c3d1208df10c5b588a3657be53742aa982ae0909d1eb1812525268794ca64";
// Exact public metadata from pinned AWF core-environment.ts 868d9c0491f66300d10c71f7b4b27d2de4253f8e.
const awfOneShotVariableNames="COPILOT_GITHUB_TOKEN,GITHUB_TOKEN,GH_TOKEN,GITHUB_API_TOKEN,GITHUB_PAT,GH_ACCESS_TOKEN,OPENAI_API_KEY,OPENAI_KEY,ANTHROPIC_API_KEY,ANTHROPIC_AUTH_TOKEN,CLAUDE_API_KEY,CODEX_API_KEY,COPILOT_PROVIDER_API_KEY,ADO_MCP_AUTH_TOKEN,OTEL_EXPORTER_OTLP_HEADERS,OTEL_EXPORTER_OTLP_TRACES_HEADERS,OTEL_EXPORTER_OTLP_METRICS_HEADERS,OTEL_EXPORTER_OTLP_LOGS_HEADERS";
function noSecrets(env,awfChildMetadata=false){let metadata=0;for(const [k,v] of Object.entries(env)){if(awfChildMetadata&&k==="AWF_ONE_SHOT_TOKENS"&&v===awfOneShotVariableNames){metadata++;continue;}ensure(!(v&&/TOKEN|SECRET|PASSWORD|PRIVATE_KEY|CREDENTIAL|API_KEY/i.test(k)),"INHERITED_AUTHORITY");}return metadata;}
function fixtureDirectory(p){const st=fs.lstatSync(p);ensure(st.isDirectory()&&!st.isSymbolicLink(),"SESSION_DIRECTORY");return st;}
function readBounded(p,max){const fd=fs.openSync(p,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);try{const st=fs.fstatSync(fd);ensure(st.isFile()&&st.size>0&&st.size<=max,"EVENT_FILE");const buf=Buffer.alloc(max+1);let n=0,r;while(n<buf.length&&(r=fs.readSync(fd,buf,n,buf.length-n,null))>0)n+=r;ensure(n<=max,"EVENT_BOUND");return buf.subarray(0,n);}finally{fs.closeSync(fd);}}
function metadataGuardNegatives(){
 for(const env of [{AWF_ONE_SHOT_TOKENS:"offline-wrong"},{AWF_ONE_SHOT_TOKENS:awfOneShotVariableNames,COPILOT_PROVIDER_API_KEY:"offline-forbidden"}]){let rejected=false;try{noSecrets(env,true);}catch(e){rejected=e.message==="INHERITED_AUTHORITY";}ensure(rejected,"NEGATIVE_NOT_REJECTED");}
 let hostRejected=false;try{noSecrets({AWF_ONE_SHOT_TOKENS:awfOneShotVariableNames});}catch(e){hostRejected=e.message==="INHERITED_AUTHORITY";}ensure(hostRejected,"NEGATIVE_NOT_REJECTED");
}
async function awfChild(label){
 metadataGuardNegatives();
 ensure(["zero","nonzero","cancel"].includes(label)&&process.getuid()!==0,"INPUT_PATH");const knownMetadata=noSecrets(process.env,true);
 const caseRoot=path.join(proofRoot,label),tmp=path.join(caseRoot,"tmp");
 ensure(process.env.TMPDIR===tmp&&path.isAbsolute(process.env.HOME||""),"INPUT_PATH");
 fixtureDirectory(tmp);fs.writeFileSync(path.join(tmp,"child-temp-check"),"synthetic",{flag:"wx"});fs.unlinkSync(path.join(tmp,"child-temp-check"));
 let server,providerError=null,requests=0,feedbacks=0,cliProcess=null,cancelled=false;
 try{
   server=http.createServer((req,res)=>{
   let data=Buffer.alloc(0);
   req.on("data",chunk=>{
    data=Buffer.concat([data,chunk]);
    if(data.length>4*1024*1024){providerError="REQUEST_BOUND";req.destroy();}
   });
   req.on("end",()=>{
    try{
     if(req.method==="GET"&&/\/models(?:\/[^?]*)?$/.test(req.url)){
      res.writeHead(200,{"Content-Type":"application/json"});
      res.end(JSON.stringify({object:"list",data:[{id:"deepseek-chat",object:"model",owned_by:"offline"}]}));return;
     }
     ensure(req.method==="POST"&&/\/chat\/completions$/.test(req.url),"REQUEST_ROUTE");
     const request=JSON.parse(data.toString("utf8"));
     ensure(Array.isArray(request.messages)&&Array.isArray(request.tools),"REQUEST_SHAPE");
     requests++;ensure(requests<=4,"REQUEST_COUNT");
     if(requests>1){
      const previous=requests-2;
      const matches=request.messages.filter(m=>m.role==="tool"&&m.tool_call_id===ids[previous]);
      ensure(matches.length===1&&JSON.stringify(matches[0].content).includes(markers[previous]),"TOOL_FEEDBACK");
      feedbacks++;
     }
     if(label==="cancel"&&requests===2){ensure(cliProcess?.pid&&!cancelled,"CLI_START");cancelled=true;stop(cliProcess);return;}
     if(requests===4){done(res,request);return;}
     const shell=request.tools.map(x=>x.function||x).find(x=>x.name==="bash");
     ensure(shell,"SHELL_TOOL");
     completion(res,request,ids[requests-1],shell.name,shellArguments(shell,commands[requests-1]));
    }catch(e){
     providerError=allowedErrors.has(e.message)?e.message:"PROVIDER_FAILURE";
     res.writeHead(400,{"Content-Type":"application/json"});
     res.end('{"error":{"message":"offline protocol rejected","type":"offline_protocol"}}');
    }
   });
  });
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));

 const env={HOME:process.env.HOME,TMPDIR:tmp,PATH:"/usr/local/bin:/usr/bin:/bin",LANG:"C.UTF-8",
  COPILOT_MODEL:"deepseek-chat",COPILOT_PROVIDER_TYPE:"openai",COPILOT_PROVIDER_WIRE_API:"completions",
  COPILOT_PROVIDER_API_KEY:"offline-dummy",COPILOT_PROVIDER_BASE_URL:"http://127.0.0.1:"+server.address().port};
 const code=await run(proofCli,["--model","deepseek-chat","--disable-builtin-mcps","--no-ask-user","--allow-all-tools","--log-level","all","--prompt","Run the three prescribed synthetic read-only shell checks, then stop."],env,path.join(caseRoot,"work"),90000,p=>{cliProcess=p;});
 ensure(providerError===null,providerError||"PROVIDER_FAILURE");if(label==="cancel"){ensure(cancelled&&code===null&&lastProcess.close_signal==="SIGKILL","CLI_EXIT");ensure(requests===2&&feedbacks===1,"TOOL_FEEDBACK");}else{ensure(code===0,"CLI_EXIT");ensure(requests===4&&feedbacks===3,"TOOL_FEEDBACK");}
 fs.writeFileSync(path.join(caseRoot,"child-proof.json"),JSON.stringify({cli_exit:code,cli_signal:lastProcess.close_signal,requests,feedbacks,tmpdir_usable:true,known_awf_metadata_count:knownMetadata})+"\n",{flag:"wx",mode:0o600});
 return label==="cancel"?124:label==="zero"?0:7;
 }finally{if(server){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}}
}
function preservedEvents(tmp){
 const entries=fs.readdirSync(tmp,{withFileTypes:true});ensure(entries.length<=128&&!entries.some(e=>e.isSymbolicLink()),"SESSION_DIRECTORY");
 ensure(!entries.some(e=>/^awf-[A-Za-z0-9]{6}$/.test(e.name)),"SESSION_DIRECTORY");
 const sessions=entries.filter(e=>e.name.startsWith("awf-agent-session-state-"));
 ensure(sessions.length===1&&sessions[0].isDirectory()&&/^awf-agent-session-state-[A-Za-z0-9]{6}$/.test(sessions[0].name),"SESSION_DIRECTORY");
 const base=path.join(tmp,sessions[0].name);fixtureDirectory(base);
 const children=fs.readdirSync(base,{withFileTypes:true});ensure(children.length<=16&&!children.some(e=>e.isSymbolicLink()),"SESSION_DIRECTORY");
 const dirs=children.filter(e=>e.isDirectory()&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(e.name));
 ensure(dirs.length===1,"SESSION_DIRECTORY");
 let files=0;for(const e of children){if(!e.isDirectory())continue;const f=path.join(base,e.name,"events.jsonl");try{const st=fs.lstatSync(f);ensure(st.isFile()&&!st.isSymbolicLink(),"EVENT_FILE");files++;}catch(error){if(error.code!=="ENOENT")throw error;}}
 ensure(files===1,"SESSION_DIRECTORY");return path.join(base,dirs[0].name,"events.jsonl");
}
async function awfHost(bundle){
 stage="AWF_INPUT";
 ensure(path.isAbsolute(bundle)&&process.getuid()!==0,"INPUT_PATH");fixtureDirectory(proofRoot);
 ensure(crypto.createHash("sha256").update(readBounded(bundle,2*1024*1024)).digest("hex")==="cf611445d648fe8ec03eb7b1e6e47ae4870d375f88e449adce7176c256f7f3b8","INPUT_PATH");
 const reports=[];
 for(const label of (cli==="awf-cancel"?["cancel"]:["zero","nonzero"])){
  stage="AWF_"+label.toUpperCase();const caseRoot=path.join(proofRoot,label),tmp=path.join(caseRoot,"tmp");
  ensure(!fs.existsSync(caseRoot),"INPUT_PATH");fs.mkdirSync(caseRoot,{mode:0o700});fs.mkdirSync(tmp,{mode:0o700});fs.mkdirSync(path.join(caseRoot,"work"),{mode:0o700});
  const before=fixtureDirectory(tmp),config=path.join(caseRoot,"awf.json");
  fs.writeFileSync(config,JSON.stringify({network:{allowDomains:[]},container:{imageTag},logging:{auditDir:path.join(caseRoot,"audit")}}),{flag:"wx",mode:0o600});
  const env={PATH:process.env.PATH,HOME:process.env.HOME,USER:os.userInfo().username,LOGNAME:os.userInfo().username,LANG:"C.UTF-8",TMPDIR:tmp,GITHUB_WORKSPACE:path.join(caseRoot,"work")};
  noSecrets(env);
  let report;
  try{
   const launch='a=false; b=false; c=false; [ -x /usr/bin/node ] && a=true; [ -x /usr/local/bin/node ] && b=true; command -v node >/dev/null 2>&1 && c=true; printf \'AWF_NATIVE_RUNTIME_PROBE {"usr_bin_node":%s,"usr_local_bin_node":%s,"path_node":%s}\\n\' "$a" "$b" "$c"; [ "$c" = true ] || exit 127; exec node "$1" awf-child "$2"';
   const code=await run(process.execPath,[bundle,"--config",config,"--container-workdir",path.join(caseRoot,"work"),"--mount","/tmp/gh-aw:/tmp/gh-aw:rw","--env-all","--log-level","error","--skip-pull","--","/bin/bash","-c",launch,"awf-native-proof",proofScript,label],env,caseRoot,240000);
   ensure(code===(label==="cancel"?124:label==="zero"?0:7),"AWF_EXIT");
   ensure(lastProcess.runtime_probe?.path_node===true,"INPUT_PATH");
   const child=JSON.parse(readBounded(path.join(caseRoot,"child-proof.json"),4096).toString("utf8"));
   ensure(child.tmpdir_usable===true&&child.known_awf_metadata_count===1,"TOOL_FEEDBACK");
   if(label==="cancel")ensure(child.cli_exit===null&&child.cli_signal==="SIGKILL"&&child.requests===2&&child.feedbacks===1,"TOOL_FEEDBACK");else ensure(child.cli_exit===0&&child.requests===4&&child.feedbacks===3,"TOOL_FEEDBACK");
   const after=fixtureDirectory(tmp);ensure(before.dev===after.dev&&before.ino===after.ino,"SESSION_DIRECTORY");
   stage="AWF_NATIVE_"+label.toUpperCase();
   const raw=readBounded(preservedEvents(tmp),8*1024*1024),events=parseEvents(raw),protocol=analyze(events,label==="cancel");
   let truncated=false,incomplete=false;
   try{parseEvents(raw.subarray(0,raw.length-1));}catch(e){truncated=e.message==="EVENT_TRUNCATED";}
   try{analyze(events.filter(e=>!(e.type==="tool.execution_complete"&&e.data.toolCallId===ids[label==="cancel"?0:2])),label==="cancel");}catch(e){incomplete=e.message==="TOOL_LINKAGE";}
   ensure(truncated&&incomplete,"NEGATIVE_NOT_REJECTED");
   report={case:label,awf_exit:code,runtime_probe:lastProcess.runtime_probe,cli_exit:child.cli_exit,cli_signal:child.cli_signal,cancellation_scope:label==="cancel"?"synthetic_cli_process_group":"none",provider_requests:child.requests,provider_feedbacks:child.feedbacks,known_awf_metadata_count:child.known_awf_metadata_count,tmpdir_usable:true,owned_directory_unchanged:true,native_preservation:true,protocol,truncated_rejected:true,incomplete_rejected:true,raw_exported:false};
  }finally{
   const after=fixtureDirectory(tmp);ensure(before.dev===after.dev&&before.ino===after.ino,"SESSION_DIRECTORY");
   fs.rmSync(caseRoot,{recursive:true,force:false});
  }
  reports.push(report);
 }
 return {schema:"v03-awf-native-preservation-feasibility/v1",status:"PASS",scope:"synthetic_framework_only",cases:reports,raw_cleanup_complete:true,raw_exported:false};
}

try{
 if(cli==="awf-child"){process.exitCode=await awfChild(root);}
 else{const report=["awf-host","awf-cancel"].includes(cli)?await awfHost(root):await main();console.log(JSON.stringify(report));}
}catch(error){console.log(JSON.stringify({schema:"v03-native-cli-observability/v1",status:"FAIL",stage,error:allowedErrors.has(error.message)?error.message:"UNCLASSIFIED_ERROR",process:["awf-host","awf-cancel"].includes(cli)?lastProcess:null,raw_exported:false}));process.exitCode=1;}
