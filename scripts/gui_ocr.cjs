const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { PNG } = require('pngjs');
const moduleName = process.env.GUI_TESSERACT_MODULE || 'tesseract.js';
const { createWorker } = require(moduleName);

async function main() {
  const modelDir = path.resolve(__dirname, '../models/ocr');
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const language = input.language || 'eng';
  const expectedHash = {
    eng: '5dc5d8d640a212c9d6184921ba103b186f50e0fed9ee716c53e6b312b400d747',
    chi_sim: 'a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730'
  }[language];
  if (!expectedHash) throw new Error('Unsupported OCR language');
  const model = fs.readFileSync(path.join(modelDir, `${language}.traineddata`));
  const hash = crypto.createHash('sha256').update(model).digest('hex');
  if (hash !== expectedHash) {
    throw new Error('OCR model hash mismatch');
  }
  const worker = await createWorker(language, 1, { langPath: modelDir, gzip: false, cacheMethod: 'none' });
  try {
    const modes = new Set(input.regions.map(item => item.pageseg));
    if (modes.size > 1) throw new Error('OCR regions must not mix pageseg modes');
    const pageseg = input.regions.length ? input.regions[0].pageseg : undefined;
    if (pageseg !== undefined) {
      if (!Number.isInteger(pageseg) || pageseg < 0 || pageseg > 13) throw new Error('Unsupported pageseg mode');
      await worker.setParameters({ tessedit_pageseg_mode: String(pageseg) });
    } else if (language === 'chi_sim') {
      await worker.setParameters({ tessedit_pageseg_mode: '7' });
    }
    const regions = [];
    for (const item of input.regions) {
      let image = input.image;
      let options = { rectangle: item.rectangle };
      if (item.scale && item.scale > 1) {
        const source = PNG.sync.read(fs.readFileSync(input.image));
        const r = item.rectangle;
        const enlarged = new PNG({ width: r.width * item.scale, height: r.height * item.scale });
        for (let y = 0; y < enlarged.height; y++) {
          for (let x = 0; x < enlarged.width; x++) {
            const from = ((r.top + Math.floor(y / item.scale)) * source.width + r.left + Math.floor(x / item.scale)) * 4;
            source.data.copy(enlarged.data, (y * enlarged.width + x) * 4, from, from + 4);
          }
        }
        image = PNG.sync.write(enlarged);
        options = {};
      }
      const result = await worker.recognize(image, options);
      regions.push({ field: item.field, text: result.data.text.trim(), confidence: result.data.confidence,
                     rectangle: item.rectangle, scale: item.scale || 1,
                     ...(item.pageseg !== undefined ? { pageseg: item.pageseg } : {}) });
    }
    const checks = [];
    if (input.pixel_checks?.length) {
      const source = PNG.sync.read(fs.readFileSync(input.image));
      for (const item of input.pixel_checks) {
        const r = item.rectangle;
        if (item.kind !== 'switch_knob_left' || r.left < 0 || r.top < 0 ||
            r.left + r.width > source.width || r.top + r.height > source.height) {
          throw new Error('Unsupported pixel check or bounds');
        }
        const y = r.top + Math.floor(r.height / 2);
        const half = Math.max(1, Math.floor(Math.min(r.width, r.height) / 12));
        const brightness = x => {
          let sum = 0;
          for (let dy = -half; dy <= half; dy++) for (let dx = -half; dx <= half; dx++) {
            const i = ((y + dy) * source.width + x + dx) * 4;
            sum += (source.data[i] + source.data[i + 1] + source.data[i + 2]) / 3;
          }
          return sum / ((2 * half + 1) ** 2);
        };
        const left = brightness(r.left + Math.floor(r.width / 4));
        const right = brightness(r.left + Math.floor(3 * r.width / 4));
        checks.push({ field: item.field, kind: item.kind, rectangle: r, left_brightness: left,
                      right_brightness: right, difference: left - right });
      }
    }
    process.stdout.write(JSON.stringify({ engine: 'tesseract.js@7.0.0', language, model_sha256: hash, regions, checks }) + '\n');
  } finally {
    await worker.terminate();
  }
}
main().catch(error => { process.stderr.write(String(error) + '\n'); process.exitCode = 1; });
