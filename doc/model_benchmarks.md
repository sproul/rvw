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
| m4   | M4 (base), MacBook Air, Mac16,13 | 32 GB | 120 GB/s | 10 | 10 (4P+6E) | fanless (Air) |

m4's row is measured, not approximate: `system_profiler SPHardwareDataType` reports
Apple M4, 32 GB, 10 cores (4 performance and 6 efficiency), and `SPDisplaysDataType`
reports 10 GPU cores. 120 GB/s is Apple's figure for the base M4, which has no
per-machine readout; everything else here is this machine's own number.


## How a row is measured

Load the model in LM Studio (or `lms load ...`), then:

    util/bench_model.sh <served-model-id>          # production config
    util/bench_model.sh <served-model-id> --raw    # thinking left on, to show the failure

It sends three fixed prompts -- the real EXPLAIN, CLARIFY and RECALL framings the
assistant sends -- and reports, per prompt:

- **fit (swap delta)**: MB of swap that appeared during the run, and the resident
  size the model actually took (`lms ps`, which reports weights plus the KV cache
  for the loaded context). ~0 MB of growth means it fit; a large jump means it was
  swapping and the speed numbers are meaningless.

  Read the swap delta with care on a machine that is *already* swapping. On m4 the
  ordinary desktop load (browsers, editors) sits on ~10 GB of swap before any model
  is loaded, and macOS neither drains that quickly nor grows it further once it is
  established -- the 35B's run below shows a *negative* delta for exactly that
  reason, and it was not a pass. So record the resident size and the free-memory
  percentage (`memory_pressure`) beside the delta, and treat the three together as
  the fit verdict.
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
2026-08-22 | m4      | Qwen3.6-35B-A3B (qwen3.6-35b-a3b) | 4-bit MLX | EXPLAIN | -4709 MB (see note) / 20.4 GB res | 23.9 | 31.9 | 41.9 | 0 | stop | not usable
2026-08-22 | m4      | Qwen3.6-35B-A3B (qwen3.6-35b-a3b) | 4-bit MLX | CLARIFY | -4709 MB (see note) / 20.4 GB res | 1.7 | 3.3 | 43.4 | 0 | stop | not usable
2026-08-22 | m4      | Qwen3.6-35B-A3B (qwen3.6-35b-a3b) | 4-bit MLX | RECALL  | -4709 MB (see note) / 20.4 GB res | 1.1 | 1.4 | 53.1 | 0 | stop | not usable
2026-08-22 | m4      | Qwen3.6-35B-A3B (qwen3.6-35b-a3b-mlx, andrevp) | 3-bit MLX | EXPLAIN | +122 MB / 14.2 GB res | 4.0 | 13.0 | 42.4 | 0 | stop | usable
2026-08-22 | m4      | Qwen3.6-35B-A3B (qwen3.6-35b-a3b-mlx, andrevp) | 3-bit MLX | CLARIFY | +122 MB / 14.2 GB res | 1.5 | 3.3 | 45.5 | 0 | stop | usable
2026-08-22 | m4      | Qwen3.6-35B-A3B (qwen3.6-35b-a3b-mlx, andrevp) | 3-bit MLX | RECALL  | +122 MB / 14.2 GB res | 1.2 | 1.5 | 56.9 | 0 | stop | usable
2026-08-22 | m4      | Ling-mini-2.0 16B-A1.4B (ling-mini-2.0) | 4-bit MLX | EXPLAIN | 0 MB / 8.5 GB res | 1.0 | 5.2 | 110.4 | 0 | stop | usable, answers untidy
2026-08-22 | m4      | Ling-mini-2.0 16B-A1.4B (ling-mini-2.0) | 4-bit MLX | CLARIFY | 0 MB / 8.5 GB res | 0.6 | 1.9 | 114.8 | 0 | stop | usable, answers untidy
2026-08-22 | m4      | Ling-mini-2.0 16B-A1.4B (ling-mini-2.0) | 4-bit MLX | RECALL  | 0 MB / 8.5 GB res | 0.6 | 1.0 | 136.0 | 0 | stop | usable, answers untidy
2026-08-22 | m4      | Qwen3.5-9B (qwen3.5-9b-mlx) | 4-bit MLX | EXPLAIN | 0 MB / 5.6 GB res | 1.8 | 25.1 | 19.4 | 0 | stop | fits, too slow
2026-08-22 | m4      | Qwen3.5-9B (qwen3.5-9b-mlx) | 4-bit MLX | CLARIFY | 0 MB / 5.6 GB res | 2.5 | 6.8  | 20.9 | 0 | stop | fits, too slow
2026-08-22 | m4      | Qwen3.5-9B (qwen3.5-9b-mlx) | 4-bit MLX | RECALL  | 0 MB / 5.6 GB res | 1.8 | 2.4  | 26.2 | 0 | stop | fits, too slow
2026-08-22 | m4      | Qwen3.5-4B (qwen3.5-4b-mlx) | 4-bit MLX | EXPLAIN | 0 MB / 2.9 GB res | 1.0 | 12.6 | 34.9 | 0 | stop | fast, hallucinates
2026-08-22 | m4      | Qwen3.5-4B (qwen3.5-4b-mlx) | 4-bit MLX | CLARIFY | 0 MB / 2.9 GB res | 1.3 | 4.3  | 36.7 | 0 | stop | fast, leaks the prompt
2026-08-22 | m4      | Qwen3.5-4B (qwen3.5-4b-mlx) | 4-bit MLX | RECALL  | 0 MB / 2.9 GB res | 0.9 | 1.3  | 42.3 | 0 | stop | fast, hallucinates
2026-08-22 | m4      | Ling-mini-2.0 16B-A1.4B (ling-mini-2.0) --raw | 4-bit MLX | EXPLAIN | 0 MB / 8.5 GB res | 0.2 | 3.1 | 108.4 | 0 | stop | suppression is a no-op here
2026-08-22 | m4      | Ling-mini-2.0 16B-A1.4B (ling-mini-2.0) --raw | 4-bit MLX | CLARIFY | 0 MB / 8.5 GB res | 0.2 | 0.9 | 109.3 | 0 | stop | suppression is a no-op here
2026-08-22 | m4      | Ling-mini-2.0 16B-A1.4B (ling-mini-2.0) --raw | 4-bit MLX | RECALL  | 0 MB / 8.5 GB res | 0.2 | 0.4 | 134.7 | 0 | stop | suppression is a no-op here

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
- Now benchmarked on m4 as well, and it does fail the memory filter there; see the
  m4 notes below.


Notes on the m4 candidate sweep (2026-08-22):

Candidates were picked to shrink the footprint while keeping the low-active-parameter
generation that makes the MoE quick: a 3-bit quant of the same Qwen3.6-35B-A3B
(`andrevp/Qwen3.6-35B-A3B-3bit-MLX`; mlx-community publishes nothing below 4-bit for
it), a smaller MoE (`mlx-community/Ling-mini-2.0-4bit`, 16B total / 1.4B active), and
two smaller dense models (`mlx-community/Qwen3.5-9B-MLX-4bit` and
`mlx-community/Qwen3.5-4B-MLX-4bit`). Each was loaded alone, at the production 32768
context, under the machine's ordinary desktop load.

- **Qwen3.6-35B-A3B 4-bit -- fails the memory filter.** 20.4 GB resident on a 32 GB
  machine drops free memory from 81% to well under a third and leaves the box pinned
  in double-digit GB of swap. Its recorded swap delta is *negative* (-4.7 GB): the
  machine was already saturated at 15.2 GB of swap before the run and macOS reclaimed
  during it, which is why the delta alone cannot be trusted here. The cost shows in
  prefill instead -- 23.9s to the first EXPLAIN token, against 4.0s for the same model
  at 3-bit and 1.0s for the small MoE. Decode itself was a respectable 42-53 tok/s, so
  the earlier "~5 tok/s" figure looks like a measurement taken while the load was still
  paging in; the honest verdict is unchanged, though, because a 24s wait for the first
  token of an EXPLAIN is not usable live.
- **Qwen3.6-35B-A3B 3-bit -- fits, and is the best of the sweep.** 14.2 GB resident,
  122 MB of swap growth, 1.2s warm ttft, 42 tok/s decode, and answers of the same
  character as the 4-bit on m3: the transcript repair, the terminology and the RECALL
  citation are all correct. Reasoning suppression works exactly as designed (0
  reasoning tokens in every case), which is expected, since it is the same model and
  chat template the prefill was tuned against. No quality loss against the 4-bit was
  visible on these three prompts: both miss the same repair, "least timeout" to *lease*
  timeout, which only the dense 9B made -- so that gap belongs to the MoE, not to the
  extra bit.
- **Ling-mini-2.0 4-bit -- fastest by a wide margin, untidy answers.** 8.5 GB resident,
  no swap at all, 0.2s warm ttft and 110-136 tok/s, about three times the 3-bit MoE.
  The answers are correct but do not hold the requested shape: EXPLAIN emitted its
  summary twice, and CLARIFY echoed the whole transcript before its three-part answer.
  It is not a Qwen thinking model, so, as the task asked, it was also run with `--raw`:
  suppression makes no difference to reasoning tokens (0 either way -- it does not
  think), but it does make the formatting worse. With `--raw` the duplicated EXPLAIN
  block disappeared. That is the empty `<think></think>` prefill being taken literally
  by a chat template that has no thinking channel to close, so it lands as stray
  assistant text. Adopting this model would therefore mean turning `reasoning_prefill`
  off for it -- a code change, unlike the Qwen candidates.
- **Qwen3.5-9B 4-bit -- fits easily, too slow.** 5.6 GB resident, no swap, and the best
  answers in the sweep: it was the only candidate to reconstruct "least timeout" as
  *lease timeout* and to explain the consequence correctly. But 19-26 tok/s makes an
  EXPLAIN take 17-37s, which is the bandwidth ceiling of a 9B dense model at 120 GB/s,
  not something a prompt can fix. Being right 25 seconds later is not usable live.
- **Qwen3.5-4B 4-bit -- fast, and wrong.** 2.9 GB resident, 35-42 tok/s, and it
  invented its facts: it decided the transcript was about Cassandra, asserted a
  15-second default renewal interval that appears nowhere, and mis-repaired "least
  timeout" to "minimum timeout". CLARIFY also echoed the instruction text of its own
  prompt back into the answer. This is the case the file's rule is written against in
  reverse -- it fits and it is fast, and it is still unusable.

Sustained behaviour on the fanless machine, EXPLAIN run back to back:

- 3-bit Qwen3.6-35B-A3B, 20 passes over about 9 minutes: 42.7 tok/s for the first
  twelve passes, then a decline to 34.1 by pass 16, where it settled. About 20% lost
  to throttling, with warm ttft creeping 1.2s -> 1.5s. `pmset -g therm` recorded no
  warning level, so this is ordinary silent throttling, not a thermal event.
- Ling-mini-2.0, 20 passes over about 2.5 minutes: 120.7 -> ~104 tok/s, about 13%.
- Qwen3.5-9B, 8 passes: 21.1 -> 17.9 tok/s, about 15%, from an already unusable base.

Every candidate throttles by 13-20% under sustained load, so the right way to read the
single-shot numbers on this machine is to discount them by about a fifth before asking
whether a model is fast enough for a whole meeting.

**Recommendation for m4: Qwen3.6-35B-A3B at 3-bit MLX**
(`andrevp/Qwen3.6-35B-A3B-3bit-MLX`).

It is the only candidate that passes all three filters in order. It *fits*: 14.2 GB
resident leaves about 17 GB for the OS and applications, and 122 MB of swap growth is
noise rather than paging. It is *fast enough*: 1.2s to the first token warm and 42
tok/s, holding 34 tok/s throttled, which puts a full EXPLAIN at 8-15s and CLARIFY and
RECALL at 1.5-4s. And its *answers are good*: correct repair and terminology on
EXPLAIN, the decisive three-part CLARIFY the tightened prompt asks for, and a correctly
cited one-line RECALL, with 0 reasoning tokens in every case because it is the same
model family the reasoning suppression was built for.

Adopting it needs no code change. `RVW_LLM_MODEL` and `RVW_LLM_URL` select the model
and endpoint, and `util/init_local_models.sh andrevp/Qwen3.6-35B-A3B-3bit-MLX` loads it
under the `meeting-assistant` identifier the app already asks for.

Two things to carry forward. If the machine is under heavier application load than it
was here -- it already sits on ~10 GB of swap before any model loads -- Ling-mini-2.0
is the fallback: 8.5 GB, three times the speed, at the price of untidy answers and of
having to turn `reasoning_prefill` off for it, which is a code change. And if answer
quality on m4 ever needs to beat the 3-bit quant rather than merely match it, the
9B is the model that knows the answer; it is the Air's memory bandwidth, not the
model, that makes it too slow to use.

A practical note for whoever benchmarks next on this machine: `lms get` timed out
repeatedly at about 3 MB/s while plain HTTPS to the same host ran at 13 MB/s, and the
abandoned jobs stayed `active` in `~/.lmstudio/.internal/download-jobs-info.json`,
which makes LM Studio hide the half-downloaded folder from `lms ls` even after the
files are complete. Downloading with `hf download <repo> --local-dir
~/.lmstudio/models/<publisher>/<repo>` and clearing the stale jobs from that file
(with the daemon stopped) was the way through.


## A more elaborate scheme, if we want one

This markdown table is enough for a handful of models on two machines, and it is
human-reviewable, which the flat file buys us. If the matrix grows and we want it
machine-readable, the natural next step -- and one that matches how this repo
already treats transcripts -- is a canonical `doc/model_benchmarks.jsonl` (one
object per run, appended, never rewritten) with this table rendered from it, and
`bench_model.sh` appending a line on each run. That is a deliberate step to take
together, not a default; raise it when the table starts to hurt.
