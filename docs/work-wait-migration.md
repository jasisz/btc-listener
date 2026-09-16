# Work/Wait migration acceptance

This branch moves block decoding and pure UTXO connection onto bounded typed Work
jobs. A single lookahead decodes Block N+1 while the owner resolves inputs
and a second job connects Block N. The CLI owner resolves database inputs, serves peers while the
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

No block context can be replaced while this owner is awaiting these jobs. Both jobs settle before the owner
commits Block N; every error or stop cancels the retained lookahead. The next
height is never read past the requested target.
Cancellation discards the answer and returns through the normal path that
flushes earlier completed work. Generated Rust detaches a cancelled computation;
its thread is not preempted and retains its job slot until it finishes. This
branch does not claim that cancellation terminates native computation.

## Compiler requirement and build

The `.aver-version` pin is `f6e8197d6f2c64cbdfc4b61f97e957fc649ea284`,
containing merged Aver PRs [#1382](https://github.com/jasisz/aver/pull/1382)
and [#1383](https://github.com/jasisz/aver/pull/1383). The first fixes nested
capability wrapper identifiers, composed-program binding checks and native
replay of records containing Bytes-keyed maps. The second lets the Work host
preserve an explicitly supplied JSPI `wait_poll` import. The pin also includes
[Aver PR #1384](https://github.com/jasisz/aver/pull/1384), pending merge, which
avoids unused replay snapshots and a redundant native Work task copy.
See the [throughput comparison](work-wait-throughput.md) for the remaining cost.

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

The compiler picks up `[work] max-jobs = 2` and the `Infra.BlockJobs` binding
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

## Remaining network waits removed

Four groups of network stalls remained after the first Work slice: inline
inbound/outbound greetings, peer writes, dashboard readers/writes, and DNS.
The six blocking TCP call sites have been removed. The application contains no
calls to Tcp.connect, Tcp.readBytes, Tcp.readSome, or Tcp.writeBytes.

- Active inbound and outbound greetings are pool-owned state. Admission reserves
  the key immediately; peerKeys and normal frame delivery expose a peer only
  after verack. One ten-second deadline is retained across partial frames and
  pings. At most four buffered greeting frames per peer are processed per
  maintenance pass. Pre-verack traffic has the existing eight-frame cap and is
  released in order only after a successful greeting. Failures discard its
  pending state and close only that peer. An outbound greeting remains part of
  the one-at-a-time dial budget; completion does not clear another dial.
- Framed peer messages enter a FIFO bounded to 8 MiB and 256 messages per peer.
  Each flush writes at most 64 KiB, accounts actual bytes, retains the exact
  offset, and watches write readiness only while bytes remain. Overflow or
  delayed write failure drops the offending peer. Closing a peer removes its
  outbox, greeting and deferred input. These bounds intentionally refuse a
  client that accumulates too much output.
- The board retains up to sixteen clients and accepts at most four per turn.
  Each client has a bounded 4 KiB request, a response offset and a single
  five-second lifetime. Partial headers and responses survive across turns.
  The latest Board travels back from catch-up Eyes; graceful shutdown releases
  retained readers as well as the listener. Pending board readers get short
  follow turns and enter the Work owner's combined wait on disjoint keys.
- DNS uses beginConnect/dialled/readNow/writeNow with a single fifteen-second
  deadline covering connect, write, prefix and response. Stop is checked between
  waits of at most 100 ms. Seed discovery remains a sequential facade used at
  startup or when no peers/candidates remain; it does not run a dashboard turn
  during the lookup. Startup joined/handshake APIs likewise remain synchronous
  facades, with bounded waits and cooperative stop checks.
- The follow loop checks for its first ready peer again after a read turn,
  because an asynchronous greeting can complete there and require catch-up.

Reproduce the production adapter acceptance tests:

```sh
aver compile tools/network_probe.av --module-root . --target rust -o /tmp/btc-network-probe
cargo build --manifest-path /tmp/btc-network-probe/Cargo.toml --profile iteration
python3 tools/regtest/network-turns.py /tmp/btc-network-probe/target/iteration/network_probe

aver compile tools/resolver_probe.av --module-root . --target rust -o /tmp/btc-resolver-probe
cargo build --manifest-path /tmp/btc-resolver-probe/Cargo.toml --profile iteration
python3 tools/regtest/resolver-turns.py /tmp/btc-resolver-probe/target/iteration/resolver_probe
```

Loopback observations on macOS arm64, iteration profile, 2026-09-16:

- Silent inbound and outbound greetings: other-peer pong 23–30 ms (including
  the harness's deliberate 20 ms fragmentation gap); owner stop 11–23 ms.
- Receiver backpressure: another peer's pong 25–30 ms, then exact delivery of
  4,000,000 payload bytes and the following three-byte marker with both
  checksums and order verified. A fragmented HTTP request received a complete
  response matching Content-Length.
- A greeting sending periodic pings but no verack expired after 10.014 seconds;
  a duplicate version dropped only its sender.
- Fragmented DNS answered correctly; mid-body EOF failed without hanging.
  SIGINT during a partial prefix returned in 45 ms. A one-byte prefix with no
  remainder expired after 15.101 seconds.
- Full node against Core: new live blocks 169 → 171 and final build 171 → 173, matching tip hash, all
  twenty audit scripts passed. The earlier silent-inbound shutdown reproduction
  improved from 9.771 seconds to 0.039 seconds. The final build also served a
  complete 1,036-byte HTTP response during a silent greeting, then exited
  zero 75 ms after SIGINT.
- Check: 119/119 reachable modules. Focused native verify: 26 peer ownership/
  queue cases and 37 resolver cases; Outbox VM verify: 9/9 cases.
- Work regression: a 20,000-transaction decode still answered pong before
  cancellation and returned cancellation in 100.17 ms. Native record/replay
  with 10,000 transactions retained the announcement and passed.

These are executable checks and individual latency observations, not universal
proofs or performance guarantees. They cover the network adapters using real
loopback sockets and the existing production Work owner.

## Node wasm acceptance

`wasm/host.mjs` now supplies `Wait.poll`, `Tcp.readNow` and `Tcp.writeNow`.
The Work adapter is copied unchanged from the pinned Aver source under
`wasm/aver-work/`; CI checks its bytes against that source. Socket wait
subscriptions are removed when a job wins the wait. Drain events resume
backpressured writers, and SIGINT/SIGTERM stop the Work owner.

The production Work probe decodes 2,000 synthetic transactions in a Node worker
while the main instance handles a fragmented ping. Delivery and cancellation
both preserve the deferred inv; pong must precede the result, and cancellation
must not deliver a decoded result. The full CLI separately rejects a corrupt
frame and exits promptly after its final peer disappears. A real Core regtest
run synced through height 175 and stopped cooperatively.

The host uses temporary Disk and in-memory KV. These tests establish this
application's Work/Wait path in Node, not durable deployment parity with native
RocksDB. Native cancellation still discards an answer without preempting the
running computation.

## Repeatable native acceptance

`tools/regtest/suite.py` automates fresh isolated Core nodes, the four script
types, reorganisations and undo, relay, compact reconstruction, inbound sync,
catch-up announcements, hostile peers, the terminal Screen and storage guards.
It keeps a JSON report and logs after cleaning up its processes. CI consumes
the existing native build artifact and runs this suite against pinned Core 31.1.
See [the standing regtest guide](regtest-testing.md) for invocation and scope.

This run caught two application issues: an empty peer pool waited for a full
timeout, and a valid compact response arriving before its separate Header was
needlessly fetched whole. The owner now rejects an empty pool immediately.
A compact response first checks the requested Id and body, then sends its
carried Header through the ordinary proof-of-work/parent/target/timestamp
pipeline and requires a placement before writing the body.

Full VM verification now passes for the main program: 10,058 examples passed,
zero failed, 464 skipped by their `when` guards. The Core-corpus graph also
passes: 12,621 examples, zero failed, 464 guarded skips. These totals overlap
because both graphs include shared modules; they must not be added together.

Synchronous owner-side database and filesystem operations remain. Moving those
requires preserving storage ownership and durability ordering. These executable
checks are not universal proofs or latency guarantees.

Static checks use `python3 tools/check-projects.py`: the production project
and the historical `tools/concurrency` experiment have distinct module roots.
Every nested project is checked separately, rather than treating its short
imports as production imports.

The separate production idle-deadline test passed after 1,200.413 seconds: a
quiet inbound was closed, its owner stayed alive, and SIGINT then stopped the
listener cleanly. Both provider suites pass (8 primitives and 23 KV cases).

The final fresh-node run passed all 24 automated scenarios in one run.
[Machine-readable acceptance results](work-wait-acceptance.json) record the
Aver pin, exact tested binary hash, platform, counts and separate idle deadline.
Consumer GitHub CI ran on the published draft. Its Lean proof gate failed
(9 build errors, 12 sorries); Core acceptance also hit a fixed-window PTY
redraw assertion. These failures are under investigation; see PR #361 for
the current check status. Aver PRs #1382 and #1383 passed their remote checks
before merging.
