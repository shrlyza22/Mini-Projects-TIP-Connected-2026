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


def _push_flow_restconf(node, flow_id, priority, match, out_port):
    """
    PUT one flow straight into ODL's config datastore via RESTCONF, so it
    survives switch reconnect / reconciliation (unlike a flow added via
    ovs-ofctl/dpctl, which lives only in OVS and is invisible to ODL).

    `out_port` is either a switch port number (int/str) or the literal
    string "CONTROLLER" to punt the packet up to ODL.
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
    try:
        r = requests.put(url, json=body, auth=RESTCONF_AUTH,
                          headers={"Content-Type": "application/json"}, timeout=5)
        return r.status_code in (200, 201, 204)
    except requests.RequestException as e:
        print(f"  [restconf] PUT {node} flow={flow_id} error: {e}")
        return False


def install_pdp_carveout(net, nat):
    """
    Two high-priority flows on s1 so h1 can always reach the PDP portal.
    Pushed via RESTCONF (not dpctl) so they persist in ODL's config
    datastore and don't disappear after a switch reconnect/reconciliation.
    """
    nat_port = s1_port_to(net, nat)
    if nat_port is None:
        print("[carveout] ERROR: could not find s1<->nat0 link"); return

    ip_match = {"ethernet-match": {"ethernet-type": {"type": 2048}}}  # IPv4

    ok_dst = _push_flow_restconf(
        "openflow:1", "carveout-dst", 200,
        {**ip_match, "ipv4-destination": f"{ODL_IP}/32"}, nat_port,
    )
    ok_src = _push_flow_restconf(
        "openflow:1", "carveout-src", 200,
        {**ip_match, "ipv4-source": f"{ODL_IP}/32"}, 1,
    )

    if ok_dst and ok_src:
        print("[carveout] h1 <-> PDP (%s) via s1 port %d (installed via RESTCONF)" % (ODL_IP, nat_port))
    else:
        print("[carveout] WARNING: one or both flows failed to install via RESTCONF — "
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