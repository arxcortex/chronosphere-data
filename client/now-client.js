// Chronosphere Now: data loader for chronosphere.html.
// Loads now.json from GitHub Pages only when the reader reaches the Now stop on the timeline,
// so the historical globe costs nothing extra. Rendering (pulses, rail, panels) lives in the page.
//
//   import { loadNow, loadLens } from './chronosphere/now-client.js';
//   const now = await loadNow(7);          // 1 and 7 share now.json; 30 loads now_30d.json
//   now.events, now.hazards, now.chokepoints, now.dispatches, now.meta

export const DATA_BASE = 'https://YOUR-GITHUB-USERNAME.github.io/chronosphere-data';

const cache = new Map();

async function getJSON(path) {
  if (cache.has(path)) return cache.get(path);
  const p = fetch(`${DATA_BASE}/${path}`, { cache: 'default' }).then(r => {
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
    return r.json();
  });
  cache.set(path, p);
  try { return await p; } catch (e) { cache.delete(path); throw e; }
}

export async function loadNow(days = 7) {
  const doc = await getJSON(days > 7 ? 'now/now_30d.json' : 'now/now.json');
  if (days >= 7) return doc;
  const cutoff = Date.now() - days * 864e5;
  return { ...doc, events: doc.events.filter(e => Date.parse(e.u || e.t) >= cutoff),
           hazards: doc.hazards.filter(h => Date.parse(h.t) >= cutoff) };
}

// Any lens pack: loadLens('geo') -> eras.json, later loadLens('cold-war') etc.
export const loadLens = (id, file = 'eras.json') => getJSON(`lenses/${id}/${file}`);

// Age of the data, for the "updated 2 h ago" line under the Now rail.
export function freshness(meta) {
  const h = Math.round((Date.now() - Date.parse(meta.generated)) / 36e5);
  return h < 1 ? 'updated within the hour' : `updated ${h} h ago`;
}
