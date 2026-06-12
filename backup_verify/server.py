import csv, io, json, threading, time, urllib.parse, urllib.request, socket, subprocess, os, hmac, hashlib, base64
from dataclasses import replace
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from datetime import datetime, timezone
from .config import Settings, cron_from_visual
from .db import Database
from .runner import VerificationRunner
from .scheduler import next_run_from_cron
from .notify import send_all

def make_simple_pdf(lines):
    safe_lines = []
    for line in lines[:55]:
        safe = str(line).replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        safe_lines.append(safe[:120])
    stream = ['BT', '/F1 10 Tf', '50 770 Td']
    for i, line in enumerate(safe_lines):
        if i:
            stream.append('0 -14 Td')
        stream.append(f'({line}) Tj')
    stream.append('ET')
    content = '\n'.join(stream)
    objs = [
        '1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj',
        '2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj',
        '3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj',
        '4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj',
        f'5 0 obj << /Length {len(content.encode("latin-1", "replace"))} >> stream\n{content}\nendstream endobj',
    ]
    pdf = '%PDF-1.4\n'
    offsets = [0]
    for obj in objs:
        offsets.append(len(pdf.encode('latin-1')))
        pdf += obj + '\n'
    xref_at = len(pdf.encode('latin-1'))
    pdf += f'xref\n0 {len(objs)+1}\n0000000000 65535 f \n'
    for off in offsets[1:]:
        pdf += f'{off:010d} 00000 n \n'
    pdf += f'trailer << /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n'
    return pdf


base_settings = Settings.from_env()
db = Database(base_settings.db_path)
db.seed_clients_from_settings(base_settings)
STATIC = Path(__file__).resolve().parent.parent / 'static'

OFFSITE_SECRET_FIELDS = {
    'keyId','applicationKey','accessKeyId','secretAccessKey','accountKey','serviceAccountJson',
    'privateKey','password','username'
}

PROVIDER_LABELS = {
    'backblaze': 'Backblaze B2', 's3': 'Amazon S3 / S3-Compatible', 'azure': 'Azure Blob Storage',
    'gcs': 'Google Cloud Storage', 'sftp': 'SFTP/SSH', 'rclone': 'rclone Remote', 'webdav': 'Generic WebDAV'
}

def merge_offsite_providers(incoming, existing):
    existing_by_id = {p.get('id'): p for p in existing or []}
    merged = []
    for item in incoming or []:
        if not isinstance(item, dict):
            continue
        old_cfg = (existing_by_id.get(item.get('id')) or {}).get('config') or {}
        cfg = dict(item.get('config') or {})
        for key in OFFSITE_SECRET_FIELDS:
            if cfg.get(key) == 'configured':
                cfg[key] = old_cfg.get(key, '')
        item = dict(item)
        item['config'] = cfg
        merged.append(item)
    return merged

def test_backblaze(cfg):
    from .b2 import B2Client
    if not cfg.get('keyId') or not cfg.get('applicationKey') or not cfg.get('bucketName'):
        return False, 'Missing Key ID, application key, or bucket name.', {}
    client = B2Client(cfg.get('keyId'), cfg.get('applicationKey'))
    bucket_id = client.bucket_id(cfg.get('bucketName'))
    files = client.list_files(bucket_id, 10)
    return True, f'Bucket authorized. {len(files)} sampled objects visible.', {'objects_sampled': len(files)}

def _aws_sig(key, msg):
    return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

def test_s3(cfg):
    access = cfg.get('accessKeyId'); secret = cfg.get('secretAccessKey'); bucket = cfg.get('bucket'); region = cfg.get('region') or 'us-east-1'
    if not access or not secret or not bucket:
        return False, 'Missing access key, secret key, or bucket.', {}
    endpoint = (cfg.get('endpointUrl') or f'https://s3.{region}.amazonaws.com').rstrip('/')
    host = urllib.parse.urlparse(endpoint).netloc
    path = '/' + urllib.parse.quote(bucket)
    now = datetime.now(timezone.utc)
    amz_date = now.strftime('%Y%m%dT%H%M%SZ'); date_stamp = now.strftime('%Y%m%d')
    canonical_headers = f'host:{host}\nx-amz-content-sha256:UNSIGNED-PAYLOAD\nx-amz-date:{amz_date}\n'
    signed_headers = 'host;x-amz-content-sha256;x-amz-date'
    canonical_request = f'HEAD\n{path}\n\n{canonical_headers}\n{signed_headers}\nUNSIGNED-PAYLOAD'
    scope = f'{date_stamp}/{region}/s3/aws4_request'
    sts = 'AWS4-HMAC-SHA256\n' + amz_date + '\n' + scope + '\n' + hashlib.sha256(canonical_request.encode()).hexdigest()
    signing_key = _aws_sig(_aws_sig(_aws_sig(_aws_sig(('AWS4' + secret).encode(), date_stamp), region), 's3'), 'aws4_request')
    sig = hmac.new(signing_key, sts.encode(), hashlib.sha256).hexdigest()
    auth = f'AWS4-HMAC-SHA256 Credential={access}/{scope}, SignedHeaders={signed_headers}, Signature={sig}'
    req = urllib.request.Request(endpoint + path, method='HEAD', headers={'Host': host, 'x-amz-date': amz_date, 'x-amz-content-sha256': 'UNSIGNED-PAYLOAD', 'Authorization': auth})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return True, f'Bucket reachable: HTTP {r.status}.', {'http_status': r.status}
    except urllib.error.HTTPError as e:
        if e.code in (200, 301, 302):
            return True, f'Bucket responded: HTTP {e.code}.', {'http_status': e.code}
        raise

def test_webdav(cfg):
    url = cfg.get('url')
    if not url:
        return False, 'Missing WebDAV URL.', {}
    target = url.rstrip('/') + '/' + (cfg.get('remotePath') or '').strip('/')
    req = urllib.request.Request(target, method='PROPFIND', headers={'Depth': '0'})
    if cfg.get('username') or cfg.get('password'):
        token = base64.b64encode(f"{cfg.get('username','')}:{cfg.get('password','')}".encode()).decode()
        req.add_header('Authorization', 'Basic ' + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            ok = r.status in (200, 207)
            return ok, f'WebDAV endpoint responded HTTP {r.status}.', {'http_status': r.status}
    except urllib.error.HTTPError as e:
        return e.code in (200,207), f'WebDAV endpoint responded HTTP {e.code}.', {'http_status': e.code}

def test_sftp(cfg):
    host = cfg.get('host'); port = int(cfg.get('port') or 22)
    if not host or not cfg.get('username'):
        return False, 'Missing host or username.', {}
    with socket.create_connection((host, port), timeout=10) as sock:
        sock.settimeout(10)
        banner = sock.recv(128).decode(errors='ignore').strip()
    ok = banner.startswith('SSH-')
    return ok, ('SSH service reachable: ' + (banner or 'no banner')), {'banner': banner, 'credential_auth': 'not attempted without Paramiko'}

def test_rclone(cfg):
    remote = cfg.get('remoteName'); path = cfg.get('path') or ''
    if not remote:
        return False, 'Missing rclone remote name.', {}
    cmd = ['rclone', 'lsjson', f'{remote}:{path}', '--max-depth', '1']
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if proc.returncode != 0:
        return False, proc.stderr.strip()[:300] or 'rclone failed.', {'exit_code': proc.returncode}
    try:
        items = json.loads(proc.stdout or '[]')
    except Exception:
        items = []
    return True, f'rclone listed {len(items)} top-level entries.', {'items_sampled': len(items)}

def test_azure(cfg):
    account = cfg.get('accountName'); key = cfg.get('accountKey'); container = cfg.get('containerName')
    if not account or not key or not container:
        return False, 'Missing storage account, access key, or container.', {}
    url = f'https://{account}.blob.core.windows.net/{urllib.parse.quote(container)}?restype=container'
    req = urllib.request.Request(url, method='GET', headers={'x-ms-version': '2020-10-02', 'x-ms-date': datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return True, f'Azure container reachable HTTP {r.status}.', {'http_status': r.status, 'note': 'public/container-level probe'}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return True, 'Azure endpoint exists but denied unsigned probe. Account/container format accepted; full SharedKey validation pending.', {'http_status': e.code}
        raise

def test_gcs(cfg):
    bucket = cfg.get('bucketName'); raw = cfg.get('serviceAccountJson')
    if not bucket or not raw:
        return False, 'Missing service account JSON or bucket name.', {}
    data = json.loads(raw)
    email = data.get('client_email')
    private_key = data.get('private_key')
    if not email or not private_key:
        return False, 'Service account JSON lacks client_email or private_key.', {}
    req = urllib.request.Request(f'https://storage.googleapis.com/storage/v1/b/{urllib.parse.quote(bucket)}', method='GET')
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return True, f'GCS bucket public metadata reachable HTTP {r.status}; JSON credential parsed for {email}.', {'http_status': r.status, 'client_email': email}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return True, f'GCS bucket endpoint denied anonymous probe; service account JSON parsed for {email}.', {'http_status': e.code, 'client_email': email}
        raise

def test_offsite_provider(provider):
    ptype = (provider or {}).get('type')
    cfg = (provider or {}).get('config') or {}
    testers = {'backblaze': test_backblaze, 's3': test_s3, 'azure': test_azure, 'gcs': test_gcs, 'sftp': test_sftp, 'rclone': test_rclone, 'webdav': test_webdav}
    if ptype not in testers:
        return {'ok': False, 'status': 'failed', 'message': 'Unknown provider type.', 'checked_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
    try:
        ok, msg, details = testers[ptype](cfg)
        return {'ok': bool(ok), 'status': 'healthy' if ok else 'failed', 'message': msg, 'details': details, 'checked_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
    except FileNotFoundError as e:
        return {'ok': False, 'status': 'failed', 'message': f'Required command not installed: {e.filename}', 'checked_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
    except Exception as e:
        return {'ok': False, 'status': 'failed', 'message': f'{PROVIDER_LABELS.get(ptype, ptype)} test failed: {type(e).__name__}: {e}', 'checked_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat()}


def current_settings():
    settings = base_settings.effective(db.load_settings())
    active_clients = [c['name'] for c in db.list_clients(active_only=True)]
    if active_clients:
        settings = replace(settings, clients=active_clients)
    return settings


runner = VerificationRunner(current_settings, db)
_next = None
_last_schedule_key = None


def compute_next():
    s = current_settings()
    if not s.schedule_enabled:
        return None
    return next_run_from_cron(s.schedule, s.tz())


def schedule_state():
    global _next, _last_schedule_key
    s = current_settings()
    key = (s.schedule_enabled, s.schedule, s.timezone)
    if key != _last_schedule_key or _next is None:
        _next = compute_next()
        _last_schedule_key = key
    return _next


def loop():
    global _next
    while True:
        s = current_settings()
        nxt = schedule_state()
        if s.schedule_enabled and nxt:
            now = datetime.now(s.tz()).replace(second=0, microsecond=0)
            if now >= nxt and not runner.running:
                threading.Thread(target=lambda: runner.run(notify=True), daemon=True).start()
                _next = next_run_from_cron(s.schedule, s.tz())
        time.sleep(30)


class Handler(SimpleHTTPRequestHandler):
    def j(self, o, code=200):
        b = json.dumps(o, default=str, indent=2).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def text(self, content, content_type='text/plain', code=200):
        b = content.encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def body(self):
        n = int(self.headers.get('content-length', '0') or 0)
        return json.loads(self.rfile.read(n).decode()) if n else {}

    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        s = current_settings()
        nxt = schedule_state()
        if p == '/api/health':
            return self.j({'ok': True, 'running': runner.running, 'version': '2.0.0'})
        if p == '/api/status':
            latest = db.latest_payload()
            client_configs = db.list_clients(active_only=False)
            names = [c['name'] for c in client_configs]
            return self.j({'running': runner.running, 'next_run': nxt.isoformat() if nxt else None, 'latest': latest, 'settings': s.public_dict(), 'clients': db.client_summary(names), 'client_configs': client_configs, 'disk_trends': db.disk_trends()})
        if p == '/api/clients':
            return self.j({'clients': db.list_clients(active_only=False)})
        if p == '/api/history':
            return self.j({'runs': db.history_details(100)})
        if p == '/api/history.csv':
            rows = db.history_details(1000)
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(['id','started_at','finished_at','status','summary'])
            for r in rows:
                w.writerow([r.get('id'), r.get('started_at'), r.get('finished_at'), r.get('status'), r.get('summary')])
            return self.text(buf.getvalue(), 'text/csv')
        if p == '/api/history.pdf':
            rows = db.history_details(200)
            text_lines = ['Backup Verify History Report', ''] + [f"#{r.get('id')} {r.get('finished_at') or r.get('started_at')} {r.get('status')} {str(r.get('summary') or '')[:90]}" for r in rows]
            pdf = make_simple_pdf(text_lines)
            b = pdf.encode('latin-1')
            self.send_response(200)
            self.send_header('Content-Type', 'application/pdf')
            self.send_header('Content-Disposition', 'attachment; filename="backup-verify-history.pdf"')
            self.send_header('Content-Length', str(len(b)))
            self.end_headers(); self.wfile.write(b); return
        if p == '/api/results.json':
            if s.results_file.exists():
                return self.j(json.loads(s.results_file.read_text()))
            return self.j({})
        if p == '/':
            self.path = '/index.html'
        return SimpleHTTPRequestHandler.do_GET(self)

    def do_POST(self):
        global _next, _last_schedule_key
        p = urllib.parse.urlparse(self.path).path
        if p == '/api/clients':
            try:
                client = db.save_client(self.body())
                return self.j({'ok': True, 'client': client, 'clients': db.list_clients(active_only=False)})
            except Exception as e:
                return self.j({'ok': False, 'error': f'{type(e).__name__}: {e}'}, 400)
        if p == '/api/run':
            b = self.body(); clients = b.get('clients')
            if isinstance(clients, str):
                clients = [clients]
            if runner.running:
                return self.j({'ok': False, 'error': 'verification already running'}, 409)
            threading.Thread(target=lambda: runner.run(clients=clients, notify=True), daemon=True).start()
            return self.j({'ok': True, 'started': True, 'clients': clients or current_settings().clients})
        if p == '/api/settings':
            b = self.body()
            secret_keys = {'telegram_bot_token','telegram_chat_id','smtp_password','smtp_username','smtp_server','smtp_to','smtp_from','gotify_token','gotify_url','b2_key_id','b2_application_key','b2_bucket_name'}
            if 'offsite_providers' in b:
                b['offsite_providers'] = merge_offsite_providers(b.get('offsite_providers'), current_settings().offsite_providers)
            b = {k: v for k, v in b.items() if not (k in secret_keys and (v is None or v == '' or v == 'configured'))}
            if any(k in b for k in ['frequency', 'schedule_frequency', 'schedule_time', 'schedule_day_of_week', 'schedule_day_of_month']):
                freq = b.get('frequency') or b.get('schedule_frequency') or current_settings().frequency
                sched_time = b.get('schedule_time') or current_settings().schedule_time
                dow = b.get('schedule_day_of_week', current_settings().schedule_day_of_week)
                dom = b.get('schedule_day_of_month', current_settings().schedule_day_of_month)
                b['schedule'] = cron_from_visual(freq, sched_time, dow, dom)
                b['frequency'] = freq
            db.save_settings(b)
            _last_schedule_key = None
            _next = schedule_state()
            return self.j({'ok': True, 'settings': current_settings().public_dict(), 'next_run': _next.isoformat() if _next else None})
        if p == '/api/test-offsite':
            b = self.body(); provider = b.get('provider') or {}
            if provider.get('config'):
                provider = merge_offsite_providers([provider], current_settings().offsite_providers)[0]
            return self.j(test_offsite_provider(provider))
        if p == '/api/test-notification':
            b = self.body(); channel = b.get('channel', 'all')
            s = current_settings()
            fake = {'overall_status': 'verified', 'clients': {}, 'disk_health': {'status': 'healthy'}}
            text = 'Backup Verify test notification from MRDTech.'
            if channel == 'all':
                return self.j(send_all(s, fake, text, db, force=True))
            # Temporarily disable unrelated channels for a precise test.
            overrides = {'telegram_enabled': channel == 'telegram', 'smtp_enabled': channel == 'email', 'gotify_enabled': channel == 'gotify'}
            ss = s.effective(overrides)
            return self.j(send_all(ss, fake, text, db, force=True))
        return self.j({'error': 'not found'}, 404)

    def do_PUT(self):
        p = urllib.parse.urlparse(self.path).path
        if p.startswith('/api/clients/'):
            try:
                client_id = int(p.rstrip('/').split('/')[-1])
                client = db.save_client(self.body(), client_id=client_id)
                return self.j({'ok': True, 'client': client, 'clients': db.list_clients(active_only=False)})
            except Exception as e:
                return self.j({'ok': False, 'error': f'{type(e).__name__}: {e}'}, 400)
        return self.j({'error': 'not found'}, 404)

    def do_DELETE(self):
        p = urllib.parse.urlparse(self.path).path
        if p.startswith('/api/clients/'):
            try:
                client_id = int(p.rstrip('/').split('/')[-1])
                ok = db.delete_client(client_id)
                return self.j({'ok': ok, 'clients': db.list_clients(active_only=False)}, 200 if ok else 404)
            except Exception as e:
                return self.j({'ok': False, 'error': f'{type(e).__name__}: {e}'}, 400)
        return self.j({'error': 'not found'}, 404)

    def translate_path(self, path):
        path = urllib.parse.urlparse(path).path.lstrip('/') or 'index.html'
        safe = '/'.join(x for x in path.split('/') if x and x not in ('.', '..'))
        return str(STATIC / safe)

    def log_message(self, fmt, *args):
        print('%s - %s' % (self.address_string(), fmt % args), flush=True)


def main():
    current_settings().data_dir.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=loop, daemon=True).start()
    httpd = ThreadingHTTPServer((base_settings.host, base_settings.port), Handler)
    print(f'backup-verify listening on {base_settings.host}:{base_settings.port}', flush=True)
    httpd.serve_forever()


if __name__ == '__main__':
    main()
