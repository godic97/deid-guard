import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from deidlib import cli
from deidlib.store import Store
from tests.fixtures import write_patients

CLI = Path(__file__).resolve().parents[1] / "deid.py"


class CliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.data = write_patients(self.root / "patients.csv")

    def tearDown(self):
        self.dir.cleanup()

    def run_cli(self, *args, stdin=None, ok=True):
        """Run the CLI in this process, so mutation testing can trace it."""
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(stdin) if stdin is not None else "")), \
                redirect_stdout(out):
            code = cli.main([*args, "--root", str(self.root)])
        self.assertEqual(code, 0 if ok else 1, out.getvalue())
        return json.loads(out.getvalue())

    def test_entry_point_runs_as_a_script(self):
        p = subprocess.run([sys.executable, str(CLI), "ping", "--root", str(self.root)],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(json.loads(p.stdout)["ok"])

    def test_ping(self):
        out = self.run_cli("ping")
        self.assertEqual(out["ok"], True)
        self.assertEqual(out["version"], __import__("deidlib").__version__)
        self.assertEqual(out["python"], sys.version.split()[0])

    def test_guard_then_apply_then_guard(self):
        self.assertEqual(self.run_cli("guard", str(self.data))["status"], "pending")
        out = self.run_cli("apply", str(self.data), stdin={"decisions": [{"column": "나이", "action": "generalize"}]})
        self.assertIn("나이: generalize (age)", out["summary"])
        self.assertEqual(self.run_cli("guard", str(self.data))["status"], "decided")

    def test_scrub_uses_masks_from_guard(self):
        self.run_cli("guard", "patients.csv")
        out = self.run_cli("scrub", stdin={"texts": ["홍길동 P-100120", "clean"]})
        self.assertEqual(out["texts"], ["NAME_000020 PATIENT_NO_000020", "clean"])
        self.assertEqual(out["hits"], 2)

    def test_restore_and_reveal(self):
        self.run_cli("scrub", stdin={"texts": ["010-1234-5678"]})
        self.assertEqual(self.run_cli("restore", stdin={"texts": ["call PHONE_000001"]})["texts"], ["call 010-1234-5678"])
        self.assertEqual(self.run_cli("reveal", "PHONE_000001")["raw"], "010-1234-5678")

    def test_errors_are_json(self):
        out = self.run_cli("apply", str(self.data), stdin={"decisions": [{"column": "x", "action": "keep"}]}, ok=False)
        self.assertIn("no column 'x'", out["error"])

    def test_status_lists_files(self):
        self.run_cli("guard", str(self.data))
        files = self.run_cli("status")["files"]
        self.assertEqual([(f["file"], f["status"]) for f in files], [("patients.csv", "pending")])

    def test_scan_profiles_data_files_but_skips_vendor_dirs(self):
        write_patients(self.root / "sub" / "more.csv", n=12)
        write_patients(self.root / "node_modules" / "pkg" / "x.csv", n=12)
        out = self.run_cli("scan")
        self.assertEqual(sorted(out["files"]), ["patients.csv", "sub/more.csv"])
        scrubbed = self.run_cli("scrub", stdin={"texts": ["P-100101"]})["texts"]
        self.assertEqual(scrubbed, ["PATIENT_NO_000001"])

    def test_scan_skips_config_json_and_leaves_its_values_alone(self):
        (self.root / "package.json").write_text('{"name": "my-app", "version": "1.0.0"}')
        out = self.run_cli("scan")
        self.assertEqual(out["files"], ["patients.csv"])
        self.assertEqual(self.run_cli("scrub", stdin={"texts": ["build my-app"]})["texts"], ["build my-app"])

    def test_guard_reads_non_tabular_json_as_is(self):
        (self.root / "cfg.json").write_text('{"name": "my-app"}')
        out = self.run_cli("guard", "cfg.json")
        self.assertEqual(out["status"], "not_data")
        self.assertEqual(out["read_path"], str((self.root / "cfg.json").resolve()))

    def test_scan_profiles_tables_before_hitting_the_file_limit(self):
        for i in range(5):
            (self.root / f"a{i}.json").write_text('{"k": 1}')
        old = cli.MAX_SCAN_FILES
        cli.MAX_SCAN_FILES = 3
        try:
            from deidlib.store import Store
            store = Store(self.root)
            out = cli.scan(store, self.root)
            store.close()
        finally:
            cli.MAX_SCAN_FILES = old
        self.assertIn("patients.csv", out["files"])


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name).resolve()
        self.store = Store(self.root)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_result_shape_and_order(self):
        write_patients(self.root / "z.csv", n=12)
        write_patients(self.root / "sub" / "a.csv", n=12)
        (self.root / "notes.txt").write_text("not data")
        out = cli.scan(self.store, self.root)
        self.assertEqual(out, {"files": ["sub/a.csv", "z.csv"], "errors": [], "truncated": False})

    def test_size_limit_is_inclusive(self):
        small = self.root / "a.csv"
        write_patients(small, n=12)
        size = small.stat().st_size
        write_patients(self.root / "b.csv", n=30)
        with mock.patch.object(cli, "MAX_SCAN_BYTES", size):
            self.assertEqual(cli.scan(self.store, self.root)["files"], ["a.csv"])

    def test_truncates_at_the_file_limit_with_tables_first(self):
        (self.root / "a.jsonl").write_text('{"id": "x1"}\n{"id": "x2"}\n')
        write_patients(self.root / "z.csv", n=12)
        with mock.patch.object(cli, "MAX_SCAN_FILES", 1):
            out = cli.scan(self.store, self.root)
        self.assertEqual(out, {"files": ["z.csv"], "errors": [], "truncated": True})

    def test_unreadable_files_are_reported(self):
        (self.root / "bad.json").write_text("{")
        out = cli.scan(self.store, self.root)
        self.assertEqual(out["files"], [])
        self.assertEqual([e["file"] for e in out["errors"]], ["bad.json"])
        self.assertTrue(out["errors"][0]["error"].startswith("JSONDecodeError: "))


class CommandDetailTest(CliTest):
    def run_raw(self, *args, stdin=""):
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(stdin)), redirect_stdout(out):
            code = cli.main(list(args))
        return code, out.getvalue()

    def test_empty_stdin_means_no_input(self):
        self.assertEqual(self.run_cli("scrub"), {"texts": [], "hits": 0})
        self.assertEqual(self.run_cli("restore"), {"texts": []})

    def test_missing_texts_key_means_no_texts(self):
        self.assertEqual(self.run_cli("scrub", stdin={}), {"texts": [], "hits": 0})
        self.assertEqual(self.run_cli("restore", stdin={}), {"texts": []})

    def test_plan_reports_unmasking(self):
        out = self.run_cli("plan", "patients.csv", stdin={"decisions": [{"column": "patient_no", "action": "keep"}]})
        self.assertEqual(out, {"file": "patients.csv", "unmasks": ["patient_no"], "columns": ["patient_no"]})
        self.assertFalse((self.root / ".deid" / "out").exists())

    def test_missing_decisions_mean_the_defaults(self):
        self.assertEqual(self.run_cli("plan", "patients.csv", stdin={})["unmasks"], [])
        self.assertEqual(self.run_cli("apply", "patients.csv", stdin={})["outputs"], [".deid/out/patients.csv"])

    def test_apply_with_a_relative_path(self):
        out = self.run_cli("apply", "patients.csv", stdin={"decisions": []})
        self.assertEqual(out["outputs"], [".deid/out/patients.csv"])

    def test_reveal_reports_the_token_and_its_original(self):
        self.run_cli("scrub", stdin={"texts": ["010-1234-5678"]})
        self.assertEqual(self.run_cli("reveal", "PHONE_000001"), {"token": "PHONE_000001", "raw": "010-1234-5678"})
        self.assertEqual(self.run_cli("reveal"), {"token": None, "raw": None})

    def test_status_lists_decided_files_with_outputs_and_skips_non_tables(self):
        for i in range(6):
            (self.root / f"cfg{i}.json").write_text('{"k": 1}')
            self.run_cli("guard", f"cfg{i}.json")
        self.run_cli("apply", "patients.csv", stdin={"decisions": []})
        files = self.run_cli("status")["files"]
        self.assertEqual(files, [{"file": "patients.csv", "status": "decided", "outputs": [".deid/out/patients.csv"]}])

    def test_status_of_a_pending_file_has_no_outputs(self):
        self.run_cli("guard", "patients.csv")
        self.assertEqual(self.run_cli("status")["files"], [{"file": "patients.csv", "status": "pending", "outputs": []}])

    def test_root_defaults_to_the_working_directory(self):
        self.run_cli("guard", "patients.csv")
        cwd = os.getcwd()
        os.chdir(self.root)
        try:
            code, out = self.run_raw("status")
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0)
        self.assertEqual([f["file"] for f in json.loads(out)["files"]], ["patients.csv"])

    def test_unknown_command_is_rejected_with_usage(self):
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as caught:
            cli.main(["nope", "--root", str(self.root)])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("usage: deid ", err.getvalue())
        self.assertIn("ping", err.getvalue())

    def test_output_keeps_non_ascii_text(self):
        code, out = self.run_raw("scrub", "--root", str(self.root), stdin=json.dumps({"texts": ["진료 기록"]}))
        self.assertEqual(code, 0)
        self.assertIn("진료 기록", out)

    def test_errors_keep_non_ascii_text_and_name_the_exception(self):
        code, out = self.run_raw("apply", "patients.csv", "--root", str(self.root),
                                 stdin=json.dumps({"decisions": [{"column": "없는열", "action": "keep"}]}))
        self.assertEqual(code, 1)
        self.assertIn("없는열", out)
        self.assertTrue(json.loads(out)["error"].startswith("ApplyError: "))

    def test_main_reads_sys_argv_when_no_argv_is_given(self):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["deid.py", "ping", "--root", str(self.root)]), redirect_stdout(out):
            self.assertEqual(cli.main(), 0)
        self.assertTrue(json.loads(out.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
