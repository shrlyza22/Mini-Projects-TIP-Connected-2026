"""
ODL Dashboard v4 — Topology + Flow Inspector (generic layout)
===============================================================
Dashboard pengganti DLUX untuk testbed SDN — dipakai baik untuk:
  1) Testing topology & flow biasa (seperti Week 2, host-tracker AKTIF).
  2) Testing skenario ZTNA (odl-l2switch-switch di-uninstall,
     host-tracker NONAKTIF, PDP yang memasang flow secara manual).

Perbedaan dari v3:
  - getNodePosition() yang di-hardcode untuk openflow:1..4 + 4 host
    tetap DIHAPUS. Diganti computeLayout() di frontend yang:
      * Menyusun SEMUA switch dalam bentuk ring, berapa pun jumlahnya
        (tidak dihardcode 4).
      * Menyusun host/placeholder ("?") berdasarkan switch tempat dia
        nempel, disebar (fan-out) di sekeliling switch itu.
      * Node yang tidak diketahui switch induknya disebar di ring luar
        alih-alih ditumpuk di (0,0).
    Ini yang jadi akar bug "semua garis numpuk ke satu titik s1-eth4"
    di v3: setiap placeholder host yang tidak ada di daftar hardcode
    otomatis jatuh ke fallback {x:0, y:0} dan saling menumpuk.
  - hostnote diperjelas: nonaktifnya host-tracker itu WAJAR saat lagi
    testing ZTNA (karena odl-l2switch-switch sengaja di-uninstall),
    tapi jadi tanda ada yang salah kalau lagi testing topology/flow
    biasa ala Week 2.
  - Backend (parsing topology/flow) tidak diubah — sudah benar sejak
    v3, bug murni ada di layout frontend.

Jalankan:
    pip install flask requests
    python3 app.py

Buka:
    http://<IP-VM>:5000
"""

from __future__ import annotations

import time
from typing import Any

import requests
from flask import Flask, jsonify, render_template_string, request
from requests.auth import HTTPBasicAuth


app = Flask(__name__)

# ============================================================
# KONFIGURASI
# ============================================================

ODL_IP = "192.168.56.2"
ODL_PORT = 8181
ODL_USER = "admin"
ODL_PASS = "admin"

BASE_URL = f"http://{ODL_IP}:{ODL_PORT}"
AUTH = HTTPBasicAuth(ODL_USER, ODL_PASS)

TOPO_URL = f"{BASE_URL}/rests/data/network-topology:network-topology"
INV_URL = f"{BASE_URL}/rests/data/opendaylight-inventory:nodes"

REQUEST_TIMEOUT = 6


# ============================================================
# HELPER
# ============================================================

def pick(data: Any, *names: str, default: Any = None) -> Any:
    """RESTCONF kadang mengembalikan key dengan prefix module, kadang
    tanpa prefix. Fungsi ini mencoba seluruh variasi."""
    if not isinstance(data, dict):
        return default
    for name in names:
        if name in data:
            return data[name]
    for key, value in data.items():
        if ":" in key and key.split(":", 1)[1] in names:
            return value
    return default


def as_list(value: Any) -> list:
    """Pastikan nilai selalu berupa list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def odl_get(url: str, params: dict[str, str] | None = None) -> dict:
    """GET RESTCONF OpenDaylight dan return JSON."""
    response = requests.get(
        url,
        auth=AUTH,
        params=params,
        headers={
            "Accept": "application/yang-data+json",
            "Cache-Control": "no-cache",
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def get_flow_topologies(data: dict) -> list[dict]:
    """Ambil hanya topology flow:1 agar tidak mencampur topology lain."""
    root = pick(data, "network-topology:network-topology", "network-topology", default={})
    topologies = as_list(pick(root, "topology", default=[]))
    flow_topologies = [
        t for t in topologies
        if str(pick(t, "topology-id", default="")) == "flow:1"
    ]
    # Fallback jika versi ODL tidak menyertakan topology-id sesuai harapan.
    return flow_topologies or topologies


# ============================================================
# FLOW PARSING
# ============================================================

def summarize_match(match: dict) -> str:
    """Ubah struktur match YANG menjadi satu baris ringkas."""
    if not match:
        return "*"

    parts: list[str] = []

    in_port = pick(match, "in-port")
    if in_port:
        parts.append(f"in={str(in_port).split(':')[-1]}")

    eth_match = pick(match, "ethernet-match", default={})
    eth_src = pick(pick(eth_match, "ethernet-source", default={}), "address")
    eth_dst = pick(pick(eth_match, "ethernet-destination", default={}), "address")
    eth_type = pick(pick(eth_match, "ethernet-type", default={}), "type")

    if eth_src:
        parts.append(f"dl_src={eth_src}")
    if eth_dst:
        parts.append(f"dl_dst={eth_dst}")

    if eth_type is not None:
        try:
            eth_type_int = int(eth_type)
            eth_names = {2048: "ip", 2054: "arp", 34525: "ipv6", 35020: "lldp"}
            parts.append(eth_names.get(eth_type_int, f"eth_type=0x{eth_type_int:04x}"))
        except (TypeError, ValueError):
            parts.append(f"eth_type={eth_type}")

    ipv4_src = pick(match, "ipv4-source")
    ipv4_dst = pick(match, "ipv4-destination")
    if ipv4_src:
        parts.append(f"nw_src={ipv4_src}")
    if ipv4_dst:
        parts.append(f"nw_dst={ipv4_dst}")

    ip_match = pick(match, "ip-match", default={})
    protocol = pick(ip_match, "ip-protocol")
    if protocol is not None:
        try:
            protocol_int = int(protocol)
            protocol_names = {1: "icmp", 6: "tcp", 17: "udp"}
            parts.append(protocol_names.get(protocol_int, f"proto={protocol_int}"))
        except (TypeError, ValueError):
            parts.append(f"proto={protocol}")

    for key, label in (
        ("tcp-source-port", "tp_src"),
        ("tcp-destination-port", "tp_dst"),
        ("udp-source-port", "tp_src"),
        ("udp-destination-port", "tp_dst"),
    ):
        value = pick(match, key)
        if value is not None:
            parts.append(f"{label}={value}")

    return ", ".join(parts) if parts else "*"


def summarize_actions(instructions: dict) -> tuple[str, str, list[str]]:
    """Return (teks action, verdict allow/drop/punt, daftar output port)."""
    actions: list[str] = []
    output_ports: list[str] = []

    for instruction in as_list(pick(instructions, "instruction", default=[])):
        apply_actions = pick(instruction, "apply-actions", default={})
        action_list = as_list(pick(apply_actions, "action", default=[]))

        if not action_list and "apply-actions" in str(instruction):
            actions.append("drop")

        for action in action_list:
            if pick(action, "drop-action") is not None:
                actions.append("drop")

            output_action = pick(action, "output-action", default=None)
            if output_action is not None:
                port = str(pick(output_action, "output-node-connector", default="?"))
                actions.append(f"output:{port}")
                output_ports.append(port)

            if pick(action, "set-field") is not None:
                actions.append("set_field")

        go_to_table = pick(instruction, "go-to-table", default=None)
        if go_to_table is not None:
            table_id = pick(go_to_table, "table_id", "table-id")
            actions.append(f"goto_table:{table_id}")

    if not actions:
        actions = ["drop"]

    action_text = ", ".join(actions)
    lowered = action_text.lower()

    if "controller" in lowered:
        verdict = "punt"
    elif "output" in lowered:
        verdict = "allow"
    else:
        verdict = "drop"

    return action_text, verdict, output_ports


# ============================================================
# API: FLOWS
# ============================================================

@app.route("/api/flows")
def get_flows():
    """Ambil seluruh flow dari tiap switch.

    Query: ?store=operational (default) atau ?store=config
    """
    store = request.args.get("store", "operational")
    content = "config" if store == "config" else "nonconfig"

    try:
        data = odl_get(INV_URL, params={"content": content})
    except Exception as error:
        return jsonify({"error": str(error), "flows": [], "switches": []}), 200

    flows: list[dict] = []
    switches: list[dict] = []

    root = pick(data, "opendaylight-inventory:nodes", "nodes", default={})

    for node in as_list(pick(root, "node", default=[])):
        node_id = pick(node, "id")
        if not node_id or not str(node_id).startswith("openflow:"):
            continue

        port_map: dict[str, str] = {}
        for connector in as_list(pick(node, "node-connector", default=[])):
            connector_id = str(pick(connector, "id", default=""))
            connector_name = pick(connector, "name", default=None)
            if connector_id:
                port_number = connector_id.split(":")[-1]
                port_map[port_number] = connector_name or port_number

        switch_flow_count = 0

        for table in as_list(pick(node, "table", default=[])):
            table_id = pick(table, "id", default=0)

            for flow in as_list(pick(table, "flow", default=[])):
                stats = pick(flow, "flow-statistics", default={}) or {}
                duration = pick(stats, "duration", default={}) or {}

                action_text, verdict, output_ports = summarize_actions(
                    pick(flow, "instructions", default={})
                )

                match = pick(flow, "match", default={})
                in_port_raw = pick(match, "in-port")

                flows.append({
                    "switch": str(node_id),
                    "table": table_id,
                    "flow_id": str(pick(flow, "id", default="-")),
                    "priority": pick(flow, "priority", default=0),
                    "match": summarize_match(match),
                    "action": action_text,
                    "verdict": verdict,
                    "in_port": str(in_port_raw).split(":")[-1] if in_port_raw else None,
                    "out_ports": [p.split(":")[-1] for p in output_ports],
                    "packets": pick(stats, "packet-count", default=None),
                    "bytes": pick(stats, "byte-count", default=None),
                    "seconds": pick(duration, "second", default=None),
                    "idle_timeout": pick(flow, "idle-timeout", default=None),
                    "hard_timeout": pick(flow, "hard-timeout", default=None),
                })

                switch_flow_count += 1

        switches.append({"id": str(node_id), "flow_count": switch_flow_count, "ports": port_map})

    flows.sort(key=lambda f: (f["switch"], f["table"], -int(f["priority"] or 0)))

    summary = {
        "total": len(flows),
        "allow": sum(1 for f in flows if f["verdict"] == "allow"),
        "drop": sum(1 for f in flows if f["verdict"] == "drop"),
        "punt": sum(1 for f in flows if f["verdict"] == "punt"),
        "store": store,
    }

    return jsonify({"flows": flows, "switches": switches, "summary": summary})


# ============================================================
# API: TOPOLOGY
# ============================================================

@app.route("/api/topology")
def get_topology():
    try:
        data = odl_get(TOPO_URL, params={"content": "nonconfig"})
    except Exception as error:
        return jsonify({"error": str(error)}), 200

    flow_counts: dict[str, int] = {}
    port_names: dict[str, str] = {}

    try:
        inventory = odl_get(INV_URL, params={"content": "nonconfig"})
        inventory_root = pick(inventory, "opendaylight-inventory:nodes", "nodes", default={})

        for node in as_list(pick(inventory_root, "node", default=[])):
            node_id = pick(node, "id")
            if not node_id or not str(node_id).startswith("openflow:"):
                continue

            flow_counts[str(node_id)] = sum(
                len(as_list(pick(table, "flow", default=[])))
                for table in as_list(pick(node, "table", default=[]))
            )

            for connector in as_list(pick(node, "node-connector", default=[])):
                connector_id = str(pick(connector, "id", default=""))
                connector_name = pick(connector, "name", default=None)
                if connector_id and connector_name:
                    port_names[connector_id] = connector_name

    except Exception:
        # Inventory tambahan gagal tidak boleh mematikan topology.
        pass

    nodes: list[dict] = []
    edges: list[dict] = []

    topology_list = get_flow_topologies(data)

    raw_nodes: list[dict] = []
    for topology in topology_list:
        raw_nodes.extend(as_list(pick(topology, "node", default=[])))

    seen_hosts = any(
        str(pick(node, "node-id", default="")).startswith("host:")
        for node in raw_nodes
    )

    # Port yang sudah dipakai link antar-switch.
    link_termination_points: set[str] = set()
    for topology in topology_list:
        for link in as_list(pick(topology, "link", default=[])):
            source_tp = pick(pick(link, "source", default={}), "source-tp")
            dest_tp = pick(pick(link, "destination", default={}), "dest-tp")
            if source_tp:
                link_termination_points.add(str(source_tp))
            if dest_tp:
                link_termination_points.add(str(dest_tp))

    # Node switch dan host.
    for node in raw_nodes:
        node_id = str(pick(node, "node-id", default=""))

        if node_id.startswith("openflow:"):
            flow_count = flow_counts.get(node_id, 0)
            nodes.append({
                "id": node_id,
                "label": f"{node_id}\n{flow_count} flow",
                "shape": "box",
                "group": "switch",
            })

            # Fallback jika host-tracker belum aktif (mis. saat ZTNA
            # dengan odl-l2switch-switch di-uninstall): tampilkan port
            # yang aktif sebagai node "?" agar tetap kelihatan ada
            # perangkat nempel di situ, meski identitasnya belum jelas.
            if not seen_hosts:
                for tp in as_list(pick(node, "termination-point", default=[])):
                    tp_id = str(pick(tp, "tp-id", default=""))

                    if not tp_id or tp_id.endswith(":LOCAL") or tp_id in link_termination_points:
                        continue

                    port = tp_id.split(":")[-1]
                    interface_name = port_names.get(tp_id, f"port {port}")
                    placeholder_id = f"edge-{tp_id}"

                    nodes.append({
                        "id": placeholder_id,
                        "label": f"?\n{interface_name}",
                        "title": (
                            f"Belum diketahui — port {interface_name} aktif "
                            "tetapi host-tracker belum mengenali host"
                        ),
                        "shape": "ellipse",
                        "group": "unknown",
                    })

                    edges.append({
                        "from": node_id,
                        "to": placeholder_id,
                        "group": "host-link",
                        "dashes": True,
                    })

        elif node_id.startswith("host:"):
            address_root = pick(node, "host-tracker-service:addresses", "addresses", default={})
            addresses = as_list(pick(address_root, "addr", default=[]))
            ip_address = next(
                (pick(a, "ip") for a in addresses if pick(a, "ip")),
                None,
            )
            mac_address = pick(
                node, "host-tracker-service:id", "id",
                default=node_id.replace("host:", ""),
            )

            nodes.append({
                "id": node_id,
                "label": str(ip_address or mac_address),
                "title": f"MAC {mac_address}",
                "shape": "ellipse",
                "group": "host",
            })

            attachment_root = pick(
                node, "host-tracker-service:attachment-points",
                "attachment-points", default=[],
            )

            for attachment_point in as_list(attachment_root):
                tp = str(pick(attachment_point, "tp-id", default=""))
                switch_id = ":".join(tp.split(":")[:2])
                if switch_id:
                    edges.append({"from": node_id, "to": switch_id, "group": "host-link"})

    # Link antar-switch.
    for topology in topology_list:
        for link in as_list(pick(topology, "link", default=[])):
            source = pick(pick(link, "source", default={}), "source-node")
            destination = pick(pick(link, "destination", default={}), "dest-node")

            if (
                source and destination and source != destination
                and not str(source).startswith("host:")
                and not str(destination).startswith("host:")
            ):
                edges.append({"from": str(source), "to": str(destination), "group": "trunk"})

    return jsonify({"nodes": nodes, "edges": edges, "host_tracking": seen_hosts})


# ============================================================
# API: TEST CONNECTION
# ============================================================

@app.route("/api/test-connection")
def test_connection():
    start_time = time.time()

    try:
        response = requests.get(
            TOPO_URL,
            auth=AUTH,
            params={"content": "nonconfig"},
            headers={"Accept": "application/yang-data+json", "Cache-Control": "no-cache"},
            timeout=5,
        )
        elapsed = round((time.time() - start_time) * 1000, 1)

        if response.status_code == 200:
            return jsonify({
                "status": "connected",
                "message": f"Controller aktif di {ODL_IP}:{ODL_PORT}",
                "latency_ms": elapsed,
            })

        if response.status_code == 401:
            return jsonify({
                "status": "error",
                "message": "Autentikasi ditolak — cek username/password Karaf",
            })

        return jsonify({
            "status": "error",
            "message": f"Controller membalas HTTP {response.status_code}",
        })

    except requests.exceptions.ConnectionError:
        return jsonify({
            "status": "disconnected",
            "message": f"Tidak ada jawaban dari {ODL_IP}:{ODL_PORT} — pastikan Karaf sudah berjalan",
        })

    except requests.exceptions.Timeout:
        return jsonify({
            "status": "timeout",
            "message": "Controller tidak merespons dalam 5 detik",
        })


# ============================================================
# FRONTEND
# ============================================================

HTML_PAGE = """
<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ODL Dashboard — Topology &amp; Flows</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  :root {
    --bg: #0b0f14; --panel: #121821; --line: #1f2937; --text: #e6edf3;
    --muted: #7d8b9c; --allow: #2dd4a7; --drop: #f4587a; --punt: #f5b544; --sw: #4c8dff;
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 20px; background: var(--bg); color: var(--text);
         font: 14px/1.5 ui-sans-serif, system-ui, sans-serif; }
  header { display: flex; align-items: baseline; gap: 14px; margin-bottom: 16px; flex-wrap: wrap; }
  h1 { font-size: 17px; margin: 0; letter-spacing: .2px; }
  .sub { color: var(--muted); font-size: 12px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  @media (max-width: 980px) { .grid { grid-template-columns: 1fr; } }
  .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px; }
  .panel h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); margin: 0 0 10px; }
  .bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
  button, select { background: #1b2431; color: var(--text); border: 1px solid var(--line);
                   padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 13px; }
  button:hover { border-color: #3a4a60; }
  button:focus-visible, select:focus-visible { outline: 2px solid var(--sw); outline-offset: 1px; }
  #status { padding: 5px 10px; border-radius: 6px; font-size: 13px; border: 1px solid var(--line); }
  .s-connected { color: var(--allow); border-color: var(--allow) !important; }
  .s-disconnected, .s-error { color: var(--drop); border-color: var(--drop) !important; }
  .s-timeout { color: var(--punt); border-color: var(--punt) !important; }
  #network { height: 520px; border-radius: 8px; background: #0d131b; border: 1px solid var(--line); }
  .stats { display: flex; gap: 16px; margin-bottom: 10px; }
  .stat b { display: block; font-size: 20px; font-variant-numeric: tabular-nums; }
  .stat span { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; }
  .allow b { color: var(--allow); } .drop b { color: var(--drop); } .punt b { color: var(--punt); }
  .tablewrap { max-height: 480px; overflow: auto; border: 1px solid var(--line); border-radius: 8px; }
  table { width: 100%; border-collapse: collapse; font: 12px/1.4 ui-monospace, "SF Mono", Menlo, monospace; }
  th { position: sticky; top: 0; background: #161e29; text-align: left; padding: 8px; color: var(--muted);
       font-weight: 500; text-transform: uppercase; font-size: 10px; letter-spacing: .06em; }
  td { padding: 7px 8px; border-top: 1px solid var(--line); vertical-align: top; }
  tbody tr:hover { background: #172030; cursor: pointer; }
  tbody tr.sel { background: #1d2a3d; }
  .tag { padding: 1px 7px; border-radius: 999px; font-size: 10px; font-weight: 600; letter-spacing: .04em; }
  .tag.allow { background: rgba(45, 212, 167, .15); color: var(--allow); }
  .tag.drop { background: rgba(244, 88, 122, .15); color: var(--drop); }
  .tag.punt { background: rgba(245, 181, 68, .15); color: var(--punt); }
  .muted { color: var(--muted); }
  .note { font-size: 12px; color: var(--muted); margin-top: 8px; }
</style>
</head>
<body>

<header>
  <h1>ODL Dashboard</h1>
  <span class="sub">controller {{odl}}</span>
  <span id="status">belum dicek</span>
  <span class="sub" id="latency"></span>
</header>

<div class="bar">
  <button onclick="refreshAll()">Refresh sekarang</button>
  <label class="sub">
    <input type="checkbox" id="auto" checked onchange="toggleAuto()">
    auto-refresh 5s
  </label>
  <select id="store" onchange="loadFlows()">
    <option value="operational">Flow terpasang (operational)</option>
    <option value="config">Flow yang dikirim (config)</option>
  </select>
  <span class="sub" id="hostnote"></span>
</div>

<div class="grid">
  <div class="panel">
    <h2>Topology</h2>
    <div id="network"></div>
    <div class="note">
      Kotak biru = switch (angka = jumlah flow). Garis tebal = link antar-switch.
      Oval hijau = host yang dikenali host-tracker.
      Oval abu "?" = port aktif tetapi identitas host belum diketahui
      (normal saat testing ZTNA, karena odl-l2switch-switch sengaja
      di-uninstall dan flow dipasang manual oleh PDP).
    </div>
  </div>

  <div class="panel">
    <h2>Flow table</h2>
    <div class="stats">
      <div class="stat allow"><b id="cAllow">0</b><span>allow</span></div>
      <div class="stat drop"><b id="cDrop">0</b><span>drop</span></div>
      <div class="stat punt"><b id="cPunt">0</b><span>ke controller</span></div>
      <div class="stat"><b id="cTotal">0</b><span>total</span></div>
    </div>
    <div class="tablewrap">
      <table>
        <thead>
          <tr>
            <th>Switch</th><th>Tbl</th><th>Prio</th><th>Match</th>
            <th>Action</th><th>Hasil</th><th>Pkt</th><th>Umur</th>
          </tr>
        </thead>
        <tbody id="flowBody">
          <tr><td colspan="8" class="muted">Memuat…</td></tr>
        </tbody>
      </table>
    </div>
    <div class="note">Klik baris untuk menyorot switch terkait di topology.</div>
  </div>
</div>

<script>
let network = null;
let nodesDS = null;
let timer = null;
let lastFlows = [];
let topologyRefreshCounter = 0;

/* ------------------------------------------------------------
   LAYOUT GENERIK (pengganti getNodePosition yang di-hardcode)
   ------------------------------------------------------------
   - Switch disusun melingkar (ring), jumlahnya berapa pun.
   - Host/placeholder "?" ditempatkan mengelilingi switch tempat
     dia nempel (fan-out), bukan ditumpuk di satu titik.
   - Node yang switch induknya tidak ketemu disebar di ring luar,
     bukan jatuh ke default (0,0).
   ------------------------------------------------------------ */

function switchSortKey(id) {
  const match = id.match(/(\\d+)$/);
  return match ? parseInt(match[1], 10) : 0;
}

function computeLayout(nodes, edges) {
  const positions = {};

  const switches = nodes
    .filter(n => n.group === 'switch')
    .slice()
    .sort((a, b) => switchSortKey(a.id) - switchSortKey(b.id));

  const others = nodes.filter(n => n.group !== 'switch');

  const ringRadius = 260;
  const switchCount = Math.max(switches.length, 1);

  switches.forEach((sw, i) => {
    const angle = (2 * Math.PI * i) / switchCount - Math.PI / 2;
    positions[sw.id] = {
      x: Math.round(ringRadius * Math.cos(angle)),
      y: Math.round(ringRadius * Math.sin(angle)),
    };
  });

  // Kelompokkan host/placeholder berdasarkan switch tempat dia nempel.
  const groupBySwitch = {};
  others.forEach(node => {
    const edge = edges.find(e =>
      (e.from === node.id && positions[e.to]) ||
      (e.to === node.id && positions[e.from])
    );
    const switchId = edge
      ? (positions[edge.to] ? edge.to : edge.from)
      : '__unassigned__';

    if (!groupBySwitch[switchId]) groupBySwitch[switchId] = [];
    groupBySwitch[switchId].push(node.id);
  });

  const outerRadius = ringRadius + 170;

  Object.entries(groupBySwitch).forEach(([switchId, childIds]) => {
    const base = positions[switchId];
    const count = childIds.length;

    if (!base) {
      // Tidak ketemu switch induknya: sebar di ring terluar,
      // BUKAN ditumpuk di (0,0).
      childIds.forEach((id, i) => {
        const angle = (2 * Math.PI * i) / count;
        positions[id] = {
          x: Math.round((outerRadius + 60) * Math.cos(angle)),
          y: Math.round((outerRadius + 60) * Math.sin(angle)),
        };
      });
      return;
    }

    const baseAngle = Math.atan2(base.y, base.x);
    const spread = 0.55; // jarak sudut antar node sejenis (radian)

    childIds.forEach((id, i) => {
      const offsetAngle = baseAngle + (i - (count - 1) / 2) * spread;
      positions[id] = {
        x: Math.round(outerRadius * Math.cos(offsetAngle)),
        y: Math.round(outerRadius * Math.sin(offsetAngle)),
      };
    });
  });

  return positions;
}

/* ------------------------------------------------------------
   DEDUPLIKASI EDGE
   ------------------------------------------------------------ */

function deduplicateEdges(edges) {
  const seen = new Set();
  return edges.filter(edge => {
    const key = [String(edge.from), String(edge.to)].sort().join('::');
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/* ------------------------------------------------------------
   VIS NETWORK OPTIONS
   ------------------------------------------------------------ */

const visOptions = {
  physics: { enabled: false },
  layout: { improvedLayout: false },
  interaction: { hover: true, dragNodes: true, dragView: true, zoomView: true },
  nodes: { font: { color: '#e6edf3', size: 12, multi: false }, borderWidth: 2 },
  edges: { color: { color: '#2b3948', highlight: '#4c8dff' }, width: 1.5, smooth: false },
  groups: {
    switch: { color: { background: '#1b3459', border: '#4c8dff' } },
    host: { color: { background: '#123830', border: '#2dd4a7' }, font: { color: '#cfe9e1' } },
    unknown: { color: { background: '#2a3342', border: '#4b5a70' }, font: { color: '#9fb0c4', size: 11 } },
  }
};

/* ------------------------------------------------------------
   CONNECTION STATUS
   ------------------------------------------------------------ */

async function testConnection() {
  const statusElement = document.getElementById('status');
  const latencyElement = document.getElementById('latency');
  try {
    const response = await fetch('/api/test-connection', { cache: 'no-store' });
    const data = await response.json();
    statusElement.textContent = data.message;
    statusElement.className = 's-' + data.status;
    latencyElement.textContent = data.latency_ms ? data.latency_ms + ' ms' : '';
  } catch (error) {
    statusElement.textContent = 'Backend dashboard tidak merespons';
    statusElement.className = 's-error';
    latencyElement.textContent = '';
  }
}

/* ------------------------------------------------------------
   LOAD TOPOLOGY
   ------------------------------------------------------------ */

async function loadTopology() {
  const hostNote = document.getElementById('hostnote');

  try {
    const response = await fetch('/api/topology', { cache: 'no-store' });
    const data = await response.json();

    if (data.error) {
      hostNote.textContent = 'Topology: ' + data.error;
      return;
    }

    hostNote.textContent = data.host_tracking
      ? 'host-tracker aktif'
      : 'host-tracker nonaktif — normal saat testing ZTNA (odl-l2switch-switch di-uninstall); kalau testing topology/flow biasa, aktifkan odl-l2switch-switch lalu jalankan pingall';

    const uniqueEdges = deduplicateEdges(data.edges);
    const positions = computeLayout(data.nodes, uniqueEdges);

    const positionedNodes = data.nodes.map(node => {
      const position = positions[node.id] || { x: 0, y: 0 };
      return { ...node, x: position.x, y: position.y, physics: false, fixed: { x: false, y: false } };
    });

    const graphEdges = uniqueEdges.map((edge, index) => ({
      id: `edge-${index}`,
      ...edge,
      width: edge.group === 'trunk' ? 3 : 1.5,
      color: edge.group === 'trunk'
        ? { color: '#4c6fa8', highlight: '#6f99ff' }
        : { color: '#2b3948', highlight: '#4c8dff' },
      dashes: Boolean(edge.dashes),
    }));

    nodesDS = new vis.DataSet(positionedNodes);
    const edgesDS = new vis.DataSet(graphEdges);
    const container = document.getElementById('network');

    if (network) network.destroy();
    network = new vis.Network(container, { nodes: nodesDS, edges: edgesDS }, visOptions);
    network.fit({ animation: { duration: 400, easingFunction: 'easeInOutQuad' } });

  } catch (error) {
    hostNote.textContent = 'Gagal memuat topology: ' + error.message;
  }
}

/* ------------------------------------------------------------
   LOAD FLOWS
   ------------------------------------------------------------ */

async function loadFlows() {
  const store = document.getElementById('store').value;
  const body = document.getElementById('flowBody');

  try {
    const response = await fetch('/api/flows?store=' + encodeURIComponent(store), { cache: 'no-store' });
    const data = await response.json();

    if (data.error) {
      body.innerHTML = `<tr><td colspan="8" class="muted">Gagal baca inventory: ${data.error}</td></tr>`;
      return;
    }

    lastFlows = data.flows;
    document.getElementById('cAllow').textContent = data.summary.allow;
    document.getElementById('cDrop').textContent = data.summary.drop;
    document.getElementById('cPunt').textContent = data.summary.punt;
    document.getElementById('cTotal').textContent = data.summary.total;

    if (!data.flows.length) {
      body.innerHTML = `<tr><td colspan="8" class="muted">Belum ada flow di store ini.</td></tr>`;
      return;
    }

    body.innerHTML = data.flows.map((flow, index) => `
      <tr onclick="focusFlow(${index}, this)">
        <td>${flow.switch.replace('openflow:', 'sw ')}</td>
        <td>${flow.table}</td>
        <td>${flow.priority}</td>
        <td>${flow.match}</td>
        <td>${flow.action}</td>
        <td><span class="tag ${flow.verdict}">${flow.verdict}</span></td>
        <td>${flow.packets ?? '—'}</td>
        <td>${flow.seconds != null ? flow.seconds + 's' : '—'}</td>
      </tr>
    `).join('');

  } catch (error) {
    body.innerHTML = `<tr><td colspan="8" class="muted">Gagal memuat flow: ${error.message}</td></tr>`;
  }
}

/* ------------------------------------------------------------
   FOCUS FLOW
   ------------------------------------------------------------ */

function focusFlow(index, row) {
  document.querySelectorAll('#flowBody tr').forEach(el => el.classList.remove('sel'));
  row.classList.add('sel');

  const flow = lastFlows[index];
  if (network && nodesDS && nodesDS.get(flow.switch)) {
    network.selectNodes([flow.switch]);
    network.focus(flow.switch, { scale: 1.1, animation: true });
  }
}

/* ------------------------------------------------------------
   REFRESH
   ------------------------------------------------------------ */

async function refreshAll() {
  await Promise.all([testConnection(), loadTopology(), loadFlows()]);
}

function toggleAuto() {
  const autoEnabled = document.getElementById('auto').checked;
  clearInterval(timer);
  if (!autoEnabled) return;

  topologyRefreshCounter = 0;
  timer = setInterval(() => {
    testConnection();
    loadFlows();
    topologyRefreshCounter += 1;
    // Topology diperbarui tiap 15 detik (setiap 3x siklus 5 detik).
    if (topologyRefreshCounter % 3 === 0) loadTopology();
  }, 5000);
}

/* START */
refreshAll();
toggleAuto();
</script>

</body>
</html>
"""


# ============================================================
# ROUTE INDEX
# ============================================================

@app.route("/")
def index():
    return render_template_string(HTML_PAGE, odl=f"{ODL_IP}:{ODL_PORT}")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    print("Dashboard: http://0.0.0.0:5000  ->  ODL " + BASE_URL)
    app.run(host="0.0.0.0", port=5000, debug=True)