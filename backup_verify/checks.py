import hashlib, json, os, re, shutil, subprocess, tempfile, urllib.parse, urllib.request, ssl
from datetime import datetime, timezone, timedelta
from pathlib import Path
from .urbackup import UrBackupClient, find_client_root, find_latest_backup_dir, random_files, find_images
from .b2 import human_bytes


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def hash_file(p, algorithm):
    h = hashlib.new(algorithm)
    with p.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def sha1_file(p):
    return hash_file(p, 'sha1')


def dir_size(root):
    total = 0
    for dp, _, fns in os.walk(root):
        for fn in fns:
            try:
                total += (Path(dp) / fn).stat().st_size
            except OSError:
                pass
    return total


def backup_dirs(root):
    if not root or not Path(root).exists():
        return []
    return sorted([p for p in Path(root).iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)


def load_checksum_manifest(root):
    m = {}
    names = {'hashes.txt', 'checksums.txt', 'sha256sums.txt', 'SHA256SUMS'}
    for dp, _, fns in os.walk(root):
        for fn in fns:
            if fn in names or fn.lower().endswith(('.sha256', '.md5')):
                p = Path(dp) / fn
                for line in p.read_text(errors='ignore').splitlines():
                    mm = re.match(r'^([a-fA-F0-9]{32,64})\s+[* ]?(.+)$', line.strip())
                    if mm:
                        m[str((p.parent / mm.group(2)).resolve())] = mm.group(1).lower()
    return m


def backup_time(path):
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def normalize_client_config(settings, client):
    if isinstance(client, dict):
        name = str(client.get('name') or client.get('client') or client.get('urbackup_client_name') or '').strip()
        api_name = str(client.get('urbackup_client_name') or name).strip() or name
        backup_root = Path(client.get('backup_root') or settings.backup_root)
        try:
            sample_size = max(1, int(client.get('sample_size') or settings.sample_size))
        except Exception:
            sample_size = settings.sample_size
        try:
            age_days = max(1, int(client.get('backup_age_threshold_days') or settings.backup_age_threshold_days))
        except Exception:
            age_days = settings.backup_age_threshold_days
        return {'name': name, 'api_name': api_name, 'backup_root': backup_root, 'sample_size': sample_size, 'backup_age_threshold_days': age_days}
    name = str(client)
    return {'name': name, 'api_name': name, 'backup_root': settings.backup_root, 'sample_size': settings.sample_size, 'backup_age_threshold_days': settings.backup_age_threshold_days}


def verify_client(settings, client, db=None):
    cfg = normalize_client_config(settings, client)
    client_name = cfg['name']
    api_name = cfg['api_name']
    backup_root = cfg['backup_root']
    warnings = []
    errors = []
    file_failures = []
    checked = failed = matches = missing = 0
    try:
        api = UrBackupClient(settings.urbackup_url, settings.urbackup_username, settings.urbackup_password).latest_successful_backup(api_name)
        warnings += api.get('warnings', [])
    except Exception as e:
        api = {}
        warnings.append(f'UrBackup API failed: {type(e).__name__}: {e}')
    root = find_client_root(backup_root, api_name) or (find_client_root(backup_root, client_name) if api_name != client_name else None)
    if not root:
        return {'client': client_name, 'urbackup_client_name': api_name, 'status': 'failed', 'last_checked': now_iso(), 'files_checked': 0, 'files_failed': 1, 'warnings': warnings, 'errors': [f'No backup directory for {api_name} under {backup_root}'], 'file_failures': [], 'api': api}
    latest = find_latest_backup_dir(root)
    dirs = backup_dirs(root)
    latest_time = backup_time(latest)
    age_days = (datetime.now(timezone.utc) - latest_time).total_seconds() / 86400
    if age_days > cfg['backup_age_threshold_days']:
        warnings.append(f"Latest backup is {age_days:.1f} days old; threshold is {cfg['backup_age_threshold_days']} days")
    if len(dirs) < settings.retention_min_copies:
        warnings.append(f'Retention policy warning: found {len(dirs)} backup copies, expected at least {settings.retention_min_copies}')
    manifest = load_checksum_manifest(latest)
    for c in settings.critical_paths:
        if not any((latest / x).exists() for x in [c, c.lower(), c.upper()]):
            warnings.append(f'Critical path missing: {c}')
    samples = random_files(latest, cfg['sample_size'])
    restore_drill = {'status': 'skipped', 'reason': 'no sample file'}
    for p in samples:
        checked += 1
        try:
            if p.stat().st_size <= 0:
                failed += 1
                msg = 'zero-size file'
                errors.append(f'{msg}: {p}')
                file_failures.append({'file': str(p), 'reason': msg})
                continue
            with p.open('rb') as f:
                f.read(4096)
            exp = manifest.get(str(p.resolve()))
            if exp:
                digest = sha256_file(p).lower() if len(exp) == 64 else hash_file(p, 'md5').lower()
                if digest == exp.lower():
                    matches += 1
                else:
                    failed += 1
                    msg = f'checksum mismatch expected={exp} actual={digest}'
                    errors.append(f'{msg}: {p}')
                    file_failures.append({'file': str(p), 'reason': msg})
            else:
                missing += 1
        except Exception as e:
            failed += 1
            msg = f'{type(e).__name__}: {e}'
            errors.append(f'Read/check failed {p}: {msg}')
            file_failures.append({'file': str(p), 'reason': msg})
    if samples:
        restore_drill = restore_one_file(samples[0])
        if restore_drill.get('status') == 'failed':
            failed += 1
            errors.append('Restore drill failed: ' + '; '.join(restore_drill.get('errors', [])))
    imgs = [verify_image(i) for i in find_images(latest)]
    failed += sum(1 for i in imgs if i['status'] == 'failed')
    if not imgs:
        warnings.append('No VHD/VHDX/raw image backup found')
    size = dir_size(latest)
    prev = db.previous_client_details(client_name) if db else None
    if prev and prev.get('backup_size_bytes'):
        old = int(prev['backup_size_bytes'])
        if old > 0 and size < old * (1 - settings.size_drop_threshold_percent / 100):
            warnings.append(f'Backup size dropped from {human_bytes(old)} to {human_bytes(size)} (> {settings.size_drop_threshold_percent}% drop)')
    status = 'failed' if failed else ('warning' if warnings else 'verified')
    return {
        'client': client_name,
        'urbackup_client_name': api_name,
        'status': status,
        'last_checked': now_iso(),
        'backup_path': str(latest),
        'latest_backup_time': latest_time.replace(microsecond=0).isoformat(),
        'backup_age_days': round(age_days, 2),
        'backup_size_bytes': size,
        'backup_size_human': human_bytes(size),
        'retention_copies_found': len(dirs),
        'retention_min_copies': settings.retention_min_copies,
        'files_checked': checked,
        'files_failed': failed,
        'checksum_matches': matches,
        'checksum_missing': missing,
        'warnings': warnings,
        'errors': errors[:50],
        'file_failures': file_failures[:100],
        'image_checks': imgs,
        'restore_drill': restore_drill,
        'api': api,
    }


def restore_one_file(path):
    r = {'source': str(path), 'status': 'failed', 'errors': []}
    try:
        with tempfile.TemporaryDirectory(prefix='backup-verify-restore-') as d:
            dst = Path(d) / Path(path).name
            shutil.copy2(path, dst)
            with dst.open('rb') as f:
                data = f.read(4096)
            if not data and dst.stat().st_size > 0:
                r['errors'].append('restored file read returned no data')
            else:
                r.update({'status': 'verified', 'restored_to': str(dst), 'bytes_read': len(data)})
    except Exception as e:
        r['errors'].append(f'{type(e).__name__}: {e}')
    return r


def verify_image(path):
    r = {'image': str(path), 'status': 'warning', 'warnings': [], 'errors': [], 'mount_test': {'status': 'skipped'}}
    if not path.exists() or path.stat().st_size <= 0:
        r['status'] = 'failed'; r['errors'].append('image missing or empty'); return r
    q = shutil.which('qemu-img')
    if q:
        cp = subprocess.run([q, 'info', '--output=json', str(path)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        if cp.returncode == 0:
            r['qemu_info'] = json.loads(cp.stdout or '{}')
            r['status'] = 'verified'
        else:
            r['status'] = 'failed'; r['errors'].append(cp.stderr[-500:])
    else:
        r['warnings'].append('qemu-img unavailable')
    guestmount = shutil.which('guestmount')
    if guestmount:
        r['mount_test'] = {'status': 'warning', 'warnings': ['guestmount available but automatic mounting is disabled unless image partition layout is explicitly configured']}
    else:
        r['mount_test'] = {'status': 'warning', 'warnings': ['guestmount unavailable; performed image metadata/readability test only']}
    return r


def sval(attrs, names):
    names = {x.lower() for x in names}
    for a in attrs or []:
        if str(a.get('name', '')).lower() in names:
            raw = a.get('raw', {})
            return raw.get('value') if isinstance(raw, dict) else raw
    return 0


def discover_smart_devices(cmd):
    if not shutil.which(cmd):
        return []
    cp = subprocess.run([cmd, '--scan-open'], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    return [l.split()[0] for l in cp.stdout.splitlines() if l.startswith('/dev/')]


def check_smart(settings, db=None):
    devs = discover_smart_devices(settings.smartctl_path) if settings.smart_devices == ['auto'] else settings.smart_devices
    drives = []
    failed = 0
    warnings = []
    if not devs:
        warnings.append('No SMART devices discovered or smartctl unavailable')
    trends = db.disk_trends() if db else {}
    for dev in devs:
        d = {'name': dev, 'status': 'unknown', 'warnings': [], 'errors': [], 'type': 'unknown'}
        try:
            cp = subprocess.run([settings.smartctl_path, '-a', '-j', dev], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            data = json.loads(cp.stdout or '{}')
            attrs = data.get('ata_smart_attributes', {}).get('table', [])
            temp = (data.get('temperature') or {}).get('current')
            is_nvme = 'nvme_smart_health_information_log' in data or str(data.get('device', {}).get('type', '')).lower().startswith('nvme')
            nv = data.get('nvme_smart_health_information_log', {})
            d.update({
                'model': data.get('model_name') or data.get('model_family'),
                'serial': data.get('serial_number'),
                'type': 'nvme' if is_nvme else 'hdd_ssd',
                'temperature': temp or nv.get('temperature'),
                'reallocated': sval(attrs, ['Reallocated_Sector_Ct', 'Reallocated_Event_Count']),
                'pending': sval(attrs, ['Current_Pending_Sector']),
                'uncorrectable': sval(attrs, ['Offline_Uncorrectable', 'Reported_Uncorrect']) or nv.get('media_errors'),
                'power_on_hours': sval(attrs, ['Power_On_Hours']) or (data.get('power_on_time') or {}).get('hours') or nv.get('power_on_hours'),
                'percentage_used': nv.get('percentage_used'),
                'available_spare': nv.get('available_spare'),
                'critical_warning': nv.get('critical_warning'),
            })
            bad = data.get('smart_status', {}).get('passed') is False or any(int(d.get(k) or 0) > 0 for k in ['reallocated', 'pending', 'uncorrectable']) or int(d.get('critical_warning') or 0) > 0
            hot = d.get('temperature') is not None and int(d.get('temperature')) >= 55
            if trends.get(dev):
                recent = trends[dev][:5]
                temps = [x.get('temperature') for x in recent if x.get('temperature') is not None]
                if len(temps) >= 3 and int(d.get('temperature') or 0) > max(temps) + 8:
                    d['warnings'].append('Drive temperature trend jumped more than 8C')
            d['status'] = 'failed' if bad else ('warning' if hot or d['warnings'] else 'healthy')
        except Exception as e:
            d['status'] = 'failed'; d['errors'].append(f'{type(e).__name__}: {e}')
        failed += 1 if d['status'] == 'failed' else 0
        drives.append(d)
    return {'status': 'failed' if failed else ('warning' if warnings or any(d.get('status') == 'warning' for d in drives) else 'healthy'), 'drives_checked': len(drives), 'drives_failed': failed, 'warnings': warnings, 'drives': drives}


def check_qnap(settings):
    if not settings.qnap_username:
        return {'status': 'warning', 'warnings': ['QNAP credentials not configured'], 'volumes': []}
    try:
        url = settings.qnap_url + '/cgi-bin/authLogin.cgi?' + urllib.parse.urlencode({'user': settings.qnap_username, 'pwd': settings.qnap_password})
        body = urllib.request.urlopen(url, context=ssl._create_unverified_context(), timeout=15).read().decode('utf-8', 'replace')
        ok = '<authPassed><![CDATA[1]]>' in body or '<authPassed>1</authPassed>' in body
        warnings = [] if ok else ['QNAP login failed or unsupported auth response']
        return {'status': 'healthy' if ok else 'warning', 'warnings': warnings, 'volumes': [{'name': 'QNAP 10.10.10.230', 'status': 'reachable' if ok else 'unknown'}]}
    except Exception as e:
        return {'status': 'warning', 'warnings': [f'QNAP API probe failed: {type(e).__name__}: {e}'], 'volumes': []}
