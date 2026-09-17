#!/usr/bin/env python3
import sys
import signal
import subprocess

child = None


def sigterm_handler(signum, frame):
    global child
    if child and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
    sys.exit(0)


signal.signal(signal.SIGTERM, sigterm_handler)
signal.signal(signal.SIGINT, sigterm_handler)

cmd = [
    "/home/ubuntu/.local/bin/uv",
    "run",
    "--with",
    "fastmcp,azure-identity,requests",
    "python",
    "/home/ubuntu/ai-orchestration/power_automate_mcp_server.py",
]

child = subprocess.Popen(cmd)
try:
    ret = child.wait()
    sys.exit(ret)
except Exception:
    sys.exit(0)
