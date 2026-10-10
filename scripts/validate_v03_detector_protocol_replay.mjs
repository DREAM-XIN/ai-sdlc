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
let nativeDiagnostics = null;
let nativeFieldDiagnostics = null;
const ensure = (ok, code) => { if (!ok) throw new Error(code); };
const allowedErrors = new Set([
 "INPUT_PATH","NONROOT","NETWORK_INTERFACE","INHERITED_AUTHORITY","EGRESS_AVAILABLE","EGRESS_INCONCLUSIVE",
 "CLI_START","CLI_WATCHDOG","CLI_OUTPUT_BOUND","CLI_EXIT","REQUEST_BOUND","REQUEST_SHAPE","REQUEST_COUNT",
 "REQUEST_ROUTE","SHELL_SCHEMA","SHELL_TOOL","TOOL_FEEDBACK","PROVIDER_FAILURE","SESSION_DIRECTORY",
 "EVENT_FILE","EVENT_BOUND","EVENT_TRUNCATED","EVENT_JSON","EVENT_SHAPE","START_SHAPE","COMPLETE_SHAPE",
 "TOOL_LINKAGE","COMMAND_IDENTITY","EXPLICIT_STATUS","RESULT_VALUE","TERMINATION","NEGATIVE_NOT_REJECTED"
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
function run(command, args, env, cwd) {
 return new Promise((resolve, reject) => {
  const child = spawn(command, args, {env,cwd,detached:true,stdio:["ignore","pipe","pipe"]});
  let bytes=0, settled=false;
  const fail=code=>{ if(!settled){settled=true;stop(child);clearTimeout(timer);reject(new Error(code));} };
  const timer=setTimeout(()=>fail("CLI_WATCHDOG"),90000);
  for(const output of [child.stdout,child.stderr]) output.on("data",b=>{
   bytes+=b.length; if(bytes>4*1024*1024)fail("CLI_OUTPUT_BOUND");
  });
  child.once("error",()=>fail("CLI_START"));
  child.once("close",code=>{if(!settled){settled=true;clearTimeout(timer);stop(child);resolve(code);}});
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
function analyze(events) {
 const starts=new Map(), completes=new Map(), categories={start:0,complete:0,assistant:0,session_end:0,other:0};
 for(const event of events){
  const d=event.data;
  if(event.type==="tool.execution_start"){
   categories.start++;
   ensure(typeof d.toolCallId==="string"&&ids.includes(d.toolCallId)
    &&d.toolName==="bash"&&!starts.has(d.toolCallId),"START_SHAPE");
   const command=typeof d.command==="string"?d.command:
    (d.input&&typeof d.input.command==="string"?d.input.command:
     (d.parameters&&typeof d.parameters.command==="string"?d.parameters.command:undefined));
   ensure(typeof command==="string","START_SHAPE");
   starts.set(d.toolCallId,{command});
  }else if(event.type==="tool.execution_complete"){
   categories.complete++;
   ensure(typeof d.toolCallId==="string"&&ids.includes(d.toolCallId)
    &&!completes.has(d.toolCallId),"COMPLETE_SHAPE");
   ensure(typeof d.success==="boolean","EXPLICIT_STATUS");
   const output=typeof d.output==="string"?d.output:
    (typeof d.result==="string"?d.result:
     (d.result&&typeof d.result.content==="string"?d.result.content:undefined));
   ensure(typeof output==="string","RESULT_VALUE");
   completes.set(d.toolCallId,{success:d.success,output});
  }else if(event.type==="assistant.message"){
   categories.assistant++;
  }else if(["session.result","session.shutdown","session.end"].includes(event.type)){
   categories.session_end++;
  }else categories.other++;
 }
 ensure(starts.size===3&&completes.size===3,"TOOL_LINKAGE");
 const commandHashes=[],resultHashes=[];
 for(let i=0;i<ids.length;i++){
  ensure(starts.has(ids[i])&&completes.has(ids[i]),"TOOL_LINKAGE");
  const start=starts.get(ids[i]),complete=completes.get(ids[i]);
  ensure(start.command===commands[i],"COMMAND_IDENTITY");
  ensure(complete.success===(i!==1),"EXPLICIT_STATUS");
  ensure(complete.output.includes(markers[i]),"RESULT_VALUE");
  commandHashes.push(fingerprint(Buffer.from(start.command,"utf8")));
  resultHashes.push(fingerprint(Buffer.from(complete.output,"utf8")));
 }
 ensure(commandHashes[0]===commandHashes[2]&&commandHashes[0]!==commandHashes[1],"COMMAND_IDENTITY");
 // Completion is additionally authenticated by actual CLI exit0 and the final
 // fake-provider assistant stop. Native end-event absence is reported, never inferred.
 return {categories,linked_calls:3,explicit_success:2,explicit_failure:1,
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
try{const report=await main();console.log(JSON.stringify(report));}
catch(error){console.log(JSON.stringify({schema:"v03-native-cli-observability/v1",status:"FAIL",
 stage,error:allowedErrors.has(error.message)?error.message:"UNCLASSIFIED_ERROR",native_diagnostics:nativeDiagnostics,native_field_diagnostics:nativeFieldDiagnostics,raw_exported:false}));process.exitCode=1;}
