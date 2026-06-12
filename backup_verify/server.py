import csv, io, json, threading, time, urllib.parse
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from datetime import datetime
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
STATIC = Path(__file__).resolve().parent.parent / 'static'


def current_settings():
    return base_settings.effective(db.load_settings())


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
            return self.j({'running': runner.running, 'next_run': nxt.isoformat() if nxt else None, 'latest': latest, 'settings': s.public_dict(), 'clients': db.client_summary(s.clients), 'disk_trends': db.disk_trends()})
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
