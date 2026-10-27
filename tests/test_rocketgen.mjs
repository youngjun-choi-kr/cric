// The 3D rocket builder's sizing rules, in Node (no browser): run with `node tests/test_rocketgen.mjs`.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// layoutOf only needs plain maths; load it without three.js by cutting the module at the first three.js use
const src = readFileSync(new URL('../web/launch3d/rocketgen.js', import.meta.url), 'utf8');
const body = src.slice(src.indexOf('const DENSITY'), src.indexOf('function bellGeometry'));
const layoutOf = new Function(`${body.replace(/^export /gm, "")}; return layoutOf;`)();
const presets = JSON.parse(readFileSync(new URL('../web/launch/presets.json', import.meta.url), 'utf8')).vehicles;

const h = Object.fromEntries(Object.entries(presets).map(([k, v]) => [k, layoutOf(v)]));
assert.ok(h.garage.height < h.electron.height && h.electron.height < h.nuri.height, 'taller rockets carry more propellant');
assert.equal(h.nuri.dims[0].engines, 4); assert.equal(h.electron.dims[0].engines, 9); assert.equal(h.sslv.dims[0].engines, 1);
assert.ok(h.electron.dims.at(-1).kick, 'a tiny last stage rides under the fairing');
const g2 = structuredClone(presets.garage); g2.stages[0].prop_kg *= 1.3;
assert.ok(layoutOf(g2).dims[0].L > h.garage.dims[0].L * 1.25, 'more propellant, a longer stage');
console.log('rocketgen ok', Object.fromEntries(Object.entries(h).map(([k, v]) => [k, +v.height.toFixed(1)])));
