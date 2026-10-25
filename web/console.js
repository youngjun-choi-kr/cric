// CRIC console: globe, encounter list, incident stream, eval panel. Loaded by index.html.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { Line2 } from 'three/addons/lines/Line2.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import { LineGeometry } from 'three/addons/lines/LineGeometry.js';

const D2R = Math.PI / 180;
const RE = 6.378137;                      // Earth radius, 1 unit = 1000 km
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const hostOf = (u) => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch { return String(u || ''); } };
const fmtDist = (km) => (km < 1 ? `${(km * 1000).toFixed(0)} m` : `${km.toFixed(2)} km`);
const fmtUTC = (iso) => String(iso).slice(11, 19) + ' UTC';
const pcTxt = (p) => (p > 0 ? p.toExponential(1) : '< 1e-300');

// ---------- data ----------
// Static mode (e.g. GitHub Pages): no API; replay recordings from demo/ and read orbits from the committed snapshot.
const STATIC = !(await fetch('/api/incidents', { method: 'HEAD' }).then((r) => r.ok).catch(() => false));
// Static mode prefers today's data from the `live` branch (refreshed daily by GitHub Actions); ?data=snapshot, a
// cinema render, or live data older than two days falls back to the frozen snapshot the recorded runs belong to.
const LIVE_BASE = 'https://raw.githubusercontent.com/youngjun-choi-kr/cric/live/';
const QS0 = new URLSearchParams(location.search);
let LIVE = null;
if (STATIC && QS0.get('data') !== 'snapshot' && !QS0.get('cinema')) {
  LIVE = await fetch(LIVE_BASE + 'live.json', { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (LIVE && Date.now() - Date.parse(LIVE.generated_utc) > 48 * 3600e3) LIVE = null;
}
const DATA_BASE = LIVE ? LIVE_BASE : STATIC ? '../fixtures/snapshot_20261025/' : '/data/';
const INCIDENT_BASE = LIVE ? LIVE_BASE + 'incidents/' : 'demo/incidents/';
if (STATIC) {
  $('staticNote').hidden = false;
  $('staticNote').innerHTML = LIVE
    ? `Live data: CelesTrak catalog of ${LIVE.generated_utc.slice(0, 16).replace('T', ' ')} UTC, screened for the next ${LIVE.hours} h.
       ${LIVE.incidents.length ? 'Today\'s incident was decided by the real pipeline.' : 'No incident run today.'}
       <a href="?data=snapshot">Recorded demo runs (24–25 Oct) →</a>`
    : `Recorded demo: runs from 24–25 Oct 2026 on the frozen catalog; these encounters have passed.
       ${QS0.get('data') === 'snapshot' ? '<a href="?">Today\'s orbits →</a>' : ''} Live decisions need the local server and keys (SETUP.md).`;
}
const [tleActive, tleDebris, conj, saved] = await Promise.all([
  fetch(DATA_BASE + 'celestrak_active.tle').then(r => r.text()),
  fetch(DATA_BASE + 'celestrak_debris.tle').then(r => (r.ok ? r.text() : '')).catch(() => ''),   // public debris clouds
  fetch(DATA_BASE + 'conjunctions.json').then(r => r.json()),
  LIVE ? Promise.resolve(LIVE.incidents) : fetch(STATIC ? 'demo/manifest.json' : '/api/incidents').then(r => (r.ok ? r.json() : [])).catch(() => []),
]);
const tleText = tleActive + '\n' + tleDebris;
const lines = tleText.split('\n').map(l => l.trimEnd()).filter(Boolean);
const sats = [], bySat = new Map();
for (let i = 0; i + 2 < lines.length; i += 3) {
  const rec = satellite.twoline2satrec(lines[i + 1], lines[i + 2]);
  const s = { name: lines[i].trim(), rec, starlink: lines[i].startsWith('STARLINK') };
  sats.push(s); bySat.set(String(Number(rec.satnum)), s);
}
$('cnt').textContent = sats.length.toLocaleString();
const recorded = new Set(saved.filter(s => s.has_trace).map(s => `${s.ours}-${s.other}`));

// ---------- renderer ----------
const QS = new URLSearchParams(location.search);
const CINEMA = QS.get('cinema') === '1';
const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: QS.has('record') });
if (QS.get('ui')) document.body.style.setProperty('--ui', String(Math.min(2.5, Math.max(0.75, Number(QS.get('ui')) || 1))));
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;
document.body.prepend(renderer.domElement);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(45, innerWidth / innerHeight, 0.01, 3000);
camera.position.set(0, 7, 19);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true; controls.dampingFactor = 0.06;
controls.minDistance = 0.05; controls.maxDistance = 400; controls.zoomSpeed = 0.8;

// ---------- textures, sky, earth, atmosphere ----------
const loader = new THREE.TextureLoader();
const IMG = 'https://unpkg.com/three-globe@2.31.0/example/img/';
const load = (f, srgb = true) => loader.loadAsync(IMG + f).then(t => {
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = renderer.capabilities.getMaxAnisotropy(); return t;
});
const [dayTex, nightTex, skyTex] = await Promise.all([load('earth-blue-marble.jpg'), load('earth-night.jpg'), load('night-sky.png')]);
scene.add(new THREE.Mesh(new THREE.SphereGeometry(1500, 48, 32),
  new THREE.MeshBasicMaterial({ map: skyTex, side: THREE.BackSide, depthWrite: false, color: 0x9aa6c8 })));
const sunU = { value: new THREE.Vector3(1, 0, 0) };
scene.add(new THREE.Mesh(new THREE.SphereGeometry(RE, 128, 96), new THREE.ShaderMaterial({
  uniforms: { dayT: { value: dayTex }, nightT: { value: nightTex }, sunDir: sunU },
  vertexShader: `
    varying vec2 vUv; varying vec3 vN; varying vec3 vW;
    void main(){ vUv=uv; vN=normalize(normal); vec4 w=modelMatrix*vec4(position,1.); vW=w.xyz; gl_Position=projectionMatrix*viewMatrix*w; }`,
  fragmentShader: `
    uniform sampler2D dayT, nightT; uniform vec3 sunDir;
    varying vec2 vUv; varying vec3 vN; varying vec3 vW;
    void main(){
      vec3 N=normalize(vN); vec3 S=normalize(sunDir);
      float d=dot(N,S);
      vec3 day=texture2D(dayT,vUv).rgb, night=texture2D(nightT,vUv).rgb;
      float m=smoothstep(-0.10,0.22,d);
      vec3 lit=day*(0.05+1.25*max(d,0.0));
      vec3 dark=night*vec3(1.5,1.15,0.75)*1.1+day*0.012;
      vec3 col=mix(dark,lit,m);
      float tw=exp(-pow(d/0.10,2.0));
      col+=vec3(1.0,0.38,0.12)*tw*0.16*(0.3+length(day));
      vec3 V=normalize(cameraPosition-vW);
      float f=pow(1.0-max(dot(N,V),0.0),3.2);
      col+=vec3(0.28,0.55,1.0)*f*0.9*clamp(d*1.6+0.45,0.0,1.0);
      gl_FragColor=vec4(col,1.0);
      #include <tonemapping_fragment>
      #include <colorspace_fragment>
    }`,
})));
scene.add(new THREE.Mesh(new THREE.SphereGeometry(RE * 1.035, 96, 64), new THREE.ShaderMaterial({
  uniforms: { sunDir: sunU }, side: THREE.BackSide, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
  vertexShader: `varying vec3 vN; varying vec3 vWN; void main(){ vN=normalize(normalMatrix*normal); vWN=normalize(mat3(modelMatrix)*normal); gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }`,
  fragmentShader: `
    uniform vec3 sunDir; varying vec3 vN; varying vec3 vWN;
    void main(){
      float i=pow(max(0.62-dot(vN,vec3(0.,0.,1.)),0.0),2.6);
      float lit=smoothstep(-0.35,0.45,dot(normalize(vWN),normalize(sunDir)));
      vec3 c=mix(vec3(0.35,0.55,1.0),vec3(1.0,0.55,0.3),smoothstep(0.35,0.0,abs(dot(normalize(vWN),normalize(sunDir)))));
      gl_FragColor=vec4(c*i*2.4*(0.12+lit),1.0);
    }`,
})));

// ---------- satellites ----------
function dotTexture() {
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const g = c.getContext('2d'), gr = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  gr.addColorStop(0, 'rgba(255,255,255,1)'); gr.addColorStop(.4, 'rgba(255,255,255,.85)'); gr.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = gr; g.fillRect(0, 0, 64, 64); return new THREE.CanvasTexture(c);
}
const dot = dotTexture();
const N = sats.length;
const pos = new Float32Array(N * 3), col = new Float32Array(N * 3);
sats.forEach((s, i) => col.set(s.starlink ? [0.38, 0.62, 1.0] : [1.0, 0.82, 0.45], i * 3));
const satGeo = new THREE.BufferGeometry();
satGeo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
satGeo.setAttribute('color', new THREE.BufferAttribute(col, 3));
const satPts = new THREE.Points(satGeo, new THREE.PointsMaterial({ size: 3.2, sizeAttenuation: false, vertexColors: true, map: dot, transparent: true, depthWrite: false, alphaTest: 0.02 }));
satPts.frustumCulled = false; scene.add(satPts);

const pairPos = new Float32Array(6), pairGeo = new THREE.BufferGeometry();
pairGeo.setAttribute('position', new THREE.BufferAttribute(pairPos, 3));
pairGeo.setAttribute('color', new THREE.BufferAttribute(new Float32Array([1, .42, .34, .35, 1, .75]), 3));
const pairPts = new THREE.Points(pairGeo, new THREE.PointsMaterial({ size: 16, sizeAttenuation: false, vertexColors: true, map: dot, transparent: true, depthTest: false, depthWrite: false }));
pairPts.frustumCulled = false; pairPts.visible = false; pairPts.renderOrder = 10; scene.add(pairPts);
const pairLine = new THREE.Line(pairGeo, new THREE.LineBasicMaterial({ color: 0xff6b57, transparent: true, opacity: .9, depthTest: false }));
pairLine.frustumCulled = false; pairLine.visible = false; pairLine.renderOrder = 9; scene.add(pairLine);

const ringGroup = new THREE.Group(); scene.add(ringGroup);   // inertial frame, rotated by -GMST each frame
function ringFor(rec, date, color) {
  const period = 2 * Math.PI / rec.no, pts = [];
  for (let k = 0; k <= 160; k++) {
    const p = satellite.propagate(rec, new Date(date.getTime() + k / 160 * period * 60000)).position;
    if (p) pts.push(new THREE.Vector3(p.x / 1000, p.z / 1000, -p.y / 1000));
  }
  return new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color, transparent: true, opacity: .55 }));
}

// ---------- time & geometry ----------
let simMs = Date.now(), speed = 10;
function sunVector(date) {
  const n = date.getTime() / 86400000 + 2440587.5 - 2451545.0;
  const L = 280.460 + 0.9856474 * n, g = (357.528 + 0.9856003 * n) * D2R;
  const lam = (L + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * D2R, eps = (23.439 - 4e-7 * n) * D2R;
  const x = Math.cos(lam), y = Math.cos(eps) * Math.sin(lam), z = Math.sin(eps) * Math.sin(lam);
  const gm = satellite.gstime(date), c = Math.cos(gm), s = Math.sin(gm);
  return new THREE.Vector3(x * c + y * s, z, -(-x * s + y * c));
}
function updateSats(date) {
  const gm = satellite.gstime(date), c = Math.cos(gm), s = Math.sin(gm);
  for (let i = 0; i < N; i++) {
    const p = satellite.propagate(sats[i].rec, date).position;
    if (!p) { pos[3 * i] = pos[3 * i + 1] = pos[3 * i + 2] = 0; continue; }
    pos[3 * i] = (p.x * c + p.y * s) / 1000; pos[3 * i + 1] = p.z / 1000; pos[3 * i + 2] = -(-p.x * s + p.y * c) / 1000;
  }
  satGeo.attributes.position.needsUpdate = true;
}
const eci = (rec, date) => satellite.propagate(rec, date);

// ---------- selection / camera ----------
let sel = null, selSats = null, offset = new THREE.Vector3(), fly = null, prevMid = new THREE.Vector3();
const ease = (t) => t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
function startFly(goal) { fly = { t: 0, camFrom: camera.position.clone(), tgtFrom: controls.target.clone(), goal }; }
function pairMid(date) {
  const gm = satellite.gstime(date), c = Math.cos(gm), s = Math.sin(gm), v = new THREE.Vector3();
  for (const sat of selSats) {
    const p = eci(sat.rec, date).position; if (!p) continue;
    v.add(new THREE.Vector3((p.x * c + p.y * s) / 1000, p.z / 1000, -(-p.x * s + p.y * c) / 1000));
  }
  return v.multiplyScalar(0.5);
}
function flyToPair() {
  const mid = pairMid(new Date(simMs)), u = mid.clone().normalize();
  const side = new THREE.Vector3().crossVectors(u, new THREE.Vector3(0, 1, 0));
  if (side.lengthSq() < 1e-4) side.set(1, 0, 0);
  side.normalize();
  offset = u.clone().multiplyScalar(1.6).add(side.multiplyScalar(3.4)).add(new THREE.Vector3(0, 0.9, 0));
  startFly(() => { const m = pairMid(new Date(simMs)); return { cam: m.clone().add(offset), tgt: m }; });
}
const evKey = (e) => `${e.object_a.norad_id}-${e.object_b.norad_id}`;
const recordedPair = (e) => recorded.has(evKey(e)) || recorded.has(`${e.object_b.norad_id}-${e.object_a.norad_id}`);
function select(ev, jump = true) {
  const a = bySat.get(String(ev.object_a.norad_id)), b = bySat.get(String(ev.object_b.norad_id));
  if (!a || !b) return;
  sel = ev; selSats = [a, b];
  incReset(); trajClear();
  $('oursSel').innerHTML = selSats.map((x) => `<option value="${Number(x.rec.satnum)}">${esc(x.name)}</option>`).join('');
  if (jump) simMs = Date.parse(ev.tca_utc) - 120000;
  ringGroup.clear();
  ringGroup.add(ringFor(a.rec, new Date(simMs), 0xff6b57), ringFor(b.rec, new Date(simMs), 0x59ffbd));
  pairPts.visible = pairLine.visible = true;
  $('readout').style.display = 'block';
  $('rNames').textContent = `${a.name}  ↔  ${b.name}`;
  document.querySelectorAll('.ev').forEach(el => el.classList.toggle('sel', el.dataset.id === evKey(ev)));
  flyToPair();
}
function overview() {
  sel = null; selSats = null; pairPts.visible = pairLine.visible = false; ringGroup.clear(); incReset(); trajClear();
  $('readout').style.display = 'none';
  document.querySelectorAll('.ev').forEach(el => el.classList.remove('sel'));
  startFly(() => ({ cam: new THREE.Vector3(0, 8, 21), tgt: new THREE.Vector3(0, 0, 0) }));
}

// ---------- conjunction list ----------
function renderList() {
  const inc = $('incStarlink').checked;
  const shown = conj.events.filter(e => inc || !(e.object_a.name.startsWith('STARLINK') && e.object_b.name.startsWith('STARLINK')));
  const rec = shown.filter(e => recordedPair(e));                      // recorded runs first, then the closest
  const evs = [...rec, ...shown.filter(e => !recordedPair(e)).slice(0, 25)];
  $('list').innerHTML = evs.map(e => `
    <div class="ev" data-id="${evKey(e)}">
      <div class="n">${esc(e.object_a.name)} ↔ ${esc(e.object_b.name)}${recordedPair(e) ? '<span class="rec">● recorded</span>' : ''}${e.involves_debris ? '<span class="tag">debris</span>' : ''}</div>
      <div class="m">${Date.parse(e.tca_utc) - Date.now() >= 2 * 3600e3 ? '<span class="tag ok">ahead</span>' : ''}<span class="d">${(e.miss_distance_km * 1000).toFixed(0)} m</span> · ${e.relative_speed_kms.toFixed(1)} km/s · alt ${e.altitude_km.toFixed(0)} km · <span data-gl="tca">TCA</span> ${fmtUTC(e.tca_utc)}</div>
    </div>`).join('');
  document.querySelectorAll('.ev').forEach((el, i) => el.onclick = () => select(evs[i]));
  if (sel) document.querySelector(`.ev[data-id="${evKey(sel)}"]`)?.classList.add('sel');
  return evs;
}
$('incStarlink').onchange = renderList;
document.querySelectorAll('[data-speed]').forEach(b => b.onclick = () => {
  speed = +b.dataset.speed; $('spd').textContent = speed ? speed + '×' : 'paused';
  document.querySelectorAll('[data-speed]').forEach(x => x.classList.toggle('on', x === b));
});
$('btnTca').onclick = () => { if (sel) select(sel, true); };
$('btnOverview').onclick = overview;

// ---------- trajectory (before / after the chosen burn) ----------
const traj = { data: null, k: 200, group: new THREE.Group(), mats: [] };
scene.add(traj.group);
const K_STEPS = [1, 200, 1000];
function trajClear() { traj.group.clear(); traj.data = null; $('trajSec').hidden = true; }
function trajDraw() {
  traj.group.clear(); traj.mats.length = 0;
  const s = traj.data; if (!s) return;
  const t0 = Date.parse(s.t0_utc), nom = [], aft = [];
  for (let i = 0; i < s.maneuvering_nominal.length; i++) {
    const gm = satellite.gstime(new Date(t0 + i * s.step_s * 1000)), c = Math.cos(gm), si = Math.sin(gm);
    const f = (p) => new THREE.Vector3((p[0] * c + p[1] * si) / 1000, p[2] / 1000, -(-p[0] * si + p[1] * c) / 1000);
    const a = f(s.maneuvering_nominal[i]), b = f(s.maneuvering_after[i]);
    nom.push(a); aft.push(a.clone().add(b.sub(a).multiplyScalar(traj.k)));
  }
  const line = (pts, color, op, width) => {
    const geo = new LineGeometry(); geo.setPositions(pts.flatMap((v) => [v.x, v.y, v.z]));
    const mat = new LineMaterial({ color, linewidth: width, transparent: true, opacity: op });
    mat.resolution.set(innerWidth, innerHeight); traj.mats.push(mat);
    const l = new Line2(geo, mat); l.frustumCulled = false; return l;
  };
  traj.group.add(line(nom, 0xff6b57, 0.7, 2.5), line(aft, 0x4fe3a1, 0.95, 3));
  const bi = Math.min(Math.max(Math.round((Date.parse(s.burn_utc) - t0) / 1000 / s.step_s), 0), nom.length - 1);
  const m = new THREE.Points(new THREE.BufferGeometry().setFromPoints([nom[bi]]),
    new THREE.PointsMaterial({ size: 15, sizeAttenuation: false, color: 0xffd34d, map: dot, transparent: true, depthTest: false, depthWrite: false }));
  m.renderOrder = 11; m.frustumCulled = false; traj.group.add(m);
}
$('goBurn').onclick = () => { if (traj.data) { simMs = Date.parse(traj.data.burn_utc) - 60000; flyToPair(); } };
$('kBtn').onclick = () => { traj.k = K_STEPS[(K_STEPS.indexOf(traj.k) + 1) % K_STEPS.length]; $('kBtn').textContent = `Path difference ×${traj.k}`; trajDraw(); };

// ---------- incident pipeline panel ----------
const MODEL_SHORT = (m) => String(m || '').split('/').pop().replace('nemotron-3-super-120b-a12b', 'Nemotron 3 Super').replace('Nemotron-3-Ultra-550b-a55b', 'Nemotron 3 Ultra');
const inc = { es: null, queue: [], timer: null, rows: {}, running: false, mode: 'replay', streamDone: false, dossierStep: {}, tavily: { search: 0, extract: 0, credits: 0 },
  pace: 1, paused: false };
function spotlight(el) { if (!el) return; el.classList.remove('spot'); void el.offsetWidth; el.classList.add('spot'); setTimeout(() => el.classList.remove('spot'), 2700); }
document.querySelectorAll('[data-pace]').forEach((b) => (b.onclick = () => {
  inc.pace = Number(b.dataset.pace);
  document.querySelectorAll('[data-pace]').forEach((x) => x.classList.toggle('on', x === b));
}));
function incStats(extra = '') {
  const t = inc.tavily;
  $('incStats').innerHTML = `Tavily search <b>${t.search}</b> · extract <b>${t.extract}</b> · new credits <b>${t.credits}</b>${extra}`;
}
function incReset() {
  inc.es?.close(); inc.es = null; clearTimeout(inc.timer); inc.timer = null; inc.queue = []; inc.rows = {}; inc.dossierStep = {};
  inc.running = false; inc.streamDone = false; inc.tavily = { search: 0, extract: 0, credits: 0 };
  $('incBody').innerHTML = ''; $('incState').textContent = 'idle'; incStats();
  ['riskSec', 'optSec', 'decSec', 'valSec', 'repSec', 'apprSec'].forEach((id) => ($(id).hidden = true));
  $('runLive').disabled = $('runReplay').disabled = !sel;
  if (STATIC) { $('runLive').disabled = true; $('runLive').title = 'Live runs need the local server (web/serve.py) and API keys'; }
}
function step(key, state, html) {
  let r = inc.rows[key];
  if (!r) { r = document.createElement('div'); r.innerHTML = '<span class="ic"></span><div class="tx"></div>'; $('incBody').appendChild(r); inc.rows[key] = r; }
  r.className = 'step ' + state;
  r.querySelector('.ic').textContent = { run: '◐', ok: '✓', warn: '!', bad: '✗' }[state];
  r.querySelector('.tx').innerHTML = html;
}
const nameOf = (norad) => bySat.get(String(norad))?.name || `NORAD ${norad}`;
function renderRisk(r, c, sw) {
  $('riskSec').hidden = false;
  const screening = r.data_quality === 'tle';      // public TLE data finds candidates; it does not decide burns
  const assumed = /SIMULATED/.test(r.assumption) ? '<span class="tag blue" data-gl="cdm">simulated precise data</span>' : '<span class="tag mid" data-gl="covariance">assumed covariance</span>';
  $('riskBody').innerHTML = `<div class="riskline"><span class="pc ${screening ? 'screen' : r.level}" data-gl="pc">Pc ${pcTxt(r.pc)}</span>
    ${screening ? `<span class="tag mid" data-gl="tle">${r.level.toUpperCase()} ON SCREENING DATA · candidate, not a decision</span>`
                : `<span class="tag ${r.level === 'red' ? 'bad' : r.level === 'yellow' ? 'mid' : 'ok'}">${r.level.toUpperCase()}</span>`}${assumed}
    ${r.diluted ? `<span class="tag mid" data-gl="diluted">diluted · up to ${pcTxt(r.pc_max_if_better_data)} with better data</span>` : ''}

    ${r.sensitivity ? `<span class="tag ${r.sensitivity.robust ? 'ok' : 'mid'}" data-gl="sensitivity">${r.sensitivity.robust ? 'robust' : 'sensitive'} · range ${pcTxt(r.sensitivity.pc_min)}–${pcTxt(r.sensitivity.pc_max)}</span>` : ''}</div>
    <div class="lim">${c ? `${esc(c.data)} · miss ${fmtDist(c.miss_km)} · ${c.hours_to_tca} h to TCA · ` : ''}thresholds: red ≥ 1e-4, yellow ≥ 1e-5${sw ? ` · Kp ${sw.kp}${sw.noaa ? ' (NOAA live)' : ' (scenario)'}` : ''}</div>`;
}
function renderOptions(opts, chosen) {
  $('optSec').hidden = false;
  if (!opts.length) { $('optBody').innerHTML = '<div class="dim">No verified option within limits.</div>'; return; }
  $('optBody').innerHTML = `<table class="t"><tr><th>id</th><th>kind</th><th>direction</th><th data-gl="dv">Δv m/s</th><th>burn</th><th data-gl="rescreen">re-screen</th></tr>
    ${opts.map((o) => `<tr class="${o.id === chosen ? 'me' : ''}"><td>${o.id}</td><td>${esc([o.kind, ...(o.also || [])].join(', '))}</td><td>${esc(o.direction)}</td>
      <td>${o.dv_ms.toFixed(3)}</td><td>${fmtUTC(o.burn_utc)}</td><td>${o.rescreen.objects_checked.toLocaleString()} ok</td></tr>`).join('')}</table>`;
}
function renderDecision(d, model) {
  $('decSec').hidden = false;
  $('decSec').querySelector('h3').textContent = `DECISION · ${MODEL_SHORT(model).toUpperCase()}`;
  $('decBody').innerHTML = `<div><span class="action ${d.action}">${d.action.replace(/_/g, ' ')}</span>${d.option_id ? ` <span class="tag ok">option ${esc(d.option_id)}</span>` : ''}
      <span class="tag">confidence ${Math.round(d.confidence * 100)}%</span></div>
    <div style="margin-top:4px">${esc(d.rationale)}</div>
    ${d.key_factors.map((f) => `<div class="factor">• <b>${esc(f.factor)}</b>: ${esc(f.effect)} <div class="ev2">${esc(f.evidence)}</div></div>`).join('')}
    ${d.message_to_other_operator ? `<div class="msg"><b>To the other operator:</b> ${esc(d.message_to_other_operator)}</div>` : ''}
    ${d.unknowns?.length ? `<div class="lim">Unknowns: ${esc(d.unknowns.join(' · '))}</div>` : ''}`;
}
function renderValidation(v) {
  $('valSec').hidden = false;
  $('valBody').innerHTML = (v.accepted_as_is ? `<span class="tag ok">accepted as is</span>` : `<span class="tag bad">overridden</span>`) +
    ` Final: <b class="action ${v.final_action}" style="font-size:13px">${v.final_action.replace(/_/g, ' ')}</b>${v.final_option_id ? ` · option ${esc(v.final_option_id)}` : ''}` +
    v.overrides.map((o) => `<div class="ovr">${esc(o.from)} → ${esc(o.to)}: ${esc(o.reason)}</div>`).join('') +
    v.warnings.map((w) => `<div class="lim">warning: ${esc(w)}</div>`).join('');
}
function renderReport(r) {
  if (!r) return;
  $('repSec').hidden = false;
  $('repTitle').textContent = `${r.kind === 'coordination_message' ? 'MESSAGE TO THE OTHER OPERATOR' : 'ESCALATION BRIEF FOR THE ON-CALL ENGINEER'} · NEMOTRON ULTRA`;
  $('repBody').innerHTML = `<div><b>${esc(r.subject)}</b></div><div style="white-space:pre-wrap;margin-top:4px;font-size:11.5px">${esc(r.body)}</div>
    ${r.asks?.length ? `<div class="factor"><b>Asks:</b> ${esc(r.asks.join(' · '))}</div>` : ''}
    ${r.decision_deadline_utc ? `<div class="lim">Decision needed by ${esc(r.decision_deadline_utc)}</div>` : ''}`;
}
// After approval: the burn flown in simulation with execution errors, then the conjunction re-checked.
function execBlock(x) {
  const ok = x.verdict === 'cleared';
  return `<div class="exec"><b>Executed in simulation</b> (${x.samples} runs, 1σ errors: ${x.errors_1sigma.magnitude_pct}% Δv,
    ${x.errors_1sigma.pointing_deg}° pointing, ${x.errors_1sigma.timing_s} s timing) · re-checked:
    <div>miss ${fmtDist(x.miss_before_km)} → <b>${fmtDist(x.miss_nominal_km)}</b> planned · worst 5% ${fmtDist(x.miss_p5_km)} · minimum ${fmtDist(x.miss_min_km)}</div>
    <span class="tag ${ok ? 'ok' : 'bad'}">${ok ? 'conjunction cleared' : esc(x.verdict)}</span>
    <span class="lim">${Math.round(x.share_above_clear_km * 100)}% of runs stay beyond ${x.clear_km} km</span></div>`;
}
// The agent proposes; a person approves or rejects. Nothing is commanded from here: CRIC has no uplink.
function renderApproval(e) {
  const v = e.validation, act = v.final_action, opt = (e.options || []).find((o) => o.id === v.final_option_id);
  const upload = e.upload_min ?? 30;
  const what = {
    MANEUVER: opt ? `Approve burn <b>${esc(opt.id)}</b>: ${esc(opt.direction)} ${opt.dv_ms.toFixed(3)} m/s at ${fmtUTC(opt.burn_utc)}` : 'Approve the manoeuvre',
    COORDINATE: 'Send the coordination message to the other operator' + (opt ? ` (our fallback: burn ${esc(opt.id)})` : ''),
    ESCALATE: 'Page the on-call flight dynamics engineer with the brief',
    REQUEST_PRECISE_DATA: 'Request precise ephemeris (CDM) for this conjunction',
    MONITOR: `Acknowledge: keep watching${e.decision?.recheck_at_utc ? `, recheck at ${fmtUTC(e.decision.recheck_at_utc)}` : ''}`,
  }[act] || esc(act);
  let deadline = '';
  if (opt && e.now_utc) {
    const by = Date.parse(opt.burn_utc) - upload * 60e3, left = Math.round((by - Date.parse(e.now_utc)) / 60e3);
    deadline = `Commands must be uploaded by <b>${fmtUTC(new Date(by).toISOString())}</b> (${left} min after the decision time).`;
  }
  const passed = e.pair?.tca_utc && Date.parse(e.pair.tca_utc) < Date.now();
  $('apprSec').hidden = false;
  $('apprBody').innerHTML = `<div class="appr"><div class="what">${what}</div>
    ${deadline ? `<div class="lim">${deadline}</div>` : ''}
    ${passed ? '<div class="lim">Recorded run: this TCA has passed. Your choice is logged, nothing is sent.</div>' : ''}
    <div class="btns"><button id="apprYes">Approve</button><button id="apprNo">Reject…</button></div>
    <textarea id="apprWhy" rows="2" placeholder="Why reject? (required; it goes into the log next to the agent's reasoning)" hidden></textarea>
    <div class="logged" id="apprLog"></div></div>`;
  const send = async (verdict) => {
    const reason = $('apprWhy').value.trim();
    if (verdict === 'reject' && !reason) { $('apprWhy').hidden = false; $('apprWhy').focus(); return; }
    const body = { file: e.file, action: act, option_id: v.final_option_id, verdict, reason, operator: 'console' };
    let res;
    if (STATIC) res = { ...body, logged_utc: new Date().toISOString().slice(0, 19), note: 'static demo: kept in this page only' };
    else {
      const r = await fetch('/api/approval', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      res = r.ok ? await r.json() : { note: `not logged: HTTP ${r.status}` };
    }
    $('apprYes').disabled = $('apprNo').disabled = true;
    $('apprLog').innerHTML = `<span class="tag ${verdict === 'approve' ? 'ok' : 'bad'}">${verdict === 'approve' ? 'approved' : 'rejected'}</span>
      ${esc(res.logged_utc || '')} · ${esc(res.note)}${reason ? `<div class="lim">reason: ${esc(reason)}</div>` : ''}
      <div class="lim">Simulated hand-off. In operations an approved burn goes to the operator's own command system; CRIC never uplinks.</div>
      ${verdict === 'approve' && e.execution ? execBlock(e.execution) : ''}`;
  };
  $('apprYes').onclick = () => send('approve');
  $('apprNo').onclick = () => { if ($('apprWhy').hidden) { $('apprWhy').hidden = false; $('apprWhy').focus(); } else send('reject'); };
}
function finish(ok, msg) {
  inc.running = false; inc.es?.close(); inc.es = null;
  $('incState').textContent = ok ? 'done' : 'error';
  $('runLive').disabled = $('runReplay').disabled = !sel;
  if (STATIC) $('runLive').disabled = true;
  if (!ok) step('err', 'bad', `<b>Error</b> ${esc(msg)}`);
}
function handle(e) {
  const k = e.kind;
  if (k === 'start') { $('incState').textContent = e.mode === 'live' ? 'running live…' : 'replaying…'; return; }
  if (k === 'time_mode') { step('time', 'ok', `<span class="who">Decision time</span> ${esc(e.mode)} · ${fmtUTC(e.now_utc)}`); return; }
  if (k === 'geometry') step('geo', 'ok', `<span class="who">Geometry</span> SGP4 on CelesTrak data · miss ${fmtDist(e.miss_km)} · ${e.rel_speed_kms.toFixed(1)} km/s`);
  else if (k === 'risk') { step('risk', e.level === 'red' ? 'warn' : 'ok', `<span class="who">Collision probability</span> Pc ${pcTxt(e.pc)} · ${e.level}${e.data_quality === 'tle' ? ' on screening data (candidate)' : ''}${e.diluted ? ' · diluted' : ''}`); }
  else if (k === 'optimiser') step('opt', 'run', `<span class="who">Optimiser</span> <span class="sub2">${esc(e.text)}</span>`);
  else if (k === 'options') { step('opt', 'ok', `<span class="who">Optimiser</span> ${e.options.length} verified burn options from ${e.feasible_points ?? '?'} feasible points, each re-screened against the catalog`); renderOptions(e.options); spotlight($('optSec')); }
  else if (k === 'satcat' || k === 'dossier_cached') step('d' + e.norad, 'run', `<span class="who">Dossier · ${esc(e.name)}</span> ${k === 'satcat' ? `SATCAT: ${esc(e.type)}, ${esc(e.status)}` : 'from cache'}`);
  else if (k === 'search_start') step('d' + e.norad, 'run', `<span class="who">Dossier · ${esc(nameOf(e.norad))}</span> Tavily search ${e.index + 1}/${e.total} <span class="sub2">${esc(e.query)}</span>`);
  else if (k === 'search_done') { inc.tavily.search++; inc.tavily.credits += e.credits || 0; incStats();
    inc.dossierStep[e.norad] = [...new Set([...(inc.dossierStep[e.norad] || []), ...(e.results || []).map((r) => hostOf(r.url))])].slice(0, 8); }
  else if (k === 'extract_start') step('d' + e.norad, 'run', `<span class="who">Dossier · ${esc(nameOf(e.norad))}</span> Tavily extract · ${e.urls.length} pages`);
  else if (k === 'extract_done') { inc.tavily.extract++; inc.tavily.credits += e.credits || 0; incStats(); }
  else if (k === 'llm_start') step('d' + e.norad, 'run', `<span class="who">Dossier · ${esc(nameOf(e.norad))}</span> <span class="model">${esc(MODEL_SHORT(e.model))}</span> reading ${e.sources} sources`);
  else if (k === 'dossier_repair') step('rep' + e.norad, 'warn', `<span class="who">Verifier</span> sent ${e.issues.length} issue(s) back for repair <span class="sub2">${esc(e.issues[0])}</span>`);
  else if (k === 'dossier') {
    const rs = e.dossier.research;
    if (rs && !inc.dossierStep[e.dossier.norad_id]) {  // dossier came from cache: show the research it was built from
      inc.tavily.search += rs.search_calls; inc.tavily.extract += rs.extract_calls; incStats();
      inc.dossierStep[e.dossier.norad_id] = [...new Set(rs.sources.map(hostOf))].slice(0, 8);
    }
    const d = e.dossier, cap = d.capability.can_maneuver, f = d.facts?.can_maneuver_now?.status === 'sourced' ? d.facts.can_maneuver_now : d.facts?.has_propulsion;
    step('d' + d.norad_id, cap === 'yes' ? 'ok' : cap === 'no' ? 'ok' : 'warn',
      `<span class="who">Dossier · ${esc(d.name)}</span> <span class="tag">${e.role === 'ours' ? 'ours' : 'other'}</span>${e.role === 'ours' && /demo assumption/.test(d.capability.basis) ? '<span class="tag mid" title="In this demo we play the operator of this satellite; its propulsion state stands in for the operator\'s own telemetry. Not a claim about the real spacecraft or its operator.">demo: we play its operator</span>' : ''} can manoeuvre: <b>${cap}</b>
       <div class="sub2">${esc(d.capability.basis)}${d.web_capability ? ` · web research said: ${esc(d.web_capability.can_maneuver)}` : ''}</div>${f?.quote && f.status === 'sourced' ? `<div class="think">“${esc(f.quote)}” — ${esc(hostOf(f.source_url))}</div>` : ''}
       ${(inc.dossierStep[d.norad_id] || []).length ? `<div class="chips">${inc.dossierStep[d.norad_id].map((h) => `<span class="chip">${esc(h)}</span>`).join('')}</div>` : ''}`);
    spotlight(inc.rows['d' + d.norad_id]?.querySelector('.think') || inc.rows['d' + d.norad_id]);
  }
  else if (k === 'space_weather_start') step('sw', 'run', `<span class="who">Space weather</span> <span class="model">Nemotron 3.5 Super VL</span> reading the Sun (SDO) and NOAA Kp…`);
  else if (k === 'space_weather') step('sw', e.kp >= 5 ? 'warn' : 'ok', `<span class="who">Space weather</span> NOAA Kp <b>${e.kp}</b> sets the covariance · <span class="model">Nemotron 3.5 Super VL</span> image read (context only): drag risk <b>${esc(e.assessment.leo_drag_risk_72h)}</b>
      ${e.disagreement ? `<span class="tag warn">image ≠ NOAA: recheck</span>` : ''}
      <div class="sub2">${esc(e.assessment.orbit_prediction_impact)}${e.disagreement ? ' · ' + esc(e.disagreement.effect) : ''}</div>`);
  else if (k === 'space_weather_error') step('sw', 'warn', `<span class="who">Space weather</span> unavailable, using Kp ${e.kp_used}: ${esc(e.message)}`);
  else if (k === 'context') {
    if (!inc.rows.sw && e.space_weather?.note) {  // scenario weather (e.g. a real storm researched with Tavily)
      step('sw', e.space_weather.kp >= 5 ? 'warn' : 'ok', `<span class="who">Space weather</span> scenario · Kp ${e.space_weather.kp}
        <div class="sub2">${esc(e.space_weather.note)}</div>`);
    }
    renderRisk(e.risk, e.conjunction, e.space_weather); spotlight($('riskSec'));
  }
  else if (k === 'constraints_start') step('con', 'run', `<span class="who">Mission notes</span> <span class="model">${esc(MODEL_SHORT(e.model))}</span> turning the notes into constraints code can enforce…`);
  else if (k === 'constraints') {
    const c = e.constraints || {}, items = [];
    if (c.propulsion_unavailable?.value) items.push('propulsion unavailable');
    (c.burn_blackouts || []).forEach((w) => items.push(`no burns ${fmtUTC(w.start_utc)}–${w.end_utc ? fmtUTC(w.end_utc) : 'until further notice'}`));
    if (c.max_dv_ms) items.push(`max ${c.max_dv_ms.value} m/s`);
    if (c.allowed_axes) items.push(`only ${c.allowed_axes.allowed.join(', ').replace(/_/g, '-')}`);
    if (c.no_commands_after) items.push(`no commands after ${fmtUTC(c.no_commands_after.time_utc)}`);
    if (c.other_operator_will_maneuver?.value) items.push('the other operator will move');
    const quotes = Object.values(c).flat().filter((x) => x && x.quote).map((x) => `“${esc(x.quote)}”`);
    step('con', items.length ? 'warn' : 'ok', `<span class="who">Mission notes</span> ${items.length ? `${items.length} constraint(s), now enforced in code: <b>${esc(items.join(' · '))}</b>` : 'no constraint in force'}
      ${e.excluded?.length ? `<div class="sub2">excluded: ${e.excluded.map((x) => esc(x.id)).join(', ')}</div>` : ''}
      ${quotes.length ? `<div class="think">${quotes.join('<br>')}</div>` : ''}${e.problems?.length ? `<div class="sub2">dropped (quote not in notes): ${e.problems.length}</div>` : ''}`);
  }
  else if (k === 'constraints_error') step('con', 'warn', `<span class="who">Mission notes</span> extraction failed; the decision model still reads them: ${esc(e.message)}`);
  else if (k === 'decision_start') step('dec', 'run', `<span class="who">Decision</span> <span class="model">${esc(MODEL_SHORT(e.model))}</span> weighing risk, options, dossiers, policy and mission notes…`);
  else if (k === 'decision') {
    step('dec', 'ok', `<span class="who">Decision</span> <span class="model">${esc(MODEL_SHORT(e.model))}</span> <b>${e.decision.action.replace(/_/g, ' ')}</b>${e.decision.option_id ? ' · ' + esc(e.decision.option_id) : ''}`);
    if (e.reasoning) $('incBody').insertAdjacentHTML('beforeend', `<details class="think"><summary>model reasoning (excerpt)</summary>${esc(e.reasoning.slice(0, 900))}${e.reasoning.length > 900 ? '…' : ''}</details>`);
    renderDecision(e.decision, e.model); spotlight($('decSec'));
  }
  else if (k === 'validation') { step('val', e.accepted_as_is ? 'ok' : 'warn', `<span class="who">Safety validator</span> ${e.accepted_as_is ? 'accepted' : 'overrode'} → <b>${e.final_action.replace(/_/g, ' ')}</b>`); renderValidation(e); spotlight($('valSec')); }
  else if (k === 'report_start') step('rep', 'run', `<span class="who">Report</span> <span class="model">Nemotron 3 Ultra</span> writing the ${e.for_action === 'COORDINATE' ? 'coordination message' : 'escalation brief'} (runs only when a person must act)`);
  else if (k === 'report') { step('rep', 'ok', `<span class="who">Report</span> <span class="model">Nemotron 3 Ultra</span> ${esc(e.report.subject)}`); renderReport(e.report); spotlight($('repSec')); }
  else if (k === 'report_error') step('rep', 'warn', `<span class="who">Report</span> failed: ${esc(e.message)}`);
  else if (k === 'done') incStats(` · <b>$${e.usage.cost_usd.toFixed(4)}</b> · ${e.usage.calls} model calls · ${e.seconds}s`);
  else if (k === 'result') {
    renderRisk(e.risk, e.conjunction, e.space_weather); renderOptions(e.options, e.validation.final_option_id);
    renderDecision(e.decision, e.model); renderValidation(e.validation); renderReport(e.report); renderApproval(e);
    if (e.trajectory) { traj.data = e.trajectory; $('trajSec').hidden = false; trajDraw(); }
  }
  else if (k === 'error') finish(false, e.message);
  else if (k === 'end') finish(true);
  $('incBody').scrollTop = $('incBody').scrollHeight;
}
function pump() {
  if (inc.timer || inc.paused || !inc.queue.length) return;
  handle(inc.queue.shift());
  inc.timer = setTimeout(() => { inc.timer = null; pump(); }, (inc.mode === 'live' ? 120 : 420) / inc.pace);
}
$('pauseBtn').onclick = () => {
  inc.paused = !inc.paused;
  $('pauseBtn').textContent = inc.paused ? '▶' : '⏸';
  if (!inc.paused) pump();
};
async function staticReplay(ours, other, presetName) {
  const m = saved.find((x) => x.ours === ours && x.other === other && x.preset === presetName);
  if (!m) { inc.queue.push({ kind: 'error', message: 'No recording for this pair and situation in the static demo.' }); pump(); return; }
  const d = await fetch(INCIDENT_BASE + m.file).then((r) => r.json());
  inc.queue.push({ kind: 'start', mode: 'replay' }, ...d.trace, d.result, { kind: 'end' }); pump();
}
function runIncident(mode) {
  if (!sel || inc.running) return;
  const ours = Number($('oursSel').value), other = selSats.map((x) => Number(x.rec.satnum)).find((n) => n !== ours);
  incReset(); trajClear(); inc.running = true; inc.mode = mode; inc.paused = false; $('pauseBtn').textContent = '⏸';
  $('runLive').disabled = $('runReplay').disabled = true;
  if (STATIC) { staticReplay(ours, other, $('presetSel').value); return; }
  const es = inc.es = new EventSource(`/api/incident?ours=${ours}&other=${other}&preset=${$('presetSel').value}&mode=${mode}`);
  es.onmessage = (m) => { const e = JSON.parse(m.data); if (e.kind === 'end' || e.kind === 'error') { inc.streamDone = true; es.close(); } inc.queue.push(e); pump(); };
  es.onerror = () => { es.close(); if (!inc.streamDone) { inc.queue.push({ kind: 'error', message: 'Connection closed or refused (another live run in progress, or the server is not running).' }); pump(); } };
}
$('runLive').onclick = () => runIncident('live');
$('runReplay').onclick = () => runIncident('replay');

// ---------- eval panel ----------
const ARM = { naive_rule: 'Rule of thumb (miss < 1 km)', policy_rule: 'Coded policy, fields only' };
const SET_LABEL = { heldout: 'Held-out', heldout2: 'Held-out 2', tuning: 'Tuning', forged: 'Real events (Tavily)' };
const SET_NOTE = {
  heldout: 'Expected answers committed before any model ran on them.',
  heldout2: 'A second held-out set, committed after the design was frozen and before any model ran on it.',
  tuning: 'Used while designing the validator rules; optimistic.',
  forged: 'Real storms researched with Tavily, replayed as current conditions.',
};
function renderEval(sets, name) {
  const d = sets[name]; if (!d) return;
  const order = (k) => (ARM[k] ? 0 : k.includes('vote3') ? 2 : 1);
  const rows = Object.entries(d.summary).filter(([k, m]) => m && typeof m === 'object').sort((a, b) => order(a[0]) - order(b[0]));
  const label = (k) => ARM[k] || 'Nemotron ' + (k.toLowerCase().includes('super') ? 'Super' : k.includes('Nano') ? 'Nano' : k) + (k.includes('vote3') ? ' · 3 votes (pipeline)' : ' · single call');
  const pct = (x) => (x == null ? '—' : Math.round(x * 100) + '%');
  const ci = (c) => (c ? `${pct(c[0])}–${pct(c[1])}` : '');
  const best = rows.find(([k]) => k.includes('vote3')) || rows.find(([k]) => !ARM[k]);
  const pol = d.summary.policy_rule;
  const headline = best ? `<div class="headline">Scenarios handled correctly: <b>${esc(label(best[0]))} + validator ${best[1].scenarios_passed_after_validator}</b>
    (95% CI ${ci(best[1].scenario_ci95_after_validator)}) · coded policy ${pol?.scenarios_passed_after_validator ?? '—'} ·
    policy-violating burns after validator: <b>${best[1].unsafe_after_validator}</b>
    <div class="lim">Sets are small: intervals are over scenarios, not runs, and overlapping intervals mean the difference is indicative, not proven.</div></div>` : '';
  $('evalBody').innerHTML = `<div class="ctl" style="margin:0 0 6px">${Object.keys(sets).map((k) => `<button class="${k === name ? 'on' : ''}" data-set="${k}">${SET_LABEL[k] || k}</button>`).join('')}</div>
    ${headline}
    <table class="t"><tr><th>arm</th><th>scenarios</th><th>+validator</th><th data-gl="runacc">runs correct</th><th data-gl="stability">stable</th><th data-gl="violating">policy-violating burns</th></tr>
    ${rows.map(([k, m]) => `<tr class="${k === best?.[0] ? 'me' : ''}"><td>${esc(label(k))}</td><td>${m.scenarios_passed_raw}</td>
      <td>${m.scenarios_passed_after_validator}</td><td>${pct(m.accuracy_after_validator)}</td><td>${ARM[k] ? '—' : pct(m.stability_raw)}</td>
      <td>${m.unsafe_raw} → ${m.unsafe_after_validator}</td></tr>`).join('')}</table>
    <div class="lim">${d.scenarios.length} scenarios on real encounters. ${SET_NOTE[name] || ''} A scenario counts as passed when most of its runs are correct. Burn counts are before → after the safety validator.</div>`;
  document.querySelectorAll('[data-set]').forEach((b) => (b.onclick = () => renderEval(sets, b.dataset.set)));
}
fetch(STATIC ? 'demo/eval.json' : '/api/eval').then((r) => (r.ok ? r.json() : null)).then((d) => {
  const sets = d?.sets || {};
  const first = ['heldout2', 'heldout', 'tuning', 'forged'].find((k) => sets[k]);
  if (!first) { $('evalBody').textContent = 'No eval run yet.'; return; }
  renderEval(sets, first);
}).catch(() => ($('evalBody').textContent = 'Eval unavailable.'));

// ---------- loop ----------
let last = performance.now(), lastU = 0;
function stepScene(date, everyFrame = false) {
  if (everyFrame) updateSats(date);
  sunU.value.copy(sunVector(date));
  ringGroup.rotation.y = -satellite.gstime(date);
  $('clock').textContent = date.toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
  if (sel) {
    const gm = satellite.gstime(date), c = Math.cos(gm), s = Math.sin(gm);
    const ps = selSats.map(x => eci(x.rec, date).position);
    if (ps[0] && ps[1]) {
      ps.forEach((p, k) => { pairPos[3 * k] = (p.x * c + p.y * s) / 1000; pairPos[3 * k + 1] = p.z / 1000; pairPos[3 * k + 2] = -(-p.x * s + p.y * c) / 1000; });
      pairGeo.attributes.position.needsUpdate = true;
      const km = Math.hypot(ps[0].x - ps[1].x, ps[0].y - ps[1].y, ps[0].z - ps[1].z);
      $('rDist').textContent = km < 10 ? `${(km * 1000).toFixed(0)} m` : `${km.toFixed(1)} km`;
      const secs = (Date.parse(sel.tca_utc) - simMs) / 1000, a = Math.abs(secs);
      $('rSub').textContent = `TCA ${secs >= 0 ? 'in' : 'passed'} ${Math.floor(a / 60)} min ${Math.floor(a % 60)} s · predicted miss ${(sel.miss_distance_km * 1000).toFixed(0)} m · ${sel.relative_speed_kms.toFixed(1)} km/s · TLE accuracy ~1 km`;
    }
  }
}
function frame(now) {
  if (cinemaOn) return;  // cinema mode drives its own loop
  const dt = Math.min((now - last) / 1000, 0.1); last = now;
  simMs += dt * 1000 * speed;
  const date = new Date(simMs);
  if (now - lastU > (speed >= 60 ? 120 : 260)) { updateSats(date); lastU = now; }
  stepScene(date);
  if (fly) {
    fly.t = Math.min(1, fly.t + dt / 1.8);
    const k = ease(fly.t), g = fly.goal();
    camera.position.lerpVectors(fly.camFrom, g.cam, k); controls.target.lerpVectors(fly.tgtFrom, g.tgt, k);
    if (fly.t >= 1) { prevMid = sel ? pairMid(date) : new THREE.Vector3(); fly = null; }
  } else if (sel) {
    const mid = pairMid(date), delta = mid.clone().sub(prevMid);
    camera.position.add(delta); controls.target.add(delta); prevMid = mid;
  }
  controls.update();
  renderer.render(scene, camera);
  requestAnimationFrame(frame);
}
addEventListener('resize', () => { camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix(); renderer.setSize(innerWidth, innerHeight); traj.mats.forEach((m) => m.resolution.set(innerWidth, innerHeight)); });

function placeLeftcol() { $('leftcol').style.top = ($('hud').getBoundingClientRect().bottom + 8) + 'px'; }
addEventListener('resize', placeLeftcol); placeLeftcol();
window.cric = { THREE, scene, camera, controls, traj, inc, spotlight };  // debug hook
updateSats(new Date(simMs));
const evs = renderList();
$('loading').classList.add('done'); setTimeout(() => $('loading').remove(), 800);
const first = evs.find((e) => recordedPair(e)) || evs[0];
if (first) select(first);
{ const { initGlossary, initTour } = await import('./tour.js'); initGlossary(); initTour(); }

// ---------- cinema mode ----------
let cinemaOn = false;
// Our satellite on the post-burn path, with the same exaggeration the drawn green line uses.
function burnPathPosition(date) {
  const s = traj.data; if (!s) return null;
  const t0 = Date.parse(s.t0_utc), x = (date.getTime() - t0) / 1000 / s.step_s;
  const i = Math.max(0, Math.min(s.maneuvering_nominal.length - 2, Math.floor(x))), f = Math.min(1, Math.max(0, x - i));
  const lerp3 = (a, b) => [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
  const nom = lerp3(s.maneuvering_nominal[i], s.maneuvering_nominal[i + 1]);
  const aft = lerp3(s.maneuvering_after[i], s.maneuvering_after[i + 1]);
  const p = nom.map((v, k) => v + (aft[k] - v) * traj.k);              // TEME km
  const gm = satellite.gstime(date), c = Math.cos(gm), sn = Math.sin(gm);
  return new THREE.Vector3((p[0] * c + p[1] * sn) / 1000, p[2] / 1000, -(-p[0] * sn + p[1] * c) / 1000);
}
async function loadTrajectory(ours, other, presetName) {
  try {
    const man = await fetch('demo/manifest.json').then((r) => (r.ok ? r.json() : []));
    const m = man.find((x) => x.ours === ours && x.other === other && x.preset === presetName);
    if (!m) return null;
    return (await fetch(`demo/incidents/${m.file}`).then((r) => r.json())).result?.trajectory || null;
  } catch { return null; }
}
if (CINEMA && first) {
  cinemaOn = true;
  const [a, b] = [first.object_a.norad_id, first.object_b.norad_id];
  const t = await loadTrajectory(a, b, QS.get('burn') || 'imaging');
  if (t) { traj.data = t; trajDraw(); }
  const { initCinema } = await import('./cinema.js');
  initCinema({
    THREE, camera, controls, renderer, scene, $,
    pairMid, selected: () => sel, trajectory: () => traj.data,
    ourPosition(date) {
      const sat = selSats?.[0]; const p = sat && satellite.propagate(sat.rec, date).position; if (!p) return null;
      const gm = satellite.gstime(date), c = Math.cos(gm), s = Math.sin(gm);
      return new THREE.Vector3((p.x * c + p.y * s) / 1000, p.z / 1000, -(-p.x * s + p.y * c) / 1000);
    },
    setSim(ms) { simMs = ms; }, stepScene,
    showTrajectory(on) { traj.group.visible = on; },
    pairLineIfClose(km) {
      const d = Math.hypot(pairPos[0] - pairPos[3], pairPos[1] - pairPos[4], pairPos[2] - pairPos[5]) * 1000;
      pairLine.visible = d < km;
    },
    burnPosition(date) { return burnPathPosition(date); },
    moveOurMarkerOnBurnPath(date) {
      const p = burnPathPosition(date); if (!p) return;
      pairPos[0] = p.x; pairPos[1] = p.y; pairPos[2] = p.z; pairGeo.attributes.position.needsUpdate = true;
    },
    scalePoints(k) { satPts.material.size = 3.2 * Math.max(1, k); pairPts.material.size = 16 * Math.max(1, k); },
    setLineResolution(w, h) { traj.mats.forEach((m) => m.resolution.set(w, h)); },
    cinemaActive: () => cinemaOn,
    exitCinema() { cinemaOn = false; document.body.classList.remove('cinema', 'cinema-ui'); $('cine')?.remove(); last = performance.now(); requestAnimationFrame(frame); },
  });
}
requestAnimationFrame(frame);
