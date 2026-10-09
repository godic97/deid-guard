import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from deidlib import cli
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
        from deidlib import cli
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


if __name__ == "__main__":
    unittest.main()
