import {spawnSync} from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
const root=path.resolve(import.meta.dirname,'..')
spawnSync(process.execPath,[path.join(root,'scripts/create-apps.mjs')],{stdio:'inherit'})
const defs=JSON.parse(fs.readFileSync(path.join(root,'apps/app-definitions.json')))
for(const id of Object.keys(defs)){
  const env={...process.env,UNI_INPUT_DIR:path.join(root,'apps',id),UNI_OUTPUT_DIR:path.join(root,'apps',id,'uni-dist')}
  const result=spawnSync(path.join(root,'node_modules/.bin/uni'),['build','-p','h5','--config','uni.vite.config.js'],{cwd:path.join(root,'apps',id),env,stdio:'inherit'})
  if(result.status)process.exit(result.status)
}
