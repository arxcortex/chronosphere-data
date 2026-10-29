// Pulls ERAS_GEO and ERAS_RELIGION out of chronosphere.html into lens JSON.
// Usage: node tools/extract_eras.mjs path/to/chronosphere.html
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';

const src = fs.readFileSync(process.argv[2], 'utf8');
const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');

function grabArray(name) {
  const start = src.indexOf(`const ${name} = [`);
  if (start < 0) throw new Error(`${name} not found`);
  let i = src.indexOf('[', start), depth = 0, inStr = null, esc = false;
  for (; i < src.length; i++) {
    const c = src[i];
    if (inStr) { if (esc) esc = false; else if (c === '\\') esc = true; else if (c === inStr) inStr = null; continue; }
    if (c === '"' || c === "'" || c === '`') { inStr = c; continue; }
    if (c === '[') depth++;
    else if (c === ']') { depth--; if (depth === 0) break; }
  }
  const literal = src.slice(src.indexOf('[', start), i + 1);
  return vm.runInNewContext(`(${literal})`, { GB_SUBDIVS: [] });
}

const hex = n => (typeof n === 'number' ? '#' + n.toString(16).padStart(6, '0') : n);
const slug = s => s.toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60);

const index = [];
for (const [name, lens] of [['ERAS_GEO', 'geo'], ['ERAS_RELIGION', 'religion']]) {
  const eras = grabArray(name).map(era => ({
    ...era,
    polities: (era.polities || []).map(p => ({ ...p, color: hex(p.color) })),
    events: (era.events || []).map(e => ({ id: `${lens}-${slug(e.name)}-${e.year}`, ...e })),
  }));
  fs.writeFileSync(path.join(root, 'lenses', lens, 'eras.json'), JSON.stringify(eras, null, 1));
  for (const era of eras) {
    const countries = [...new Set(era.polities.flatMap(p => p.countries || []))];
    for (const e of era.events) {
      if (typeof e.lat !== 'number') continue;
      index.push({ id: e.id, lens, name: e.name, year: e.year, lat: e.lat, lon: e.lon,
                   era: era.name, era_year: era.year, wiki: e.wiki || null, era_countries: countries });
    }
  }
  console.log(`${lens}: ${eras.length} eras, ${eras.reduce((n, e) => n + e.events.length, 0)} events`);
}
fs.writeFileSync(path.join(root, 'data', 'history_index.json'), JSON.stringify(index));
console.log(`history_index: ${index.length} events`);
