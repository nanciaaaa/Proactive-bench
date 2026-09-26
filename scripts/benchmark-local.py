#!/usr/bin/env python3
"""Run a reproducible local latency, memory and disk-growth smoke benchmark."""
import argparse,json,subprocess,sys,tempfile,time,urllib.request,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
def call(base,path,method='GET',data=None):
 req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,method=method,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=10) as response:return json.loads(response.read())
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--requests',type=int,default=1000);parser.add_argument('--output',default='tests/results/local-performance.json');args=parser.parse_args()
 with tempfile.TemporaryDirectory() as temp:
  runtime=Path(temp)/'perf';proc=subprocess.Popen([sys.executable,str(ROOT/'local-controller/server.py'),'--instance-id','performance','--runtime',str(runtime),'--port','0'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  info=json.loads(proc.stdout.readline());base=f"http://127.0.0.1:{info['port']}";before=sum(x.stat().st_size for x in runtime.rglob('*') if x.is_file());samples=[]
  try:
   for i in range(args.requests):
    start=time.perf_counter()
    if i%5:call(base,f'/runtime/v1/apps/chaowei/data/articles?offset={(i*50)%9950}&limit=50')
    else:call(base,'/runtime/v1/apps/chaowei/telemetry','POST',{'type':'benchmark','payload':{'iteration':i}})
    samples.append((time.perf_counter()-start)*1000)
   samples.sort();after=sum(x.stat().st_size for x in runtime.rglob('*') if x.is_file());status=(Path('/proc')/str(proc.pid)/'status').read_text();rss=next((int(line.split()[1])*1024 for line in status.splitlines() if line.startswith('VmRSS:')),None)
   result={'schema_version':'1.0','request_count':args.requests,'dataset_records':140000,'latency_ms':{'p50':round(samples[len(samples)//2],3),'p95':round(samples[int(len(samples)*.95)-1],3),'max':round(max(samples),3)},'controller_rss_bytes':rss,'disk_bytes':{'before':before,'after':after,'growth':after-before}}
  finally:proc.terminate();proc.wait(timeout=10);proc.stdout.close();proc.stderr.close()
 out=ROOT/args.output;out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()

