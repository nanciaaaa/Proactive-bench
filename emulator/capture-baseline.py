#!/usr/bin/env python3
"""Capture a versioned, read-only manifest from an already running KU AVD."""
import argparse, hashlib, json, re, subprocess
from datetime import datetime, timezone
from pathlib import Path

def adb(serial,*args,check=True):
 cmd=['adb']+(['-s',serial] if serial else [])+list(args)
 return subprocess.run(cmd,text=True,capture_output=True,check=check).stdout.strip()
def prop(serial,name):return adb(serial,'shell','getprop',name)
def run(*args,check=False):
 return subprocess.run(list(args),text=True,capture_output=True,check=check).stdout.strip()
def main():
 p=argparse.ArgumentParser();p.add_argument('--serial',required=True);p.add_argument('--avd',required=True);p.add_argument('--snapshot',default='init_state');p.add_argument('--snapshot-file');p.add_argument('--avd-config');p.add_argument('--system-image');p.add_argument('--ku-container');p.add_argument('--ku-image',default='ghcr.io/yaosqz/knowu-bench:latest');p.add_argument('--output',required=True);a=p.parse_args()
 packages=[]
 for line in adb(a.serial,'shell','pm','list','packages','-f','-U').splitlines():
  m=re.match(r'package:(.+\.apk)=(\S+)(?: uid:(\d+))?',line)
  if not m:continue
  apk,pkg,uid=m.groups(); dump=adb(a.serial,'shell','dumpsys','package',pkg,check=False)
  def one(pattern,default=''):
   x=re.search(pattern,dump,re.M);return x.group(1).strip() if x else default
  launcher=adb(a.serial,'shell','cmd','package','resolve-activity','--brief','-a','android.intent.action.MAIN','-c','android.intent.category.LAUNCHER',pkg,check=False).splitlines()
  apk_hash=adb(a.serial,'shell','sha256sum',apk,check=False).split(' ')[0]
  launcher_activity=launcher[-1] if launcher and 'No activity found' not in launcher[-1] else ''
  signature=one(r'signatures:\[([^\]]*)\]') or one(r'\bSHA-256 digest: ([0-9A-F:]+)')
  appops=[]
  for op_line in adb(a.serial,'shell','cmd','appops','get',pkg,check=False).splitlines():
   op_match=re.match(r'^\s*([A-Z0-9_]+):\s*([a-z_]+)',op_line)
   if op_match:appops.append({'op':op_match.group(1),'mode':op_match.group(2)})
  packages.append({'package':pkg,'apk_path':apk,'apk_sha256':apk_hash,'uid':uid,'version_code':one(r'versionCode=(\d+)'),'version_name':one(r'versionName=([^\s]+)'),'launcher_activity':launcher_activity,'signing_certificate':signature,'data_dir':one(r'dataDir=([^\s]+)'),'granted_permissions':sorted(re.findall(r'^\s+([\w.]+): granted=true',dump,re.M)),'appops':sorted(appops,key=lambda x:x['op'])})
 config=adb(a.serial,'shell','wm','size',check=False)+'; '+adb(a.serial,'shell','wm','density',check=False)
 def file_hash(value):
  if not value:return None
  path=Path(value);return 'sha256:'+hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
 image_id=run('docker','image','inspect',a.ku_image,'--format','{{.Id}}') if a.ku_image else ''
 ku={}
 if a.ku_container:
  prefix=('docker','exec',a.ku_container)
  def inside(*args):return run(*prefix,*args)
  avd_root=f'/root/.android/avd/{a.avd}.avd'
  ku={'image':a.ku_image,'image_id':image_id,'java_version':inside('bash','-lc','java -version 2>&1 | head -n1'),'android_sdk_root':inside('bash','-lc','printf %s "$ANDROID_SDK_ROOT"'),'build_tools':inside('bash','-lc','basename "$(find "$ANDROID_SDK_ROOT/build-tools" -mindepth 1 -maxdepth 1 -type d | sort | tail -n1)"'),'emulator_start_script_sha256':inside('sha256sum','/app/docker/start_emulator.sh').split(' ')[0],'novnc_start_script_sha256':inside('sha256sum','/app/docker/start_novnc.sh').split(' ')[0],'avd_config_sha256':inside('sha256sum',f'{avd_root}/config.ini').split(' ')[0],'snapshot_tree_sha256':inside('bash','-lc',f'find {avd_root}/snapshots/{a.snapshot} -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum').split(' ')[0],'system_image_tree_sha256':inside('bash','-lc','find "$ANDROID_SDK_ROOT/system-images/android-34/google_apis/x86_64" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum').split(' ')[0]}
  emulator_version=inside('bash','-lc','emulator -version | head -n1')
 else:
  emulator_version=subprocess.run(['emulator','-version'],text=True,capture_output=True).stdout.splitlines()[0] if subprocess.run(['which','emulator'],capture_output=True).returncode==0 else 'unavailable'
  ku={'image':a.ku_image,'image_id':image_id}
 snapshot_hash=file_hash(a.snapshot_file) or (f"sha256:{ku['snapshot_tree_sha256']}" if ku.get('snapshot_tree_sha256') else None)
 avd_config_hash=file_hash(a.avd_config) or (f"sha256:{ku['avd_config_sha256']}" if ku.get('avd_config_sha256') else None)
 system_image_hash=file_hash(a.system_image) or (f"sha256:{ku['system_image_tree_sha256']}" if ku.get('system_image_tree_sha256') else None)
 manifest={'schema_version':'1.1','captured_at':datetime.now(timezone.utc).isoformat(),'ku_runtime':ku,'avd':a.avd,'serial':a.serial,'snapshot':a.snapshot,'snapshot_sha256':snapshot_hash,'avd_config_sha256':avd_config_hash,'system_image_sha256':system_image_hash,'android_api':prop(a.serial,'ro.build.version.sdk'),'abi':prop(a.serial,'ro.product.cpu.abi'),'system_fingerprint':prop(a.serial,'ro.build.fingerprint'),'emulator_version':emulator_version,'screen':config,'locale':prop(a.serial,'persist.sys.locale'),'timezone':prop(a.serial,'persist.sys.timezone'),'input_method':adb(a.serial,'shell','settings','get','secure','default_input_method',check=False),'network':{'airplane_mode':adb(a.serial,'shell','settings','get','global','airplane_mode_on',check=False),'private_dns':adb(a.serial,'shell','settings','get','global','private_dns_mode',check=False)},'certificates':adb(a.serial,'shell','ls','/system/etc/security/cacerts',check=False).splitlines(),'packages':sorted(packages,key=lambda x:x['package'])}
 raw=json.dumps(manifest,ensure_ascii=False,sort_keys=True).encode();manifest['manifest_sha256']=hashlib.sha256(raw).hexdigest()
 out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
 print(out)
if __name__=='__main__':main()
