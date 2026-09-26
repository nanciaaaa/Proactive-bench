#!/usr/bin/env python3
import hashlib,json,subprocess
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent;defs=json.loads((ROOT/'apps/app-definitions.json').read_text());apps=[]
for app_id,cfg in defs.items():
 path=ROOT/f'dist/apks/{app_id}-debug.apk'
 if not path.is_file():raise SystemExit(f'missing {path}')
 apps.append({'app_id':app_id,'package':cfg['package'],'version':'1.0.0','variant':'debug','file':path.name,'size_bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
config={}
for line in (ROOT/'config/ku-runtime.env').read_text().splitlines():
 if line and not line.startswith('#'):
  key,value=line.split('=',1);config[key]=value
image_id=subprocess.run(['docker','image','inspect',config['KU_IMAGE'],'--format','{{.Id}}'],text=True,capture_output=True,check=True).stdout.strip()
manifest={'schema_version':'1.1','created_at':datetime.now(timezone.utc).isoformat(),'toolchain':{'ku_image':config['KU_IMAGE'],'ku_image_id':image_id,'java':config['KU_JAVA_VERSION'],'android_api':config['KU_ANDROID_API'],'build_tools':config['KU_BUILD_TOOLS'],'compile_sdk':34,'target_sdk':34,'capacitor':'6.2.1'},'apps':apps}
(ROOT/'dist/apks/manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
