import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { pathToFileURL } from "node:url";

const PREFIX="ai-sdlc.detector-observation";
const reasons=new Set(["IDENTITY","OWNERSHIP","PATH","BOUND","SESSION","EVENT_JSON","EVENT_SHAPE","EVENT_TRUNCATED","LINKAGE","IO"]);
function requireThat(ok,code){if(!ok)throw new Error(code);}
function identity(env){
 requireThat(env.OBSERVER_RUNNER_ENVIRONMENT==="github-hosted"&&env.OBSERVER_RUNNER_OS==="Linux","IDENTITY");
 requireThat(/^[1-9][0-9]{0,19}$/.test(env.SOURCE_RUN_ID||"")&&/^[1-9][0-9]{0,3}$/.test(env.RUN_ATTEMPT||"")&&/^[0-9a-f]{40}$/.test(env.SOURCE_WORKFLOW_SHA||""),"IDENTITY");
 requireThat(typeof env.RUNNER_TEMP==="string"&&path.isAbsolute(env.RUNNER_TEMP)&&path.normalize(env.RUNNER_TEMP)===env.RUNNER_TEMP,"PATH");
 return {run_id:env.SOURCE_RUN_ID,run_attempt:env.RUN_ATTEMPT,workflow_sha:env.SOURCE_WORKFLOW_SHA};
}
function locations(env,id){
 const base=path.join(env.RUNNER_TEMP,"gh-aw");
 return {root:`/tmp/gh-aw/detector-observation-${id.run_id}-attempt-${id.run_attempt}`,
  stamp:path.join(base,"detector-observation-owner.json"),summary:path.join(base,"detector-observation-summary.json")};
}
function anchoredOpen(file,directoryOnly=false,scope=null){
 requireThat(process.platform==="linux"&&path.isAbsolute(file)&&path.normalize(file)===file,"PATH");
 const parts=file.split("/").filter(Boolean);
 let fd=fs.openSync("/",fs.constants.O_RDONLY|fs.constants.O_DIRECTORY|fs.constants.O_NOFOLLOW),current="";
 try{
  for(let i=0;i<parts.length;i++){
   const part=parts[i];requireThat(part!=="."&&part!=="..","PATH");current+="/"+part;
   const isDir=i<parts.length-1||directoryOnly;
   const child=fs.openSync("/proc/self/fd/"+fd+"/"+part,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW|(isDir?fs.constants.O_DIRECTORY:fs.constants.O_NONBLOCK));
   fs.closeSync(fd);fd=child;
   if(scope&&current===scope.root){const st=fs.fstatSync(fd);requireThat(st.isDirectory()&&String(st.dev)===scope.dev&&String(st.ino)===scope.ino,"OWNERSHIP");}
  }
  return fd;
 }catch(error){fs.closeSync(fd);if(["ELOOP","ENOTDIR"].includes(error.code))throw new Error("PATH");throw error;}
}
function regular(file,max,scope=null){
 const fd=anchoredOpen(file,false,scope);
 try{
  const st=fs.fstatSync(fd);requireThat(st.isFile()&&st.nlink===1&&st.size>0&&st.size<=max,"BOUND");
  const buffer=Buffer.alloc(max+1);let count=0,got;
  while(count<buffer.length&&(got=fs.readSync(fd,buffer,count,buffer.length-count,null))>0)count+=got;
  requireThat(count>0&&count<=max,"BOUND");
  const after=fs.fstatSync(fd);requireThat(after.dev===st.dev&&after.ino===st.ino&&after.size===count&&after.mtimeMs===st.mtimeMs&&after.ctimeMs===st.ctimeMs,"BOUND");
  return buffer.subarray(0,count);
 }finally{fs.closeSync(fd);}
}
function directory(file,scope=null){const fd=anchoredOpen(file,true,scope);try{return fs.fstatSync(fd);}finally{fs.closeSync(fd);}}
function boundedEntries(dir,max,scope=null){
 const fd=anchoredOpen(dir,true,scope);
 try{
  const anchor="/proc/self/fd/"+fd,names=[],iterator=fs.opendirSync(anchor);
  try{for(let entry;(entry=iterator.readSync())!==null;){names.push(entry.name);requireThat(names.length<=max,"BOUND");}}finally{iterator.closeSync();}
  return names.map(name=>{const st=fs.lstatSync(anchor+"/"+name);requireThat(!st.isSymbolicLink(),"PATH");return {name,file:path.join(dir,name),st};});
 }finally{fs.closeSync(fd);}
}
function owned(p,id){
 const stamp=JSON.parse(regular(p.stamp,4096).toString("utf8"));
 requireThat(Object.keys(stamp).sort().join(",")==="dev,ino,root,run_attempt,run_id,schema_version,workflow_sha","OWNERSHIP");
 requireThat(stamp.schema_version===PREFIX+".owner/v1"&&stamp.root===p.root&&Object.keys(id).every(k=>stamp[k]===id[k]),"OWNERSHIP");
 const st=directory(p.root);
 requireThat(st.uid===process.getuid()&&(st.mode&0o777)===0o700&&String(st.dev)===stamp.dev&&String(st.ino)===stamp.ino,"OWNERSHIP");
 return stamp;
}
export function prepare(env=process.env){
 const id=identity(env),p=locations(env,id);
 directory("/tmp/gh-aw");directory(path.join(env.RUNNER_TEMP,"gh-aw"));
 requireThat(!fs.existsSync(p.root)&&!fs.existsSync(p.stamp)&&!fs.existsSync(p.summary),"OWNERSHIP");
 fs.mkdirSync(p.root,{mode:0o700});fs.mkdirSync(path.join(p.root,"copilot"),{mode:0o700});const st=directory(p.root);
 const stamp={schema_version:PREFIX+".owner/v1",...id,root:p.root,dev:String(st.dev),ino:String(st.ino)};
 fs.writeFileSync(p.stamp,JSON.stringify(stamp)+"\n",{flag:"wx",mode:0o600});
 owned(p,id);
 requireThat(typeof env.GITHUB_OUTPUT==="string"&&path.isAbsolute(env.GITHUB_OUTPUT),"PATH");
 fs.appendFileSync(env.GITHUB_OUTPUT,"parent="+p.root+"\n"+"copilot_home="+path.join(p.root,"copilot")+"\n");
 requireThat(typeof env.GITHUB_ENV==="string"&&path.isAbsolute(env.GITHUB_ENV)&&path.normalize(env.GITHUB_ENV)===env.GITHUB_ENV,"PATH");
 const envParent=anchoredOpen(path.dirname(env.GITHUB_ENV),true);
 try{
  const envFd=fs.openSync("/proc/self/fd/"+envParent+"/"+path.basename(env.GITHUB_ENV),fs.constants.O_WRONLY|fs.constants.O_APPEND|fs.constants.O_NOFOLLOW|fs.constants.O_NONBLOCK);
  try{
   const envStat=fs.fstatSync(envFd);requireThat(envStat.isFile()&&envStat.nlink===1&&envStat.uid===process.getuid()&&envStat.size<=65536,"PATH");
   owned(p,id);directory(path.join(p.root,"copilot"),stamp);
   fs.writeSync(envFd,"COPILOT_HOME="+path.join(p.root,"copilot")+"\n");
  }finally{fs.closeSync(envFd);}
 }finally{fs.closeSync(envParent);}
 return p.root;
}
export function analyzeNative(raw){
 requireThat(raw.length>0&&raw.length<=8*1024*1024,"BOUND");requireThat(raw.at(-1)===10,"EVENT_TRUNCATED");
 let lines;try{lines=new TextDecoder("utf-8",{fatal:true}).decode(raw).split("\n").filter(x=>x.trim());}catch{throw new Error("EVENT_JSON");}
 requireThat(lines.length<=4096,"BOUND");
 const key=crypto.randomBytes(32),hash=s=>crypto.createHmac("sha256",key).update(s,"utf8").digest("hex");
 try{
 const starts=new Map(),ends=new Map();
 const out={event_records:lines.length,tool_starts:0,tool_completions:0,linked_calls:0,unmatched_starts:0,unmatched_completions:0,
  native_tool_success:0,native_tool_failure:0,shell_exit_zero:0,shell_exit_nonzero:0,shell_exit_unknown:0,
  repeated_command_occurrences:0,repeated_command_and_result_occurrences:0,result_unavailable:0,native_terminal_event_present:false};
 for(const line of lines){
  let e;try{e=JSON.parse(line);}catch{throw new Error("EVENT_JSON");}
  requireThat(e&&typeof e==="object"&&!Array.isArray(e)&&typeof e.type==="string"&&e.data&&typeof e.data==="object"&&!Array.isArray(e.data),"EVENT_SHAPE");
  const d=e.data;
  if(e.type==="tool.execution_start"){
   requireThat(typeof d.toolCallId==="string"&&d.toolCallId.length>0&&d.toolCallId.length<=256&&!starts.has(d.toolCallId)&&typeof d.toolName==="string","LINKAGE");
   const command=d.toolName==="bash"?d.arguments?.command:undefined;
   requireThat(d.toolName!=="bash"||typeof command==="string","EVENT_SHAPE");
   starts.set(d.toolCallId,{shell:d.toolName==="bash",command:command===undefined?null:hash(command)});out.tool_starts++;
  }else if(e.type==="tool.execution_complete"){
   requireThat(typeof d.toolCallId==="string"&&d.toolCallId.length>0&&d.toolCallId.length<=256&&!ends.has(d.toolCallId)&&typeof d.success==="boolean","LINKAGE");
   const code=d.shellExecution?.exitCode;
   requireThat(code===undefined||Number.isInteger(code)&&code>=0&&code<=255,"EVENT_SHAPE");
   const content=typeof d.result?.content==="string"?d.result.content:null;
   ends.set(d.toolCallId,{success:d.success,code,result:content===null?null:hash(content)});out.tool_completions++;
  }else if(["session.result","session.shutdown","session.end"].includes(e.type))out.native_terminal_event_present=true;
 }
 const commands=new Set(),pairs=new Set();
 for(const [id,start] of starts){
  const end=ends.get(id);if(!end){out.unmatched_starts++;continue;}out.linked_calls++;
  if(end.success)out.native_tool_success++;else out.native_tool_failure++;
  if(start.shell){
   if(end.code===undefined)out.shell_exit_unknown++;else if(end.code===0)out.shell_exit_zero++;else out.shell_exit_nonzero++;
   if(commands.has(start.command))out.repeated_command_occurrences++;commands.add(start.command);
   if(end.result===null)out.result_unavailable++;else{const pair=start.command+":"+end.result;if(pairs.has(pair))out.repeated_command_and_result_occurrences++;pairs.add(pair);}
  }
 }
 for(const id of ends.keys())if(!starts.has(id))out.unmatched_completions++;
 return out;
 }finally{key.fill(0);}
}
function eventsPath(root,scope){
 const entries=boundedEntries(root,128,scope);
 // A surviving AWF work directory may still be owned by an interrupted process.
 requireThat(!entries.some(e=>/^awf-[A-Za-z0-9]{6}$/.test(e.name)),"SESSION");
 const home=path.join(root,"copilot");
 const selectedHome=entries.filter(e=>e.name==="copilot");
 requireThat(selectedHome.length===1&&selectedHome[0].st.isDirectory(),"SESSION");
 const children=boundedEntries(path.join(home,"session-state"),16,scope),uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
 const selected=children.filter(e=>uuid.test(e.name)&&e.st.isDirectory());requireThat(selected.length===1,"SESSION");
 let found=0;
 for(const child of children){if(!child.st.isDirectory())continue;const fd=anchoredOpen(child.file,true,scope);try{let st;try{st=fs.lstatSync("/proc/self/fd/"+fd+"/events.jsonl");}catch(error){if(error.code!=="ENOENT")throw error;}if(st){requireThat(st.isFile()&&!st.isSymbolicLink()&&st.nlink===1,"PATH");found++;}}finally{fs.closeSync(fd);}}
 requireThat(found===1,"SESSION");
 return path.join(selected[0].file,"events.jsonl");
}
export function observe(env=process.env){
 const id=identity(env),p=locations(env,id);
 const summary={schema_version:PREFIX+".summary/v1",...id,coverage:"partial",safety_authority:false,status:"unavailable",reason:"IO",cleanup:"not_attempted",counts:null};
 let hasOwnership=false;
 try{
  const scope=owned(p,id);hasOwnership=true;
  const raw=regular(eventsPath(p.root,scope),8*1024*1024,scope);
  owned(p,id);
  summary.counts=analyzeNative(raw);summary.status="observed";summary.reason=null;
 }catch(e){summary.counts=null;summary.reason=reasons.has(e.message)?e.message:"IO";}
 finally{
  if(hasOwnership){
   try{
    owned(p,id);
    // Raw data remains local. No recursive deletion or secure-erasure claim.
    // The immutable workflow is restricted to disposable GitHub-hosted Ubuntu.
    summary.cleanup="deferred_to_ephemeral_runner";
   }catch{summary.cleanup="refused_ownership_changed";summary.status="unavailable";summary.reason="OWNERSHIP";summary.counts=null;}
  }
 }
 // No raw records, paths, session/tool IDs, hashes, commands, or results leave this process.
 const outputFd=anchoredOpen(path.dirname(p.summary),true);
 try{
  const base="/proc/self/fd/"+outputFd,tmp=base+"/detector-observation-summary.json.tmp";
  fs.writeFileSync(tmp,JSON.stringify(summary)+"\n",{flag:"wx",mode:0o600});
  // Exclusive target creation also rejects a preexisting or dangling-symlink destination.
  fs.linkSync(tmp,base+"/detector-observation-summary.json");fs.unlinkSync(tmp);
 }finally{fs.closeSync(outputFd);}
 return summary;
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
 try{
  if(process.argv.length!==3||!["prepare","observe"].includes(process.argv[2]))throw new Error("IDENTITY");
  if(process.argv[2]==="prepare")prepare();else{const result=observe();if(result.status!=="observed"||result.cleanup!=="deferred_to_ephemeral_runner")process.exitCode=1;}
 }catch(e){process.stderr.write(JSON.stringify({observer:"unavailable",reason:reasons.has(e.message)?e.message:"IO",raw_exported:false})+"\n");process.exitCode=1;}
}
