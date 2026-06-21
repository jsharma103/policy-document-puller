#!/bin/sh
# State Farm runs headful, so bring up a virtual display, then hand off to the
# server. We start Xvfb directly rather than via `xvfb-run`, which misbehaves
# as PID 1 in a container (it starts Xvfb but never launches the command).
Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp >/dev/null 2>&1 &
export DISPLAY=:99
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
