#!/usr/bin/env python3
"""
ZTNA data plane — runs on VM2 (Mininet/OVS).

4-switch ring, one host per switch:
    h1 Research | h2 Server | h3 IoT | h4 Guest
A NAT node on s1 gives hosts a route to the PDP (VM1, 192.168.56.2) so the
research host can log in; masquerade handles the return path (no route needed
on VM1). Static ARP everywhere => no ARP broadcast => ring stays loop-free.

Default-drop + LLDP-to-controller used to be provided by odl-l2switch
(verified in Week 2). Since odl-l2switch-switch is uninstalled for ZTNA
testing, both are now installed manually — but NOT the same way:
  - priority=0 default-drop stays a plain dpctl flow. It's a belt-and-
    braces rule only: fail-mode=secure already drops anything unmatched,
    so it's fine if this particular flow disappears on reconciliation.
  - priority=100 LLDP-punt AND the priority=200 PDP carveout are both
    pushed via RESTCONF into ODL's config datastore instead of dpctl.
    A flow added straight to OVS is invisible to ODL and gets wiped the
    moment openflowplugin resyncs the switch (which happens shortly
    after connect) — that's exactly why the LLDP-punt flow kept
    disappearing before link discovery had a chance to run, and why the
    carveout had to be re-injected by hand every session. Flows in the
    config datastore get reapplied by ODL itself on every reconnect, so
    both now survive across restarts without manual intervention.

Run:  sudo python3 ztna_net.py
Then: mininet> h1 python3 pep_client.py
"""

import time

import requests
from requests.auth import HTTPBasicAuth

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.topo import Topo
from mininet.cli import CLI
from mininet.log import setLogLevel

ODL_IP  = "192.168.56.2"       # VM1 (OpenDaylight + PDP)
OF_PORT = 6653
GW_IP   = "10.0.0.254"         # NAT gateway on s1 (data-plane side)

RESTCONF_BASE = f"http://{ODL_IP}:8181/rests/data"
RESTCONF_AUTH = HTTPBasicAuth("admin", "admin")

HOSTS = {  # name: (ip, mac, segment)
    "h1": ("10.0.0.1", "00:00:00:00:00:01", "research"),
    "h2": ("10.0.0.2", "00:00:00:00:00:02", "server"),
    "h3": ("10.0.0.3", "00:00:00:00:00:03", "iot"),
    "h4": ("10.0.0.4", "00:00:00:00:00:04", "guest"),
}


class RingTopo(Topo):
    def build(self):
        sw = []
        for i in range(1, 5):
            s = self.addSwitch("s%d" % i, protocols="OpenFlow13")
            name = "h%d" % i
            ip, mac, _ = HOSTS[name]
            h = self.addHost(name, ip="%s/24" % ip, mac=mac)
            self.addLink(h, s, port1=0, port2=1)          # host on switch port 1
            sw.append(s)
        for i in range(4):                                # ring s1-s2-s3-s4-s1
            self.addLink(sw[i], sw[(i + 1) % 4], port1=2, port2=3)


def s1_port_to(net, node):
    """Return the s1 port number whose link goes to `node` (e.g. nat0)."""
    s1 = net.get("s1")
    for intf in s1.intfList():
        if intf.link:
            peer = intf.link.intf1 if intf.link.intf2 is intf else intf.link.intf2
            if peer.node is node:
                return s1.ports[intf]
    return None


def setup_static_arp(net, nat):
    """No ARP on the wire -> no broadcast -> no ring loop."""
    gw_mac = nat.MAC()
    for name, (ip, mac, _) in HOSTS.items():
        h = net.get(name)
        for oname, (oip, omac, _) in HOSTS.items():
            if oname != name:
                h.cmd("arp -s %s %s" % (oip, omac))
        h.cmd("arp -s %s %s" % (GW_IP, gw_mac))           # gateway to PDP
        nat.cmd("arp -s %s %s" % (ip, mac))               # gateway knows each host


def start_services(net):
    """Simple TCP listeners so 'Limited' vs 'Full' ports are testable."""
    net.get("h2").cmd("python3 -m http.server 8080 >/tmp/h2-8080.log 2>&1 &")
    net.get("h2").cmd("python3 -m http.server 9000 >/tmp/h2-9000.log 2>&1 &")
    net.get("h2").cmd("python3 -m http.server 22   >/tmp/h2-22.log   2>&1 &")
    net.get("h3").cmd("python3 -m http.server 80   >/tmp/h3-80.log   2>&1 &")
    # h4 (guest) intentionally offers nothing


def _push_flow_restconf(node, flow_id, priority, match, out_port, retries=3, delay=2):
    """
    PUT one flow straight into ODL's config datastore via RESTCONF, so it
    survives switch reconnect / reconciliation (unlike a flow added via
    ovs-ofctl/dpctl, which lives only in OVS and is invisible to ODL).

    `out_port` is either a switch port number (int/str) or the literal
    string "CONTROLLER" to punt the packet up to ODL.

    Only retries on TRANSIENT failures (connection error, timeout) — right
    after net.start(), ODL may not have finished the OpenFlow handshake
    with the switch yet, which can make the very first attempt fail even
    though everything else is correct. A 401/403 is NOT transient — it
    means the credentials were rejected, and retrying that repeatedly
    across ~15 flows is exactly what can trip an account-lockout policy
    in odl-aaa-shiro. So auth failures fail fast, after a single attempt.
    """
    url = (f"{RESTCONF_BASE}/opendaylight-inventory:nodes/node={node}"
           f"/flow-node-inventory:table=0/flow={flow_id}")
    body = {"flow-node-inventory:flow": [{
        "id": flow_id, "table_id": 0, "priority": priority,
        "match": match,
        "instructions": {"instruction": [{
            "order": 0,
            "apply-actions": {"action": [{
                "order": 0,
                "output-action": {"output-node-connector": str(out_port)},
            }]},
        }]},
    }]}

    for attempt in range(1, retries + 1):
        try:
            r = requests.put(url, json=body, auth=RESTCONF_AUTH,
                              headers={"Content-Type": "application/json"}, timeout=5)
            if r.status_code in (200, 201, 204):
                return True
            if r.status_code in (401, 403):
                print(f"  [restconf] PUT {node} flow={flow_id} -> HTTP {r.status_code} "
                      "(auth rejected — not retrying, check ODL credentials)")
                return False
            print(f"  [restconf] PUT {node} flow={flow_id} attempt {attempt}/{retries} -> HTTP {r.status_code}")
        except requests.RequestException as e:
            print(f"  [restconf] PUT {node} flow={flow_id} attempt {attempt}/{retries} error: {e}")
        if attempt < retries:
            time.sleep(delay)
    return False


#   Ring adjacency (fixed, matches RingTopo.build()): each switch's
#   port1 = its own host, port2 = link "forward" to the next switch,
#   port3 = link "back" to the previous switch, ring order s1-s2-s3-s4-s1.
#
#   Forward direction (host -> PDP): the next hop toward s1 for each
#   switch. s2 and s4 sit directly next to s1 (1 hop); s3 is 2 hops away
#   either direction, routed here via s2 to match the path PDP itself
#   prints for h3/iot ("s1 -> 2 -> 3").
FORWARD_NEXT_HOP = {
    "openflow:2": 3,   # s2 -> s1 directly
    "openflow:3": 3,   # s3 -> s2 (which then relays -> s1 via its own rule)
    "openflow:4": 2,   # s4 -> s1 directly
}

#   Return direction (PDP -> host): per-switch, per-destination-host
#   output port, so replies actually reach whichever host asked — instead
#   of being blindly sent out h1's port regardless of who logged in.
RETURN_HOPS = {
    "openflow:1": {"10.0.0.1": 1, "10.0.0.2": 2, "10.0.0.3": 2, "10.0.0.4": 3},
    "openflow:2": {"10.0.0.2": 1, "10.0.0.3": 2},
    "openflow:3": {"10.0.0.3": 1},
    "openflow:4": {"10.0.0.4": 1},
}


def _delete_flow_restconf(node, flow_id):
    """DELETE one flow from ODL's config datastore, if it exists. Used to
    clean up flow IDs from an older version of this script that would
    otherwise linger and conflict with the new ones (same priority, more
    permissive match — can shadow the per-host return flows below)."""
    url = (f"{RESTCONF_BASE}/opendaylight-inventory:nodes/node={node}"
           f"/flow-node-inventory:table=0/flow={flow_id}")
    try:
        requests.delete(url, auth=RESTCONF_AUTH, timeout=5)
    except requests.RequestException:
        pass


def install_pdp_carveout(net, nat):
    """
    Flows so ANY host (h1-h4) can reach the PDP portal, not just h1.

    The original design only carved out a path for h1 (the only switch
    with a rule at all was s1, where the NAT lives) — h2/h3/h4 had zero
    flow permitting traffic toward PDP on their own switch, so their
    packets were dropped by priority=0 before ever entering the ring.
    That's exactly why h4/bob's login timed out while h1/alice worked.

    This installs, via RESTCONF (config datastore, survives reconnects):
      - one forward flow per switch: any packet to PDP_IP hops toward s1.
      - one return flow per (switch, host) pair along that host's actual
        path back from s1, so replies land on the right host.
    """
    nat_port = s1_port_to(net, nat)
    if nat_port is None:
        print("[carveout] ERROR: could not find s1<->nat0 link"); return

    # Clean up the old h1-only flow IDs from a previous version of this
    # script — same priority (200) but a looser match, which can shadow
    # the new per-host return flows below.
    _delete_flow_restconf("openflow:1", "carveout-dst")
    _delete_flow_restconf("openflow:1", "carveout-src")

    ip_match = {"ethernet-match": {"ethernet-type": {"type": 2048}}}  # IPv4
    ok = True

    # Forward: dst=PDP_IP, hop toward s1 (single rule set covers every host)
    ok &= _push_flow_restconf(
        "openflow:1", "carveout-fwd", 200,
        {**ip_match, "ipv4-destination": f"{ODL_IP}/32"}, nat_port,
    )
    for node, port in FORWARD_NEXT_HOP.items():
        ok &= _push_flow_restconf(
            node, "carveout-fwd", 200,
            {**ip_match, "ipv4-destination": f"{ODL_IP}/32"}, port,
        )

    # Return: src=PDP_IP + dst=<host ip>, hop toward that specific host
    for node, hop_table in RETURN_HOPS.items():
        for host_ip, port in hop_table.items():
            flow_id = "carveout-ret-" + host_ip.split(".")[-1]
            ok &= _push_flow_restconf(
                node, flow_id, 200,
                {**ip_match, "ipv4-source": f"{ODL_IP}/32",
                 "ipv4-destination": f"{host_ip}/32"}, port,
            )

    if ok:
        print("[carveout] h1-h4 <-> PDP (%s) installed via RESTCONF (all hosts covered)" % ODL_IP)
    else:
        print("[carveout] WARNING: one or more flows failed to install via RESTCONF — "
              "check ODL is reachable at %s:8181" % ODL_IP)


def install_lldp_punt(net):
    """
    Let LLDP (0x88cc) through to the controller on every switch so ODL's
    topology-lldp-discovery can still see switch-to-switch links, even
    with odl-l2switch-switch uninstalled.

    Pushed via RESTCONF (not dpctl) — same reason as the PDP carveout:
    a dpctl-only flow is invisible to ODL and gets wiped the moment
    openflowplugin resyncs the switch, usually within seconds of connect
    (well before LLDP discovery has a chance to run). Once this lives in
    ODL's config datastore, ODL re-applies it automatically on every
    reconnect, so link discovery keeps working across restarts.
    """
    lldp_match = {"ethernet-match": {"ethernet-type": {"type": 35020}}}  # 0x88cc

    for i in range(1, 5):
        node = f"openflow:{i}"
        ok = _push_flow_restconf(node, "lldp-punt", 100, lldp_match, "CONTROLLER")
        if not ok:
            print(f"[lldp] WARNING: failed to install LLDP-punt on {node} via RESTCONF")

    print("[lldp] punt-to-controller flow installed on s1-s4 via RESTCONF (link discovery enabled)")


def main():
    setLogLevel("info")
    net = Mininet(topo=RingTopo(), switch=OVSSwitch, controller=None,
                  autoSetMacs=False, build=False)
    net.addController("c0", controller=RemoteController, ip=ODL_IP, port=OF_PORT)
    net.build()
    s1 = net.get("s1")
    nat = net.addNAT(name="nat0", ip=GW_IP + "/24", connect=s1)   # auto-links to s1
    net.start()

    for sname in ("s1", "s2", "s3", "s4"):               # deny-by-default, no NORMAL fallback
        s = net.get(sname)
        s.cmd("ovs-vsctl set-fail-mode %s secure" % sname)

    for sname in ("s1", "s2", "s3", "s4"):                # local default-drop (fine via dpctl —
        s = net.get(sname)                                # secure fail-mode already drops
        s.dpctl("add-flow", "priority=0,actions=drop")    # unmatched packets even without this)

    install_lldp_punt(net)                                # via RESTCONF — persists across reconnects

    nat.configDefault()                                          # host default routes + masquerade
    nat.setIP(GW_IP + "/24")                                     # pin the gateway IP
    for name in HOSTS:
        net.get(name).cmd("ip route replace default via %s" % GW_IP)

    setup_static_arp(net, nat)
    start_services(net)
    install_pdp_carveout(net, nat)

    print("\nnat0: ip=%s mac=%s" % (GW_IP, nat.MAC()))
    print("Ready. Log in from the research host:\n"
          "  mininet> h1 python3 pep_client.py\n"
          "Sanity:  mininet> h1 ping -c1 %s   (should reach the PDP host)\n"
          "Topology: wait ~10-15s after 'Ready' for LLDP discovery to settle,\n"
          "then check the dashboard — s1-s4 should show as a connected ring.\n"
          "If it's still 4 isolated switches, check ODL is reachable and that\n"
          "the lldp-punt / carveout RESTCONF PUTs above returned OK, not WARNING.\n" % ODL_IP)
    CLI(net)
    net.stop()


if __name__ == "__main__":
    main()