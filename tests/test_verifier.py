import json
import os
import tempfile
import unittest
from pathlib import Path


class BackupVerifierIntegrationTests(unittest.TestCase):
    def test_runner_verifies_backup_tree_and_writes_results(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "urbackup"
            data = Path(d) / "data"
            results = Path(d) / "results.json"
            backup = root / "MichaelD-ASUS" / "2026-06-11_0200"
            for folder in ["Windows", "Users/Michael", "Program Files/App"]:
                (backup / folder).mkdir(parents=True, exist_ok=True)
            for i in range(60):
                (backup / "Users" / "Michael" / f"file-{i}.txt").write_text(f"data {i}\n", encoding="utf-8")

            os.environ.update(
                {
                    "APP_DATA_DIR": str(data),
                    "APP_DB": str(data / "backup_verify.sqlite3"),
                    "RESULTS_FILE": str(results),
                    "BACKUP_ROOT": str(root),
                    "CLIENTS": "MichaelD-ASUS",
                    "SAMPLE_SIZE": "50",
                    "SMART_DEVICES": "",
                    "URBACKUP_URL": "http://127.0.0.1:9",
                    "TELEGRAM_BOT_TOKEN": "",
                    "TELEGRAM_HOME_CHANNEL": "",
                }
            )

            from backup_verify.config import Settings
            from backup_verify.db import Database
            from backup_verify.runner import VerificationRunner

            settings = Settings.from_env()
            runner = VerificationRunner(settings, Database(settings.db_path))
            payload = runner.run(notify=False)

            self.assertIn(payload["overall_status"], {"verified", "warning"})
            self.assertEqual(payload["clients"]["MichaelD-ASUS"]["files_checked"], 50)
            written = json.loads(results.read_text())
            self.assertIn("MichaelD-ASUS", written)
            self.assertEqual(written["MichaelD-ASUS"]["files_checked"], 50)


if __name__ == "__main__":
    unittest.main()
