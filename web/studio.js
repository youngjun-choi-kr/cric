// CRIC Mission Studio: 1 your rocket -> 2 your launch window -> 3 your satellite, operated by CRIC.
// All physics runs on the local server (cric.launch); this page draws it on the same globe as the console.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { createCloseView } from './launch3d/closeview.js';

const $ = (id) => document.getElementById(id);
const RE = 6.378137;                                   // 1 unit = 1000 km, as in the console
const D2R = Math.PI / 180;
const fmt = (x, d = 0) => (x == null || !isFinite(x) ? '–' : Number(x).toLocaleString('en-US', { maximumFractionDigits: d, minimumFractionDigits: d }));
const tplus = (t) => `T+${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
const hhmm = (iso) => iso.slice(11, 16);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const llv = (lat, lon, alt_km = 0) => {                 // geodetic-ish lat/lon -> scene (x = X, y = Z, z = -Y)
  const r = RE + alt_km / 1000, la = lat * D2R, lo = lon * D2R;
  return new THREE.Vector3(r * Math.cos(la) * Math.cos(lo), r * Math.sin(la), -r * Math.cos(la) * Math.sin(lo));
};

// ---------- API ----------
const HAS_API = await fetch('/api/incidents', { method: 'HEAD' }).then((r) => r.ok).catch(() => false);
if (!HAS_API) $('serverNote').hidden = false;
async function api(path, body, raw) {
  if (!HAS_API) throw new Error('Mission Studio simulates on the local server: run web/serve.py and open http://localhost:8765/studio.html');
  const r = await fetch('/api/launch/' + path, raw ? { method: 'POST', headers: { 'Content-Type': raw.type }, body: raw }
    : body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({ error: r.statusText }));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
let busyN = 0;
async function busy(text, fn) {
  busyN++; $('busy').hidden = false; $('busyTxt').textContent = text;
  try { return await fn(); }
  catch (e) { alert(e.message); throw e; }
  finally { if (--busyN === 0) $('busy').hidden = true; }
}

// ---------- globe ----------
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
document.body.prepend(renderer.domElement);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(45, innerWidth / innerHeight, 0.01, 3000);
camera.position.set(0, 8, 20);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true; controls.minDistance = 6.6; controls.maxDistance = 120;
addEventListener('resize', () => { camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix(); renderer.setSize(innerWidth, innerHeight); });
const IMG = 'https://unpkg.com/three-globe@2.31.0/example/img/';
const loader = new THREE.TextureLoader();
const load = (f) => loader.loadAsync(IMG + f).then((t) => { t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8; return t; });
const [dayTex, nightTex, skyTex] = await Promise.all([load('earth-blue-marble.jpg'), load('earth-night.jpg'), load('night-sky.png')]);
const CV = createCloseView($('close'), { earthTex: dayTex });   // the close-up stage: hangar, launch, deploy
let globeOn = true;                                              // the globe pauses while the close-up covers it
scene.add(new THREE.Mesh(new THREE.SphereGeometry(1500, 48, 32), new THREE.MeshBasicMaterial({ map: skyTex, side: THREE.BackSide, depthWrite: false, color: 0x8090b8 })));
const sunU = { value: new THREE.Vector3(1, 0, 0) };
function setSun(date) {                                   // subsolar point, good to a degree
  const doy = (date - Date.UTC(date.getUTCFullYear(), 0, 0)) / 864e5;
  const decl = -23.44 * Math.cos((2 * Math.PI / 365) * (doy + 10));
  const lon = -15 * (date.getUTCHours() + date.getUTCMinutes() / 60 - 12);
  sunU.value.copy(llv(decl, lon).normalize());
}
setSun(new Date());
scene.add(new THREE.Mesh(new THREE.SphereGeometry(RE, 128, 96), new THREE.ShaderMaterial({
  uniforms: { dayT: { value: dayTex }, nightT: { value: nightTex }, sunDir: sunU },
  vertexShader: `varying vec2 vUv; varying vec3 vN; varying vec3 vW;
    void main(){ vUv=uv; vN=normalize(normal); vec4 w=modelMatrix*vec4(position,1.); vW=w.xyz; gl_Position=projectionMatrix*viewMatrix*w; }`,
  fragmentShader: `uniform sampler2D dayT, nightT; uniform vec3 sunDir; varying vec2 vUv; varying vec3 vN; varying vec3 vW;
    void main(){ vec3 N=normalize(vN); float d=dot(N,normalize(sunDir));
      vec3 day=texture2D(dayT,vUv).rgb, night=texture2D(nightT,vUv).rgb;
      vec3 col=mix(night*1.4+day*0.06, day*(0.18+1.1*max(d,0.0)), smoothstep(-0.1,0.25,d));
      vec3 V=normalize(cameraPosition-vW); col+=vec3(0.28,0.55,1.0)*pow(1.0-max(dot(N,V),0.0),3.0)*0.8;
      gl_FragColor=vec4(col,1.0);
      #include <tonemapping_fragment>
      #include <colorspace_fragment>
    }`,
})));
const layer = { flight: new THREE.Group(), disp: new THREE.Group(), sites: new THREE.Group(), orbit: new THREE.Group() };
Object.values(layer).forEach((g) => scene.add(g));
function dotTex() {
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const g = c.getContext('2d'), gr = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  gr.addColorStop(0, '#fff'); gr.addColorStop(0.4, 'rgba(255,255,255,.85)'); gr.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = gr; g.fillRect(0, 0, 64, 64); return new THREE.CanvasTexture(c);
}
const DOT = dotTex();
const points = (vecs, color, size = 4) => {
  const g = new THREE.BufferGeometry().setFromPoints(vecs);
  return new THREE.Points(g, new THREE.PointsMaterial({ color, size, sizeAttenuation: false, map: DOT, transparent: true, depthWrite: false }));
};
const line = (vecs, color, opacity = 1) => new THREE.Line(new THREE.BufferGeometry().setFromPoints(vecs),
  new THREE.LineBasicMaterial({ color, transparent: opacity < 1, opacity }));
const clear = (g) => { while (g.children.length) { const c = g.children.pop(); c.geometry?.dispose(); c.material?.dispose(); } };

// HTML labels that follow points on the globe
const labels = [];
function label(text, pos, cls) { const el = document.createElement('div'); el.className = 'lbl ' + cls; el.textContent = text; document.body.append(el); labels.push({ el, pos, group: cls }); }
function dropLabels(cls) { for (let i = labels.length - 1; i >= 0; i--) if (labels[i].group === cls) { labels[i].el.remove(); labels.splice(i, 1); } }
const tmpV = new THREE.Vector3();
function placeLabels() {
  const camDir = camera.position.clone().normalize();
  for (const l of labels) {
    const vis = l.pos.clone().normalize().dot(camDir) > 0.15;
    tmpV.copy(l.pos).project(camera);
    l.el.style.display = vis && tmpV.z < 1 ? '' : 'none';
    l.el.style.left = ((tmpV.x + 1) / 2 * innerWidth) + 'px'; l.el.style.top = ((1 - tmpV.y) / 2 * innerHeight) + 'px';
  }
}
let camGoal = null;
function lookAt(lat, lon, dist = 13) { camGoal = llv(lat, lon).normalize().multiplyScalar(dist); }

// ---------- state ----------
const P = await (HAS_API ? api('presets') : fetch('launch/presets.json').then((r) => r.json()));
const S = { method: 'preset', vehicle: null, prov: {}, flight: null, guidance: null, fixes: null, site: 'naro', target: null, cands: [], liftoff: null, lcola: null, deployed: null };
const VAL = P.validation;                               // keyed by vehicle name

// ---------- step navigation ----------
function showStep(n) {
  document.querySelectorAll('#steps button').forEach((b) => b.classList.toggle('on', b.dataset.step === String(n)));
  document.querySelectorAll('[data-for]').forEach((el) => { el.hidden = el.dataset.for !== String(n) || (el.id === 'right1' && !S.flight); });
  layer.flight.visible = layer.disp.visible = n !== 3;
  layer.orbit.visible = n === 3;
  if (n === 3) dropLabels('imp');
  if (n === 2 && !S.heat) loadHeat();
  catPts.visible = n === 3;
  if (n !== 1) { CV.hide(); globeOn = true; document.body.classList.remove('flying'); }
  else if (view === 'bay' && !document.body.classList.contains('flying')) showBay();
}
let view = 'bay';
function setView(v, apply = true) {
  view = v;
  document.querySelectorAll('#viewSw button').forEach((b) => b.classList.toggle('on', b.dataset.v === v));
  if (!apply) return;
  if (v === 'bay') showBay(); else { CV.hide(); globeOn = true; }
}
function landed() { document.body.classList.remove('flying'); setView('globe', false); }
document.querySelectorAll('#viewSw button').forEach((b) => b.addEventListener('click', () => setView(b.dataset.v)));
function insets() {                 // the hangar centres between the panels that are open
  const w = (id) => { const el = $(id); return el && !el.hidden ? el.getBoundingClientRect().right + 8 : 0; };
  const rw = (id) => { const el = $(id); return el && !el.hidden ? innerWidth - el.getBoundingClientRect().left + 8 : 0; };
  CV.setInsets(innerWidth > 900 ? w('left1') : 0, innerWidth > 900 ? rw('right1') : 0);
}
addEventListener('resize', insets);
function showBay() {
  insets();
  const id = P.vehicles[S.vehicle.id] ? S.vehicle.id : S.vehicle.id || 'custom';
  CV.showBay(P.vehicles, id, S.vehicle, +$('payload').value || 5);
  globeOn = false;
}
let bayT = null;
function vehicleEdited() {          // the hangar shows each edit (debounced while a slider moves)
  clearTimeout(bayT);
  bayT = setTimeout(() => { if (CV.mode === 'bay') CV.updateCurrent(S.vehicle, +$('payload').value || 5); }, 120);
}
document.querySelectorAll('#steps button').forEach((b) => b.addEventListener('click', () => showStep(+b.dataset.step)));
const unlock = (n) => { document.querySelector(`#steps button[data-step="${n}"]`).disabled = false; };

// ---------- step 1: rocket input ----------
for (const [id, v] of Object.entries(P.vehicles)) $('presetSel').add(new Option(`${v.name}${id === 'garage' ? '  · fictional first rocket' : ''}`, id));
for (const [id, s] of Object.entries(P.sites)) $('siteSel').add(new Option(s.name, id));
$('presetSel').value = 'garage'; $('siteSel').value = 'naro';
document.querySelectorAll('#methodTabs button').forEach((b) => b.addEventListener('click', () => setMethod(b.dataset.m)));
function setMethod(m) {
  S.method = m;
  document.querySelectorAll('#methodTabs button').forEach((b) => b.classList.toggle('on', b.dataset.m === m));
  document.querySelectorAll('[data-mp]').forEach((el) => { el.hidden = el.dataset.mp !== m; });
  if (m === 'form') renderForm();
}
function loadPreset(id) {
  const v = structuredClone(P.vehicles[id]);
  S.vehicle = v;
  S.prov = { payload_kg: id === 'garage' ? 'assumed' : 'published' };
  $('payload').value = v.payload_kg;
  const val = VAL[v.name];
  $('presetCard').innerHTML = `<div class="row"><b>${esc(v.name)}</b><span>${fmt(v.glow_kg / 1000, 1)} t at liftoff · ${v.stages.length} stages</span></div>
    <div class="dim small">${esc(v.note)}</div>
    ${v.published?.payload_kg ? `<div class="row small mt"><span>Published</span><span>${fmt(v.published.payload_kg)} kg to ${esc(v.published.orbit)}</span></div>` : ''}
    ${val ? `<div class="row small"><span>This model</span><span>${fmt(val.model_payload_kg)} kg <span class="tag ${Math.abs(val.error_pct) <= 10 ? 'ok' : 'mid'}">${val.error_pct > 0 ? '+' : ''}${val.error_pct}%</span></span></div>
       <div class="dim small">Model check: largest payload our 3-DOF ascent places in the published orbit from ${esc(val.site)}. Conservative by design.</div>` : ''}
    ${v.sources?.length ? `<div class="small mt">${v.sources.map((u) => `<a href="${esc(u)}" target="_blank">source</a>`).join(' · ')}</div>` : ''}
    <div class="small mt"><a href="#" id="editPreset">Edit these numbers →</a></div>`;
  $('editPreset').onclick = (e) => { e.preventDefault(); setMethod('form'); };
  if (v.published?.orbit_alt_km) { $('alt').value = v.published.orbit_alt_km; }
  updateIncl();
  if (view === 'bay' && !document.body.classList.contains('flying')) showBay();
}
$('presetSel').addEventListener('change', () => loadPreset($('presetSel').value));

const FIELDS = [['prop_kg', 'Propellant kg', 1], ['dry_kg', 'Dry kg', 1], ['thrust_vac_n', 'Thrust kN', 1e-3], ['isp_vac_s', 'Isp vac s', 1], ['isp_sl_s', 'Isp SL s', 1]];
const provOf = (st, k) => {
  const s = (st.source || {})[k] || 'assumed';
  return /edited/.test(s) ? 'edited' : /missing/.test(s) ? 'missing' : /^read/.test(s) ? 'read' : /^published/.test(s) ? 'published' : /^derived/.test(s) ? 'derived' : 'assumed';
};
function renderForm() {
  const v = S.vehicle;
  const pay = +$('payload').value || 0;
  let h = `<div class="grid2"><label>Name<input id="fName" value="${esc(v.name)}"></label><label>Diameter m<input id="fDiam" type="number" step="0.1" value="${v.diameter_m}"></label>
    <label>Fairing kg<input id="fFair" type="number" value="${v.fairing_kg}"></label><label>Payload kg <span class="prov ${S.prov.payload_kg || 'assumed'}">${S.prov.payload_kg || 'assumed'}</span><input id="fPay" type="number" value="${pay}"></label></div>
    <div class="slid"><span>Payload</span><input type="range" id="sPay" min="0" max="${Math.max(50, Math.round(pay * 3))}" value="${pay}"><b id="sPayV">${fmt(pay)} kg</b></div>`;
  v.stages.forEach((st, i) => {
    h += `<div class="stage" data-i="${i}"><div class="hd"><span>${esc(st.name)}</span><label class="chk" style="margin:0"><input type="checkbox" data-solid ${st.solid ? 'checked' : ''}> solid</label></div><div class="f">`;
    for (const [k, lab, sc] of FIELDS) {
      const p = provOf(st, k);
      h += `<label>${lab} <span class="prov ${p}" title="${esc((st.source || {})[k] || '')}">${p}</span><input type="number" data-k="${k}" value="${st[k] == null ? '' : +(st[k] * sc).toFixed(sc < 1 ? 2 : 1)}"></label>`;
    }
    h += `</div><div class="slid"><span>Propellant ×</span><input type="range" data-scale="prop" min="50" max="200" value="100"><b>100%</b></div>
      <div class="slid"><span>Thrust ×</span><input type="range" data-scale="thrust" min="50" max="200" value="100"><b>100%</b></div></div>`;
  });
  $('vehForm').innerHTML = h;
  const markEdited = (st, k) => { st.source = { ...(st.source || {}), [k]: 'edited' }; };
  $('fName').oninput = (e) => { v.name = e.target.value; };
  $('fDiam').oninput = (e) => { v.diameter_m = +e.target.value; };
  $('fFair').oninput = (e) => { v.fairing_kg = +e.target.value; };
  const setPay = (x) => { $('payload').value = x; $('fPay').value = x; $('sPay').value = x; $('sPayV').textContent = fmt(x) + ' kg'; S.prov.payload_kg = 'edited'; };
  $('fPay').oninput = (e) => setPay(+e.target.value);
  $('sPay').oninput = (e) => setPay(+e.target.value);
  $('vehForm').querySelectorAll('.stage').forEach((el) => {
    const st = v.stages[+el.dataset.i];
    const base = { prop_kg: st.prop_kg, dry_kg: st.dry_kg, thrust_vac_n: st.thrust_vac_n };
    el.querySelectorAll('input[data-k]').forEach((inp) => inp.addEventListener('input', () => {
      const sc = FIELDS.find((f) => f[0] === inp.dataset.k)[2];
      st[inp.dataset.k] = inp.value === '' ? null : +inp.value / sc; markEdited(st, inp.dataset.k);
      inp.previousElementSibling.className = 'prov edited'; inp.previousElementSibling.textContent = 'edited';
      vehicleEdited();
    }));
    el.querySelector('[data-solid]').onchange = (e) => { st.solid = e.target.checked; };
    el.querySelectorAll('input[data-scale]').forEach((r) => r.addEventListener('input', () => {
      const f = +r.value / 100; r.nextElementSibling.textContent = r.value + '%';
      if (r.dataset.scale === 'prop') { st.prop_kg = base.prop_kg * f; st.dry_kg = base.dry_kg * f; markEdited(st, 'prop_kg'); markEdited(st, 'dry_kg'); }
      else { st.thrust_vac_n = base.thrust_vac_n * f; markEdited(st, 'thrust_vac_n'); }
      vehicleEdited();
      for (const k of ['prop_kg', 'dry_kg', 'thrust_vac_n']) {
        const inp = el.querySelector(`input[data-k="${k}"]`), sc = FIELDS.find((x) => x[0] === k)[2];
        inp.value = +(st[k] * sc).toFixed(sc < 1 ? 2 : 1);
      }
    }));
  });
}

// spec sheet upload
const drop = $('drop');
['dragover', 'dragenter'].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach((e) => drop.addEventListener(e, () => drop.classList.remove('over')));
drop.addEventListener('drop', (ev) => { ev.preventDefault(); if (ev.dataTransfer.files[0]) readSheet(ev.dataTransfer.files[0]); });
$('sheetFile').addEventListener('change', (e) => e.target.files[0] && readSheet(e.target.files[0]));
async function readSheet(file) {
  $('sheetStatus').innerHTML = `Reading <b>${esc(file.name)}</b>…`;
  const r = await busy(`Reading the spec sheet (${file.type === 'application/pdf' ? 'Nemotron 3 Super' : 'Nemotron 3.5 Super VL'})…`, () => api('sheet', null, file));
  const v = r.vehicle;
  if (v.missing.length) {             // fill what cannot be run without, and say so
    for (const st of v.stages) for (const k of ['prop_kg', 'dry_kg', 'thrust_vac_n']) if (st[k] == null) { st[k] = k === 'dry_kg' ? (st.prop_kg || 100) * 0.12 : k === 'prop_kg' ? 1000 : 20000; }
  }
  S.vehicle = { ...v, published: {}, sources: [], note: v.notes || 'read from a spec sheet' };
  S.prov = { payload_kg: v.source_overall.payload_kg };
  $('payload').value = v.payload_kg;
  const counts = {};
  v.stages.forEach((st) => Object.keys(st.source).forEach((k) => { const p = provOf(st, k); counts[p] = (counts[p] || 0) + 1; }));
  $('sheetStatus').innerHTML = `<div class="card"><b>${esc(v.name)}</b> · ${v.stages.length} stages · ${Object.entries(counts).map(([k, n]) => `<span class="prov ${k}">${n} ${k}</span>`).join(' ')}
    ${v.missing.length ? `<div class="small" style="color:var(--warn)">Not on the sheet: ${esc(v.missing.join(', '))}. Placeholder values were put in; check them in the form.</div>` : ''}
    ${v.notes ? `<div class="small dim">${esc(v.notes)}</div>` : ''}<div class="small dim">${r.usage.calls} model call · $${r.usage.cost_usd.toFixed(4)}</div></div>`;
  setMethod('form');
  setView('bay');
}

// target orbit
function updateIncl() {
  S.heat = null;
  const k = $('orbitKind').value, alt = +$('alt').value;
  $('incl').disabled = k !== 'inclination';
  if (k === 'sso') $('incl').value = ssoIncl(alt).toFixed(2);
  if (k === 'polar') $('incl').value = 90;
  if (k === 'iss') $('incl').value = 51.6;
  updateAz();
}
function ssoIncl(alt) {
  const a = 6378137 + alt * 1000, n = Math.sqrt(3.986004418e14 / a ** 3);
  return Math.acos(-(2 * 1.99106e-7) / (3 * 1.08263e-3 * (6378137 / a) ** 2 * n)) / D2R;
}
function azimuths(lat, incl) {
  const c = Math.cos(incl * D2R) / Math.cos(lat * D2R);
  if (Math.abs(c) > 1) return [];
  const a = Math.asin(c) / D2R;
  return [...new Set([((a % 360) + 360) % 360, ((180 - a) % 360 + 360) % 360])].sort((x, y) => x - y);
}
const inCorr = (site, az) => site.corridors.some(([lo, hi]) => lo <= az && az <= hi);
function updateAz() {
  const s = P.sites[$('siteSel').value], azs = azimuths(s.lat, +$('incl').value), ok = azs.filter((a) => inCorr(s, a));
  $('azNote').innerHTML = ok.length ? `Launch azimuth <b>${ok[0].toFixed(1)}°</b> · inside the site's safe corridor (${s.corridors.map((c) => c.join('–')).join(', ')}°)`
    : `<span style="color:var(--mid)">${azs.length ? `This inclination needs ${azs.map((a) => a.toFixed(0) + '°').join(' or ')}, outside the corridor; it will fly the corridor edge.` : 'This inclination is lower than the site latitude.'}</span>`;
  drawSites();
}
['alt', 'orbitKind', 'incl'].forEach((id) => $(id).addEventListener('input', updateIncl));
$('siteSel').addEventListener('change', () => { S.site = $('siteSel').value; updateAz(); const s = P.sites[S.site]; lookAt(s.lat, s.lon); });
$('targetBtn').addEventListener('click', async () => {
  const text = $('targetText').value.trim(); if (!text) return;
  const r = await busy('Reading your mission (Nemotron Nano)…', () => api('target', { text }));
  const t = r.target; S.target = t;
  $('alt').value = t.altitude_km; $('orbitKind').value = t.kind === 'equatorial' ? 'inclination' : t.kind;
  if (t.payload_kg) { $('payload').value = t.payload_kg; S.prov.payload_kg = 'read'; }
  updateIncl(); if (t.kind === 'inclination' || t.kind === 'equatorial') $('incl').value = t.inclination_deg;
  updateAz();
  const nm = text.match(/\b([A-Z][A-Z0-9]+-\d+)\b/);
  if (nm) $('satName').value = nm[1];
  if (t.home) { $('homeName').value = t.home; if (t.home_lat != null) $('homeLL').value = `${t.home_lat}, ${t.home_lon}`; }
  if (t.mission) $('satMission').value = t.mission;
  $('targetStatus').innerHTML = `${fmt(t.altitude_km)} km · ${t.inclination_deg}° (${esc(t.inclination_source)})${t.payload_kg ? ` · ${t.payload_kg} kg` : ''}${t.home ? ` · home ${esc(t.home)}` : ''}`;
});

function drawSites() {
  clear(layer.sites); dropLabels('site');
  const cur = $('siteSel').value;
  for (const [id, s] of Object.entries(P.sites)) {
    const p = llv(s.lat, s.lon, 5);
    layer.sites.add(points([p], id === cur ? 0x5ab0ff : 0x6d7fa8, id === cur ? 9 : 5));
    if (id === cur) {
      label(s.name, p, 'site');
      for (const [lo, hi] of s.corridors) {              // the safe corridor as a fan on the ground
        const fan = [p];
        for (let a = lo; a <= hi; a += 2) fan.push(llv(...dest(s.lat, s.lon, a, 900), 2));
        fan.push(p);
        layer.sites.add(line(fan, 0x5ab0ff, 0.5));
      }
    }
  }
}
function dest(lat, lon, brg, km) {
  const d = km / 6371, la = lat * D2R, b = brg * D2R;
  const la2 = Math.asin(Math.sin(la) * Math.cos(d) + Math.cos(la) * Math.sin(d) * Math.cos(b));
  const lo2 = lon * D2R + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(la), Math.cos(d) - Math.sin(la) * Math.sin(la2));
  return [la2 / D2R, lo2 / D2R];
}

// ---------- step 1: fly ----------
const mission = (extra = {}) => ({
  vehicle: S.vehicle, payload_kg: +$('payload').value, site: $('siteSel').value, target_alt_km: +$('alt').value,
  incl_deg: +$('incl').value, guidance: S.guidance, ...extra,
});
$('flyBtn').addEventListener('click', fly);
async function fly() {
  S.guidance = null; S.fixes = null;
  const r = await busy('Optimising the ascent guidance and flying it (differential evolution, parallel)…', () => api('simulate', mission()));
  S.flight = r.flight; S.guidance = r.guidance; S.az = r.azimuth_deg;
  S.lcola = null; S.cands = [];
  $('fixBox').innerHTML = ''; $('dispBox').innerHTML = ''; clear(layer.disp);
  document.body.classList.add('flying');
  renderFlight(r);
  showStep(1);
  if (S.flight.target_reached) { unlock(2); }
  launch3d(r);
}
// Launch in close-up; the globe takes over above ~150 km, and gets the deploy back in orbit.
function launch3d(r) {
  const f = r.flight;
  document.body.classList.add('flying');
  globeOn = false;
  if (anim) { anim.playing = false; anim.trail.geometry.setDrawRange(0, 0); }
  CV.playLaunch(f, r.vehicle, r.site, {
    onHandoff: (tH) => {
      globeOn = true;
      const k = Math.max(0, f.samples.findIndex((x) => x[0] >= tH));
      const sp = f.samples[k];
      camera.position.copy(llv(sp[4], sp[5], sp[1]).multiplyScalar(1.004).add(llv(sp[4], sp[5], 0).normalize().multiplyScalar(0.25)));
      controls.target.set(0, 0, 0);
      camGoal = llv(sp[4], sp[5]).normalize().multiplyScalar(15);
      CV.hide();
      anim.t = tH; anim.playing = true;
      anim.onEnd = () => {
        if (f.target_reached) {
          globeOn = false;
          CV.playDeploy(f, r.vehicle, r.site, { onDone: () => { CV.hide(); globeOn = true; landed(); } });
        } else landed();
      };
    },
  });
}
const STAGE_COL = [0xffb347, 0x5ab0ff, 0x4fe3a1, 0xd3a6ff];
let anim = null;
function renderFlight(r) {
  const f = r.flight, ok = f.target_reached, orbit = f.orbit, thin = ok && f.margin_dv_ms < 50;
  const v = r.vehicle;
  $('verdict').innerHTML = `<div class="dim small">${esc(v.name)} · ${fmt(f.payload_kg)} kg payload · ${esc(r.site.name)} · azimuth ${r.azimuth_deg}°</div>` +
    (ok ? `<div class="big ok">✓ Orbit: ${fmt(f.perigee_km)} × ${fmt(f.apogee_km)} km, ${f.inclination_deg}°</div>
          <div class="kv"><span>Margin</span><b>${fmt(f.margin_dv_ms)} m/s left in the last stage${f.margin_kg != null ? ` (≈ ${fmt(f.margin_kg, 1)} kg of propellant)` : ''}</b>
          ${thin ? '<span>Reserve</span><b style="color:var(--mid)">under 50 m/s: normal thrust and Isp scatter will make many real flights miss. Check the dispersion.</b>' : ''}
          <span>Max Q</span><b>${fmt(f.max_q_kpa, 1)} kPa at ${tplus(f.max_q_t)}</b><span>Insertion</span><b>${tplus(f.insertion.t)}</b></div>`
      : orbit ? `<div class="big mid">Orbit, but short of target: ${fmt(f.perigee_km)} × ${fmt(f.apogee_km)} km</div>`
      : `<div class="big bad">✗ Does not reach orbit</div>
          <div class="kv"><span>Highest point</span><b>${fmt(f.apex_km)} km</b>
          ${f.perigee_km > 0 ? `<span>Perigee</span><b>${fmt(f.perigee_km)} km: below the ~150 km needed to stay up, so the atmosphere pulls it down</b>` : ''}
          ${f.impact ? `<span>Comes down</span><b>${fmtLL(f.impact.lat, f.impact.lon)} · ${fmt(f.impact.t / 60)} min after liftoff${f.impact.t > 3000 ? ' (after most of an orbit)' : `, ${fmt(Math.abs(f.impact.downrange_km))} km downrange`}</b>` : ''}</div>`) +
    `<div class="kv">${f.stage_impacts.map((s) => `<span>${esc(s.stage)}</span><b>${s.stays_in_orbit ? 'stays in orbit' : `falls at ${fmtLL(s.lat, s.lon)}, ${fmt(Math.abs(s.downrange_km))} km downrange`}</b>`).join('')}</div>`;
  $('flightBar').innerHTML = `<button id="replayBtn">⟲ Replay ascent</button>
    ${ok && !thin ? '' : `<button id="adviseBtn" class="primary">${ok ? 'Add a reserve' : 'Why? How do I fix it?'}</button>`}
    <button id="dispBtn">Where could it fall? (500 runs)</button>
    ${ok ? `<button id="toStep2" class="${thin ? '' : 'primary'}">Next: launch window →</button>` : ''}`;
  $('replayBtn').onclick = () => launch3d(r);
  $('dispBtn').onclick = runDispersion;
  if ($('adviseBtn')) $('adviseBtn').onclick = runAdvise;
  if ($('toStep2')) $('toStep2').onclick = () => showStep(2);
  // Δv table
  const rows = f.dv_table;
  $('dvTable').innerHTML = `<table class="t"><tr><th>m/s</th><th>ideal</th><th>gravity</th><th>drag</th><th>steering</th><th>delivered</th></tr>
    ${rows.map((x) => `<tr><td>${esc(x.name)}</td><td>${fmt(x.ideal)}</td><td>−${fmt(x.gravity)}</td><td>−${fmt(x.drag)}</td><td>−${fmt(x.steering)}</td><td>${fmt(x.ideal - x.gravity - x.drag - x.steering)}</td></tr>`).join('')}
    <tr><td><b>Total</b></td><td><b>${fmt(f.dv_total_ms)}</b></td><td>−${fmt(f.losses_ms.gravity)}</td><td>−${fmt(f.losses_ms.drag)}</td><td>−${fmt(f.losses_ms.steering)}</td><td><b>${fmt(f.dv_total_ms - f.losses_ms.gravity - f.losses_ms.drag - f.losses_ms.steering)}</b></td></tr></table>
    <div class="small dim">Orbital speed at ${fmt(f.target_alt_km)} km: ${fmt(f.orbital_speed_ms)} m/s (Earth's rotation helps or hurts with the azimuth).</div>`;
  $('events').innerHTML = f.events.map((e) => `<div class="ev"><b>${tplus(e.t)}</b><span>${esc(e.event)}</span></div>`).join('');
  $('flownSpec').innerHTML = `<table class="t"><tr><th></th><th>prop kg</th><th>dry kg</th><th>kN</th><th>Isp</th><th>burn s</th></tr>${v.stages.map((s) =>
    `<tr><td>${esc(s.name)}${s.solid ? ' (solid)' : ''}</td><td>${fmt(s.prop_kg)}</td><td>${fmt(s.dry_kg)}</td><td>${fmt(s.thrust_vac_n / 1000, 1)}</td><td>${fmt(s.isp_vac_s)}</td><td>${fmt(s.burn_s)}</td></tr>`).join('')}</table>
    <div class="dim">Liftoff mass ${fmt(v.glow_kg)} kg · guidance [${r.guidance.map((x) => x.toFixed(1)).join(', ')}] (pitch-over altitude km, stage-1 end pitch, upper-stage pitch program)</div>`;
  // globe
  clear(layer.flight); dropLabels('imp');
  const pts = f.samples.map((s) => llv(s[4], s[5], s[1]));
  const cols = [];
  f.samples.forEach((s) => { const c = new THREE.Color(STAGE_COL[s[6] % 4]); if (s[7] === 'coast') c.multiplyScalar(0.55); cols.push(c.r, c.g, c.b); });
  const g = new THREE.BufferGeometry().setFromPoints(pts);
  g.setAttribute('color', new THREE.Float32BufferAttribute(cols, 3));
  const trail = new THREE.Line(g, new THREE.LineBasicMaterial({ vertexColors: true }));
  const beads = new THREE.Points(g, new THREE.PointsMaterial({ vertexColors: true, size: 3.5, sizeAttenuation: false, map: DOT, transparent: true, depthWrite: false }));
  layer.flight.add(trail, beads);           // a WebGL line is one pixel wide; the beads make the path readable
  const ground = line(f.samples.map((s) => llv(s[4], s[5], 1)), 0xffffff, 0.18);
  layer.flight.add(ground);
  const marker = points([pts[0]], 0xffffff, 10); layer.flight.add(marker);
  for (const s of f.stage_impacts) if (!s.stays_in_orbit) {
    const p = llv(s.lat, s.lon, 2); layer.flight.add(points([p], 0xff6b57, 8)); label(`✕ ${s.stage}`, p, 'imp');
  }
  if (f.impact) { const p = llv(f.impact.lat, f.impact.lon, 2); layer.flight.add(points([p], 0xff3b2f, 12)); label('✕ vehicle comes down here', p, 'imp'); }
  anim = { f, trail, marker, pts, t: 0 };
  trail.geometry.setDrawRange(0, 0);
  lookAt(r.site.lat, r.site.lon, 14);
}
const fmtLL = (la, lo) => `${Math.abs(la).toFixed(1)}°${la >= 0 ? 'N' : 'S'} ${Math.abs(((lo + 540) % 360) - 180).toFixed(1)}°${((lo + 540) % 360) - 180 >= 0 ? 'E' : 'W'}`;
function startAnim(f) { if (!anim) return; anim.t = 0; anim.playing = true; anim.f = f; }
function stepAnim(dt) {
  if (!anim?.playing) return;
  const s = anim.f.samples, last = s[s.length - 1][0];
  const rate = anim.t < (anim.f.insertion?.t ?? last) && s.find((x) => x[0] >= anim.t)?.[7] === 'coast' ? 90 : 25;
  anim.t = Math.min(last, anim.t + dt * rate);
  let k = s.findIndex((x) => x[0] >= anim.t); if (k < 0) k = s.length - 1;
  anim.trail.geometry.setDrawRange(0, k + 1);
  anim.marker.geometry.setFromPoints([anim.pts[k]]);
  if (anim.t >= last) { anim.playing = false; const cb = anim.onEnd; anim.onEnd = null; cb?.(); }
}

async function runAdvise() {
  const r = await busy('Searching for fixes, each one verified by re-flying the rocket (parallel)…', () => api('advise', mission({ explain: true })));
  S.fixes = r.fixes;
  const e = r.explanation;
  $('fixBox').innerHTML = `${e ? `<div class="explain"><div class="h">${esc(e.headline)}</div><div class="dim">${esc(e.why)}</div><div class="small dim">Nemotron 3 Super explains; the fixes below come from the simulator, not the model.</div></div>` : ''}
    ${r.fixes.map((x, i) => `<div class="fix"><span>${x.infeasible ? '⚠' : '✓'} ${esc(x.text)}${x.result ? `<br><span class="dim small">verified: ${fmt(x.result.perigee_km)} × ${fmt(x.result.apogee_km)} km</span>` : ''}</span>
      ${x.verified ? `<button data-fix="${i}" class="primary">Apply &amp; fly</button>` : ''}</div>`).join('')}`;
  $('fixBox').querySelectorAll('[data-fix]').forEach((b) => b.addEventListener('click', () => applyFix(S.fixes[+b.dataset.fix])));
}
function applyFix(x) {
  S.lastFix = x.text;
  setView('bay');                                   // the hangar shows the rocket as it was, then the change
  if (x.kind === 'payload') { $('payload').value = x.payload_kg; S.prov.payload_kg = 'edited'; }
  else {
    const st = S.vehicle.stages.find((s) => s.name === x.stage);
    const ratio = st.dry_kg / (st.dry_kg + st.prop_kg);
    st.prop_kg *= x.factor; st.dry_kg = st.prop_kg * ratio / (1 - ratio);
    if (x.kind === 'stage+thrust') st.thrust_vac_n *= x.factor;
    st.source = { ...st.source, prop_kg: 'edited', dry_kg: 'edited', ...(x.kind === 'stage+thrust' ? { thrust_vac_n: 'edited' } : {}) };
  }
  if (S.method === 'form') renderForm();
  setTimeout(() => CV.updateCurrent(S.vehicle, +$('payload').value || 5), 1500);  // the stage grows, then it flies
  setTimeout(fly, 5000);
}

async function runDispersion() {
  const r = await busy('Monte Carlo: 500 flights with thrust, Isp, mass, pitch and azimuth scatter…', () => api('dispersion', mission({ runs: 500 })));
  clear(layer.disp);
  const ell = (e, color) => { if (!e) return; for (const [k, op] of [['p50', 0.9], ['p95', 0.55]]) layer.disp.add(line(e[k].map(([la, lo]) => llv(la, lo, 3)), color, op)); };
  if (r.vehicle_impacts.length) { layer.disp.add(points(r.vehicle_impacts.map(([la, lo]) => llv(la, lo, 2)), 0xff3b2f, 3)); ell(r.vehicle_ellipse, 0xff6b57); }
  Object.entries(r.stages).forEach(([n, s], i) => { layer.disp.add(points(s.impacts.map(([la, lo]) => llv(la, lo, 2)), STAGE_COL[i % 4], 3)); ell(s.ellipse, STAGE_COL[i % 4]); });
  const rows = Object.entries(r.stages).map(([n, s]) => `<tr><td>${esc(n)}</td><td>${s.impacts.length}</td><td>${s.ellipse ? `${s.ellipse.p95_axes_km[0]} × ${s.ellipse.p95_axes_km[1]} km` : 'spread too wide for an ellipse'}</td></tr>`).join('');
  $('dispBox').innerHTML = `<div class="card"><b>Dispersion · ${r.runs} flights</b>
    <div class="row"><span>Reaches orbit</span><b>${(r.p_orbit * 100).toFixed(0)}%</b></div><div class="row"><span>Reaches the target orbit</span><b>${(r.p_target * 100).toFixed(0)}%</b></div>
    <table class="t mt"><tr><th>falls</th><th>runs</th><th>95% area</th></tr>${rows}
    ${r.vehicle_impacts.length ? `<tr><td>whole vehicle</td><td>${r.vehicle_impacts.length}</td><td>${r.vehicle_ellipse ? `${r.vehicle_ellipse.p95_axes_km[0]} × ${r.vehicle_ellipse.p95_axes_km[1]} km` : 'along the ground track (no ellipse)'}</td></tr>` : ''}</table>
    <div class="small dim">1σ: thrust ${r.sigma.thrust * 100}%, Isp ${r.sigma.isp * 100}%, dry mass ${r.sigma.dry * 100}%, pitch ${r.sigma.kick_add}°, azimuth ${r.sigma.az_add}°. Same code runs as a batch job for thousands of runs (Nebius Serverless Jobs).</div></div>`;
}

// ---------- step 2: windows ----------
async function loadHeat() {
  const r = await busy('Weather (Open-Meteo) and space weather (NOAA SWPC) for every site that can reach your orbit…', () => api('windows', { incl_deg: +$('incl').value }));
  S.heat = r;
  const first = r.rows.find((x) => x.hours)?.hours[0].t;
  const day0 = first ? new Date(first) : new Date();
  $('heatNote').textContent = `assumed small-launcher thresholds: gusts ≥ ${r.criteria.gust_ms[1]} m/s, rain ≥ ${r.criteria.precip_pct[1]}%, CAPE ≥ ${r.criteria.cape_jkg[1]} J/kg, 250 hPa wind ≥ ${r.criteria.wind250_ms[1]} m/s → no-go`;
  let h = `<div class="days"><span></span><div>${[...Array(7)].map((_, d) => `<span>${new Date(day0.getTime() + d * 864e5).toUTCString().slice(0, 11)}</span>`).join('')}</div></div>`;
  for (const row of r.rows) {
    h += `<div class="hrow"><div class="nm" title="${esc(row.name)}">${esc(row.name.replace(/ \(.*/, ''))} <small>${row.azimuth_deg != null ? row.azimuth_deg.toFixed(0) + '°' : ''}</small></div>`;
    if (!row.reachable || row.error) h += `<div class="unreach">${esc(row.why || row.error)}</div></div>`;
    else h += `<div class="cells" data-site="${row.site}">${row.hours.map((x, k) => (Date.parse(x.t) + 3600e3 < Date.now()
      ? '<i class="na" title="past"></i>' : `<i class="${x.status === 'n/a' ? 'na' : x.status}" data-k="${k}"></i>`)).join('')}</div></div>`;
  }
  $('heat').innerHTML = h + `<div class="small dim mt">Each cell is one hour (UTC). Green go · amber caution · red no-go. Click an hour to screen its minutes for collisions.</div>`;
  $('heat').querySelectorAll('.cells').forEach((c) => {
    const row = r.rows.find((x) => x.site === c.dataset.site);
    c.addEventListener('mousemove', (ev) => {
      const k = ev.target.dataset.k; if (k == null) return;
      const x = row.hours[k];
      tip(ev, `<b>${esc(row.name)}</b><br>${x.t.slice(0, 16).replace('T', ' ')} UTC · <b>${x.status}</b><br>${x.checks.map((c2) => `${c2.check}: ${c2.value ?? '–'}${c2.unit}`).join('<br>')}`);
    });
    c.addEventListener('mouseleave', () => { $('tip').hidden = true; });
    c.addEventListener('click', (ev) => { const k = ev.target.dataset.k; if (k != null) pickHour(row, +k, ev.target); });
  });
}
function tip(ev, html) { const t = $('tip'); t.innerHTML = html; t.hidden = false; t.style.left = Math.min(ev.clientX + 12, innerWidth - 300) + 'px'; t.style.top = (ev.clientY + 12) + 'px'; }

async function pickHour(row, k, el) {
  $('heat').querySelectorAll('i.sel').forEach((x) => x.classList.remove('sel')); el.classList.add('sel');
  if (row.site !== $('siteSel').value) {                // another site: fly from there first
    $('siteSel').value = row.site; S.site = row.site; updateAz();
    await fly(); showStep(2);
    if (!S.flight.target_reached) { $('lcolaBox').innerHTML = `<div class="lcmsg" style="color:var(--warn)">From ${esc(row.name)} this rocket does not reach the target orbit.</div>`; return; }
  }
  const start = row.hours[k].t;
  S.hour = { row, k, start };
  const r = await busy(`LCOLA: screening 60 liftoff minutes from ${hhmm(start)} UTC against every catalogued object near your orbit…`, () => api('lcola', mission({ start_utc: start, minutes: 60 })));
  S.lcola = r;
  const best = [...r.rows].filter((x) => x.status === 'go').sort((a, b) => (b.closest_km ?? 1e9) - (a.closest_km ?? 1e9));
  const worst = [...r.rows].sort((a, b) => (a.closest_km ?? 1e9) - (b.closest_km ?? 1e9))[0];
  $('lcolaBox').innerHTML = `<h2><span>COLLISION AVOIDANCE AFTER INSERTION (LCOLA) · ${esc(row.name)} · ${start.slice(0, 10)} ${hhmm(start)}–${hhmm(r.rows[r.rows.length - 1].liftoff_utc)} UTC</span>
      <span class="dim small">${fmt(r.objects_screened)} objects in your altitude band · first orbit</span></h2>
    <div class="strip">${r.rows.map((x, i) => `<i class="${x.status}${i % 10 === 0 ? ' lab' : ''}" data-i="${i}" data-l="${hhmm(x.liftoff_utc)}"></i>`).join('')}</div>
    <div class="lcmsg">${worst?.closest_km != null ? `Liftoff <b>${hhmm(worst.liftoff_utc)}</b>: your satellite would pass <b style="color:var(--warn)">${fmt(worst.closest_km, 1)} km</b> from ${esc(worst.object)} ${fmt(worst.minutes_after_insertion)} min after insertion. ` : ''}
      ${best[0] ? `Liftoff <b>${hhmm(best[0].liftoff_utc)}</b>: nearest object ${fmt(best[0].closest_km, 1)} km.` : 'No minute in this hour is clear.'}
      <span class="dim small">Caution &lt; ${r.criteria_km.caution} km, no-go &lt; ${r.criteria_km.nogo} km (assumed). ${esc(r.note)}.</span></div>`;
  const strip = $('lcolaBox').querySelector('.strip');
  strip.addEventListener('mousemove', (ev) => {
    const i = ev.target.dataset.i; if (i == null) return; const x = r.rows[i];
    tip(ev, `<b>Liftoff ${hhmm(x.liftoff_utc)} UTC</b> · ${x.status}<br>${x.closest_km != null ? `closest: ${fmt(x.closest_km, 2)} km from ${esc(x.object)}<br>${fmt(x.minutes_after_insertion, 1)} min after insertion` : 'nothing within the search radius'}`);
  });
  strip.addEventListener('mouseleave', () => { $('tip').hidden = true; });
  strip.addEventListener('click', (ev) => { const i = ev.target.dataset.i; if (i != null) toggleCand(r.rows[i].liftoff_utc); });
  S.cands = [...new Set([worst?.liftoff_utc, ...best.slice(0, 2).map((x) => x.liftoff_utc)].filter(Boolean))].slice(0, 4);
  renderCands();
}
function toggleCand(t) {
  S.cands = S.cands.includes(t) ? S.cands.filter((x) => x !== t) : [...S.cands, t].slice(-4);
  renderCands();
}
function renderCands() {
  const rows = Object.fromEntries((S.lcola?.rows || []).map((x) => [x.liftoff_utc, x]));
  $('lcolaBox').querySelectorAll('.strip i').forEach((el) => el.classList.toggle('pick', S.cands.includes(S.lcola.rows[+el.dataset.i].liftoff_utc)));
  $('candList').innerHTML = S.cands.map((t) => `<span class="tag ${rows[t]?.status === 'go' ? 'ok' : rows[t]?.status === 'nogo' ? 'bad' : 'mid'}" data-t="${t}">${t.slice(5, 10)} ${hhmm(t)} UTC ✕</span>`).join('') || '<span class="dim small">no window picked</span>';
  $('candList').querySelectorAll('[data-t]').forEach((el) => el.addEventListener('click', () => toggleCand(el.dataset.t)));
  $('teamBtn').disabled = !S.cands.length;
}
$('teamBtn').addEventListener('click', async () => {
  const r = await busy('Launch team voting (weather, range safety, orbital safety · Nemotron 3 Super) and mission director brief (Nemotron 3 Ultra)…',
    () => api('gonogo', mission({ candidates: [...S.cands].sort() })));
  const ROLE = { weather: 'Weather', range: 'Range', orbital: 'Orbital' };
  const b = r.brief;
  $('board').innerHTML = r.windows.map((w) => {
    const vs = r.votes[w.liftoff_utc];
    return `<div class="win"><div class="hd"><span>${w.liftoff_utc.slice(5, 10)} ${hhmm(w.liftoff_utc)} UTC</span><span class="tag ${w.status === 'go' ? 'ok' : w.status === 'nogo' ? 'bad' : 'mid'}">checks: ${w.status}</span></div>
      ${Object.entries(vs).map(([k, v]) => `<div class="vote"><span class="r">${ROLE[k]}</span><span class="tag ${v.vote === 'go' ? 'ok' : 'bad'}">${v.vote}</span><span>${esc(v.reason)}</span></div>`).join('')}
      <div class="small dim">LCOLA ${w.lcola.closest_km != null ? fmt(w.lcola.closest_km, 1) + ' km (' + esc(w.lcola.object) + ')' : 'clear'} · Kp ${typeof w.kp === 'object' ? w.kp.value : w.kp}</div></div>`;
  }).join('') + `<div class="brief ${b.decision === 'go' ? '' : 'hold'}"><div class="dim small">MISSION DIRECTOR · Nemotron 3 Ultra</div>
    <div class="big">${b.decision === 'go' ? `GO for ${hhmm(b.liftoff_utc)} UTC` : 'HOLD'}</div><div>${esc(b.summary)}</div>
    ${b.watch_items?.length ? `<div class="small mt"><b>Watch:</b> ${b.watch_items.map(esc).join(' · ')}</div>` : ''}
    ${b.guard ? `<div class="small" style="color:var(--mid)">Guard: ${esc(b.guard)}</div>` : ''}
    <div class="small dim mt">${r.usage.calls} model calls · $${r.usage.cost_usd.toFixed(4)}</div>
    ${b.decision === 'go' ? `<button id="toStep3" class="primary big mt">Launch at ${hhmm(b.liftoff_utc)} UTC →</button>` : ''}</div>`;
  if (b.decision === 'go') {
    S.liftoff = b.liftoff_utc; unlock(3);
    $('toStep3').onclick = () => { showStep(3); $('liftoffNote').innerHTML = `Liftoff ${S.liftoff.replace('T', ' ').slice(0, 16)} UTC from ${esc(P.sites[$('siteSel').value].name)} on ${esc(S.vehicle.name)}.`; };
  }
});

// ---------- step 3: operate ----------
let catSats = [], catPts = new THREE.Points(), catPos = null, simMs = Date.now(), simRate = 60, me = null;
scene.add(catPts); catPts.visible = false;
async function loadCatalog() {
  if (catSats.length) return;
  const txt = (await Promise.all(['/data/celestrak_active.tle', '/data/celestrak_debris.tle'].map((u) => fetch(u).then((r) => (r.ok ? r.text() : '')).catch(() => ''))) ).join('\n');
  const ls = txt.split('\n').map((l) => l.trimEnd()).filter(Boolean);
  for (let i = 0; i + 2 < ls.length; i += 3) catSats.push(satellite.twoline2satrec(ls[i + 1], ls[i + 2]));
  catPos = new Float32Array(catSats.length * 3);
  catPts.geometry = new THREE.BufferGeometry(); catPts.geometry.setAttribute('position', new THREE.BufferAttribute(catPos, 3));
  catPts.material = new THREE.PointsMaterial({ color: 0xffd27a, size: 2.4, sizeAttenuation: false, map: DOT, transparent: true, depthWrite: false, opacity: 0.8 });
  catPts.frustumCulled = false;
}
const eci2scene = (p, date) => { const gm = satellite.gstime(date), c = Math.cos(gm), s = Math.sin(gm); return new THREE.Vector3((p.x * c + p.y * s) / 1000, p.z / 1000, -(-p.x * s + p.y * c) / 1000); };
let catK = 0;
function updateCatalog(date) {
  if (!catSats.length || !catPts.visible) return;
  const gm = satellite.gstime(date), c = Math.cos(gm), s = Math.sin(gm), n = catSats.length;
  const step = Math.ceil(n / 4);                         // a quarter of the catalog per frame
  for (let i = catK; i < Math.min(n, catK + step); i++) {
    const p = satellite.propagate(catSats[i], date).position;
    if (p) { catPos[3 * i] = (p.x * c + p.y * s) / 1000; catPos[3 * i + 1] = p.z / 1000; catPos[3 * i + 2] = -(-p.x * s + p.y * c) / 1000; }
  }
  catK = catK + step >= n ? 0 : catK + step;
  catPts.geometry.attributes.position.needsUpdate = true;
}
$('deployBtn').addEventListener('click', async () => {
  const [lat, lon] = $('homeLL').value.split(',').map(Number);
  const r = await busy('Inserting into orbit, writing its TLE, and screening it against the catalog for 72 h…', () => api('deploy', mission({
    liftoff_utc: S.liftoff, name: $('satName').value, owner: $('satOwner').value, mission: $('satMission').value,
    can_maneuver: $('satProp').checked, home: { name: $('homeName').value, lat, lon } })));
  S.deployed = r;
  await loadCatalog();
  const rec = satellite.twoline2satrec(r.tle[0], r.tle[1]);
  catSats.push(rec);
  const grown = new Float32Array(catSats.length * 3); grown.set(catPos); catPos = grown;
  catPts.geometry.setAttribute('position', new THREE.BufferAttribute(catPos, 3));
  me = { rec, name: r.name };
  simMs = Date.parse(r.epoch_utc); setSun(new Date(simMs));
  clear(layer.orbit); dropLabels('me');
  const period = 2 * Math.PI / rec.no * 60e3;              // ms
  const ring = []; for (let k = 0; k <= 180; k++) { const d = new Date(simMs + period * k / 180); const p = satellite.propagate(rec, d).position; if (p) ring.push(eci2scene(p, new Date(simMs))); }
  const orbitLine = line(ring, 0x4fe3a1, 0.9); orbitLine.userData.inertial = true; layer.orbit.add(orbitLine);
  me.dot = points([ring[0]], 0x4fe3a1, 12); layer.orbit.add(me.dot);
  me.lbl = { pos: ring[0].clone() }; label(r.name, me.lbl.pos, 'me');
  const home = $('homeName').value;
  if (isFinite(lat)) { const hp = llv(lat, lon, 3); layer.orbit.add(points([hp], 0x5ab0ff, 8)); label('⌂ ' + home, hp, 'me'); }
  $('deployOut').innerHTML = `<div class="card mt"><b>${esc(r.name)}</b> is in orbit · NORAD ${r.norad_id} (studio range)
    <pre class="tle">${esc(r.name)}\n${esc(r.tle[0])}\n${esc(r.tle[1])}</pre>
    <div class="small dim">Tracked with ${fmt(r.objects_in_catalog - 1)} catalogued objects. The TLE reproduces the simulated insertion state at epoch.</div></div>`;
  const kst = (iso) => new Date(Date.parse(iso) + 9 * 3600e3).toISOString().slice(5, 16).replace('T', ' ');
  $('opsOut').innerHTML = `${r.passes?.length ? `<h2 class="mt">PASSES OVER ${esc(home.toUpperCase())}</h2>${r.passes.slice(0, 6).map((p) => `<div class="pass"><span>${p.rise_utc.slice(5, 16).replace('T', ' ')} UTC <span class="dim">(${kst(p.rise_utc)} KST)</span></span><b>max ${p.max_elevation_deg}°</b></div>`).join('')}` : ''}
    <h2 class="mt">CLOSE APPROACHES · next ${r.screen_hours} h</h2>
    ${r.encounters.length ? r.encounters.map((e, i) => `<div class="enc"><span><b>${esc(e.object_b.name)}</b><br><span class="dim">${e.tca_utc.slice(5, 16).replace('T', ' ')} UTC · ${fmt(e.miss_distance_km * 1000)} m · ${e.relative_speed_kms} km/s</span></span>
      <button data-enc="${i}" class="primary">Assess with CRIC</button></div>`).join('') : '<div class="dim">No approach within 5 km. CRIC keeps screening daily.</div>'}`;
  $('opsOut').querySelectorAll('[data-enc]').forEach((b) => b.addEventListener('click', () => assess(r.encounters[+b.dataset.enc])));
  controls.target.set(0, 0, 0); camGoal = ring[0].clone().normalize().multiplyScalar(17);   // look at the new satellite
});

const STEP_TXT = {
  space_weather: (e) => `Space weather: Kp ${e.kp}`, geometry: (e) => `Geometry: miss ${fmt(e.miss_km, 2)} km at ${fmt(e.rel_speed_kms, 1)} km/s`,
  risk: (e) => `Collision probability ${e.pc ? e.pc.toExponential(1) : '≈ 0'} · ${e.level}`, options: (e) => `${e.options.length} burn options computed`,
  dossier: (e) => `Dossier (${e.role}): ${e.dossier.name} · can manoeuvre: ${e.dossier.capability?.can_maneuver}`,
  search_done: () => 'Web research (Tavily)', decision_start: (e) => `Decision: ${e.votes} votes (Nemotron 3 Super)`,
  decision: (e) => `Model proposes ${e.decision.action}`, validation: (e) => `Safety validator: ${e.final_action}`,
  agreement: (e) => `Coded rules and model ${e.agree ? 'agree' : 'disagree'} → ${e.final?.action}`,
};
function assess(enc) {
  const out = $('incOut');
  out.innerHTML = `<h2 class="mt">CRIC · ${esc(enc.object_a.name)} × ${esc(enc.object_b.name)}</h2><div class="stream" id="stream"></div>`;
  const es = new EventSource(`/api/incident?ours=${enc.object_a.norad_id}&other=${enc.object_b.norad_id}&preset=quiet&mode=live`);
  const add = (t, k = '•') => { const d = document.createElement('div'); d.innerHTML = `<span class="k">${k}</span>${esc(t)}`; $('stream').append(d); };
  add('Live run: Token Factory + Tavily (about a minute, ~$0.03)', '▶');
  es.onmessage = (m) => {
    const e = JSON.parse(m.data);
    if (STEP_TXT[e.kind]) add(STEP_TXT[e.kind](e));
    if (e.kind === 'error') { add(e.message, '⚠'); es.close(); }
    if (e.kind === 'end') es.close();
    if (e.kind === 'result') showDecision(e);
  };
  es.onerror = () => { es.close(); };
}
function showDecision(e) {
  const fin = e.validation.final_action, opt = e.options.find((o) => o.id === e.validation.final_option_id);
  const box = document.createElement('div'); box.className = 'decision';
  box.innerHTML = `<div class="dim small">DECISION · ${e.seconds.toFixed(0)} s · $${e.usage.cost_usd.toFixed(3)}</div>
    <div class="big">${esc(fin)}${opt ? ` · ${opt.dv_ms.toFixed(3)} m/s ${esc(opt.direction)} at ${opt.burn_utc.slice(11, 16)} UTC` : ''}</div>
    <div>${esc(e.decision.rationale)}</div>
    <div class="small dim mt">Pc ${e.risk.pc ? e.risk.pc.toExponential(1) : '≈ 0'} · miss ${fmt(e.conjunction.miss_km, 2)} km · ${esc(e.other.name)} can manoeuvre: ${esc(e.other.capability?.can_maneuver)}</div>
    <div class="ctl mt"><input id="opName" placeholder="Your name (who decides)" value="${esc($('satOwner').value)}">
      <button id="apOk" class="primary">Approve</button><button id="apNo">Reject</button></div><div id="apOut" class="small"></div>`;
  $('incOut').append(box);
  const send = async (verdict) => {
    const reason = verdict === 'reject' ? prompt('Why reject?') : '';
    if (verdict === 'reject' && !reason) return;
    const r = await fetch('/api/approval', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file: e.file, action: fin, option_id: e.validation.final_option_id || '', verdict, reason, operator: $('opName').value }) }).then((x) => x.json());
    $('apOut').innerHTML = `✓ ${esc(verdict)}d by ${esc(r.operator)} · logged (hash ${esc(r.hash?.slice(0, 10))}…) · ${esc(r.note)}`;
  };
  $('apOk').onclick = () => send('approve'); $('apNo').onclick = () => send('reject');
}

// ---------- loop ----------
let last = performance.now();
function frame(now) {
  const dt = Math.min((now - last) / 1000, 0.1); last = now;
  stepAnim(dt);
  if (camGoal) { camera.position.lerp(camGoal, 0.06); if (camera.position.distanceTo(camGoal) < 0.05) camGoal = null; }
  if (layer.orbit.visible && me) {
    simMs += dt * 1000 * simRate;
    const d = new Date(simMs);
    updateCatalog(d);
    const p = satellite.propagate(me.rec, d).position;
    if (p) { const v = eci2scene(p, d); me.dot.geometry.setFromPoints([v]); me.lbl.pos.copy(v); }
    layer.orbit.children.forEach((c) => { if (c.userData.inertial) c.rotation.y = satellite.gstime(new Date(Date.parse(S.deployed.epoch_utc))) - satellite.gstime(d); });
  }
  controls.update();
  placeLabels();
  document.querySelectorAll('.lbl').forEach((el) => { if (!globeOn) el.style.display = 'none'; });
  if (globeOn) renderer.render(scene, camera);
  requestAnimationFrame(frame);
}
window.studio = { S, camera, llv, CV };                   // for debugging from the console
loadPreset('garage');
$('targetText').value = 'My 5 kg cubesat JIMIN-1 should photograph Seoul, where I live, from a 500 km sun-synchronous orbit.';
updateIncl();
const s0 = P.sites.naro; lookAt(s0.lat, s0.lon, 15);
$('loading').classList.add('done'); setTimeout(() => $('loading').remove(), 800);
requestAnimationFrame(frame);
