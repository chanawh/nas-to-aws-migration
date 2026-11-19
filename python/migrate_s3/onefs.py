"""OneFS virtual-node source mode.

The OneFS node is NOT emulated and NOT in this repo. It is a real
OneFS Simulator virtual node running in VMware Workstation Pro on the
user's own machine (see docs/ONEFS_VIRTUAL_NODE.md for the download,
resources and first-boot steps). This module is the bridge between
that node and the S3 sync engine:

  OneFS VM (VMware, bridged IP) --SMB--> host mount root --sync--> S3 emulator

The sync engine itself does not change. It reads a directory tree
where each share is a subdirectory. This module creates that tree:
mount-root/finance, mount-root/it-support, and so on, each one a
real SMB mount of the matching OneFS share, plus the local metadata
files (users.csv, acl.json, quotas.json) that live beside them.

Credentials are never stored in this repo. They come from env vars
ONEFS_USER / ONEFS_PASSWORD, or from --onefs-user / --onefs-password
on the command line. The Platform API uses a self-signed cert, so
HTTPS calls here skip verification on purpose - lab only, same host.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import ssl
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

SHARES = ["finance", "it-support", "it-operations", "operations"]
ONEFS_PATHS = {s: f"/ifs/data/{s}" for s in SHARES}

# SmartQuota caps, in the same units as quotas.json in the dataset.
ONEFS_QUOTAS = {
    "finance": "2G",
    "it-support": "2G",
    "it-operations": "1G",
    "operations": "1G",
}


@dataclass
class OneFSCheck:
    host: str
    reachable: bool = False
    tcp_445_smb: bool = False
    tcp_8080_api: bool = False
    tcp_22_ssh: bool = False
    api_ok: bool = False
    cluster_name: str = ""
    onefs_version: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.reachable and self.tcp_445_smb and self.api_ok


def _tcp_ok(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _api_get(host: str, user: str, password: str, path: str) -> dict:
    """One Platform API GET. Returns {} on any failure, never raises."""
    import base64

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req = urllib.request.Request(
        f"https://{host}:8080{path}",
        headers={"Authorization": f"Basic {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}


def _api_probe(host: str) -> bool:
    """Unauthenticated probe: a real OneFS answers the Platform API
    with 401 (no credentials) or 200. Anything else - timeout, reset,
    a proxy error - is not OneFS.

    TCP connect alone is not proof: some networks accept a TCP SYN
    and then hang, which made check-onefs report PASS for a dead
    TEST-NET address in this VM. A real HTTP round-trip is the check.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(f"https://{host}:8080/platform/3/cluster/config")
    try:
        with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        return e.code in (401, 403)
    except Exception:
        return False


def check_onefs(host: str, user: str = "root", password: str = "") -> OneFSCheck:
    result = OneFSCheck(host=host)
    result.tcp_445_smb = _tcp_ok(host, 445)
    result.tcp_8080_api = _tcp_ok(host, 8080)
    result.tcp_22_ssh = _tcp_ok(host, 22)
    result.reachable = result.tcp_445_smb or result.tcp_8080_api or result.tcp_22_ssh
    if not result.reachable:
        result.errors.append(
            f"{host} is not reachable on 445/8080/22. Check the VM is powered on, "
            "the network is Bridged (not NAT/Host-only), and the host firewall allows SMB."
        )
        return result
    if not result.tcp_445_smb:
        result.errors.append("Port 445 (SMB) is closed. SMB is not enabled on the node, or the host firewall is blocking it.")
    if password:
        cfg = _api_get(host, user, password, "/platform/3/cluster/config")
        if cfg:
            result.api_ok = True
            result.cluster_name = str(cfg.get("name", ""))
            result.onefs_version = str(cfg.get("onefs_version", {}).get("release", ""))
        else:
            result.errors.append("Platform API did not answer on 8080. Check the user/password, and that the cluster finished its first-boot wizard.")
    else:
        result.api_ok = _api_probe(host)
        if not result.api_ok:
            result.errors.append(
                "Platform API did not answer with a real OneFS response on 8080. "
                "The cluster may still be booting, the wizard has not finished, "
                "or the IP is not a OneFS node."
            )
    return result


def list_smb_shares(host: str, user: str, password: str) -> list[str]:
    data = _api_get(host, user, password, "/platform/1/protocols/smb/shares")
    shares = data.get("shares", []) if isinstance(data, dict) else []
    return [str(s.get("name", "")) for s in shares if isinstance(s, dict) and s.get("name")]


def mount_commands(host: str, mount_root: Path, user: str, password: str) -> list[list[str]]:
    """The OS commands that mount every OneFS share under mount_root.

    Returned, not run, so --dry-run can print them and tests can inspect
    them without touching the system. mount_shares() runs them.
    Windows (VMware Workstation Pro host): net use Z: \\\\host\\share
    with a drive letter per share, plus a directory junction from
    mount_root/<share> to that drive. Linux: mount -t cifs.
    """
    system = platform.system()
    commands: list[list[str]] = []
    if system == "Windows":
        letters = ["F:", "G:", "H:", "I:"]
        for share, letter in zip(SHARES, letters):
            commands.append(["net", "use", letter, f"\\\\{host}\\{share}", f"/user:{user}", password, "/persistent:no"])
            commands.append(["cmd", "/c", "mklink", "/D", str(mount_root / share), f"{letter}\\"])
    else:
        for share in SHARES:
            commands.append([
                "sudo", "mount", "-t", "cifs", f"//{host}/{share}",
                str(mount_root / share), "-o",
                f"vers=3.0,username={user},password={password},uid={os.getuid()},gid={os.getgid()}",
            ])
    return commands


def mount_shares(host: str, mount_root: Path, user: str, password: str, *, dry_run: bool = False) -> list[str]:
    """Create mount_root/<share> and mount each OneFS share into it.

    Returns the list of commands run (or that would run, on dry-run).
    On dry-run nothing is mounted and no directories are created.
    """
    ran: list[str] = []
    for cmd in mount_commands(host, mount_root, user, password):
        ran.append(" ".join(cmd))
        if dry_run:
            continue
        # The mount target directory must exist first on Linux; on
        # Windows the mklink target is created by mklink itself.
        if platform.system() != "Windows" and cmd[0] == "sudo":
            Path(cmd[4]).mkdir(parents=True, exist_ok=True)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise IOError(f"mount failed: {' '.join(cmd)}\n{result.stdout}\n{result.stderr}")
    return ran


def unmount_shares(mount_root: Path, *, dry_run: bool = False) -> list[str]:
    """Unmount every share under mount_root. Returns commands run."""
    ran: list[str] = []
    if platform.system() == "Windows":
        for share, letter in zip(SHARES, ["F:", "G:", "H:", "I:"]):
            cmd = ["net", "use", letter, "/delete", "/y"]
            ran.append(" ".join(cmd))
            if not dry_run:
                subprocess.run(cmd, capture_output=True, text=True)
            link = mount_root / share
            if not dry_run and link.is_symlink():
                link.unlink()
    else:
        for share in SHARES:
            target = mount_root / share
            cmd = ["sudo", "umount", str(target)]
            ran.append(" ".join(cmd))
            if not dry_run:
                subprocess.run(cmd, capture_output=True, text=True)
    return ran


def seed_onefs(source_root: Path, mount_root: Path) -> dict[str, int]:
    """Copy a generated dataset into the mounted OneFS shares.

    source_root is a local dataset (from `generate`). mount_root is the
    OneFS mount tree. users.csv / acl.json / quotas.json are copied to
    mount_root itself (they are migration metadata, not OneFS data);
    the share files are copied into mount_root/<share>/. Returns
    {share: files_copied}.
    """
    if not source_root.is_dir():
        raise FileNotFoundError(f"source dataset {source_root} does not exist - run generate first")
    mount_root.mkdir(parents=True, exist_ok=True)
    for name in ("users.csv", "acl.json", "quotas.json"):
        src = source_root / name
        if src.exists():
            shutil.copy2(src, mount_root / name)
    copied: dict[str, int] = {}
    for share in SHARES:
        src_dir = source_root / share
        dst_dir = mount_root / share
        if not src_dir.is_dir():
            copied[share] = 0
            continue
        dst_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for p in sorted(src_dir.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(src_dir)
            dst = dst_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
            count += 1
        copied[share] = count
    return copied


def validate_mount_root(mount_root: Path) -> list[str]:
    """Check a mount root is ready to sync from. Returns problems, [] if clean."""
    problems: list[str] = []
    if not mount_root.is_dir():
        return [f"mount root {mount_root} does not exist - run mount-onefs first"]
    for name in ("users.csv", "acl.json", "quotas.json"):
        if not (mount_root / name).is_file():
            problems.append(f"missing {name} in {mount_root} - run seed-onefs first")
    for share in SHARES:
        d = mount_root / share
        if not d.is_dir():
            problems.append(f"share directory {d} is missing - is {share} mounted?")
        elif not any(d.iterdir()):
            problems.append(f"share {share} is mounted but empty - did seed-onefs run?")
    return problems
