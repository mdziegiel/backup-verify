import json, threading, multiprocessing, os, traceback
from datetime import datetime, timezone
from .checks import verify_client, check_smart, check_qnap
from .b2 import check_b2
from .notify import send_all, format_summary


def _verify_client_worker(queue, settings, client, db):
    try:
        queue.put({'ok': True, 'result': verify_client(settings, client, db)})
    except Exception as e:
        queue.put({'ok': False, 'error': f'{type(e).__name__}: {e}', 'traceback': traceback.format_exc()[-2000:]})


def verify_client_safe(settings, client, db):
    name = client.get('name') if isinstance(client, dict) else str(client)
    try:
        timeout = int(os.environ.get('VERIFY_CLIENT_TIMEOUT_SECONDS', '120'))
    except Exception:
        timeout = 120
    queue = multiprocessing.Queue(maxsize=1)
    proc = multiprocessing.Process(target=_verify_client_worker, args=(queue, settings, client, db), daemon=True)
    proc.start()
    proc.join(timeout)
    if proc.is_alive():
        proc.terminate()
        return {
            'client': name,
            'status': 'failed',
            'last_checked': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            'files_checked': 0,
            'files_failed': 1,
            'warnings': [],
            'errors': [f'Client verification timed out after {timeout}s. Backup storage may be slow, stale, or stuck in NFS I/O.'],
            'file_failures': [{'file': str(client.get('backup_root') if isinstance(client, dict) else ''), 'reason': 'verification timeout'}],
        }
    if not queue.empty():
        item = queue.get()
        if item.get('ok'):
            return item.get('result')
        return {
            'client': name,
            'status': 'failed',
            'last_checked': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            'files_checked': 0,
            'files_failed': 1,
            'warnings': [],
            'errors': [item.get('error') or 'verification worker failed'],
            'worker_traceback': item.get('traceback'),
        }
    return {
        'client': name,
        'status': 'failed',
        'last_checked': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        'files_checked': 0,
        'files_failed': 1,
        'warnings': [],
        'errors': ['verification worker exited without result'],
    }


class VerificationRunner:
    def __init__(self, settings_provider, db):
        self.settings_provider = settings_provider if callable(settings_provider) else (lambda: settings_provider)
        self.db = db
        self.lock = threading.Lock()
        self.running = False
        self.last_error = None

    def current_settings(self):
        return self.settings_provider()

    def client_configs(self, requested=None):
        saved = self.db.list_clients(active_only=requested is None) if hasattr(self.db, 'list_clients') else []
        if requested is not None:
            wanted = {str(x) for x in requested}
            by_name = {c['name']: c for c in saved}
            by_api = {c.get('urbackup_client_name') or c['name']: c for c in saved}
            out = []
            settings = self.current_settings()
            for name in requested:
                out.append(by_name.get(str(name)) or by_api.get(str(name)) or {
                    'name': str(name),
                    'backup_root': str(settings.backup_root),
                    'urbackup_client_name': str(name),
                    'sample_size': settings.sample_size,
                    'backup_age_threshold_days': settings.backup_age_threshold_days,
                    'enabled': True,
                })
            return out
        if saved:
            return saved
        settings = self.current_settings()
        return [{
            'name': name,
            'backup_root': str(settings.backup_root),
            'urbackup_client_name': name,
            'sample_size': settings.sample_size,
            'backup_age_threshold_days': settings.backup_age_threshold_days,
            'enabled': True,
        } for name in settings.clients]

    def run(self, clients=None, notify=True):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('verification already running')
        self.running = True
        try:
            settings = self.current_settings()
            started = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            rid = self.db.create_run(started)
            p = {'run_id': rid, 'started_at': started, 'clients': {}, 'disk_health': {}, 'qnap_health': {}, 'b2': {}}
            for client in self.client_configs(clients):
                r = verify_client_safe(settings, client, self.db)
                client_name = r.get('client') or client.get('name')
                p['clients'][client_name] = {k: v for k, v in r.items() if k != 'client'}
                self.db.add_client_result(rid, r)
            dh = check_smart(settings, self.db)
            p['disk_health'] = dh
            for d in dh.get('drives', []):
                self.db.add_disk_result(rid, d)
            p['qnap_health'] = check_qnap(settings)
            p['b2'] = check_b2(settings)
            statuses = [r.get('status') for r in p['clients'].values()] + [dh.get('status'), p['qnap_health'].get('status')]
            if p['b2'].get('status') not in ('disabled', None):
                statuses.append(p['b2'].get('status'))
            p['overall_status'] = 'failed' if 'failed' in statuses else ('warning' if 'warning' in statuses else 'verified')
            p['finished_at'] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            self.write_results(p, settings)
            summary = format_summary(p)
            self.db.finish_run(rid, p['finished_at'], p['overall_status'], summary, p)
            if notify:
                p['notifications'] = send_all(settings, p, summary, self.db)
            return p
        except Exception as e:
            self.last_error = f'{type(e).__name__}: {e}'
            raise
        finally:
            self.running = False
            self.lock.release()

    def write_results(self, p, settings=None):
        settings = settings or self.current_settings()
        out = {c: {'status': r.get('status'), 'last_checked': r.get('last_checked') or p.get('finished_at'), 'files_checked': r.get('files_checked', 0), 'files_failed': r.get('files_failed', 0)} for c, r in p.get('clients', {}).items()}
        dh = p.get('disk_health', {})
        out['disk_health'] = {'status': dh.get('status'), 'drives_checked': dh.get('drives_checked', 0), 'drives_failed': dh.get('drives_failed', 0)}
        b2 = p.get('b2', {})
        if b2:
            out['b2'] = {'status': b2.get('status'), 'last_run_time': b2.get('last_run_time'), 'offsite_coverage_score': b2.get('offsite_coverage_score')}
        settings.results_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = settings.results_file.with_suffix('.tmp')
        tmp.write_text(json.dumps(out, indent=2, sort_keys=True))
        tmp.replace(settings.results_file)
