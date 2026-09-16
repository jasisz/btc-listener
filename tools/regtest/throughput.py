#!/usr/bin/env python3
"""Compare native Set throughput using identical Core-validated regtest blocks.

Each pair fans one mature coinbase out to many P2WPKH outputs, then spends
them in a consolidation block. generateblock bypasses mempool policy, not
consensus validation. Download once; clone the closed pre-Set database for
every run. No public network or shared Core directory is used.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import os
import shutil
import sys
import struct
import subprocess
import time
import urllib.request

from suite_support import Core, Suite


def rpc(core, method, *params):
    cookie = (core.data / "regtest" / ".cookie").read_text().strip()
    request = urllib.request.Request(
        f"http://127.0.0.1:{core.rpc_port}/",
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        {"Authorization": "Basic " + base64.b64encode(cookie.encode()).decode(),
         "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=180) as response:
        answer = json.load(response)
    assert answer.get("error") is None, answer
    return answer["result"]


def compact(n):
    if n < 253:
        return bytes([n])
    if n <= 65535:
        return b"\xfd" + struct.pack("<H", n)
    return b"\xfe" + struct.pack("<I", n)


def transaction(inputs, outputs):
    return (struct.pack("<i", 2) + compact(len(inputs))
            + b"".join(bytes.fromhex(txid)[::-1] + struct.pack("<I", index)
                       + b"\0\xff\xff\xff\xff" for txid, index in inputs)
            + compact(len(outputs))
            + b"".join(struct.pack("<q", value) + compact(len(script)) + script
                       for value, script in outputs) + bytes(4)).hex()


def mined(core, address, raw):
    signed = rpc(core, "signrawtransactionwithwallet", raw)
    assert signed["complete"], signed.get("errors")
    txid = rpc(core, "decoderawtransaction", signed["hex"])["txid"]
    block = rpc(core, "generateblock", address, [signed["hex"]])["hash"]
    return txid, rpc(core, "getblock", block)["size"]


def prepare(args, binary):
    with Core(args.core_bin, args.output / "core", stop_timeout=180) as core:
        rpc(core, "createwallet", "throughput")
        address = rpc(core, "getnewaddress", "", "bech32")
        script = bytes.fromhex(rpc(core, "getaddressinfo", address)["scriptPubKey"])
        hashes = rpc(core, "generatetoaddress", 150, address)
        sizes = []
        for index in range(args.pairs):
            coinbase = rpc(core, "getblock", hashes[index], 2)["tx"][0]
            output = next(v for v in coinbase["vout"] if v["scriptPubKey"].get("address") == address)
            amount = round(output["value"] * 100_000_000)
            value, fee = 100_000, 1_000_000
            fan, size = mined(core, address, transaction([(coinbase["txid"], output["n"])],
                [(value, script)] * args.outputs + [(amount - args.outputs * value - fee, script)]))
            sizes.append(size)
            _, size = mined(core, address, transaction([(fan, n) for n in range(args.outputs)],
                [(args.outputs * value - fee, script)]))
            sizes.append(size)
            print(f"prepared pair {index + 1}/{args.pairs}", flush=True)
        height = core.height()
        suite = Suite(binary, args.output)
        suite.data = args.output / "seed"
        suite.cli("seed-headers", "headers", core.peer, suite.data)
        suite.cli("seed-bodies", "bodies", core.peer, suite.data, 1, height, timeout=300)
        metadata = {"height": height, "tip": rpc(core, "getbestblockhash"),
                    "pairs": args.pairs, "fanout": args.outputs, "large_block_bytes": sizes}
        (args.output / "fixture.json").write_text(json.dumps(metadata, indent=2) + "\n")
        return metadata


def measure(args, label, binary, trial, height):
    data = args.output / "trial-chain"
    assert not data.exists(), "unfinished trial directory; inspect it before retrying"
    shutil.copytree(args.output / "seed", data)
    log = args.output / f"{label}-{trial}.log"
    started = time.monotonic()
    with log.open("w") as stream:
        child = subprocess.Popen([str(binary), "regtest", "utxo", str(data), str(height)],
                                 stdout=stream, stderr=subprocess.STDOUT)
        try:
            deadline = started + 300
            while True:
                pid, status, usage = os.wait4(child.pid, os.WNOHANG)
                if pid:
                    child.returncode = os.waitstatus_to_exitcode(status)
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError("Set benchmark exceeded 300 seconds")
                time.sleep(.005)
        finally:
            if child.returncode is None:
                child.kill()
                child.wait()
    wall = time.monotonic() - started
    assert child.returncode == 0, log.read_text()
    output = log.read_text()
    expected = json.loads((args.output / "fixture.json").read_text())
    pairs, outputs = expected["pairs"], expected["fanout"]
    summary = f"{height} Blocks connected to Height {height}; Set +{height + pairs * (outputs + 2)} -{pairs * (outputs + 1)} Outputs, {pairs * 2_000_000} satoshis in fees"
    assert summary in output, output
    result = {"variant": label, "trial": trial, "wall_seconds": wall,
              "user_seconds": usage.ru_utime, "system_seconds": usage.ru_stime,
              "max_rss_bytes": usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024,
              "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "output": output}
    shutil.rmtree(data)  # Only this invocation's disposable database clone.
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-bin", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--binary", action="append", required=True, help="LABEL=/absolute/binary")
    parser.add_argument("--pairs", type=int, default=8)
    parser.add_argument("--outputs", type=int, default=4000)
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args()
    assert 1 <= args.pairs <= 49 and 1 <= args.outputs <= 10000
    binaries = [(item.split("=", 1)[0], Path(item.split("=", 1)[1]).resolve()) for item in args.binary]
    args.output.mkdir(parents=True, exist_ok=True)
    fixture = args.output / "fixture.json"
    metadata = json.loads(fixture.read_text()) if fixture.exists() else prepare(args, binaries[0][1])
    # Exclude one warmup per binary; alternate subsequent run order.
    for label, binary in binaries:
        measure(args, label, binary, "warmup", metadata["height"])
    results = []
    for trial in range(args.trials):
        for label, binary in (binaries if trial % 2 == 0 else list(reversed(binaries))):
            result = measure(args, label, binary, trial, metadata["height"])
            results.append(result)
            (args.output / "throughput.json").write_text(json.dumps({"fixture": metadata, "runs": results}, indent=2) + "\n")
            print(json.dumps({k: v for k, v in result.items() if k != "output"}), flush=True)


if __name__ == "__main__":
    main()
