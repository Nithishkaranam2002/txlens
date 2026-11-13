"""API latency on the full demo data -> evals/reports/perf.md (and perf.json).

Logs in as the demo users of center A (passwords from DEMO_*_PASSWORD in .env, never printed)
and times the main read endpoints and the heavier POSTs: p50, p95 and max over N runs after
warm-up. Page load times come from `pnpm --dir frontend perf-pages`, written to
evals/reports/perf_pages.json; when that file exists its numbers join the report.

Run against a production-like stack (`make demo` or `make demo-restore`):
`make perf` (API_URL=http://localhost:8000 RUNS=20 LABEL=after).
"""

import argparse
import json
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from txlens_backend.config import get_settings

REPORTS = Path(__file__).parents[2] / "reports"
CSRF = {"X-Requested-With": "perf"}


@dataclass(frozen=True)
class Case:
    """One timed request. `body` makes it a POST."""

    role: str
    name: str
    path: str
    body: dict[str, Any] | None = None
    runs: int | None = None  # fewer runs for rate-limited endpoints


@dataclass(frozen=True)
class Timing:
    """Latency of one case in milliseconds."""

    case: Case
    p50: float
    p95: float
    max: float
    status: int
    runs: int


def client_for(api: str, role: str, password: str) -> httpx.Client:
    """A logged-in client. The session cookie is Secure, which browsers send to http://localhost
    but httpx does not, so it is passed on explicitly (runs are shorter than its lifetime)."""
    client = httpx.Client(base_url=api, headers=CSRF, timeout=120)
    response = client.post("/auth/login", json={"username": f"{role}.center_a",
                                                "password": password})  # fmt: skip
    response.raise_for_status()
    client.headers["Cookie"] = f"txlens_access={response.cookies['txlens_access']}"
    return client


def percentile(values: list[float], share: float) -> float:
    """Nearest-rank percentile."""
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(share * len(ordered)) - 1))]


def measure(client: httpx.Client, case: Case, runs: int, warmup: int = 2) -> Timing:
    """Warm up, then time `runs` requests."""
    send: Callable[[], httpx.Response] = (
        (lambda: client.get(case.path)) if case.body is None
        else (lambda: client.post(case.path, json=case.body))
    )  # fmt: skip
    for _ in range(warmup):
        send()
    times, status = [], 0
    for _ in range(runs):
        started = time.perf_counter()
        status = send().status_code
        times.append((time.perf_counter() - started) * 1000)
    return Timing(case, statistics.median(times), percentile(times, 0.95), max(times), status,
                  runs)  # fmt: skip


def cases(clinician: httpx.Client, steward: httpx.Client) -> list[Case]:
    """The endpoints to time; ids are looked up first (a flagged patient, its pseudonym, a
    saved cohort)."""
    first = clinician.get("/worklist", params={"limit": 1}).json()["items"][0]
    pid = first["patient"]["patient_id"]
    alias = steward.get("/worklist", params={"limit": 1}).json()["items"][0]["patient"]
    alias_id = alias["patient_id"]
    cohorts = clinician.get("/cohorts", params={"limit": 1}).json()["items"]
    rules = {"organ": "kidney"}
    found = [
        Case("clinician", "worklist (default: high + medium)",
             "/worklist?severity=high&severity=medium&limit=50"),
        Case("clinician", "worklist, all severities, page 2", "/worklist?limit=50&offset=50"),
        Case("clinician", "worklist, low only", "/worklist?severity=low&limit=50"),
        Case("data_steward", "worklist, de-identified", "/worklist?limit=50"),
        Case("clinician", "patient list", "/patients?limit=50"),
        Case("clinician", "patient timeline", f"/patients/{pid}/timeline"),
        Case("clinician", "patient flags", f"/patients/{pid}/flags"),
        Case("clinician", "patient risk", f"/patients/{pid}/risk"),
        Case("data_steward", "patient timeline, de-identified", f"/patients/{alias_id}/timeline"),
        Case("data_steward", "patient flags, de-identified", f"/patients/{alias_id}/flags"),
        Case("clinician", "review queue", "/review/items?limit=50"),
        Case("researcher", "cohort preview", "/cohorts/preview", rules),
        Case("researcher", "survival analysis", "/survival/analysis",
             {"grouping": "donor_type"}, runs=5),
        Case("researcher", "copilot conversations", "/copilot/conversations"),
    ]  # fmt: skip
    if cohorts:
        cid = cohorts[0]["cohort_id"]
        found.append(Case("clinician", "cohort members", f"/cohorts/{cid}/members?limit=50"))
    return found


def report(
    timings: list[Timing], label: str, pages: dict[str, Any] | None,
    baseline: dict[str, dict[str, float]] | None, pages_before: dict[str, Any] | None = None,
) -> str:  # fmt: skip
    """Markdown for evals/reports/perf.md; with a baseline, its p50/p95/max are shown first."""
    before = "before p50 / p95 / max | " if baseline else ""
    lines = [f"# API and page latency ({label})", "",
             "Full demo data (2,000 patients, 3 centers). Production builds in Docker on one "
             "laptop; times include the HTTP round trip. Re-run: `make perf`.", "",
             f"| Endpoint | Role | {before}p50 ms | p95 ms | max ms | runs | status |",
             f"|---|---|{'---:|' if baseline else ''}---:|---:|---:|---:|---:|"]  # fmt: skip
    for t in sorted(timings, key=lambda t: -t.p95):
        old = (baseline or {}).get(t.case.name)
        then = f"{old['p50']:.0f} / {old['p95']:.0f} / {old['max']:.0f} | " if old else ""
        then = then or ("— | " if baseline else "")
        lines.append(f"| {t.case.name} | {t.case.role} | {then}{t.p50:.0f} | {t.p95:.0f} | "
                     f"{t.max:.0f} | {t.runs} | {t.status} |")  # fmt: skip
    if pages:
        earlier = (pages_before or {}).get("pages", {})
        lines += [
            "",
            f"| Page | {'before p50 | ' if earlier else ''}p50 ms (until its data is "
            "shown) | max ms | runs |",
            f"|---|{'---:|' if earlier else ''}---:|---:|---:|",
        ]
        for name, page in pages["pages"].items():
            then = f"{earlier[name]['p50']:.0f} | " if name in earlier else ""
            lines.append(f"| {name} | {then}{page['p50']:.0f} | {page['max']:.0f} | "
                         f"{page['runs']} |")  # fmt: skip
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    """Time every case and write the report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--label", default="current code")
    parser.add_argument("--out", type=Path, default=REPORTS / "perf.md")
    parser.add_argument("--baseline", type=Path, help="an earlier perf.json to compare with")
    args = parser.parse_args(argv)
    settings = get_settings()
    passwords = {
        "clinician": settings.demo_clinician_password,
        "researcher": settings.demo_researcher_password,
        "data_steward": settings.demo_data_steward_password,
    }
    clients = {r: client_for(args.api, r, p.get_secret_value()) for r, p in passwords.items()}
    timings = [
        measure(clients[c.role], c, c.runs or args.runs)
        for c in cases(clients["clinician"], clients["data_steward"])
    ]
    pages_file = REPORTS / "perf_pages.json"
    pages = json.loads(pages_file.read_text()) if pages_file.exists() else None
    baseline = (
        {row["name"]: row for row in json.loads(args.baseline.read_text())}
        if args.baseline else None
    )  # fmt: skip
    # The page timings of the baseline run sit next to it (perf_pages_before.json).
    pages_before_file = args.baseline.with_name("perf_pages_before.json") if args.baseline else None
    pages_before = (
        json.loads(pages_before_file.read_text())
        if pages_before_file and pages_before_file.exists() else None
    )  # fmt: skip
    args.out.write_text(report(timings, args.label, pages, baseline, pages_before))
    args.out.with_suffix(".json").write_text(json.dumps(
        [{"name": t.case.name, "role": t.case.role, "p50": round(t.p50, 1),
          "p95": round(t.p95, 1), "max": round(t.max, 1), "status": t.status}
         for t in timings], indent=2) + "\n")  # fmt: skip
    print(args.out.read_text())


if __name__ == "__main__":
    main()
