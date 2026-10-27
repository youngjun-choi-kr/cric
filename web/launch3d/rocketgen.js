// A rocket built from its numbers: no 3D assets, so any vehicle (preset, spec sheet, form) gets a body.
// Shape is derived and labeled as such: stage length from propellant volume over the cross-section, engine count
// and bell size from thrust over the base area. Units: meters, +Y up, y = 0 at the bottom of stage 1.
import * as THREE from 'three';

export const PROV_COL = { published: 0x4fe3a1, read: 0x4fe3a1, derived: 0x5ab0ff, assumed: 0xffc857, edited: 0xd3a6ff, missing: 0xff6b57 };
export const provOf = (st, k) => {
  const s = (st.source || {})[k] || 'assumed';
  return /edited/.test(s) ? 'edited' : /missing/.test(s) ? 'missing' : /^read/.test(s) ? 'read'
    : /^published/.test(s) ? 'published' : /^derived/.test(s) ? 'derived' : 'assumed';
};

const DENSITY = { kerolox: 1030, hydrolox: 360, solid: 1750 };   // bulk propellant density, kg/m^3

/** Stage dimensions from the numbers (pure; also used for labels). */
export function layoutOf(v) {
  const D = Math.max(0.3, v.diameter_m || 1.2);
  const total = v.stages.reduce((a, s) => a + s.prop_kg, 0) || 1;
  const n = v.stages.length;
  const dims = v.stages.map((s, i) => {
    const kick = i === n - 1 && n > 1 && s.prop_kg / total < 0.03;            // small kick stage, under the fairing
    const d = kick ? D * 0.62 : D;
    const dens = s.solid ? DENSITY.solid : s.isp_vac_s > 400 ? DENSITY.hydrolox : DENSITY.kerolox;
    const vol = s.prop_kg / dens;
    const L = Math.max(kick ? 0.35 : 0.6, (vol / (Math.PI * (d / 2) ** 2)) * 1.18 + 0.25 * d);   // tanks + domes
    const kN = s.thrust_vac_n / 1000;
    const engines = s.solid || i > 0 ? 1 : d >= 1.6 ? 4 : kN / (d * d) > 140 ? 9 : 1;
    const re = d * (engines === 1 ? (i === 0 ? 0.36 : 0.42) : engines === 4 ? 0.22 : 0.13);
    return { kick, d, L, engines, re, bell: re * (i === 0 ? 2.0 : 2.8), solid: !!s.solid, name: s.name };
  });
  const fairingL = Math.max(1.4 * D, 2.3 * D) + (dims.some((x) => x.kick) ? 0.5 * D : 0);
  const inter = 0.28 * D;
  let h = 0;
  dims.forEach((x, i) => { if (!x.kick) h += x.L + (i < n - 1 ? inter : 0); });
  return { D, dims, fairingL, inter, height: h + fairingL };
}

function bellGeometry(re, len) {
  const pts = [];
  for (let k = 0; k <= 12; k++) {
    const f = k / 12;
    pts.push(new THREE.Vector2(re * (0.32 + 0.68 * Math.pow(f, 0.7)), -len * f));
  }
  return new THREE.LatheGeometry(pts, 28);
}

function noseGeometry(r, L) {
  const pts = [new THREE.Vector2(0.001, 0)];
  for (let k = 0; k <= 24; k++) {
    const f = k / 24;
    pts.push(new THREE.Vector2(r * Math.pow(1 - Math.pow(f, 1.6), 0.6) + 0.001, L * f));
  }
  return new THREE.LatheGeometry(pts, 48);
}

function nameTexture(text) {
  const c = document.createElement('canvas'); c.width = 128; c.height = 1024;
  const g = c.getContext('2d');
  g.translate(64, 512); g.rotate(-Math.PI / 2);
  g.font = 'bold 84px Helvetica, Arial, sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
  g.fillStyle = '#1b2a44'; g.fillText(text.toUpperCase().slice(0, 16), 0, 0);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8;
  return t;
}

/**
 * Build a rocket. Returns { group, stages[], fairing, payload, setLengths(), layout(), setXray(), height }.
 * Each stage body is a unit cylinder scaled to its length, so stages can grow smoothly when a fix is applied.
 */
export function buildRocket(v, { payloadKg = 5, name } = {}) {
  const lay = layoutOf(v);
  const group = new THREE.Group();
  group.name = v.name;
  const white = new THREE.MeshStandardMaterial({ color: 0xf2f4f7, roughness: 0.38, metalness: 0.1, envMapIntensity: 0.7 });
  const grey = new THREE.MeshStandardMaterial({ color: 0x9aa3ad, roughness: 0.55, metalness: 0.2, envMapIntensity: 0.6 });
  const black = new THREE.MeshStandardMaterial({ color: 0x15181d, roughness: 0.45, metalness: 0.3, envMapIntensity: 0.5 });
  const metal = new THREE.MeshStandardMaterial({ color: 0x4a4f57, roughness: 0.28, metalness: 0.95, side: THREE.DoubleSide, envMapIntensity: 1.1 });
  const copper = new THREE.MeshStandardMaterial({ color: 0x8a5a35, roughness: 0.35, metalness: 0.9, envMapIntensity: 0.9 });
  const trim = new THREE.MeshStandardMaterial({ color: 0x6d747d, roughness: 0.4, metalness: 0.7, envMapIntensity: 0.8 });
  const stages = lay.dims.map((dm, i) => {
    const st = v.stages[i];
    const g = new THREE.Group();
    const body = new THREE.Mesh(new THREE.CylinderGeometry(dm.d / 2, dm.d / 2, 1, 48), dm.solid ? grey : white);
    body.position.y = 0.5; body.castShadow = body.receiveShadow = true;
    const bodyPivot = new THREE.Group(); bodyPivot.add(body); g.add(bodyPivot);
    if (!dm.kick) {                                                   // cable raceway down the side
      const race = new THREE.Mesh(new THREE.BoxGeometry(dm.d * 0.06, 0.96, dm.d * 0.035), trim);
      race.position.set(0, 0.5, -dm.d / 2 - dm.d * 0.012); bodyPivot.add(race);
    }
    const rings = [0, 1].map((k) => {                                 // stage end rings (positioned in layout)
      const r = new THREE.Mesh(new THREE.CylinderGeometry(dm.d / 2 * 1.012, dm.d / 2 * 1.012, Math.max(0.04, dm.d * 0.035), 48, 1, true), trim);
      g.add(r); return r;
    });
    const eng = new THREE.Group();
    const geo = bellGeometry(dm.re, dm.bell);
    const ring = dm.engines === 9 ? [[0, 0], ...[...Array(8)].map((_, k) => [Math.cos(k * Math.PI / 4) * dm.d * 0.32, Math.sin(k * Math.PI / 4) * dm.d * 0.32])]
      : dm.engines === 4 ? [...Array(4)].map((_, k) => [Math.cos(k * Math.PI / 2 + Math.PI / 4) * dm.d * 0.24, Math.sin(k * Math.PI / 2 + Math.PI / 4) * dm.d * 0.24])
      : [[0, 0]];
    const nozzles = ring.map(([x, z]) => {
      const b = new THREE.Mesh(geo, metal); b.position.set(x, 0, z); b.castShadow = true; eng.add(b);
      const throat = new THREE.Mesh(new THREE.TorusGeometry(dm.re * 0.36, dm.re * 0.07, 8, 20), copper);
      throat.rotation.x = Math.PI / 2; throat.position.set(x, -dm.bell * 0.06, z); eng.add(throat);
      if (dm.engines <= 4 && !dm.solid) {                             // a turbopump beside each chamber
        const tp = new THREE.Mesh(new THREE.CylinderGeometry(dm.re * 0.22, dm.re * 0.22, dm.re * 0.7, 14), trim);
        tp.position.set(x + dm.re * 0.55, dm.re * 0.2, z); eng.add(tp);
      }
      return b.position;
    });
    g.add(eng);
    let band = null;
    if (i < lay.dims.length - 1 && !lay.dims[i + 1].kick) {           // interstage above this stage
      band = new THREE.Mesh(new THREE.CylinderGeometry(dm.d / 2 * 1.005, dm.d / 2 * 1.005, lay.inter, 48, 1, true), black);
      g.add(band);
    }
    // Name down the side only for rockets that are not someone's real product (a trademark is not ours to paint)
    const own = !v.published?.payload_kg || /fictional/i.test(v.name) || name;
    if (i === 0 && !dm.solid && own) {
      const deco = new THREE.Mesh(new THREE.CylinderGeometry(dm.d / 2 * 1.004, dm.d / 2 * 1.004, 1, 32, 1, true, -0.6, 1.2),
        new THREE.MeshStandardMaterial({ map: nameTexture(name || v.name.replace(/\s*\(.*\)/, '')), transparent: true, roughness: 0.5 }));
      deco.position.y = 0.5; bodyPivot.add(deco);
    }
    if (i === 0 && dm.d < 1.0) {                                      // fins on small rockets
      for (let k = 0; k < 4; k++) {
        const fin = new THREE.Mesh(new THREE.BoxGeometry(0.03, dm.d * 1.1, dm.d * 0.7), black);
        const a = k * Math.PI / 2;
        fin.position.set(Math.cos(a) * dm.d * 0.75, dm.d * 0.55, Math.sin(a) * dm.d * 0.75);
        fin.rotation.y = -a; fin.castShadow = true; g.add(fin);
      }
    }
    group.add(g);
    return { g, body, bodyPivot, eng, nozzles, band, rings, dm, L: dm.L, target: dm.L, prov: provOf(st, 'prop_kg'), provT: provOf(st, 'thrust_vac_n') };
  });
  // payload (a box sized by mass) and the fairing halves
  const side = Math.max(0.1, Math.cbrt(payloadKg / 450)) * (payloadKg < 30 ? 1 : 1);
  const payload = new THREE.Group();
  const bus = new THREE.Mesh(new THREE.BoxGeometry(side, side * (payloadKg < 30 ? 3 : 1.2), side),
    new THREE.MeshStandardMaterial({ color: 0xc9a227, roughness: 0.35, metalness: 0.6 }));
  bus.position.y = side * (payloadKg < 30 ? 1.5 : 0.6); payload.add(bus);
  const panelMat = new THREE.MeshStandardMaterial({ color: 0x1d3b8a, roughness: 0.3, metalness: 0.5, side: THREE.DoubleSide });
  const panels = [-1, 1].map((sgn) => {
    const hinge = new THREE.Group(); hinge.position.set(sgn * side / 2, bus.position.y, 0);
    const p = new THREE.Mesh(new THREE.BoxGeometry(side * 2.4, side * (payloadKg < 30 ? 2.8 : 1.1), 0.01), panelMat);
    p.position.x = sgn * side * 1.2; hinge.add(p); hinge.rotation.y = sgn * Math.PI / 2 * 0.98; p.visible = false;
    payload.add(hinge); return hinge;
  });
  group.add(payload);
  const fairing = new THREE.Group();
  const nose = noseGeometry(lay.D / 2, lay.fairingL);
  const halves = [0, Math.PI].map((rot) => {
    const h = new THREE.Mesh(nose.clone(), white);
    const geo = new THREE.LatheGeometry(nose.parameters.points, 24, rot, Math.PI);
    h.geometry.dispose(); h.geometry = geo; h.castShadow = true;
    const pivot = new THREE.Group(); pivot.add(h); fairing.add(pivot);
    const seam = new THREE.Mesh(new THREE.BoxGeometry(0.012 * lay.D, lay.fairingL * 0.28, 0.02 * lay.D), black);
    seam.position.set(Math.sin(rot) * lay.D / 2 * 0.99, lay.fairingL * 0.14, Math.cos(rot) * lay.D / 2 * 0.99); h.add(seam);
    return pivot;
  });
  group.add(fairing);
  const xrayEdges = [];

  function layout() {
    let y = 0, kickY = null;
    stages.forEach((s, i) => {
      if (s.dm.kick) return;
      s.g.position.y = y;
      s.bodyPivot.scale.y = s.L;
      s.rings[0].position.y = 0.02; s.rings[1].position.y = s.L - 0.02;
      if (s.band) { s.band.position.y = s.L + lay.inter / 2; }
      y += s.L + (s.band ? lay.inter : 0);
    });
    kickY = y;
    stages.forEach((s) => {
      if (!s.dm.kick) return;
      s.g.position.y = kickY + s.dm.bell; s.bodyPivot.scale.y = s.L; kickY += s.dm.bell + s.L;
      s.rings[0].position.y = 0.02; s.rings[1].position.y = s.L - 0.02;
    });
    // upper-stage engines sit inside the interstage; first-stage engines hang below y = 0
    stages.forEach((s, i) => { s.eng.position.y = 0; s.eng.visible = i === 0 || s.dm.kick || s.revealed; });
    payload.position.y = kickY + 0.05;
    fairing.position.y = y;
    api.height = y + lay.fairingL;
  }
  function setXray(on) {
    stages.forEach((s) => {
      s.body.material = on ? new THREE.MeshStandardMaterial({ color: PROV_COL[s.prov], emissive: PROV_COL[s.prov], emissiveIntensity: 0.45, transparent: true, opacity: 0.55, roughness: 0.6 })
        : s.dm.solid ? grey : white;
      s.eng.children.forEach((b) => { b.material = on ? new THREE.MeshStandardMaterial({ color: PROV_COL[s.provT], emissive: PROV_COL[s.provT], emissiveIntensity: 0.5, side: THREE.DoubleSide }) : metal; });
    });
    fairing.children.forEach((p) => { p.children[0].material = on ? new THREE.MeshStandardMaterial({ color: 0xdbe6ff, transparent: true, opacity: 0.18 }) : white; });
    xrayEdges.forEach((e) => { e.visible = on; });
  }
  const api = { group, stages, fairing, halves, payload, panels, layout, setXray, lay, height: lay.height, D: lay.D };
  layout();
  return api;
}

export function disposeTree(o) {
  o.traverse((c) => { c.geometry?.dispose(); const m = c.material; (Array.isArray(m) ? m : m ? [m] : []).forEach((x) => { x.map?.dispose(); x.dispose(); }); });
}
