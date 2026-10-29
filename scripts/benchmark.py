"""Small closed-loop streaming benchmark; Python standard library only."""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import time
import urllib.request
import uuid


def percentile(values, quantile):
    values = sorted(values)
    position = (len(values) - 1) * quantile
    low, high = math.floor(position), math.ceil(position)
    return values[low] + (values[high] - values[low]) * (position - low)


def kubectl(profile, *arguments):
    executable = shutil.which("minikube")
    if executable is None and Path("D:/Tools/Minikube/minikube.exe").exists():
        executable = "D:/Tools/Minikube/minikube.exe"
    if executable is None:
        raise RuntimeError("minikube is not on PATH")
    result = subprocess.run(
        [executable, "--profile=" + profile, "kubectl", "--", *arguments],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode:
        raise RuntimeError("kubectl inspection failed")
    return result.stdout


def request_once(base_url, token, profile, max_tokens, timeout):
    # Unique early text reduces reuse of this benchmark's prompt prefix cache.
    prompt = "Request " + uuid.uuid4().hex + ". "
    if profile == "short":
        prompt += "Explain why containers are useful. Give a detailed answer."
    else:
        prompt += ("A team runs containers on Kubernetes. It stores model weights on a persistent volume. " * 70)
        prompt += "Summarize the benefits and possible operational problems in detail."
    payload = {
        "model": "qwen", "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens, "temperature": 0, "stream": True,
        "stream_options": {"include_usage": True},
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
    )
    # Do not inherit Windows proxy settings for a loopback connection.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.perf_counter()
    first_content = None
    last_content = None
    usage = None
    finish_reason = None
    completed = False
    try:
        with opener.open(request, timeout=timeout) as response:
            for line in response:
                if not line.startswith(b"data: "):
                    continue
                data = line[6:].strip()
                if data == b"[DONE]":
                    completed = True
                    break
                chunk = json.loads(data)
                if chunk.get("usage") is not None:
                    usage = chunk["usage"]
                for choice in chunk.get("choices", []):
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
                    if choice.get("delta", {}).get("content"):
                        now = time.perf_counter()
                        if first_content is None:
                            first_content = now
                        last_content = now
        ended = time.perf_counter()
        if not completed or first_content is None or usage is None:
            raise RuntimeError("Incomplete stream or missing usage")
        output_tokens = usage["completion_tokens"]
        decode_seconds = last_content - first_content
        # Chunk arrival times approximate token timing; SSE chunks may contain
        # more than one token. Do not claim per-token ITL percentiles here.
        tpot = decode_seconds / (output_tokens - 1) if output_tokens > 1 and decode_seconds > 0 else None
        return {
            "ok": True, "ttft_s": first_content - started,
            "e2e_s": ended - started, "tpot_s": tpot,
            "decode_tokens_s": 1 / tpot if tpot else None,
            "prompt_tokens": usage["prompt_tokens"], "output_tokens": output_tokens,
            "finish_reason": finish_reason,
        }
    except Exception as error:
        # Never save response bodies, request text or token-bearing exceptions.
        return {"ok": False, "error_type": type(error).__name__,
                "e2e_s": time.perf_counter() - started}


def summarize(rows, elapsed):
    successful = [row for row in rows if row["ok"]]
    result = {"requests": len(rows), "succeeded": len(successful),
              "failed": len(rows) - len(successful), "elapsed_s": elapsed,
              "requests_s": len(successful) / elapsed,
              "output_tokens_s": sum(row["output_tokens"] for row in successful) / elapsed}
    for name in ("ttft_s", "e2e_s", "tpot_s", "decode_tokens_s", "prompt_tokens", "output_tokens"):
        values = [row[name] for row in successful if row.get(name) is not None]
        if values:
            result[name] = {"mean": statistics.mean(values),
                            "p50": percentile(values, 0.5), "p95": percentile(values, 0.95)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--profile", default="llm-lab")
    parser.add_argument("--requests", type=int, default=4)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 2])
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--label", default="baseline")
    parser.add_argument("--output", type=Path, default=Path("reports/baseline.json"))
    args = parser.parse_args()
    if args.requests < 1 or min(args.concurrency) < 1 or args.max_tokens < 2:
        parser.error("requests/concurrency must be positive; max-tokens must be >= 2")
    if not args.base_url.startswith(("http://127.0.0.1:", "http://localhost:")):
        parser.error("Use a loopback port-forward URL")
    try:
        token = os.environ.get("LLM_API_TOKEN")
        if not token:
            encoded = kubectl(args.profile, "get", "secret", "api-credentials", "-n", "llm-lab",
                              "-o", "jsonpath={.data.token}")
            token = base64.b64decode(encoded.strip()).decode()
        config = json.loads(kubectl(args.profile, "get", "configmap", "vllm-config", "vllm-environment",
                                    "model-download-config", "-n", "llm-lab", "-o", "json"))
        pods = json.loads(kubectl(args.profile, "get", "pods", "-n", "llm-lab", "-l", "app=vllm", "-o", "json"))
        deployment = json.loads(kubectl(args.profile, "get", "deployment", "vllm", "-n", "llm-lab", "-o", "json"))
    except Exception as error:
        raise SystemExit("Benchmark setup failed: " + type(error).__name__) from None
    if not token:
        raise SystemExit("API token is empty")
    images = [{"pod": pod["metadata"]["name"], "containers": [
        {"name": c["name"], "image_id": c.get("imageID")}
        for c in pod.get("status", {}).get("containerStatuses", [])]}
        for pod in pods["items"]]
    report = {"label": args.label, "started_utc": datetime.now(timezone.utc).isoformat(),
              "base_url": args.base_url, "requests_per_scenario": args.requests,
              "max_output_tokens": args.max_tokens, "images": images,
              "resources": [{"name": c["name"], "resources": c.get("resources", {})}
                            for c in deployment["spec"]["template"]["spec"]["containers"]],
              "configmaps": [{"name": c["metadata"]["name"], "data": c["data"]} for c in config["items"]],
              "notes": ["Closed-loop client concurrency; port-forward included in latency.",
                        "First nonempty SSE content approximates TTFT; TPOT uses content chunk timestamps.",
                        "Warmups excluded; unique early request text reduces prefix cache reuse.",
                        f"{args.requests} samples per scenario; p95 is unstable with small sample counts."],
              "scenarios": []}
    failed = False
    for prompt_profile in ("short", "long"):
        print(f"Warmup: {prompt_profile}", flush=True)
        warmup = request_once(args.base_url, token, prompt_profile, args.max_tokens, args.timeout)
        if not warmup["ok"]:
            raise SystemExit("Warmup failed: " + warmup["error_type"])
        for concurrency in args.concurrency:
            print(f"Measuring: {prompt_profile}, clients={concurrency}, requests={args.requests}", flush=True)
            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                rows = list(pool.map(lambda _: request_once(args.base_url, token, prompt_profile,
                                                           args.max_tokens, args.timeout), range(args.requests)))
            summary = summarize(rows, time.perf_counter() - started)
            failed |= summary["failed"] > 0
            report["scenarios"].append({"prompt_profile": prompt_profile, "concurrency": concurrency,
                                        "summary": summary, "requests": rows})
            if summary["succeeded"]:
                print(f"  success={summary['succeeded']}/{len(rows)}, "
                      f"TTFT p50={summary['ttft_s']['p50']:.3f}s, "
                      f"E2E p50={summary['e2e_s']['p50']:.3f}s, "
                      f"throughput={summary['output_tokens_s']:.2f} output tokens/s", flush=True)
            else:
                print("  All requests failed; see error categories in the report.", flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Saved: " + str(args.output.resolve()))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
