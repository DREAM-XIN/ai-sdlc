mkdir -p "/tmp/gh-aw/threat-detection"
evidence_tmp="/tmp/gh-aw/threat-detection/execution.json.tmp"
printf '{"version":1,"component":"detection","run_id":%s,"run_attempt":%s,"state":"started"}\n' "$GITHUB_RUN_ID" "$GITHUB_RUN_ATTEMPT" > "$evidence_tmp"
mv "$evidence_tmp" "/tmp/gh-aw/threat-detection/execution.json"
export GH_AW_AWF_EXECUTION_COMPONENT="detection"
export GH_AW_AWF_EXECUTION_EVIDENCE_FILE="/tmp/gh-aw/threat-detection/execution.json"
set -o pipefail
printf '%s' "$(date +%s%3N)" > /tmp/gh-aw/agent_cli_start_ms.txt
GH_AW_COPILOT_SRC="$(command -v copilot 2>/dev/null || true)"
if [ -z "$GH_AW_COPILOT_SRC" ] || [ ! -x "$GH_AW_COPILOT_SRC" ]; then
  echo "GitHub Copilot CLI executable not found on PATH after installation" >&2
  exit 127
fi
GH_AW_COPILOT_BIN="${RUNNER_TEMP}/gh-aw/bin/copilot"
mkdir -p "${RUNNER_TEMP}/gh-aw/bin"
if [ "$GH_AW_COPILOT_SRC" != "$GH_AW_COPILOT_BIN" ]; then
  cp "$GH_AW_COPILOT_SRC" "$GH_AW_COPILOT_BIN"
fi
chmod 755 "$GH_AW_COPILOT_BIN"

(umask 177 && touch /tmp/gh-aw/threat-detection/detection.log)
GH_AW_MAX_AI_CREDITS="${GH_AW_MAX_AI_CREDITS:-400}"
if [[ ! "$GH_AW_MAX_AI_CREDITS" =~ ^[0-9]+$ ]]; then
  GH_AW_MAX_AI_CREDITS="400"
fi
cp -- "scripts/v03_detector_awf_config.json" "${RUNNER_TEMP}/gh-aw/awf-config.json"
cp "${RUNNER_TEMP}/gh-aw/awf-config.json" /tmp/gh-aw/awf-config.json
export GH_AW_MODELS_JSON_PATH="/tmp/gh-aw/models.json"
GH_AW_DOCKER_HOST=""
if [[ "${DOCKER_HOST:-}" =~ ^tcp:// ]]; then
  GH_AW_DOCKER_HOST="${DOCKER_HOST}"
fi
if [[ "${DOCKER_HOST:-}" =~ ^tcp:// ]]; then
  _GH_AW_CHROOT_JSON=$(jq -c --arg src "${RUNNER_TEMP}/gh-aw" --arg user "$(id -un)" --argjson uid "$(id -u)" --argjson gid "$(id -g)" --arg home "${RUNNER_TEMP}/gh-aw/home" '.chroot={"binariesSourcePath":$src,"identity":{"user":$user,"uid":$uid,"gid":$gid,"home":$home}}' "${RUNNER_TEMP}/gh-aw/awf-config.json") || { echo "chroot config patch failed" >&2; exit 1; }
  printf '%s\n' "$_GH_AW_CHROOT_JSON" > "${RUNNER_TEMP}/gh-aw/awf-config.json"
fi
GH_AW_TOOL_CACHE_MOUNT=""
GH_AW_TOOL_CACHE="${RUNNER_TOOL_CACHE:?RUNNER_TOOL_CACHE must be set}"
if [ -d "$GH_AW_TOOL_CACHE" ]; then
  if [[ "$GH_AW_TOOL_CACHE" != /opt/* ]]; then
    GH_AW_TOOL_CACHE_MOUNT="$GH_AW_TOOL_CACHE:$GH_AW_TOOL_CACHE:ro"
  fi
fi
# shellcheck disable=SC1003,SC2016,SC2086
awf --config "${RUNNER_TEMP}/gh-aw/awf-config.json" --container-workdir "${GITHUB_WORKSPACE}" --mount "${RUNNER_TEMP}/gh-aw:${RUNNER_TEMP}/gh-aw:ro" --mount "${RUNNER_TEMP}/gh-aw:/host${RUNNER_TEMP}/gh-aw:ro" ${GH_AW_TOOL_CACHE_MOUNT:+--mount "$GH_AW_TOOL_CACHE_MOUNT"} ${GH_AW_DOCKER_HOST:+--docker-host "$GH_AW_DOCKER_HOST"} --env-all --exclude-env ACTIONS_ID_TOKEN_REQUEST_TOKEN --exclude-env ACTIONS_ID_TOKEN_REQUEST_URL --exclude-env COPILOT_GITHUB_TOKEN --exclude-env COPILOT_PROVIDER_API_KEY --mount /tmp/gh-aw:/tmp/gh-aw:rw --mount /tmp/gh-aw/threat-detection:/tmp/gh-aw/threat-detection:rw --log-level info --skip-pull \
  -- /bin/bash -c 'set +o histexpand; export PATH="${RUNNER_TEMP}/gh-aw/bin:$PATH" && : "${RUNNER_TOOL_CACHE:?RUNNER_TOOL_CACHE must be set}"; GH_AW_TOOL_CACHE="$RUNNER_TOOL_CACHE"; GH_AW_TOOL_BINS="$(find "$GH_AW_TOOL_CACHE" -maxdepth 5 -type d -name bin 2>/dev/null | tr '\''\n'\'' '\'':'\'')"; GH_AW_TOOL_BINS="${GH_AW_TOOL_BINS%:}"; export PATH="$PATH${GH_AW_TOOL_BINS:+:}$GH_AW_TOOL_BINS"; [ -n "$GOROOT" ] && export PATH="$GOROOT/bin:$PATH" || true; [ -n "$ERLANG_HOME" ] && export PATH="$ERLANG_HOME/bin:$PATH" || true && threat-detect --engine copilot --retries 0 --output /tmp/gh-aw/threat-detection/detection_result.json /tmp/gh-aw/threat-detection' 2>&1 | tee -a /tmp/gh-aw/threat-detection/detection.log
