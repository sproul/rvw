"""Measure one loaded local model on this machine, with comparable numbers.

Runs three fixed prompts -- the real EXPLAIN_SPEECH, UNGARBLE_SPEECH and RECALL framings the
assistant sends -- against a model that is already loaded in LM Studio, and
reports the numbers that decide whether a model is usable live on this machine:
whether it fit without swapping, how long until the first token, how fast it then
generated, and whether it wasted its budget thinking. The prompts are fixed here
so a run on the 96 GB M3 and a run on the 32 GB M4 are measuring the same thing.

Load the model first (LM Studio, or `lms load ...`), then:

    util/bench_model.sh <served-model-id> [--raw]

--raw turns off the empty-<think> reasoning suppression, to show what a thinking
model does when left alone. The default matches how the assistant runs.
"""

import argparse
import json
import re
import subprocess
import time
import urllib.request

from rvw import config, prompts, recall
from rvw.meeting_index import Hit

CHAT_URL = config.llm_base_url.rstrip("/") + "/chat/completions"

TRANSCRIPT = (
    "[00:03] them: so the least timeout on the coordinator is thirty seconds\n"
    "[00:09] them: and the client should reconnect with exponential back off\n"
    "[00:14] me: what happens if the lease cannot be renewed in time"
)


def standard_cases():
    """The three answering behaviours, with fixed inputs so runs are comparable."""
    passages = recall.numbered_passages([
        _passage("the coordinator lease timeout was thirty seconds", "2026-08-20T09:00:05"),
        _passage("the client should reconnect with exponential backoff", "2026-08-20T09:00:09")])
    return [("EXPLAIN_SPEECH", prompts.build_explain_messages(TRANSCRIPT, 60)),
            ("UNGARBLE_SPEECH", prompts.build_ungarble_messages(TRANSCRIPT, 45)),
            ("RECALL", prompts.build_recall_messages(
                "what did they say about reconnect behavior", passages))]


def _passage(text, when):
    return Hit(meeting="2026-08-20_09.00", meeting_date="2026-08-20", start_epoch=0.0,
               start_local=when, speaker="them", stream="system", text=text,
               meeting_dir="/archive/2026-08-20_09.00", screenshots=[])


def swap_used_mb():
    """Megabytes of swap in use now; the jump during a run is how swapping shows."""
    output = subprocess.run(["sysctl", "-n", "vm.swapusage"],
                            capture_output=True, text=True).stdout
    match = re.search(r"used\s*=\s*([\d.]+)M", output)
    return float(match.group(1)) if match else float("nan")


def prepared_messages(messages, suppress):
    """The empty-<think> prefill the assistant uses, unless --raw asked for none."""
    if not suppress:
        return messages
    return list(messages) + [{"role": "assistant", "content": config.reasoning_prefill}]


def run_case(model, messages, suppress, timeout):
    """Stream one prompt and time the first token, the throughput and the thinking."""
    payload = {"model": model, "messages": prepared_messages(messages, suppress),
               "max_tokens": config.llm_max_tokens, "temperature": 0.3,
               "stream": True, "stream_options": {"include_usage": True}}
    request = urllib.request.Request(CHAT_URL, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    return _collect_stream(request, timeout)


def _collect_stream(request, timeout):
    started = time.monotonic()
    first_token_at, content, finish, usage = None, [], None, {}
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw in response:
            parsed = _parse_line(raw)
            if parsed is None:
                continue
            usage = parsed.get("usage") or usage
            if parsed.get("content"):
                first_token_at = first_token_at or time.monotonic()
                content.append(parsed["content"])
            finish = parsed.get("finish") or finish
    return _case_metrics(time.monotonic() - started, first_token_at and first_token_at - started,
                         "".join(content).strip(), finish, usage)


def _parse_line(raw_line):
    line = raw_line.decode("utf-8", "replace").strip()
    if not line.startswith("data:"):
        return None
    body = line[len("data:"):].strip()
    if not body or body == "[DONE]":
        return None
    parsed = json.loads(body)
    choice = (parsed.get("choices") or [{}])[0]
    return {"content": (choice.get("delta") or {}).get("content"),
            "finish": choice.get("finish_reason"), "usage": parsed.get("usage")}


def _case_metrics(wall_s, ttft_s, answer, finish, usage):
    completion = usage.get("completion_tokens")
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    decode_s = (wall_s - ttft_s) if ttft_s else None
    return {"wall_s": wall_s, "ttft_s": ttft_s, "completion_tokens": completion,
            "reasoning_tokens": reasoning, "finish": finish, "answer_chars": len(answer),
            "answer": answer,
            "decode_tok_s": (completion / decode_s) if completion and decode_s else None}


def machine_line():
    def sysctl(name):
        return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True).stdout.strip()
    ram_gb = round(int(sysctl("hw.memsize")) / 1073741824)
    return "host=%s  chip=%s  ram=%dGB" % (sysctl("kern.hostname"),
                                           sysctl("machdep.cpu.brand_string"), ram_gb)


def report(model, results, swap_delta_mb):
    print("\nmodel: %s" % model)
    print(machine_line())
    print("swap grew by %.0f MB during the run (a large jump means it did not fit)"
          % swap_delta_mb)
    header = "%-8s %8s %8s %10s %9s %8s  %s"
    print(header % ("case", "ttft_s", "wall_s", "decode t/s", "reason_tk", "finish", "answer[:60]"))
    for name, metrics in results:
        print(header % (name, _fmt(metrics["ttft_s"], "%.1f"), _fmt(metrics["wall_s"], "%.1f"),
                        _fmt(metrics["decode_tok_s"], "%.1f"),
                        _fmt(metrics["reasoning_tokens"], "%d"), metrics["finish"] or "-",
                        (metrics["answer"][:60] or "<empty>").replace("\n", " ")))


def _fmt(value, spec):
    return (spec % value) if value is not None else "-"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("model", help="the served model id, e.g. qwen3.6-35b-a3b")
    parser.add_argument("--raw", action="store_true", help="disable reasoning suppression")
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()

    swap_before = swap_used_mb()
    results = [(name, run_case(args.model, messages, not args.raw, args.timeout))
               for name, messages in standard_cases()]
    report(args.model, results, swap_used_mb() - swap_before)
    print("\nFull answers:")
    for name, metrics in results:
        print("\n----- %s -----\n%s" % (name, metrics["answer"] or "<empty>"))


if __name__ == "__main__":
    main()
