#!/usr/bin/env bash
# F1a: lock the director down and set its model. Idempotent; keeps a backup.
# Policy: minimal profile + explicit deny of every dangerous group (deny wins),
# allow only FilmLab's own MCP tools (filmlab__*), which arrive in F1b.
# toolSearch: false shows FilmLab's tools to the model directly. Behind OpenClaw's tool search the model had to
# reach them through dispatch_action, and gemma kept nesting arguments, calling hidden names (empty replies) and
# malforming searches. Allow/deny policy applies the same either way. .tools is merged so other keys (elevated) stay.
set -euo pipefail
source ~/.profile

CFG=~/.openclaw/openclaw.json
MODEL="${FILMLAB_DIRECTOR_MODEL:-gemma4:31b-cloud}"
cp "$CFG" "$CFG.pre-policy.$(date +%Y%m%d%H%M%S)"

DENY='["group:runtime","group:fs","group:web","group:ui","group:automation",
       "group:messaging","group:nodes","group:media","group:memory",
       "sessions_spawn","sessions_send","subagents","conversations_send",
       "conversations_turn","suggest_task","skill_workshop","skill_library"]'

jq --arg model "ollama/$MODEL" --arg id "$MODEL" --argjson deny "$DENY" '
  .tools = ((.tools // {}) + {profile: "minimal", deny: $deny, toolSearch: false})
  | .agents.defaults.model.primary = $model
  | .agents.entries.director.tools = {profile: "minimal", allow: ["session_status", "filmlab__*"], deny: $deny}
  | .models.providers.ollama.models |= (
      map(select(.id != $id)) + [{
        id: $id, name: $id, reasoning: true, input: ["text", "image"],
        cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0},
        contextWindow: 262144, maxTokens: 8192,
        compat: {supportsTools: true, supportsUsageInStreaming: true, supportsJsonSchemaResponseFormat: false}
      }])
' "$CFG" > "$CFG.tmp"
mv "$CFG.tmp" "$CFG"
chmod 600 "$CFG"
jq '{tools, model: .agents.defaults.model, director: .agents.entries.director}' "$CFG"
