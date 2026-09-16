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

## Remaining acceptance work

The existing `wasm/host.mjs` conformance harness still implements the legacy
`tcp_poll` ABI. This branch has not ported its Work/Wait bindings or validated
the migrated application through wasm CI. The native results above do not establish
wasm compatibility. The `.aver-version` pin also still needs moving to a
revision containing the compiler fixes specified above.

Synchronous owner-side database and filesystem operations remain. Moving those
safely requires preserving storage ownership and durability ordering; this
change does not claim to make all I/O asynchronous. Native Work cancellation
still discards a result rather than preempting its computation.

Sustained hostile-peer soak, full #328 catch-up/announcement schedules, and the
complete standing regtest suite have not been run. Full-project VM verification
has not been run either: focused consumer verify cases were run as native tests
(the existing native exporter issue with negative Int expected literals was
avoided by selecting the relevant peer cases). Generic Aver Work tests cover
VM/native replay; this consumer probe covers native replay.
