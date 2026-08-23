# Local model benchmarks

Which local models are usable, on which machine, and why. This file is checked in
and is the record; `util/bench_model.sh` produces the numbers so a run on one
machine means the same thing as a run on another. Append to the results table
when you test a model; never delete a row, because a model that failed on a
machine is exactly what stops us trying it again.

The one rule this exists to enforce: **pick the model to fit the machine's memory
first, and only then judge its speed and its answers.**


## Hardware factors, in the order they bite

Memory capacity is primary, but it is not the only constraint, and the second one
is not CPU. On Apple Silicon (unified memory, MLX/Metal inference):

1. **RAM capacity -- the fit-or-swap cliff.** The model's weights, its KV cache
   for the context length in use, and the OS and app all live in the same unified
   memory. If they do not fit, macOS swaps to SSD and generation throughput falls
   off a cliff -- measured on the M4 Air, the 35B drove the machine into 16.8 GB
   of swap and about 5 tokens/s, which is unusable live. This is why memory is the
   first filter: a model that does not fit is not slow, it is unusable.
2. **Memory bandwidth -- the real analog to "not enough CPU".** Token generation
   is memory-bandwidth-bound, not compute-bound: each token streams the active
   weights from RAM, so GB/s sets the ceiling on tokens/s. The base M4 has roughly
   a third of the M3 Max's bandwidth, so even a model that fits will decode two to
   three times slower on the Air. This, not CPU, is what silently makes a model
   too slow.
3. **GPU cores / compute -- prefill and time to first token.** Processing the
   prompt (a long transcript) before the first token is compute-bound and scales
   with GPU cores. Fewer GPU cores means a longer wait for the first token on a
   long EXPLAIN, even when steady-state generation is fine.
4. **CPU cores -- rarely the bottleneck.** For MLX GPU inference the CPU mostly
   orchestrates; raw CPU count seldom limits throughput. So the answer to "could
   insufficient CPU pose an analogous problem" is: not really -- bandwidth (2) and
   GPU prefill (3) are the analogous problems, not CPU.
5. **Thermals -- sustained load on a fanless machine.** The Air has no fan and
   throttles under sustained inference. A benchmark is a sprint; a meeting is a
   marathon, so a model that benchmarks well can still degrade over a long session.
   Note sustained behaviour, not just the first answer.

A Mixture-of-Experts model such as Qwen3.6-35B-A3B is a special case worth
understanding: its *memory footprint* is the full 35B (all experts must be
resident), but its *bandwidth and compute per token* are only the ~3B active
parameters. That is why it generates fast where it fits, and why it is a poor fit
for a 32 GB box despite being fast on a 96 GB one: the footprint, not the speed,
is the problem. A smaller MoE, or a lower-bit quant that shrinks the footprint,
keeps the low-active-parameter speed while fitting -- which is the first thing to
try on the Air.


## Machines

Bandwidth, GPU and CPU core counts below are approximate and per Apple's specs;
correct a machine's own row from `system_profiler SPHardwareDataType` /
`SPDisplaysDataType` when you benchmark on it.

| host | chip        | RAM   | mem bandwidth | GPU cores | CPU cores      | cooling |
|------|-------------|-------|---------------|-----------|----------------|---------|
| m3   | M3 Max      | 96 GB | ~300-400 GB/s | 30-40     | 14-16 (P+E)    | active fan |
| m4   | M4 (base)   | 32 GB | ~120 GB/s     | ~10       | ~10 (4P+6E)    | fanless (Air) |


## How a row is measured

Load the model in LM Studio (or `lms load ...`), then:

    util/bench_model.sh <served-model-id>          # production config
    util/bench_model.sh <served-model-id> --raw    # thinking left on, to show the failure

It sends three fixed prompts -- the real EXPLAIN, CLARIFY and RECALL framings the
assistant sends -- and reports, per prompt:

- **fit (swap delta)**: MB of swap that appeared during the run. ~0 means it fit;
  a large jump means it was swapping and the speed numbers are meaningless.
- **ttft_s**: seconds to the first answer token (prefill; the first case of a run
  also pays one-time warmup, so read it as an upper bound).
- **wall_s**: seconds to the finished answer.
- **decode_tok/s**: generation throughput after the first token.
- **reason_tk**: reasoning tokens spent. With suppression this should be 0; if it
  is not, the model is thinking away its budget (see doc/phase* and config.py).
- **finish**: `stop` is a complete answer; `length` means it hit the token cap.
- **verdict**: a human call -- is this usable live on this machine?

Judge the answers by eye too; the tool prints them in full. Speed with a wrong or
empty answer is not usable.


## Results

date       | machine | model                     | quant | case    | fit (swapΔ) | ttft_s | wall_s | decode_tok/s | reason_tk | finish | verdict
-----------|---------|---------------------------|-------|---------|-------------|--------|--------|--------------|-----------|--------|--------
2026-08-22 | m3      | Qwen3.6-35B-A3B (qwen3.6-35b-a3b) | 4-bit MLX | EXPLAIN | 0 MB | 6.1 | 13.3 | 88.5  | 0 | stop | usable
2026-08-22 | m3      | Qwen3.6-35B-A3B (qwen3.6-35b-a3b) | 4-bit MLX | CLARIFY | 0 MB | 0.4 | 2.7  | 89.7  | 0 | stop | usable
2026-08-22 | m3      | Qwen3.6-35B-A3B (qwen3.6-35b-a3b) | 4-bit MLX | RECALL  | 0 MB | 0.4 | 0.5  | 110.0 | 0 | stop | usable

Notes on the m3 / Qwen3.6-35B-A3B run (2026-08-22):

- Fit comfortably on 96 GB with no swap; ~20 GB footprint at 4-bit. Decode held
  ~88-110 tok/s, the A3B's 3B-active profile. EXPLAIN's 6.1s ttft is the first
  case of the process and includes one-time warmup; CLARIFY and RECALL, warm, show
  the true ~0.4s prefill for these prompt sizes.
- Reasoning suppression (the empty-<think> prefill, config.reasoning_prefill) was
  on, as in production: 0 reasoning tokens in every case. With `--raw` on the same
  machine the EXPLAIN prompt instead spent all 1023/1024 tokens on reasoning and
  returned an empty answer (finish=length) -- the failure this suppression fixes.
- Answer quality was good for EXPLAIN and RECALL. CLARIFY originally rambled: with
  the thinking channel closed, its "weigh the plausible readings" instruction leaked
  the deliberation into the visible answer (an early run took ~29s). Its prompt was
  then tightened to a decisive, fixed three-part format, and verified terse over five
  runs on m3 (0.7-1.6s, 210-340 chars each). If a future model rambles on CLARIFY or
  any prompt, it is fighting the closed thinking channel; make the prompt more
  decisive rather than blaming the model.
- Not yet benchmarked on m4: this model is expected to fail the memory filter
  there (see the M4 Air swap measurement above). That is the point of the m4 task.


## A more elaborate scheme, if we want one

This markdown table is enough for a handful of models on two machines, and it is
human-reviewable, which the flat file buys us. If the matrix grows and we want it
machine-readable, the natural next step -- and one that matches how this repo
already treats transcripts -- is a canonical `doc/model_benchmarks.jsonl` (one
object per run, appended, never rewritten) with this table rendered from it, and
`bench_model.sh` appending a line on each run. That is a deliberate step to take
together, not a default; raise it when the table starts to hurt.
