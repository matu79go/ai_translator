#!/usr/bin/env bash
# Deploy ai_translator to GX10. Run from WSL inside the repo (after pushing to origin/master).
#   bash deploy/gx10/deploy.sh
# GX10 pulls origin/master, the frontend is built here (GX10 has no Node.js) and copied over,
# then the two user services are restarted. Gemma 4 (vLLM) is shared with other services and
# is never started or stopped from here.
set -euo pipefail
HOST="${GX10_SSH:-gx10}"
cd "$(git rev-parse --show-toplevel)"

git fetch -q origin master
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/master)" ] || [ -n "$(git status --porcelain -- backend frontend asr_server deploy)" ]; then
  echo "abort: local tree is not the pushed origin/master (commit and push first)" >&2
  exit 1
fi

echo "== GX10: pull"
ssh "$HOST" 'cd ~/ai_translator && git pull --ff-only -q && git log --oneline -1'

echo "== build frontend (WSL)"
(cd frontend && npm run build >/dev/null)
ssh "$HOST" 'rm -rf ~/ai_translator/frontend/dist && mkdir -p ~/ai_translator/frontend/dist'
tar -C frontend/dist -cf - . | ssh "$HOST" 'tar -C ~/ai_translator/frontend/dist -xf -'

echo "== GX10: deps + services"
ssh "$HOST" 'set -e; cd ~/ai_translator
  backend/.venv/bin/pip install -q -r backend/requirements.txt
  ~/venvs/nemotron-asr/bin/pip install -q websockets
  cp deploy/gx10/*.service ~/.config/systemd/user/
  systemctl --user daemon-reload
  systemctl --user enable -q nemotron-asr ai-translator
  systemctl --user restart nemotron-asr ai-translator'

bash deploy/gx10/status.sh
