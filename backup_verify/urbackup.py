import base64,json,os,random,urllib.parse,urllib.request
from pathlib import Path
class UrBackupClient:
    def __init__(self,base_url,username='',password=''): self.base_url=base_url.rstrip('/'); self.username=username; self.password=password
    def request(self,path,params=None):
        url=self.base_url+path+(('?'+urllib.parse.urlencode(params)) if params else '')
        headers={}
        if self.username or self.password: headers['Authorization']='Basic '+base64.b64encode(f'{self.username}:{self.password}'.encode()).decode()
        with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=20) as r: body=r.read()
        return json.loads(body.decode('utf-8','replace')) if body[:1] in (b'{',b'[') else body.decode('utf-8','replace')
    def latest_successful_backup(self,client):
        errors=[]
        for params in [{'a':'backups','clientname':client},{'a':'backups','client':client},{'a':'status'}]:
            try: return {'source':'api','raw':self.request('/x',params)}
            except Exception as e: errors.append(f'{type(e).__name__}: {e}')
        return {'source':'api-unavailable','warnings':errors}
def find_client_root(backup_root,client):
    if not backup_root.exists(): return None
    if (backup_root/client).exists(): return backup_root/client
    for p in backup_root.iterdir():
        if p.is_dir() and client.lower() in p.name.lower(): return p
    return None
def find_latest_backup_dir(client_root):
    dirs=[p for p in client_root.iterdir() if p.is_dir()]
    return sorted(dirs,key=lambda p:p.stat().st_mtime)[-1] if dirs else client_root
def random_files(root,count):
    files=[]
    for dp,_,fns in os.walk(root):
        for fn in fns:
            if fn in {'hashes.txt','checksums.txt','sha256sums.txt','SHA256SUMS'} or fn.lower().endswith(('.sha256','.md5')):
                continue
            p=Path(dp)/fn
            if p.suffix.lower() not in {'.vhd','.vhdx','.img','.raw'}: files.append(p)
        if len(files)>max(count*50,5000): break
    random.SystemRandom().shuffle(files); return files[:count]
def find_images(root):
    out=[]
    for dp,_,fns in os.walk(root):
        for fn in fns:
            if fn.lower().endswith(('.vhd','.vhdx','.img','.raw')): out.append(Path(dp)/fn)
    return sorted(out,key=lambda p:p.stat().st_mtime,reverse=True)[:5]
