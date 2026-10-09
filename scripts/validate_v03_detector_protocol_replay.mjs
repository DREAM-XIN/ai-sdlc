#!/usr/bin/env node
// Offline diagnostic only. Real pinned detector/harness/CLI/proxy; fake inference endpoint.
// No model quality or liveness claim. The outer caller must enforce Docker --network none.
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import http from "node:http";
import net from "node:net";
import crypto from "node:crypto";
import {createRequire} from "node:module";
import {spawn, fork, spawnSync} from "node:child_process";
import {fileURLToPath} from "node:url";

const require = createRequire(import.meta.url);
const self = fileURLToPath(import.meta.url);
function ensure(value, code) { if (!value) throw new Error(code); }
function gitBlob(file) {
  const bytes = fs.readFileSync(file);
  return crypto.createHash("sha1").update(Buffer.from("blob " + bytes.length + "\0")).update(bytes).digest("hex");
}
const forbidden = /TOKEN|SECRET|PASSWORD|PRIVATE_KEY|CREDENTIAL/i;
function requestFamily(req) {
  const method = ["GET", "POST"].includes(req.method) ? req.method : "OTHER";
  const route = /\/chat\/completions$/.test(req.url) ? "COMPLETIONS" :
    /\/models(?:\/[^?]*)?$/.test(req.url) ? "MODELS" :
    /\/responses$/.test(req.url) ? "RESPONSES" : "OTHER";
  return method + "_" + route;
}

async function proxyChild() {
  const [root, targetPort, cap] = process.argv.slice(3);
  process.env.AWF_MAX_RUNS = cap;
  const {createProviderServer} = require(path.join(root, "server.js"));
  const {createCopilotAdapter} = require(path.join(root, "providers/copilot.js"));
  const {getMaxRunsReflectState} = require(path.join(root, "guards/max-runs-guard.js"));
  const adapter = createCopilotAdapter({
    COPILOT_API_TARGET: "http://127.0.0.1:" + targetPort,
    COPILOT_PROVIDER_API_KEY: "offline-dummy-not-a-real-credential",
    COPILOT_PROVIDER_TYPE: "openai",
    COPILOT_PROVIDER_WIRE_API: "completions",
  });
  const server = createProviderServer(adapter);
  let rejected = 0;
  const requests = {};
  server.on("request", (_req, res) => {
    const family = requestFamily(_req); requests[family] = (requests[family] || 0) + 1;
    res.once("finish", () => { if (res.statusCode === 429) rejected++; });
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  process.send({kind: "ready", port: server.address().port});
  process.on("message", message => {
    if (message === "stats") process.send({kind: "stats", guard: getMaxRunsReflectState(), rejected, requests});
    if (message === "stop") server.close(() => process.exit(0));
  });
}

function runCaptured(command, args, env, cwd, logPath, limitMs) {
  return new Promise((resolve, reject) => {
    const fd = fs.openSync(logPath, "w", 0o600);
    const child = spawn(command, args, {env, cwd, detached: true, stdio: ["ignore", fd, fd]});
    fs.closeSync(fd);
    const timer = setTimeout(() => {
      try { process.kill(-child.pid, "SIGKILL"); } catch {}
      reject(new Error("OUTER_WATCHDOG"));
    }, limitMs);
    child.once("error", () => { clearTimeout(timer); reject(new Error("PROCESS_START")); });
    child.once("close", (code, signal) => {
      clearTimeout(timer);
      resolve({code, signal});
    });
  });
}
function childMessage(child, kind, timeoutMs = 10000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { cleanup(); reject(new Error("PROXY_PROTOCOL")); }, timeoutMs);
    function cleanup() { clearTimeout(timer); child.off("message", receive); child.off("exit", exited); }
    function receive(message) { if (message?.kind === kind) { cleanup(); resolve(message); } }
    function exited() { cleanup(); reject(new Error("PROXY_EXIT")); }
    child.on("message", receive); child.once("exit", exited);
  });
}
function outputValues(file) {
  return Object.fromEntries(fs.readFileSync(file, "utf8").trim().split("\n").filter(Boolean).map(line => {
    const index = line.indexOf("="); return [line.slice(0, index), line.slice(index + 1)];
  }));
}
function containsMarker(value, marker) {
  if (typeof value === "string") {
    if (value.includes(marker)) return true;
    try { return containsMarker(JSON.parse(value), marker); } catch { return false; }
  }
  if (Array.isArray(value)) return value.some(item => containsMarker(item, marker));
  if (value && typeof value === "object") return Object.values(value).some(item => containsMarker(item, marker));
  return false;
}
function toolDefinitions(request) {
  return (request.tools || []).map(tool => tool.function || tool);
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
function noEngineDescendants(copilotBinary) {
  for (const item of fs.readdirSync("/proc")) {
    if (!/^[0-9]+$/.test(item) || Number(item) === process.pid) continue;
    let argv;
    try { argv = fs.readFileSync("/proc/" + item + "/cmdline", "utf8").split("\0"); } catch { continue; }
    ensure(!(argv[0] === copilotBinary || path.basename(argv[0] || "") === "copilot"
      || path.basename(argv[1] || "") === "copilot_harness.cjs"), "ENGINE_DESCENDANT_REMAINS");
  }
}

async function runCase(options, label, cap, retries) {
  const {proxyRoot, actionsRoot, detectorBinary, copilotBinary, workRoot} = options;
  const root = path.join(workRoot, label);
  fs.mkdirSync(path.join(root, "artifacts", "aw-prompts"), {recursive: true});
  fs.mkdirSync(path.join(root, "runner", "gh-aw"), {recursive: true});
  fs.mkdirSync(path.join(root, "home"), {recursive: true});
  fs.mkdirSync(path.join(root, "bin"), {recursive: true});
  fs.symlinkSync(actionsRoot, path.join(root, "runner", "gh-aw", "actions"));
  for (const name of ["prompts", "safeoutputs", "mcp-scripts"]) {
    fs.symlinkSync("/inputs/" + name, path.join(root, "runner", "gh-aw", name));
  }
  fs.symlinkSync(copilotBinary, path.join(root, "bin", "copilot"));
  fs.symlinkSync(detectorBinary, path.join(root, "bin", "threat-detect"));
  fs.writeFileSync(path.join(root, "artifacts", "aw-prompts", "prompt.txt"),
    "Analyze this harmless offline fixture. A recommendation has no external authority.\n");
  fs.writeFileSync(path.join(root, "artifacts", "agent_output.json"), JSON.stringify({items: []}));
  fs.writeFileSync(path.join(root, "artifacts", "aw_info.json"), JSON.stringify({workflow_name: "offline-protocol-fixture"}));
  const marker = '{"fixture":"detector-repeat","status":"ok"}';
  const ledger = path.join(root, "tool-ledger");
  const resultCall = path.join(root, "result-tool-called");
  const repeatCommand = "printf '%s\\n' '" + marker + "' | tee -a '" + ledger + "'";
  const verdictCommand = "printf called > '" + resultCall + "'; threat_detection_result --prompt-injection=false --secret-leak=false --malicious-patch=false";
  const state = {requests: 0, feedback: 0, expected: null, resultSent: false, fault: null, httpFamilies: {}};
  let executionSummary = null;
  const provider = http.createServer((req, res) => {
    const family = requestFamily(req); state.httpFamilies[family] = (state.httpFamilies[family] || 0) + 1;
    let raw = "";
    req.on("data", bytes => { raw += bytes; if (raw.length > 4 * 1024 * 1024) req.destroy(); });
    req.on("end", () => {
      try {
        if (req.method === "GET" && /\/models$/.test(req.url)) {
          res.writeHead(200, {"Content-Type": "application/json"});
          res.end(JSON.stringify({object: "list", data: [{id: "deepseek-chat", object: "model", owned_by: "offline"}]}));
          return;
        }
        ensure(req.method === "POST" && /\/chat\/completions$/.test(req.url), "UNEXPECTED_PROVIDER_PATH");
        const request = JSON.parse(raw);
        ensure(request.model === "deepseek-chat", "MODEL_ROUTE");
        if (state.expected && !state.resultSent) {
          const feedback = (request.messages || []).filter(message =>
            message.role === "tool" && message.tool_call_id === state.expected);
          ensure(feedback.length === 1, "TOOL_FEEDBACK_LINKAGE");
          const content = typeof feedback[0].content === "string" ? feedback[0].content : JSON.stringify(feedback[0].content);
          ensure(containsMarker(content, marker), "TOOL_FEEDBACK_CONTENT");
          const executed = fs.readFileSync(ledger, "utf8").trim().split("\n");
          ensure(executed.length === state.feedback + 1 && executed.every(row => row === marker), "TOOL_EXECUTION_LEDGER");
          state.feedback++;
        }
        state.requests++;
        ensure(state.requests <= 55, "UNEXPECTED_PROVIDER_RETRY");
        if (state.resultSent) {
          res.writeHead(200, {"Content-Type": "application/json"});
          res.end(JSON.stringify({id: "offline-complete", object: "chat.completion", created: 1,
            model: "deepseek-chat", choices: [{index: 0, message: {role: "assistant", content: "Done."}, finish_reason: "stop"}]}));
          return;
        }
        const shell = toolDefinitions(request).find(tool => tool.name === "bash");
        ensure(shell, "SHELL_TOOL_NOT_ADVERTISED");
        const isVerdict = state.feedback === 52;
        const callId = "offline-call-" + state.requests;
        const args = shellArguments(shell, isVerdict ? verdictCommand : repeatCommand);
        state.expected = callId;
        state.resultSent = isVerdict;
        completion(res, request, callId, shell.name, args);
      } catch (error) {
        state.fault = /^[A-Z_]+$/.test(error.message) ? error.message : "PROVIDER_PROTOCOL";
        res.writeHead(500, {"Content-Type": "application/json"});
        res.end(JSON.stringify({error: {type: "offline_fixture_protocol_failure"}}));
      }
    });
  });
  await new Promise(resolve => provider.listen(0, "127.0.0.1", resolve));
  const proxyLog = path.join(root, "proxy.log");
  const proxyFd = fs.openSync(proxyLog, "w", 0o600);
  const baseEnv = {PATH: path.join(root, "bin") + ":/usr/local/bin:/usr/bin:/bin",
                   HOME: path.join(root, "home"), LANG: "C.UTF-8"};
  const proxy = fork(self, ["--proxy", proxyRoot, String(provider.address().port), String(cap)], {
    env: {...baseEnv, AWF_MAX_RUNS: String(cap), AWF_MAX_CACHE_MISSES: "20"},
    stdio: ["ignore", proxyFd, proxyFd, "ipc"],
  });
  fs.closeSync(proxyFd);
  try {
    const ready = await childMessage(proxy, "ready");
    const env = {...baseEnv,
      RUNNER_TEMP: path.join(root, "runner"), GITHUB_WORKSPACE: path.join(root, "artifacts"),
      COPILOT_MODEL: "deepseek-chat", COPILOT_PROVIDER_BASE_URL: "http://127.0.0.1:" + ready.port,
      COPILOT_PROVIDER_API_KEY: "offline-dummy-not-a-real-credential",
      COPILOT_PROVIDER_TYPE: "openai", COPILOT_PROVIDER_WIRE_API: "completions",
      GH_AW_PHASE: "detection", GH_AW_MAX_TURNS: "50", GH_AW_HARNESS_MAX_RETRIES: String(retries),
      GH_AW_HARNESS_INITIAL_DELAY_MS: "1", GH_AW_HARNESS_MAX_DELAY_MS: "1",
      AWF_REFLECT_ENABLED: "0", GH_AW_DETECTION_CONTINUE_ON_ERROR: "false",
      RUN_DETECTION: "true", THREAT_DETECT_INSTALL_OUTCOME: "success",
    };
    const result = path.join(root, "detection_result.json");
    const detectionLog = path.join(root, "detection.log");
    const execution = await runCaptured(detectorBinary,
      ["--engine", "copilot", "--model", "deepseek-chat", "--max-turns", "50", "--retries", "0",
       "--engine-timeout", "5m", "--output", result, path.join(root, "artifacts")],
      env, path.join(root, "artifacts"), detectionLog, 330000);
    ensure(!state.fault, state.fault || "PROVIDER_PROTOCOL");
    const terminal = [...fs.readFileSync(detectionLog, "utf8").matchAll(/THREAT_DETECTION_STATUS: reason=(result_recorded|config_error|engine_error|engine_timeout|invalid_report_exhausted|cancelled|output_write_error) exit=([0-9]+)/g)];
    ensure(terminal.length === 1 && Number(terminal[0][2]) === execution.code, "DETECTOR_TERMINAL_STATUS");
    const terminationReason = terminal[0][1];
    const startupLog = fs.readFileSync(detectionLog, "utf8");
    const moduleAllowlist = [["shim.cjs","HARNESS_DEP_01"],["error_helpers.cjs","HARNESS_DEP_02"],["actions_secret_masking.cjs","HARNESS_DEP_03"],["messages_core.cjs","HARNESS_DEP_04"],["process_runner.cjs","HARNESS_DEP_05"],["copilot_sdk_sidecar.cjs","HARNESS_DEP_06"],["harness_retry_config.cjs","HARNESS_DEP_07"],["harness_retry_runner.cjs","HARNESS_DEP_08"],["awf_reflect.cjs","HARNESS_DEP_09"],["safeoutputs_cli.cjs","HARNESS_DEP_10"],["permission_denied_helpers.cjs","HARNESS_DEP_11"],["harness_retry_guard.cjs","HARNESS_DEP_12"],["harness_crash_signals.cjs","HARNESS_DEP_13"],["detect_agent_errors.cjs","HARNESS_DEP_14"],["model_fallback.cjs","HARNESS_DEP_15"],["model_costs.cjs","HARNESS_DEP_16"],["resolve_model_alias.cjs","HARNESS_DEP_17"],["ai_credits_context.cjs","HARNESS_DEP_18"]];
    const missingSpecifiers = [...startupLog.matchAll(/Cannot find module ['"]([^'"]+)['"]/g)].map(match => match[1]);
    const publicSources = fs.readdirSync(actionsRoot).filter(name => /^[a-zA-Z0-9_.-]+\.cjs$/.test(name)).sort();
    const literalInventory = [];
    for (const [sourceIndex, name] of publicSources.entries()) {
      const filename = path.join(actionsRoot, name);
      ensure(fs.realpathSync(filename).startsWith(fs.realpathSync(actionsRoot) + "/"), "STATIC_INVENTORY_BOUNDARY");
      const stat = fs.statSync(filename);
      ensure(stat.isFile() && stat.size < 4 * 1024 * 1024, "STATIC_INVENTORY_BOUND");
      const source = fs.readFileSync(filename, "utf8");
      const literals = [...new Set([...source.matchAll(/require\(\s*["']([^"'\n]+)["']\s*\)/g)].map(match => match[1]))].sort();
      for (const [literalIndex, literal] of literals.entries()) literalInventory.push({sourceIndex, literalIndex, literal});
    }
    const bundleRoot = path.dirname(copilotBinary);
    const bundleFiles = [];
    function listBundle(dir, prefix = "") {
      for (const entry of fs.readdirSync(dir, {withFileTypes: true}).sort((a,b) => a.name.localeCompare(b.name))) {
        const relative = prefix + entry.name;
        const filename = path.join(dir, entry.name);
        ensure(fs.realpathSync(filename).startsWith(fs.realpathSync(bundleRoot) + "/"), "BUNDLE_INVENTORY_BOUNDARY");
        if (entry.isDirectory()) listBundle(filename, relative + "/");
        else { ensure(entry.isFile(), "BUNDLE_INVENTORY_TYPE"); bundleFiles.push(relative); }
        ensure(bundleFiles.length <= 64, "BUNDLE_INVENTORY_BOUND");
      }
    }
    listBundle(bundleRoot);
    bundleFiles.sort();
    const argumentTokens = ["copilot", "node", "--add-dir", "--log-level", "all", "--disable-builtin-mcps",
      "--no-ask-user", "--allow-all-tools", "--prompt", "--prompt-file", "--continue"];
    const syntheticMissingModuleRelative = [...new Set(missingSpecifiers)].map(specifier => {
      for (const directory of [root]) {
        if (!specifier.startsWith(directory + "/")) continue;
        const relative = specifier.slice(directory.length + 1);
        if (/^[A-Za-z0-9_.@+\/-]{1,160}$/.test(relative) &&
            relative.split("/").every(segment => segment && segment !== "." && segment !== "..") &&
            path.normalize(relative) === relative) return relative;
      }
      return "UNKNOWN";
    });
    const caseModuleRelations = [...new Set(missingSpecifiers)].map(specifier => {
      const locations = [path.join(root, "artifacts"), root, path.join(root, "bin"), path.join(root, "home")];
      const matches = [];
      for (const [locationIndex, directory] of locations.entries()) {
        for (const [fileIndex, file] of bundleFiles.entries()) {
          if (specifier === path.join(directory, file)) matches.push({kind: "BUNDLE_FILE", locationIndex, fileIndex});
        }
        for (const [tokenIndex, token] of argumentTokens.entries()) {
          if (specifier === path.join(directory, token)) matches.push({kind: "FIXED_ARGUMENT", locationIndex, tokenIndex});
        }
      }
      return matches.length ? matches : [{kind: "UNKNOWN_RELATION"}];
    });
    const staticModuleMatches = [...new Set(missingSpecifiers)].map(specifier => {
      const matches = literalInventory.filter(entry => specifier === entry.literal ||
        (entry.literal.startsWith("./") && specifier === path.resolve(actionsRoot, entry.literal)));
      if (matches.length) return {kind: "PINNED_LITERAL", indices: matches.map(entry => [entry.sourceIndex, entry.literalIndex])};
      const family = specifier.startsWith(path.join(root, "bin") + "/") ? "FIXTURE_BIN_CHILD" :
        specifier.startsWith("/inputs/copilot/") ? "INPUT_CLI_CHILD" :
        specifier.startsWith(actionsRoot + "/") || specifier.startsWith(path.join(root, "runner", "gh-aw", "actions") + "/") ? "ACTIONS_CHILD" :
        specifier.startsWith(root + "/") ? "CASE_WORKSPACE" :
        specifier.startsWith("/inputs/") ? "READONLY_INPUT" :
        specifier.startsWith("/tmp/") ? "TEMPORARY" :
        specifier.startsWith("/") ? "OTHER_ABSOLUTE" :
        specifier.startsWith(".") ? "RELATIVE" : "BARE_PACKAGE";
      return {kind: "UNKNOWN_MODULE", family};
    });
    const moduleClasses = missingSpecifiers.map(specifier => {
      const entry = moduleAllowlist.find(([name]) => specifier === "./" + name || specifier === path.join(actionsRoot, name));
      if (entry) return entry[1];
      if (specifier === path.join(root, "runner", "gh-aw", "actions", "copilot_harness.cjs")) return "HARNESS_ENTRY";
      if (specifier === copilotBinary || specifier === path.join(root, "bin", "copilot")) return "CLI_ENTRY";
      return "UNKNOWN_MODULE";
    });
    const missingDirectDependencies = moduleAllowlist.filter(([name]) => !fs.existsSync(path.join(actionsRoot, name))).map(([, code]) => code);
    const startMarker = startupLog.indexOf("attempt 1: process started");
    const endMarker = startupLog.indexOf("attempt 1: process exit event", startMarker);
    let sanitizedSyntheticCLIError = null;
    if (startMarker >= 0 && endMarker > startMarker) {
      const firstCLI = startupLog.slice(startMarker, endMarker);
      for (const rawLine of firstCLI.split("\n")) {
        const line = rawLine.replace(/\x1b\[[0-9;]*m/g, "").trim();
        if (!/^(?:Error|error):/.test(line) ||
            /prompt|argument|argv|environment|credential|token|password|secret|api.?key|[A-Z_]{3,}=|Analyze this harmless/i.test(line)) continue;
        const scrubbed = line.replace(/https?:\/\/[^\s]+/g, "[URL]")
          .replace(/["'][^"']*["']/g, "[QUOTED]")
          .replace(/(?:^|\s)\/[A-Za-z0-9_.@+\/-]+/g, " [PATH]");
        if (!/^[\x20-\x7e]+$/.test(scrubbed)) continue;
        sanitizedSyntheticCLIError = scrubbed.slice(0, 200);
        break;
      }
    }
    const firstAttempt = startupLog.match(/attempt 1 failed: exitCode=([0-9]+) failureClass=(invocation_cap_exceeded|ai_credits_exhausted|api_proxy_guard_rejected|capi_quota_exceeded|mcp_policy_blocked|model_not_supported|http_400_response_error|null_type_tool_call|no_auth_info|authentication_failed|sdk_session_idle_timeout|mcp_gateway_shutdown|permission_denied|capi_error_400|long_run_exit|partial_execution|no_output)\b/);
    const firstProcess = startupLog.match(/attempt 1: process closed exitCode=([0-9]+) stdout=([0-9]+)B stderr=([0-9]+)B/);
    const firstAttemptOutcome = firstAttempt ? {exit: Number(firstAttempt[1]), classification: firstAttempt[2]} : {classification: "NOT_RECORDED"};
    if (firstProcess) Object.assign(firstAttemptOutcome, {process_exit: Number(firstProcess[1]),
      stdout_bytes: Number(firstProcess[2]), stderr_bytes: Number(firstProcess[3])});
    const nativeCandidates = [
      "home/.cache/copilot/pkg/linux-x64/1.0.90/runtime.linux-x64-gnu.node",
      "home/.cache/copilot/pkg/linux-x64/1.0.90/prebuilds/runtime.node",
      "home/.cache/copilot/pkg/linux-x64/1.0.90/native/runtime.linux-x64-gnu.node",
    ].map((relative, index) => {
      try { const stat = fs.statSync(path.join(root, relative)); return {index, present: stat.isFile(), bytes: stat.size}; }
      catch { return {index, present: false, bytes: 0}; }
    });
    const tempMount = fs.readFileSync("/proc/self/mountinfo", "utf8").split("\n").find(line => line.split(" ")[4] === "/tmp");
    const tmpNoexec = tempMount ? tempMount.split(" ").some(field => field.split(",").includes("noexec")) : null;
    const nativeLoadFlags = {
      failed_to_map_segment: /failed to map segment|cannot map shared object/i.test(startupLog),
      operation_not_permitted: /Operation not permitted/i.test(startupLog),
      dlopen_error: /ERR_DLOPEN_FAILED|dlopen/i.test(startupLog),
    };
    const startupClasses = [
      ["MISSING_MODULE", /Cannot find module|MODULE_NOT_FOUND/],
      ["MISSING_EXECUTABLE", /ENOENT|executable file not found/],
      ["AUTHENTICATION", /No authentication information found|Authentication failed|not authenticated/i],
      ["MODEL_UNAVAILABLE", /model.*not supported|model.*not found|no model endpoints|unresolved alias/i],
      ["CLI_ARGUMENT", /unknown option|unknown argument|invalid choice/i],
      ["FILESYSTEM", /EACCES|EROFS|permission denied|read-only file system/i],
      ["CONNECTION", /ECONNREFUSED|ENETUNREACH|fetch failed/],
      ["MISSING_PROMPT", /prompt.*missing|required.*prompt|prompt.*not found/i],
    ].filter(([, pattern]) => pattern.test(startupLog)).map(([name]) => name);
    executionSummary = {detector_exit: execution.code, detector_termination_reason: terminationReason,
      first_attempt: firstAttemptOutcome, sanitized_synthetic_cli_error: sanitizedSyntheticCLIError, native_candidates: nativeCandidates, tmp_noexec: tmpNoexec, native_load_flags: nativeLoadFlags, startup_classes: startupClasses.length ? startupClasses : ["UNCLASSIFIED"],
      missing_module_classes: [...new Set(moduleClasses)], static_module_matches: staticModuleMatches, case_module_relations: caseModuleRelations, synthetic_missing_module_relative: syntheticMissingModuleRelative, missing_direct_dependencies: missingDirectDependencies,
      official_prompt_directory_present: fs.existsSync(path.join(root, "runner", "gh-aw", "prompts"))};
    const statsPromise = childMessage(proxy, "stats");
    proxy.send("stats");
    const stats = await statsPromise;
    executionSummary.proxy_http_families = stats.requests;
    executionSummary.provider_http_families = state.httpFamilies;
    const concludeOutput = path.join(root, "conclude-output");
    fs.writeFileSync(concludeOutput, "");
    const conclusion = await runCaptured("/bin/bash", [path.join(actionsRoot, "conclude_threat_detection.sh"), result],
      {...env, GITHUB_OUTPUT: concludeOutput, DETECTION_LOG_FILE: detectionLog,
       GITHUB_STEP_SUMMARY: path.join(root, "step-summary")}, root, path.join(root, "conclude.log"), 10000);
    const values = outputValues(concludeOutput);
    noEngineDescendants(copilotBinary);
    const count = fs.existsSync(ledger) ? fs.readFileSync(ledger, "utf8").trim().split("\n").length : 0;
    if (cap === 500) {
      ensure(state.feedback === 52 && count === 52 && state.resultSent && fs.existsSync(resultCall), "BASELINE_FEEDBACK");
      ensure(execution.code === 0 && conclusion.code === 0 && values.success === "true" && values.conclusion === "success",
             "GENUINE_RESULT_REJECTED");
      const verdict = JSON.parse(fs.readFileSync(result, "utf8"));
      ensure(verdict.prompt_injection === false && verdict.secret_leak === false && verdict.malicious_patch === false,
             "GENUINE_RESULT_FIELDS");
    } else {
      ensure(state.requests === 50 && stats.guard.invocation_count === 50 && stats.guard.max_runs === 50
        && stats.rejected > 0 && fs.readFileSync(proxyLog, "utf8").includes("max_runs_exceeded"), "NATIVE_CAP_NOT_OBSERVED");
      ensure(!state.resultSent && !fs.existsSync(resultCall) && !fs.existsSync(result), "CAP_FABRICATED_VERDICT");
      ensure(execution.code !== 0 && conclusion.code !== 0 && values.success === "false" && values.conclusion === "failure",
             "CAP_DID_NOT_FAIL_CLOSED");
    }
    return {case: label, status: "PASS", upstream_responses: state.requests, linked_marker_feedbacks: state.feedback,
      executed_fixture_tools: count, native_max_runs: stats.guard.max_runs, native_invocations: stats.guard.invocation_count,
      proxy_rejections: stats.rejected, result_tool_delivered: fs.existsSync(resultCall),
      detector_exit: execution.code, detector_termination_reason: terminationReason, conclusion: values.conclusion, success: values.success};
  } catch (error) {
    error.counts = {case: label, upstream_responses: state.requests, verified_tool_feedbacks: state.feedback,
                    result_tool_sent: state.resultSent, execution: executionSummary};
    throw error;
  } finally {
    proxy.kill("SIGTERM");
    provider.closeAllConnections();
    await new Promise(resolve => provider.close(resolve));
  }
}

async function main() {
  const [proxyRoot, actionsRoot, detectorBinary, copilotBinary, workRoot] = process.argv.slice(2);
  ensure([proxyRoot, actionsRoot, detectorBinary, copilotBinary].every(value => value?.startsWith("/inputs/"))
         && workRoot === "/tmp/replay", "PATH_BOUNDARY");
  ensure(process.getuid() !== 0, "ROOT_EXECUTION_FORBIDDEN");
  ensure(Object.values(os.networkInterfaces()).flat().every(item => item.internal), "NO_EGRESS_BOUNDARY");
  await new Promise((resolve, reject) => {
    const socket = net.connect({host: "192.0.2.1", port: 9});
    const timer = setTimeout(() => { socket.destroy(); reject(new Error("EGRESS_PROBE_INCONCLUSIVE")); }, 1000);
    socket.once("connect", () => { clearTimeout(timer); socket.destroy(); reject(new Error("EGRESS_AVAILABLE")); });
    socket.once("error", error => {
      clearTimeout(timer); socket.destroy();
      if (["ENETUNREACH", "EHOSTUNREACH", "EACCES", "EPERM"].includes(error.code)) resolve();
      else reject(new Error("EGRESS_PROBE_INCONCLUSIVE"));
    });
  });
  ensure(Object.keys(process.env).every(key => !forbidden.test(key)), "INHERITED_AUTHORITY");
  ensure(!fs.existsSync("/usr/local/bin/copilot"), "CLI_SHADOWING");
  ensure(!fs.existsSync(workRoot), "STALE_REPLAY_WORKSPACE");
  fs.mkdirSync(workRoot, {recursive: true});
  ensure(crypto.createHash("sha256").update(fs.readFileSync(detectorBinary)).digest("hex")
    === "b4ecda6a8f1ee09913c40b58e5e9d3337d2173618d41b1bfdef9207e4e7959b9", "DETECTOR_PIN");
  for (const [name, expected] of [
    ["server.js", "f334dfbd38e82f7a2746ccd394ff329ebd1dd6f8"],
    ["proxy-request.js", "0425479e4242e77ccbd0995f0f55251b8fd83d16"],
    ["guards/max-runs-guard.js", "a9f5e14561aee60ea02d9cfe698588b8ef0ef556"],
    ["guards/counter-guard.js", "5903fb1430ce3369ba07d8ceb3ab0c4a63b470ea"],
    ["guards/common-guard-checks.js", "50b9df32e23d58ffdde3f16865d98ce78b09919f"],
  ]) ensure(gitBlob(path.join(proxyRoot, name)) === expected, "PROXY_PIN");
  ensure(gitBlob(path.join(actionsRoot, "copilot_harness.cjs")) === "7132b67bbef15083cf11d183a987eab668eec62c", "HARNESS_PIN");
  ensure(gitBlob(path.join(actionsRoot, "conclude_threat_detection.sh")) === "c72df00b31d59b67968c6e578bf069c616b0421e", "CONCLUDE_PIN");
  fs.mkdirSync(path.join(workRoot, "version-home"));
  const version = spawnSync(copilotBinary, ["--version"], {
    env: {HOME: path.join(workRoot, "version-home"), PATH: "/usr/local/bin:/usr/bin:/bin"}, encoding: "utf8", timeout: 10000,
  });
  ensure(version.status === 0 && /\b1\.0\.90\b/.test(version.stdout), "CLI_PIN");
  const options = {proxyRoot, actionsRoot, detectorBinary, copilotBinary, workRoot};
  const summaries = [];
  summaries.push(await runCase(options, "baseline_env50_native500", 500, 6));
  summaries.push(await runCase(options, "capped_env50_native50", 50, 0));
  console.log(JSON.stringify({status: "PASS", scope: "synthetic protocol and spend-bound only", cases: summaries}));
}

if (process.argv[2] === "--proxy") {
  proxyChild().catch(() => process.exit(2));
} else {
  main().catch(error => {
    const code = /^[A-Z_]+$/.test(error.message) ? error.message : "REPLAY_INFRASTRUCTURE";
    console.log(JSON.stringify({status: "FAIL", stage: code, counts: error.counts || null}));
    process.exitCode = 1;
  });
}
