import hashlib,json,os,re,shutil,subprocess,urllib.parse,urllib.request,ssl
from datetime import datetime,timezone
from pathlib import Path
from .urbackup import UrBackupClient,find_client_root,find_latest_backup_dir,random_files,find_images
def now_iso(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
def sha256_file(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()
def load_checksum_manifest(root):
    m={}; names={'hashes.txt','checksums.txt','sha256sums.txt','SHA256SUMS'}
    for dp,_,fns in os.walk(root):
        for fn in fns:
            if fn in names or fn.lower().endswith(('.sha256','.md5')):
                p=Path(dp)/fn
                for line in p.read_text(errors='ignore').splitlines():
                    mm=re.match(r'^([a-fA-F0-9]{32,64})\s+[* ]?(.+)$',line.strip())
                    if mm: m[str((p.parent/mm.group(2)).resolve())]=mm.group(1).lower()
    return m
def verify_client(settings,client):
    warnings=[]; errors=[]; checked=failed=matches=missing=0
    try:
        api=UrBackupClient(settings.urbackup_url,settings.urbackup_username,settings.urbackup_password).latest_successful_backup(client); warnings+=api.get('warnings',[])
    except Exception as e: api={}; warnings.append(f'UrBackup API failed: {type(e).__name__}: {e}')
    root=find_client_root(settings.backup_root,client)
    if not root: return {'client':client,'status':'failed','last_checked':now_iso(),'files_checked':0,'files_failed':1,'warnings':warnings,'errors':[f'No backup directory for {client} under {settings.backup_root}'],'api':api}
    latest=find_latest_backup_dir(root); manifest=load_checksum_manifest(latest)
    for c in settings.critical_paths:
        if not any((latest/x).exists() for x in [c,c.lower(),c.upper()]): warnings.append(f'Critical path missing: {c}')
    for p in random_files(latest,settings.sample_size):
        checked+=1
        try:
            if p.stat().st_size<=0: failed+=1; errors.append(f'Zero-size file: {p}'); continue
            with p.open('rb') as f: f.read(4096)
            exp=manifest.get(str(p.resolve()))
            if exp:
                if sha256_file(p).lower()==exp.lower(): matches+=1
                else: failed+=1; errors.append(f'Checksum mismatch: {p}')
            else: missing+=1
        except Exception as e: failed+=1; errors.append(f'Read/check failed {p}: {type(e).__name__}: {e}')
    imgs=[verify_image(i) for i in find_images(latest)]; failed+=sum(1 for i in imgs if i['status']=='failed')
    if not imgs: warnings.append('No VHD/VHDX/raw image backup found')
    status='failed' if failed else ('warning' if warnings else 'verified')
    return {'client':client,'status':status,'last_checked':now_iso(),'backup_path':str(latest),'files_checked':checked,'files_failed':failed,'checksum_matches':matches,'checksum_missing':missing,'warnings':warnings,'errors':errors[:50],'image_checks':imgs,'api':api}
def verify_image(path):
    r={'image':str(path),'status':'warning','warnings':[],'errors':[]}
    if not path.exists() or path.stat().st_size<=0: r['status']='failed'; r['errors'].append('image missing or empty'); return r
    q=shutil.which('qemu-img')
    if q:
        cp=subprocess.run([q,'info','--output=json',str(path)],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
        if cp.returncode==0: r['qemu_info']=json.loads(cp.stdout or '{}'); r['status']='verified'
        else: r['status']='failed'; r['errors'].append(cp.stderr[-500:])
    else: r['warnings'].append('qemu-img unavailable')
    r['warnings'].append('Full filesystem mount/fsck requires privileged container and host-supported image tooling')
    return r
def sval(attrs,names):
    names={x.lower() for x in names}
    for a in attrs or []:
        if str(a.get('name','')).lower() in names:
            raw=a.get('raw',{}); return raw.get('value') if isinstance(raw,dict) else raw
    return 0
def discover_smart_devices(cmd):
    if not shutil.which(cmd): return []
    cp=subprocess.run([cmd,'--scan-open'],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    return [l.split()[0] for l in cp.stdout.splitlines() if l.startswith('/dev/')]
def check_smart(settings):
    devs=discover_smart_devices(settings.smartctl_path) if settings.smart_devices==['auto'] else settings.smart_devices; drives=[]; failed=0; warnings=[]
    if not devs: warnings.append('No SMART devices discovered or smartctl unavailable')
    for dev in devs:
        d={'name':dev,'status':'unknown','warnings':[],'errors':[]}
        try:
            cp=subprocess.run([settings.smartctl_path,'-a','-j',dev],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60); data=json.loads(cp.stdout or '{}')
            attrs=data.get('ata_smart_attributes',{}).get('table',[]); temp=(data.get('temperature') or {}).get('current')
            d.update({'model':data.get('model_name'),'serial':data.get('serial_number'),'temperature':temp,'reallocated':sval(attrs,['Reallocated_Sector_Ct','Reallocated_Event_Count']),'pending':sval(attrs,['Current_Pending_Sector']),'uncorrectable':sval(attrs,['Offline_Uncorrectable','Reported_Uncorrect']),'power_on_hours':sval(attrs,['Power_On_Hours']) or (data.get('power_on_time') or {}).get('hours')})
            bad=data.get('smart_status',{}).get('passed') is False or any(int(d.get(k) or 0)>0 for k in ['reallocated','pending','uncorrectable']); hot=temp is not None and int(temp)>=55
            d['status']='failed' if bad else ('warning' if hot else 'healthy')
        except Exception as e: d['status']='failed'; d['errors'].append(f'{type(e).__name__}: {e}')
        failed += 1 if d['status']=='failed' else 0; drives.append(d)
    return {'status':'failed' if failed else ('warning' if warnings else 'healthy'),'drives_checked':len(drives),'drives_failed':failed,'warnings':warnings,'drives':drives}
def check_qnap(settings):
    if not settings.qnap_username: return {'status':'warning','warnings':['QNAP credentials not configured']}
    try:
        url=settings.qnap_url+'/cgi-bin/authLogin.cgi?'+urllib.parse.urlencode({'user':settings.qnap_username,'pwd':settings.qnap_password})
        body=urllib.request.urlopen(url,context=ssl._create_unverified_context(),timeout=15).read().decode('utf-8','replace')
        ok='<authPassed><![CDATA[1]]>' in body or '<authPassed>1</authPassed>' in body
        return {'status':'healthy' if ok else 'warning','warnings':[] if ok else ['QNAP login failed or unsupported auth response']}
    except Exception as e: return {'status':'warning','warnings':[f'QNAP API probe failed: {type(e).__name__}: {e}']}
