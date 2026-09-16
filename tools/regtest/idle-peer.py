#!/usr/bin/env python3
"""Check the real 20-minute quiet-inbound deadline; no shortened test clock."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import socket
import time

from suite_support import Follow, free_port, greeting

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--binary", required=True, type=Path)
parser.add_argument("--peer", required=True)
parser.add_argument("--chain", required=True, type=Path)
parser.add_argument("--output", required=True, type=Path)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
data = args.output / "chain"
shutil.copytree(args.chain, data)
port = free_port()
with Follow(args.binary, args.output / "follow.log", args.peer, data, ["serve:" + str(port)]) as live:
    live.wait("following at Height")
    with socket.create_connection(("127.0.0.1", port), timeout=5) as peer:
        peer.settimeout(10)
        greeting(peer)
        started = time.monotonic()
        peer.settimeout(1230)
        print("quiet inbound seated; observing the production 1200-second deadline", flush=True)
        while peer.recv(65536):
            assert time.monotonic() - started < 1230
        elapsed = time.monotonic() - started
        assert 1190 <= elapsed <= 1230, elapsed
    assert live.process.poll() is None, live.text()
report = {"case": "quiet-inbound-production-deadline", "status": "passed", "seconds": round(elapsed, 3),
          "binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest()}
(args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report), flush=True)
