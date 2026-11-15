#!/usr/bin/env python3
"""Offline tests for the MCP server, over stdio as a client starts it: tool schemas, argument checks, errors, stop.

They need the MCP SDK, so they skip under a Python without it. Run them on the server's own Python:
tools/mcp-venv/bin/python -B -m unittest discover -s tests -p "test_mcp*.py"
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.dont_write_bytecode = True
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard  # noqa: E402
os.environ.setdefault("CTUNE_DATA", tempfile.mkdtemp(prefix="ctune-test-data-"))

# The real server plus two tools that touch no hardware: one that fails, one that blocks until it is stopped.
SERVER = """
import sys
import time
sys.path.insert(0, {repo!r})
from ctune import mcp_server

@mcp_server.tool
def fail(core: int):
    \"\"\"Raise the way a guardrail does.\"\"\"
    raise ValueError(f"core {{core}} does not exist")

@mcp_server.tool
def block(marker: str):
    \"\"\"Block like an inline test, and leave a marker when its finally runs.\"\"\"
    open(marker + ".started", "w").close()
    try:
        time.sleep(60)
    finally:
        open(marker, "w").write("stopped")

mcp_server.main()
"""


@guard.needs_mcp_sdk
class Stdio(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ctune-test-mcp-")
        script = os.path.join(self.dir, "server.py")
        with open(script, "w") as f:
            f.write(SERVER.format(repo=REPO))
        self.proc = subprocess.Popen([sys.executable, "-I", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True,
                                     env={**os.environ, "CTUNE_DATA": os.path.join(self.dir, "data")})
        self.addCleanup(self.stop)
        self.id = 0
        self.init = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                "clientInfo": {"name": "test", "version": "0"}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def stop(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()
        for pipe in (self.proc.stdin, self.proc.stdout):
            pipe.close()

    def send(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params=None, wait=True):
        self.id += 1
        self.send({"jsonrpc": "2.0", "id": self.id, "method": method, "params": params or {}})
        return json.loads(self.proc.stdout.readline())["result"] if wait else None

    def call(self, name, **arguments):
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        return result["isError"], result["content"][0]["text"]

    def test_the_server_names_itself_and_explains_the_domains(self):
        self.assertEqual(self.init["serverInfo"]["name"], "ctune")
        self.assertIn("mem_*", self.init["instructions"])

    def test_every_tool_has_a_description_and_rejects_unknown_arguments(self):
        tools = {t["name"]: t for t in self.request("tools/list")["tools"]}
        for name in ("errors", "mem_snapshot", "cpu_apply", "gpu_apply", "gpu_test", "signoff", "test_status"):
            self.assertIn(name, tools)
        for name, t in tools.items():
            self.assertTrue(t["description"], name)
            self.assertNotIn("\n", t["description"], name)
            self.assertIs(t["inputSchema"]["additionalProperties"], False, name)
        self.assertEqual(tools["cpu_apply"]["inputSchema"]["required"], ["core", "margin"])
        self.assertEqual(tools["mem_test"]["inputSchema"]["properties"]["kind"]["enum"], ["screen", "fabric", "tm5"])

    def test_a_call_runs_the_ctune_function_and_returns_its_json(self):
        failed, text = self.call("journal_add", kind="note", text="from a test", data={"domain": "mem", "step": 3})
        self.assertFalse(failed, text)
        failed, text = self.call("journal_list", n=1)
        self.assertFalse(failed, text)
        record = json.loads(text)[-1]
        self.assertEqual((record["kind"], record["text"], record["domain"], record["step"]),
                         ("note", "from a test", "mem", 3))

    def test_a_misspelt_argument_is_an_error_not_a_default(self):
        failed, text = self.call("journal_list", number=1)
        self.assertTrue(failed)
        self.assertIn("number", text)

    def test_a_wrong_type_or_value_is_an_error(self):
        self.assertTrue(self.call("fail", core="first")[0])
        self.assertTrue(self.call("journal_add", kind="verdict", text="x")[0])

    def test_a_ctune_error_reaches_the_agent_with_its_text(self):
        failed, text = self.call("fail", core=99)
        self.assertTrue(failed)
        self.assertIn("ValueError: core 99 does not exist", text)

    def test_stopping_the_server_during_a_call_unwinds_the_call_first(self):
        """An inline test stops its loads in a `finally`; a server that died without running it would orphan them."""
        marker = os.path.join(self.dir, "marker")
        self.request("tools/call", {"name": "block", "arguments": {"marker": marker}}, wait=False)
        deadline = time.monotonic() + 10
        while not os.path.exists(marker + ".started"):
            self.assertLess(time.monotonic(), deadline, "the call never started")
            time.sleep(0.05)
        self.proc.send_signal(signal.SIGTERM)
        self.assertEqual(self.proc.wait(timeout=10), 128 + signal.SIGTERM)
        with open(marker) as f:
            self.assertEqual(f.read(), "stopped")


@guard.needs_mcp_sdk
class Glue(unittest.TestCase):
    def test_gpu_test_passes_only_what_the_caller_gave(self):
        """The defaults live in gpu.test, so the server forwards no value the agent did not set."""
        from ctune import mcp_server
        from ctune.gpu import api as gpu
        with mock.patch.object(mcp_server, "_run") as run:
            mcp_server.gpu_test(tc_seconds=300)
        self.assertEqual(run.call_args.args[:3], ("gpu_test", gpu.test, True))
        self.assertEqual(run.call_args.kwargs, {"tc_seconds": 300})

    def test_detached_start_passes_the_tests_own_kind(self):
        """mem_test and gpu_test take a `kind` argument, which must not collide with the runner's own."""
        from ctune import mcp_server
        from ctune.core import runner, stress
        failed = mock.Mock(returncode=1, stderr="stubbed")
        with mock.patch.object(stress, "check_interrupted"), mock.patch.object(stress, "running", return_value=None), \
                mock.patch.object(runner, "units", return_value=[]), \
                mock.patch.object(runner.subprocess, "run", return_value=failed) as run:
            with self.assertRaisesRegex(RuntimeError, "systemd-run failed"):
                mcp_server._run("mem_test", None, True, kind="fabric", minutes=None)
        spec = json.loads(run.call_args.args[0][-1])
        self.assertEqual(spec, {"kind": "mem_test", "kwargs": {"kind": "fabric", "minutes": None}})
        self.assertIn(f"--setenv=CTUNE_DATA={stress.DATA}", run.call_args.args[0])   # the unit writes where we read
        inline = mock.Mock(return_value="ran")
        self.assertEqual(mcp_server._run("gpu_test", inline, False, kind="core"), "ran")
        inline.assert_called_once_with(kind="core")


if __name__ == "__main__":
    unittest.main()
