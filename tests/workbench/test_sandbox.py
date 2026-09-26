"""The code sandbox, exercised through the real subprocess backend.

Every test spawns an isolated interpreter, so the file takes a few seconds; the limits are set
low so the failure cases end quickly.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from workbench.sandbox import SandboxLimits, SandboxRunLog, SandboxRunner
from workbench.sandbox.manifest import ManifestError, build_manifest, verify_manifest
from workbench.sandbox.verify import analyse


@pytest.fixture(scope="module")
def runner(tmp_path_factory):
    root = tmp_path_factory.mktemp("sandbox")
    return SandboxRunner(root, SandboxLimits(timeout_seconds=6, memory_mb=128, cpu_seconds=6),
                         backend="subprocess", log=SandboxRunLog(root / "runs.jsonl"))


def test_prints_stdout_and_exit_code(runner):
    res = runner.run("print('hello sandbox')")
    assert res.ok and res.exit_code == 0
    assert res.stdout.strip() == "hello sandbox"
    assert res.backend == "subprocess"
    assert res.workdir_destroyed
    assert res.python_version
    failing = runner.run("import sys\nraise SystemExit(3)")
    assert failing.exit_code == 3 and not failing.ok


def test_timeout_kills_infinite_loop(tmp_path):
    r = SandboxRunner(tmp_path, SandboxLimits(timeout_seconds=2, memory_mb=128), backend="subprocess")
    res = r.run("while True:\n    pass")
    assert res.timed_out and res.limit_hit == "timeout"
    assert not res.ok
    assert res.workdir_destroyed
    assert not list(tmp_path.glob("sbx-*"))


def test_network_egress_is_blocked(runner):
    code = ("import socket\n"
            "try:\n"
            "    socket.create_connection(('8.8.8.8', 53), timeout=1)\n"
            "    print('connected')\n"
            "except PermissionError as exc:\n"
            "    print('blocked', exc)\n")
    res = runner.run(code)
    assert "blocked" in res.stdout and "connected" not in res.stdout
    assert res.egress_attempts and "8.8.8.8" in res.egress_attempts[0]
    assert res.limit_hit == "egress"
    raw = runner.run("import socket\nsocket.socket()")
    assert raw.exit_code != 0 and "egress is disabled" in raw.stderr


def test_dangerous_imports_are_refused(runner):
    for mod in ("subprocess", "ctypes", "multiprocessing", "urllib.request"):
        res = runner.run(f"import {mod}\nprint('imported')")
        assert res.exit_code != 0, mod
        assert "not permitted" in res.stderr, mod
        assert "imported" not in res.stdout


def test_os_spawn_and_delete_are_stubbed(runner):
    res = runner.run("import os\nos.system('echo hi')")
    assert res.exit_code != 0 and "os.system is not permitted" in res.stderr
    res = runner.run("import os\nopen('a.txt', 'w').write('x')\nos.remove('a.txt')")
    assert res.exit_code != 0 and "os.remove is not permitted" in res.stderr


def test_writes_inside_workdir_are_returned_with_hashes(runner):
    res = runner.run("open('result.csv', 'wb').write(b'a,b\\n1,2\\n')")
    assert res.ok
    assert [f.path for f in res.files_written] == ["result.csv"]
    assert res.files_written[0].sha256 == hashlib.sha256(b"a,b\n1,2\n").hexdigest()
    assert res.files_written[0].bytes == 8
    assert "result.csv" in res.artifacts
    import base64

    assert base64.b64decode(res.artifacts["result.csv"]) == b"a,b\n1,2\n"


def test_writes_outside_workdir_are_refused(runner, tmp_path):
    target = tmp_path / "escaped.txt"
    code = f"open({str(target)!r}, 'w').write('x')"
    res = runner.run(code)
    assert res.exit_code != 0
    assert "outside the working directory" in res.stderr
    assert not target.exists()
    via_pathlib = runner.run(f"import pathlib\npathlib.Path({str(target)!r}).write_text('x')")
    assert via_pathlib.exit_code != 0 and not target.exists()


def test_inputs_are_visible_and_changes_tracked(runner):
    res = runner.run("print(open('data.txt').read().strip())\nopen('data.txt', 'a').write('more')",
                     inputs={"data.txt": "seed"})
    assert res.ok and res.stdout.strip() == "seed"
    assert res.files_changed == [{"path": "data.txt", "status": "modified"}]
    assert not res.files_written           # inputs are not counted as produced files


def test_memory_bomb_is_stopped(tmp_path):
    r = SandboxRunner(tmp_path, SandboxLimits(timeout_seconds=8, memory_mb=128), backend="subprocess")
    res = r.run("x = bytearray(2 * 1024 ** 3)\nprint(len(x))")
    assert not res.ok
    assert res.limit_hit in ("memory", "timeout")
    assert "2147483648" not in res.stdout
    if os.name == "nt":
        assert res.job_object


def test_disk_write_cap(tmp_path):
    r = SandboxRunner(tmp_path, SandboxLimits(timeout_seconds=8, memory_mb=256, max_disk_write_mb=1), backend="subprocess")
    res = r.run("open('big.bin', 'w').write('a' * (3 * 1024 * 1024))")
    assert res.limit_hit == "disk" and not res.ok


def test_tests_decide_verification(runner):
    code = "def add(a, b):\n    return a + b\n"
    good = runner.run(code, tests="def test_add():\n    assert add(1, 2) == 3\n")
    assert good.ok and good.verified
    assert good.verification["tests_total"] == 1 and good.verification["tests_passed"] == 1
    bad = runner.run(code, tests="def test_add():\n    assert add(1, 2) == 4\n")
    assert bad.ok                                   # the code itself ran fine
    assert not bad.verified                         # but it did not pass its tests
    assert bad.verification["failures"] and "test_add" in bad.verification["failures"][0]
    untested = runner.run(code)
    assert untested.ok and not untested.verified    # ran without crashing is not verified


def test_static_analysis_flags_eval_and_banned_imports(runner):
    report = analyse("import subprocess\nx = eval('1+1')\nopen('/etc/passwd')")
    assert not report["ok"]
    assert any("eval" in c for c in report["banned_calls"])
    assert any("absolute path" in c for c in report["banned_calls"])
    assert any("subprocess" in i for i in report["banned_imports"])
    clean = analyse("import math\nprint(math.sqrt(4))")
    assert clean["ok"] and clean["complexity"]["lines"] == 2
    res = runner.run("x = eval('1+1')\nprint(x)", tests="def test_x():\n    assert x == 2\n")
    assert res.verification["passed"]
    assert not res.verified                          # static analysis vetoes the verdict
    assert not res.static_analysis["ok"]
    broken = analyse("def f(:\n  pass")
    assert not broken["ok"] and broken["syntax_error"]


def test_run_log_is_chained_and_one_entry_per_run(runner):
    before = len(runner.log)
    runner.run("print(1)")
    runner.run("print(2)")
    assert len(runner.log) == before + 2
    check = runner.log.verify()
    assert check.ok and check.entries == before + 2
    rows = runner.log.read(2)
    assert all("stdout_sha256" in r and "hash" in r and "prev_hash" in r for r in rows)
    assert rows[1]["prev_hash"] == rows[0]["hash"]
    # tampering with an old line is detected
    lines = runner.log.path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    row["ok"] = not row["ok"]
    lines[0] = json.dumps(row)
    tampered = Path(str(runner.log.path) + ".tampered")
    tampered.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert not SandboxRunLog(tampered).verify().ok


def test_runs_are_deterministic(runner):
    code = "import random\nprint(random.random(), hash('abc') % 1000)"
    a = runner.run(code)
    b = runner.run(code)
    assert a.ok and b.ok and a.stdout == b.stdout


def test_environment_is_isolated(runner):
    res = runner.run("import os, sys\nprint(sorted(os.environ))\nprint(any('site-packages' in p for p in sys.path))")
    assert res.ok
    assert "['PYTHONHASHSEED', 'SANDBOX']" in res.stdout
    assert "False" in res.stdout.splitlines()[-1]


def test_output_is_truncated(tmp_path):
    r = SandboxRunner(tmp_path, SandboxLimits(timeout_seconds=8, max_output_bytes=500), backend="subprocess")
    res = r.run("print('x' * 5000)")
    assert "truncated" in res.stdout and len(res.stdout) < 700


def test_result_is_json_serialisable(runner):
    res = runner.run("open('f.bin', 'wb').write(bytes(range(256)))")
    json.dumps(res.model_dump(mode="json"))


def test_vendor_manifest_pins_and_refuses_drift(tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "helper.py").write_text("VALUE = 42\n", encoding="utf-8")
    manifest = build_manifest(vendor)
    assert [v.path for v in manifest.vendor] == ["helper.py"]
    assert verify_manifest(vendor).hash == manifest.hash
    r = SandboxRunner(tmp_path / "runs", SandboxLimits(timeout_seconds=6), backend="subprocess", vendor_dir=vendor)
    res = r.run("import helper\nprint(helper.VALUE)")
    assert res.ok and res.stdout.strip() == "42"
    assert res.manifest_hash == manifest.hash
    (vendor / "helper.py").write_text("VALUE = 43\n", encoding="utf-8")
    with pytest.raises(ManifestError):
        verify_manifest(vendor)
    drifted = SandboxRunner(tmp_path / "runs2", SandboxLimits(timeout_seconds=6), backend="subprocess", vendor_dir=vendor)
    res = drifted.run("import helper\nprint(helper.VALUE)")
    assert res.exit_code != 0 and "refused" in (res.error or "")
    assert drifted.manifest_hash == "invalid"


def test_unvendored_requirement_is_reported(runner):
    res = runner.run("print('ok')", requirements=["numpy"])
    assert res.stdout.strip() == "ok"
    assert not res.ok and "unvendored" in (res.error or "")
