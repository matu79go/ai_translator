#!/usr/bin/env bash
# Status of everything the meeting assist needs on GX10 (read-only).
HOST="${GX10_SSH:-gx10}"
ssh "$HOST" 'bash -s' <<'REMOTE'
ok() { printf "  %-34s %s\n" "$1" "$2"; }
ok "Gemma 4 vLLM (shared, :8001)" "$(curl -s -m 3 localhost:8001/v1/models | grep -q gemma && echo OK || echo DOWN)"
ok "nemotron-asr.service (:8092)" "$(systemctl --user is-active nemotron-asr)"
ok "ai-translator.service (:8100)" "$(systemctl --user is-active ai-translator) / health: $(curl -s -m 3 localhost:8100/health | grep -q healthy && echo OK || echo NG)"
ok "tailscale serve :10443" "$(tailscale serve status 2>/dev/null | grep -q ':10443' && echo configured || echo missing)"
echo "  URL: https://$(tailscale status --json 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'):10443/#meeting"
REMOTE
