import {spawnSync} from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
const root=path.resolve(import.meta.dirname,'..')
spawnSync(process.execPath,[path.join(root,'scripts/create-apps.mjs')],{stdio:'inherit'})
const defs=JSON.parse(fs.readFileSync(path.join(root,'apps/app-definitions.json')))
for(const id of Object.keys(defs)){
  const r=spawnSync(path.join(root,'node_modules/.bin/vite'),['build','--mode',process.env.BUILD_MODE||'debug','--config',path.join(root,`apps/${id}/vite.config.js`)],{cwd:root,stdio:'inherit'})
  if(r.status) process.exit(r.status)
}
