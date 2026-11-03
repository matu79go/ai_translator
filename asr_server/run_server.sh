#!/bin/bash
# Start the Nemotron ASR server on GX10 (docs/designs/meeting-assist-local.md §13).
# Deploy from WSL:  scp asr_server/{asr_server.py,run_server.sh} gx10:~/work/nemotron-asr/server/
# Start on GX10:    bash ~/work/nemotron-asr/server/run_server.sh        (stop: pkill -f asr_server.py)
cd "$(dirname "$0")"
export HF_HUB_OFFLINE=1
nohup ~/venvs/nemotron-asr/bin/python asr_server.py "$@" > asr_server.log 2>&1 &
echo "started pid $! (log: $(pwd)/asr_server.log)"
