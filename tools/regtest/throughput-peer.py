#!/usr/bin/env python3
"""Measure ping latency during real Set catch-up over a throughput.py seed.

The fake peer only announces the already downloaded tip and empty headers;
all block data comes from the identical Core-validated database fixture.
"""
import argparse
import json
import shutil
import signal
import socket
import statistics
import struct
import subprocess
import threading
import time
from pathlib import Path

from suite_support import receive, wire, wait_until


def trial(binary, fixture, label, cancel):
    metadata = json.loads((fixture / "fixture.json").read_text())
    data = fixture / (label + "-peer-chain")
    shutil.copytree(fixture / "seed", data)
    path = fixture / (label + "-peer.log")
    done, caught = threading.Event(), threading.Event()
    sent, latencies, failures = {}, [], []
    with socket.socket() as listener, path.open("w") as log:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(20)
        process = subprocess.Popen([str(binary), "regtest", "follow",
            f"127.0.0.1:{listener.getsockname()[1]}", str(data), "log"], stdout=log, stderr=subprocess.STDOUT)
        start = time.monotonic()
        try:
            with listener.accept()[0] as peer:
                peer.settimeout(20)
                lock = threading.Lock()
                def send(command, body=b""):
                    with lock:
                        peer.sendall(wire(command, body))
                assert receive(peer)[0] == "version"
                version = struct.pack("<iQq", 70016, 1, int(time.time())) + bytes(52) + struct.pack("<Q", 7654) + b"\x06/bench" + struct.pack("<i", metadata["height"]) + b"\0"
                send("version", version)
                send("verack")
                while receive(peer)[0] != "verack":
                    pass
                def reading():
                    try:
                        while not done.is_set():
                            command, body = receive(peer)
                            if command == "getheaders":
                                send("headers", b"\0")
                            elif command == "ping":
                                send("pong", body)
                            elif command == "pong":
                                nonce = struct.unpack("<Q", body)[0]
                                latencies.append((time.monotonic() - sent[nonce]) * 1000)
                    except (OSError, AssertionError) as why:
                        if not done.is_set():
                            failures.append(str(why))
                reader = threading.Thread(target=reading, daemon=True)
                reader.start()
                # Measure the Set phase, after the handshake/header exchange.
                wait_until(lambda: "utxo    connecting" in path.read_text(), 30, "Set start")
                set_start = time.monotonic()
                nonce = 0
                stop_at = None
                while time.monotonic() - start < 60:
                    text = path.read_text()
                    if f"following at Height {metadata['height']}:" in text:
                        caught.set()
                        break
                    assert process.poll() is None, text
                    assert not failures, failures
                    sent[nonce] = time.monotonic()
                    send("ping", struct.pack("<Q", nonce))
                    nonce += 1
                    if cancel and latencies:
                        stop_at = time.monotonic()
                        process.send_signal(signal.SIGINT)
                        process.wait(timeout=5)
                        break
                    time.sleep(.025)
                finished = time.monotonic()
                assert caught.is_set() or stop_at is not None, path.read_text()
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                    process.wait(timeout=5)
                assert process.returncode == 0, path.read_text()
                done.set()
                try:
                    peer.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass  # The owner may already have closed its connection.
                reader.join(timeout=2)
                assert latencies, "no ping completed during Set catch-up"
                result = {"variant": label, "cancel": cancel, "pongs": len(latencies),
                          "ping_median_ms": statistics.median(latencies), "ping_max_ms": max(latencies),
                          "set_elapsed_seconds": finished - set_start, "tip_reached": caught.is_set()}
                if stop_at is not None:
                    result["stop_ms"] = (finished - stop_at) * 1000
                    assert not caught.is_set(), "cancellation test finished catch-up before stopping"
                return result
        finally:
            done.set()
            if process.poll() is None:
                process.kill()
                process.wait()
            shutil.rmtree(data)  # This invocation's private clone only.


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--cancel", action="store_true")
    args = parser.parse_args()
    result = trial(args.binary.resolve(), args.fixture, args.label, args.cancel)
    (args.fixture / (args.label + "-peer.json")).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
