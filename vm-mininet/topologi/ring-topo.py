#!/usr/bin/env python3

import time

from mininet.cli import CLI
from mininet.log import info, setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController


ODL_IP = "192.168.56.2"
ODL_PORT = 6653


def warm_up(net):
    """
    Menghasilkan traffic awal agar:
    - ARP table terbentuk,
    - host-tracker mengenali seluruh host,
    - flow L2Switch mulai terpasang.
    """
    info("*** Membersihkan ARP cache\n")

    for host in net.hosts:
        host.cmd("ip neigh flush all")

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


def ring():
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

    h1 = net.addHost(
        "h1",
        ip="10.0.0.1/24",
        mac="00:00:00:00:00:01",
    )

    h2 = net.addHost(
        "h2",
        ip="10.0.0.2/24",
        mac="00:00:00:00:00:02",
    )

    h3 = net.addHost(
        "h3",
        ip="10.0.0.3/24",
        mac="00:00:00:00:00:03",
    )

    h4 = net.addHost(
        "h4",
        ip="10.0.0.4/24",
        mac="00:00:00:00:00:04",
    )

    net.addLink(h1, s1, port2=1)
    net.addLink(h2, s2, port2=1)
    net.addLink(h3, s3, port2=1)
    net.addLink(h4, s4, port2=1)

    net.addLink(s1, s2, port1=2, port2=2)
    net.addLink(s2, s3, port1=3, port2=2)
    net.addLink(s3, s4, port1=3, port2=2)
    net.addLink(s4, s1, port1=3, port2=3)

    try:
        info("*** Memulai jaringan\n")
        net.start()

        info("*** Menunggu seluruh switch terhubung ke ODL\n")

        connected = net.waitConnected(
            timeout=30,
            delay=0.5,
        )

        if not connected:
            info(
                "*** WARNING: Tidak semua switch "
                "terhubung dalam 30 detik\n"
            )

        # waitConnected hanya memastikan sesi controller.
        # ODL masih memerlukan waktu untuk LLDP, loop handling,
        # host discovery, dan flow installation.
        info("*** Menunggu topology convergence\n")
        time.sleep(8)

        warm_up(net)

        info("*** Verifikasi konektivitas setelah warm-up\n")
        packet_loss = net.pingAll()

        info(
            f"*** Packet loss setelah warm-up: "
            f"{packet_loss}%\n"
        )

        CLI(net)

    finally:
        net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    ring()
