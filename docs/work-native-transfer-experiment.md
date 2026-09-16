# Native Work transfer experiment

This is a diagnostic experiment, not a production optimization. It changes the
generated Rust of the migrated listener, not its Aver source or the compiler.
The accompanying patch is deliberately outside the build: ordinary builds and
CI do not apply it.

The current native Work path converts the task to a `ProviderValue` tree, decodes
that tree in the worker, encodes the reply to another tree, then decodes it in
the owner. The experiment instead transfers the already typed task and reply
through private host containers. It keeps the existing Work provider, job engine,
job-kind ownership check, two-job limit, readiness, cancellation, pure algorithms
and domain/contract adapters. It measures the cost of those four conversions.

This patch is intentionally specific to the tested program. It does not support
replacement providers or replay and must not be adopted as the general API.
A production implementation needs a compiler-owned native Work path, preservation
of contract checks and job-kind ownership, a codec fallback for replacement
providers, and parity tests for recording/replay, cancellation and wrong-kind
handles. Transported data must remain invisible as a resource in the language.

## Reproduce the experiment

Use Aver commit `f6e8197d6f2c64cbdfc4b61f97e957fc649ea284` and the listener
application tree at `663cf84e2bd68921d93bf15d1d1848f1fdcb3def`. Configure local
provider paths as described in the migration guide. Generate ordinary Rust
without `--with-replay` in two separate directories. Apply
`work-native-transfer-experiment.patch` using `git apply` from the experimental
generated project directory; keep the other project untouched.

Build both with `cargo build --offline --release` from their own directories,
using the same Cargo target cache and lockfile. Save each binary before building
the next project. The profile is opt-level 3, LTO enabled, one codegen unit.
Compare them with `tools/regtest/throughput.py`; `--warm-prefix 150` excludes the
maturity blocks from the measured interval. Every run checks exact block/output
counts and fees. These are local warm-cache Set measurements; they do not run
script validation and do not establish mainnet throughput.

## Profile evidence

A separate macOS `sample` capture of the migrated release binary (1 second,
1 ms interval) contains Work reply/task `ProviderCodec` list traversals,
`BTreeMap<String, ProviderValue>` field lookups, allocation and memory copies.
The many sleeping RocksDB/thread-pool stacks are not CPU-cost percentages.
The controlled experiment, rather than the sample counts alone, measures how
much removing the Work conversion changes elapsed time.

## Result and recommendation

Across seven alternating trials, full-run medians were 0.889 s upstream,
0.999 s migrated, and 0.892 s for this experiment. The separate large-block
window confirms the same pattern; see the release table in
[the throughput report](work-wait-throughput.md#release-follow-up-and-alternative-transfer).
This justifies a general native Work transfer optimization with the parity
requirements above. A thread-pool change was not needed to recover the gap in
this fixture; pooling and larger task batches remain separate hypotheses.
