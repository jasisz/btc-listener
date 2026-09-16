# Work/Wait migration acceptance

This branch moves block decoding and pure UTXO connection onto one typed Work
job at a time. The CLI owner resolves database inputs, serves peers while the
job runs, and alone applies and persists its result. The exact existing
`Domain.Block.transactionsOf` and `Domain.Connect.connected` algorithms run
behind capability-owned Task/Reply adapters. A worker owns no database or socket.
This is the explicit CLI owner using Work/Wait; it is not yet a generated
`[run]` / yielding-process rewrite.

All eight `Tcp.poll` calls on the application path now use `Wait.poll`.
Peer and dashboard readiness reads use `readNow`. A Work wait watches the job,
connected sockets, listener and pending dial, with a 100 ms stop-check deadline.
A read tick processes one batch even when it contains only an incomplete frame.
A ping is answered while Work is pending; other frames remain deferred. Address
gossip is folded into the Book, and the remaining deferred messages now pass
through the ordinary dispatcher, oldest first, rather than being discarded.
The existing queue still has its 64-message overflow policy.

No block context can be replaced while this owner is awaiting its one job.
Cancellation discards the answer and returns through the normal path that
flushes earlier completed work. Generated Rust detaches a cancelled computation;
its thread is not preempted and retains its job slot until it finishes. This
branch does not claim that cancellation terminates native computation.

## Compiler requirement and build

Use Aver PR [#1382](https://github.com/jasisz/aver/pull/1382), tested at
`14b8ff6d`, or a revision containing it. The compiler fixes nested capability
wrapper identifiers, composed-program binding checks, and native replay of
records containing Bytes-keyed maps. Current Aver main without the PR does not
build this consumer slice correctly.

The upstream provider Cargo manifests already contain the author's absolute
local aver-rt path. For a local build, set both `providers/primitives/Cargo.toml`
and `providers/kv/Cargo.toml` to the aver-rt directory of the **same checkout**
used by the compiler. Those machine-specific overrides are not part of this
migration commit. Reuse a Cargo target directory when disk space is limited.

```sh
aver check main.av
aver compile main.av --target rust --with-replay -o /tmp/btc-work-rust
cargo build --manifest-path /tmp/btc-work-rust/Cargo.toml --profile iteration
```

The compiler picks up `[work] max-jobs = 1` and the `Infra.BlockJobs` binding
from aver.toml. The normal CLI and data format are unchanged.

## Production owner responsiveness probe

This entry uses the actual `Infra.Working`, `Infra.Peers`, `Infra.Tending` and
`Domain.BlockWorkJob` implementation, with a local Bitcoin wire peer. It supplies
a synthetic payload containing repeated genesis transactions to keep the decoder
busy. It is not a consensus-valid block, a throughput benchmark, or an end-to-end
chain acceptance test. No database is opened by this probe.

```sh
aver compile tools/working_probe.av --module-root . --target rust --with-replay -o /tmp/btc-working-probe
cargo build --manifest-path /tmp/btc-working-probe/Cargo.toml --profile iteration
python3 tools/regtest/work-wait.py /tmp/btc-working-probe/target/iteration/working_probe
python3 tools/regtest/work-wait.py /tmp/btc-working-probe/target/iteration/working_probe --mode cancel
python3 tools/regtest/work-wait.py /tmp/btc-working-probe/target/iteration/working_probe --count 10000 --mode record
```

The peer fragments a checksummed ping with a 20 ms gap and sends an inv. The
harness checks the nonce/checksum, requires pong before the worker result,
checks the complete transaction count and retained announcement, then closes
all processes and sockets. Cancel mode requires no delivered result and a
prompt owner return. Record mode replays after the real peer has gone away.
Use the corresponding target path if CARGO_TARGET_DIR is set.

Observed on macOS arm64, iteration profile, 2026-09-16:

| Trial | Transactions | Pong latency | Owner result from start | Cancellation result |
| --- | ---: | ---: | ---: | ---: |
| Delivery | 20,000 | 30.69 ms | 941.13 ms | — |
| SIGINT during work | 20,000 | 31.19 ms | 131.35 ms | 100.13 ms after SIGINT |
| Native recording/replay | 10,000 | 110.87 ms | 593.33 ms | — |

These individual observations establish progress while this workload runs;
they are not latency guarantees or evidence of increased validation throughput.

## Bitcoin Core regtest results

The actual compiled node was also tested against official Bitcoin Core 31.1.0
on loopback with no public peers:

- Initial headers/bodies/txindex/outputs/UTXO/audit: 160 blocks, 180 transactions,
  20 spends resolved, all 20 scripts passed, none failed or undecided.
- Invalidate height 156 and mine a replacement: disconnect five blocks, connect
  twelve, reach 167, audit 187 transactions and all 20 scripts, compare hashes at
  156 and 167 with Core, then stop cooperatively.
- Final source with deferred dispatch and bounded partial reads: follow from 167,
  receive two new live blocks, reach 169, compare the tip hash with Core, audit
  all 20 scripts again, exit zero on SIGINT.
- `aver check main.av`: all 118 modules pass. The worker adapters have 34 passing
  native verify examples; 17 focused native cases cover deferred dispatch and
  partial-read ticks. These are executable checks, not universal proofs.

## Remaining acceptance work

This is a working migration slice, not a claim that every operation cooperates.
A silent inbound handshake still runs inline: a real-node test measured
**9.771 seconds** from SIGINT to exit while that handshake was pending. The
existing ten-second handshake deadline explains the delay. Outbound handshakes,
`Tcp.writeBytes` backpressure, synchronous owner-side database I/O and the
board's per-reader wait are also still blocking paths. They need separate
stateful nonblocking adapters; moving computation to Work does not fix them.

Next acceptance work is loop-driven inbound/outbound handshakes, bounded partial
writes, board readers retained across turns, and sustained hostile-peer tests.
Also exercise overflow and the full #328 catch-up/announcement scenario against
Core; the focused deferred-queue cases and the live two-block smoke do not cover
every such schedule. Full-project VM verification and the complete standing
regtest suite have not been run on this branch. Existing generic Work tests
cover VM/native replay; the consumer probe currently covers native replay only.
