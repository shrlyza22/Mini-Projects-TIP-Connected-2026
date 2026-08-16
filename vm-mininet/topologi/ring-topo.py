#!/usr/bin/env python3

import argparse
import subprocess
import time

from mininet.cli import CLI
from mininet.log import info, setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController


ODL_IP = "192.168.56.2"
ODL_PORT = 6653

PDP_IP = "192.168.56.2"
PDP_PORT = 5000

GW_IP = "10.0.0.254"
GW_CIDR = f"{GW_IP}/24"

PEP_INFRA_PRIORITY = 310
PEP_INFRA_COOKIE = "0xfeed"


def warm_up(net):
    """
    Original host-discovery warm-up.
    Hanya h1-h4 yang dihitung sebagai data-plane hosts penelitian.
    """
    info("*** Membersihkan ARP cache\n")

    for host_name in ("h1", "h2", "h3", "h4"):
        net.get(host_name).cmd("ip neigh flush all")

    info("*** Menjalankan host-discovery warm-up\n")

    pairs = [
        ("h1", "h2"),
        ("h2", "h3"),
        ("h3", "h4"),
        ("h4", "h1"),
    ]

    for source_name, destination_name in pairs:
        source = net.get(source_name)
        destination = net.get(destination_name)
        source.cmd(
            f"ping -c 2 -W 1 {destination.IP()} "
            "> /dev/null 2>&1"
        )

    time.sleep(3)


def ping_original_hosts(net):
    """Ping test hanya untuk empat host asli; nat0 tidak ikut dihitung."""
    hosts = [net.get(name) for name in ("h1", "h2", "h3", "h4")]
    return net.ping(hosts=hosts)


def _ovs_add_flow(switch_name, rule):
    result = subprocess.run(
        ["ovs-ofctl", "-O", "OpenFlow13", "add-flow", switch_name, rule],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Gagal memasang flow infrastruktur pada {switch_name}: "
            f"{result.stderr.strip()}"
        )


def clear_pep_infrastructure_flows():
    """Hapus hanya flow 0xfeed milik jalur PEP, bukan flow PDP."""
    for switch_name in ("s1", "s2", "s3", "s4"):
        subprocess.run(
            [
                "ovs-ofctl",
                "-O",
                "OpenFlow13",
                "del-flows",
                switch_name,
                f"cookie={PEP_INFRA_COOKIE}/-1",
            ],
            capture_output=True,
            text=True,
        )


def install_pep_infrastructure_flows():
    """
    Jalur khusus PEP -> PDP.

    h1:
      h1 --s1:1 ... s1:4-- nat0 -> PDP

    h4:
      h4 --s4:1 --s4:3 -> s1:3 --s1:4-- nat0 -> PDP

    Tidak ada rule di sini yang membuka h2/server atau h3/iot.
    Akses protected resources tetap milik pdp.py.
    """
    clear_pep_infrastructure_flows()

    # ---------------- h1 <-> gateway/PDP ----------------
    rules = {
        "s1": [
            # h1 -> gateway
            f"cookie={PEP_INFRA_COOKIE},priority={PEP_INFRA_PRIORITY},"
            f"arp,in_port=1,arp_tpa={GW_IP},actions=output:4",

            f"cookie={PEP_INFRA_COOKIE},priority={PEP_INFRA_PRIORITY},"
            f"ip,in_port=1,nw_src=10.0.0.1,nw_dst={GW_IP},actions=output:4",

            # h1 -> PDP:5000
            f"cookie={PEP_INFRA_COOKIE},priority={PEP_INFRA_PRIORITY},"
            f"tcp,in_port=1,nw_src=10.0.0.1,nw_dst={PDP_IP},"
            f"tp_dst={PDP_PORT},actions=output:4",

            # gateway -> h1
            f"cookie={PEP_INFRA_COOKIE},priority={PEP_INFRA_PRIORITY},"
            f"arp,in_port=4,arp_spa={GW_IP},arp_tpa=10.0.0.1,actions=output:1",

            f"cookie={PEP_INFRA_COOKIE},priority={PEP_INFRA_PRIORITY},"
            f"ip,in_port=4,nw_src={GW_IP},nw_dst=10.0.0.1,actions=output:1",

            # PDP:5000 -> h1
            f"cookie={PEP_INFRA_COOKIE},priority={PEP_INFRA_PRIORITY},"
            f"tcp,in_port=4,nw_src={PDP_IP},nw_dst=10.0.0.1,"
            f"tp_src={PDP_PORT},actions=output:1",
        ],

        # ---------------- h4 path toward s1 ----------------
        "s4": [
            f"cookie={PEP_INFRA_COOKIE},priority={PEP_INFRA_PRIORITY},"
            f"arp,in_port=1,arp_tpa={GW_IP},actions=output:3",

            f"cookie={PEP_INFRA_COOKIE},priority={PEP_INFRA_PRIORITY},"
            f"ip,in_port=1,nw_src=10.0.0.4,nw_dst={GW_IP},actions=output:3",

            f"cookie={PEP_INFRA_COOKIE},priority={PEP_INFRA_PRIORITY},"
            f"tcp,in_port=1,nw_src=10.0.0.4,nw_dst={PDP_IP},"
            f"tp_dst={PDP_PORT},actions=output:3",

            f"cookie={PEP_INFRA_COOKIE},priority={PEP_INFRA_PRIORITY},"
            f"arp,in_port=3,arp_spa={GW_IP},arp_tpa=10.0.0.4,actions=output:1",

            f"cookie={PEP_INFRA_COOKIE},priority={PEP_INFRA_PRIORITY},"
            f"ip,in_port=3,nw_src={GW_IP},nw_dst=10.0.0.4,actions=output:1",

            f"cookie={PEP_INFRA_COOKIE},priority={PEP_INFRA_PRIORITY},"
            f"tcp,in_port=3,nw_src={PDP_IP},nw_dst=10.0.0.4,"
            f"tp_src={PDP_PORT},actions=output:1",
        ],
    }

    # h4 transit across s1: s4 enters s1 on port3, NAT is port4
    rules["s1"] += [
        f"cookie={PEP_INFRA_COOKIE},priority={PEP_INFRA_PRIORITY},"
        f"arp,in_port=3,arp_tpa={GW_IP},actions=output:4",

        f"cookie={PEP_INFRA_COOKIE},priority={PEP_INFRA_PRIORITY},"
        f"ip,in_port=3,nw_src=10.0.0.4,nw_dst={GW_IP},actions=output:4",

        f"cookie={PEP_INFRA_COOKIE},priority={PEP_INFRA_PRIORITY},"
        f"tcp,in_port=3,nw_src=10.0.0.4,nw_dst={PDP_IP},"
        f"tp_dst={PDP_PORT},actions=output:4",

        f"cookie={PEP_INFRA_COOKIE},priority={PEP_INFRA_PRIORITY},"
        f"arp,in_port=4,arp_spa={GW_IP},arp_tpa=10.0.0.4,actions=output:3",

        f"cookie={PEP_INFRA_COOKIE},priority={PEP_INFRA_PRIORITY},"
        f"ip,in_port=4,nw_src={GW_IP},nw_dst=10.0.0.4,actions=output:3",

        f"cookie={PEP_INFRA_COOKIE},priority={PEP_INFRA_PRIORITY},"
        f"tcp,in_port=4,nw_src={PDP_IP},nw_dst=10.0.0.4,"
        f"tp_src={PDP_PORT},actions=output:3",
    ]

    for switch_name, switch_rules in rules.items():
        for rule in switch_rules:
            _ovs_add_flow(switch_name, rule)

    info("*** PEP infrastructure flow aktif (priority 310)\n")


def verify_ring_ports(expect_nat=False):
    """
    Mapping inti harus selalu sama dengan pdp.py.
    Port 4 diverifikasi hanya pada mode PEP.
    """
    expected = {
        "s1-eth1": "1",
        "s1-eth2": "2",
        "s1-eth3": "3",
        "s2-eth1": "1",
        "s2-eth2": "2",
        "s2-eth3": "3",
        "s3-eth1": "1",
        "s3-eth2": "2",
        "s3-eth3": "3",
        "s4-eth1": "1",
        "s4-eth2": "2",
        "s4-eth3": "3",
    }

    if expect_nat:
        expected["s1-eth4"] = "4"

    for interface_name, expected_port in expected.items():
        result = subprocess.run(
            ["ovs-vsctl", "--if-exists", "get", "Interface",
             interface_name, "ofport"],
            capture_output=True,
            text=True,
        )
        actual = result.stdout.strip()

        if actual != expected_port:
            raise RuntimeError(
                f"{interface_name}: expected ofport={expected_port}, "
                f"actual={actual!r}"
            )

    info("*** Port mapping ring sesuai pdp.py\n")


def configure_pep_network(net):
    """
    Konfigurasi PEP minimum.
    Default route hanya diperlukan h1 dan h4 karena keduanya adalah client PEP.
    h2 dan h3 tetap murni protected resources pada subnet 10.0.0.0/24.
    """
    h1 = net.get("h1")
    h4 = net.get("h4")
    nat = net.get("nat0")

    nat_addr = nat.cmd("ip -4 -br addr show nat0-eth0").strip()
    if GW_IP not in nat_addr:
        raise RuntimeError(
            f"nat0 tidak memiliki {GW_CIDR}: {nat_addr}"
        )

    h1.cmd(f"ip route replace default via {GW_IP}")
    h4.cmd(f"ip route replace default via {GW_IP}")

    for host_name in ("h1", "h4"):
        route = net.get(host_name).cmd("ip route show default").strip()
        if f"via {GW_IP}" not in route:
            raise RuntimeError(
                f"Default route {host_name} gagal dibuat: {route}"
            )

    install_pep_infrastructure_flows()

    info("*** PEP gateway: 10.0.0.254 via s1 port 4\n")
    info("*** Default route hanya ditambahkan pada h1 dan h4\n")


def ring(pep_mode=False):
    net = Mininet(
        controller=None,
        switch=OVSSwitch,
        autoSetMacs=False,
        autoStaticArp=False,
        waitConnected=True,
    )

    controller = net.addController(
        "c0",
        controller=RemoteController,
        ip=ODL_IP,
        port=ODL_PORT,
    )

    # ---------------- ORIGINAL 4 SWITCHES ----------------
    s1 = net.addSwitch(
        "s1",
        dpid="0000000000000001",
        protocols="OpenFlow13",
        failMode="secure",
    )
    s2 = net.addSwitch(
        "s2",
        dpid="0000000000000002",
        protocols="OpenFlow13",
        failMode="secure",
    )
    s3 = net.addSwitch(
        "s3",
        dpid="0000000000000003",
        protocols="OpenFlow13",
        failMode="secure",
    )
    s4 = net.addSwitch(
        "s4",
        dpid="0000000000000004",
        protocols="OpenFlow13",
        failMode="secure",
    )

    # ---------------- ORIGINAL 4 HOSTS ----------------
    h1 = net.addHost(
        "h1", ip="10.0.0.1/24", mac="00:00:00:00:00:01"
    )
    h2 = net.addHost(
        "h2", ip="10.0.0.2/24", mac="00:00:00:00:00:02"
    )
    h3 = net.addHost(
        "h3", ip="10.0.0.3/24", mac="00:00:00:00:00:03"
    )
    h4 = net.addHost(
        "h4", ip="10.0.0.4/24", mac="00:00:00:00:00:04"
    )

    # ---------------- ORIGINAL HOST LINKS ----------------
    net.addLink(h1, s1, port2=1)
    net.addLink(h2, s2, port2=1)
    net.addLink(h3, s3, port2=1)
    net.addLink(h4, s4, port2=1)

    # ---------------- ORIGINAL RING ----------------
    net.addLink(s1, s2, port1=2, port2=2)
    net.addLink(s2, s3, port1=3, port2=2)
    net.addLink(s3, s4, port1=3, port2=2)
    net.addLink(s4, s1, port1=3, port2=3)

    # NAT hanya ditambahkan jika mode PEP diminta.
    if pep_mode:
        net.addNAT(
            "nat0",
            ip=GW_CIDR,
            connect=s1,
            inNamespace=False,
        )

    try:
        if pep_mode:
            info("*** MODE: PEP end-to-end\n")
        else:
            info("*** MODE: PDP controlled / original ring\n")

        info("*** Memulai jaringan\n")
        net.start()

        info("*** Menunggu seluruh switch terhubung ke ODL\n")
        connected = net.waitConnected(timeout=30, delay=0.5)

        if not connected:
            info("*** WARNING: tidak semua switch terhubung dalam 30 detik\n")

        info("*** Menunggu topology convergence\n")
        time.sleep(8)

        # Original discovery behavior
        warm_up(net)

        info("*** Verifikasi konektivitas h1-h4 setelah warm-up\n")
        packet_loss = ping_original_hosts(net)
        info(f"*** Packet loss h1-h4: {packet_loss}%\n")

        if packet_loss == 0:
            info("*** Baseline connectivity h1-h4 OK\n")
        else:
            info(
                "*** NOTE: jika default-deny PDP sudah tersimpan/aktif, "
                "ICMP dapat diblok walaupun ring topology sehat.\n"
            )

        # Core ring must always stay identical.
        verify_ring_ports(expect_nat=pep_mode)

        if pep_mode:
            configure_pep_network(net)
            info(
                "*** PEP MODE READY: h1/h4 dapat menuju PDP melalui nat0\n"
            )
        else:
            info(
                "*** PDP MODE READY: hanya 4 host + 4 switch ring; "
                "tidak ada nat0\n"
            )

        CLI(net)

    finally:
        if pep_mode:
            clear_pep_infrastructure_flows()
        net.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Unified ODL-ZTNA ring topology"
    )
    parser.add_argument(
        "--pep",
        action="store_true",
        help="tambahkan NAT/gateway dan jalur PEP -> PDP",
    )
    args = parser.parse_args()

    setLogLevel("info")
    ring(pep_mode=args.pep)
