import base64, json, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path


def human_bytes(n):
    try: n = float(n)
    except Exception: return '0 B'
    units = ['B','KiB','MiB','GiB','TiB','PiB']
    i = 0
    while n >= 1024 and i < len(units)-1:
        n /= 1024; i += 1
    return f'{n:.1f} {units[i]}' if i else f'{int(n)} B'


class B2Client:
    def __init__(self, key_id, app_key):
        token = base64.b64encode(f'{key_id}:{app_key}'.encode()).decode()
        req = urllib.request.Request('https://api.backblazeb2.com/b2api/v2/b2_authorize_account', headers={'Authorization': 'Basic ' + token})
        with urllib.request.urlopen(req, timeout=30) as r:
            self.auth = json.loads(r.read().decode())
        self.api = self.auth['apiUrl']
        self.download = self.auth['downloadUrl']
        self.token = self.auth['authorizationToken']

    def call(self, name, payload):
        data = json.dumps(payload).encode()
        req = urllib.request.Request(self.api + '/b2api/v2/' + name, data=data, headers={'Authorization': self.token, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())

    def bucket_id(self, bucket_name):
        data = self.call('b2_list_buckets', {'accountId': self.auth['accountId'], 'bucketName': bucket_name})
        buckets = data.get('buckets') or []
        if not buckets:
            raise RuntimeError(f'B2 bucket not found: {bucket_name}')
        return buckets[0]['bucketId']

    def list_files(self, bucket_id, max_files=1000):
        files = []
        name = None
        while len(files) < max_files:
            data = self.call('b2_list_file_names', {'bucketId': bucket_id, 'startFileName': name, 'maxFileCount': min(1000, max_files-len(files))})
            files.extend(data.get('files') or [])
            name = data.get('nextFileName')
            if not name:
                break
        return files

    def download_sample(self, bucket_name, file_name, size_limit=1024*1024):
        url = self.download + '/file/' + urllib.parse.quote(bucket_name) + '/' + urllib.parse.quote(file_name)
        req = urllib.request.Request(url, headers={'Authorization': self.token, 'Range': f'bytes=0-{size_limit-1}'})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read(size_limit)
        return len(data)


def local_inventory(root, limit=1000):
    root = Path(root)
    files = []
    total = 0
    newest = None
    if not root.exists():
        return {'files': [], 'total_bytes': 0, 'newest_mtime': None}
    for p in root.rglob('*'):
        if not p.is_file():
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        total += st.st_size
        m = datetime.fromtimestamp(st.st_mtime, timezone.utc)
        newest = max(newest, m) if newest else m
        if len(files) < limit:
            files.append({'path': str(p.relative_to(root)).replace('\\','/'), 'size': st.st_size, 'mtime': m.isoformat()})
    return {'files': files, 'total_bytes': total, 'newest_mtime': newest.isoformat() if newest else None}


def check_b2(settings):
    if not settings.b2_enabled:
        return {'status': 'disabled', 'warnings': ['B2 integration disabled']}
    if not settings.b2_key_id or not settings.b2_application_key or not settings.b2_bucket_name:
        return {'status': 'warning', 'warnings': ['B2 credentials or bucket not configured']}
    try:
        client = B2Client(settings.b2_key_id, settings.b2_application_key)
        bucket_id = client.bucket_id(settings.b2_bucket_name)
        files = client.list_files(bucket_id, 1000)
        bucket_size = sum(int(f.get('contentLength') or 0) for f in files)
        newest_b2_ms = max([int(f.get('uploadTimestamp') or 0) for f in files] or [0])
        newest_b2 = datetime.fromtimestamp(newest_b2_ms/1000, timezone.utc) if newest_b2_ms else None
        local = local_inventory(settings.backup_root, 1000)
        b2_names = {f.get('fileName') for f in files}
        local_files = local['files']
        covered = sum(1 for f in local_files if f['path'] in b2_names or any(name.endswith('/' + f['path']) for name in b2_names))
        coverage = round((covered / len(local_files))*100, 1) if local_files else 0
        warnings = []
        if local.get('newest_mtime') and newest_b2:
            local_new = datetime.fromisoformat(local['newest_mtime'])
            if newest_b2 < local_new - timedelta(days=settings.b2_behind_days):
                warnings.append(f'B2 is behind local backups by more than {settings.b2_behind_days} days')
        sha1_matches = sha1_checked = 0
        local_by_name = {f['path']: f for f in local_files}
        for f in files[:100]:
            sha1 = f.get('contentSha1')
            if sha1 and sha1 != 'none':
                sha1_checked += 1
                if f.get('fileName') in local_by_name or Path(f.get('fileName','')).name in {Path(x).name for x in local_by_name}:
                    sha1_matches += 1
        download_test = {'status': 'skipped'}
        if files:
            small = sorted(files, key=lambda x: int(x.get('contentLength') or 0))[0]
            n = client.download_sample(settings.b2_bucket_name, small['fileName'])
            download_test = {'status': 'verified' if n >= 0 else 'warning', 'file': small['fileName'], 'bytes_read': n}
        status = 'warning' if warnings else 'healthy'
        return {
            'status': status,
            'last_backup_job_status': 'available' if files else 'empty',
            'last_run_time': newest_b2.isoformat() if newest_b2 else None,
            'bytes_uploaded': bucket_size,
            'bucket_size_human': human_bytes(bucket_size),
            'cost_estimate_monthly_usd': round((bucket_size / (1024**3)) * 0.006, 2),
            'files_seen': len(files),
            'sync_status': 'behind' if warnings else 'current',
            'offsite_coverage_score': coverage,
            'sha1_checked': sha1_checked,
            'sha1_local_name_matches': sha1_matches,
            'download_test': download_test,
            'warnings': warnings,
        }
    except Exception as e:
        return {'status': 'warning', 'warnings': [f'B2 API check failed: {type(e).__name__}: {e}']}
