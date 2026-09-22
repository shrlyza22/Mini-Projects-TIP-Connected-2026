#!/usr/bin/env python3
"""
ZTNA Policy Engine (PDP) — runs on VM1 alongside OpenDaylight.

Flow of one session:
  pep_client (inside a Mininet host) --HTTP--> /login
     -> authenticate (user -> role/segment)
     -> compute Trust Score  T = wR*R + wC*C + wB*B
     -> for each resource the role is entitled to, check tier + min-score
     -> provision directed flows along the shortest ring path via RESTCONF
     -> return the granted access map

Enforcement lives in the OVS flow tables. The PDP installs persistent
default-deny rules and temporary higher-priority per-session ALLOW rules; it
never carries data traffic itself.

Run:   pip install flask requests
       sudo python3 pdp.py            # binds 0.0.0.0:5000
Test:  python3 pdp.py --dry-run       # prints the RESTCONF JSON, no ODL calls
"""

import argparse
import ipaddress
import time
import uuid
from datetime import datetime

import requests
from flask import Flask, jsonify, request

# ---------------------------------------------------------------------------
# CONFIG  (edit these to match your testbed)
# ---------------------------------------------------------------------------
ODL_HOST   = "192.168.56.2"
ODL_PORT   = 8181
ODL_AUTH   = ("admin", "admin")
ODL_BASE   = f"http://{ODL_HOST}:{ODL_PORT}/rests/data"

PDP_IP     = "192.168.56.2"        # what the client dials (VM1)
DATA_SUBNET = "10.0.0.0/24"        # expected data-plane subnet
ALLOWED_HOURS = range(0, 24)       # e.g. range(7, 22) for 07:00-21:59 only

# Trust score weights and tier bands (matches T(s,t) from the report)
W_R, W_C, W_B = 0.5, 0.3, 0.2
TIER_FULL, TIER_LIMITED = 70, 40   # >= 70: FULL, >= 40: LIMITED, < 40: DENIED

# Contextual / behavioral penalty magnitudes. Exposed as module globals so the
# sensitivity analysis requested by the reviewers can sweep them via CLI
# without editing the source between runs.
PENALTY_IP = 40          # IP outside the expected data subnet
PENALTY_HOURS = 30       # access outside ALLOWED_HOURS
PENALTY_MAC = 50         # IP/MAC binding mismatch
PENALTY_FAIL = 15        # per failed authentication
PENALTY_FAIL_CAP = 60    # maximum total behavioral penalty

# Base identity score per role
R_BY_ROLE = {"research": 80, "server": 95, "iot": 50, "guest": 30}

# Users -> credentials + role/segment
USERS = {
    "ratih": {"password": "research123", "role": "research"},
    "bima":   {"password": "guest123",    "role": "guest"},
}

# ---------------------------------------------------------------------------
# TOPOLOGY  (4-switch ring; host on port 1 of its own switch)
# Actual Mininet port layout:
#   h1--s1:1, h2--s2:1, h3--s3:1, h4--s4:1
#   s1:2 <-> s2:2
#   s2:3 <-> s3:2
#   s3:3 <-> s4:2
#   s4:3 <-> s1:3
# ---------------------------------------------------------------------------
HOST = {  # ip -> (segment, mac, switch node-id)
    "10.0.0.1": ("research", "00:00:00:00:00:01", "openflow:1"),
    "10.0.0.2": ("server",   "00:00:00:00:00:02", "openflow:2"),
    "10.0.0.3": ("iot",      "00:00:00:00:00:03", "openflow:3"),
    "10.0.0.4": ("guest",    "00:00:00:00:00:04", "openflow:4"),
}
SEG_IP = {seg: ip for ip, (seg, _, _) in HOST.items()}
HOST_PORT = 1  # switch-side port each host sits on

ADJ = {
    "openflow:1": ["openflow:2", "openflow:4"],
    "openflow:2": ["openflow:3", "openflow:1"],
    "openflow:3": ["openflow:4", "openflow:2"],
    "openflow:4": ["openflow:1", "openflow:3"],
}
# egress port on switch A toward switch B
LINK_PORT = {
    # direct upper link: s1-eth2 <-> s2-eth2
    ("openflow:1", "openflow:2"): 2,
    ("openflow:2", "openflow:1"): 2,

    # right link: s2-eth3 <-> s3-eth2
    ("openflow:2", "openflow:3"): 3,
    ("openflow:3", "openflow:2"): 2,

    # lower link: s3-eth3 <-> s4-eth2
    ("openflow:3", "openflow:4"): 3,
    ("openflow:4", "openflow:3"): 2,

    # left link: s4-eth3 <-> s1-eth3
    ("openflow:4", "openflow:1"): 3,
    ("openflow:1", "openflow:4"): 3,
}

# ---------------------------------------------------------------------------
# POLICY  (segment -> {resource-segment: [allowed tcp dst ports]})
#   Guest is fully isolated; IoT may only reach one Server port (telemetry).
# ---------------------------------------------------------------------------
POLICY = {
    "research": {"server": [8080, 9000, 22], "iot": [80]},
    "iot":      {"server": [9000]},
    "guest":    {},
    "server":   {},
}
MIN_SCORE = {"server": TIER_FULL, "iot": TIER_LIMITED, "guest": TIER_LIMITED}
# In the Limited tier we drop the sensitive ports and keep only these:
LIMITED_PORTS = {80, 8080}

# OpenFlow priority hierarchy
# 300+ : reserved for PEP/PDP reachability carve-outs in the Mininet topology
# 250  : temporary per-session ALLOW flows installed by this PDP
# 200  : persistent DEFAULT-DENY flows installed by this PDP
PRIO_SESSION = 250
PRIO_DEFAULT_DENY = 200

# ---------------------------------------------------------------------------
app = Flask(__name__)
SESSION = requests.Session()   # reuse one TCP conn to ODL (much faster)
SESSIONS = {}          # token -> dict(user, role, ip, mac, flows=[...])
_flow_id = 100         # monotonically increasing ODL flow id
DRY_RUN = False

# Performance instrumentation (surfaced via /metrics). Kept intentionally
# dependency-free so it still works in --dry-run and on minimal VMs.
METRICS = {
    "logins": 0,
    "logouts": 0,
    "flows_installed": 0,
    "flows_revoked": 0,
    "last_provision_ms": None,
    "last_revoke_ms": None,
}
FLOW_PUT_MS = []       # rolling per-flow RESTCONF PUT latency, in milliseconds
FLOW_PUT_MS_MAX = 1000


def next_flow_id():
    global _flow_id
    _flow_id += 1
    return str(_flow_id)


# ---------- trust scoring --------------------------------------------------
def context_score(ip, reported_mac):
    score, reasons = 100, []
    try:
        in_subnet = ipaddress.ip_address(ip) in ipaddress.ip_network(DATA_SUBNET)
    except ValueError:
        in_subnet = False
    if not in_subnet:
        score -= PENALTY_IP; reasons.append("ip outside data subnet")
    if datetime.now().hour not in ALLOWED_HOURS:
        score -= PENALTY_HOURS; reasons.append("outside allowed hours")
    expected_mac = HOST.get(ip, (None, None, None))[1]
    if expected_mac and reported_mac and reported_mac.lower() != expected_mac.lower():
        score -= PENALTY_MAC; reasons.append("mac/ip binding mismatch")
    return max(0, score), reasons


def behaviour_score(failed_logins):
    return max(0, 100 - min(failed_logins * PENALTY_FAIL, PENALTY_FAIL_CAP))


def trust_score(role, ip, mac, failed_logins):
    R = R_BY_ROLE.get(role, 0)
    C, reasons = context_score(ip, mac)
    B = behaviour_score(failed_logins)
    T = round(W_R * R + W_C * C + W_B * B, 1)
    tier = "FULL" if T >= TIER_FULL else "LIMITED" if T >= TIER_LIMITED else "DENIED"
    return T, tier, {"R": R, "C": C, "B": B, "reasons": reasons}


# ---------- path + flow building ------------------------------------------
def shortest_path(src_sw, dst_sw):
    """BFS over the ring; returns list of node-ids incl. endpoints."""
    from collections import deque
    q, seen = deque([[src_sw]]), {src_sw}
    while q:
        p = q.popleft()
        if p[-1] == dst_sw:
            return p
        for nb in ADJ[p[-1]]:
            if nb not in seen:
                seen.add(nb); q.append(p + [nb])
    return None


def _flow_json(fid, priority, smac, sip, dip, dport, out_port):
    return {"flow-node-inventory:flow": [{
        "id": fid, "table_id": 0, "priority": priority,
        "match": {
            "ethernet-match": {
                "ethernet-type": {"type": 2048},
                "ethernet-source": {"address": smac},   # binds identity (anti-spoof)
            },
            "ipv4-source": f"{sip}/32",
            "ipv4-destination": f"{dip}/32",
            "ip-match": {"ip-protocol": 6},
            "tcp-destination-port": dport,
        },
        "instructions": {"instruction": [{
            "order": 0,
            "apply-actions": {"action": [{
                "order": 0,
                "output-action": {"output-node-connector": str(out_port)},
            }]},
        }]},
    }]}


def _put_flow(node, fid, body):
    url = (f"{ODL_BASE}/opendaylight-inventory:nodes/node={node}"
           f"/flow-node-inventory:table=0/flow={fid}")
    if DRY_RUN:
        import json
        print(f"PUT {url}\n{json.dumps(body)}\n")
        return True
    started = time.perf_counter()
    try:
        r = SESSION.put(url, json=body, auth=ODL_AUTH,
                        headers={"Content-Type": "application/json"}, timeout=5)
    except requests.RequestException as e:
        print(f"  [PUT {node} flow={fid}] ERROR {e}")
        return False
    FLOW_PUT_MS.append(round((time.perf_counter() - started) * 1000, 2))
    if len(FLOW_PUT_MS) > FLOW_PUT_MS_MAX:
        del FLOW_PUT_MS[:len(FLOW_PUT_MS) - FLOW_PUT_MS_MAX]
    ok = r.status_code in (200, 201, 204)
    if ok:
        METRICS["flows_installed"] += 1
    else:
        print(f"  [PUT {node} flow={fid}] {r.status_code} {r.text[:200]}")
    return ok


def _delete_flow(node, fid):
    url = (f"{ODL_BASE}/opendaylight-inventory:nodes/node={node}"
           f"/flow-node-inventory:table=0/flow={fid}")
    if DRY_RUN:
        print(f"DELETE {url}"); return True
    try:
        SESSION.delete(url, auth=ODL_AUTH, timeout=5)
    except requests.RequestException:
        pass
    return True


def _drop_json(fid, priority, dst_ip):
    """Persistent IPv4 default-deny rule for a protected data-plane host."""
    return {"flow-node-inventory:flow": [{
        "id": str(fid), "table_id": 0, "priority": priority,
        "match": {
            "ethernet-match": {
                "ethernet-type": {"type": 2048},
            },
            "ipv4-destination": f"{dst_ip}/32",
        },
        "instructions": {"instruction": [{
            "order": 0,
            "apply-actions": {"action": [{
                "order": 0,
                "drop-action": {},
            }]},
        }]},
    }]}


def install_default_deny():
    """Install persistent default-deny rules below session ALLOW priority.

    ODL/L2Switch may install lower-priority forwarding rules. These rules keep
    the data plane closed by default; an authenticated session is opened only
    by the more-specific PRIO_SESSION flows and closes again after logout.
    ARP is intentionally not blocked so hosts can still resolve next hops.
    """
    installed = 0
    base_id = 9000
    for sw_idx, node in enumerate(ADJ, start=1):
        for host_idx, dst_ip in enumerate(HOST, start=1):
            fid = str(base_id + sw_idx * 10 + host_idx)
            body = _drop_json(fid, PRIO_DEFAULT_DENY, dst_ip)
            if _put_flow(node, fid, body):
                installed += 1
    print(f"[default-deny] installed/refreshed {installed} rules "
          f"at priority {PRIO_DEFAULT_DENY}")
    return installed


def revoke_existing_sessions(user, ip):
    """Keep only one active access session per identity/IP."""
    stale = [token for token, sess in SESSIONS.items()
             if sess["user"] == user and sess["ip"] == ip]
    revoked = 0
    for token in stale:
        sess = SESSIONS.pop(token)
        for node, fid in sess["flows"]:
            _delete_flow(node, fid)
            revoked += 1
    if stale:
        print(f"[session] auto-revoked {len(stale)} old session(s) "
              f"for {user}@{ip} ({revoked} flows)")
    return revoked


def _install_along(path, dst_sw, smac, sip, dip, port, is_return):
    """Install one directed flow on every switch along `path` (endpoints incl.).
    Forward matches tcp dst-port; return matches tcp src-port. Symmetric because
    the caller passes the same path reversed."""
    installed = []
    for i, node in enumerate(path):
        out_port = HOST_PORT if node == dst_sw else LINK_PORT[(node, path[i + 1])]
        fid = next_flow_id()
        body = _flow_json(fid, PRIO_SESSION, smac, sip, dip, port, out_port)
        if is_return:
            m = body["flow-node-inventory:flow"][0]["match"]
            del m["tcp-destination-port"]
            m["tcp-source-port"] = port
        if _put_flow(node, fid, body):
            installed.append((node, fid))
    return installed


def provision_session(client_ip, client_mac, res_ip, port):
    """Provision both directions for client<->resource:port over one shortest
    path (return leg is the forward path reversed => symmetric routing)."""
    c_sw, r_sw = HOST[client_ip][2], HOST[res_ip][2]
    path = shortest_path(c_sw, r_sw)
    fwd = _install_along(path, r_sw, client_mac,
                         client_ip, res_ip, port, is_return=False)
    ret = _install_along(path[::-1], c_sw, HOST[res_ip][1],
                         res_ip, client_ip, port, is_return=True)
    return fwd + ret, path


def install_carveout():
    """High-priority flow so any data host can always reach the PDP portal.
    Installed on s1 (research host's switch) toward the root-ns/NAT gateway."""
    fid = next_flow_id()
    body = {"flow-node-inventory:flow": [{
        "id": fid, "table_id": 0, "priority": 200,
        "match": {"ethernet-match": {"ethernet-type": {"type": 2048}},
                  "ipv4-destination": f"{PDP_IP}/32"},
        "instructions": {"instruction": [{"order": 0, "apply-actions": {"action": [{
            "order": 0, "output-action": {"output-node-connector": "NORMAL"}}]}}]},
    }]}
    _put_flow("openflow:1", fid, body)
    print(f"[carveout] host -> PDP ({PDP_IP}) reachability flow installed on openflow:1")


# ---------- decision -------------------------------------------------------
def evaluate(role, ip, mac, T, tier):
    """Return {resource: {ports:[...], path:[...]}} that gets provisioned."""
    granted = {}
    for dst_seg, ports in POLICY.get(role, {}).items():
        if T < MIN_SCORE.get(dst_seg, TIER_FULL):
            continue
        allow = ports if tier == "FULL" else [p for p in ports if p in LIMITED_PORTS]
        if not allow:
            continue
        granted[dst_seg] = {"ports": allow}
    return granted


# ---------- HTTP -----------------------------------------------------------
FAILED = {}  # user -> count


@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(force=True, silent=True) or {}
    user = data.get("username", "")
    pw   = data.get("password", "")
    mac  = data.get("mac", "")
    ip   = request.remote_addr
    if ip not in HOST:                      # masquerade fallback: trust reported ip
        ip = data.get("ip", ip)

    rec = USERS.get(user)
    if not rec or rec["password"] != pw:
        FAILED[user] = FAILED.get(user, 0) + 1
        return jsonify(ok=False, error="invalid credentials"), 401

    role = rec["role"]
    T, tier, parts = trust_score(role, ip, mac, FAILED.get(user, 0))
    if tier == "DENIED":
        return jsonify(ok=False, tier=tier, trust=T, detail=parts,
                       error="trust below threshold"), 403

    # Prevent overlapping/stale ALLOW rules for the same host.
    revoke_existing_sessions(user, ip)

    granted = evaluate(role, ip, mac, T, tier)
    token = uuid.uuid4().hex[:12]
    flows = []
    t0 = time.perf_counter()
    for dst_seg, info in granted.items():
        dst_ip = SEG_IP[dst_seg]
        info["ip"] = dst_ip
        path = None
        for port in info["ports"]:
            session_flows, path = provision_session(ip, mac, dst_ip, port)
            flows += session_flows
        info["path"] = path
    provision_ms = round((time.perf_counter() - t0) * 1000, 1)
    METRICS["logins"] += 1
    METRICS["last_provision_ms"] = provision_ms
    print(f"[login] {user} ({role}) T={T} {tier} -> {len(flows)} flows in "
          f"{provision_ms:.1f} ms : {', '.join(granted) or 'none'}")
    SESSIONS[token] = {"user": user, "role": role, "ip": ip, "mac": mac,
                       "flows": flows, "ts": time.time()}
    return jsonify(ok=True, token=token, role=role, trust=T, tier=tier,
                   detail=parts, granted=granted, provision_ms=provision_ms)


@app.route("/logout", methods=["POST"])
def logout():
    data = request.get_json(force=True, silent=True) or {}
    sess = SESSIONS.pop(data.get("token", ""), None)
    if not sess:
        return jsonify(ok=False, error="unknown token"), 404
    started = time.perf_counter()
    for node, fid in sess["flows"]:
        _delete_flow(node, fid)
    revoke_ms = round((time.perf_counter() - started) * 1000, 1)
    METRICS["logouts"] += 1
    METRICS["flows_revoked"] += len(sess["flows"])
    METRICS["last_revoke_ms"] = revoke_ms
    return jsonify(ok=True, revoked=len(sess["flows"]), revoke_ms=revoke_ms)


@app.route("/health")
def health():
    return jsonify(ok=True, sessions=len(SESSIONS))


@app.route("/metrics")
def metrics():
    samples = sorted(FLOW_PUT_MS)

    def percentile(p):
        if not samples:
            return None
        index = min(len(samples) - 1, round((p / 100.0) * (len(samples) - 1)))
        return samples[index]

    return jsonify(
        sessions=len(SESSIONS),
        logins=METRICS["logins"],
        logouts=METRICS["logouts"],
        flows_installed=METRICS["flows_installed"],
        flows_revoked=METRICS["flows_revoked"],
        last_provision_ms=METRICS["last_provision_ms"],
        last_revoke_ms=METRICS["last_revoke_ms"],
        flow_put_samples=len(samples),
        flow_put_ms_avg=round(sum(samples) / len(samples), 2) if samples else None,
        flow_put_ms_p50=percentile(50),
        flow_put_ms_p95=percentile(95),
        flow_put_ms_max=samples[-1] if samples else None,
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print RESTCONF JSON instead of calling ODL")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--full-threshold", type=float, default=TIER_FULL,
                    help="min trust score for FULL tier")
    ap.add_argument("--limited-threshold", type=float, default=TIER_LIMITED,
                    help="min trust score for LIMITED tier")
    ap.add_argument("--w-r", type=float, default=W_R,
                    help="weight for role/identity factor R (default 0.5)")
    ap.add_argument("--w-c", type=float, default=W_C,
                    help="weight for contextual factor C (default 0.3)")
    ap.add_argument("--w-b", type=float, default=W_B,
                    help="weight for behavioral factor B (default 0.2)")
    ap.add_argument("--penalty-ip", type=float, default=PENALTY_IP,
                    help="context penalty for IP outside subnet (default 40)")
    ap.add_argument("--penalty-hours", type=float, default=PENALTY_HOURS,
                    help="context penalty for outside allowed hours (default 30)")
    ap.add_argument("--penalty-mac", type=float, default=PENALTY_MAC,
                    help="context penalty for IP/MAC mismatch (default 50)")
    ap.add_argument("--penalty-fail", type=float, default=PENALTY_FAIL,
                    help="behavioral penalty per failed login (default 15)")
    ap.add_argument("--penalty-fail-cap", type=float, default=PENALTY_FAIL_CAP,
                    help="maximum behavioral penalty (default 60)")
    ap.add_argument("--allowed-hours", default="0-24", metavar="START-END",
                    help="allowed access hours as START-END (default 0-24). An "
                         "empty range such as 1-1 forces the outside-hours "
                         "penalty, which is useful to reach LIMITED without "
                         "spoofing the MAC")
    args = ap.parse_args()
    DRY_RUN = args.dry_run

    # override threshold + resync MIN_SCORE (dict-nya dihitung sekali di atas,
    # jadi harus di-update manual di sini kalau threshold-nya diganti)
    TIER_FULL, TIER_LIMITED = args.full_threshold, args.limited_threshold
    MIN_SCORE["server"] = TIER_FULL
    MIN_SCORE["iot"] = TIER_LIMITED
    MIN_SCORE["guest"] = TIER_LIMITED

    try:
        _hour_start, _hour_end = (int(part) for part in args.allowed_hours.split("-", 1))
    except ValueError:
        raise SystemExit(f"invalid --allowed-hours {args.allowed_hours!r}; use START-END")
    ALLOWED_HOURS = range(_hour_start, _hour_end)

    # Weights for T = wR*R + wC*C + wB*B. Configurable so the assignment can be
    # swept for the sensitivity analysis requested by the reviewers, without
    # editing the source between runs. Rebinding these module-level names also
    # updates trust_score(), which reads them as globals at call time.
    weight_sum = args.w_r + args.w_c + args.w_b
    if abs(weight_sum - 1.0) > 1e-6:
        raise SystemExit(
            f"weights must sum to 1.0 (got {weight_sum:.3f}): "
            f"w-r={args.w_r}, w-c={args.w_c}, w-b={args.w_b}"
        )
    for name, value in (("w-r", args.w_r), ("w-c", args.w_c), ("w-b", args.w_b)):
        if not 0.0 <= value <= 1.0:
            raise SystemExit(f"weight {name} must be within [0, 1] (got {value})")
    W_R, W_C, W_B = args.w_r, args.w_c, args.w_b

    PENALTY_IP = args.penalty_ip
    PENALTY_HOURS = args.penalty_hours
    PENALTY_MAC = args.penalty_mac
    PENALTY_FAIL = args.penalty_fail
    PENALTY_FAIL_CAP = args.penalty_fail_cap

    print(f"[config] T = {W_R:g}R + {W_C:g}C + {W_B:g}B | "
          f"FULL>={TIER_FULL:g}, LIMITED>={TIER_LIMITED:g}, DENIED<{TIER_LIMITED:g}")
    print(f"[config] penalties: ip={PENALTY_IP:g} hours={PENALTY_HOURS:g} "
          f"mac={PENALTY_MAC:g} fail={PENALTY_FAIL:g} cap={PENALTY_FAIL_CAP:g}")
    print(f"[config] allowed hours: {_hour_start}-{_hour_end}")

    # Closed-by-default baseline; session ALLOW rules have higher priority.
    install_default_deny()

    app.run(host="0.0.0.0", port=args.port)