"""The virtual environment the MCP server runs in: tools/mcp-venv, holding the official MCP Python SDK.

requirements-mcp.txt pins the SDK and everything it depends on by version and SHA256, and pip installs wheels only, so
no package runs build code. The rest of ctune needs no package, so the system Python stays as it is.
"""
import os
import subprocess
import sys

from ctune.core.paths import REPO, TOOLS

VENV = os.path.join(TOOLS, "mcp-venv")
PYTHON = os.path.join(VENV, "bin", "python")
LOCK = os.path.join(REPO, "requirements-mcp.txt")


def setup():
    """Create the environment from scratch and install the locked packages (about 60 MB on disk)."""
    subprocess.run([sys.executable, "-m", "venv", "--clear", VENV], check=True)
    r = subprocess.run([PYTHON, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--require-hashes",
                        "--only-binary", ":all:", "-r", LOCK], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"pip install failed ({r.returncode}): {(r.stdout + r.stderr)[-1500:]}")
    sdk = subprocess.run([PYTHON, "-I", "-c", "from importlib.metadata import version; print(version('mcp'))"],
                         capture_output=True, text=True, check=True).stdout.strip()
    return {"venv": VENV, "mcp_sdk": sdk, "launcher": os.path.join(REPO, "bin", "ctune-mcp"),
            "next": "restart the agent session, so that it starts the server again"}
