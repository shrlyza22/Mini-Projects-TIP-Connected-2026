#!/usr/bin/env python3
"""
Performance collection for the ZTNA PDP — evidence for reviewer comment R3-4.
================================================================================

Collects, against a *running* PDP (default http://localhost:5000):
  1. Flow-provisioning latency per login            -> /login  field provision_ms
  2. Flow-revocation latency per logout             -> /logout field revoke_ms
  3. Controller load during concurrent revocation   -> --mass mode
  4. Aggregate RESTCONF PUT latency percentiles     -> /metrics endpoint
  5. Optional ODL controller CPU during the churn   -> /proc/<karaf-pid>/stat

It also reproduces the three tier scenarios (FULL / LIMITED / DENIED) to confirm
they still hold with the current build.

Where to put it: on VM1, in the same directory as pdp.py (~/PDP).
No separate config file is needed — every option is a CLI flag.

Run (after ODL, the PDP, and Mininet are up):
    cd ~/PDP
    python3 perf_collect.py --cycles 30
    python3 perf_collect.py --cycles 30 --mass

Outputs:
    perf_results.md   human-readable summary (paste-ready for the paper)
    perf_results.csv  raw per-sample latencies

Note on scenario ordering: the PDP counts failed logins per user and never
resets it, so FULL (0 failures) must run before LIMITED (2 failures) and
DENIED (4 failures).
"""

import argparse
import csv
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Fixed lab identities (must match ztna_net.py / pdp.py)
# ---------------------------------------------------------------------------
HOST_MAC = {
    "10.0.0.1": "00:00:00:00:00:01",
    "10.0.0.2": "00:00:00:00:00:02",
    "10.0.0.3": "00:00:00:00:00:03",
    "10.0.0.4": "00:00:00:00:00:04",
}
CREDENTIALS = {"ratih": "research123", "bima": "guest123"}
SPOOF_MAC = "aa:bb:cc:dd:ee:ff"

# name, user, ip, mac, primed_failures, expected_tier
SCENARIOS = [
    ("FULL",    "ratih", "10.0.0.1", HOST_MAC["10.0.0.1"], 0, "FULL"),
    ("LIMITED", "ratih", "10.0.0.1", SPOOF_MAC,            2, "LIMITED"),
    ("DENIED",  "bima",  "10.0.0.4", SPOOF_MAC,            4, "DENIED"),
]

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def api(url, method="POST", payload=None, timeout=20):
    """Return (status, body_dict, elapsed_ms). status is None on transport error."""
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    started = time.perf_counter()
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            raw, status = response.read(), response.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    except (urllib.error.URLError, TimeoutError, socket.timeout):
        return None, {}, (time.perf_counter() - started) * 1000

    elapsed_ms = (time.perf_counter() - started) * 1000
    try:
        body = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        body = {}
    return status, body, elapsed_ms


def login(user, ip, mac, base):
    return api(
        base + "/login",
        payload={"username": user, "password": CREDENTIALS[user], "ip": ip, "mac": mac},
    )


def logout(token, base):
    return api(base + "/logout", payload={"token": token})


def metrics(base):
    status, body, _ = api(base + "/metrics", method="GET")
    return body if status == 200 else {}


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def summarize(values):
    values = [v for v in values if v is not None]
    if not values:
        return {}
    ordered = sorted(values)

    def percentile(p):
        index = min(len(ordered) - 1, round((p / 100.0) * (len(ordered) - 1)))
        return ordered[index]

    return {
        "n": len(ordered),
        "mean": sum(ordered) / len(ordered),
        "p50": percentile(50),
        "p95": percentile(95),
        "min": ordered[0],
        "max": ordered[-1],
    }


# ---------------------------------------------------------------------------
# ODL controller CPU (Linux /proc)
# ---------------------------------------------------------------------------
def find_odl_pid():
    """Locate the OpenDaylight/Karaf JVM. Prefer the actual Java main class
    rather than the launcher shell, whose CPU usage is misleading."""
    for pattern in ("org.apache.karaf.main.Main", "karaf"):
        try:
            output = subprocess.run(
                ["pgrep", "-f", pattern], capture_output=True, text=True, check=False
            ).stdout.split()
            if output:
                return int(output[0])
        except (OSError, ValueError):
            continue
    return None


def cpu_seconds(pid):
    try:
        with open("/proc/%d/stat" % pid, encoding="utf-8") as handle:
            fields = handle.read().split()
        ticks = int(fields[13]) + int(fields[14])  # utime + stime
        return ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, IndexError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------
def ensure_failed_logins(user, target, base, state):
    """Send wrong-password attempts until the server-side failure count reaches
    `target`. The server keeps this counter per user and never resets it, so we
    only ever top it up."""
    while state.get(user, 0) < target:
        api(base + "/login",
            payload={"username": user, "password": "wrong-" + os.urandom(3).hex()})
        state[user] = state.get(user, 0) + 1


def run_scenarios(base):
    results = []
    state = {}
    for name, user, ip, mac, failures, expected in SCENARIOS:
        ensure_failed_logins(user, failures, base, state)
        status, body, elapsed = login(user, ip, mac, base)
        tier = body.get("tier")
        results.append({
            "scenario": name,
            "expected": expected,
            "status": status,
            "attained": tier,
            "trust": body.get("trust"),
            "provision_ms": body.get("provision_ms"),
            "roundtrip_ms": round(elapsed, 1),
            "ok": bool(body.get("ok")),
            "match": tier == expected,
        })
        token = body.get("token")
        if token:
            logout(token, base)
    return results


def churn(base, cycles):
    provision_ms, revoke_ms, roundtrip_login, roundtrip_logout = [], [], [], []
    for _ in range(cycles):
        status, body, elapsed = login("ratih", "10.0.0.1", HOST_MAC["10.0.0.1"], base)
        roundtrip_login.append(elapsed)
        if status == 200 and body.get("ok"):
            provision_ms.append(body.get("provision_ms"))
            token = body.get("token")
            status2, body2, elapsed2 = logout(token, base)
            roundtrip_logout.append(elapsed2)
            if body2.get("revoke_ms") is not None:
                revoke_ms.append(body2.get("revoke_ms"))
    return {
        "provision": summarize(provision_ms),
        "revoke": summarize(revoke_ms),
        "roundtrip_login": summarize(roundtrip_login),
        "roundtrip_logout": summarize(roundtrip_logout),
    }


def mass_revocation(base):
    """Create one FULL session for ratih from each of the four host IPs, then
    revoke them all and measure the total time (controller load on revocation).

    Note: the PDP keeps one session per (user, IP); distinct IPs coexist, so up
    to four concurrent sessions are possible with the current policy."""
    sessions = []
    for ip, mac in HOST_MAC.items():
        status, body, _ = login("ratih", ip, mac, base)
        if status == 200 and body.get("ok"):
            sessions.append((ip, body.get("token")))

    started = time.perf_counter()
    total_revoked = 0
    for ip, token in sessions:
        status, body, _ = logout(token, base)
        if body.get("ok"):
            total_revoked += body.get("revoked", 0)
    wall_ms = (time.perf_counter() - started) * 1000

    return {"sessions": len(sessions), "flows_revoked": total_revoked,
            "wall_ms": round(wall_ms, 1)}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def fmt(stats, unit="ms"):
    if not stats:
        return "n/a"
    return (f"n={stats['n']}  mean={stats['mean']:.1f}  p50={stats['p50']:.1f}  "
            f"p95={stats['p95']:.1f}  min={stats['min']:.1f}  max={stats['max']:.1f} {unit}")


def build_markdown(scenario_rows, churn_stats, mass, metrics_after, cpu):
    lines = ["# PDP Performance Results", ""]

    lines.append("## Scenario validation")
    lines.append("")
    lines.append("| Scenario | Expected | Attained | Trust | provision_ms | Status |")
    lines.append("|---|---|---|---|---|---|")
    for row in scenario_rows:
        lines.append(
            f"| {row['scenario']} | {row['expected']} | {row['attained']} "
            f"| {row['trust']} | {row['provision_ms']} | {row['status']} |"
        )
    lines.append("")

    lines.append("## Provisioning / revocation latency (login-logout churn)")
    lines.append("")
    for key, label in (("provision", "Provisioning (login)"),
                       ("revoke", "Revocation (logout)"),
                       ("roundtrip_login", "Client login round-trip"),
                       ("roundtrip_logout", "Client logout round-trip")):
        lines.append(f"- **{label}**: {fmt(churn_stats.get(key, {}))}")
    lines.append("")

    if mass:
        lines.append("## Concurrent revocation (controller load)")
        lines.append("")
        lines.append(f"- Concurrent sessions: {mass['sessions']}")
        lines.append(f"- Flows revoked: {mass['flows_revoked']}")
        lines.append(f"- Total revocation wall time: {mass['wall_ms']} ms")
        if mass["sessions"]:
            lines.append(f"- Mean per session: {mass['wall_ms'] / mass['sessions']:.1f} ms")
        lines.append("")

    if cpu:
        lines.append("## ODL controller CPU during churn")
        lines.append("")
        lines.append(f"- CPU time consumed: {cpu['cpu_s']:.2f} s over {cpu['wall_s']:.2f} s wall")
        lines.append(f"- Average CPU: {cpu['avg_pct']:.1f}%")
        lines.append("")

    if metrics_after:
        lines.append("## /metrics snapshot after the run")
        lines.append("")
        for key in ("logins", "logouts", "flows_installed", "flows_revoked",
                    "flow_put_samples", "flow_put_ms_avg", "flow_put_ms_p50",
                    "flow_put_ms_p95", "flow_put_ms_max"):
            if key in metrics_after:
                lines.append(f"- {key}: {metrics_after[key]}")
        lines.append("")

    return "\n".join(lines)


def write_csv(path, scenario_rows, churn_stats):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["scenario", "expected", "attained", "trust",
                         "provision_ms", "roundtrip_ms", "status"])
        for row in scenario_rows:
            writer.writerow([row["scenario"], row["expected"], row["attained"],
                             row["trust"], row["provision_ms"],
                             row["roundtrip_ms"], row["status"]])
        writer.writerow([])
        writer.writerow(["operation", "n", "mean", "p50", "p95", "min", "max"])
        for key, _ in (("provision", ""), ("revoke", ""),
                       ("roundtrip_login", ""), ("roundtrip_logout", "")):
            stats = churn_stats.get(key, {})
            if stats:
                writer.writerow([key, stats["n"], round(stats["mean"], 2),
                                 stats["p50"], stats["p95"], stats["min"], stats["max"]])


def main():
    parser = argparse.ArgumentParser(description="ZTNA PDP performance collection")
    parser.add_argument("--url", default="http://localhost:5000")
    parser.add_argument("--cycles", type=int, default=30,
                        help="number of FULL login/logout cycles (default 30)")
    parser.add_argument("--mass", action="store_true",
                        help="also run the concurrent-revocation experiment")
    parser.add_argument("--odl-pid", type=int, default=None,
                        help="Karaf PID for CPU sampling (auto-detected if omitted)")
    parser.add_argument("--out-prefix", default="perf_results")
    args = parser.parse_args()

    base = args.url.rstrip("/")

    status, _, _ = api(base + "/health", method="GET", timeout=5)
    if status != 200:
        print(f"! PDP not reachable at {base} (health status={status}). "
              "Start it first: sudo python3 pdp.py")
        return 2

    print(f"== PDP performance collection @ {base} ==")
    metrics_before = metrics(base)

    print("\n[1] tier scenarios ...")
    scenario_rows = run_scenarios(base)
    for row in scenario_rows:
        flag = "OK" if row["match"] else "MISMATCH"
        print(f"  {row['scenario']:<8} expected={row['expected']:<8} "
              f"attained={row['attained']:<8} T={row['trust']} "
              f"provision_ms={row['provision_ms']} [{flag}]")

    odl_pid = args.odl_pid or find_odl_pid()
    cpu_before = cpu_seconds(odl_pid) if odl_pid else None
    wall_before = time.perf_counter()

    print(f"\n[2] churn: {args.cycles} x FULL login/logout ...")
    churn_stats = churn(base, args.cycles)
    print("  provisioning:", fmt(churn_stats["provision"]))
    print("  revocation  :", fmt(churn_stats["revoke"]))

    wall_elapsed = time.perf_counter() - wall_before
    cpu = None
    cpu_after = cpu_seconds(odl_pid) if odl_pid else None
    if cpu_before is not None and cpu_after is not None and wall_elapsed > 0:
        cpu = {"cpu_s": cpu_after - cpu_before, "wall_s": wall_elapsed,
               "avg_pct": (cpu_after - cpu_before) / wall_elapsed * 100}
        print(f"  ODL CPU (pid {odl_pid}): {cpu['cpu_s']:.2f}s / "
              f"{cpu['wall_s']:.2f}s = {cpu['avg_pct']:.1f}%")

    mass = None
    if args.mass:
        print("\n[3] concurrent revocation ...")
        mass = mass_revocation(base)
        print(f"  {mass['sessions']} sessions, {mass['flows_revoked']} flows, "
              f"{mass['wall_ms']} ms total")

    metrics_after = metrics(base)
    markdown = build_markdown(scenario_rows, churn_stats, mass, metrics_after, cpu)
    with open(args.out_prefix + ".md", "w", encoding="utf-8") as handle:
        handle.write(markdown)
    write_csv(args.out_prefix + ".csv", scenario_rows, churn_stats)

    print(f"\nSaved: {args.out_prefix}.md, {args.out_prefix}.csv")
    if metrics_before and metrics_after:
        print("flows_installed total:",
              metrics_after.get("flows_installed"),
              "(before run:", metrics_before.get("flows_installed"), ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
