#!/usr/bin/env python3
"""Exercise the compiled consumer slice over loopback; no Bitcoin node required."""
import argparse
import hashlib
import json
import queue
import socket
import subprocess
import threading
import time


def frame(command, nonce):
    checksum = hashlib.sha256(hashlib.sha256(nonce).digest()).digest()[:4]
    return bytes.fromhex("fabfb5da") + command.ljust(12, b"\0") + len(nonce).to_bytes(4, "little") + checksum + nonce


def trial(binary):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    started = time.monotonic()
    process = subprocess.Popen([binary, str(port)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    events = queue.Queue()

    def read_output():
        for line in process.stdout:
            events.put((time.monotonic(), line.rstrip()))
        events.put((time.monotonic(), None))

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    lines = []
    try:
        while True:
            at, line = events.get(timeout=20)
            if line is None:
                raise AssertionError(f"process ended before worker progress: {lines}")
            lines.append(line)
            if line == "pulse progressed while preparation was pending":
                break
        nonce = (1).to_bytes(8, "little")
        request = frame(b"ping", nonce)
        sent_at = time.monotonic()
        with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
            # Force the answer module to retain a partial header between turns.
            client.sendall(request[:7])
            time.sleep(0.015)
            client.sendall(request[7:])
            received = b""
            while len(received) < 32:
                chunk = client.recv(32 - len(received))
                if not chunk:
                    raise AssertionError("peer closed before the complete pong")
                received += chunk
        pong_at = time.monotonic()
        assert received == frame(b"pong", nonce), received.hex()
        finished_at = None
        pulses = None
        while True:
            at, line = events.get(timeout=30)
            if line is None:
                break
            lines.append(line)
            if line.startswith("prepared=50000 pulses="):
                finished_at = at
                pulses = int(line.split("pulses=")[1])
        assert process.wait(timeout=5) == 0, lines
        assert "pong sent" in lines, lines
        assert finished_at is not None and pong_at < finished_at, lines
        assert pulses >= 2, lines
        return {"prepared": 50000, "pulses": pulses, "pong_ms": round((pong_at - sent_at) * 1000, 2),
                "finished_ms": round((finished_at - started) * 1000, 2),
                "work_remaining_after_pong_ms": round((finished_at - pong_at) * 1000, 2)}
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        reader.join(timeout=1)
        process.stdout.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary")
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    for _ in range(args.rounds):
        print(json.dumps(trial(args.binary)), flush=True)
