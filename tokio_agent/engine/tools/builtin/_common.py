"""
Shared utilities for built-in tools — subprocess, SSH, PostgreSQL helpers.

Centralizes duplicated _run() and _ssh_run() patterns.
"""
from __future__ import annotations

import subprocess
from typing import Dict, Optional


def run_local(cmd: str, timeout: int = 60) -> str:
    """Run a local shell command and return stdout/stderr."""
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    return out or err or f"exit code {p.returncode}"


def run_local_checked(cmd: str, timeout: int = 60) -> str:
    """Run a local shell command; raise RuntimeError on non-zero exit."""
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if p.returncode != 0:
        raise RuntimeError(err or out or f"Command failed ({p.returncode})")
    return out


def ssh_run(
    host: str,
    user: str,
    cmd: str,
    key: str = "",
    port: int = 22,
    timeout: int = 60,
    connect_timeout: int = 8,
) -> str:
    """Run a command on a remote host via SSH."""
    ssh = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", f"ConnectTimeout={connect_timeout}",
        "-p", str(port),
    ]
    if key:
        ssh += ["-i", key]
    ssh += [f"{user}@{host}", cmd]
    p = subprocess.run(ssh, capture_output=True, text=True, timeout=timeout)
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if p.returncode != 0 and not out:
        return err or f"exit code {p.returncode}"
    return out


def ssh_run_checked(
    host: str,
    user: str,
    cmd: str,
    key: str = "",
    port: int = 22,
    timeout: int = 60,
    connect_timeout: int = 8,
) -> str:
    """Run a command on a remote host via SSH; raise on failure."""
    ssh = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", f"ConnectTimeout={connect_timeout}",
        "-p", str(port),
    ]
    if key:
        ssh += ["-i", key]
    ssh += [f"{user}@{host}", cmd]
    p = subprocess.run(ssh, capture_output=True, text=True, timeout=timeout)
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if p.returncode != 0:
        raise RuntimeError(err or out or f"SSH failed ({p.returncode})")
    return out
