#!/usr/bin/env python3
import argparse,json,sys
EXT={'com.ku.simulator.dudu','com.ku.simulator.lianqi','com.ku.simulator.starrail','com.ku.simulator.babaquna','com.ku.simulator.wangyiyun','com.ku.simulator.chaowei'}
p=argparse.ArgumentParser();p.add_argument('before');p.add_argument('after');p.add_argument('--output');a=p.parse_args()
b=json.load(open(a.before));c=json.load(open(a.after));bp={x['package']:x for x in b['packages']};cp={x['package']:x for x in c['packages']}
removed=sorted(bp.keys()-cp.keys());added=sorted(cp.keys()-bp.keys());changed={k:{'before':bp[k],'after':cp[k]} for k in bp.keys()&cp.keys() if bp[k]!=cp[k]}
system={k:{'before':b.get(k),'after':c.get(k)} for k in ('ku_runtime','avd','snapshot','snapshot_sha256','avd_config_sha256','android_api','abi','system_fingerprint','system_image_sha256','screen','locale','timezone','input_method','network','certificates') if b.get(k)!=c.get(k)}
report={'safe':not removed and not changed and not system and set(added)<=EXT,'removed':removed,'added':added,'changed':changed,'system_changes':system}
raw=json.dumps(report,ensure_ascii=False,indent=2)+'\n';print(raw,end='')
if a.output:open(a.output,'w').write(raw)
sys.exit(0 if report['safe'] else 1)
