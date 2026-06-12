import json,os,tempfile,unittest
from pathlib import Path
class BackupVerifyTests(unittest.TestCase):
    def test_checksum_and_random_file_verification(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); latest=root/'MichaelD-ASUS'/'2026-06-11'; (latest/'Users').mkdir(parents=True); (latest/'Windows').mkdir(); (latest/'Program Files').mkdir()
            f=latest/'Users'/'sample.txt'; f.write_text('restorable')
            from backup_verify.checks import sha256_file,verify_client
            (latest/'checksums.txt').write_text(f'{sha256_file(f)}  Users/sample.txt\n')
            os.environ.update({'BACKUP_ROOT':str(root),'CLIENTS':'MichaelD-ASUS','SAMPLE_SIZE':'1','URBACKUP_URL':'http://127.0.0.1:9','TELEGRAM_BOT_TOKEN':'','TELEGRAM_HOME_CHANNEL':''})
            from backup_verify.config import Settings
            r=verify_client(Settings.from_env(),'MichaelD-ASUS')
            self.assertIn(r['status'], {'verified','warning'}); self.assertEqual(r['files_checked'],1); self.assertEqual(r['files_failed'],0); self.assertEqual(r['checksum_matches'],1)
    def test_results_json_shape(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ.update({'APP_DATA_DIR':d,'APP_DB':str(Path(d)/'x.db'),'RESULTS_FILE':str(Path(d)/'results.json'),'BACKUP_ROOT':d,'TELEGRAM_BOT_TOKEN':'','TELEGRAM_HOME_CHANNEL':''})
            from backup_verify.config import Settings
            from backup_verify.db import Database
            from backup_verify.runner import VerificationRunner
            s=Settings.from_env(); runner=VerificationRunner(s,Database(s.db_path)); payload={'clients':{'MichaelD-ASUS':{'status':'verified','last_checked':'2026-06-11T02:00:00','files_checked':87,'files_failed':0}},'disk_health':{'status':'healthy','drives_checked':4,'drives_failed':0}}
            runner.write_results(payload); data=json.loads(Path(s.results_file).read_text())
            self.assertEqual(data['MichaelD-ASUS']['status'],'verified'); self.assertEqual(data['disk_health']['drives_checked'],4)
if __name__=='__main__': unittest.main()
