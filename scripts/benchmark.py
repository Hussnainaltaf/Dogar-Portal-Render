#!/usr/bin/env python3
"""
Load / performance benchmark for the OEP recruitment portal.

Fires N concurrent workers, each issuing R requests against a set of
representative endpoints, and reports latency percentiles (p50/p90/p95/p99),
throughput (req/s), and error rate per endpoint plus an overall summary.

It authenticates ONCE (respecting the 10/min login rate-limit) and reuses the
HttpOnly session cookie for every worker, so protected endpoints are exercised
under realistic authenticated load.

Usage:
    python scripts/benchmark.py \
        --base-url http://localhost:3000 \
        --email admin@yourcompany.com \
        --password '...' \
        --concurrency 20 \
        --requests 50 \
        --report docs/PERFORMANCE_REPORT.md

Dependencies: httpx (already in requirements-dev.txt).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime

try:
    import httpx
except ImportError:  # pragma: no cover
    print("httpx is required: pip install -r requirements-dev.txt", file=sys.stderr)
    sys.exit(2)


# ---------------------------------------------------------------------------
# Endpoints under test. GET-only so the benchmark is idempotent and safe to
# run repeatedly against a live DB.
# ---------------------------------------------------------------------------
DEFAULT_ENDPOINTS = [
    ("health",      "GET", "/api/health"),
    ("ping",        "GET", "/api/ping"),
    ("me",          "GET", "/api/users/me"),
    ("clients",     "GET", "/api/clients/?limit=25"),
    ("demands",     "GET", "/api/demands/?limit=25"),
    ("candidates",  "GET", "/api/candidates/?limit=25"),
    ("documents",   "GET", "/api/documents/templates"),
    ("dashboard",   "GET", "/api/dashboard/stats"),
]


@dataclass
class Sample:
    endpoint: str
    ok: bool
    status: int
    latency_ms: float


@dataclass
class EndpointStats:
    name: str
    latencies: list = field(default_factory=list)
    errors: int = 0
    total: int = 0
    statuses: dict = field(default_factory=dict)

    def add(self, s: Sample):
        self.total += 1
        self.statuses[s.status] = self.statuses.get(s.status, 0) + 1
        if s.ok:
            self.latencies.append(s.latency_ms)
        else:
            self.errors += 1

    def pct(self, p: float) -> float:
        if not self.latencies:
            return float("nan")
        ordered = sorted(self.latencies)
        k = max(0, min(len(ordered) - 1, int(round((p / 100.0) * (len(ordered) - 1)))))
        return ordered[k]

    def summary(self) -> dict:
        lat = self.latencies
        return {
            "endpoint": self.name,
            "requests": self.total,
            "ok": len(lat),
            "errors": self.errors,
            "error_rate_pct": round(100.0 * self.errors / self.total, 2) if self.total else 0.0,
            "min_ms": round(min(lat), 2) if lat else None,
            "p50_ms": round(self.pct(50), 2) if lat else None,
            "p90_ms": round(self.pct(90), 2) if lat else None,
            "p95_ms": round(self.pct(95), 2) if lat else None,
            "p99_ms": round(self.pct(99), 2) if lat else None,
            "max_ms": round(max(lat), 2) if lat else None,
            "mean_ms": round(statistics.mean(lat), 2) if lat else None,
            "statuses": self.statuses,
        }


def authenticate(base_url: str, email: str, password: str, timeout: float) -> dict:
    """Log in once and return an explicit {cookie_name: value} dict.

    Returning a plain dict (rather than the client's live Cookies jar) makes the
    session portable across the fresh httpx.Client each worker builds, including
    the HttpOnly `access_token` cookie that authenticates protected endpoints.
    """
    with httpx.Client(base_url=base_url, timeout=timeout, follow_redirects=False) as c:
        # Grab a CSRF cookie first (GET any page sets it via CsrfMiddleware).
        c.get("/api/ping")
        csrf = c.cookies.get("csrf_token") or c.cookies.get("csrftoken")
        headers = {}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        r = c.post(
            "/api/auth/login",
            json={"email": email, "password": password},
            headers=headers,
        )
        if r.status_code != 200:
            print(f"[auth] login failed: HTTP {r.status_code} {r.text[:200]}", file=sys.stderr)
            sys.exit(1)
        jar = {k: v for k, v in c.cookies.items()}
        if "access_token" not in jar:
            print("[auth] warning: no access_token cookie captured — protected "
                  "endpoints will 401", file=sys.stderr)
        print(f"[auth] logged in as {email} (cookies: {', '.join(jar.keys())})")
        return jar


def worker(base_url: str, endpoints, requests_per_worker: int, cookies, timeout: float):
    samples = []
    with httpx.Client(base_url=base_url, timeout=timeout, cookies=cookies, follow_redirects=True) as c:
        for _ in range(requests_per_worker):
            for name, method, path in endpoints:
                t0 = time.perf_counter()
                try:
                    resp = c.request(method, path)
                    dt = (time.perf_counter() - t0) * 1000.0
                    ok = 200 <= resp.status_code < 400
                    samples.append(Sample(name, ok, resp.status_code, dt))
                except Exception:
                    dt = (time.perf_counter() - t0) * 1000.0
                    samples.append(Sample(name, False, 0, dt))
    return samples


def run(args) -> dict:
    endpoints = DEFAULT_ENDPOINTS
    cookies = authenticate(args.base_url, args.email, args.password, args.timeout)

    stats = {name: EndpointStats(name) for name, _, _ in endpoints}
    total_requests = args.concurrency * args.requests * len(endpoints)
    print(f"[run] {args.concurrency} workers x {args.requests} rounds x {len(endpoints)} endpoints "
          f"= {total_requests} requests")

    wall_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(worker, args.base_url, endpoints, args.requests, cookies, args.timeout)
            for _ in range(args.concurrency)
        ]
        for f in as_completed(futures):
            for s in f.result():
                stats[s.endpoint].add(s)
    wall = time.perf_counter() - wall_start

    per_endpoint = [stats[name].summary() for name, _, _ in endpoints]
    all_lat = [x for st in stats.values() for x in st.latencies]
    all_err = sum(st.errors for st in stats.values())
    all_total = sum(st.total for st in stats.values())

    def overall_pct(p):
        if not all_lat:
            return None
        o = sorted(all_lat)
        k = max(0, min(len(o) - 1, int(round((p / 100.0) * (len(o) - 1)))))
        return round(o[k], 2)

    overall = {
        "total_requests": all_total,
        "errors": all_err,
        "error_rate_pct": round(100.0 * all_err / all_total, 2) if all_total else 0.0,
        "wall_seconds": round(wall, 2),
        "throughput_rps": round(all_total / wall, 1) if wall else None,
        "p50_ms": overall_pct(50),
        "p90_ms": overall_pct(90),
        "p95_ms": overall_pct(95),
        "p99_ms": overall_pct(99),
        "mean_ms": round(statistics.mean(all_lat), 2) if all_lat else None,
    }

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "config": {
            "base_url": args.base_url,
            "concurrency": args.concurrency,
            "requests_per_worker": args.requests,
            "endpoints": [p for _, _, p in endpoints],
        },
        "overall": overall,
        "per_endpoint": per_endpoint,
    }


def write_markdown(result: dict, path: str):
    o = result["overall"]
    cfg = result["config"]
    lines = [
        "# Performance / Load Benchmark Report",
        "",
        f"- **Generated:** {result['generated_at']}",
        f"- **Target:** `{cfg['base_url']}`",
        f"- **Load profile:** {cfg['concurrency']} concurrent workers × "
        f"{cfg['requests_per_worker']} rounds × {len(cfg['endpoints'])} endpoints",
        "",
        "## Overall summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Total requests | {o['total_requests']} |",
        f"| Errors | {o['errors']} ({o['error_rate_pct']}%) |",
        f"| Wall time | {o['wall_seconds']} s |",
        f"| Throughput | {o['throughput_rps']} req/s |",
        f"| Latency mean | {o['mean_ms']} ms |",
        f"| Latency p50 | {o['p50_ms']} ms |",
        f"| Latency p90 | {o['p90_ms']} ms |",
        f"| Latency p95 | {o['p95_ms']} ms |",
        f"| Latency p99 | {o['p99_ms']} ms |",
        "",
        "## Per-endpoint breakdown",
        "",
        "| Endpoint | Reqs | Err% | mean | p50 | p90 | p95 | p99 | max |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for e in result["per_endpoint"]:
        lines.append(
            f"| `{e['endpoint']}` | {e['requests']} | {e['error_rate_pct']}% | "
            f"{e['mean_ms']} | {e['p50_ms']} | {e['p90_ms']} | {e['p95_ms']} | "
            f"{e['p99_ms']} | {e['max_ms']} |"
        )
    lines += [
        "",
        "## Interpretation guidance",
        "",
        "- **p95 < 300 ms** on list endpoints under this concurrency is healthy for an "
        "internal recruitment portal.",
        "- A rising **error_rate_pct** (429/500) under higher `--concurrency` marks the "
        "saturation point — increase workers/DB pool or add caching before that point.",
        "- Re-run after schema/index changes to catch query regressions.",
        "",
        "_Generated by `scripts/benchmark.py`._",
        "",
    ]
    with open(path, "w") as fh:
        fh.write("\n".join(lines))
    print(f"[report] wrote {path}")


def main():
    ap = argparse.ArgumentParser(description="OEP portal load/perf benchmark")
    ap.add_argument("--base-url", default="http://localhost:3000")
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--requests", type=int, default=50, help="rounds per worker")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--report", default="docs/PERFORMANCE_REPORT.md")
    ap.add_argument("--json", default="", help="optional path to also dump raw JSON")
    args = ap.parse_args()

    result = run(args)
    write_markdown(result, args.report)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(result, fh, indent=2)
        print(f"[report] wrote {args.json}")

    o = result["overall"]
    print(f"\n=== DONE: {o['total_requests']} reqs, {o['throughput_rps']} req/s, "
          f"p95={o['p95_ms']}ms, errors={o['error_rate_pct']}% ===")


if __name__ == "__main__":
    main()
