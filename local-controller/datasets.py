import hashlib, json, sqlite3
from pathlib import Path

VERSION='2026.09.10'
FIXTURE=Path(__file__).resolve().parent.parent/'datasets/fixtures/research-seed-v1.json'
RESOURCES={
 'dudu':['places','vehicle_quotes','coupons'],
 'lianqi':['routes','coupons'],
 'starrail':['stations','trains','seats'],
 'babaquna':['flights','hotels','attractions','coupons'],
 'wangyiyun':['songs','playlists'],
 'chaowei':['articles','tags'],
}
NAMES={'places':'地点','vehicle_quotes':'车型报价','routes':'路线','coupons':'优惠券','stations':'车站','trains':'车次','seats':'席别','flights':'机票','hotels':'酒店','attractions':'景点票','songs':'歌曲','playlists':'歌单','articles':'文章','tags':'标签'}
DETAILS={
 'places':lambda i:f'上海 · 距离 {(i%18)+1}.2 km',
 'vehicle_quotes':lambda i:f'{("快车","舒适型","六座车")[i%3]} · 预计 {(i%24)+8} 分钟',
 'routes':lambda i:f'预约 {8+i%12:02d}:{(i*5)%60:02d} · ETA {(i%25)+6} 分钟',
 'coupons':lambda i:f'满 {30+i%5*10} 减 {5+i%4*3} · 领取后 7 日有效',
 'stations':lambda i:f'{("始发站","中间站","终点站")[i%3]} · 站台 {(i%12)+1}',
 'trains':lambda i:f'G{100+i%800} · 经停 {i%8+1} 站 · {8+i%10:02d}:{(i*7)%60:02d}',
 'seats':lambda i:f'{("二等座","一等座","商务座")[i%3]} · 可退改',
 'flights':lambda i:f'MU{1000+i%8000} · {6+i%16:02d}:{(i*5)%60:02d} 起飞',
 'hotels':lambda i:f'{("大床房","双床房","家庭房")[i%3]} · 含早 · 可取消',
 'attractions':lambda i:f'{("成人票","亲子票","家庭票")[i%3]} · 当日有效',
 'songs':lambda i:f'{("流行","民谣","轻音乐")[i%3]} · 03:{i%60:02d}',
 'playlists':lambda i:f'{20+i%80} 首歌曲 · 为你推荐',
 'articles':lambda i:f'{("工程","旅行","音乐","生活")[i%4]} · {3+i%18} 分钟阅读',
 'tags':lambda i:f'包含 {10+i%90} 篇文章 · 持续更新',
}

def build_dataset(path: Path, count=10000):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        try:
            db=sqlite3.connect(path); current=db.execute("SELECT value FROM dataset_meta WHERE key='version'").fetchone();db.close()
            if current and current[0]==VERSION:return manifest(path)
        except sqlite3.Error:pass
        path.unlink()
    db=sqlite3.connect(path)
    db.executescript('PRAGMA journal_mode=OFF; CREATE TABLE dataset_meta(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE records(app_id TEXT, resource TEXT, id TEXT PRIMARY KEY, title TEXT, subtitle TEXT, meta TEXT, price INTEGER, available INTEGER, rank INTEGER, body TEXT, topic_id TEXT, track_ids TEXT); CREATE INDEX records_lookup ON records(app_id,resource,rank); CREATE VIRTUAL TABLE records_fts USING fts5(id,title,subtitle,content=records,content_rowid=rowid);')
    db.execute('INSERT INTO dataset_meta VALUES(?,?)',('version',VERSION))
    fixture=json.loads(FIXTURE.read_text(encoding='utf8'))
    rows=[]
    for app,resources in RESOURCES.items():
     for res in resources:
      for i in range(count):
       if i<len(fixture['resources'].get(res,[])):
        entry=fixture['resources'][res][i];title,subtitle,price,available=entry[:4];body=entry[4] if res in ('articles','tags','attractions') and len(entry)>4 else None;topic_id=entry[5] if res=='articles' and len(entry)>5 else None;track_ids=json.dumps(entry[4],ensure_ascii=False) if res=='playlists' and len(entry)>4 else None
       else:
        price=(i*17)%500+9;available=(i*31)%90+1;title=f'{NAMES[res]} {i+1}';subtitle=DETAILS[res](i);body=None;topic_id=None;track_ids=json.dumps([f'wangyiyun-songs-{j:05d}' for j in range(i%5,(i%5)+4)]) if res=='playlists' else None
       rows.append((app,res,f'{app}-{res}-{i:05d}',title,subtitle,f'¥{price} · 余量 {available}',price,available,i,body,topic_id,track_ids))
    db.executemany('INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',rows)
    db.execute("INSERT INTO records_fts(rowid,id,title,subtitle) SELECT rowid,id,title,subtitle FROM records")
    db.commit(); db.close()
    return manifest(path)

def manifest(path: Path):
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    total=sum(len(v) for v in RESOURCES.values())*10000
    result={'schema_version':'1.0','dataset_version':VERSION,'checksum':f'sha256:{digest}','record_count':total,'resources':RESOURCES,'data_origin':'synthetic_research_fixture','fixture_schema_version':'1.0'}
    path.with_suffix('.manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result
