"""Shared utilities for Hyperliquid trader and dashboard."""

import os
from pathlib import Path

# Symbol decimal precision for order sizing on Hyperliquid
SZ_DECIMALS = {"BTC": 5, "ETH": 4, "SOL": 2}


def close_sdk_sessions(*clients):
    """Close Hyperliquid SDK HTTP sessions to prevent leaked sockets."""
    for client in clients:
        if client and hasattr(client, "session"):
            try:
                client.session.close()
            except Exception:
                pass


def count_open_fds():
    """Count open file descriptors (portable across macOS and Linux)."""
    for path in ("/proc/self/fd", "/dev/fd"):
        if os.path.isdir(path):
            try:
                return len(os.listdir(path))
            except OSError:
                continue
    return -1


def load_dotenv(env_path, force=False):
    """Load .env file into os.environ.

    Args:
        env_path: Path to .env file
        force: If True, overwrite existing env vars. If False, use setdefault.
    """
    env_path = Path(env_path)
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        # Strip surrounding quotes if present
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        if force:
            os.environ[k] = v
        else:
            os.environ.setdefault(k, v)
