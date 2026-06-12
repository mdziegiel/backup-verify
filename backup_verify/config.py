import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

def split_csv(value, default):
    return [x.strip() for x in (value or '').split(',') if x.strip()] or default

def load_env_file(path):
    if not path: return {}
    p=Path(os.path.expanduser(path))
    if not p.exists(): return {}
    out={}
    for raw in p.read_text(errors='ignore').splitlines():
        line=raw.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        k,v=line.split('=',1); out[k.strip()]=v.strip().strip('"').strip("'")
    return out

@dataclass(frozen=True)
class Settings:
    host:str; port:int; data_dir:Path; db_path:Path; results_file:Path; schedule:str; timezone:str; sample_size:int; clients:list; critical_paths:list; backup_root:Path; urbackup_url:str; urbackup_username:str; urbackup_password:str; proxmox_url:str; proxmox_token_id:str; proxmox_token_secret:str; smart_devices:list; smartctl_path:str; qnap_url:str; qnap_username:str; qnap_password:str; telegram_bot_token:str; telegram_chat_id:str; telegram_env_path:str; alert_on_success:bool
    @classmethod
    def from_env(cls):
        env_path=os.environ.get('TELEGRAM_ENV_PATH','/host-hermes/.env'); mounted=load_env_file(env_path)
        def get(k,d=''): return os.environ.get(k) or mounted.get(k) or d
        data_dir=Path(get('APP_DATA_DIR','/data'))
        return cls(get('APP_HOST','0.0.0.0'), int(get('APP_PORT','10122')), data_dir, Path(get('APP_DB',str(data_dir/'backup_verify.sqlite3'))), Path(get('RESULTS_FILE','/opt/backup-verify/results.json')), get('VERIFY_SCHEDULE','0 2 * * 0'), get('VERIFY_TIMEZONE','America/New_York'), max(1,min(500,int(get('SAMPLE_SIZE','87')))), split_csv(get('CLIENTS','MichaelD-ASUS,MichaelD-Lenovo'), ['MichaelD-ASUS','MichaelD-Lenovo']), split_csv(get('CRITICAL_PATHS','Windows,Users,Program Files'), ['Windows','Users','Program Files']), Path(get('BACKUP_ROOT','/mnt/qnap-backups/urbackup')), get('URBACKUP_URL','http://10.10.10.76:55414').rstrip('/'), get('URBACKUP_USERNAME'), get('URBACKUP_PASSWORD'), get('PROXMOX_URL','https://10.10.10.251:8006').rstrip('/'), get('PROXMOX_TOKEN_ID'), get('PROXMOX_TOKEN_SECRET'), split_csv(get('SMART_DEVICES','auto'), ['auto']), get('SMARTCTL_PATH','smartctl'), get('QNAP_URL','https://10.10.10.230').rstrip('/'), get('QNAP_USERNAME'), get('QNAP_PASSWORD'), get('TELEGRAM_BOT_TOKEN'), get('TELEGRAM_HOME_CHANNEL') or get('TELEGRAM_CHAT_ID'), env_path, get('ALERT_ON_SUCCESS','true').lower() in {'1','true','yes','on'})
    def tz(self): return ZoneInfo(self.timezone)
