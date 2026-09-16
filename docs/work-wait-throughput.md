# Work/Wait throughput comparison

The migration serves peer traffic while block calculations run. It does not
make block commits independent: every connection still consumes the Set left
by its predecessor. One lookahead decode now overlaps resolution and connection
of the current block; both results settle before the owner commits in order.
Two job slots bound the lookahead. Existing independent products for UTXO reads
and script audits remain separate from that limit.

The original migration serialized those stages and lost the upstream overlap.
The local comparison below records that regression as well as the fixes.

## Reproduce

For an application comparison, build upstream and this branch with the same
compiler, runtime and Cargo profile. To measure the Aver runtime optimization,
compare the explicitly identified before/after runtime versions in the JSON. Keep copies of the binaries before rebuilding the shared target.

```sh
python3 tools/regtest/throughput.py \
  --core-bin /path/to/bitcoin/bin --output /tmp/btc-throughput \
  --binary upstream=/path/to/upstream --binary migrated=/path/to/migrated
python3 tools/regtest/throughput-peer.py \
  --fixture /tmp/btc-throughput --binary /path/to/migrated --label migrated
python3 tools/regtest/throughput-peer.py \
  --fixture /tmp/btc-throughput --binary /path/to/migrated --label cancelled --cancel
# The trace test additionally needs a binary compiled with --with-replay:
python3 tools/regtest/pipeline-trace.py \
  --seed /tmp/btc-throughput/seed --binary /path/to/replay-enabled-migrated
```

The fixture uses an isolated Bitcoin Core 31.1 regtest node. It mines 150
maturity blocks followed by eight pairs of blocks: a mature coinbase fans out
to 4,000 P2WPKH outputs, then a consolidation spends those outputs. Core's
`generateblock` bypasses mempool policy but validates consensus. There are
166 blocks, 32,008 spent outputs, and 16,000,000 satoshis of fees.

Download headers and bodies once. Each timed Set run starts from a copy of that
closed database, with no UTXO state. One warmup per binary precedes three timed
runs in alternating order. Every run must report exactly the same height,
created/spent output counts and fees. CPU time and peak RSS belong to the child
process; database copying is outside the timed interval. The peer measurement
is separate: a loopback peer sends pings during real catch-up over the same seed.

## Scope

The checked-in JSON report contains binary hashes and all individual results.
These are macOS arm64 runs in the native `iteration` profile (opt-level 1,
no LTO), with warm filesystem caches. They are not release-build or mainnet
measurements. The Set phase does not run scripts; the existing acceptance suite
separately audits script behavior. Short timings are sensitive to scheduling.

Replay-enabled builds and ordinary builds are reported separately. Aver
[PR #1384](https://github.com/jasisz/aver/pull/1384) removes snapshots made while
recording is disabled and avoids a second copy of an owned native Work task.
Both changes are general compiler/runtime optimizations, with their own tests.

## Local measurements

Median of three timed runs after warmup. Replay support is compiled in but recording is off.

| Variant | Wall time | CPU time | Peak RSS |
|---|---:|---:|---:|
| upstream-plain | 1.173 s | 1.316 s | 39.0 MiB |
| pipeline-plain | 1.333 s | 1.569 s | 61.0 MiB |
| owned-plain | 1.317 s | 1.533 s | 53.6 MiB |
| upstream-replay | 1.277 s | 1.439 s | 116.0 MiB |
| serial-replay | 1.762 s | 1.814 s | 187.4 MiB |
| pipeline-replay | 1.519 s | 1.825 s | 186.5 MiB |
| lazy-replay | 1.409 s | 1.657 s | 61.1 MiB |
| owned-replay | 1.388 s | 1.617 s | 53.3 MiB |

`pipeline` restores the lookahead, `lazy` additionally removes idle replay snapshots, and `owned` additionally transfers native Work arguments without the extra copy. `plain` omits replay support. These changes reduce overhead; the final plain build still takes about 12% longer than upstream in this fixture. It should not be presented as a throughput improvement over upstream.

## Release follow-up and alternative transfer

All three variants below use the same Aver compiler/runtime commit `f6e8197d`,
Rust release profile (opt-level 3, LTO, one codegen unit), dependencies and
fixture. Seven timed trials per variant follow one warmup, with alternating
order. The second window first connects the 150 maturity blocks outside the
measurement, then times only the 16 transaction-heavy blocks. No Cargo build
runs alongside these measurements.

| Heights | Variant | Wall median | CPU median | Peak RSS median |
|---|---|---:|---:|---:|
| 1–166 | upstream-release | 0.889 s | 0.983 s | 38.1 MiB |
| 1–166 | migrated-release | 0.999 s | 1.157 s | 54.6 MiB |
| 1–166 | native-transfer-experiment | 0.892 s | 1.014 s | 39.2 MiB |
| 151–166 | upstream-release | 0.872 s | 0.961 s | 38.4 MiB |
| 151–166 | migrated-release | 0.982 s | 1.129 s | 54.2 MiB |
| 151–166 | native-transfer-experiment | 0.869 s | 0.982 s | 38.8 MiB |

Release preserves the regression: the migrated program takes about 12–13%
longer than upstream. The isolated native-transfer experiment removes almost
all of the elapsed-time gap in both windows. Its wall time is within the observed
run-to-run spread of upstream; this is not evidence that it is faster. CPU time
still exceeds upstream slightly. The full-run wall ranges are 0.880–0.896 s for
upstream and 0.888–0.909 s for the experiment.

The experiment skips the Work task/reply `ProviderValue` tree conversions,
keeping the same worker threads, limits, readiness and domain algorithms. It
also answers loopback pings during catch-up. This supports implementing a typed
native Work transfer path in Aver instead of merely tuning the current codecs
or adding more threads. It is **not included in the production compiler or
application** and has not passed the complete acceptance/replay matrix.

[Raw release measurements](work-wait-release-throughput.json) include every
trial, binary hashes and the separate peer probe. The
[experiment and reproduction patch](work-native-transfer-experiment.md) explain
its scope and the requirements before adopting it as a general optimization.
