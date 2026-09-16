#!/usr/bin/env python3
"""
ZTNA CLI client for Mininet hosts.

Examples:
  Interactive:
    mininet> h1 python3 /home/ubuntu/mini-projects/pep_client.py

  Deterministic stdin password:
    mininet> h1 bash -lc 'printf "%s\n" "research123" | python3 /home/ubuntu/mini-projects/pep_client.py --username ratih --password-stdin'

  Login then probe the granted HTTP resources:
    mininet> h1 python3 /home/ubuntu/mini-projects/pep_client.py --probe

  Logout:
    mininet> h1 python3 /home/ubuntu/mini-projects/pep_client.py --logout TOKEN
"""

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

PDP_URL = "http://192.168.56.2:5000"


def local_identity():
    """Read the Mininet host's 10.0.0.x IPv4 address and its actual MAC."""
    ip = None
    mac = None

    proc = subprocess.run(
        ["ip", "-o", "-4", "addr", "show"],
        capture_output=True,
        text=True,
        check=False,
    )

    for line in proc.stdout.splitlines():
        if " inet 10.0.0." not in line:
            continue

        parts = line.split()
        if len(parts) < 4:
            continue

        # ip -o may show e.g. h1-eth0@if62. /sys/class/net needs h1-eth0.
        dev = parts[1].split("@", 1)[0]
        ip = parts[3].split("/", 1)[0]

        mac_proc = subprocess.run(
            ["cat", f"/sys/class/net/{dev}/address"],
            capture_output=True,
            text=True,
            check=False,
        )
        candidate = mac_proc.stdout.strip().lower()
        if candidate:
            mac = candidate

        break

    return ip, mac


def _decode_json(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {"error": raw.decode("utf-8", errors="replace")}


# Do not inherit HTTP_PROXY/HTTPS_PROXY for the lab-private PDP address.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post(path, payload):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        PDP_URL + path,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with _OPENER.open(req, timeout=20) as response:
            return response.status, _decode_json(response.read())

    except urllib.error.HTTPError as exc:
        return exc.code, _decode_json(exc.read())

    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        reason = getattr(exc, "reason", exc)
        return None, {"error": f"cannot reach PDP: {reason}"}


def read_credentials(args):
    if args.username:
        username = args.username.strip()
    else:
        username = input("username: ").strip()

    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        # Mininet's `h1 <command>` execution is not a normal controlling TTY.
        # Plain input is intentionally used here for deterministic lab testing.
        # Do not reuse this interaction model for production credentials.
        password = input("password: ").rstrip("\r\n")

    return username, password


def print_grants(granted):
    if not granted:
        print("  (no resources granted for this role/tier)")
        return

    for resource, info in granted.items():
        ports = ",".join(str(port) for port in info.get("ports", []))
        path_nodes = []
        for node in info.get("path", []):
            suffix = node.split(":", 1)[-1]
            path_nodes.append(f"s{suffix}")
        path = " -> ".join(path_nodes)
        print(f"  {resource:8s} ports [{ports}]   path {path}")


def probe_grants(granted, timeout=4):
    """Try an HTTP GET on every granted resource:port (data-plane check).

    Reports status, latency, and payload size so the flow rule's effect can be
    observed from the client side (@ --probe). Resources behind blocked flows
    will report 'unreachable' instead of an HTTP status.
    """
    if not granted:
        print("  (nothing to probe)")
        return

    for resource, info in granted.items():
        res_ip = info.get("ip")
        for port in info.get("ports", []):
            url = f"http://{res_ip}:{port}/"
            started = time.perf_counter()
            try:
                with _OPENER.open(url, timeout=timeout) as response:
                    body = response.read()
                    elapsed = (time.perf_counter() - started) * 1000
                    print(
                        f"  {resource:8s} {res_ip}:{port:<5} -> HTTP {response.status} "
                        f"{len(body)}B  {elapsed:.1f} ms"
                    )
            except urllib.error.HTTPError as exc:
                elapsed = (time.perf_counter() - started) * 1000
                print(
                    f"  {resource:8s} {res_ip}:{port:<5} -> HTTP {exc.code} "
                    f"{elapsed:.1f} ms"
                )
            except (urllib.error.URLError, TimeoutError, socket.timeout):
                elapsed = (time.perf_counter() - started) * 1000
                print(
                    f"  {resource:8s} {res_ip}:{port:<5} -> unreachable "
                    f"(blocked or no listener) after {elapsed:.1f} ms"
                )


def login(args):
    ip, mac = local_identity()

    if args.ip:
        ip = args.ip
    if args.mac:
        mac = args.mac

    if not ip or not mac:
        print(f"! cannot determine Mininet identity: ip={ip!r}, mac={mac!r}")
        return 2

    print(f"== ZTNA login ==  (this host: {ip} / {mac})")

    username, password = read_credentials(args)

    if args.debug:
        # Never print the password itself.
        print(
            f"DEBUG username={username!r} "
            f"password_length={len(password)} ip={ip!r} mac={mac!r}"
        )

    started = time.perf_counter()
    status, response = post(
        "/login",
        {
            "username": username,
            "password": password,
            "ip": ip,
            "mac": mac,
        },
    )
    login_ms = (time.perf_counter() - started) * 1000

    if status is None:
        print("!", response.get("error", "cannot reach PDP"))
        return 3

    if not response.get("ok"):
        print(
            f"DENIED ({status}): {response.get('error')}  "
            f"[tier={response.get('tier')} trust={response.get('trust')}]"
        )
        reasons = response.get("detail", {}).get("reasons", [])
        if reasons:
            print("  reasons:", ", ".join(reasons))
        return 1

    print(
        f"\nGRANTED  role={response['role']}  "
        f"trust={response['trust']}  tier={response['tier']}"
    )
    print_grants(response.get("granted", {}))

    provision = response.get("provision_ms")
    timing = f"client login round-trip: {login_ms:.1f} ms"
    if provision is not None:
        timing += f"  (PDP provisioning: {provision} ms)"
    print(timing)

    if args.probe:
        print("\n== data-plane probe ==")
        probe_grants(response.get("granted", {}))

    token = response["token"]
    print(f"\nsession token: {token}")
    print("flows stay active until you revoke them:")
    print(
        "    h1 python3 /home/ubuntu/mini-projects/"
        f"pep_client.py --logout {token}"
    )
    return 0


def logout(token):
    started = time.perf_counter()
    status, response = post("/logout", {"token": token})
    logout_ms = (time.perf_counter() - started) * 1000

    if status is None:
        print("!", response.get("error", "cannot reach PDP"))
        return 3

    if response.get("ok"):
        revoke = response.get("revoke_ms")
        detail = f" ({revoke} ms)" if revoke is not None else ""
        print(f"logged out (flows revoked{detail})")
        print(f"client logout round-trip: {logout_ms:.1f} ms")
        return 0

    print(f"logout failed ({status}): {response}")
    return 1


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username")
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="read exactly one password line from stdin",
    )
    parser.add_argument("--logout", metavar="TOKEN")
    parser.add_argument(
        "--ip",
        help="[lab testing] report this IP instead of the host's own",
    )
    parser.add_argument(
        "--mac",
        help="[lab testing] report this MAC instead of the host's own "
             "(used to exercise the IP/MAC-mismatch policy path)",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="after login, HTTP-probe every granted resource:port",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="show username/IP/MAC and password length, never password contents",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.logout:
        return logout(args.logout)

    return login(args)


if __name__ == "__main__":
    raise SystemExit(main())
