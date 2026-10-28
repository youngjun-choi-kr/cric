// Mission Studio: the server API, the hosted replay that stands in for it, the busy indicator, the glossary and
// the orbit-timing helpers. Shared by studio.js; no three.js here.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const HAS_API = await fetch('/api/incidents', { method: 'HEAD' }).then((r) => r.ok).catch(() => false);
// Without the server (GitHub Pages) the page replays one recorded run: every answer below comes from that run.
const REPLAY = HAS_API ? null : await fetch('launch/replay/jimin1.json').then((r) => (r.ok ? r.json() : null)).catch(() => null);
const NOW = () => (REPLAY ? Date.parse(REPLAY.recorded_utc) : Date.now());
if (!HAS_API) {
  $('serverNote').hidden = false;
  if (REPLAY) $('serverNote').innerHTML = `<b>Hosted replay</b> of a run recorded on ${REPLAY.recorded_utc.slice(0, 10)}. Every number comes from that run.
    To fly your own rocket, run it locally (SETUP.md).`;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function vehDiff(a, b) {
  if (!a || !b) return 0;
  if (a.stages.length !== b.stages.length) return 1e9;
  return a.stages.reduce((t, s, i) => t + Math.abs(s.prop_kg - b.stages[i].prop_kg) / (s.prop_kg || 1)
    + Math.abs(s.thrust_vac_n - b.stages[i].thrust_vac_n) / (s.thrust_vac_n || 1), 0);
}
async function replayApi(path, body) {
  const list = REPLAY.entries.filter((e) => e.path === path);
  if (!list.length) throw new Error('Not in this recording. Run Mission Studio locally to try it (SETUP.md).');
  let e = list[0];
  if (path === 'burn') {                                         // recorded burns: matched by direction and size
    const same = list.filter((x) => x.req.direction === body.direction && Math.abs(x.req.dv_ms - body.dv_ms) < 0.05);
    if (!same.length) throw new Error(`This hosted page replays recorded burns only: ${[...new Set(list.map((x) => `${x.req.dv_ms} m/s ${x.req.direction}`))].join(', ')} (the two goal buttons). Run Mission Studio locally to try any burn (SETUP.md).`);
    e = same.find((x) => x.req.burn_utc === body.burn_utc) || same[0];
  }
  if (body?.vehicle) {
    e = list.reduce((a, x) => (vehDiff(x.req.vehicle, body.vehicle) < vehDiff(a.req.vehicle, body.vehicle) ? x : a));
    const same = vehDiff(e.req.vehicle, body.vehicle) < 0.02 && e.req.site === body.site
      && Math.abs(e.req.payload_kg - body.payload_kg) < 0.01 && Math.abs(e.req.target_alt_km - body.target_alt_km) < 0.01;
    if (!same) throw new Error('This hosted page replays recorded flights: Garage One from Naro (as loaded, then with its fix) and each published rocket as it loads, from its own site. Changed numbers need the simulator: run Mission Studio locally (SETUP.md).');
  }
  // inputs the answer depends on must be the recorded ones: an edited note, sentence or satellite gets a refusal,
  // never the recorded answer to a different question
  const SAME = { target: ['text'], gonogo: ['notes', 'candidates'], deploy: ['name', 'home', 'can_maneuver', 'prop_kg', 'isp_s'],
    lcola: ['start_utc'], dispersion: ['runs', 'wind_utc'] }[path] || [];
  const norm = (v) => JSON.stringify(Array.isArray(v) ? [...v].sort() : typeof v === 'string' ? v.trim() : v ?? null);
  const changed = SAME.filter((k) => body && e.req && k in e.req && norm(body[k]) !== norm(e.req[k]));
  if (changed.length) {
    throw new Error(`This hosted page replays one recorded run, and ${changed.join(', ')} ${changed.length > 1 ? 'differ' : 'differs'} from it, so there is no recorded answer. Put the recorded value back, or run Mission Studio locally to try your own (SETUP.md).`);
  }
  for (const k of ['engine_out_t', 'ours', 'other']) {           // what-if time and encounter pair must match too
    if (body && k in body && e.req && String(e.req[k]) !== String(body[k])) {
      throw new Error(`This hosted page replays one recorded run${k === 'engine_out_t' ? ` (engine out at T+${e.req[k]} s)` : ''}. Run Mission Studio locally to try other cases (SETUP.md).`);
    }
  }
  await sleep(Math.min(2600, 500 + e.seconds * 120));             // a hint of the real wait, shortened
  return structuredClone(e.res);
}
// How long each call usually takes (learned in this browser), for the progress bar
const TYPICAL = { simulate: 25, advise: 45, dispersion: 25, windows: 15, lcola: 12, gonogo: 35, target: 8, sheet: 20, deploy: 35, burn: 25, whatif: 20, encounter: 5 };
try { Object.assign(TYPICAL, JSON.parse(localStorage.getItem('studioTypical') || '{}')); } catch { /* ignore */ }
let pending = null;
async function api(path, body, raw) {
  pending = path;
  const t0 = performance.now();
  try { return await apiCall(path, body, raw); }
  finally {
    if (HAS_API) {
      TYPICAL[path] = Math.round(0.5 * (TYPICAL[path] || 20) + 0.5 * (performance.now() - t0) / 1000);
      try { localStorage.setItem('studioTypical', JSON.stringify(TYPICAL)); } catch { /* ignore */ }
    }
  }
}
async function apiCall(path, body, raw) {
  if (!HAS_API) {
    if (REPLAY && path !== 'presets') return replayApi(path, body);
    throw new Error('Mission Studio simulates on the local server: run web/serve.py and open http://localhost:8765/studio.html');
  }
  const r = await fetch('/api/launch/' + path, raw ? { method: 'POST', headers: { 'Content-Type': raw.type }, body: raw }
    : body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({ error: r.statusText }));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
let busyN = 0;
async function busy(text, fn) {
  busyN++; $('busy').hidden = false;
  pending = null;
  const t0 = performance.now(), tick = () => {
    const el = (performance.now() - t0) / 1000, exp = pending && HAS_API ? TYPICAL[pending] : null;
    const left = exp ? Math.round(exp - el) : null;
    $('busyTxt').textContent = `${text} ${Math.round(el)} s${left == null ? '' : left > 1 ? ` · about ${left} s left` : ' · a little longer than usual'}`;
    $('busyBar').style.width = exp ? `${Math.min(96, 100 * el / exp).toFixed(1)}%` : '0%';
  };
  tick(); const iv = setInterval(tick, 500);
  try { return await fn(); }
  catch (e) { alert(e.message); throw e; }
  finally { clearInterval(iv); if (--busyN === 0) $('busy').hidden = true; }
}
// Plain-words help for the jargon, shown on hover
const GLOSS = {
  LCOLA: 'Launch collision avoidance: checking that the new satellite will not pass too close to anything already in orbit during its first orbits. Each liftoff minute gives a different path.',
  Pc: 'Collision probability: the chance the two objects actually hit, from their predicted miss distance and how uncertain both positions are.',
  'Δv': 'Delta-v: the change of speed the rocket can deliver. Reaching orbit at 500 km needs about 7.6 km/s plus what gravity, drag and steering take.',
  'Max Q': 'Maximum dynamic pressure: the moment the air pushes hardest on the rocket (fast and still low). Structures are sized for it.',
  Perigee: 'The lowest point of the orbit. Below about 150 km the thin air slows the satellite and it falls back within hours or days.',
  LTDN: 'Local time of the descending node: the local clock time when the satellite crosses the equator going south. A sun-synchronous orbit keeps it fixed, so every image of a place has the same lighting.',
  TLE: 'Two-line element set: the public orbit format the whole catalog uses. Good to about a kilometer.',
  CDM: 'Conjunction data message: the precise orbit and uncertainty a screening provider sends when an encounter matters.',
  SSO: 'Sun-synchronous orbit: a near-polar orbit whose plane turns with the Sun, so it passes over each place at the same local time.',
};
const G = (k, text = k) => `<abbr class="g" title="${esc(GLOSS[k])}">${text}</abbr>`;
// LTDN: local time when the orbit crosses the equator going south (the imaging side of a sun-synchronous orbit)
const ltdnOf = (ltan) => (ltan + 12) % 24;
const hm = (h) => `${String(Math.floor(h)).padStart(2, '0')}:${String(Math.round((h % 1) * 60) % 60).padStart(2, '0')}`;
function ltdnTarget() { const [a, b] = ($('ltdnT')?.value || '10:30').split(':').map(Number); return a + (b || 0) / 60; }
const ltdnOk = (ltan) => { const d = Math.abs(((ltdnOf(ltan) - ltdnTarget() + 36) % 24) - 12); return d <= (+$('ltdnTol')?.value || 45) / 60; };


export { HAS_API, REPLAY, NOW, sleep, api, busy, GLOSS, G, ltdnOf, hm, ltdnTarget, ltdnOk };
