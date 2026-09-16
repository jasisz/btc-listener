#!/usr/bin/env python3
"""Loopback acceptance for the production Peers and Board adapters.
Build tools/network_probe.av and pass the native executable here.
No public network, Bitcoin Core, database or consensus assumptions.
"""
import argparse
import hashlib
import json
from pathlib import Path
import signal
import socket
import struct
import subprocess
import tempfile
import time
from contextlib import ExitStack

def frame(command, body=b""):
    check = hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4]
    return bytes.fromhex("fabfb5da") + command.encode().ljust(12, b"\0") + struct.pack("<I", len(body)) + check + body

def exact(peer, size):
    chunks = []
    while size:
        chunk = peer.recv(size)
        assert chunk, "connection closed during frame"
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)

def receive(peer):
    header = exact(peer, 24)
    size = struct.unpack("<I", header[16:20])[0]
    assert size <= 4_000_000
    body = exact(peer, size)
    command = header[4:16].rstrip(b"\0").decode()
    assert frame(command, body) == header + body, "framing/order/checksum corruption"
    return command, body

def version():
    return struct.pack("<iQq", 70016, 0, int(time.time())) + bytes(52) + struct.pack("<Q", 4242) + b"\x09/nettest/" + struct.pack("<i", 0) + b"\0"

def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]

def connect(port, stack, receive_buffer=None):
    sock = stack.enter_context(socket.socket())
    if receive_buffer:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, receive_buffer)
    sock.settimeout(5)
    sock.connect(("127.0.0.1", port))
    return sock

def wait_log(process, log, text):
    deadline = time.monotonic() + 10
    while text not in log.read_text():
        assert process.poll() is None, log.read_text()
        assert time.monotonic() < deadline, log.read_text()
        time.sleep(.01)

def greet(peer, outbound=False):
    if outbound:
        assert receive(peer)[0] == "version"
    peer.sendall(frame("version", version()) + frame("verack"))
    while receive(peer)[0] != "verack":
        pass

def ping(peer):
    nonce = struct.pack("<Q", time.monotonic_ns())
    began = time.monotonic()
    request = frame("ping", nonce)
    peer.sendall(request[:7])
    time.sleep(.02)
    peer.sendall(request[7:])
    while True:
        command, body = receive(peer)
        if command == "pong":
            assert body == nonce
            elapsed = time.monotonic() - began
            assert elapsed < 1.0, elapsed
            return round(elapsed * 1000, 2)

def stop(process, log):
    began = time.monotonic()
    process.send_signal(signal.SIGINT)
    assert process.wait(timeout=3) == 0, log.read_text()
    elapsed = time.monotonic() - began
    assert elapsed < 1, elapsed
    return round(elapsed * 1000, 2)

def trial(binary, mode):
    with tempfile.TemporaryDirectory(prefix="btc-network-") as directory, ExitStack() as stack:
        log = Path(directory) / "owner.log"
        output = stack.enter_context(log.open("w"))
        listener = stack.enter_context(socket.socket())
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)
        peer_port, board_port = port(), port()
        process = subprocess.Popen([binary, f"127.0.0.1:{listener.getsockname()[1]}", str(peer_port), str(board_port)], stdout=output, stderr=subprocess.STDOUT)
        try:
            wait_log(process, log, "network ready")
            outbound = stack.enter_context(listener.accept()[0])
            outbound.settimeout(5)
            if mode == "outbound":
                # TCP connected, but Bitcoin handshake never answers. A new
                # inbound peer must still greet and receive its pong.
                assert receive(outbound)[0] == "version"
                good = connect(peer_port, stack)
                greet(good)
                latency = ping(good)
                return {"mode": mode, "pong_ms": latency, "stop_ms": stop(process, log)}

            greet(outbound, outbound=True)
            if mode == "inbound":
                silent = connect(peer_port, stack)
                silent.sendall(frame("version", version())[:7])
                wait_log(process, log, "dialled us from")
                latency = ping(outbound)
                # Slow HTTP readers must not consume per-client waits.
                slow_http = [connect(board_port, stack) for _ in range(4)]
                slow_http[0].sendall(b"GET / HTTP/1.1\r\n")
                latency = max(latency, ping(outbound))
                return {"mode": mode, "pong_ms": latency, "stop_ms": stop(process, log)}

            if mode == "expiry":
                silent = connect(peer_port, stack)
                began = time.monotonic()
                silent.sendall(frame("version", version()))
                assert receive(silent)[0] == "version"
                assert receive(silent)[0] == "verack"
                latency = 0
                # Traffic before verack must not renew the handshake deadline.
                for _ in range(4):
                    time.sleep(2)
                    silent.sendall(frame("ping", struct.pack("<Q", 42)))
                    assert receive(silent) == ("pong", struct.pack("<Q", 42))
                    latency = max(latency, ping(outbound))
                silent.settimeout(4)
                assert silent.recv(1) == b""
                elapsed = time.monotonic() - began
                assert 9 <= elapsed <= 11.5, elapsed
                return {"mode": mode, "deadline_seconds": round(elapsed, 3), "pong_ms": latency, "stop_ms": stop(process, log)}

            if mode == "refused":
                bad = connect(peer_port, stack)
                bad.sendall(frame("version", version()) * 2)
                assert receive(bad)[0] == "version"
                assert receive(bad)[0] == "verack"
                assert bad.recv(1) == b""
                return {"mode": mode, "pong_ms": ping(outbound), "stop_ms": stop(process, log)}

            slow = connect(peer_port, stack, 16384)
            greet(slow)
            slow.sendall(frame("load"))
            wait_log(process, log, "bulk queued")
            # Let the send window close before testing another peer.
            time.sleep(.3)
            latency = ping(outbound)
            command, body = receive(slow)
            assert command == "bulk" and body == bytes([7]) * 4_000_000
            assert receive(slow) == ("marker", b"\x01\x02\x03")
            http = connect(board_port, stack)
            http.sendall(b"GET / HTTP/1.1\r\n")
            time.sleep(.05)
            http.sendall(b"Host: localhost\r\n\r\n")
            response = b""
            while True:
                part = http.recv(65536)
                if not part:
                    break
                response += part
            assert response.startswith(b"HTTP/1.1 200"), response[:100]
            headers, body = response.split(b"\r\n\r\n", 1)
            length = next(line for line in headers.split(b"\r\n") if line.lower().startswith(b"content-length:"))
            assert len(body) == int(length.split(b":", 1)[1]), "truncated dashboard"
            return {"mode": mode, "pong_ms": latency, "ordered_bytes": 4_000_003, "dashboard_bytes": len(body), "stop_ms": stop(process, log)}
        except BaseException:
            print(log.read_text())
            raise
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary")
    args = parser.parse_args()
    for mode in ("inbound", "outbound", "backpressure", "expiry", "refused"):
        print(json.dumps(trial(str(Path(args.binary).resolve()), mode)), flush=True)
