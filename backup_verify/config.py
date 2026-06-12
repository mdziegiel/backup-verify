import os
from dataclasses import dataclass, replace
from pathlib import Path
from zoneinfo import ZoneInfo


def split_csv(value, default):
    return [x.strip() for x in (value or '').split(',') if x.strip()] or default


def as_bool(value, default=False):
    if value is None or value == '':
        return default
    return str(value).strip().lower() in {'1','true','yes','on','enabled'}


def as_int(value, default, lo=None, hi=None):
    try:
        n = int(value)
    except Exception:
        n = default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


def load_env_file(path):
    if not path:
        return {}
    p = Path(os.path.expanduser(path))
    if not p.exists():
        return {}
    out = {}
    for raw in p.read_text(errors='ignore').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    data_dir: Path
    db_path: Path
    results_file: Path
    schedule: str
    schedule_enabled: bool
    frequency: str
    schedule_time: str
    schedule_day_of_week: int
    schedule_day_of_month: int
    timezone: str
    sample_size: int
    clients: list
    critical_paths: list
    backup_root: Path
    backup_age_threshold_days: int
    size_drop_threshold_percent: int
    retention_min_copies: int
    urbackup_url: str
    urbackup_username: str
    urbackup_password: str
    proxmox_url: str
    proxmox_token_id: str
    proxmox_token_secret: str
    smart_devices: list
    smartctl_path: str
    qnap_url: str
    qnap_username: str
    qnap_password: str
    telegram_enabled: bool
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_env_path: str
    smtp_enabled: bool
    smtp_server: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_to: str
    smtp_from: str
    gotify_enabled: bool
    gotify_url: str
    gotify_token: str
    notify_on_completion: bool
    notify_on_failure_only: bool
    notify_on_warning: bool
    notify_on_disk_health_change: bool
    quiet_hours_start: str
    quiet_hours_end: str
    b2_enabled: bool
    b2_key_id: str
    b2_application_key: str
    b2_bucket_name: str
    b2_behind_days: int

    @classmethod
    def from_env(cls):
        env_path = os.environ.get('TELEGRAM_ENV_PATH', '/host-hermes/.env')
        mounted = load_env_file(env_path)
        def get(k, d=''):
            return os.environ.get(k) or mounted.get(k) or d
        data_dir = Path(get('APP_DATA_DIR', '/data'))
        schedule = get('VERIFY_SCHEDULE', '0 2 * * 0')
        return cls(
            get('APP_HOST', '0.0.0.0'),
            int(get('APP_PORT', '10122')),
            data_dir,
            Path(get('APP_DB', str(data_dir / 'backup_verify.sqlite3'))),
            Path(get('RESULTS_FILE', '/opt/backup-verify/results.json')),
            schedule,
            as_bool(get('SCHEDULE_ENABLED', 'true'), True),
            get('SCHEDULE_FREQUENCY', 'weekly'),
            get('SCHEDULE_TIME', '02:00'),
            as_int(get('SCHEDULE_DAY_OF_WEEK', '0'), 0, 0, 6),
            as_int(get('SCHEDULE_DAY_OF_MONTH', '1'), 1, 1, 31),
            get('VERIFY_TIMEZONE', 'America/New_York'),
            as_int(get('SAMPLE_SIZE', '87'), 87, 1, 500),
            split_csv(get('CLIENTS', 'MichaelD-ASUS,MichaelD-Lenovo'), ['MichaelD-ASUS', 'MichaelD-Lenovo']),
            split_csv(get('CRITICAL_PATHS', 'Windows,Users,Program Files,ProgramData'), ['Windows', 'Users', 'Program Files', 'ProgramData']),
            Path(get('BACKUP_ROOT', '/mnt/qnap-backups/urbackup')),
            as_int(get('BACKUP_AGE_THRESHOLD_DAYS', '2'), 2, 1, 365),
            as_int(get('SIZE_DROP_THRESHOLD_PERCENT', '30'), 30, 1, 99),
            as_int(get('RETENTION_MIN_COPIES', '2'), 2, 1, 999),
            get('URBACKUP_URL', 'http://10.10.10.76:55414').rstrip('/'),
            get('URBACKUP_USERNAME'),
            get('URBACKUP_PASSWORD'),
            get('PROXMOX_URL', 'https://10.10.10.251:8006').rstrip('/'),
            get('PROXMOX_TOKEN_ID'),
            get('PROXMOX_TOKEN_SECRET'),
            split_csv(get('SMART_DEVICES', 'auto'), ['auto']),
            get('SMARTCTL_PATH', 'smartctl'),
            get('QNAP_URL', 'https://10.10.10.230').rstrip('/'),
            get('QNAP_USERNAME'),
            get('QNAP_PASSWORD'),
            as_bool(get('TELEGRAM_ENABLED', 'true'), True),
            get('TELEGRAM_BOT_TOKEN'),
            get('TELEGRAM_HOME_CHANNEL') or get('TELEGRAM_CHAT_ID'),
            env_path,
            as_bool(get('SMTP_ENABLED', 'false'), False),
            get('SMTP_SERVER'),
            as_int(get('SMTP_PORT', '587'), 587, 1, 65535),
            get('SMTP_USERNAME'),
            get('SMTP_PASSWORD'),
            get('SMTP_TO'),
            get('SMTP_FROM') or get('SMTP_USERNAME') or 'backup-verify@mrdtech.local',
            as_bool(get('GOTIFY_ENABLED', 'false'), False),
            get('GOTIFY_URL'),
            get('GOTIFY_TOKEN'),
            as_bool(get('NOTIFY_ON_COMPLETION', get('ALERT_ON_SUCCESS', 'true')), True),
            as_bool(get('NOTIFY_ON_FAILURE_ONLY', 'false'), False),
            as_bool(get('NOTIFY_ON_WARNING', 'true'), True),
            as_bool(get('NOTIFY_ON_DISK_HEALTH_CHANGE', 'true'), True),
            get('QUIET_HOURS_START', ''),
            get('QUIET_HOURS_END', ''),
            as_bool(get('B2_ENABLED', 'false'), False),
            get('B2_KEY_ID'),
            get('B2_APPLICATION_KEY'),
            get('B2_BUCKET_NAME'),
            as_int(get('B2_BEHIND_DAYS', '2'), 2, 1, 365),
        )

    def tz(self):
        return ZoneInfo(self.timezone)

    def public_dict(self):
        safe = self.__dict__.copy()
        for key in ['telegram_bot_token','smtp_password','gotify_token','b2_key_id','b2_application_key','urbackup_password','qnap_password','proxmox_token_secret']:
            safe[key] = 'configured' if safe.get(key) else ''
        safe['backup_root'] = str(self.backup_root)
        safe['results_file'] = str(self.results_file)
        safe['data_dir'] = str(self.data_dir)
        safe['db_path'] = str(self.db_path)
        return safe

    def effective(self, overrides):
        if not overrides:
            return self
        vals = {}
        bools = {'schedule_enabled','telegram_enabled','smtp_enabled','gotify_enabled','notify_on_completion','notify_on_failure_only','notify_on_warning','notify_on_disk_health_change','b2_enabled'}
        ints = {'sample_size','backup_age_threshold_days','size_drop_threshold_percent','retention_min_copies','smtp_port','b2_behind_days','schedule_day_of_week','schedule_day_of_month'}
        lists = {'clients','critical_paths','smart_devices'}
        paths = {'backup_root','results_file','db_path','data_dir'}
        for k, v in overrides.items():
            if not hasattr(self, k):
                continue
            if k in bools:
                vals[k] = as_bool(v, getattr(self, k))
            elif k in ints:
                vals[k] = as_int(v, getattr(self, k))
            elif k in lists:
                vals[k] = split_csv(v if isinstance(v, str) else ','.join(v), getattr(self, k))
            elif k in paths:
                vals[k] = Path(v)
            else:
                vals[k] = v
        return replace(self, **vals)


def cron_from_visual(frequency, time_value, dow=0, dom=1):
    try:
        hour, minute = [int(x) for x in str(time_value or '02:00').split(':', 1)]
    except Exception:
        hour, minute = 2, 0
    hour = max(0, min(23, hour)); minute = max(0, min(59, minute))
    frequency = (frequency or 'weekly').lower()
    if frequency == 'daily':
        return f'{minute} {hour} * * *'
    if frequency == 'monthly':
        return f'{minute} {hour} {max(1, min(31, int(dom or 1)))} * *'
    return f'{minute} {hour} * * {max(0, min(6, int(dow or 0)))}'
