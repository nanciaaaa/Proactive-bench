import hashlib, json, re, sqlite3, threading, uuid
from datetime import datetime, timezone
from pathlib import Path
from datasets import RESOURCES

APPS=('dudu','lianqi','starrail','babaquna','wangyiyun','chaowei')

class Conflict(Exception): pass
class BusinessError(Exception):
 def __init__(self,code,status=409):self.code,self.status=code,status
class Store:
 def __init__(self,path,instance_id,seed,sim_time,event_file):
  self.path,self.instance_id,self.seed_value,self.sim_time,self.event_file=path,instance_id,seed,sim_time,event_file
  self.frozen=False
  self.event_file.touch(exist_ok=True)
  self.lock=threading.RLock(); self.db=sqlite3.connect(path,check_same_thread=False,isolation_level=None)
  self.db.row_factory=sqlite3.Row; self.db.execute('PRAGMA journal_mode=WAL'); self.db.execute('PRAGMA foreign_keys=ON')
  self.db.executescript('''CREATE TABLE IF NOT EXISTS app_state(app_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,state_json TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS actions(action_id TEXT PRIMARY KEY,app_id TEXT NOT NULL,request_hash TEXT,response_json TEXT NOT NULL,episode_scope TEXT);
  CREATE TABLE IF NOT EXISTS events(sequence_no INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE,app_id TEXT,event_kind TEXT,event_type TEXT,correlation_id TEXT,causation_id TEXT,simulated_at TEXT,written_at TEXT,source TEXT,payload_json TEXT);
  CREATE TABLE IF NOT EXISTS faults(id INTEGER PRIMARY KEY AUTOINCREMENT,app_id TEXT,path_pattern TEXT,kind TEXT,status INTEGER,delay_ms INTEGER,remaining INTEGER,enabled INTEGER DEFAULT 1);
  CREATE TABLE IF NOT EXISTS inventory(app_id TEXT,item_id TEXT,available INTEGER,price INTEGER,revision INTEGER,PRIMARY KEY(app_id,item_id));
  CREATE TABLE IF NOT EXISTS injected_records(app_id TEXT,resource TEXT,id TEXT,title TEXT,subtitle TEXT,meta TEXT,price INTEGER,available INTEGER,rank INTEGER,PRIMARY KEY(app_id,resource,id));
  CREATE TABLE IF NOT EXISTS typed_entities(app_id TEXT,entity_type TEXT,semantic_ref TEXT,episode_scope TEXT,record_version INTEGER,updated_at TEXT,authority TEXT,data_json TEXT,PRIMARY KEY(app_id,entity_type,semantic_ref));
  CREATE TABLE IF NOT EXISTS seed_receipts(idempotency_key TEXT PRIMARY KEY,app_id TEXT,episode_scope TEXT,applied_at TEXT,readback_hash TEXT,receipt_json TEXT,request_hash TEXT);
  CREATE TABLE IF NOT EXISTS episode_checkpoints(app_id TEXT,episode_scope TEXT,state_json TEXT,inventory_json TEXT,receipt_cursor INTEGER,PRIMARY KEY(app_id,episode_scope));
  CREATE TABLE IF NOT EXISTS episode_injected_backups(app_id TEXT,episode_scope TEXT,resource TEXT,id TEXT,previous_json TEXT,PRIMARY KEY(app_id,episode_scope,resource,id));
  CREATE TABLE IF NOT EXISTS mutations(mutation_id TEXT PRIMARY KEY,episode_scope TEXT,actor TEXT,app_id TEXT,semantic_ref TEXT,operation TEXT,at TEXT,before_version INTEGER,after_version INTEGER,changed_fields TEXT,status TEXT,trace_ref TEXT);
  CREATE TABLE IF NOT EXISTS passive_packets(sequence INTEGER PRIMARY KEY AUTOINCREMENT,packet_id TEXT UNIQUE,app_id TEXT,type TEXT,source TEXT,observed_at TEXT,delivered_at TEXT,text_projection TEXT,screenshot_ref TEXT,payload_json TEXT);
  CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);''')
  if 'request_hash' not in {r[1] for r in self.db.execute('PRAGMA table_info(actions)')}:
   self.db.execute('ALTER TABLE actions ADD COLUMN request_hash TEXT')
  if 'episode_scope' not in {r[1] for r in self.db.execute('PRAGMA table_info(actions)')}:
   self.db.execute('ALTER TABLE actions ADD COLUMN episode_scope TEXT')
  if 'app_id' not in {r[1] for r in self.db.execute('PRAGMA table_info(seed_receipts)')}:
   self.db.execute('ALTER TABLE seed_receipts ADD COLUMN app_id TEXT')
  if 'request_hash' not in {r[1] for r in self.db.execute('PRAGMA table_info(seed_receipts)')}:
   self.db.execute('ALTER TABLE seed_receipts ADD COLUMN request_hash TEXT')
  if 'body' not in {r[1] for r in self.db.execute('PRAGMA table_info(injected_records)')}:
   self.db.execute('ALTER TABLE injected_records ADD COLUMN body TEXT')
  for field in ('topic_id','track_ids'):
   if field not in {r[1] for r in self.db.execute('PRAGMA table_info(injected_records)')}:
    self.db.execute(f'ALTER TABLE injected_records ADD COLUMN {field} TEXT')
  self.db.execute('INSERT OR IGNORE INTO metadata VALUES(?,?)',('database_id',str(uuid.uuid4())))
  for app in APPS:self.db.execute('INSERT OR IGNORE INTO app_state VALUES(?,0,?)',(app,json.dumps(self._initial(app),ensure_ascii=False)))
 def _initial(self,app):
  base={'orders':[],'history':[],'favorites':[],'popup_queue':[],'account_fields':{}}
  if app=='wangyiyun':base.update(playlists=[],liked=[],play_history=[],play_queue=[],playing=False)
  if app=='chaowei':base.update(notes=[],knowledge_history=[])
  if app in ('starrail','babaquna'):base.update(passengers=[])
  if app=='lianqi':base.update(coupons=[],navigation=None)
  return base
 def state(self,app):
  with self.lock:
   row=self.db.execute('SELECT * FROM app_state WHERE app_id=?',(app,)).fetchone()
   if row is None:raise ValueError('APP_NOT_FOUND')
   return row['revision'],json.loads(row['state_json'])
 def inject_records(self,app,resource,items):
  if self.frozen:raise BusinessError('SESSION_FROZEN',409)
  if not isinstance(items,list) or not items or len(items)>1000:raise ValueError('INVALID_INJECTION')
  rows=[]
  for index,item in enumerate(items):
   if not isinstance(item,dict) or not isinstance(item.get('title'),str) or not item['title'].strip():raise ValueError('INVALID_INJECTION')
   try:price=int(item.get('price',0));available=int(item.get('available',0));rank=int(item.get('rank',index))
   except (TypeError,ValueError):raise ValueError('INVALID_INJECTION')
   if price<0 or available<0:raise ValueError('INVALID_INJECTION')
   record_id=str(item.get('id') or f'{app}-{resource}-injected-{index:05d}')[:160]
   title=item['title'].strip()[:160];subtitle=str(item.get('subtitle','')).strip()[:300];meta=str(item.get('meta','')).strip()[:160]
   rows.append((app,resource,record_id,title,subtitle,meta,price,available,rank,str(item.get('body',''))[:4000],str(item.get('topic_id',''))[:160],json.dumps(item.get('track_ids',[]),ensure_ascii=False)))
  with self.lock:
   self.db.executemany('INSERT OR REPLACE INTO injected_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',rows)
  return [dict(zip(('app_id','resource','id','title','subtitle','meta','price','available','rank','body','topic_id','track_ids'),row)) for row in rows]
 def injected_records(self,app,resource,search=''):
  with self.lock:
   rows=self.db.execute('SELECT * FROM injected_records WHERE app_id=? AND resource=? ORDER BY rank,id',(app,resource)).fetchall()
  needle=search.casefold().strip()
  return [dict(row) for row in rows if not needle or needle in f"{row['title']} {row['subtitle']}".casefold()]
 def visible_records(self,app,resource,search=''):
  with self.lock:
   injected=self.injected_records(app,resource)
   catalog=Path(self.path).parent/'datasets/catalog.sqlite'
   baseline=[]
   if catalog.exists():
    with sqlite3.connect(f'file:{catalog}?mode=ro',uri=True) as db:
     db.row_factory=sqlite3.Row
     baseline=[dict(row) for row in db.execute('SELECT * FROM records WHERE app_id=? AND resource=? ORDER BY rank,id',(app,resource))]
   hidden={row['id'] for row in injected}
   records=injected+[row for row in baseline if row['id'] not in hidden]
   needle=search.casefold().strip()
   return [row for row in records if not needle or needle in f"{row['title']} {row['subtitle']}".casefold()]
 def _remember_injected(self,app,episode,resource,ref):
  other=self.db.execute('SELECT 1 FROM episode_injected_backups WHERE app_id=? AND resource=? AND id=? AND episode_scope!=?',(app,resource,ref,episode)).fetchone()
  if other:raise BusinessError('PATCH_TARGET_EXISTS',409)
  old=self.db.execute('SELECT * FROM injected_records WHERE app_id=? AND resource=? AND id=?',(app,resource,ref)).fetchone()
  self.db.execute('INSERT OR IGNORE INTO episode_injected_backups VALUES(?,?,?,?,?)',(app,episode,resource,ref,json.dumps(dict(old),ensure_ascii=False) if old else None))
 def _item_record(self,app,item):
  row=self.db.execute('SELECT * FROM injected_records WHERE app_id=? AND id=?',(app,item)).fetchone()
  if row:return dict(row)
  catalog=Path(self.path).parent/'datasets/catalog.sqlite'
  if catalog.exists():
   with sqlite3.connect(f'file:{catalog}?mode=ro',uri=True) as db:
    db.row_factory=sqlite3.Row
    row=db.execute('SELECT * FROM records WHERE app_id=? AND id=?',(app,item)).fetchone()
    if row:return dict(row)
  return None
 def action(self,app,body):
  action_id=body.get('action_id'); typ=body.get('type'); payload=body.get('payload') or {}; expected=body.get('expected_revision')
  if self.frozen:raise BusinessError('SESSION_FROZEN',409)
  if not action_id or not typ or not isinstance(body.get('payload'),dict): raise ValueError('INVALID_ACTION')
  if expected is None:raise ValueError('EXPECTED_REVISION_REQUIRED')
  request_hash=uuid.uuid5(uuid.NAMESPACE_URL,json.dumps({'app_id':app,'type':typ,'payload':payload,'expected_revision':expected},sort_keys=True,ensure_ascii=False)).hex
  with self.lock:
   self.db.execute('BEGIN IMMEDIATE')
   try:
    old=self.db.execute('SELECT app_id,request_hash,response_json FROM actions WHERE action_id=?',(action_id,)).fetchone()
    if old:
     if old['app_id']!=app or (old['request_hash'] and old['request_hash']!=request_hash):raise BusinessError('IDEMPOTENCY_KEY_REUSED')
     self.db.execute('COMMIT'); return json.loads(old['response_json'])
    revision,state=self.state(app)
    if expected is not None and expected!=revision: raise Conflict()
    before_state=json.loads(json.dumps(state))
    scope_row=self.db.execute('SELECT episode_scope FROM seed_receipts WHERE app_id=? ORDER BY rowid DESC LIMIT 1',(app,)).fetchone()
    episode=scope_row['episode_scope'] if scope_row else 'default'
    if scope_row:
     inventory=[dict(row) for row in self.db.execute('SELECT * FROM inventory WHERE app_id=?',(app,))]
     receipt_cursor=self.db.execute('SELECT COALESCE(MAX(rowid),0) FROM seed_receipts WHERE app_id=?',(app,)).fetchone()[0]
     self.db.execute('INSERT OR IGNORE INTO episode_checkpoints VALUES(?,?,?,?,?)',(app,episode,json.dumps(before_state,ensure_ascii=False),json.dumps(inventory,ensure_ascii=False),receipt_cursor))
    item=payload.get('item_id','sample'); message='操作成功'
    if payload.get('simulate') in ('price_changed','inventory_changed','time_conflict','already_favorited'):
     raise BusinessError({'price_changed':'PRICE_CHANGED','inventory_changed':'INVENTORY_CHANGED','time_conflict':'TIME_CONFLICT','already_favorited':'ALREADY_FAVORITED'}[payload['simulate']])
    if typ in ('primary','create_order','book','call_car'):
     inv=self.db.execute('SELECT * FROM inventory WHERE app_id=? AND item_id=?',(app,item)).fetchone()
     if not inv:
      record=self._item_record(app,item)
      if record is None:
       try:rank=int(item.rsplit('-',1)[-1])
       except ValueError:rank=sum(item.encode())
       price=(rank*17)%500+9;available=(rank*31)%90+1
      else:price,available=record['price'],record['available']
      self.db.execute('INSERT INTO inventory VALUES(?,?,?,?,0)',(app,item,available,price));inv=self.db.execute('SELECT * FROM inventory WHERE app_id=? AND item_id=?',(app,item)).fetchone()
     if inv['available']<=0:raise BusinessError('INVENTORY_CHANGED')
     if payload.get('expected_price') is not None and int(payload['expected_price'])!=inv['price']:raise BusinessError('PRICE_CHANGED')
     self.db.execute('UPDATE inventory SET available=available-1,revision=revision+1 WHERE app_id=? AND item_id=?',(app,item))
     record=self._item_record(app,item)
     context=payload.get('context','')
     if not isinstance(context,str) or len(context)>160:raise BusinessError('INVALID_ACTION',400)
     details={k:payload.get(k,'') for k in ('origin','destination','service_type','reservation_time','train','train_schedule','seat','traveler_name','traveler_phone')}
     if any(not isinstance(v,str) or len(v)>160 for v in details.values()):raise BusinessError('INVALID_ACTION',400)
     if details['traveler_phone'] and not re.fullmatch(r'1\d{10}',details['traveler_phone']):raise BusinessError('INVALID_TRAVELER',400)
     state['orders'].append({'id':f'{app}-order-{revision+1}','item_id':item,
                            'title':record['title'] if record else '行程服务',
                            'subtitle':' · '.join(x for x in (context,record['subtitle'] if record else '') if x),
                            'status':'CONFIRMED','base_price':inv['price'],'discount':0,
                            'price':inv['price'],'coupon_title':'','payments':[],
                            'created_at':self.sim_time,**details,
                            'vehicle_plate':f'沪A·{(self.seed_value+revision)%90000+10000}' if app in ('dudu','lianqi') else '',
                            'driver':'王师傅' if app in ('dudu','lianqi') else '',
                            'eta_minutes':8 if app in ('dudu','lianqi') else None}); message='订单已确认'
    elif typ in ('cancel','cancel_order'):
     if app=='dudu':raise BusinessError('INVALID_ACTION',400)
     if not state['orders']:raise BusinessError('ORDER_NOT_FOUND',404)
     order=next((o for o in reversed(state['orders']) if o['id']==payload.get('order_id')),None) if payload.get('order_id') else state['orders'][-1]
     if not order:raise BusinessError('ORDER_NOT_FOUND',404)
     if order['status']!='CONFIRMED' and not (app=='lianqi' and order['status']=='PAID'):raise BusinessError('INVALID_ACTION',400)
     if order['status']=='PAID':order.setdefault('payments',[]).append({'amount':-order['price'],'method':'预约取消退款','at':self.sim_time})
     order['status']='CANCELLED';self.db.execute('UPDATE inventory SET available=available+1,revision=revision+1 WHERE app_id=? AND item_id=?',(app,order['item_id']));message='订单已取消'
    elif typ in ('pay','payment'):
     if not state['orders']:raise BusinessError('ORDER_NOT_FOUND',404)
     order=next((o for o in reversed(state['orders']) if o['id']==payload.get('order_id')),None) if payload.get('order_id') else state['orders'][-1]
     if not order:raise BusinessError('ORDER_NOT_FOUND',404)
     if order['status']!='CONFIRMED':raise BusinessError('INVALID_ACTION',400)
     order['status']='PAID';order.setdefault('payments',[]).append({'amount':order['price'],'method':'账户余额','at':self.sim_time});message='支付成功'
    elif typ=='checkout_pay':
     order=next((o for o in reversed(state['orders']) if o['id']==payload.get('order_id')),None)
     if not order:raise BusinessError('ORDER_NOT_FOUND',404)
     method,password=payload.get('method'),payload.get('password')
     if order['status']!='CONFIRMED' or method not in ('微信支付','支付宝支付') or not isinstance(password,str) or not re.fullmatch(r'\d{6}',password):raise BusinessError('PAYMENT_INVALID',400)
     if app=='babaquna' and (not order.get('traveler_name') or not re.fullmatch(r'1\d{10}',order.get('traveler_phone',''))):raise BusinessError('INVALID_TRAVELER',400)
     if app=='starrail' and (not order.get('traveler_name') or not re.fullmatch(r'1\d{10}',order.get('traveler_phone','')) or not order.get('seat')):raise BusinessError('INVALID_TRAVELER',400)
     order['status']='PAID';order.setdefault('payments',[]).append({'amount':order['price'],'method':method,'at':self.sim_time});message='支付成功'
    elif typ=='favorite':
     if item in state['favorites']:raise BusinessError('ALREADY_FAVORITED')
     state['favorites'].append(item);message='已收藏'
    elif typ in ('toggle_favorite','like'):
     if item in state['favorites']:state['favorites'].remove(item); message='已取消收藏'
     else:state['favorites'].append(item); message='已收藏'
     if typ=='like':state['liked']=list(state['favorites'])
    elif typ=='note': state.setdefault('notes',[]).append({'item_id':item,'text':payload.get('text','')}); message='笔记已保存'
    elif typ=='play': state.setdefault('play_history',[]).append(item);state['playing']=True;message='开始播放'
    elif typ=='play_all':
     song_ids=payload.get('item_ids')
     if not isinstance(song_ids,list) or not 1<=len(song_ids)<=50 or any(not isinstance(x,str) or len(x)>160 for x in song_ids):raise BusinessError('INVALID_ACTION',400)
     if any((self._item_record(app,song) or {}).get('resource')!='songs' for song in song_ids):raise BusinessError('INVALID_ACTION',400)
     state['play_queue']=song_ids;state.setdefault('play_history',[]).append(song_ids[0]);state['playing']=True;message='开始播放歌单'
    elif typ in ('pause','resume'):
     if not state.get('play_history'):raise BusinessError('INVALID_ACTION',400)
     state['playing']=typ=='resume';message='继续播放' if state['playing'] else '已暂停'
    elif typ=='add_passenger':
     name,phone=payload.get('name','乘客'),payload.get('phone','')
     if not isinstance(name,str) or not name.strip() or len(name)>80 or not isinstance(phone,str) or (phone and not re.fullmatch(r'1\d{10}',phone)):raise BusinessError('INVALID_TRAVELER',400)
     state.setdefault('passengers',[]).append({'name':name.strip(),'phone':phone,'id':item});message='出行人已添加'
    elif typ=='add_playlist':state.setdefault('playlists',[]).append({'id':item,'name':payload.get('name','新歌单'),'songs':[]});message='歌单已创建'
    elif typ=='save_account_field':
     key,value=payload.get('key'),payload.get('value')
     if not isinstance(key,str) or not re.fullmatch(r'[a-z_]{1,40}',key) or not isinstance(value,str) or len(value)>160:raise BusinessError('INVALID_ACTION',400)
     state.setdefault('account_fields',{})[key]=value.strip();message='已保存'
    elif typ=='add_to_playlist':
     if (self._item_record(app,item) or {}).get('resource')!='songs':raise BusinessError('INVALID_ACTION',400)
     lists=state.setdefault('playlists',[])
     target=next((x for x in lists if x['id']==payload.get('playlist_id')),None)
     if not target:raise BusinessError('INVALID_ACTION',400)
     if item not in target['songs']:target['songs'].append(item)
     message='已加入歌单'
    elif typ=='use_coupon':
     if not state['orders']:raise BusinessError('ORDER_NOT_FOUND',404)
     order=next((o for o in reversed(state['orders']) if o['id']==payload.get('order_id')),None) if payload.get('order_id') else state['orders'][-1]
     if not order:raise BusinessError('ORDER_NOT_FOUND',404)
     if order['status']!='CONFIRMED':raise BusinessError('INVALID_ACTION',400)
     coupon=next((c for c in state.get('coupons',[]) if c.get('id')==item),None)
     if coupon is None:
      record=self._item_record(app,item)
      if record:coupon={'id':item,'code':item,'title':record['title'],'discount':record['subtitle']}
     if coupon is None:raise BusinessError('COUPON_NOT_APPLICABLE',409)
     rule=f"{coupon.get('discount','')} {coupon.get('title','')}"
     full=re.search(r'满\s*(\d+)\s*减\s*(\d+)',rule)
     simple=re.search(r'减\s*(\d+)',rule)
     percent=re.search(r'(\d+(?:\.\d+)?)\s*折',rule)
     minimum=int(full.group(1)) if full else 0
     base=order.get('base_price',order['price'])
     reduction=round(base*(1-float(percent.group(1))/10)) if percent else int((full or simple).group(2 if full else 1)) if full or simple else 0
     if percent and simple:reduction=min(reduction,int(simple.group(1)))
     if not reduction or base<minimum:raise BusinessError('COUPON_NOT_APPLICABLE',409)
     order['base_price']=base;order['discount']=min(reduction,base);order['price']=base-order['discount'];order['coupon_title']=coupon['title']
     if not any(c.get('id')==item for c in state.get('coupons',[])):state.setdefault('coupons',[]).append(coupon)
     message='优惠已抵扣'
    elif typ=='remove_coupon':
     order=next((o for o in reversed(state['orders']) if o['id']==payload.get('order_id')),None)
     if not order:raise BusinessError('ORDER_NOT_FOUND',404)
     if order['status']!='CONFIRMED':raise BusinessError('INVALID_ACTION',400)
     order['price']=order.get('base_price',order['price']);order['discount']=0;order['coupon_title']='';message='已选择不使用优惠券'
    elif typ=='navigate':state['navigation']={'destination':item,'eta_minutes':payload.get('eta_minutes',18)};message='导航已开始'
    elif typ=='set_traveler':
     order=next((o for o in state['orders'] if o['id']==payload.get('order_id')),None)
     if not order:raise BusinessError('ORDER_NOT_FOUND',404)
     name,phone=payload.get('name'),payload.get('phone')
     if order['status']!='CONFIRMED' or app not in ('starrail','babaquna') or not isinstance(name,str) or not name.strip() or not isinstance(phone,str) or not re.fullmatch(r'1\d{10}',phone):raise BusinessError('INVALID_TRAVELER',400)
     order['traveler_name']=name.strip();order['traveler_phone']=phone;message='出行人已确认'
    elif typ in ('refund','change_order'):
     order=next((o for o in state['orders'] if o['id']==payload.get('order_id')),None)
     if not order:raise BusinessError('ORDER_NOT_FOUND',404)
     if app!='starrail' or order['status']!='PAID':raise BusinessError('INVALID_ACTION',400)
     if typ=='refund':
      order['status']='REFUNDED';self.db.execute('UPDATE inventory SET available=available+1,revision=revision+1 WHERE app_id=? AND item_id=?',(app,order['item_id']));message='退票成功'
     else:
      new_item=payload.get('new_item_id');train=payload.get('train')
      if not isinstance(new_item,str) or not isinstance(train,str) or not train.strip():raise BusinessError('INVALID_ACTION',400)
      record=self._item_record(app,new_item)
      if not record or record['resource']!='seats':raise BusinessError('INVALID_ACTION',400)
      available=self.db.execute('SELECT available FROM inventory WHERE app_id=? AND item_id=?',(app,new_item)).fetchone()
      if available and available['available']<=0:raise BusinessError('INVENTORY_CHANGED')
      if available:self.db.execute('UPDATE inventory SET available=available-1,revision=revision+1 WHERE app_id=? AND item_id=?',(app,new_item))
      self.db.execute('UPDATE inventory SET available=available+1,revision=revision+1 WHERE app_id=? AND item_id=?',(app,order['item_id']))
      previous=order['base_price'];order.update(item_id=new_item,train=train.strip(),train_schedule=str(payload.get('train_schedule',''))[:160],seat=record['title'],base_price=record['price'],price=record['price'],discount=0,coupon_title='',fare_difference=record['price']-previous,status='CHANGED');message='改签成功'
    elif typ=='trigger_popup':
     state['popup_queue']=[{'id':payload.get('scenario_id',f'{app}-scenario'),'kind':payload.get('kind','notice'),'title':payload.get('title','服务通知'),'message':payload.get('message','你有一条新的服务提醒。')}];message='场景已触发'
    elif typ=='dismiss_popup':state['popup_queue']=[];message='弹窗已关闭'
    else: raise ValueError('UNKNOWN_ACTION')
    state['history'].append({'action':typ,'item_id':item,'at':self.sim_time})
    changed=sorted(k for k,v in state.items() if k!='history' and before_state.get(k)!=v)
    status='committed' if changed else 'noop';before_rev=revision
    if changed:revision+=1
    self.db.execute('UPDATE app_state SET revision=?,state_json=? WHERE app_id=?',(revision,json.dumps(state,ensure_ascii=False),app))
    event=self._event(app,'domain',typ,action_id,action_id,{'item_id':item,'revision':revision})
    response={'success':True,'revision':revision,'result':{'message':message,'event_id':event['event_id']},'state':state}
    self.db.execute('INSERT INTO actions(action_id,app_id,request_hash,response_json,episode_scope) VALUES(?,?,?,?,?)',(action_id,app,request_hash,json.dumps(response,ensure_ascii=False),episode))
    self._journal(app,'agent_gui',item,typ,before_rev,revision,changed,status,event['event_id'],episode)
    if typ=='trigger_popup':
     pop=state['popup_queue'][0] if state['popup_queue'] else {}
     self._emit_packet(app,'app_event','popup_script',{'kind':pop.get('kind'),'title':pop.get('title'),'message':pop.get('message')},text_projection=f"{pop.get('title','')}：{pop.get('message','')}")
    else:
     self._emit_packet(app,'user_action','gui',{'action_type':typ,'item_id':item,'revision':revision},text_projection=message)
    self.db.execute('COMMIT')
    self._append_event(event); return response
   except BusinessError as e:
    self.db.execute('ROLLBACK')
    self._journal(app,'agent_gui',payload.get('item_id',''),typ,expected if expected is not None else 0,expected if expected is not None else 0,[],'rejected',e.code,locals().get('episode','default'))
    raise
   except Exception:self.db.execute('ROLLBACK'); raise
 def telemetry(self,app,body):
  with self.lock:
   if self.frozen:raise BusinessError('SESSION_FROZEN',409)
   event=self._event(app,'telemetry',body.get('type','ui_event'),body.get('correlation_id'),None,body.get('payload',{})); self._append_event(event); return {'success':True,'event_id':event['event_id']}
 def record_debug(self,typ,payload,app='system'):
  with self.lock:
   event=self._event(app,'debug',typ,str(uuid.uuid4()),None,payload);self._append_event(event);return event
 def _event(self,app,kind,typ,corr,cause,payload):
  eid=str(uuid.uuid4()); now=datetime.now(timezone.utc).isoformat()
  source={'domain':'controller','telemetry':'app','debug':'debug-console'}[kind]
  cur=self.db.execute('INSERT INTO events(event_id,app_id,event_kind,event_type,correlation_id,causation_id,simulated_at,written_at,source,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?)',(eid,app,kind,typ,corr,cause,self.sim_time,now,source,json.dumps(payload,ensure_ascii=False)))
  return {'schema_version':'1.0','event_id':eid,'instance_id':self.instance_id,'app_id':app,'event_kind':kind,'event_type':typ,'sequence_no':cur.lastrowid,'correlation_id':corr,'causation_id':cause,'simulated_at':self.sim_time,'written_at':now,'source':source,'payload':payload}
 def _append_event(self,event):
  with self.event_file.open('a',encoding='utf8') as f:f.write(json.dumps(event,ensure_ascii=False)+'\n')
 def events(self,app=None,limit=100):
  with self.lock:
   rows=self.db.execute('SELECT * FROM events WHERE app_id=? ORDER BY sequence_no DESC LIMIT ?',(app,limit)).fetchall() if app else self.db.execute('SELECT * FROM events ORDER BY sequence_no DESC LIMIT ?',(limit,)).fetchall()
   return [dict(r)|{'payload':json.loads(r['payload_json'])} for r in rows]
 def faults(self):
  with self.lock:return [dict(r) for r in self.db.execute('SELECT * FROM faults ORDER BY id')]
 def add_fault(self,rule):
  kind=rule.get('kind','http_error')
  if kind not in ('offline','delay','timeout','http_error','intermittent','pagination_failure'):raise ValueError('INVALID_FAULT_KIND')
  with self.lock:
   cur=self.db.execute('INSERT INTO faults(app_id,path_pattern,kind,status,delay_ms,remaining) VALUES(?,?,?,?,?,?)',(rule.get('app_id','*'),rule.get('path_pattern','*'),kind,int(rule.get('status',503)),int(rule.get('delay_ms',0)),int(rule.get('remaining',1))))
   return dict(self.db.execute('SELECT * FROM faults WHERE id=?',(cur.lastrowid,)).fetchone())
 def match_fault(self,path):
  with self.lock:
   rows=self.db.execute('SELECT * FROM faults WHERE enabled=1 AND remaining!=0 ORDER BY id').fetchall()
   for row in rows:
    pattern=row['path_pattern']; app=row['app_id']
    if (pattern=='*' or pattern in path) and (app=='*' or f'/apps/{app}/' in path):
     if row['remaining']>0 and not self.frozen:self.db.execute('UPDATE faults SET remaining=remaining-1 WHERE id=?',(row['id'],))
     return dict(row)
   return None
 def backup(self,target):
  with self.lock:
   backup=sqlite3.connect(target)
   try:self.db.backup(backup)
   finally:backup.close()
 def summary(self):
  with self.lock:
   tables={}
   for name in ('app_state','actions','events','faults','inventory','injected_records','typed_entities','seed_receipts','mutations','passive_packets'):
    tables[name]=self.db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
   db_id=self.db.execute("SELECT value FROM metadata WHERE key='database_id'").fetchone()[0]
   wal=Path(str(self.path)+'-wal');shm=Path(str(self.path)+'-shm');base=self.path.stat().st_size
   wal_size=wal.stat().st_size if wal.exists() else 0;shm_size=shm.stat().st_size if shm.exists() else 0
   return {'database_id':db_id,'path':str(self.path),'size_bytes':base,'wal_bytes':wal_size,'shm_bytes':shm_size,'total_bytes':base+wal_size+shm_size,'tables':tables}
 def close(self):
  with self.lock:
   self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
   self.db.close()

 # ---- P1 类型化数据层（CONTRACT_UPGRADE_BLUEPRINT §3.2/§3.3/§4.2/§4.3） ----
 def _journal(self,app,actor,semantic_ref,operation,before_version,after_version,changed_fields,status='committed',trace_ref=None,episode='default'):
  with self.lock:
   cur=self.db.execute('INSERT INTO mutations(mutation_id,episode_scope,actor,app_id,semantic_ref,operation,at,before_version,after_version,changed_fields,status,trace_ref) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),episode,actor,app,semantic_ref,operation,self.sim_time,f'{app}.v{before_version}',f'{app}.v{after_version}',json.dumps(changed_fields,ensure_ascii=False),status,trace_ref))
   return cur.lastrowid
 def _emit_packet(self,app,type_,source,payload,text_projection='',screenshot_ref=None):
  with self.lock:
   cur=self.db.execute('INSERT INTO passive_packets(packet_id,app_id,type,source,observed_at,delivered_at,text_projection,screenshot_ref,payload_json) VALUES(?,?,?,?,?,NULL,?,?,?)',(str(uuid.uuid4()),app,type_,source,self.sim_time,text_projection,screenshot_ref,json.dumps(payload,ensure_ascii=False)))
   return cur.lastrowid
 def read_mutations(self,app,since_cursor=0,limit=100):
  with self.lock:
   rows=self.db.execute('SELECT rowid AS cursor,* FROM mutations WHERE app_id=? AND rowid>? ORDER BY rowid LIMIT ?',(app,since_cursor,limit)).fetchall()
   items=[]
   for r in rows:
    d=dict(r);d['changed_fields']=json.loads(d.pop('changed_fields'))
    for key in ('before_version','after_version'):
     if isinstance(d[key],int):d[key]=f'{app}.v{d[key]}'
    items.append(d)
   next_cursor=items[-1]['cursor'] if items else since_cursor
   return {'mutations':items,'next_cursor':next_cursor,'has_more':len(items)==limit}
 def drain_events(self,app,cursor=0,limit=100):
  with self.lock:
   rows=self.db.execute('SELECT packet_id,sequence,app_id,type,observed_at,delivered_at,text_projection,screenshot_ref FROM passive_packets WHERE app_id=? AND sequence>? ORDER BY sequence LIMIT ?',(app,cursor,limit)).fetchall()
   if rows and not self.frozen:
    ids=[r['sequence'] for r in rows]
    self.db.execute(f"UPDATE passive_packets SET delivered_at=? WHERE sequence IN ({','.join('?'*len(ids))})",(self.sim_time,*ids))
   items=[]
   for r in rows:
    d=dict(r);d['source']=d.pop('app_id');d['delivered_at']=d['delivered_at'] or self.sim_time;items.append(d)
   next_cursor=items[-1]['sequence'] if items else cursor
   return {'packets':items,'next_cursor':next_cursor,'has_more':len(items)==limit}
 def _sync_delta(self,app,entity,ref,before,after):
  if entity in RESOURCES.get(app,()):
   self.db.execute('DELETE FROM injected_records WHERE app_id=? AND resource=? AND id=?',(app,entity,ref))
   if after is not None:
    self.db.execute('INSERT INTO injected_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(app,entity,ref,str(after.get('title','')),str(after.get('subtitle','')),str(after.get('meta','')),int(after.get('price',0)),int(after.get('available',0)),int(after.get('rank',0)),str(after.get('body',''))[:4000],str(after.get('topic_id',''))[:160],json.dumps(after.get('track_ids',[]),ensure_ascii=False)))
   return
  if entity=='fault':
   if after is not None and after.get('kind') not in ('offline','delay','timeout','http_error','intermittent','pagination_failure'):raise ValueError('INVALID_FAULT_KIND')
   if before is not None:self.db.execute('DELETE FROM faults WHERE app_id=? AND kind=? AND path_pattern=?',(app,before.get('kind'),before.get('path_pattern','/runtime/')))
   if after is not None:self.db.execute('INSERT INTO faults(app_id,path_pattern,kind,status,delay_ms,remaining) VALUES(?,?,?,?,?,?)',(app,after.get('path_pattern','/runtime/'),after.get('kind'),int(after.get('status',503)),int(after.get('delay_ms',0)),int(after.get('remaining',1))))
   return
  _,state=self.state(app)
  if entity=='ui_content':
   if after is None:state.pop('ui_content',None)
   else:state['ui_content']=after
  elif entity=='popup':
   state['popup_queue']=[] if after is None else [{'id':ref,'kind':after.get('kind','notice'),'title':after.get('title',''),'message':after.get('message','')}]
  elif entity=='account_field':
   if before is not None:state.setdefault('account_fields',{}).pop(before.get('key',ref),None)
   if after is not None:state.setdefault('account_fields',{})[after.get('key',ref)]=after.get('value','')
  elif entity=='play_history':
   if before is not None:state.setdefault('play_history',[])[:]=[x for x in state['play_history'] if x!=before.get('item_id',ref)]
   if after is not None:state.setdefault('play_history',[]).append(after.get('item_id',ref))
   state['playing']=bool(after and after.get('playing',False))
  elif entity=='favorite':
   if before is not None:state['favorites']=[x for x in state['favorites'] if x!=before.get('item_id',ref)]
   if after is not None:state['favorites'].append(after.get('item_id',ref))
  else:
   field={'order':'orders','passenger':'passengers','user_playlist':'playlists','note':'notes','coupon_wallet':'coupons'}.get(entity)
   if field is None:raise ValueError('INVALID_FIXTURE')
   key='item_id' if entity=='note' else 'id'
   old_ref=before.get('item_id',ref) if entity=='note' and before else ref
   state.setdefault(field,[])[:]=[x for x in state[field] if x.get(key)!=old_ref]
   if after is not None:
    if entity=='order':value={'item_id':after.get('item_id',ref),'status':after.get('status','CONFIRMED'),'price':after.get('price',0),'created_at':after.get('created_at',self.sim_time),**after,'id':ref}
    elif entity=='passenger':value={**after,'id':ref}
    elif entity=='user_playlist':value={'id':ref,'name':after.get('name',''),'songs':after.get('songs',[])}
    elif entity=='note':value={'item_id':after.get('item_id',ref),'text':after.get('text','')}
    else:value={'id':ref,'code':after.get('code',ref),'title':after.get('title',''),'discount':after.get('discount','')}
    state[field].append(value)
  self._set_state(app,state)
 def seed(self,app,fixture,idempotency_key=None):
  """seed 幂等：同一 fixture 两次 seed 无重复对象；同 key 异内容报 IDEMPOTENCY_KEY_REUSED。
  展示数据写 typed_entities+injected_records（GUI 数据路由同源）；popup/fault 覆盖场景状态；
  用户态实体同步写 app_state state_json（GUI state 路由同源）。"""
  if not isinstance(fixture,dict) or not isinstance(fixture.get('common_records'),list) or not isinstance(fixture.get('world_deltas'),list):raise ValueError('INVALID_FIXTURE')
  if self.frozen:raise BusinessError('SESSION_FROZEN',409)
  catalog=json.loads((Path(__file__).resolve().parent.parent/'contracts/entity-catalog'/f'{app}.json').read_text())
  allowed_entities={e['name'] for e in catalog['entity_types']}|{'fault'}
  episode=fixture.get('episode_scope') or 'default'
  for rec in fixture['common_records']:
   if not isinstance(rec,dict) or not all(k in rec for k in ('app_id','entity_type','semantic_ref','data')) or not isinstance(rec['data'],dict):raise ValueError('INVALID_FIXTURE')
   if rec['app_id']!=app or rec['entity_type'] not in allowed_entities:raise ValueError('INVALID_FIXTURE')
   if rec.get('updated_at') is not None:
    try:
     timestamp=datetime.fromisoformat(rec['updated_at'])
     if timestamp.utcoffset() is None:raise ValueError
    except (TypeError,ValueError):raise ValueError('INVALID_FIXTURE')
   if rec['entity_type']=='ui_content':
    data=rec['data']
    if not set(data)<={'hero','planner','profile_name'} or any(not isinstance(data[k],str) for k in ('hero','profile_name') if k in data) or ('planner' in data and (not isinstance(data['planner'],list) or any(not isinstance(x,dict) or not isinstance(x.get('label'),str) or not isinstance(x.get('value'),str) for x in data['planner']))):raise ValueError('INVALID_FIXTURE')
  canonical=json.dumps({'episode_scope':episode,'records':[{'app_id':r['app_id'],'entity_type':r['entity_type'],'semantic_ref':r['semantic_ref'],'record_version':r.get('record_version',1),'updated_at':r.get('updated_at'),'authority':r.get('authority','runner_seed'),'data':r['data']} for r in fixture['common_records']],'deltas':fixture['world_deltas']},sort_keys=True,ensure_ascii=False)
  request_hash=hashlib.sha256(canonical.encode()).hexdigest()
  key=idempotency_key or hashlib.sha256(canonical.encode()).hexdigest()
  with self.lock:
   self.db.execute('BEGIN IMMEDIATE')
   try:
    prior=self.db.execute('SELECT * FROM seed_receipts WHERE idempotency_key=?',(key,)).fetchone()
    if prior:
     if (prior['request_hash'] or prior['readback_hash'])!=request_hash or prior['app_id']!=app or prior['episode_scope']!=episode:raise BusinessError('IDEMPOTENCY_KEY_REUSED')
     self.db.execute('COMMIT');return json.loads(prior['receipt_json'])
    prior=self.db.execute('SELECT receipt_json FROM seed_receipts WHERE app_id=? AND episode_scope=? AND request_hash=? LIMIT 1',(app,episode,request_hash)).fetchone()
    if prior:
     receipt=json.loads(prior['receipt_json'])|{'idempotency_key':key}
     self.db.execute('INSERT INTO seed_receipts VALUES(?,?,?,?,?,?,?)',(key,app,episode,self.sim_time,receipt['readback_hash'],json.dumps(receipt,ensure_ascii=False),request_hash))
     self.db.execute('COMMIT');return receipt
    applied=[]
    for rec in fixture['common_records']:
     entity,ref,data=rec['entity_type'],rec['semantic_ref'],rec['data']
     existing=self.db.execute('SELECT episode_scope,data_json FROM typed_entities WHERE app_id=? AND entity_type=? AND semantic_ref=?',(app,entity,ref)).fetchone()
     if existing and existing['episode_scope']!=episode:raise BusinessError('PATCH_TARGET_EXISTS',409)
     raw_version=rec.get('record_version',1)
     if isinstance(raw_version,str):
      match=re.fullmatch(re.escape(app)+r'\.v([1-9]\d*)',raw_version)
      if not match:raise ValueError('INVALID_FIXTURE')
      version=int(match.group(1))
     elif isinstance(raw_version,int) and not isinstance(raw_version,bool) and raw_version>0:version=raw_version
     else:raise ValueError('INVALID_FIXTURE')
     updated=rec.get('updated_at') or self.sim_time;authority=rec.get('authority','runner_seed')
     self.db.execute('INSERT OR REPLACE INTO typed_entities VALUES(?,?,?,?,?,?,?,?)',(app,entity,ref,episode,version,updated,authority,json.dumps(data,ensure_ascii=False)))
     if entity=='popup':
      _,state=self.state(app)
      state['popup_queue']=[{'id':ref,'kind':data.get('kind','notice'),'title':data.get('title',''),'message':data.get('message','')}]
      self._set_state(app,state,authority)
      self._emit_packet(app,'app_event','popup_script',{'kind':data.get('kind','notice'),'title':data.get('title',''),'message':data.get('message','')},text_projection=f"{data.get('title','')}：{data.get('message','')}")
     elif entity=='fault':
      kind=data.get('kind')
      if kind not in ('offline','delay','timeout','http_error','intermittent','pagination_failure'):raise ValueError('INVALID_FAULT_KIND')
      self.db.execute('DELETE FROM faults WHERE app_id=?',(app,))
      self.db.execute('INSERT INTO faults(app_id,path_pattern,kind,status,delay_ms,remaining) VALUES(?,?,?,?,?,?)',(app,data.get('path_pattern','/runtime/'),kind,int(data.get('status',503)),int(data.get('delay_ms',0)),int(data.get('remaining',1))))
     elif entity in RESOURCES.get(app,()):
      self._remember_injected(app,episode,entity,ref)
      self.db.execute('INSERT OR REPLACE INTO injected_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(app,entity,ref,str(data.get('title','')),str(data.get('subtitle','')),str(data.get('meta','')),int(data.get('price',0)),int(data.get('available',0)),int(data.get('rank',0)),str(data.get('body',''))[:4000],str(data.get('topic_id',''))[:160],json.dumps(data.get('track_ids',[]),ensure_ascii=False)))
     elif entity=='ui_content':
      _,state=self.state(app)
      state['ui_content']=data
      self._set_state(app,state,authority)
     elif entity in ('order','favorite','passenger','play_history','user_playlist','note','coupon_wallet','account_field'):
      self._sync_delta(app,entity,ref,json.loads(existing['data_json']) if existing else None,data)
     applied.append({'entity_type':entity,'semantic_ref':ref,'record_version':f'{app}.v{version}'})
     self._journal(app,'runner_seed',ref,f'seed:{entity}',0,version,sorted(data.keys()),'committed',key,episode)
    for delta in fixture['world_deltas']:
     if not isinstance(delta,dict) or delta.get('op') not in ('add','update','delete') or not delta.get('semantic_ref') or not isinstance(delta.get('set',{}),dict):raise ValueError('INVALID_FIXTURE')
     op,ref=delta['op'],delta['semantic_ref'];row=self.db.execute('SELECT * FROM typed_entities WHERE app_id=? AND semantic_ref=?',(app,ref)).fetchone()
     if row and row['episode_scope']!=episode:raise BusinessError('PATCH_TARGET_EXISTS',409)
     if op=='add':
      if row:raise BusinessError('PATCH_TARGET_EXISTS',409)
      entity=delta.get('entity_type')
      if entity not in allowed_entities:raise ValueError('INVALID_FIXTURE')
      data={k.removeprefix('data.'):v for k,v in delta['set'].items()}
      declared={k.removeprefix(ref+'.').removeprefix('data.') for k in delta.get('allowed_changed_fields',[])}
      if set(data)!=declared:raise BusinessError('PATCH_FIELDS_MISMATCH',400)
      if entity in RESOURCES.get(app,()):self._remember_injected(app,episode,entity,ref)
      self.db.execute('INSERT INTO typed_entities VALUES(?,?,?,?,?,?,?,?)',(app,entity,ref,episode,1,self.sim_time,delta.get('authority','runner_seed'),json.dumps(data,ensure_ascii=False)))
      self._sync_delta(app,entity,ref,None,data)
     elif op=='update':
      if not row:raise BusinessError('PATCH_TARGET_NOT_FOUND',404)
      declared={k.removeprefix(ref+'.').removeprefix('data.') for k in delta.get('allowed_changed_fields',[])};before=json.loads(row['data_json']);data=before.copy()
      patch={k.removeprefix('data.'):v for k,v in delta['set'].items()}
      changed=[k for k in patch if data.get(k)!=patch[k]]
      if set(changed)!=declared:raise BusinessError('PATCH_FIELDS_MISMATCH',400)
      data.update(patch)
      if row['entity_type'] in RESOURCES.get(app,()):self._remember_injected(app,episode,row['entity_type'],ref)
      self.db.execute('UPDATE typed_entities SET data_json=?,record_version=record_version+1,updated_at=? WHERE app_id=? AND semantic_ref=?',(json.dumps(data,ensure_ascii=False),self.sim_time,app,ref))
      self._sync_delta(app,row['entity_type'],ref,before,data)
     else:
      if not row:raise BusinessError('PATCH_TARGET_NOT_FOUND',404)
      declared={k.removeprefix(ref+'.').removeprefix('data.') for k in delta.get('allowed_changed_fields',[])}
      if set(json.loads(row['data_json']))!=declared:raise BusinessError('PATCH_FIELDS_MISMATCH',400)
      if row['entity_type'] in RESOURCES.get(app,()):self._remember_injected(app,episode,row['entity_type'],ref)
      self.db.execute('DELETE FROM typed_entities WHERE app_id=? AND semantic_ref=?',(app,ref))
      self._sync_delta(app,row['entity_type'],ref,json.loads(row['data_json']),None)
     applied.append({'delta':op,'semantic_ref':ref})
     self._journal(app,'runner_seed',ref,f'delta:{op}',0,1,sorted(delta['set'].keys()),'committed',key,episode)
    readback_hash=hashlib.sha256(json.dumps(self.read_state(app),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    receipt={'status':'ok','idempotency_key':key,'app_id':app,'episode_scope':episode,'applied_at':self.sim_time,'readback_hash':readback_hash,'applied':applied}
    self.db.execute('INSERT INTO seed_receipts VALUES(?,?,?,?,?,?,?)',(key,app,episode,self.sim_time,readback_hash,json.dumps(receipt,ensure_ascii=False),request_hash))
    self.db.execute('COMMIT');return receipt
   except BusinessError as e:
    self.db.execute('ROLLBACK')
    self._journal(app,'runner_seed',app,'seed',0,0,[],'rejected',e.code,episode)
    raise
   except Exception:self.db.execute('ROLLBACK');raise
 def _set_state(self,app,state,authority='runner_seed'):
  row=self.db.execute('SELECT revision FROM app_state WHERE app_id=?',(app,)).fetchone();rev=row['revision']+1 if row else 1
  self.db.execute('UPDATE app_state SET revision=?,state_json=? WHERE app_id=?',(rev,json.dumps(state,ensure_ascii=False),app))
  self._append_event(self._event(app,'domain',f'seed_{authority}',str(uuid.uuid4()),None,{'revision':rev}))
 def freeze(self):
  with self.lock:self.frozen=True
 def unfreeze(self):
  with self.lock:self.frozen=False
 def advance_time(self,logical_time):
  """只推进 fixture 预置外生事件（六 App 无定时业务，applied_events 恒空）并返回被动事件 cursor。"""
  with self.lock:
   self.sim_time=logical_time
   cursor=self.db.execute('SELECT COALESCE(MAX(sequence),0) FROM passive_packets').fetchone()[0]
   return {'logical_time':logical_time,'applied_events':[],'passive_cursor':cursor}
 def teardown(self,app,episode_scope):
  """恢复 episode 的 GUI 前状态并清理注入数据；保留 mutation journal 供审计。"""
  with self.lock:
   self.db.execute('BEGIN IMMEDIATE')
   try:
    checkpoint=self.db.execute('SELECT * FROM episode_checkpoints WHERE app_id=? AND episode_scope=?',(app,episode_scope)).fetchone()
    receipts=self.db.execute('SELECT COUNT(*) FROM seed_receipts WHERE app_id=? AND episode_scope=?',(app,episode_scope)).fetchone()[0]
    backups=self.db.execute('SELECT * FROM episode_injected_backups WHERE app_id=? AND episode_scope=?',(app,episode_scope)).fetchall()
    rows=[dict(r) for r in self.db.execute('SELECT entity_type,semantic_ref,data_json FROM typed_entities WHERE app_id=? AND episode_scope=?',(app,episode_scope)).fetchall()]
    if not checkpoint and not receipts and not rows and not backups:
     state=self.read_state(app)
     self.db.execute('COMMIT')
     return {'status':'ok','episode_scope':episode_scope,'removed':{},'readback_hash':hashlib.sha256(json.dumps(state,sort_keys=True,ensure_ascii=False).encode()).hexdigest()}
    if checkpoint:
     later=self.db.execute('SELECT 1 FROM seed_receipts WHERE app_id=? AND episode_scope!=? AND rowid>? LIMIT 1',(app,episode_scope,checkpoint['receipt_cursor'])).fetchone()
     if later and self.db.execute('SELECT 1 FROM episode_checkpoints WHERE app_id=? AND episode_scope!=? LIMIT 1',(app,episode_scope)).fetchone():raise BusinessError('TEARDOWN_FAILED',500)
     self.db.execute('UPDATE app_state SET revision=revision+1,state_json=? WHERE app_id=?',(checkpoint['state_json'],app))
     self.db.execute('DELETE FROM inventory WHERE app_id=?',(app,))
     self.db.executemany('INSERT INTO inventory VALUES(?,?,?,?,?)',[(r['app_id'],r['item_id'],r['available'],r['price'],r['revision']) for r in json.loads(checkpoint['inventory_json'])])
     if later:
      # ponytail: one GUI-active episode per app; interleaved GUI episodes need per-object undo records.
      for foreign in self.db.execute('SELECT entity_type,semantic_ref,data_json FROM typed_entities WHERE app_id=? AND episode_scope!=?',(app,episode_scope)).fetchall():
       if foreign['entity_type'] not in RESOURCES.get(app,()) and foreign['entity_type']!='fault':
        data=json.loads(foreign['data_json'])
        self._sync_delta(app,foreign['entity_type'],foreign['semantic_ref'],data,data)
    removed={'typed_entities':len(rows),'injected_records':0,'faults':0,'receipts':0,'mutations':0,'state_entries':0}
    _,state=self.state(app)
    before_len=sum(len(state.get(k,[])) for k in ('orders','favorites','passengers','play_history','playlists','notes','coupons'))
    had_popup=False
    for row in rows:
     et,ref=row['entity_type'],row['semantic_ref'];data=json.loads(row['data_json'])
     if et=='popup':had_popup=True
     elif et=='fault':
      cur=self.db.execute('DELETE FROM faults WHERE app_id=? AND kind=? AND path_pattern=?',(app,data.get('kind',''),data.get('path_pattern','/runtime/')))
      removed['faults']+=cur.rowcount
     elif et in RESOURCES.get(app,()):
      cur=self.db.execute('DELETE FROM injected_records WHERE app_id=? AND resource=? AND id=?',(app,et,ref))
      removed['injected_records']+=cur.rowcount
     elif et=='order':state['orders']=[o for o in state.get('orders',[]) if o.get('id')!=ref]
     elif et=='favorite':
      item=data.get('item_id',ref)
      state['favorites']=[x for x in state.get('favorites',[]) if x!=item]
     elif et=='passenger':state['passengers']=[p for p in state.get('passengers',[]) if p.get('id')!=ref]
     elif et=='play_history':
      item=data.get('item_id',ref)
      state['play_history']=[x for x in state.get('play_history',[]) if x!=item]
      if not state['play_history']:state['playing']=False
     elif et=='user_playlist':state['playlists']=[p for p in state.get('playlists',[]) if p.get('id')!=ref]
     elif et=='note':
      item=data.get('item_id',ref)
      state['notes']=[n for n in state.get('notes',[]) if n.get('item_id')!=item]
     elif et=='coupon_wallet':state['coupons']=[c for c in state.get('coupons',[]) if c.get('id')!=ref]
     elif et=='account_field':state.get('account_fields',{}).pop(data.get('key',ref),None)
     elif et=='ui_content':state.pop('ui_content',None)
    if had_popup:state['popup_queue']=self._initial(app)['popup_queue']
    after_len=sum(len(state.get(k,[])) for k in ('orders','favorites','passengers','play_history','playlists','notes','coupons'))
    removed['state_entries']=before_len-after_len
    self._set_state(app,state,'runner_teardown')
    self.db.execute('DELETE FROM typed_entities WHERE app_id=? AND episode_scope=?',(app,episode_scope))
    for backup in backups:
     self.db.execute('DELETE FROM injected_records WHERE app_id=? AND resource=? AND id=?',(app,backup['resource'],backup['id']))
     if backup['previous_json'] is not None:
      old=json.loads(backup['previous_json'])
      self.db.execute('INSERT INTO injected_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',tuple(old[k] for k in ('app_id','resource','id','title','subtitle','meta','price','available','rank','body','topic_id','track_ids')))
    self.db.execute('DELETE FROM episode_injected_backups WHERE app_id=? AND episode_scope=?',(app,episode_scope))
    cur=self.db.execute('DELETE FROM seed_receipts WHERE episode_scope=? AND (app_id=? OR app_id IS NULL)',(episode_scope,app))
    removed['receipts']=cur.rowcount
    self.db.execute('DELETE FROM actions WHERE episode_scope=? AND app_id=?',(episode_scope,app))
    self.db.execute('DELETE FROM episode_checkpoints WHERE app_id=? AND episode_scope=?',(app,episode_scope))
    self._journal(app,'runner_teardown',f'episode:{episode_scope}','teardown',0,0,[],'committed',None,episode_scope)
    self.db.execute('COMMIT')
    state=self.read_state(app)
    return {'status':'ok','episode_scope':episode_scope,'removed':removed,'readback_hash':hashlib.sha256(json.dumps(state,sort_keys=True,ensure_ascii=False).encode()).hexdigest()}
   except Exception:
    self.db.execute('ROLLBACK')
    raise BusinessError('TEARDOWN_FAILED',500)
 def restore_baseline(self,app,episode_scope,baseline_ref,info):
  receipt=self.teardown(app,episode_scope)
  return {'episode_scope':episode_scope,'baseline_ref':baseline_ref,'baseline_hash':receipt['readback_hash'],'device_version':info.get('baseline_version'),'service_version':'local-controller-v1','cleared_scope':episode_scope,'removed':receipt['removed']}
 def read_state(self,app,scope=None):
  with self.lock:
   revision,state=self.state(app)
   typed=[]
   for r in self.db.execute('SELECT app_id,entity_type,semantic_ref,episode_scope,record_version,updated_at,authority,data_json FROM typed_entities WHERE app_id=?',(app,)).fetchall():
    d=dict(r);d['data']=json.loads(d.pop('data_json'));d['record_version']=f"{app}.v{d['record_version']}";typed.append(d)
   if isinstance(scope,list):
    typed=[t for t in typed if t['entity_type'] in scope]
    state={k:v for k,v in state.items() if k in scope or k in ('orders','favorites','popup_queue')}
   return {'revision':revision,'state':state,'typed_entities':typed}
 def query(self,app,operation,selector=None,projection=None,as_of=None):
  catalog=json.loads((Path(__file__).resolve().parent.parent/'contracts/entity-catalog'/f'{app}.json').read_text())
  ops={op['name']:op for ent in catalog['entity_types'] for op in ent['query_operations']}
  if operation not in ops:raise ValueError('INVALID_OPERATION')
  opdef=ops[operation];selector={} if selector is None else selector;projection=list(opdef['result_fields']) if projection is None else projection
  if not isinstance(selector,dict):raise ValueError('INVALID_OPERATION')
  if not isinstance(projection,list) or not projection:raise ValueError('INVALID_PROJECTION')
  if not set(projection)<=set(opdef['result_fields']):raise ValueError('INVALID_PROJECTION')
  if not set(selector)<=set(opdef['selector_fields']):raise ValueError('INVALID_OPERATION')
  if as_of is not None:
   try:
    cutoff=datetime.fromisoformat(as_of)
    if cutoff.utcoffset() is None:raise ValueError
   except (TypeError,ValueError):raise ValueError('INVALID_AS_OF')
  entity=next(ent['name'] for ent in catalog['entity_types'] if any(o['name']==operation for o in ent['query_operations']))
  single='semantic_ref' in selector and opdef['granularity']=='details'
  with self.lock:
   if entity in RESOURCES.get(app,()):
    typed={r['semantic_ref']:r for r in self.db.execute('SELECT * FROM typed_entities WHERE app_id=? AND entity_type=?',(app,entity))}
    rows=[]
    for record in self.visible_records(app,entity):
     ref=record['id'];metadata=typed.get(ref)
     data=json.loads(metadata['data_json']) if metadata else record
     rows.append({'semantic_ref':ref,'record_version':metadata['record_version'] if metadata else 1,
                  'updated_at':metadata['updated_at'] if metadata else '1970-01-01T00:00:00+00:00',
                  'data':{**data,'semantic_ref':ref}})
   elif entity=='ui_content':
    rows=[dict(r)|{'data':json.loads(r['data_json'])} for r in self.db.execute('SELECT * FROM typed_entities WHERE app_id=? AND entity_type=?',(app,entity)).fetchall()]
   else:
    _,state=self.state(app)
    raw=[]
    if entity=='order':raw=[{'semantic_ref':x['id'],**{k:x.get(k) for k in ('item_id','title','subtitle','base_price','discount','coupon_title','price','fare_difference','status','created_at','payments','origin','destination','service_type','reservation_time','train','train_schedule','seat','traveler_name','traveler_phone','vehicle_plate','driver','eta_minutes')}} for x in state.get('orders',[])]
    elif entity=='popup':raw=[{'semantic_ref':x['id'],'kind':x.get('kind'),'title':x.get('title'),'message':x.get('message')} for x in state.get('popup_queue',[])]
    elif entity=='favorite':raw=[{'semantic_ref':x,'item_id':x} for x in state.get('favorites',[])]
    elif entity=='passenger':raw=[{'semantic_ref':x.get('id',''),'name':x.get('name',''),'phone':x.get('phone','')} for x in state.get('passengers',[])]
    elif entity=='play_history':raw=[{'semantic_ref':x,'item_id':x,'playing':x==state['play_history'][-1] and state.get('playing',False)} for x in state.get('play_history',[])]
    elif entity=='user_playlist':raw=[{'semantic_ref':x.get('id',''),'name':x.get('name',''),'songs':x.get('songs',[])} for x in state.get('playlists',[])]
    elif entity=='note':raw=[{'semantic_ref':x.get('item_id',''),'item_id':x.get('item_id',''),'text':x.get('text','')} for x in state.get('notes',[])]
    elif entity=='coupon_wallet':raw=[{'semantic_ref':x.get('id',''),'code':x.get('code',''),'title':x.get('title',''),'discount':x.get('discount','')} for x in state.get('coupons',[])]
    elif entity=='account_field':raw=[{'semantic_ref':k,'key':k,'value':v} for k,v in state.get('account_fields',{}).items()]
    rows=[{'semantic_ref':x.get('semantic_ref',''),'record_version':1,'updated_at':self.sim_time,'data':x} for x in raw]
  if as_of is not None:rows=[r for r in rows if datetime.fromisoformat(r['updated_at'])<=cutoff]
  if single:
   hits=[r for r in rows if r['semantic_ref']==selector['semantic_ref']]
   if not hits:return {'status':'not_found','error':{'code':'NOT_FOUND','message':'record not found','retryable':False}}
   hit=hits[0]
   return {'status':'ok','semantic_ref':hit['semantic_ref'],'record_version':hit['record_version'],'coverage':{'scope':'single_record','complete':True,'as_of':as_of},'data':{k:hit['data'].get(k) for k in projection}}
  needle=str(selector.get('keyword','')).casefold().strip()
  hits=[r for r in rows if not needle or needle in f"{r['data'].get('title','')} {r['data'].get('subtitle','')}".casefold()]
  try:limit=int(selector.get('limit',50));offset=int(selector.get('offset',0))
  except (TypeError,ValueError):raise ValueError('INVALID_PAGINATION')
  if limit<1 or limit>200 or offset<0:raise ValueError('INVALID_PAGINATION')
  page=hits[offset:offset+limit]
  complete=len(hits)<=offset+limit
  return {'status':'ok','coverage':{'scope':'query_scope','complete':complete,'as_of':as_of},'data':[{'semantic_ref':r['semantic_ref'],**{k:r['data'].get(k) for k in projection}} for r in page]}
