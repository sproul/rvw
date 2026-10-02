# Task brief: choose a local model that runs well on this 32 GB M4 Air

You are Claude Code, running on **m4**, an Apple M4 MacBook Air with **32 GB** of
unified memory and no fan. You are in the `rvw` repository (the local listening
assistant). Your job is to find the best local model to run the assistant on
*this* machine, benchmark the candidates honestly, and record the results.

First run `git pull` and confirm `util/bench_model.sh` and
`doc/model_benchmarks.md` exist; the second is the record you will add to, and it
explains the method and the hardware reasoning. Read it before you start.

## Why this task exists

On m3 (96 GB) the Qwen3.6-35B-A3B model is fast and usable (see its rows in
`doc/model_benchmarks.md`). On this 32 GB machine that same model was measured
driving the system into ~16.8 GB of swap at ~5 tokens/s -- unusable. Memory is
the reason, and memory is the first thing to select on.

## The rule

Pick the model to fit this machine's memory first, then judge speed, then judge
answer quality. A model that does not fit is not slow, it is unusable, so it does
not matter how good its answers are.

Concretely on 32 GB: leave generous headroom for the OS, other apps and the KV
cache (the assistant uses a 32768 context, which is not free). Treat any candidate
whose run shows more than a token amount of swap growth as failed, whatever its
speed looked like. Aim for a resident footprint that leaves the machine
comfortable, not one that just barely fits.

Remember the other hardware factors this machine has less of, documented in the
benchmark file: memory bandwidth (~1/3 of the m3, so decode will be slower even
when a model fits), GPU cores (slower prefill / time to first token), and no fan
(sustained load throttles). CPU core count is not the constraint; do not optimise
for it.

## What to do

1. **Pick candidates.** Prefer options that keep quality while shrinking the
   memory footprint. Reasonable starting points, but explore beyond them:
   - a lower-bit quant of the same MoE (e.g. a 3-bit Qwen3.6-A3B) -- keeps the
     fast low-active-parameter generation while fitting in less memory;
   - a smaller dense model (e.g. a Qwen3 8B / 4B class), 4-bit MLX;
   - any other small MoE that fits.
   Find and download them with `lms get <hf-url> --mlx` or LM Studio; note the
   served id each one loads under (`curl -s http://127.0.0.1:1234/v1/models`).

2. **Benchmark each, loaded one at a time.** Load the candidate, then:

       util/bench_model.sh <served-model-id>

   The tool reports fit (swap delta), time to first token, decode tokens/s,
   reasoning tokens and finish reason for the real EXPLAIN, UNGARBLE and RECALL
   prompts, and prints the full answers. Read the answers, not just the numbers:
   fast and wrong is not usable. For a thinking model, the default suppresses
   reasoning the way the assistant does (an empty-<think> prefill); if a candidate
   is not a Qwen thinking model, note whether suppression helped, hurt or did
   nothing (compare against `--raw`).

3. **Check sustained behaviour on the fanless machine.** A single benchmark is a
   sprint. For the one or two best candidates, run several EXPLAINs back to back
   and note whether throughput falls as the machine warms up.

4. **Record every run in `doc/model_benchmarks.md`**, appending rows to the
   results table -- including the failures, and including swap growth. Correct
   this machine's row in the Machines table from its real specs
   (`system_profiler SPHardwareDataType` and `SPDisplaysDataType`) so the recorded
   RAM, bandwidth and core counts are this machine's actual numbers, not the
   approximate ones. Commit the updated file.

5. **Recommend one model** for the assistant on this machine, with the reasoning:
   what fit, what was fast enough, and whether its EXPLAIN / UNGARBLE / RECALL
   answers were good. Note that adopting it needs no code change -- `RVW_LLM_MODEL`
   and `RVW_LLM_URL` switch models, and `util/init_local_models.sh <hf-model>`
   loads a chosen model under the `meeting-assistant` identifier the app uses.

## Deliverable

A committed update to `doc/model_benchmarks.md` with a row per candidate per case
(RAM and swap recorded), and a short written recommendation of the model to use on
m4 with its rationale. If nothing fits and performs acceptably, say so plainly and
list what was tried, so the next step is choosing a different class of model rather
than repeating these.

One caution carried over from m3: because the thinking channel is closed, a model
will deliberate in the visible answer if the prompt invites it. UNGARBLE did this on
m3 until its prompt was tightened to a decisive, fixed format, which fixed it there.
A different model may still ramble on UNGARBLE, EXPLAIN or RECALL; if it does, record
it against the model and machine -- it is the model fighting the closed thinking
channel, not your model choice failing, and the answer is a more decisive prompt.
