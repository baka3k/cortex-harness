"""Worker per-file progress + two-stage SLL→LL (plan 261002-1410).

M1/M2: run the jar directly over the fixture corpus and assert the
``[vb6][worker]`` stderr progress protocol, the parse-timing worker_meta
fields and per-file ``parse_meta.worker_elapsed_ms``.

Plan 261002-1511 adds the pre-pass ``scanning i/N`` line kind: the pre-pass
(countSyntaxErrors) precedes the batch parse loop and used to be invisible
while hanging for minutes on pathological files (SLL pre-scan now bounds it).

M4 (parity): the same manifest parsed with two-stage SLL→LL and with forced
LL-only (``VB6_WORKER_SLL=0``) must produce identical file payloads
(canonical JSON byte-equal, timing fields excluded).

M3 (adapter relay): fake-process unit tests (no java) assert the
``[vb6][worker]`` progress lines relay live in EVERY run (verbose or not —
user override of AD-03: never swallow parse progress), ANTLR console noise
stays verbose-only, the slowest-file summary always prints, and the
timeout path still kills the subprocess and raises TimeoutExpired.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import contextlib
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.vb import vb6_antlr_adapter  # noqa: E402
from tools.vb.vb6_antlr_adapter import ensure_worker_built  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "vb6-application"
STRESS_FILE = ROOT / "tests" / "fixtures" / "vb6-two-stage" / "stress.cls"

JAVA_AVAILABLE = Path("/usr/bin/java").exists() or os.environ.get("JAVA_HOME") is not None

PROGRESS_LINE_RE = re.compile(
    r"^\[vb6\]\[worker\] parsed (\d+)/(\d+) file=(.*) ms=(\d+)( sll_fallback)?$"
)

START_LINE_RE = re.compile(r"^\[vb6\]\[worker\] parsing (\d+)/(\d+) file=(.*)$")

SCAN_LINE_RE = re.compile(r"^\[vb6\]\[worker\] scanning (\d+)/(\d+) file=(.*)$")


def _fixture_bas_cls() -> list:
    return sorted(p.name for p in FIXTURE_DIR.iterdir() if p.suffix.lower() in {".bas", ".cls"})


def _manifest_entries() -> list:
    entries = [{"file_path": name} for name in _fixture_bas_cls()]
    # single manifest root; parse_path bridges the sibling fixture dir
    entries.append({"file_path": "stress.cls", "parse_path": str(STRESS_FILE)})
    return entries


def _run_jar(extra_env: dict | None = None) -> subprocess.CompletedProcess:
    jar = ensure_worker_built()
    manifest = {
        "root": str(FIXTURE_DIR),
        "project": "Sample.vbp",
        "files": _manifest_entries(),
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(manifest, handle)
        manifest_path = handle.name
    try:
        env = dict(os.environ)
        # jar-backed assertions (sll_fallback marker, fallback count) require
        # two-stage ON — normalize an ambient VB6_WORKER_SLL out of the run
        env.setdefault("VB6_WORKER_SLL", "1")
        env.update(extra_env or {})
        return subprocess.run(
            ["java", "-jar", jar, "--manifest", manifest_path, "--workspace-timeout-ms", "180000"],
            capture_output=True, text=True, timeout=600, env=env, check=False,
        )
    finally:
        os.unlink(manifest_path)


def _canonical_files(stdout_text: str) -> str:
    """Canonical JSON of the files array with timing fields excluded."""
    data = json.loads(stdout_text)
    for item in data.get("files", []):
        payload = item.get("payload")
        if isinstance(payload, dict) and isinstance(payload.get("parse_meta"), dict):
            payload["parse_meta"].pop("worker_elapsed_ms", None)
    return json.dumps(data.get("files"), sort_keys=True, separators=(",", ":"))


@unittest.skipUnless(JAVA_AVAILABLE, "java not available")
class WorkerProgressTest(unittest.TestCase):
    """M1 + M2: stderr progress lines, timing meta, per-file elapsed."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.proc = _run_jar()

    def test_stdout_contract_untouched(self) -> None:
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr[-2000:])
        # stdout must stay ONE json document — any leaked stderr line would
        # break json.loads here
        data = json.loads(self.proc.stdout)
        self.assertIn("files", data)
        self.assertNotIn("[vb6][worker]", self.proc.stdout)

    def test_progress_line_per_file(self) -> None:
        lines = [line for line in self.proc.stderr.splitlines()
                 if PROGRESS_LINE_RE.match(line)]
        expected = len(_manifest_entries())
        self.assertEqual(len(lines), expected,
                         f"expected {expected} progress lines, got {len(lines)}")
        seen_index = []
        for line in lines:
            match = PROGRESS_LINE_RE.match(line)
            index, total, _name, ms, _fb = match.groups()
            seen_index.append(int(index))
            self.assertEqual(int(total), expected)
            self.assertGreaterEqual(int(ms), 0)
        self.assertEqual(seen_index, list(range(1, expected + 1)),
                         "progress counter must be sequential 1..N")

    def test_start_line_per_file_precedes_parsed(self) -> None:
        # the `parsing i/N` line names the file BEFORE its parse begins, so a
        # multi-minute ANTLR prediction still shows which file it is stuck on
        stderr_lines = self.proc.stderr.splitlines()
        starts = [START_LINE_RE.match(line) for line in stderr_lines if START_LINE_RE.match(line)]
        expected = len(_manifest_entries())
        self.assertEqual(len(starts), expected,
                         f"expected {expected} start lines, got {len(starts)}")
        self.assertEqual([int(match.group(1)) for match in starts],
                         list(range(1, expected + 1)),
                         "start counter must be sequential 1..N")
        first_parsed_index = min(
            index for index, line in enumerate(stderr_lines) if PROGRESS_LINE_RE.match(line)
        )
        first_start_index = min(
            index for index, line in enumerate(stderr_lines) if START_LINE_RE.match(line)
        )
        self.assertLess(first_start_index, first_parsed_index,
                        "first `parsing` line must precede the first `parsed` line")

    def test_scanning_line_per_file_precedes_parsing(self) -> None:
        # the pre-pass `scanning i/N` line must name every file BEFORE the
        # batch parse loop starts (plan 261002-1511: a pre-pass stall used to
        # be completely invisible)
        stderr_lines = self.proc.stderr.splitlines()
        scans = [SCAN_LINE_RE.match(line) for line in stderr_lines if SCAN_LINE_RE.match(line)]
        expected = len(_manifest_entries())
        self.assertEqual(len(scans), expected,
                         f"expected {expected} scanning lines, got {len(scans)}")
        self.assertEqual([int(match.group(1)) for match in scans],
                         list(range(1, expected + 1)),
                         "scanning counter must be sequential 1..N")
        self.assertEqual({int(match.group(2)) for match in scans}, {expected})
        first_scan_index = min(
            index for index, line in enumerate(stderr_lines) if SCAN_LINE_RE.match(line)
        )
        first_start_index = min(
            index for index, line in enumerate(stderr_lines) if START_LINE_RE.match(line)
        )
        self.assertLess(first_scan_index, first_start_index,
                        "first `scanning` line must precede the first `parsing` line")

    def test_sll_fallback_marker(self) -> None:
        # malformed.bas has a real syntax error: SLL stage must bail and its
        # line carries the marker; the SLL-clean fixture must not (M4 evidence)
        fallback_files = {PROGRESS_LINE_RE.match(line).group(3)
                          for line in self.proc.stderr.splitlines()
                          if PROGRESS_LINE_RE.match(line) and PROGRESS_LINE_RE.match(line).group(5)}
        clean_files = {PROGRESS_LINE_RE.match(line).group(3)
                       for line in self.proc.stderr.splitlines()
                       if PROGRESS_LINE_RE.match(line) and not PROGRESS_LINE_RE.match(line).group(5)}
        self.assertIn("malformed.bas", fallback_files)
        self.assertIn("stress.cls", clean_files, "SLL-clean stress fixture must not bail")
        self.assertIn("modUtil.bas", clean_files)

    def test_worker_meta_timing_fields(self) -> None:
        meta = json.loads(self.proc.stdout)["worker_meta"]
        self.assertGreater(meta["parse_ms_total"], 0)
        self.assertTrue(meta["parse_slowest_file"])
        self.assertGreaterEqual(meta["parse_slowest_ms"], 0)
        self.assertIn("sll_fallback_files", meta)
        self.assertGreaterEqual(meta["sll_fallback_files"], 1,
                                "malformed.bas must take the SLL fallback (bail)")

    def test_parse_meta_worker_elapsed_filled(self) -> None:
        data = json.loads(self.proc.stdout)
        elapsed = [
            item["payload"]["parse_meta"]["worker_elapsed_ms"]
            for item in data["files"]
            if item.get("ok")
        ]
        self.assertTrue(elapsed)
        self.assertTrue(any(ms > 0 for ms in elapsed),
                        "at least one payload must carry a real worker_elapsed_ms")


@unittest.skipUnless(JAVA_AVAILABLE, "java not available")
class TwoStageParityTest(unittest.TestCase):
    """M4: two-stage (SLL→LL) and forced LL-only produce identical files."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.sll_files = _canonical_files(_run_jar().stdout)
        cls.ll_files = _canonical_files(_run_jar({"VB6_WORKER_SLL": "0"}).stdout)

    def test_payloads_byte_equal(self) -> None:
        self.assertEqual(self.sll_files, self.ll_files,
                         "two-stage parse must be payload-identical to LL-only")

    def test_malformed_isolated_in_both_modes(self) -> None:
        for label, files_json in (("sll", self.sll_files), ("ll", self.ll_files)):
            files = json.loads(files_json)
            by_path = {item["file_path"]: item for item in files}
            self.assertFalse(by_path["malformed.bas"].get("ok"),
                             f"malformed.bas must stay ok=false in {label} mode")
            self.assertTrue(by_path["stress.cls"].get("ok"),
                            f"stress.cls must parse in {label} mode")


class _FakeProc:
    """Minimal Popen stand-in: piped stdout/stderr, optional hang on wait."""

    def __init__(self, stdout: str, stderr: str, hang: bool = False,
                 returncode: int = 0) -> None:
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)
        self._hang = hang
        self.returncode = returncode
        self.killed = False

    def wait(self, timeout=None) -> int:
        if self._hang:
            raise subprocess.TimeoutExpired(self, timeout)
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self._hang = False


def _run_adapter(fake_proc: _FakeProc, *, verbose: bool = False,
                 timeout_sec: float = 30.0):
    with mock.patch.object(vb6_antlr_adapter, "ensure_worker_built",
                           return_value="/fake/vb6-antlr-worker.jar"), \
         mock.patch.object(vb6_antlr_adapter.subprocess, "Popen",
                           return_value=fake_proc) as popen_mock:
        captured_out, captured_err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(captured_out), \
             contextlib.redirect_stderr(captured_err):
            payloads, errors, meta = vb6_antlr_adapter.parse_vb6_files_with_antlr(
                root=str(ROOT / "tests" / "fixtures" / "vb6-application"),
                files=["modMain.bas"],
                timeout_sec=timeout_sec,
                verbose=verbose,
            )
    return payloads, errors, meta, captured_out.getvalue(), popen_mock


FAKE_STDOUT = json.dumps({
    "files": [
        {"file_path": "modMain.bas", "ok": True, "payload": {"functions": []}},
        {"file_path": "malformed.bas", "ok": False, "error": "syntax"},
    ],
    "worker_meta": {
        "parse_ms_total": 321,
        "parse_slowest_file": "modMain.bas",
        "parse_slowest_ms": 210,
        "sll_fallback_files": 1,
        "elapsed_ms": 500,
        "files": 2,
    },
})
FAKE_STDERR = (
    "[vb6][worker] scanning 1/2 file=modMain.bas\n"
    "[vb6][worker] scanning 2/2 file=my mod.bas\n"
    "[vb6][worker] parsing 1/2 file=modMain.bas\n"
    "[vb6][worker] parsed 1/2 file=modMain.bas ms=111\n"
    "[vb6][worker] parsing 2/2 file=my mod.bas\n"
    "[vb6][worker] parsed 2/2 file=my mod.bas ms=210 sll_fallback\n"
    "line 7:18 no viable alternative at input 'Option Explicit'\n"
)


class AdapterRelayTest(unittest.TestCase):
    """M3: progress relays live in every run; ANTLR noise verbose-only;
    always-on slowest summary."""

    def test_progress_relays_live_without_verbose_noise_hidden(self) -> None:
        fake = _FakeProc(stdout=FAKE_STDOUT, stderr=FAKE_STDERR)
        payloads, errors, meta, out, _popen = _run_adapter(fake, verbose=False)
        self.assertIn("modMain.bas", payloads)
        self.assertEqual(errors.get("malformed.bas"), "syntax")
        self.assertEqual(meta["parse_ms_total"], 321)
        self.assertEqual(meta["sll_fallback_files"], 1)
        # progress must never be swallowed, even without --verbose —
        # pre-pass scanning lines included (plan 261002-1511)
        self.assertIn("[vb6][worker] scanning 1/2 file=modMain.bas", out)
        self.assertIn("[vb6][worker] parsing 1/2 file=modMain.bas", out)
        self.assertIn("[vb6][worker] parsed 1/2 file=modMain.bas ms=111", out)
        self.assertIn("[vb6][worker] parsed 2/2 file=my mod.bas ms=210 sll_fallback", out)
        self.assertIn(
            "[vb6][engine] parse: 2 files in 0.3s; slowest: modMain.bas (210ms)",
            out,
        )
        # ANTLR console noise stays verbose-only
        self.assertNotIn("no viable alternative", out)

    def test_summary_ignores_start_lines(self) -> None:
        # the `parsing` start lines must not inflate the aggregated file count
        fake = _FakeProc(stdout=FAKE_STDOUT, stderr=FAKE_STDERR)
        _payloads, _errors, _meta, out, _popen = _run_adapter(fake, verbose=False)
        self.assertIn("parse: 2 files in", out)

    def test_verbose_relays_progress_and_noise(self) -> None:
        fake = _FakeProc(stdout=FAKE_STDOUT, stderr=FAKE_STDERR)
        _payloads, _errors, _meta, out, _popen = _run_adapter(fake, verbose=True)
        self.assertIn("[vb6][worker] parsing 2/2 file=my mod.bas", out)
        self.assertIn("[vb6][worker] parsed 1/2 file=modMain.bas ms=111", out)
        self.assertIn("[vb6][worker] parsed 2/2 file=my mod.bas ms=210 sll_fallback", out)
        self.assertIn("no viable alternative", out)
        # top-5 slowest, descending: 210ms row before the 111ms row
        self.assertIn("210ms  my mod.bas", out)
        self.assertIn("111ms  modMain.bas", out)

    def test_timeout_raises_and_kills_process(self) -> None:
        fake = _FakeProc(stdout="", stderr="", hang=True)
        with self.assertRaises(subprocess.TimeoutExpired):
            _run_adapter(fake, timeout_sec=0.2)
        self.assertTrue(fake.killed, "watchdog must kill the hung worker")

    def test_nonzero_rc_surfaces_stderr_tail(self) -> None:
        fake = _FakeProc(stdout="{}", stderr="[vb6][worker] parsed 1/1 file=a.bas ms=5\n"
                                             "boom: fatal jvm\n", returncode=1)
        with self.assertRaises(RuntimeError) as ctx:
            _run_adapter(fake)
        self.assertIn("boom: fatal jvm", str(ctx.exception))

    def test_summary_keeps_only_last_run_after_retry_marker(self) -> None:
        # batch retry (Vb6Worker): the worker re-emits rows for surviving
        # files after a `[vb6][worker] retry` marker — the summary must count
        # only the last run, matching worker_meta's last-run aggregation (R4)
        retry_stderr = (
            "[vb6][worker] parsing 1/2 file=modMain.bas\n"
            "[vb6][worker] parsed 1/2 file=modMain.bas ms=111\n"
            "[vb6][worker] parsing 2/2 file=boom.bas\n"
            "[vb6][worker] retry 1/2 files after batch failure\n"
            "[vb6][worker] parsing 1/1 file=modMain.bas\n"
            "[vb6][worker] parsed 1/1 file=modMain.bas ms=90\n"
        )
        retry_stdout = json.dumps({
            "files": [{"file_path": "modMain.bas", "ok": True, "payload": {}}],
            "worker_meta": {"parse_ms_total": 90, "parse_slowest_file": "modMain.bas",
                            "parse_slowest_ms": 90, "sll_fallback_files": 0},
        })
        fake = _FakeProc(stdout=retry_stdout, stderr=retry_stderr)
        _p, _e, _m, out, _popen = _run_adapter(fake, verbose=False)
        self.assertIn("[vb6][engine] parse: 1 files in 0.1s; slowest: modMain.bas (90ms)", out)
        self.assertNotIn("111", out.split("[vb6][engine]")[1])


@unittest.skipUnless(JAVA_AVAILABLE, "java not available")
class CommentRunSllFenceTest(unittest.TestCase):
    """A long full-line comment run is a newline run on the default channel.
    LL ALL(*) on module's NEWLINE* chain is quadratic in that run (400 lines
    ~4.7s, 800 lines does not finish). The fence must parse it with SLL even
    when VB6_WORKER_SLL=0, and keep the procedure's line number."""

    def test_long_comment_run_bounded_when_sll_disabled(self) -> None:
        comment_lines = 400
        sub_line = 4 + comment_lines  # VERSION, Attribute, Option Explicit, then comments
        body = "\n".join(
            ["VERSION 1.0 CLASS", 'Attribute VB_Name = "Fence"', "Option Explicit"]
            + ["' commented-out procedure"] * comment_lines
            + ["Public Sub Foo()", "End Sub", ""]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "Fence.cls"
            source.write_text(body, encoding="utf-8")
            manifest = {
                "root": str(root),
                "project": "",
                "files": [{"file_path": "Fence.cls"}],
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            jar = ensure_worker_built()
            env = dict(os.environ)
            env["VB6_WORKER_SLL"] = "0"
            proc = subprocess.run(
                ["java", "-jar", jar, "--manifest", str(manifest_path),
                 "--workspace-timeout-ms", "15000"],
                capture_output=True, text=True, timeout=60, env=env, check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        data = json.loads(proc.stdout)
        item = data["files"][0]
        self.assertTrue(item.get("ok"), item.get("error"))
        functions = item["payload"]["functions"]
        self.assertEqual([fn["name"] for fn in functions], ["Foo"])
        self.assertEqual(functions[0]["start_line"], sub_line)
        meta = data["worker_meta"]
        self.assertGreaterEqual(meta["sll_fenced_files"], 1)
        self.assertLess(meta["parse_slowest_ms"], 2000,
                        "fenced file must stay on SLL; LL on 400 comment lines is ~5s")


class AdapterSllDefaultTest(unittest.TestCase):
    """Go/no-go (plan 261002-1410 phase 03.2): measured 33% SLL-bail on the
    legacy corpus, so two-stage ships OFF through the adapter; an explicit
    VB6_WORKER_SLL=1 in the environment opts back in."""

    def test_sll_disabled_by_default(self) -> None:
        fake = _FakeProc(stdout=FAKE_STDOUT, stderr=FAKE_STDERR)
        with mock.patch.dict(os.environ):
            os.environ.pop("VB6_WORKER_SLL", None)
            _p, _e, _m, _out, popen = _run_adapter(fake, verbose=False)
        env = popen.call_args.kwargs["env"]
        self.assertEqual(env.get("VB6_WORKER_SLL"), "0")

    def test_explicit_opt_in_is_honored(self) -> None:
        fake = _FakeProc(stdout=FAKE_STDOUT, stderr=FAKE_STDERR)
        environment = dict(os.environ)
        environment["VB6_WORKER_SLL"] = "1"
        with mock.patch.dict(os.environ, environment, clear=False):
            _p, _e, _m, _out, popen = _run_adapter(fake, verbose=False)
        env = popen.call_args.kwargs["env"]
        self.assertEqual(env.get("VB6_WORKER_SLL"), "1")


if __name__ == "__main__":
    unittest.main()
