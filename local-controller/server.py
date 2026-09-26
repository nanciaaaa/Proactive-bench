#!/usr/bin/env python3
import argparse, hashlib, json, os, re, secrets, shutil, subprocess, sys, threading, time, zipfile
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse,parse_qs
sys.path.insert(0,str(Path(__file__).resolve().parent))
from datasets import build_dataset,RESOURCES
from store import Store,APPS,Conflict,BusinessError

ROOT=Path(__file__).resolve().parent.parent
ERROR_CODES=json.loads((ROOT/'contracts/error-codes.json').read_text())['error_codes']
PACKAGES={app:f'com.ku.simulator.{app}' for app in APPS}
NOTIFICATION_APPS={'dudu'}
SCENARIOS=[
 {'id':'permission','label':'权限请求','mode':'popup','kind':'permission','title':'开启位置服务','message':'允许获取位置信息，以便提供附近的车辆和内容。'},
 {'id':'promotion','label':'促销弹窗','mode':'popup','kind':'promotion','title':'限时优惠','message':'本周精选权益已经到账，点击即可查看。'},
 {'id':'inventory','label':'库存不足','mode':'business_error','simulate':'inventory_changed'},
 {'id':'price','label':'价格变化','mode':'business_error','simulate':'price_changed'},
 {'id':'payment','label':'支付确认','mode':'popup','kind':'payment','title':'支付确认','message':'确认使用账户余额完成支付。'},
 {'id':'cancel_loss','label':'取消损失','mode':'popup','kind':'cancel_loss','title':'取消提示','message':'当前订单取消后可能产生手续费。'},
 {'id':'time_conflict','label':'时间冲突','mode':'business_error','simulate':'time_conflict'},
 {'id':'duplicate','label':'重复收藏','mode':'business_error','simulate':'already_favorited'},
 {'id':'membership','label':'会员内容','mode':'popup','kind':'membership','title':'会员专享内容','message':'开通会员即可解锁更多专属权益。'},
 {'id':'network','label':'网络失败','mode':'network_failure'},
]
class Runtime:
 def __init__(self,args):
  self.args=args; self.initial_sim_time=args.sim_time; self.dir=Path(args.runtime).resolve(); self.dir.mkdir(parents=True,exist_ok=True);self.lock=threading.RLock()
  try:existing_info=json.loads((self.dir/'instance.json').read_text())
  except (OSError,json.JSONDecodeError):existing_info={}
  for name in ('datasets','logs','screenshots','exports'): (self.dir/name).mkdir(exist_ok=True)
  source=ROOT/'datasets/generated/catalog.sqlite'; self.dataset=build_dataset(source)
  target=self.dir/'datasets/catalog.sqlite'
  manifest_source=source.with_suffix('.manifest.json');manifest_target=self.dir/'datasets/catalog.manifest.json'
  if target.exists() and manifest_target.exists():
   try:installed_version=json.loads(manifest_target.read_text()).get('dataset_version')
   except (OSError,json.JSONDecodeError):installed_version=None
   if installed_version and installed_version!=self.dataset['dataset_version']:
    target.chmod(0o644);target.unlink();shutil.copy2(source,target);shutil.copy2(manifest_source,manifest_target)
  elif not target.exists():
   shutil.copy2(source,target);shutil.copy2(manifest_source,manifest_target)
  target.chmod(0o444)
  actual='sha256:'+hashlib.sha256(target.read_bytes()).hexdigest()
  self.dataset_path=target; self.lifecycle='INITIALIZING'
  artifact_manifest=ROOT/'dist/apks/manifest.json';apk_versions={app:'unbuilt' for app in APPS}
  if artifact_manifest.exists():
   for entry in json.loads(artifact_manifest.read_text()).get('apps',[]):apk_versions[entry['app_id']]=entry['version']
  self.info={'instance_id':args.instance_id,'adb_serial':args.adb_serial,'avd':args.avd,'snapshot':args.snapshot,'ku_container':args.ku_container,'ku_image':args.ku_image,'ku_image_id':args.ku_image_id,'baseline_version':args.baseline_version,'apk_versions':apk_versions,'dataset_version':self.dataset['dataset_version'],'seed':args.seed,'simulated_time':args.sim_time,'controller_port':args.port,'lifecycle':self.lifecycle}
  existing_key=existing_info.get('runner_key')
  self.runner_key=existing_key if isinstance(existing_key,str) and len(existing_key) >= 32 else secrets.token_urlsafe(32)
  self.feature_flags={'debug':True,'scenario_panel':True,'user_memory_enabled':False}
  self._save(); self.store=Store(self.dir/'state.sqlite',args.instance_id,args.seed,args.sim_time,self.dir/'events.jsonl');self.lifecycle='READY' if actual==self.dataset['checksum'] else 'CONTAMINATED';self._save()
 def _save(self):self.info['lifecycle']=self.lifecycle;(self.dir/'instance.json').write_text(json.dumps({**self.info,'runner_key':self.runner_key},ensure_ascii=False,indent=2)+'\n',encoding='utf8')
 def transition(self,state):self.lifecycle=state;self._save()
 def rebuild_store(self):
  with self.lock:
   self.store.close(); state=self.dir/'state.sqlite'
   for candidate in (state,Path(str(state)+'-wal'),Path(str(state)+'-shm')):
    if candidate.exists():candidate.unlink()
   events=self.dir/'events.jsonl';events.write_text('',encoding='utf8')
   for folder in (self.dir/'screenshots',self.dir/'exports'):
    for artifact in folder.iterdir():
     if artifact.is_file():artifact.unlink()
   (self.dir/'logs/access.log').write_text('',encoding='utf8')
   self.args.sim_time=self.initial_sim_time;self.info['simulated_time']=self.initial_sim_time
   self.store=Store(state,self.args.instance_id,self.args.seed,self.initial_sim_time,events)
 def reset_authority(self):
  with self.lock:
   before=self.store.summary();self.transition('RESETTING')
   try:
    actual='sha256:'+hashlib.sha256(self.dataset_path.read_bytes()).hexdigest()
    if actual!=self.dataset['checksum']:
     self.transition('CONTAMINATED');return False
    self.rebuild_store();self.transition('READY');after=self.store.summary();self.store.record_debug('instance_reset',{'before':before,'after':after});return True
   except Exception:self.transition('FAILED');return False
 def export_instance(self):
  with self.lock:
   out=self.dir/'exports'/f'{self.args.instance_id}.zip';snapshot=self.dir/'exports'/'state.sqlite';self.store.backup(snapshot)
   with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
    z.write(self.dir/'instance.json','instance.json');z.write(snapshot,'state.sqlite');z.write(self.dir/'events.jsonl','events.jsonl');z.write(self.dataset_path,'datasets/catalog.sqlite')
    manifest=self.dir/'datasets/catalog.manifest.json'
    if manifest.exists():z.write(manifest,'datasets/catalog.manifest.json')
   snapshot.unlink();return out
 def adb(self,*args,binary=False):
  if not self.args.adb_serial:return None
  try:result=subprocess.run(['adb','-s',self.args.adb_serial,*args],capture_output=True,text=not binary,timeout=20)
  except (subprocess.TimeoutExpired,OSError):return None
  if result.returncode:return None
  return result.stdout
 def ku_adb(self,*args):
  if not self.args.ku_container:return self.adb(*args)
  try:result=subprocess.run(['docker','exec',self.args.ku_container,'adb',*args],capture_output=True,text=True,timeout=20)
  except (subprocess.TimeoutExpired,OSError):return None
  return result.stdout if result.returncode==0 else None

class API(BaseHTTPRequestHandler):
 server_version='KULocalController/1.0'
 def log_message(self,fmt,*args):
  with (self.server.runtime.dir/'logs/access.log').open('a') as f:f.write((fmt%args)+'\n')
 def json(self,status,data,headers=None):
  raw=json.dumps(data,ensure_ascii=False).encode(); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(raw)))
  self.send_header('Cache-Control','no-store');
  self.send_header('Access-Control-Allow-Origin','*');self.send_header('Access-Control-Allow-Headers','Content-Type,If-None-Match,X-Runner-Key');self.send_header('Access-Control-Allow-Methods','GET,POST,OPTIONS')
  for k,v in (headers or {}).items():self.send_header(k,v)
  self.end_headers();self.wfile.write(raw)
 def body(self):
  try:
   length=int(self.headers.get('Content-Length','0'))
   if length>1024*1024:return None
   return json.loads(self.rfile.read(length) or b'{}')
  except:return None
 def do_GET(self):self.route('GET')
 def do_POST(self):self.route('POST')
 def do_OPTIONS(self):self.send_response(204);self.send_header('Access-Control-Allow-Origin','*');self.send_header('Access-Control-Allow-Headers','Content-Type,If-None-Match,X-Runner-Key');self.send_header('Access-Control-Allow-Methods','GET,POST,OPTIONS');self.end_headers()
 def route(self,method):
  r=self.server.runtime; u=urlparse(self.path); p=u.path; q=parse_qs(u.query)
  m_mgmt=re.fullmatch(r'/local/v1/apps/([^/]+)/(seed|read_state|restore_baseline|mutations|teardown|capabilities)',p)
  m_query=re.fullmatch(r'/local/v1/apps/([^/]+)/query',p)
  mgmt=(p in ('/local/v1/instance/advance_time','/local/v1/instance/freeze','/local/v1/instance/unfreeze','/local/v1/instance/feature_flag','/local/v1/instance/export','/local/v1/apps/user_memory/query')) or (m_mgmt and m_mgmt.group(1) in APPS) or (method=='POST' and p.startswith('/local/v1/') and not (m_query and m_query.group(1) in APPS))
  if mgmt and not secrets.compare_digest(self.headers.get('X-Runner-Key',''),r.runner_key):
   source_match=re.match(r'/local/v1/apps/([^/]+)/',p)
   return self.json(403,{'status':'permission_denied','source':source_match.group(1) if source_match else 'local-controller','operation':p,'semantic_ref':None,'record_version':None,'observed_at':r.args.sim_time,'coverage':None,'data':None,'error':{'code':'PERMISSION_DENIED','message':'missing or invalid X-Runner-Key','retryable':False}})
  if p=='/health':return self.json(200,{'status':'ok','lifecycle':r.lifecycle,'instance_id':r.args.instance_id})
  if p=='/local/v1/instance/status':return self.json(200,r.info|{'lifecycle':r.lifecycle})
  if p=='/local/v1/device/status':
   state=r.adb('get-state');boot=r.adb('shell','getprop','sys.boot_completed');reverse=r.adb('reverse','--list')
   installed={app:bool(r.adb('shell','pm','path',pkg)) for app,pkg in PACKAGES.items()} if state else {app:False for app in APPS}
   return self.json(200,{'available':bool(state),'state':state.strip() if state else 'unavailable','boot_completed':boot.strip()=='1' if boot else False,'reverse':reverse.splitlines() if reverse else [],'installed':installed})
  if p=='/local/v1/snapshot/status':
   result=r.ku_adb('emu','avd','snapshot','list');return self.json(200,{'available':result is not None,'raw':result or '','snapshot':r.args.snapshot,'source':'ku-container' if r.args.ku_container else 'host-adb'})
  if p=='/local/v1/sqlite/summary':return self.json(200,r.store.summary())
  m=re.fullmatch(r'/local/v1/screenshots/(\d+\.png)',p)
  if m:
   target=r.dir/'screenshots'/m.group(1)
   if not target.is_file():return self.err(404,'SCREENSHOT_NOT_FOUND')
   raw=target.read_bytes();self.send_response(200);self.send_header('Content-Type','image/png');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw);return
  if p.startswith('/console'):
   file=ROOT/'debug-console/index.html'; raw=file.read_bytes(); self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
  if p=='/local/v1/faults':
   if method=='GET':return self.json(200,{'faults':r.store.faults()})
   if r.store.frozen:return self.err(409,'SESSION_FROZEN')
   rule=self.body()
   if rule is None:return self.err(400,'INVALID_JSON')
   created=r.store.add_fault(rule);r.store.record_debug('fault_created',{'before':None,'after':created},created['app_id'] if created['app_id'] in APPS else 'system');return self.json(201,{'success':True,'fault':created})
  fault=r.store.match_fault(p) if p.startswith('/runtime/') else None
  if fault:
   if fault['delay_ms']:time.sleep(min(fault['delay_ms'],30000)/1000)
   if fault['kind'] in ('offline','timeout','http_error','intermittent','pagination_failure'):
    fm=re.fullmatch(r'/runtime/v1/apps/([^/]+)/.*',p)
    if not r.store.frozen:r.store._emit_packet(fm.group(1) if fm else 'system','notification','fault_injection',{'kind':fault['kind'],'path':p},text_projection='网络暂时不可用')
    return self.err(fault['status'] or 503,'SIMULATED_NETWORK_FAILURE')
  m=re.fullmatch(r'/local/v1/apps/([^/]+)/teardown',p)
  if method=='POST' and m:
   app=m.group(1)
   if app not in APPS:return self.err(404,'APP_NOT_FOUND')
   body=self.body() or {}; episode=body.get('episode_scope')
   if not isinstance(episode,str) or not episode.strip():return self.adapter_err(404,'EPISODE_NOT_FOUND','teardown')
   try:result=r.store.teardown(app,episode)
   except BusinessError as e:
    if e.code=='TEARDOWN_FAILED':r.transition('FAILED')
    return self.adapter_err(e.status,e.code,'teardown')
   return self.adapter_json('teardown',result)
  if method=='POST' and p=='/local/v1/instance/freeze':
   r.store.freeze();return self.json(200,{'success':True,'frozen':True})
  if method=='POST' and p=='/local/v1/instance/unfreeze':
   r.store.unfreeze();return self.json(200,{'success':True,'frozen':False})
  if method=='POST' and p=='/local/v1/instance/advance_time':
   if r.store.frozen:return self.adapter_err(409,'SESSION_FROZEN','advance_time')
   body=self.body() or {}; value=body.get('logical_time')
   try:
    target=datetime.fromisoformat(value)
    if target.utcoffset() is None or target<datetime.fromisoformat(r.args.sim_time):raise ValueError
   except (TypeError,ValueError):return self.adapter_err(400,'INVALID_SIMULATED_TIME','advance_time')
   before=r.args.sim_time;r.args.sim_time=value;r.info['simulated_time']=value;r._save()
   result=r.store.advance_time(value)
   r.store.record_debug('time_advanced',{'before':before,'after':value})
   return self.adapter_json('advance_time',{'status':'ok','data':result})
  if method=='POST' and p=='/local/v1/instance/feature_flag':
   if r.store.frozen:return self.err(409,'SESSION_FROZEN')
   body=self.body() or {}; flag=body.get('flag'); enabled=body.get('enabled')
   if flag not in r.feature_flags or not isinstance(enabled,bool):return self.err(400,'INVALID_OPERATION')
   r.feature_flags[flag]=enabled
   return self.json(200,{'success':True,'feature_flags':r.feature_flags})
  if method=='POST' and p=='/local/v1/apps/user_memory/query':
   if not r.feature_flags.get('user_memory_enabled'):
    return self.adapter_json('user_memory/query',{'status':'feature_off','data':None})
   return self.adapter_json('user_memory/query',{'status':'ok','data':{'memories':[],'episode_scope':None,'as_of':r.args.sim_time}})
  m=re.fullmatch(r'/local/v1/apps/([^/]+)/capabilities',p)
  if m:
   app=m.group(1)
   if app not in APPS:return self.json(404,{'status':'not_found','source':'local-controller','operation':p,'semantic_ref':None,'record_version':None,'observed_at':r.args.sim_time,'coverage':None,'data':None,'error':{'code':'APP_NOT_FOUND','message':'unknown app','retryable':False}})
   catalog=json.loads((ROOT/'contracts/entity-catalog'/f'{app}.json').read_text())
   return self.json(200,{'status':'ok','source':app,'operation':p,'semantic_ref':None,'record_version':None,'observed_at':r.args.sim_time,'coverage':None,'data':catalog})
  m=re.fullmatch(r'/local/v1/apps/([^/]+)/restore_baseline',p)
  if method=='POST' and m:
   app=m.group(1)
   if app not in APPS:return self.err(404,'APP_NOT_FOUND')
   body=self.body() or {}
   if body.get('baseline_ref')!=r.args.snapshot or not isinstance(body.get('episode_scope'),str) or not body['episode_scope'].strip():return self.adapter_err(400,'INVALID_FIXTURE','restore_baseline')
   try:return self.adapter_json('restore_baseline',{'data':r.store.restore_baseline(app,body['episode_scope'],body['baseline_ref'],r.info)})
   except BusinessError as e:
    if e.code=='TEARDOWN_FAILED':r.transition('FAILED')
    return self.adapter_err(e.status,e.code,'restore_baseline')
  m=re.fullmatch(r'/local/v1/apps/([^/]+)/mutations',p)
  if method=='GET' and m:
   app=m.group(1)
   if app not in APPS:return self.err(404,'APP_NOT_FOUND')
   try:since=int(q.get('since_cursor',['0'])[0])
   except ValueError:return self.err(400,'INVALID_PAGINATION')
   if since<0:return self.adapter_err(400,'INVALID_PAGINATION','read_mutations')
   return self.adapter_json('read_mutations',{'status':'ok','data':r.store.read_mutations(app,since)})
  m=re.fullmatch(r'/local/v1/apps/([^/]+)/passive/events',p)
  if method=='GET' and m:
   app=m.group(1)
   if app not in APPS:return self.err(404,'APP_NOT_FOUND')
   try:cursor=int(q.get('cursor',['0'])[0]);limit=int(q.get('limit',['100'])[0])
   except ValueError:return self.err(400,'INVALID_PAGINATION')
   if cursor<0 or limit<1:return self.adapter_err(400,'INVALID_PAGINATION','drain_events')
   return self.adapter_json('drain_events',{'status':'ok','data':r.store.drain_events(app,cursor,min(limit,200))})
  m=re.fullmatch(r'/local/v1/apps/([^/]+)/(seed|read_state|query)',p)
  if method=='POST' and m:
   app,op=m.groups()
   if app not in APPS:return self.err(404,'APP_NOT_FOUND')
   body=self.body() or {}
   try:
    if op=='seed':
     fixture=body.get('fixture',body);result=r.store.seed(app,fixture,body.get('idempotency_key'))
    elif op=='read_state':result=r.store.read_state(app,body.get('scope'))
    else:result=r.store.query(app,body.get('operation'),body.get('selector'),body.get('projection'),body.get('as_of'))
    if result.get('status')=='not_found':return self.adapter_err(404,'NOT_FOUND',op,result.get('error',{}).get('message'))
    return self.adapter_json(op,result)
   except Conflict:return self.adapter_err(409,'REVISION_CONFLICT',op)
   except BusinessError as e:return self.adapter_err(e.status,e.code,op)
   except ValueError as e:return self.adapter_err(400,str(e),op)
  m=re.fullmatch(r'/runtime/v1/apps/([^/]+)/bootstrap',p)
  if m:
   app=m.group(1)
   if app not in APPS:return self.err(404,'APP_NOT_FOUND')
   if r.lifecycle not in ('READY','RUNNING'):return self.err(409,'SESSION_NOT_READY')
   if r.lifecycle=='READY':r.transition('RUNNING')
   rev,_=r.store.state(app); data={'contract_version':'1.0','adapter_contract_version':'1.0','instance':r.info,'app_config_version':'1.2','dataset':{'version':r.dataset['dataset_version'],'checksum':r.dataset['checksum'],'resources':RESOURCES[app],'data_origin':r.dataset['data_origin']},'seed':r.args.seed,'simulated_time':r.args.sim_time,'simulated_location':{'lat':31.2304,'lng':121.4737},'feature_flags':r.feature_flags,'initial_state_ref':{'revision':rev},'popup_script_ref':'deterministic-v1','scenarios':SCENARIOS}
   etag='"'+hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()+'"'
   if self.headers.get('If-None-Match')==etag:self.send_response(304);self.end_headers();return
   return self.json(200,data,{'ETag':etag})
  m=re.fullmatch(r'/local/v1/apps/([^/]+)/data/([^/]+)',p)
  if method=='POST' and m:
   app,res=m.groups()
   if app not in APPS or res not in RESOURCES[app]:return self.err(404,'RESOURCE_NOT_FOUND')
   body=self.body() or {}
   try:items=r.store.inject_records(app,res,body.get('items'))
   except BusinessError as e:return self.err(e.status,e.code)
   except ValueError as e:return self.err(400,str(e))
   r.store.record_debug('data_injected',{'resource':res,'count':len(items)},app)
   return self.json(200,{'success':True,'app_id':app,'resource':res,'count':len(items),'items':items})
  m=re.fullmatch(r'/runtime/v1/apps/([^/]+)/data/([^/]+)',p)
  if method=='GET' and m:
   app,res=m.groups()
   if app not in APPS or res not in RESOURCES[app]:return self.err(404,'RESOURCE_NOT_FOUND')
   try:limit=max(1,min(int(q.get('limit',['50'])[0]),200)); offset=max(0,int(q.get('offset',['0'])[0]))
   except ValueError:return self.err(400,'INVALID_PAGINATION')
   search=q.get('q',[''])[0]
   visible=r.store.visible_records(app,res,search);page_items=visible[offset:offset+limit];has_more=offset+limit<len(visible)
   return self.json(200,{'items':page_items,'page':{'offset':offset,'limit':limit,'next_offset':offset+len(page_items),'has_more':has_more}})
  m=re.fullmatch(r'/(?:runtime|local)/v1/apps/([^/]+)/state',p)
  if m:
   app=m.group(1)
   if app not in APPS:return self.err(404,'APP_NOT_FOUND')
   rev,state=r.store.state(app);return self.json(200,{'revision':rev,'state':state})
  m=re.fullmatch(r'/local/v1/apps/([^/]+)/events',p)
  if m:
   app=m.group(1)
   if app not in APPS:return self.err(404,'APP_NOT_FOUND')
   try:limit=max(1,min(int(q.get('limit',['100'])[0]),1000))
   except ValueError:return self.err(400,'INVALID_PAGINATION')
   return self.json(200,{'events':r.store.events(app,limit)})
  if p=='/local/v1/events':
   try:limit=max(1,min(int(q.get('limit',['200'])[0]),1000))
   except ValueError:return self.err(400,'INVALID_PAGINATION')
   return self.json(200,{'events':r.store.events(None,limit)})
  if method=='POST' and p=='/local/v1/instance/init':r.transition('READY');return self.json(200,{'success':True,'status':'READY'})
  if method=='POST' and p=='/local/v1/instance/lifecycle':
   state=(self.body() or {}).get('state')
   if state not in ('STOPPED','INITIALIZING','READY','RUNNING','RESETTING','FAILED','CONTAMINATED'):return self.err(400,'INVALID_LIFECYCLE')
   r.transition(state);return self.json(200,{'success':True,'status':state})
  if p=='/local/v1/clock':
   if method=='GET':return self.json(200,{'simulated_time':r.args.sim_time})
   if r.store.frozen:return self.err(409,'SESSION_FROZEN')
   body=self.body() or {}; value=body.get('simulated_time')
   if not value:return self.err(400,'INVALID_SIMULATED_TIME')
   before=r.args.sim_time;r.args.sim_time=value;r.info['simulated_time']=value;r.store.sim_time=value;r._save();r.store.record_debug('clock_changed',{'before':before,'after':value});return self.json(200,{'success':True,'simulated_time':value})
  if method=='POST' and p=='/local/v1/instance/reset':
   return self.json(200,{'success':True,'status':'READY'}) if r.reset_authority() else self.err(500,'RESET_FAILED')
  if method=='POST' and p=='/local/v1/instance/export':
   out=r.export_instance()
   return self.json(200,{'success':True,'path':str(out),'sha256':hashlib.sha256(out.read_bytes()).hexdigest()})
  if method=='POST' and p=='/local/v1/device/screenshot':
   raw=r.adb('exec-out','screencap','-p',binary=True)
   if raw is None:return self.err(503,'DEVICE_NOT_AVAILABLE')
   target=r.dir/'screenshots'/f'{int(time.time()*1000)}.png';target.write_bytes(raw);r.store.record_debug('screenshot_captured',{'path':str(target)});url='/local/v1/screenshots/'+target.name
   app=(self.body() or {}).get('app_id') or 'system'
   r.store._emit_packet(app if app in APPS else 'system','gui','screenshot',{'url':url},text_projection='当前屏幕截图',screenshot_ref=url)
   return self.json(201,{'success':True,'path':str(target),'url':url})
  m=re.fullmatch(r'/local/v1/apps/([^/]+)/process',p)
  if method=='POST' and m:
   app=m.group(1);action=(self.body() or {}).get('action')
   if app not in PACKAGES or action not in ('start','stop'):return self.err(400,'INVALID_PROCESS_ACTION')
   result=r.adb('shell','am','force-stop',PACKAGES[app]) if action=='stop' else r.adb('shell','am','start','-n',PACKAGES[app]+'/.MainActivity')
   if result is None:return self.err(503,'DEVICE_NOT_AVAILABLE')
   r.store.record_debug('app_process_changed',{'before':None,'after':action},app)
   return self.json(200,{'success':True,'app_id':app,'action':action})
  m=re.fullmatch(r'/local/v1/apps/([^/]+)/notification',p)
  if method=='POST' and m:
   app=m.group(1); body=self.body() or {}
   if app not in NOTIFICATION_APPS:return self.err(404,'NOTIFICATION_SOURCE_UNSUPPORTED')
   required={'notification_id','channel','title','body'}
   if set(body) - required or set(body) != required or any(not isinstance(body[key],str) or not body[key] for key in required):
    return self.err(400,'INVALID_NOTIFICATION')
   if any(len(body[key]) > limit for key,limit in (('notification_id',96),('channel',64),('title',120),('body',2000))):
    return self.err(400,'INVALID_NOTIFICATION')
   if r.lifecycle not in ('READY','RUNNING'):return self.err(409,'SESSION_NOT_READY')
   package=PACKAGES[app]
   if r.adb('shell','pm','grant',package,'android.permission.POST_NOTIFICATIONS') is None:
    return self.err(503,'DEVICE_NOT_AVAILABLE')
   if r.adb('shell','am','broadcast','-n',f'{package}/.NotificationReceiver','-a',f'{package}.PUBLISH_NOTIFICATION',
            '--es','notification_id',body['notification_id'],'--es','channel',body['channel'],
            '--es','title',body['title'],'--es','body',body['body']) is None:
    return self.err(503,'DEVICE_NOT_AVAILABLE')
   r.store._emit_packet(app,'notification','runner_notification',body,text_projection=f"{body['title']}：{body['body']}")
   return self.json(201,{'success':True,'app_id':app,'package':package,'notification_id':body['notification_id'],'channel':body['channel']})
  m=re.fullmatch(r'/runtime/v1/apps/([^/]+)/(actions|telemetry)',p)
  if method=='POST' and m:
   app,kind=m.groups(); body=self.body()
   if app not in APPS:return self.err(404,'APP_NOT_FOUND')
   if r.lifecycle not in ('READY','RUNNING'):return self.err(409,'SESSION_NOT_READY')
   if body is None:return self.err(400,'INVALID_JSON')
   try:return self.json(200,r.store.action(app,body) if kind=='actions' else r.store.telemetry(app,body))
   except Conflict:return self.err(409,'REVISION_CONFLICT')
   except BusinessError as e:return self.err(e.status,e.code)
   except ValueError as e:return self.err(400,str(e))
  return self.err(404,'NOT_FOUND')
 def err(self,status,code):return self.json(status,{'success':False,'error':{'code':code}})
 def adapter_json(self,operation,result):
  r=self.server.runtime
  match=re.match(r'/local/v1/apps/([^/]+)/',urlparse(self.path).path)
  source=match.group(1) if match else 'local-controller';version=result.get('record_version')
  if isinstance(version,int):version=f'{source}.v{version}'
  return self.json(200,{'status':result.get('status','ok'),'source':source,'operation':operation,'semantic_ref':result.get('semantic_ref'),'record_version':version,'observed_at':r.args.sim_time,'coverage':result.get('coverage'),'data':result.get('data',result)})
 def adapter_err(self,status,code,operation,message=None):
  spec=ERROR_CODES.get(code,{'retryable':False})
  match=re.match(r'/local/v1/apps/([^/]+)/',urlparse(self.path).path)
  return self.json(status,{'status':{'not_found':'not_found','permission_denied':'permission_denied','stale':'stale','timeout':'timeout','backend_error':'backend_error','feature_off':'feature_off'}.get(code) or ('not_found' if status==404 else ('stale' if status==409 else 'backend_error')),'source':match.group(1) if match else 'local-controller','operation':operation,'semantic_ref':None,'record_version':None,'observed_at':self.server.runtime.args.sim_time,'coverage':None,'data':None,'error':{'code':code,'message':message or code,'retryable':spec.get('retryable',False)}})

def main():
 p=argparse.ArgumentParser();p.add_argument('--instance-id',required=True);p.add_argument('--runtime',required=True);p.add_argument('--port',type=int,default=0);p.add_argument('--adb-serial',default='');p.add_argument('--avd',default='');p.add_argument('--snapshot',default='init_state');p.add_argument('--ku-container',default='');p.add_argument('--ku-image',default='');p.add_argument('--ku-image-id',default='');p.add_argument('--baseline-version',default='unknown');p.add_argument('--seed',type=int,default=20260920);p.add_argument('--sim-time',default='2026-09-20T09:00:00+08:00');a=p.parse_args()
 server=ThreadingHTTPServer(('127.0.0.1',a.port),API);a.port=server.server_port;server.runtime=Runtime(a);print(json.dumps({'port':a.port,'pid':os.getpid()}),flush=True)
 try:server.serve_forever()
 except KeyboardInterrupt:pass
 finally:server.runtime.store.close();server.server_close()
if __name__=='__main__':main()
