import concurrent.futures, hashlib, json, os, sqlite3, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request, uuid, zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
APPS={'dudu':'places','lianqi':'routes','starrail':'trains','babaquna':'hotels','wangyiyun':'songs','chaowei':'articles'}
ALL_RESOURCES={'dudu':['places','vehicle_quotes'],'lianqi':['routes','coupons'],'starrail':['stations','trains','seats'],'babaquna':['flights','hotels','attractions'],'wangyiyun':['songs','playlists'],'chaowei':['articles','tags']}

class ControllerCase(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory(); runtime=Path(cls.tmp.name)/'instance'
  cls.proc=subprocess.Popen([sys.executable,str(ROOT/'local-controller/server.py'),'--instance-id','test-1','--runtime',str(runtime),'--port','0','--seed','42'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  line=cls.proc.stdout.readline(); info=json.loads(line); cls.base=f"http://127.0.0.1:{info['port']}";cls.runtime=runtime
  for _ in range(30):
   try:cls.call('/health');break
   except Exception:time.sleep(.1)
 @classmethod
 def tearDownClass(cls):
  cls.proc.terminate();cls.proc.wait(timeout=10);cls.tmp.cleanup()
 @classmethod
 def call(cls,path,method='GET',data=None,headers=None,raw=False):
  body=json.dumps(data).encode() if data is not None else None
  headers={'Content-Type':'application/json',**(headers or {})}
  if method=='POST' and path.startswith('/local/v1/') and not path.endswith('/query'):
   headers.setdefault('X-Runner-Key',json.loads((cls.runtime/'instance.json').read_text())['runner_key'])
  req=urllib.request.Request(cls.base+path,data=body,method=method,headers=headers)
  try:
   with urllib.request.urlopen(req,timeout=10) as r:
    return (r.status,r.headers,r.read()) if raw else json.loads(r.read())
  except urllib.error.HTTPError as e:
   if raw:return e.code,e.headers,e.read()
   payload=json.loads(e.read());payload['_status']=e.code;return payload
 def test_01_health_and_instance_layout(self):
  self.assertEqual(self.call('/health')['status'],'ok')
  for name in ('instance.json','state.sqlite','events.jsonl','datasets','logs','screenshots','exports'):self.assertTrue((self.runtime/name).exists(),name)
  info=json.loads((self.runtime/'instance.json').read_text());self.assertEqual(info['seed'],42);self.assertEqual(info['lifecycle'],'READY')
  self.assertEqual(set(info['apk_versions']),set(APPS));self.assertEqual((self.runtime/'datasets/catalog.sqlite').stat().st_mode & 0o222,0)
 def test_02_all_apps_bootstrap_etag_and_pagination(self):
  for app,res in APPS.items():
   status,headers,raw=self.call(f'/runtime/v1/apps/{app}/bootstrap',raw=True);self.assertEqual(status,200);self.assertTrue(headers['ETag'])
   bootstrap=json.loads(raw);self.assertEqual(bootstrap['dataset']['checksum'],'sha256:'+hashlib.sha256((self.runtime/'datasets/catalog.sqlite').read_bytes()).hexdigest())
   status,_,_=self.call(f'/runtime/v1/apps/{app}/bootstrap',headers={'If-None-Match':headers['ETag']},raw=True);self.assertEqual(status,304)
   page=self.call(f'/runtime/v1/apps/{app}/data/{res}?offset=9990&limit=20');self.assertEqual(len(page['items']),10);self.assertFalse(page['page']['has_more'])
   first=self.call(f'/runtime/v1/apps/{app}/data/{res}?limit=1')['items'][0];self.assertEqual(first['id'],f'{app}-{res}-00000');self.assertIsInstance(first['price'],int);self.assertIsInstance(first['available'],int)
   self.assertEqual(set(first),{'id','app_id','resource','title','subtitle','meta','price','available','rank','body','topic_id','track_ids'})
   self.assertEqual(bootstrap['dataset']['data_origin'],'synthetic_research_fixture')
 def test_03_idempotency_revision_and_event_separation(self):
  aid=str(uuid.uuid4());body={'action_id':aid,'type':'create_order','payload':{'item_id':'dudu-places-00001','expected_price':42},'expected_revision':0}
  first=self.call('/runtime/v1/apps/dudu/actions','POST',body);second=self.call('/runtime/v1/apps/dudu/actions','POST',body)
  self.assertEqual(first,second);self.assertEqual(first['revision'],1);self.assertEqual(len(first['state']['orders']),1);self.assertEqual(first['state']['orders'][0]['price'],42)
  conflict=self.call('/runtime/v1/apps/dudu/actions','POST',{'action_id':str(uuid.uuid4()),'type':'pay','payload':{},'expected_revision':0});self.assertEqual(conflict['_status'],409);self.assertEqual(conflict['error']['code'],'REVISION_CONFLICT')
  self.call('/runtime/v1/apps/dudu/telemetry','POST',{'type':'page_view','payload':{'page':'home'}})
  events=self.call('/local/v1/apps/dudu/events')['events'];self.assertEqual({x['event_kind'] for x in events},{'domain','telemetry'})
  sequences=[x['sequence_no'] for x in events];self.assertEqual(sequences,sorted(sequences,reverse=True))
  self.assertEqual(self.call('/local/v1/apps/dudu/state')['revision'],1)
 def test_04_concurrent_optimistic_lock(self):
  self.call('/local/v1/instance/reset','POST',{})
  def write(_):return self.call('/runtime/v1/apps/lianqi/actions','POST',{'action_id':str(uuid.uuid4()),'type':'create_order','payload':{},'expected_revision':0})
  with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(write,range(8)))
  self.assertEqual(sum(x.get('success') is True for x in results),1);self.assertEqual(sum(x.get('error',{}).get('code')=='REVISION_CONFLICT' for x in results),7)
 def test_05_fault_is_deterministic_and_consumed(self):
  self.call('/local/v1/faults','POST',{'app_id':'starrail','path_pattern':'/data/','kind':'pagination_failure','remaining':1,'status':503})
  failed=self.call('/runtime/v1/apps/starrail/data/trains?limit=2');self.assertEqual(failed['error']['code'],'SIMULATED_NETWORK_FAILURE')
  self.assertEqual(len(self.call('/runtime/v1/apps/starrail/data/trains?limit=2')['items']),2)
 def test_06_reset_clears_authority_and_export_works(self):
  db_path=self.runtime/'state.sqlite';before=sqlite3.connect(db_path).execute("SELECT value FROM metadata WHERE key='database_id'").fetchone()[0]
  result=self.call('/local/v1/instance/reset','POST',{});self.assertEqual(result['status'],'READY')
  after=sqlite3.connect(db_path).execute("SELECT value FROM metadata WHERE key='database_id'").fetchone()[0];self.assertNotEqual(before,after)
  for app in APPS:
   state=self.call(f'/local/v1/apps/{app}/state');self.assertEqual(state['revision'],0);self.assertFalse(state['state']['orders'])
  key=json.loads((self.runtime/'instance.json').read_text())['runner_key']
  exported=self.call('/local/v1/instance/export','POST',{},headers={'X-Runner-Key':key});self.assertTrue(Path(exported['path']).exists());self.assertEqual(len(exported['sha256']),64)
  with zipfile.ZipFile(exported['path']) as archive:self.assertIn('datasets/catalog.manifest.json',archive.namelist())
 def test_07_repeated_resets(self):
  count=50 if os.getenv('FULL_RESET_TEST')=='1' else 10
  for _ in range(count):self.assertEqual(self.call('/local/v1/instance/reset','POST',{})['status'],'READY')
  self.assertEqual(self.call('/health')['lifecycle'],'READY')
 def test_08_clock_and_business_errors(self):
  stamp='2027-01-02T03:04:05+08:00';self.assertEqual(self.call('/local/v1/clock','POST',{'simulated_time':stamp})['simulated_time'],stamp)
  body={'action_id':str(uuid.uuid4()),'type':'create_order','expected_revision':0,'payload':{'item_id':'x','simulate':'price_changed'}}
  result=self.call('/runtime/v1/apps/babaquna/actions','POST',body);self.assertEqual(result['_status'],409);self.assertEqual(result['error']['code'],'PRICE_CHANGED')
 def test_09_cors_device_fallback_and_p95(self):
  status,headers,_=self.call('/health',raw=True);self.assertEqual(status,200);self.assertEqual(headers['Access-Control-Allow-Origin'],'*')
  self.assertFalse(self.call('/local/v1/device/status')['available'])
  samples=[]
  for _ in range(50):
   begin=time.perf_counter();self.call('/runtime/v1/apps/chaowei/data/articles?limit=50');samples.append((time.perf_counter()-begin)*1000)
  samples.sort();self.assertLess(samples[int(len(samples)*.95)-1],500)
 def test_10_reset_rejects_writes_and_restores_clock(self):
  self.call('/local/v1/clock','POST',{'simulated_time':'2030-01-01T00:00:00+08:00'})
  self.call('/local/v1/instance/lifecycle','POST',{'state':'RESETTING'})
  blocked=self.call('/runtime/v1/apps/dudu/actions','POST',{'action_id':str(uuid.uuid4()),'type':'call_car','payload':{},'expected_revision':0})
  self.assertEqual(blocked['_status'],409);self.assertEqual(blocked['error']['code'],'SESSION_NOT_READY')
  self.call('/local/v1/faults','POST',{'app_id':'*','path_pattern':'*','kind':'offline','remaining':-1})
  (self.runtime/'screenshots/stale.png').write_bytes(b'stale')
  self.assertEqual(self.call('/local/v1/instance/reset','POST',{})['status'],'READY')
  self.assertEqual(self.call('/local/v1/clock')['simulated_time'],'2026-09-20T09:00:00+08:00')
  self.assertEqual(self.call('/local/v1/faults')['faults'],[])
  self.assertFalse((self.runtime/'screenshots/stale.png').exists());self.assertFalse((self.runtime/'exports/test-1.zip').exists())
  debug_events=self.call('/local/v1/events')['events'];self.assertEqual(len(debug_events),1);self.assertEqual(debug_events[0]['event_kind'],'debug');self.assertEqual(debug_events[0]['event_type'],'instance_reset')
 def test_11_action_contract_and_idempotency_reuse(self):
  self.call('/local/v1/instance/reset','POST',{})
  missing=self.call('/runtime/v1/apps/dudu/actions','POST',{'action_id':str(uuid.uuid4()),'type':'call_car','payload':{}})
  self.assertEqual(missing['_status'],400);self.assertEqual(missing['error']['code'],'EXPECTED_REVISION_REQUIRED')
  empty=self.call('/runtime/v1/apps/dudu/actions','POST',{'action_id':str(uuid.uuid4()),'type':'pay','payload':{},'expected_revision':0})
  self.assertEqual(empty['_status'],404);self.assertEqual(empty['error']['code'],'ORDER_NOT_FOUND')
  aid=str(uuid.uuid4());first=self.call('/runtime/v1/apps/chaowei/actions','POST',{'action_id':aid,'type':'favorite','payload':{'item_id':'article-1'},'expected_revision':0});self.assertTrue(first['success'])
  reused=self.call('/runtime/v1/apps/chaowei/actions','POST',{'action_id':aid,'type':'favorite','payload':{'item_id':'article-2'},'expected_revision':0});self.assertEqual(reused['error']['code'],'IDEMPOTENCY_KEY_REUSED')
  duplicate=self.call('/runtime/v1/apps/chaowei/actions','POST',{'action_id':str(uuid.uuid4()),'type':'favorite','payload':{'item_id':'article-1'},'expected_revision':1});self.assertEqual(duplicate['error']['code'],'ALREADY_FAVORITED')
 def test_12_validation_summary_and_opt_in_popup(self):
  self.call('/local/v1/instance/reset','POST',{})
  popup=self.call('/runtime/v1/apps/starrail/state')['state']['popup_queue'];self.assertEqual(popup,[])
  self.assertEqual(self.call('/runtime/v1/apps/nope/state')['error']['code'],'APP_NOT_FOUND')
  self.assertEqual(self.call('/runtime/v1/apps/dudu/data/places?limit=nope')['error']['code'],'INVALID_PAGINATION')
  summary=self.call('/local/v1/sqlite/summary');self.assertEqual(summary['tables']['app_state'],6);self.assertTrue(summary['database_id'])
 def test_13_all_resources_search_and_scenario_catalog(self):
  self.call('/local/v1/instance/reset','POST',{})
  expected={'permission','promotion','inventory','price','payment','cancel_loss','time_conflict','duplicate','membership','network'}
  for app,resources in ALL_RESOURCES.items():
   bootstrap=self.call(f'/runtime/v1/apps/{app}/bootstrap')
   self.assertEqual({x['id'] for x in bootstrap['scenarios']},expected)
   self.assertEqual(bootstrap['popup_script_ref'],'deterministic-v1')
   for resource in resources:
    page=self.call(f'/runtime/v1/apps/{app}/data/{resource}?q=1&limit=3')
    self.assertTrue(page['items']);self.assertTrue(all(item['resource']==resource for item in page['items']))
 def test_14_configured_popup_and_business_scenarios(self):
  self.call('/local/v1/instance/reset','POST',{})
  triggered=self.call('/runtime/v1/apps/wangyiyun/actions','POST',{'action_id':str(uuid.uuid4()),'type':'trigger_popup','payload':{'scenario_id':'membership','kind':'membership','title':'会员专享内容','message':'本地会员态'},'expected_revision':0})
  self.assertEqual(triggered['state']['popup_queue'][0]['kind'],'membership')
  duplicate=self.call('/runtime/v1/apps/chaowei/actions','POST',{'action_id':str(uuid.uuid4()),'type':'favorite','payload':{'item_id':'article-1','simulate':'already_favorited'},'expected_revision':0})
  self.assertEqual(duplicate['_status'],409);self.assertEqual(duplicate['error']['code'],'ALREADY_FAVORITED')
 def test_15_instance_data_injection_keeps_read_contract(self):
  self.call('/local/v1/instance/reset','POST',{})
  item={'id':'dudu-places-00000','title':'世纪公园地铁站','subtitle':'浦东新区锦绣路 · 2号口','meta':'约 3 分钟上车','price':23,'available':8,'rank':0}
  injected=self.call('/local/v1/apps/dudu/data/places','POST',{'items':[item]});self.assertTrue(injected['success']);self.assertEqual(injected['count'],1)
  first=self.call('/runtime/v1/apps/dudu/data/places?limit=1')['items'][0]
  self.assertEqual(first['title'],'世纪公园地铁站');self.assertEqual(set(first),{'id','app_id','resource','title','subtitle','meta','price','available','rank','body','topic_id','track_ids'})
  invalid=self.call('/local/v1/apps/dudu/data/places','POST',{'items':[{'title':'bad','price':-1}]});self.assertEqual(invalid['_status'],400)
  self.assertEqual(self.call('/local/v1/sqlite/summary')['tables']['injected_records'],1)
  self.call('/local/v1/instance/reset','POST',{});self.assertEqual(self.call('/runtime/v1/apps/dudu/data/places?limit=1')['items'][0]['title'],'人民广场')

class RestartCase(unittest.TestCase):
 def test_controller_restart_preserves_authoritative_state(self):
  with tempfile.TemporaryDirectory() as temp:
   runtime=Path(temp)/'persistent'
   def launch():
    proc=subprocess.Popen([sys.executable,str(ROOT/'local-controller/server.py'),'--instance-id','restart','--runtime',str(runtime),'--port','0'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    info=json.loads(proc.stdout.readline());return proc,f"http://127.0.0.1:{info['port']}"
   def call(base,path,method='GET',data=None):
    req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,method=method,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=10) as response:return json.loads(response.read())
   first,base=launch();first_key=json.loads((runtime/'instance.json').read_text())['runner_key'];created=call(base,'/runtime/v1/apps/chaowei/actions','POST',{'action_id':str(uuid.uuid4()),'type':'note','payload':{'item_id':'article-1','text':'offline'},'expected_revision':0});self.assertEqual(created['revision'],1);first.terminate();first.wait(timeout=10);first.stdout.close();first.stderr.close()
   second,base=launch()
   try:
    state=call(base,'/runtime/v1/apps/chaowei/state');self.assertEqual(state['revision'],1);self.assertEqual(state['state']['notes'][0]['text'],'offline');self.assertEqual(json.loads((runtime/'instance.json').read_text())['runner_key'],first_key)
   finally:second.terminate();second.wait(timeout=10);second.stdout.close();second.stderr.close()

class SupervisorCase(unittest.TestCase):
 def test_supervisor_restarts_controller_on_same_port(self):
  with tempfile.TemporaryDirectory() as temp:
   runtime=Path(temp)/'supervised';runtime.mkdir()
   import socket
   with socket.socket() as sock:
    sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
   supervisor=subprocess.Popen(['bash',str(ROOT/'scripts/controller-supervisor.sh'),'supervised',
                                '--runtime',str(runtime),'--port',str(port)],stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
   base=f'http://127.0.0.1:{port}'
   try:
    for _ in range(50):
     try: urllib.request.urlopen(base+'/health',timeout=1).close();break
     except Exception: time.sleep(.1)
    else:self.fail('supervisor did not start controller')
    children=subprocess.check_output(['pgrep','-P',str(supervisor.pid)],text=True).split()
    self.assertTrue(children)
    os.kill(int(children[0]),9)
    for _ in range(50):
     try: urllib.request.urlopen(base+'/health',timeout=1).close();break
     except Exception: time.sleep(.1)
    else:self.fail('supervisor did not restart controller')
   finally:
    (runtime/'controller.stop').write_text('stop\n')
    supervisor.terminate();supervisor.wait(timeout=10)

 def test_tampered_dataset_marks_instance_contaminated(self):
  with tempfile.TemporaryDirectory() as temp:
   runtime=Path(temp)/'bad';(runtime/'datasets').mkdir(parents=True);(runtime/'datasets/catalog.sqlite').write_bytes(b'tampered')
   proc=subprocess.Popen([sys.executable,str(ROOT/'local-controller/server.py'),'--instance-id','bad','--runtime',str(runtime),'--port','0'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
   info=json.loads(proc.stdout.readline());base=f"http://127.0.0.1:{info['port']}"
   try:
    with urllib.request.urlopen(base+'/health',timeout=10) as response:health=json.loads(response.read())
    self.assertEqual(health['lifecycle'],'CONTAMINATED')
    with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(base+'/runtime/v1/apps/dudu/bootstrap',timeout=10)
    self.assertEqual(caught.exception.code,409);caught.exception.close()
   finally:proc.terminate();proc.wait(timeout=10);proc.stdout.close();proc.stderr.close()

 def test_versioned_dataset_upgrade_is_explicit_and_tamper_survives_reset(self):
  with tempfile.TemporaryDirectory() as temp:
   runtime=Path(temp)/'upgrade';(runtime/'datasets').mkdir(parents=True)
   (runtime/'datasets/catalog.sqlite').write_bytes(b'old-version')
   (runtime/'datasets/catalog.manifest.json').write_text(json.dumps({'dataset_version':'old'}))
   proc=subprocess.Popen([sys.executable,str(ROOT/'local-controller/server.py'),'--instance-id','upgrade','--runtime',str(runtime),'--port','0'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
   info=json.loads(proc.stdout.readline());base=f"http://127.0.0.1:{info['port']}"
   try:
    with urllib.request.urlopen(base+'/health',timeout=10) as response:health=json.loads(response.read())
    self.assertEqual(health['lifecycle'],'READY')
    manifest=json.loads((runtime/'datasets/catalog.manifest.json').read_text());self.assertEqual(manifest['dataset_version'],'2026.09.10')
    target=runtime/'datasets/catalog.sqlite';target.chmod(0o644);target.write_bytes(b'tampered-after-upgrade')
    key=json.loads((runtime/'instance.json').read_text())['runner_key']
    req=urllib.request.Request(base+'/local/v1/instance/reset',data=b'{}',method='POST',headers={'Content-Type':'application/json','X-Runner-Key':key})
    with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(req,timeout=10)
    self.assertEqual(caught.exception.code,500);caught.exception.close()
    with urllib.request.urlopen(base+'/health',timeout=10) as response:health=json.loads(response.read())
    self.assertEqual(health['lifecycle'],'CONTAMINATED')
   finally:proc.terminate();proc.wait(timeout=10);proc.stdout.close();proc.stderr.close()

if __name__=='__main__':unittest.main()
