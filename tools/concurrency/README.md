> Historical first probe. The branch now also migrates the production block
> owner to Work/Wait. See [the migration results](../../docs/work-wait-migration.md)
> for current behavior, tests and remaining blocking paths. Claims below about
> unchanged production follow describe this earlier experiment only.

# Consumer concurrency probe

This experiment runs btc-listener's unchanged `Domain.BlockWork.preparedAcross`
on a generated Work worker while another process answers a Bitcoin regtest
wire-format ping through nonblocking sockets. `domain` is a relative symlink to
the real consumer modules, not a copy. The normal entry point and `follow` are
unchanged.

The job repeats preparation of the genesis block 50,000 times. Each result must
contain one transaction and the expected identity, Merkle, parent and work
findings. It returns the verified count to the coordinator. This is a repeatable
responsiveness probe, not a mainnet throughput benchmark or signature-validation
benchmark. The crypto provider is linked, but genesis preparation does not
exercise its signature operations. The job boundary currently carries an Int
task and an Int result; transporting the production block/result records is
still part of the migration.

The socket adapter accepts one fixed ping, including its checksum and nonce,
and creates the pong using the consumer's `Domain.Message.frame`. It is not a
Bitcoin handshake or a complete node. The Python client deliberately splits
the request and checks the complete response. It fails unless the pong arrives
before the worker result, all 50,000 preparations succeed, and both processes
finish normally. There is no database or public network access.

## Running

Use Aver main at `3f21a8783c19ad021f06d87fe59efab23b0991bc` or a compatible later
revision. The consumer's pinned older compiler does not provide this experiment's
coordinator features. As in the consumer's normal build, point
`providers/primitives/Cargo.toml`'s `aver-rt` path to the **same checkout** as the
compiler's generated Cargo.toml uses. The upstream provider manifest contains
the author's absolute local path; adapting that path is a local build setting.

From this directory:

```sh
aver compile main.av --target rust -o /tmp/btc-concurrency-rust
cargo build --manifest-path /tmp/btc-concurrency-rust/Cargo.toml --profile iteration
python3 check.py /tmp/btc-concurrency-rust/target/iteration/main
```

An existing `CARGO_TARGET_DIR` can be reused; point the last command at that
target's `iteration/main`. `check.py --rounds 1 <binary>` runs one trial. Port
numbers are selected dynamically and every child process is cleaned up.

## Measured on 2026-09-16

Consumer main: `b00a976c91092c10e958fccf2e71fb7608c01e2b`.
Aver main: `3f21a8783c19ad021f06d87fe59efab23b0991bc`.
macOS, generated Rust, iteration profile, one Work job, three runs:

| Run | Pong latency | Worker result from start | Worker time remaining after pong | Pulses served |
| --- | ---: | ---: | ---: | ---: |
| 1 | 18.93 ms | 3362.71 ms | 2911.07 ms | 243 |
| 2 | 18.03 ms | 2937.94 ms | 2894.88 ms | 242 |
| 3 | 19.27 ms | 2931.54 ms | 2886.78 ms | 240 |

Latency includes an intentional 15 ms pause between request fragments.
These are observations on this machine, not a latency guarantee. Current Aver
also type-checked all 114 modules reachable from the consumer's normal main.
The final probe passes `aver check` for all 22 reachable modules and 21 emitted
Rust verify cases (16 preparation/state cases and 5 framing/port cases).
These are executable examples, not universal proof credit.

## What this establishes, and what remains

The runtime can keep serving sockets while this real consumer computation runs.
Upgrading Aver alone does not migrate `follow`: it still uses synchronous calls,
independent products with a join, and manual peer servicing between chunks.
Recent proof PRs broaden guarantees for generated protocols; they do not
automatically move existing consumer computations to workers.

Next, migrate a bounded production path: gather immutable block input, enqueue
preparation, keep serving peers, accept the result against its block/context,
and commit state on the owning process. Carry production task/result records,
then cover stale results, errors, shutdown, replay and committed snapshots.
Keep database writes out of the pure job. Run the standing Bitcoin Core regtest
suite, including a block announcement arriving during catch-up (#328), before
proposing a production change.

This is an uncommitted experimental slice. The full Core regtest suite was not
run; no change to production `follow`, universal proof claim, or completed epic
acceptance is implied.
