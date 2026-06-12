import json,os,sys,urllib.request
try:
    data=json.loads(urllib.request.urlopen(f"http://127.0.0.1:{os.environ.get('APP_PORT','10122')}/api/health",timeout=3).read().decode())
    sys.exit(0 if data.get('ok') else 1)
except Exception: sys.exit(1)
