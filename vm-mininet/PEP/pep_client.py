#!/usr/bin/env python3
"""
ZTNA CLI client for Mininet hosts.

Examples:
  Interactive:
    mininet> h1 python3 /home/ubuntu/mini-projects/pep_client_fixed.py

  Deterministic stdin password:
    mininet> h1 bash -lc 'printf "%s\n" "research123" | python3 /home/ubuntu/mini-projects/pep_client_fixed.py --username alice --password-stdin'

  Logout:
    mininet> h1 python3 /home/ubuntu/mini-projects/pep_client_fixed.py --logout TOKEN
"""

import argparse
import json
import socket
import subprocess
import sys
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


def login(args):
    ip, mac = local_identity()

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

    status, response = post(
        "/login",
        {
            "username": username,
            "password": password,
            "ip": ip,
            "mac": mac,
        },
    )

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

    token = response["token"]
    print(f"\nsession token: {token}")
    print("flows stay active until you revoke them:")
    print(
        "    h1 python3 /home/ubuntu/mini-projects/"
        f"pep_client_fixed.py --logout {token}"
    )
    return 0


def logout(token):
    status, response = post("/logout", {"token": token})

    if status is None:
        print("!", response.get("error", "cannot reach PDP"))
        return 3

    if response.get("ok"):
        print("logged out (flows revoked)")
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
