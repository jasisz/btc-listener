#!/usr/bin/env python3
"""Fragmented DNS response, mid-body EOF, cancellation and absolute deadline."""
import argparse
from contextlib import ExitStack
import json
import signal
import socket
import struct
import subprocess
import time

def exact(peer, size):
    data = b""
    while len(data) < size:
        part = peer.recv(size - len(data))
        assert part
        data += part
    return data

def trial(binary, mode):
    with ExitStack() as stack:
        listener = stack.enter_context(socket.socket())
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)
        process = subprocess.Popen([binary, str(listener.getsockname()[1])], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        began = time.monotonic()
        try:
            peer = stack.enter_context(listener.accept()[0])
            peer.settimeout(5)
            question = exact(peer, struct.unpack(">H", exact(peer, 2))[0])
            answer = question[:2] + bytes.fromhex("81800001000100000000") + question[12:] + bytes.fromhex("c00c000100010000003c00047f000001")
            framed = struct.pack(">H", len(answer)) + answer
            if mode == "fragmented":
                peer.sendall(framed[:1])
                time.sleep(.03)
                peer.sendall(framed[1:7])
                time.sleep(.03)
                peer.sendall(framed[7:])
            elif mode == "eof":
                peer.sendall(framed[:7])
                peer.shutdown(socket.SHUT_WR)
            elif mode == "cancel":
                peer.sendall(framed[:1])
                time.sleep(.05)
                began = time.monotonic()
                process.send_signal(signal.SIGINT)
            elif mode == "deadline":
                # A readable one-byte length prefix used to escape the timeout
                # and block forever inside an exact-length read.
                peer.sendall(framed[:1])
            output, _ = process.communicate(timeout=18)
            elapsed = time.monotonic() - began
            assert process.returncode == 0, output
            expected = {"fragmented": "resolved 1", "eof": "closed before its complete answer", "cancel": "DNS discovery stopped", "deadline": "DNS exchange deadline expired"}[mode]
            assert expected in output, output
            if mode == "cancel":
                assert elapsed < .5, elapsed
            if mode == "deadline":
                assert 14.5 < elapsed < 16, elapsed
            return {"mode": mode, "seconds": round(elapsed, 3)}
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary")
    args = parser.parse_args()
    for mode in ("fragmented", "eof", "cancel", "deadline"):
        print(json.dumps(trial(args.binary, mode)), flush=True)
