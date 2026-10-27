// The close-up stage of Mission Studio, in metres: the hangar where rockets are compared and edited, the launch
// (pad, plume, staging, fairing, smoke), and the payload deploy in orbit. Every motion is driven by the
// simulation's samples; only the look (plume, smoke, sky) is illustration. The globe view takes over above
// ~150 km and hands back for the deploy.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { buildRocket, disposeTree, layoutOf } from './rocketgen.js';

const R = 6378137, D2R = Math.PI / 180;
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmt = (x, d = 0) => (x == null || !isFinite(x) ? '–' : Number(x).toLocaleString('en-US', { maximumFractionDigits: d, minimumFractionDigits: d }));
const clock = (t) => `T${t < 0 ? '−' : '+'}${String(Math.floor(Math.abs(t) / 3600)).padStart(2, '0')}:${String(Math.floor(Math.abs(t) % 3600 / 60)).padStart(2, '0')}:${String(Math.floor(Math.abs(t) % 60)).padStart(2, '0')}`;

// ---------- geometry on a spherical Earth (the simulator's) ----------
const ecef = (lat, lon, alt = 0) => {
  const r = R + alt, a = lat * D2R, b = lon * D2R;
  return [r * Math.cos(a) * Math.cos(b), r * Math.cos(a) * Math.sin(b), r * Math.sin(a)];
};
function frameAt(lat, lon) {
  const a = lat * D2R, b = lon * D2R;
  return { o: ecef(lat, lon), e: [-Math.sin(b), Math.cos(b), 0], n: [-Math.sin(a) * Math.cos(b), -Math.sin(a) * Math.sin(b), Math.cos(a)],
    u: [Math.cos(a) * Math.cos(b), Math.cos(a) * Math.sin(b), Math.sin(a)] };
}
const dot = (p, q) => p[0] * q[0] + p[1] * q[1] + p[2] * q[2];
function toLocal(F, p, out = new THREE.Vector3()) {               // ECEF -> scene (x east, y up, z south)
  const d = [p[0] - F.o[0], p[1] - F.o[1], p[2] - F.o[2]];
  return out.set(dot(d, F.e), dot(d, F.u), -dot(d, F.n));
}
function dest(lat, lon, brg, m) {
  const d = m / R, la = lat * D2R, b = brg * D2R;
  const la2 = Math.asin(Math.sin(la) * Math.cos(d) + Math.cos(la) * Math.sin(d) * Math.cos(b));
  const lo2 = lon * D2R + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(la), Math.cos(d) - Math.sin(la) * Math.sin(la2));
  return [la2 / D2R, ((lo2 / D2R + 540) % 360) - 180];
}
function subsolar(date) {
  const doy = (date - Date.UTC(date.getUTCFullYear(), 0, 0)) / 864e5;
  return [-23.44 * Math.cos((2 * Math.PI / 365) * (doy + 10)), -15 * (date.getUTCHours() + date.getUTCMinutes() / 60 - 12)];
}

// ---------- shaders ----------
const SKY = {
  uniforms: { alt: { value: 0 }, sun: { value: new THREE.Vector3(0, 1, 0) }, day: { value: 1 } },
  vertexShader: `varying vec3 vD; void main(){ vD = normalize(position); vec4 p = projectionMatrix * modelViewMatrix * vec4(position, 1.); gl_Position = p.xyww; }`,
  fragmentShader: `uniform float alt; uniform vec3 sun; uniform float day; varying vec3 vD;
    float h(vec3 p){ return fract(sin(dot(p, vec3(12.9898,78.233,45.164))) * 43758.5453); }
    void main(){
      float k = smoothstep(8., 75., alt);
      vec3 zen = mix(vec3(.07,.2,.55), vec3(.0,.0,.01), k);
      vec3 hor = mix(vec3(.5,.66,.86), vec3(.05,.1,.26), smoothstep(10., 100., alt));
      float y = vD.y;
      vec3 c = mix(hor, zen, pow(clamp(y, 0., 1.), .35));
      c = mix(c, hor * .6, clamp(-y * 3., 0., 1.));
      float s = max(dot(vD, normalize(sun)), 0.);
      c += vec3(1., .9, .7) * (pow(s, 900.) * 6. + pow(s, 12.) * .25 * (1. - k * .7));
      c *= mix(0.04, 1.0, day);                                       // night: the sky goes dark
      vec3 q = floor(vD * 420.), fq = fract(vD * 420.) - .5;          // one star per lit cell, a small round point
      float st = step(.9985, h(q)) * smoothstep(.16, .04, length(fq)) * max(smoothstep(30., 70., alt), 1. - day) * step(0., y + .05);
      c += vec3(st) * .9;
      gl_FragColor = vec4(c, 1.);
      #include <tonemapping_fragment>
      #include <colorspace_fragment>
    }`,
  side: THREE.BackSide, depthWrite: false, depthTest: false,
};
const FLAME = {
  uniforms: { time: { value: 0 }, power: { value: 1 }, core: { value: new THREE.Color(1, 0.86, 0.55) }, edge: { value: new THREE.Color(1, 0.42, 0.1) } },
  vertexShader: `varying vec2 vUv; varying vec3 vN; varying vec3 vV;
    void main(){ vUv = uv; vec4 w = modelViewMatrix * vec4(position, 1.); vN = normalize(normalMatrix * normal); vV = normalize(-w.xyz); gl_Position = projectionMatrix * w; }`,
  fragmentShader: `uniform float time, power; uniform vec3 core, edge; varying vec2 vUv; varying vec3 vN; varying vec3 vV;
    void main(){
      float v = vUv.y;                       // 1 at the nozzle, 0 at the tip
      float f = abs(dot(vN, vV));
      float n = .75 + .25 * sin(time * 60. + v * 40.) * sin(time * 37. + vUv.x * 30.);
      float a = pow(v, 1.6) * pow(f, 1.3) * n * power;
      vec3 c = mix(edge, core, pow(v, .7) * f);
      gl_FragColor = vec4(min(c * 1.6, vec3(1.)), clamp(a * 1.4, 0., 1.));
    }`,
  transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
};
const PARTICLE = {
  uniforms: { map: { value: null }, scale: { value: 600 } },
  vertexShader: `attribute float size; attribute float alpha; attribute vec3 tint; varying float vA; varying vec3 vC;
    uniform float scale;
    void main(){ vA = alpha; vC = tint; vec4 mv = modelViewMatrix * vec4(position, 1.); gl_PointSize = min(size * scale / -mv.z, 512.); gl_Position = projectionMatrix * mv; }`,
  fragmentShader: `uniform sampler2D map; varying float vA; varying vec3 vC;
    void main(){ float a = texture2D(map, gl_PointCoord).a * vA; if (a < .004) discard; gl_FragColor = vec4(vC, a); }`,
  transparent: true, depthWrite: false,
};

function softDot() {
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const g = c.getContext('2d'), gr = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  gr.addColorStop(0, 'rgba(255,255,255,1)'); gr.addColorStop(0.5, 'rgba(255,255,255,.5)'); gr.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = gr; g.fillRect(0, 0, 64, 64); return new THREE.CanvasTexture(c);
}

function cloudTexture(coverPct) {
  // value noise, a few octaves, cut at the forecast cover so roughly that share of the sky is cloud
  const N = 512, c = document.createElement('canvas'); c.width = c.height = N;
  const g = c.getContext('2d'), img = g.createImageData(N, N), rnd = [];
  for (let i = 0; i < 64 * 64; i++) rnd.push(Math.random());
  const val = (x, y, f) => {
    const X = (x * f) % 64, Y = (y * f) % 64, x0 = Math.floor(X), y0 = Math.floor(Y), tx = X - x0, ty = Y - y0;
    const R = (i, j) => rnd[((j + 64) % 64) * 64 + ((i + 64) % 64)], sm = (t) => t * t * (3 - 2 * t);
    const a = R(x0, y0) + (R(x0 + 1, y0) - R(x0, y0)) * sm(tx), b = R(x0, y0 + 1) + (R(x0 + 1, y0 + 1) - R(x0, y0 + 1)) * sm(tx);
    return a + (b - a) * sm(ty);
  };
  const cut = 1 - Math.min(0.95, coverPct / 100);
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
    const u = x / N, v = y / N;
    const n = 0.55 * val(u, v, 4) + 0.27 * val(u, v, 9) + 0.13 * val(u, v, 19) + 0.05 * val(u, v, 41);
    const a = Math.max(0, Math.min(1, (n - cut) * 6));
    const k = (y * N + x) * 4; img.data[k] = img.data[k + 1] = img.data[k + 2] = 245; img.data[k + 3] = a * 255;
  }
  g.putImageData(img, 0, 0);
  const t = new THREE.CanvasTexture(c); t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(3, 3); return t;
}

let audio = null, soundOn = false;
function rumble(level) {                         // brown noise through a low-pass filter, louder while burning
  if (!soundOn) { if (audio) audio.gain.gain.value = 0; return; }
  if (!audio) {
    const ctx = new AudioContext(), len = ctx.sampleRate * 2, buf = ctx.createBuffer(1, len, ctx.sampleRate), d = buf.getChannelData(0);
    let last = 0; for (let i = 0; i < len; i++) { last = (last + 0.02 * (Math.random() * 2 - 1)) / 1.02; d[i] = last * 3.5; }
    const src = ctx.createBufferSource(); src.buffer = buf; src.loop = true;
    const lp = ctx.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 180;
    const gain = ctx.createGain(); gain.gain.value = 0;
    src.connect(lp).connect(gain).connect(ctx.destination); src.start(); audio = { ctx, gain };
  }
  audio.gain.gain.value += (level * 0.8 - audio.gain.gain.value) * 0.1;
}

class Particles {
  constructor(n, map, blending = THREE.NormalBlending) {
    this.n = n; this.k = 0;
    this.pos = new Float32Array(n * 3); this.vel = new Float32Array(n * 3); this.age = new Float32Array(n).fill(1e9);
    this.life = new Float32Array(n).fill(1); this.s0 = new Float32Array(n); this.s1 = new Float32Array(n);
    this.size = new Float32Array(n); this.alpha = new Float32Array(n); this.tint = new Float32Array(n * 3); this.a0 = new Float32Array(n);
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(this.pos, 3)); g.setAttribute('size', new THREE.BufferAttribute(this.size, 1));
    g.setAttribute('alpha', new THREE.BufferAttribute(this.alpha, 1)); g.setAttribute('tint', new THREE.BufferAttribute(this.tint, 3));
    const m = new THREE.ShaderMaterial({ ...PARTICLE, uniforms: THREE.UniformsUtils.clone(PARTICLE.uniforms), blending });
    m.uniforms.map.value = map;
    this.points = new THREE.Points(g, m); this.points.frustumCulled = false;
  }
  emit(p, v, life, s0, s1, a0, c) {
    const i = this.k; this.k = (this.k + 1) % this.n;
    this.pos.set([p.x, p.y, p.z], i * 3); this.vel.set([v.x, v.y, v.z], i * 3);
    this.age[i] = 0; this.life[i] = life; this.s0[i] = s0; this.s1[i] = s1; this.a0[i] = a0; this.tint.set(c, i * 3);
  }
  step(dt, drag = 0.4) {
    for (let i = 0; i < this.n; i++) {
      if (this.age[i] > this.life[i]) { this.alpha[i] = 0; continue; }
      this.age[i] += dt;
      const f = Math.min(1, this.age[i] / this.life[i]), damp = Math.exp(-drag * dt);
      for (let j = 0; j < 3; j++) { this.vel[i * 3 + j] *= damp; this.pos[i * 3 + j] += this.vel[i * 3 + j] * dt; }
      this.size[i] = this.s0[i] + (this.s1[i] - this.s0[i]) * Math.sqrt(f);
      this.alpha[i] = this.a0[i] * (1 - f) * Math.min(1, this.age[i] * 8);
    }
    const a = this.points.geometry.attributes;
    a.position.needsUpdate = a.size.needsUpdate = a.alpha.needsUpdate = a.tint.needsUpdate = true;
  }
  clear() { this.age.fill(1e9); this.alpha.fill(0); }
}

const NOTE = 'Shape derived from the vehicle data · motion from the 3-DOF simulation · plume, smoke, sky and cloud texture are illustration (light and cloud amount: the chosen launch hour; before one is chosen, local noon and 25%)';

export function createCloseView(root, { earthTex, onState } = {}) {
  root.innerHTML = `<div class="cv-hud" hidden>
      <div class="cv-top"><div class="cv-live"><b>SIMULATED FLIGHT</b> <span id="cvTitle"></span></div>
        <div class="cv-note"></div></div>
      <div class="cv-toast" id="cvToast"></div>
      <div class="cv-cams"><button data-cam="auto" class="on">Auto</button><button data-cam="pad">Pad</button><button data-cam="track">Tracking</button>
        <button data-cam="chase">Chase</button><button data-cam="onboard">Onboard</button><button id="cvSound" title="Engine rumble (synthesised)">🔇</button><button id="cvSkip" title="Skip to the globe view">Skip ▸</button></div>
      <div class="cv-side"><canvas id="cvMap" width="480" height="280"></canvas><canvas id="cvGraph" width="480" height="150"></canvas>
        <div class="cv-legend2"><i style="background:#5ab0ff"></i>altitude <i style="background:#ffc857"></i>speed</div></div>
      <div class="cv-stack" id="cvStack"></div>
      <div class="cv-bottom">
        <div class="cv-l"><div><span>SPEED</span><b id="cvSpd">0</b><i>km/h</i></div><div><span>ALTITUDE</span><b id="cvAlt">0</b><i>km</i></div></div>
        <div class="cv-c"><div class="cv-clock" id="cvClock">T−00:00:10</div><div class="cv-tl" id="cvTl"><div class="cv-prog" id="cvProg"></div></div><div id="cvStage" class="cv-stage"></div></div>
        <div class="cv-r"><div><span>DYN. PRESSURE</span><b id="cvQ">0</b><i>kPa</i></div><div><span>ACCEL</span><b id="cvG">0</b><i>g</i></div></div>
      </div></div>
    <div class="cv-bay" hidden><div class="cv-baybar"><label><input type="checkbox" id="cvXray"> X-ray: where each number came from</label>
      <span class="cv-legend"><i style="background:#4fe3a1"></i>published / read <i style="background:#5ab0ff"></i>derived <i style="background:#ffc857"></i>assumed <i style="background:#d3a6ff"></i>edited <i style="background:#ff6b57"></i>missing</span></div></div>`;
  const $ = (id) => root.querySelector('#' + id);
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setSize(innerWidth, innerHeight);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  root.prepend(renderer.domElement);
  const cam = new THREE.PerspectiveCamera(42, innerWidth / innerHeight, 0.1, 5e7);
  const controls = new OrbitControls(cam, renderer.domElement);
  controls.enableDamping = true;
  // reflections from a neutral studio environment (built in code, no asset) and bloom on bright things only
  const envTex = new THREE.PMREMGenerator(renderer).fromScene(new RoomEnvironment(), 0.04).texture;
  const composer = new EffectComposer(renderer);
  const renderPass = new RenderPass(new THREE.Scene(), cam);
  const bloom = new UnrealBloomPass(new THREE.Vector2(innerWidth, innerHeight), 0.35, 0.35, 0.95);
  composer.addPass(renderPass); composer.addPass(bloom); composer.addPass(new OutputPass());
  function draw(scene, strength) { renderPass.scene = scene; bloom.strength = strength; composer.render(); }
  addEventListener('resize', () => {
    cam.aspect = innerWidth / innerHeight; cam.updateProjectionMatrix(); renderer.setSize(innerWidth, innerHeight);
    composer.setSize(innerWidth, innerHeight);
  });
  const DOT = softDot();
  let mode = 'idle', visible = false;
  const labels = [];

  // ===================================================== BAY
  const bay = new THREE.Scene();
  bay.background = new THREE.Color(0x070b16);
  bay.environment = envTex; bay.environmentIntensity = 0.55;
  bay.fog = new THREE.Fog(0x070b16, 60, 260);
  bay.add(new THREE.HemisphereLight(0x9fbfff, 0x10141c, 0.55));
  const key = new THREE.DirectionalLight(0xffffff, 2.2); key.position.set(-30, 60, 40); key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048); Object.assign(key.shadow.camera, { left: -60, right: 60, top: 80, bottom: -10, far: 200 });
  bay.add(key, key.target);
  const rim = new THREE.DirectionalLight(0x5ab0ff, 1.2); rim.position.set(20, 30, -60); bay.add(rim);
  const floor = new THREE.Mesh(new THREE.CircleGeometry(300, 64), new THREE.MeshStandardMaterial({ color: 0x0d1424, roughness: 0.85 }));
  floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true; bay.add(floor);
  const grid = new THREE.GridHelper(300, 150, 0x1d2b4a, 0x121b30); grid.position.y = 0.01; bay.add(grid);
  const person = new THREE.Group();                                  // 1.8 m for scale
  const skin = new THREE.MeshStandardMaterial({ color: 0x8196c4, roughness: 0.7 });
  const bodyM = new THREE.Mesh(new THREE.CapsuleGeometry(0.22, 1.0, 4, 12), skin); bodyM.position.y = 0.72;
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.14, 16, 12), skin); head.position.y = 1.62;
  person.add(bodyM, head); person.traverse((c) => { c.castShadow = true; }); bay.add(person);
  const slots = [];                                                   // {id, v, r, x}
  let selected = null, xray = false;
  let bayGoal = null;

  function label(text, cls) {
    const el = document.createElement('div'); el.className = 'cv-lbl ' + (cls || ''); el.innerHTML = text; root.append(el); return el;
  }
  function clearSlots() { slots.forEach((s) => { bay.remove(s.r.group); disposeTree(s.r.group); s.el.remove(); }); slots.length = 0; }
  function placeSlots() {
    let x = 0;
    slots.forEach((s, i) => { const w = Math.max(s.r.D * 3.2, 4); if (i) x += w / 2; s.x = x; s.r.group.position.x = x; x += w / 2; });
    const mid = slots.length ? (slots[0].x + slots[slots.length - 1].x) / 2 : 0;
    slots.forEach((s) => { s.r.group.position.x -= mid; s.x -= mid; });
  }
  function slotLabel(s) {
    const v = s.v, mass = v.stages.reduce((a, st) => a + st.prop_kg + st.dry_kg, 0) + (v.fairing_kg || 0);
    s.el.innerHTML = `<b>${esc(v.name.replace(/\s*\(fictional\)/, ''))}</b><span>${fmt(s.r.height, 1)} m · ${fmt(mass / 1000, 1)} t</span>`;
  }
  function showBay(vehicles, currentId, current, payloadKg) {
    const jump = mode !== 'bay';                                     // coming from a launch: cut, don't fly across
    mode = 'bay'; setVisible(true);
    root.querySelector('.cv-hud').hidden = true; root.querySelector('.cv-bay').hidden = false;
    clearSlots();
    const list = Object.entries(vehicles).map(([id, v]) => ({ id, v: id === currentId && current ? current : v }));
    if (current && !(currentId in vehicles)) list.push({ id: currentId, v: current });
    list.forEach((it) => { const r = buildRocket(it.v, { payloadKg }); it.r = r; });
    list.sort((a, b) => a.r.height - b.r.height);
    for (const it of list) {
      bay.add(it.r.group); it.r.setXray(xray);
      it.el = label('', 'rk'); slots.push(it); slotLabel(it);
    }
    placeSlots();
    select(currentId);
    if (jump && bayGoal) { cam.position.copy(bayGoal.pos); controls.target.copy(bayGoal.target); cam.up.set(0, 1, 0); }
  }
  function select(id) {
    selected = slots.find((s) => s.id === id) || slots[0];
    slots.forEach((s) => s.el.classList.toggle('sel', s === selected));
    if (!selected) return;
    person.position.set(selected.x + selected.r.D * 0.9 + 0.6, 0, selected.r.D * 0.8);
    const h = selected.r.height, hr = Math.max(h, 17);          // small rockets: keep the neighbours for scale
    bayGoal = { target: new THREE.Vector3(selected.x + (hr - h) * 0.25, h * 0.5, 0), pos: new THREE.Vector3(selected.x + hr * 0.7, hr * 0.38, hr * 1.75 + 9) };
  }
  /** The selected rocket was edited: rebuild it, letting each stage grow from its old length (a fix is visible). */
  function updateCurrent(v, payloadKg) {
    if (mode !== 'bay' || !selected) return;
    const old = selected.r;
    const r = buildRocket(v, { payloadKg });
    r.stages.forEach((s, i) => { if (old.stages[i] && old.stages[i].dm.kick === s.dm.kick) s.L = old.stages[i].L; });
    r.layout();
    bay.remove(old.group); disposeTree(old.group);
    selected.r = r; selected.v = v; bay.add(r.group); r.setXray(xray);
    placeSlots(); slotLabel(selected); select(selected.id);
  }
  root.querySelector('#cvXray').addEventListener('change', (e) => { xray = e.target.checked; slots.forEach((s) => s.r.setXray(xray)); });

  // ===================================================== LAUNCH
  const sky = new THREE.Mesh(new THREE.SphereGeometry(1, 32, 16), new THREE.ShaderMaterial(SKY));
  sky.renderOrder = -1; sky.frustumCulled = false;
  const L = { scene: null };
  function buildGround(F, lat0, lon0) {
    const RINGS = 140, SEG = 160, rmin = 20, rmax = 2.6e6;
    const n = 1 + RINGS * SEG, pos = new Float32Array(n * 3), uv = new Float32Array(n * 2), idx = [];
    const v = new THREE.Vector3();
    const put = (k, lat, lon) => {
      toLocal(F, ecef(lat, lon), v); pos.set([v.x, v.y, v.z], k * 3);
      uv.set([((lon + 180) / 360) % 1, (lat + 90) / 180], k * 2);
    };
    put(0, lat0, lon0);
    for (let r = 0; r < RINGS; r++) {
      const d = rmin * Math.pow(rmax / rmin, r / (RINGS - 1));
      for (let s = 0; s < SEG; s++) put(1 + r * SEG + s, ...dest(lat0, lon0, (s / SEG) * 360, d));
    }
    for (let s = 0; s < SEG; s++) idx.push(0, 1 + ((s + 1) % SEG), 1 + s);
    for (let r = 0; r < RINGS - 1; r++) for (let s = 0; s < SEG; s++) {
      const a = 1 + r * SEG + s, b = 1 + r * SEG + ((s + 1) % SEG), c = a + SEG, d = b + SEG;
      idx.push(a, b, c, b, d, c);
    }
    // the dateline: keep u continuous inside each triangle
    for (let t = 0; t < idx.length; t += 3) {
      const us = [idx[t], idx[t + 1], idx[t + 2]].map((k) => uv[k * 2]);
      if (Math.max(...us) - Math.min(...us) > 0.5) for (const k of [idx[t], idx[t + 1], idx[t + 2]]) if (uv[k * 2] < 0.5) uv[k * 2] += 1;
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3)); g.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
    g.setIndex(idx); g.computeVertexNormals();
    const tex = earthTex.clone(); tex.wrapS = THREE.RepeatWrapping; tex.needsUpdate = true;
    const mat = new THREE.MeshStandardMaterial({ map: tex, roughness: 0.95, metalness: 0 });
    mat.onBeforeCompile = (sh) => {                                   // water (blue, dark) is smooth: it glints
      sh.fragmentShader = sh.fragmentShader.replace('#include <roughnessmap_fragment>', `
        float roughnessFactor = roughness;
        vec4 wtex = texture2D(map, vMapUv);
        float water = smoothstep(0.03, 0.12, wtex.b - max(wtex.r, wtex.g));
        roughnessFactor = mix(roughness, 0.22, water);`);
    };
    const m = new THREE.Mesh(g, mat);
    m.receiveShadow = true;
    return m;
  }
  function buildPad(height, D) {
    const g = new THREE.Group();
    const conc = new THREE.MeshStandardMaterial({ color: 0x9a9890, roughness: 0.95 });
    const steel = new THREE.MeshStandardMaterial({ color: 0x7c2f22, roughness: 0.6, metalness: 0.5 });
    const apron = new THREE.Mesh(new THREE.CylinderGeometry(70, 75, 1.2, 48), conc); apron.position.y = 0.6; apron.receiveShadow = true;
    const ring = new THREE.Mesh(new THREE.CircleGeometry(420, 48), new THREE.MeshStandardMaterial({ color: 0x5d6b4a, roughness: 1 }));
    ring.rotation.x = -Math.PI / 2; ring.position.y = 0.15; ring.receiveShadow = true;
    const mount = new THREE.Mesh(new THREE.BoxGeometry(D * 2.4, 2.2, D * 2.4), new THREE.MeshStandardMaterial({ color: 0x3b4048, roughness: 0.7, metalness: 0.4 }));
    mount.position.y = 2.3; mount.castShadow = mount.receiveShadow = true;
    const trench = new THREE.Mesh(new THREE.BoxGeometry(D * 1.6, 0.4, 30), new THREE.MeshStandardMaterial({ color: 0x222428 }));
    trench.position.set(0, 1.25, 16);
    g.add(ring, apron, mount, trench);
    const tower = new THREE.Group(), w = Math.max(1.6, D * 1.1), th = height * 1.08;
    for (const [x, z] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
      const leg = new THREE.Mesh(new THREE.BoxGeometry(0.18, th, 0.18), steel); leg.position.set(x * w / 2, th / 2, z * w / 2); tower.add(leg);
    }
    for (let y = 2; y < th; y += Math.max(1.5, th / 14)) {
      for (const [a, b, c] of [[w, 0.12, 0.12], [0.12, 0.12, w]]) {
        for (const s of [-1, 1]) { const bar = new THREE.Mesh(new THREE.BoxGeometry(a, b, c), steel); bar.position.set(a > 1 ? 0 : s * w / 2, y, a > 1 ? s * w / 2 : 0); tower.add(bar); }
      }
    }
    const arm = new THREE.Mesh(new THREE.BoxGeometry(D * 2.5 + w, 0.4, 0.6), steel); arm.position.set(-(D * 1.25 + w / 2) / 2, th * 0.82, 0); tower.add(arm);
    tower.position.set(D * 1.25 + w / 2 + 0.6, 1.2, 0);
    tower.traverse((c) => { c.castShadow = true; });
    g.add(tower);
    return g;
  }

  function sampleAt(S, t) {
    if (t <= S[0][0]) return S[0];
    let lo = 0, hi = S.length - 1;
    if (t >= S[hi][0]) return S[hi];
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (S[m][0] <= t) lo = m; else hi = m; }
    const a = S[lo], b = S[hi], f = (t - a[0]) / (b[0] - a[0] || 1);
    return a.map((x, k) => (typeof x === 'number' && k !== 6 ? x + (b[k] - x) * f : x));
  }

  let fl = null;   // the flight being shown
  function setupLaunch(flight, vehicle, site, opts) {
    disposeLaunch();
    const scene = new THREE.Scene();
    L.scene = scene;
    scene.environment = envTex; scene.environmentIntensity = 0.35;
    const S = flight.samples.map((s) => s.slice());
    S.unshift([0, 0, 0, 0, site.lat, site.lon, 0, 'burn', 90, 0, S[0][10], S[0][11], S[0][12]]);
    const F = frameAt(site.lat, site.lon);
    const date = opts.date || new Date();
    const [sl, so] = subsolar(date);
    const sunL = toLocal(F, ecef(sl, so, 1.5e11)).normalize();
    if (opts.inOrbit && sunL.y < 0.35) sunL.set(0.5, 0.6, 0.62).normalize();   // deploy shot: illustrative daylight
    const hemi = new THREE.HemisphereLight(0xbfd6ff, 0x404030, 0.6); scene.add(hemi);
    const dayK = THREE.MathUtils.smoothstep(sunL.y, -0.12, 0.08);   // 0 at night, 1 in daylight
    sky.material.uniforms.day.value = dayK;
    hemi.intensity = 0.12 + 0.48 * dayK;
    const sunLight = new THREE.DirectionalLight(0xfff2dd, 0.15 + 2.45 * dayK); sunLight.castShadow = true;
    const plumeLight = new THREE.PointLight(0xffa64d, 0, 4000, 1.6); scene.add(plumeLight);
    const cover = opts.cloudCover ?? 25;                            // % from the forecast hour, or illustrative
    let clouds = null;
    if (cover > 3 && !opts.inOrbit) {
      clouds = new THREE.Mesh(new THREE.PlaneGeometry(26000, 26000), new THREE.MeshLambertMaterial({ map: cloudTexture(cover), transparent: true,
        opacity: 0.7, depthWrite: false, side: THREE.DoubleSide, fog: false, color: new THREE.Color().setScalar(0.05 + 0.95 * dayK) }));
      clouds.rotation.x = -Math.PI / 2; clouds.position.y = 2600; clouds.renderOrder = 2; scene.add(clouds);
    }
    sunLight.shadow.mapSize.set(2048, 2048);
    scene.add(sunLight, sunLight.target);
    sky.scale.setScalar(4e7); scene.add(sky); sky.material.uniforms.sun.value.copy(sunL);
    scene.fog = new THREE.Fog(0xbcd2ee, 2e3, 9e4);
    const ground = buildGround(F, site.lat, site.lon); scene.add(ground);
    const rk = buildRocket(vehicle, { payloadKg: flight.payload_kg, name: opts.name });
    const base = opts.inOrbit ? 0 : 3.4;                         // top of the launch mount
    if (!opts.inOrbit) { const pad = buildPad(rk.height + rk.lay.dims[0].bell + base, rk.D); scene.add(pad); }
    const holder = new THREE.Group(); holder.add(rk.group); rk.group.position.y = rk.lay.dims[0].bell; scene.add(holder);
    rk.group.traverse((c) => { if (c.isMesh) c.castShadow = true; });
    // plumes: two cones per nozzle (core + glow), on every stage, shown for the burning one
    const flames = rk.stages.map((s) => s.nozzles.map((p) => {
      const mk = (w, len, pw) => {
        const m = new THREE.Mesh(new THREE.ConeGeometry(1, 1, 24, 1, true), new THREE.ShaderMaterial({ ...FLAME, uniforms: THREE.UniformsUtils.clone(FLAME.uniforms) }));
        m.rotation.x = Math.PI; m.userData = { w, len, pw }; m.frustumCulled = false; return m;
      };
      const g = new THREE.Group(); g.position.set(p.x, 0, p.z);
      const core = mk(0.55, 5, 1), glow = mk(1.4, 9, 0.55); g.add(core, glow);
      core.material.blending = THREE.NormalBlending; core.material.uniforms.edge.value.setRGB(1, 0.55, 0.15);
      s.eng.add(g); g.visible = false; return { g, core, glow };
    }));
    const diamonds = rk.stages[0].nozzles.map((p) => {
      const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: DOT, color: 0xfff4d0, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false }));
      sp.position.set(p.x, 0, p.z); rk.stages[0].eng.add(sp); return sp;
    });
    const smoke = new Particles(9000, DOT); scene.add(smoke.points);
    const spark = new Particles(1500, DOT, THREE.AdditiveBlending); scene.add(spark.points);
    const events = flight.events.map((e) => ({ ...e }));
    const sepT = [];                                                // times the active stage index changes
    for (let k = 1; k < S.length; k++) if (S[k][6] > S[k - 1][6]) sepT.push({ t: S[k][0], from: S[k - 1][6], to: S[k][6] });
    const fairT = events.find((e) => /fairing/.test(e.event))?.t;
    const s1Out = sepT[0]?.t ?? events.find((e) => /burnout/.test(e.event))?.t ?? 120;
    let tHand = Math.max((fairT ?? 0) + 14, s1Out + 22);
    const tAlt = S.find((x) => x[1] > 165)?.[0];
    if (tAlt) tHand = Math.min(tHand, tAlt);
    tHand = Math.min(tHand, S[S.length - 1][0]);
    fl = { flight, vehicle, site, S, F, scene, rk, holder, flames, diamonds, smoke, spark, sunL, sunLight, ground, sepT, fairT, tHand,
      plumeLight, dayK, clouds, cloudOpacity: 0.7,
      t: -10, rate: 1, camMode: 'auto', spent: [], fairDone: false, toast: [], done: false, opts, base, hadSep: new Set(),
      pos: new THREE.Vector3(), dir: new THREE.Vector3(0, 1, 0), up: new THREE.Vector3(0, 1, 0), along: new THREE.Vector3(1, 0, 0), vel: new THREE.Vector3() };
    // HUD timeline
    const tl = $('cvTl'); tl.querySelectorAll('.cv-mk').forEach((x) => x.remove());
    const marks = [{ t: flight.max_q_t, event: 'MAX Q' },        // the moments people watch for, not every ignition
      ...sepT.map((x) => ({ t: x.t, event: `${vehicle.stages[x.from]?.name || 'stage'} sep` })),
      ...(fairT ? [{ t: fairT, event: 'Fairing' }] : [])].filter((m) => m.t > 0 && m.t <= tHand + 1);
    let prevT = -1e9, alt = false;
    for (const m of marks.sort((x, y) => x.t - y.t)) {
      alt = m.t - prevT < tHand * 0.09 ? !alt : false; prevT = m.t;   // close labels alternate above / below
      const el = document.createElement('div'); el.className = 'cv-mk' + (alt ? ' alt' : ''); el.style.left = `${(m.t / tHand) * 100}%`;
      el.title = m.event; el.innerHTML = `<i></i><span>${esc(m.event.replace(/:.*/, '').replace(/ ignition/, ' ign.'))}</span>`; tl.append(el);
    }
    $('cvTitle').textContent = `${vehicle.name} · ${flight.payload_kg} kg · ${site.name}`;
    root.querySelector('.cv-note').textContent = NOTE;
    // stage stack diagram
    const tot = rk.stages.reduce((a, x) => a + x.dm.L, 0) + rk.lay.fairingL;
    $('cvStack').innerHTML = `<div class="fair" style="flex:${rk.lay.fairingL / tot}"></div>` + [...rk.stages].reverse()
      .map((x, i) => `<div class="st" data-i="${rk.stages.length - 1 - i}" style="flex:${x.dm.L / tot}"><span>${esc(x.dm.name)}</span></div>`).join('');
    fl.mapT = -1;
  }
  // ground track on the real Earth texture, and altitude / speed over time
  function drawSide(f, t) {
    const S = f.Sall || f.S, img = earthTex.image, cv = $('cvMap'), g = cv.getContext('2d');
    if (!f.mapBox) {
      const end = f.deploy ? S.length : S.findIndex((x) => x[0] > f.tHand) + 1 || S.length;
      const lon0 = f.site.lon, un = (lo) => lon0 + ((lo - lon0 + 540) % 360) - 180;
      const pts = S.slice(0, end).map((x) => [x[4], un(x[5])]);
      let [a, b, c, d] = [Math.min(...pts.map((p) => p[0])), Math.max(...pts.map((p) => p[0])), Math.min(...pts.map((p) => p[1])), Math.max(...pts.map((p) => p[1]))];
      const pad = Math.max(3, (b - a) * 0.25, (d - c) * 0.25);
      a -= pad; b += pad; c -= pad; d += pad;
      const want = cv.width / cv.height;                              // keep the map's aspect (degrees, roughly)
      if ((d - c) / (b - a) < want) { const m = (c + d) / 2, w = (b - a) * want / 2; c = m - w; d = m + w; }
      else { const m = (a + b) / 2, h = (d - c) / want / 2; a = m - h; b = m + h; }
      f.mapBox = { a: Math.max(-89, a), b: Math.min(89, b), c, d, un, pts };
    }
    const B = f.mapBox, W = img.width, H = img.height;
    const X = (lo) => (lo - B.c) / (B.d - B.c) * cv.width, Y = (la) => (B.b - la) / (B.b - B.a) * cv.height;
    g.fillStyle = '#04070f'; g.fillRect(0, 0, cv.width, cv.height);
    for (const shift of [-360, 0, 360]) {                           // the texture repeats across the dateline
      const c0 = B.c - shift, d0 = B.d - shift;
      const sx0 = Math.max(0, (c0 + 180) / 360 * W), sx1 = Math.min(W, (d0 + 180) / 360 * W);
      if (sx1 <= sx0) continue;
      const sy0 = (90 - B.b) / 180 * H, sy1 = (90 - B.a) / 180 * H;
      const dx0 = X(sx0 / W * 360 - 180 + shift), dx1 = X(sx1 / W * 360 - 180 + shift);
      g.drawImage(img, sx0, sy0, sx1 - sx0, sy1 - sy0, dx0, 0, dx1 - dx0, cv.height);
    }
    g.fillStyle = 'rgba(4,8,18,.25)'; g.fillRect(0, 0, cv.width, cv.height);
    const now = S.findIndex((x) => x[0] > t); const k = now < 0 ? B.pts.length : Math.min(now, B.pts.length);
    g.lineWidth = 2.5; g.strokeStyle = 'rgba(255,255,255,.35)'; g.setLineDash([6, 6]); g.beginPath();
    B.pts.forEach(([la, lo], i) => (i ? g.lineTo(X(lo), Y(la)) : g.moveTo(X(lo), Y(la)))); g.stroke(); g.setLineDash([]);
    g.strokeStyle = '#ffb347'; g.lineWidth = 3.5; g.beginPath();
    B.pts.slice(0, k).forEach(([la, lo], i) => (i ? g.lineTo(X(lo), Y(la)) : g.moveTo(X(lo), Y(la)))); g.stroke();
    const p = B.pts[Math.max(0, k - 1)]; g.fillStyle = '#fff'; g.beginPath(); g.arc(X(p[1]), Y(p[0]), 6, 0, 7); g.fill();
    g.fillStyle = '#5ab0ff'; g.beginPath(); g.arc(X(B.un(f.site.lon)), Y(f.site.lat), 5, 0, 7); g.fill();
    // graphs
    const gc = $('cvGraph'), q = gc.getContext('2d'), T = f.deploy ? S[S.length - 1][0] : f.tHand;
    const rows = S.filter((x) => x[0] <= T), amax = Math.max(...rows.map((x) => x[1]), 1), vmax = Math.max(...rows.map((x) => x[3]), 1);
    q.clearRect(0, 0, gc.width, gc.height); q.fillStyle = 'rgba(4,8,18,.55)'; q.fillRect(0, 0, gc.width, gc.height);
    const line = (idx, max, col) => {
      q.strokeStyle = col; q.lineWidth = 3; q.beginPath();
      rows.filter((x) => x[0] <= t).forEach((x, i) => { const px = x[0] / T * gc.width, py = gc.height - 8 - x[idx] / max * (gc.height - 18); i ? q.lineTo(px, py) : q.moveTo(px, py); });
      q.stroke();
    };
    line(1, amax, '#5ab0ff'); line(3, vmax, '#ffc857');
    q.fillStyle = 'rgba(219,230,255,.7)'; q.font = '20px sans-serif';
    q.fillText(`${fmt(amax)} km`, 8, 22); q.fillText(`${fmt(vmax / 1000, 1)} km/s`, gc.width - 110, 22);
  }
  function disposeLaunch() {
    if (!fl) return;
    fl.scene.remove(sky); disposeTree(fl.scene); fl = null;
  }
  function playLaunch(flight, vehicle, site, opts = {}) {
    disposeEncounter();
    mode = 'launch'; setVisible(true);
    root.querySelector('.cv-bay').hidden = true; root.querySelector('.cv-hud').hidden = false;
    clearSlots();
    setupLaunch(flight, vehicle, site, opts);
    controls.enabled = false;
  }

  function toast(text) {
    const el = $('cvToast'); el.textContent = text; el.classList.remove('show'); void el.offsetWidth; el.classList.add('show');
  }
  function separate(s) {                                           // a spent stage leaves the stack
    const st = fl.rk.stages[s.from];
    if (!st || fl.hadSep.has(s.from)) return;
    fl.hadSep.add(s.from);
    const wp = new THREE.Vector3(), wq = new THREE.Quaternion();
    st.g.getWorldPosition(wp); st.g.getWorldQuaternion(wq);
    fl.holder.remove(st.g); st.g.parent?.remove(st.g);
    fl.scene.add(st.g); st.g.position.copy(wp); st.g.quaternion.copy(wq);
    fl.flames[s.from].forEach((f) => { f.g.visible = false; });
    st.g.userData.vel = fl.vel.clone().addScaledVector(fl.dir, -2.5);
    st.g.userData.spin = new THREE.Vector3((Math.random() - 0.5) * 0.4, (Math.random() - 0.5) * 0.2, (Math.random() - 0.5) * 0.4);
    fl.spent.push(st.g);
    const nx = fl.rk.stages[s.to]; if (nx) { nx.revealed = true; }
    // the remaining stack's origin moves up to the next stage: shift so the camera framing follows the live part
    const off = nx ? nx.g.position.y : 0;
    fl.rk.group.position.y -= off; fl.stackOff = (fl.stackOff || 0) + off;
    fl.rk.stages.forEach((x) => { x.eng.visible = x === nx || x.dm.kick || x.revealed; });
    for (let k = 0; k < 40; k++) fl.spark.emit(wp.clone().addScaledVector(fl.dir, st.L), new THREE.Vector3((Math.random() - 0.5) * 6, (Math.random() - 0.5) * 6, (Math.random() - 0.5) * 6), 1.5, 1.5, 4, 0.6, [0.9, 0.95, 1]);
    toast(`${st.dm.name.toUpperCase()} SEPARATION`);
  }
  function jettisonFairing() {
    fl.fairDone = true;
    fl.rk.fairing.children.forEach((p, k) => {
      const wp = new THREE.Vector3(), wq = new THREE.Quaternion();
      p.getWorldPosition(wp); p.getWorldQuaternion(wq);
      fl.rk.fairing.remove(p); fl.scene.add(p); p.position.copy(wp); p.quaternion.copy(wq);
      const side = new THREE.Vector3(k ? -1 : 1, 0, 0).applyQuaternion(wq);
      p.userData.vel = fl.vel.clone().addScaledVector(side, 3).addScaledVector(fl.dir, -1);
      p.userData.spin = new THREE.Vector3(0, 0, (k ? 1 : -1) * 0.5).applyQuaternion(wq);
      fl.spent.push(p);
    });
    toast('FAIRING SEPARATION');
  }

  function cameraFor(modeName, dt) {
    const P = fl.pos, h = fl.rk.height, D = fl.dir, U = fl.up;
    const side = new THREE.Vector3().crossVectors(D, fl.along).normalize();
    if (side.lengthSq() < 0.1) side.set(1, 0, 0);
    const mid = P.clone().addScaledVector(D, h * 0.5);
    let pos, look = mid, fov = 42;
    if (modeName === 'pad') {
      pos = new THREE.Vector3(h * 2.6, h * 0.25 + 4, h * 3.2);
      const dist = pos.distanceTo(mid); fov = THREE.MathUtils.clamp(2 * Math.atan(h * 1.6 / dist) / D2R, 6, 55);
    } else if (modeName === 'track') {
      pos = new THREE.Vector3(-2600, 40, 1800);
      const dist = pos.distanceTo(mid); fov = THREE.MathUtils.clamp(2 * Math.atan(h * 1.9 / dist) / D2R, 0.15, 50);
    } else if (modeName === 'onboard') {
      pos = P.clone().addScaledVector(D, h * 0.78).addScaledVector(side, fl.rk.D * 0.62);
      look = P.clone().addScaledVector(D, -60).addScaledVector(side, fl.rk.D * 0.2); fov = 70;
    } else if (modeName === 'deploy') {
      const pay = new THREE.Vector3(); fl.rk.payload.getWorldPosition(pay);
      const top = P.clone().addScaledVector(D, h * 0.6);
      pos = top.clone().addScaledVector(side, 2.2 + h * 0.5).addScaledVector(U, 1.6 + h * 0.35).addScaledVector(D, -0.8);
      look = top.clone().lerp(pay, 0.6).addScaledVector(U, -0.9); fov = 46;
    } else {                                                        // chase
      pos = P.clone().addScaledVector(D, -h * 1.1).addScaledVector(side, h * 0.9).addScaledVector(U, h * 0.35);
      fov = 40;
    }
    // Smooth the camera's offset from the rocket, not its absolute position: at km/s an absolute lerp falls
    // kilometres behind. Ground cameras (pad, tracking) stay put and aim straight at the rocket.
    const k = 1 - Math.exp(-dt * 2.5);
    const ground = modeName === 'pad' || modeName === 'track';
    const off = pos.clone().sub(P), lookOff = look.clone().sub(P);
    if (!fl.camInit || ground !== fl.lastGround) { fl.camOff = off.clone(); fl.lookOff = lookOff.clone(); fl.camInit = true; }
    if (ground) { cam.position.copy(pos); fl.camOff.copy(off); } else { fl.camOff.lerp(off, k); cam.position.copy(P).add(fl.camOff); }
    fl.lookOff.lerp(lookOff, ground ? 1 : k);
    fl.lastGround = ground; fl.lastCam = modeName;
    cam.up.copy(U);
    const shake = modeName === 'pad' && fl.t > -2 && fl.t < 12 ? 0.25 * Math.max(0, 1 - fl.t / 12) : modeName === 'chase' && fl.t < 60 ? 0.04 : 0;
    if (shake) cam.position.add(new THREE.Vector3((Math.random() - 0.5) * shake, (Math.random() - 0.5) * shake, (Math.random() - 0.5) * shake));
    cam.lookAt(P.clone().add(fl.lookOff));
    cam.fov += (fov - cam.fov) * (modeName === 'track' ? 1 : k);
    cam.near = Math.max(0.05, cam.position.distanceTo(P) * 0.01); cam.far = 3e7; cam.updateProjectionMatrix();
  }
  function autoCam(t) {
    if (fl.deploy) return 'deploy';
    if (t < 9) return 'pad';
    if (t < 55) return 'track';
    const near = fl.sepT.some((s) => Math.abs(t - s.t) < 6) || (fl.fairT && Math.abs(t - fl.fairT) < 6);
    return near ? 'chase' : t > fl.sepT[0]?.t + 20 && t < fl.tHand - 10 ? 'onboard' : 'chase';
  }
  function rateFor(t) {
    if (fl.deploy) return 1;
    if (t < 20) return 1;
    const near = fl.sepT.some((s) => t > s.t - 5 && t < s.t + 7) || (fl.fairT && t > fl.fairT - 4 && t < fl.fairT + 6);
    return near ? 1 : t < 60 ? 2.5 : 4;
  }

  const tmp = new THREE.Vector3();
  function stepLaunch(dtReal) {
    const f = fl; if (!f) return;
    f.rate += (rateFor(f.t) - f.rate) * Math.min(1, dtReal * 2);
    const dt = dtReal * f.rate;
    f.t += dt;
    const t = Math.max(0, f.t);
    const s = sampleAt(f.S, t), s2 = sampleAt(f.S, t + 1);
    const p = ecef(s[4], s[5], s[1] * 1000), p2 = ecef(s2[4], s2[5], s2[1] * 1000);
    toLocal(f.F, p, f.pos); toLocal(f.F, p2, tmp);
    f.vel.copy(tmp).sub(f.pos);
    const center = new THREE.Vector3(0, -R, 0);
    f.up.copy(f.pos).sub(center).normalize();
    const hor = f.vel.clone().addScaledVector(f.up, -f.vel.dot(f.up));
    if (hor.lengthSq() > 1e-4) f.along.copy(hor.normalize());
    else { const az = (f.flight.azimuth_deg || 0) * D2R; f.along.set(Math.sin(az), 0, -Math.cos(az)); }   // x east, z south
    const pitch = (f.t < 0 ? 90 : s[8] ?? 90) * D2R;
    f.dir.copy(f.along).multiplyScalar(Math.cos(pitch)).addScaledVector(f.up, Math.sin(pitch)).normalize();
    // stack position: the live stack's bottom follows the trajectory; the pad offset fades out after clearing the tower
    const lift = f.base * Math.max(0, 1 - Math.max(0, f.t) / 6);   // nozzles leave the mount, then follow the track
    f.holder.position.copy(f.pos).addScaledVector(f.up, lift);
    f.holder.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), f.dir);
    // events
    for (const sp of f.sepT) if (f.t >= sp.t && !f.hadSep.has(sp.from)) separate(sp);
    if (f.fairT && f.t >= f.fairT && !f.fairDone) jettisonFairing();
    if (f.t > -0.1 && !f.lifted) { f.lifted = true; toast('LIFTOFF'); }
    if (f.flight.max_q_t && f.t >= f.flight.max_q_t && !f.maxq) { f.maxq = true; toast(`MAX Q · ${fmt(f.flight.max_q_kpa, 1)} kPa`); }
    // plume
    const burning = f.t > -2.5 && (f.t < 0 || s[7] === 'burn' || s[7] === 'circ') && !f.deploy;
    const pr = Math.exp(-s[1] / 7.2);
    const stIdx = f.t < 0 ? 0 : s[6];
    f.flames.forEach((fs, i) => fs.forEach((x) => {
      const on = burning && i === stIdx;
      x.g.visible = on;
      if (!on) return;
      const dm = f.rk.stages[i].dm, thr = f.t < 0 ? Math.min(1, (f.t + 2.5) / 2) : 1;
      for (const m of [x.core, x.glow]) {
        const u = m.userData, wide = 1 + 2.6 * (1 - pr) * (u.pw < 1 ? 1.4 : 1);
        m.scale.set(dm.re * u.w * wide, dm.bell * u.len * thr * (1 + 1.2 * (1 - pr)), dm.re * u.w * wide);
        m.position.y = -dm.bell - m.scale.y / 2;
        m.material.uniforms.time.value = performance.now() / 1000;
        m.material.uniforms.power.value = u.pw * thr * (dm.solid ? 1.3 : 1) * (0.35 + 0.65 * pr) * (u.pw < 1 ? 0.6 + 0.4 * pr : 1);
      }
    }));
    f.diamonds.forEach((d, k) => {
      const dm = f.rk.stages[0].dm;
      d.visible = burning && stIdx === 0 && pr > 0.2;
      d.position.y = -dm.bell - dm.bell * (0.9 + (k % 3) * 0.7);
      d.scale.setScalar(dm.re * 1.2); d.material.opacity = pr * 0.8;
    });
    // smoke and ground cloud (sim-time emission so faster playback leaves the same trail)
    if (burning && s[1] < 45) {
      const n = Math.min(25, Math.ceil(dt * (s[1] < 2 ? 70 : 40)));
      const noz = new THREE.Vector3(); f.rk.stages[stIdx].eng.getWorldPosition(noz);
      const tail = noz.clone().addScaledVector(f.dir, -f.rk.lay.dims[stIdx].bell * 9);
      for (let k = 0; k < n; k++) {
        const jitter = new THREE.Vector3(Math.random() - 0.5, Math.random() - 0.5, Math.random() - 0.5).multiplyScalar(4);
        const back = f.vel.clone().multiplyScalar(Math.random()).negate().multiplyScalar(0.0);
        const at = tail.clone().addScaledVector(f.vel, -Math.random() * dt);
        const grey = (0.78 + Math.random() * 0.15) * (0.25 + 0.75 * f.dayK);   // smoke is dark at night
        f.smoke.emit(at, jitter.add(back), 20 + Math.random() * 15, f.rk.D * 1.5, f.rk.D * (6 + 20 * (1 - pr)), 0.22 * (0.4 + 0.6 * pr), [grey, grey, grey * 1.02]);
      }
      if (f.t < 3) for (let k = 0; k < 6; k++) {                  // ground cloud from the flame trench
        const a = Math.random() * Math.PI * 2, sp = 12 + Math.random() * 18;
        f.smoke.emit(new THREE.Vector3(Math.cos(a) * 3, 2, Math.sin(a) * 3 + 6), new THREE.Vector3(Math.cos(a) * sp, 1 + Math.random() * 3, Math.sin(a) * sp),
          12 + Math.random() * 8, 4, 16, 0.2, [0.86, 0.85, 0.83].map((c) => c * (0.25 + 0.75 * f.dayK)));
      }
    }
    f.smoke.step(dt, 0.25); f.spark.step(dt, 1.2);
    const ppx = renderer.domElement.height / (2 * Math.tan(cam.fov * D2R / 2));    // metres -> pixels at 1 m
    f.smoke.points.material.uniforms.scale.value = ppx; f.spark.points.material.uniforms.scale.value = ppx;
    // spent parts: gravity toward Earth's centre, light drag low down
    for (const o of f.spent) {
      const v = o.userData.vel, g = o.position.clone().sub(center).normalize().multiplyScalar(-9.8);
      v.addScaledVector(g, dt); if (s[1] < 80) v.multiplyScalar(Math.exp(-0.02 * dt));
      o.position.addScaledVector(v, dt);
      const w = o.userData.spin; o.rotation.x += w.x * dt; o.rotation.y += w.y * dt; o.rotation.z += w.z * dt;
    }
    // deploy
    if (f.deploy) {
      const d = f.deploy, k = Math.min(1, (f.t - d.t0) / 5);
      f.rk.payload.position.y = d.y0 + Math.max(0, f.t - d.t0 - 1.5) * 0.35;
      f.rk.panels.forEach((h, i) => { h.children[0].visible = true; h.rotation.y = (i ? 1 : -1) * Math.PI / 2 * 0.98 * (1 - THREE.MathUtils.smoothstep(k, 0.35, 1)); });
      if (f.t - d.t0 > 11 && !f.done) { f.done = true; f.opts.onDone?.(); }
    }
    // light, sky, fog follow the rocket
    f.sunLight.position.copy(f.pos).addScaledVector(f.sunL, 400); f.sunLight.target.position.copy(f.pos);
    const sc = Math.max(30, f.rk.height * 2.5);
    Object.assign(f.sunLight.shadow.camera, { left: -sc, right: sc, top: sc, bottom: -sc, near: 1, far: 1200 }); f.sunLight.shadow.camera.updateProjectionMatrix();
    sky.material.uniforms.alt.value = s[1]; sky.position.copy(cam.position);
    // the plume lights its surroundings (most visible at night); clouds and rumble follow the burn
    f.plumeLight.visible = burning; f.plumeLight.intensity = burning ? (1.5 + 6 * (1 - f.dayK)) * f.rk.D : 0;
    f.rk.stages[stIdx]?.eng.getWorldPosition(f.plumeLight.position); f.plumeLight.position.addScaledVector(f.dir, -f.rk.lay.dims[stIdx].bell * 2);
    if (f.clouds) f.clouds.material.opacity = f.cloudOpacity * (s[1] < 12 ? 1 : Math.max(0, 1 - (s[1] - 12) / 20));
    rumble(burning && s[1] < 40 ? Math.min(1, 1.2 * pr + 0.15) * (f.camMode === 'onboard' || autoCam(f.t) === 'onboard' ? 0.6 : 1) : 0);
    if (s[1] > 150) {                 // from orbit: the atmosphere as a blue haze over the ground
      f.scene.fog.near = s[1] * 300; f.scene.fog.far = s[1] * 2600; f.scene.fog.color.setRGB(0.22, 0.4, 0.74);
    } else {
      f.scene.fog.near = 2e3 + s[1] * 4e3; f.scene.fog.far = 9e4 + s[1] * 6e4;
      f.scene.fog.color.setRGB(0.74 - Math.min(0.6, s[1] / 60), 0.83 - Math.min(0.65, s[1] / 60), 0.94 - Math.min(0.7, s[1] / 70)).multiplyScalar(0.06 + 0.94 * f.dayK);
    }
    // camera
    cameraFor(f.camMode === 'auto' ? autoCam(f.t) : f.camMode, dtReal);
    // HUD
    $('cvClock').textContent = clock(f.t);
    $('cvSpd').textContent = fmt(f.t < 0 ? 0 : s[3] * 3.6);
    $('cvAlt').textContent = fmt(f.t < 0 ? 0 : s[1], s[1] < 10 ? 2 : 1);
    $('cvQ').textContent = fmt(f.t < 0 ? 0 : s[9], 1);
    $('cvG').textContent = fmt(f.t < 0 ? 0 : s[10], 2);
    $('cvStage').textContent = f.deploy ? 'PAYLOAD DEPLOY' : `${f.vehicle.stages[stIdx]?.name || ''} · ${burning ? 'BURNING' : 'COAST'}`;
    $('cvProg').style.width = `${Math.min(100, Math.max(0, f.t / f.tHand * 100))}%`;
    root.querySelectorAll('#cvStack .st').forEach((el) => {
      const i = +el.dataset.i;
      el.classList.toggle('on', burning && i === stIdx); el.classList.toggle('gone', f.hadSep.has(i));
    });
    root.querySelector('#cvStack .fair')?.classList.toggle('gone', !!f.fairDone);
    if (performance.now() - (f.mapT || 0) > 250 && earthTex.image) { f.mapT = performance.now(); drawSide(f, Math.max(0, f.t)); }
    if (!f.deploy && f.t >= f.tHand && !f.handed) { f.handed = true; f.opts.onHandoff?.(f.tHand); }
  }

  /** In orbit: the last stage, the payload leaving it and opening its panels. */
  function playDeploy(flight, vehicle, site, opts = {}) {
    const S = flight.samples, last = S[S.length - 1];
    mode = 'launch'; setVisible(true);
    root.querySelector('.cv-bay').hidden = true; root.querySelector('.cv-hud').hidden = false;
    const at = { ...site, lat: last[4], lon: last[5] };
    setupLaunch({ ...flight, samples: S }, vehicle, at, { ...opts, inOrbit: true });
    const f = fl;
    // keep only the last stage and the payload
    f.rk.stages.forEach((st, i) => { if (i < f.rk.stages.length - 1) { st.g.parent.remove(st.g); } else { st.revealed = true; st.eng.visible = true; } });
    f.rk.group.position.y = -f.rk.stages[f.rk.stages.length - 1].g.position.y;
    f.rk.fairing.visible = false; f.fairDone = true;
    f.sepT = []; f.fairT = null; f.hadSep = new Set(f.rk.stages.map((_, i) => i));
    f.Sall = S.map((x) => x.slice()); f.mapBox = null;
    f.S = [S[S.length - 2] || last, last].map((x) => x.slice());
    f.t = last[0]; f.base = 0; f.deploy = { t0: last[0] + 2, y0: f.rk.payload.position.y }; f.lifted = true; f.maxq = true;
    toast(`ORBIT · ${fmt(flight.perigee_km)} × ${fmt(flight.apogee_km)} km · ${flight.inclination_deg}°`);
    setTimeout(() => toast('PAYLOAD DEPLOYED'), 3500);
  }

  // ---------- a close approach in slow motion: the other object's path relative to ours ----------
  let enc = null;
  function playEncounter(e, opts = {}) {
    disposeLaunch(); disposeEncounter();
    mode = 'encounter'; setVisible(true);
    root.querySelector('.cv-bay').hidden = true; root.querySelector('.cv-hud').hidden = false;
    root.classList.add('enc');
    const scene = new THREE.Scene(), F = frameAt(e.lat, e.lon), alt = e.alt_km * 1000;
    scene.add(new THREE.HemisphereLight(0xbfd6ff, 0x404030, 0.8));
    const sunL = toLocal(F, ecef(...subsolar(new Date(e.tca_utc)), 1.5e11)).normalize();
    const sun = new THREE.DirectionalLight(0xffffff, 2.2); sun.position.copy(sunL).multiplyScalar(1e6); scene.add(sun);
    sky.scale.setScalar(4e7); sky.material.uniforms.alt.value = e.alt_km; sky.material.uniforms.day.value = 1; sky.material.uniforms.sun.value.copy(sunL); scene.add(sky);
    scene.fog = new THREE.Fog(0x3866bd, alt * 0.3, alt * 2.6);
    const ground = buildGround(F, e.lat, e.lon); scene.add(ground);
    const at = new THREE.Vector3(0, alt, 0);
    const toV = ([, r, t, n]) => new THREE.Vector3(t * 1000, r * 1000, n * 1000).add(at);   // T east-ish, R up, N across
    const mk = (col, size) => { const p = new THREE.Points(new THREE.BufferGeometry().setFromPoints([at]),
      new THREE.PointsMaterial({ color: col, size, sizeAttenuation: false, map: DOT, transparent: true, depthWrite: false })); scene.add(p); return p; };
    const me = mk(0x4fe3a1, 22), other = mk(0xffb347, 22);
    const trail = new THREE.Line(new THREE.BufferGeometry().setFromPoints(e.track_rtn_km.map(toV)), new THREE.LineBasicMaterial({ color: 0xffb347, transparent: true, opacity: 0.85 }));
    trail.geometry.setDrawRange(0, 0); scene.add(trail);
    const k0 = Math.floor(e.track_rtn_km.length / 2), pTca = toV(e.track_rtn_km[k0]);
    const missLine = new THREE.Line(new THREE.BufferGeometry().setFromPoints([at, pTca]), new THREE.LineDashedMaterial({ color: 0xffffff, dashSize: 120, gapSize: 80 }));
    missLine.computeLineDistances(); missLine.visible = false; scene.add(missLine);
    const relDir = toV(e.track_rtn_km[k0 + 5]).sub(pTca).normalize(), missDir = pTca.clone().sub(at).normalize();
    const side = new THREE.Vector3().crossVectors(relDir, missDir).normalize();
    const dist = Math.max(3500, e.miss_km * 1000 * 3.2);
    cam.position.copy(at).add(pTca).multiplyScalar(0.5).addScaledVector(side, dist).addScaledVector(new THREE.Vector3(0, 1, 0), dist * 0.35);
    cam.up.set(0, 1, 0); cam.fov = 50; cam.near = 1; cam.far = 3e7; cam.updateProjectionMatrix();
    cam.lookAt(at.clone().add(pTca).multiplyScalar(0.5).addScaledVector(new THREE.Vector3(0, 1, 0), -dist * 0.55));   // Earth's limb in view
    const lblMe = label(`<b>${esc(e.ours)}</b>`, 'enc me'), lblOther = label(`<b>${esc(e.other)}</b>`, 'enc'), lblMiss = label('', 'enc miss');
    $('cvTitle').textContent = `${e.ours} × ${e.other} · closest approach ${e.tca_utc.slice(0, 16).replace('T', ' ')} UTC`;
    root.querySelector('.cv-note').textContent = 'Positions from SGP4 on public TLE data (about 1 km) · slow motion, 1 s of the encounter = 25 s · markers not to scale';
    enc = { e, scene, at, toV, me, other, trail, missLine, lbls: [lblMe, lblOther, lblMiss], k0, pTca, t: -1.0, opts, shown: false };
  }
  function disposeEncounter() {
    if (!enc) return;
    enc.lbls.forEach((l) => l.remove()); enc.scene.remove(sky); disposeTree(enc.scene); enc = null; root.classList.remove('enc');
  }
  function stepEncounter(dt) {
    const E = enc; if (!E) return;
    E.t = Math.min(1.0, E.t + dt * 0.04);
    const tr = E.e.track_rtn_km, k = Math.max(0, Math.min(tr.length - 1, Math.round((E.t + 2) / 0.02)));
    const p = E.toV(tr[k]);
    E.other.geometry.setFromPoints([p]); E.trail.geometry.setDrawRange(Math.max(0, k - 60), Math.min(k, 60) + 1);
    if (E.t >= 0 && !E.shown) { E.shown = true; E.missLine.visible = true; toast(`CLOSEST APPROACH · ${fmt(E.e.miss_km, 2)} km · ${fmt(E.e.rel_speed_kms, 1)} km/s`); }
    const put = (el, v, dy = 0) => { tmp.copy(v).project(cam); el.style.display = tmp.z < 1 ? '' : 'none'; el.style.left = `${(tmp.x + 1) / 2 * innerWidth}px`; el.style.top = `${(1 - tmp.y) / 2 * innerHeight + dy}px`; };
    put(E.lbls[0], E.at, -14); put(E.lbls[1], p, -14);
    E.lbls[2].innerHTML = E.shown ? `${fmt(E.e.miss_km * 1000)} m` : ''; put(E.lbls[2], E.at.clone().add(E.pTca).multiplyScalar(0.5), 0);
    $('cvClock').textContent = `TCA ${E.t < 0 ? '−' : '+'}${Math.abs(E.t).toFixed(2)} s`;
    if (E.t >= 1.0 && !E.done) { E.done = true; setTimeout(() => E.opts.onDone?.(), 2500); }
  }

  // camera buttons
  root.querySelectorAll('[data-cam]').forEach((b) => b.addEventListener('click', () => {
    if (!fl) return; fl.camMode = b.dataset.cam;
    root.querySelectorAll('[data-cam]').forEach((x) => x.classList.toggle('on', x === b));
  }));
  $('cvSound').addEventListener('click', (e) => { soundOn = !soundOn; e.target.textContent = soundOn ? '🔊' : '🔇'; audio?.ctx.resume(); });
  $('cvSkip').addEventListener('click', () => {
    if (mode === 'encounter' && enc) { enc.done = true; enc.opts.onDone?.(); return; }
    if (fl && !fl.deploy && !fl.handed) { fl.handed = true; fl.opts.onHandoff?.(fl.tHand); }
  });

  // ---------- visibility and loop ----------
  function setVisible(on) {
    visible = on; root.classList.toggle('on', on);
    if (on) onState?.(mode);
  }
  function hide() { disposeEncounter(); setVisible(false); mode = 'idle'; rumble(0); if (audio) audio.gain.gain.value = 0; }
  let insetL = 0, insetR = 0;        // pixels covered by side panels: the scene centres in what is left
  function setInsets(l, r) { insetL = l; insetR = r; }
  let last = performance.now();
  function loop(now) {
    const dt = Math.min((now - last) / 1000, 0.1); last = now;
    if (visible) {
      const sh = mode === 'bay' ? (insetL - insetR) / 2 : 0;
      if (sh) cam.setViewOffset(innerWidth, innerHeight, -sh, 0, innerWidth, innerHeight); else cam.clearViewOffset();
      if (mode === 'bay') {
        controls.enabled = true;
        if (bayGoal) {
          controls.target.lerp(bayGoal.target, 0.06); cam.position.lerp(bayGoal.pos, 0.05);
          if (cam.position.distanceTo(bayGoal.pos) < 0.05) bayGoal = null;
        }
        cam.up.set(0, 1, 0); cam.near = 0.1; cam.far = 2000; cam.fov = 40; cam.updateProjectionMatrix();
        for (const s of slots) {                                   // stages grow toward their new length
          let moved = false;
          s.r.stages.forEach((st) => { if (Math.abs(st.L - st.dm.L) > 1e-3) { st.L += (st.dm.L - st.L) * Math.min(1, dt * 2.2); moved = true; } });
          if (moved) { s.r.layout(); slotLabel(s); }
        }
        controls.update();
        draw(bay, 0.25);
        for (const s of slots) {
          tmp.set(s.x, s.r.height + 1.2, 0).project(cam);
          const hudh = parseFloat(getComputedStyle(document.body).getPropertyValue('--hudh')) || 64;
          s.el.style.display = tmp.z < 1 && (1 - tmp.y) / 2 * innerHeight > hudh + 40 ? '' : 'none';
          s.el.style.left = `${(tmp.x + 1) / 2 * innerWidth}px`;     // the projection already carries the view offset
          s.el.style.top = `${(1 - tmp.y) / 2 * innerHeight}px`; s.el.style.top = `${(1 - tmp.y) / 2 * innerHeight}px`;
        }
      } else if (mode === 'encounter' && enc) {
        stepEncounter(dt); sky.position.copy(cam.position);
        draw(enc.scene, 0.3);
      } else if (mode === 'launch' && fl) {
        stepLaunch(dt);
        draw(fl.scene, fl.deploy ? 0.2 : 0.35);
      }
    }
    requestAnimationFrame(loop);
  }
  requestAnimationFrame(loop);
  return { cam, controls, showBay, updateCurrent, select, playLaunch, playDeploy, playEncounter, hide, setInsets, layoutOf, get mode() { return mode; }, get flight() { return fl; } };
}
