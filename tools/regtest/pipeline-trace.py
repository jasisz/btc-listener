#!/usr/bin/env python3
"""Check actual two-job ordering and replay on three blocks of a downloaded seed.

Requires a --with-replay binary and a closed database containing bodies 1..3
whose UTXO walk has not started. The source database is only copied.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def check(events):
    active, peak, kinds, connected = set(), 0, {}, []
    for event in events:
        operation = event["type"]
        value = event["outcome"].get("value", {})
        if operation == "Infra.BlockJobs.begin":
            handle = value["$ok"]["$capabilityResource"]["trace"]
            assert handle not in active
            active.add(handle)
            peak = max(peak, len(active))
            task = event["args"][0]["$variant"]
            kind = task["name"]
            kinds[kind] = kinds.get(kind, 0) + 1
            if kind == "Connect":
                connected.append(task["fields"][1])
        elif operation == "Infra.BlockJobs.take" and "$some" in value.get("$ok", {}):
            active.remove(event["args"][0]["$capabilityResource"]["trace"])
        elif operation == "Work.cancel":
            active.discard(event["args"][0]["$capabilityResource"]["trace"])
    assert peak == 2, ("lookahead must overlap, within two handles", peak)
    assert not active, ("jobs survived the owner step", active)
    assert kinds == {"Decode": 3, "Connect": 3}, kinds
    assert connected == [1, 2, 3], connected
    return {"peak_outstanding_handles": peak, "jobs": kinds, "connect_heights": connected}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--seed", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="btc-pipeline-trace-") as directory:
        root = Path(directory)
        shutil.copytree(args.seed, root / "chain")
        session = root / "session.json"
        env = {k: v for k, v in os.environ.items() if k not in ("AVER_REPLAY_RECORD", "AVER_REPLAY_REPLAY")}
        recorded = subprocess.run([str(args.binary.resolve()), "regtest", "utxo", str(root / "chain"), "3"],
            env={**env, "AVER_REPLAY_RECORD": str(session)}, capture_output=True, text=True, timeout=60)
        assert recorded.returncode == 0, recorded.stdout + recorded.stderr
        result = check(json.loads(session.read_text())["effects"])
        shutil.rmtree(root / "chain")
        replayed = subprocess.run([str(args.binary.resolve())],
            env={**env, "AVER_REPLAY_REPLAY": str(session)}, capture_output=True, text=True, timeout=60)
        assert replayed.returncode == 0, replayed.stdout + replayed.stderr
        result["replay_without_database"] = True
        print(json.dumps(result))
