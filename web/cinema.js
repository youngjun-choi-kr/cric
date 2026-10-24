// Cinema mode: scripted camera shots rendered by the console's own globe engine.
//
//   web/?cinema=1                       preview; keys 1-4 pick a shot, Space pauses, R restarts,
//                                       L letterbox, C caption, U shows the console UI again, Esc leaves
//   web/?cinema=1&record=find-pair      render that shot frame by frame to data/frames/<shot>/ (local server only)
//        &fps=60&w=3840&h=2160          output size and rate (defaults shown)
//
// Every shot maps shot time to simulation time and camera pose deterministically, so a shot renders the same
// frames every time regardless of machine speed. Assemble with:
//   ffmpeg -framerate 60 -i data/frames/<shot>/%05d.png -c:v libx264 -pix_fmt yuv420p -crf 12 <shot>.mp4

const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const easeOut = (t) => 1 - Math.pow(1 - t, 3);
const clamp01 = (t) => Math.min(1, Math.max(0, t));

export function initCinema(api) {
  const { THREE, camera, controls, renderer, scene, $ } = api;
  const params = new URLSearchParams(location.search);
  const V = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);

  // Local frame around the pair: u = radial up, side = horizontal, fwd = along-track-ish.
  function frameAt(date) {
    const mid = api.pairMid(date);
    const u = mid.clone().normalize();
    let side = V().crossVectors(u, V(0, 1, 0));
    if (side.lengthSq() < 1e-6) side = V(1, 0, 0);
    side.normalize();
    const fwd = V().crossVectors(side, u).normalize();
    return { mid, u, side, fwd };
  }
  const tca = () => Date.parse(api.selected().tca_utc);

  const SHOTS = {
    'overview-push': {
      key: '1', seconds: 8, caption: '15,912 objects. Right now.',
      sim: (t) => tca() - 20 * 60e3 + t * 4 * 60e3,
      pose(t, date) {
        const f = frameAt(date), k = easeInOut(t);
        const from = V(0, 9, 26), to = f.u.clone().multiplyScalar(15).add(V(0, 2.5, 0));
        return { cam: from.lerp(to, k), tgt: V().lerp(f.mid.clone().multiplyScalar(0.25), k) };
      },
    },
    'find-pair': {
      key: '2', seconds: 9, caption: 'Two of them will pass 17 metres apart.',
      sim: (t) => tca() - 120e3 + easeOut(t) * 108e3,   // slows down toward TCA - 12 s
      pose(t, date) {
        const f = frameAt(date), k = easeInOut(t);
        const from = f.mid.clone().add(f.u.clone().multiplyScalar(5.5)).add(f.side.clone().multiplyScalar(6));
        const to = f.mid.clone().add(f.u.clone().multiplyScalar(0.28)).add(f.side.clone().multiplyScalar(0.75))
          .add(f.fwd.clone().multiplyScalar(-0.25));
        return { cam: from.lerp(to, k), tgt: f.mid };
      },
    },
    'go-to-burn': {
      key: '3', seconds: 10, caption: 'One burn now. Two kilometres of clearance at closest approach.', needsTrajectory: true,
      // From just before the burn to just after TCA: about half an hour of flight squeezed into 10 seconds.
      sim: (t) => {
        const b = Date.parse(api.trajectory().burn_utc) - 30e3, end = tca() + 20e3;
        return b + easeInOut(t) * (end - b);
      },
      pose(t, date) {
        // Chase camera on our satellite as it flies the exaggerated post-burn path.
        const ours = api.burnPosition(date) || api.ourPosition(date) || frameAt(date).mid;
        const ahead = api.burnPosition(new Date(date.getTime() + 20e3)) || ours.clone();
        const fwd = ahead.clone().sub(ours).normalize();
        const u = ours.clone().normalize();
        const side = V().crossVectors(fwd, u).normalize();
        const swing = (-0.5 + easeInOut(t)) * 0.35;
        const cam = ours.clone().add(fwd.clone().multiplyScalar(-0.6)).add(u.clone().multiplyScalar(0.22))
          .add(side.clone().multiplyScalar(swing));
        return { cam, tgt: ours.clone().add(fwd.clone().multiplyScalar(0.5)) };
      },
    },
    'pass-and-pullback': {
      key: '4', seconds: 11, caption: '17 m → 2 km',
      sim: (t) => tca() - 25e3 + t * 90e3,
      pose(t, date) {
        const f = frameAt(date);
        const close = f.mid.clone().add(f.u.clone().multiplyScalar(0.3)).add(f.side.clone().multiplyScalar(0.8));
        const far = V(0, 6, 22);
        const k = easeInOut(clamp01((t - 0.45) / 0.55));               // hold on the pass, then pull back
        return { cam: close.lerp(far, k), tgt: f.mid.clone().lerp(V(), k) };
      },
    },
  };

  // ---------- overlay ----------
  const overlay = document.createElement('div');
  overlay.id = 'cine';
  overlay.innerHTML = `<div class="lb top"></div><div class="lb bottom"></div>
    <div class="cap" id="cineCap"></div>
    <div class="help" id="cineHelp">CINEMA · <b id="cineShot"></b> <span id="cineT"></span><br>
      1–4 shot · Space pause · R restart · L letterbox · C caption · U console · Esc exit</div>`;
  document.body.appendChild(overlay);
  document.body.classList.add('cinema');
  const state = { name: params.get('shot') || 'overview-push', t0: performance.now(), paused: false, pausedAt: 0,
                  letterbox: params.get('letterbox') === '1', caption: params.get('caption') !== '0' };
  const applyFlags = () => {
    overlay.classList.toggle('letterbox', state.letterbox);
    $('cineCap').hidden = !state.caption;
  };
  applyFlags();

  function apply(name, t) {
    const shot = SHOTS[name];
    api.showTrajectory(!!shot.needsTrajectory);   // the exaggerated burn path belongs to the burn shot only
    const date = new Date(shot.sim(clamp01(t)));
    api.setSim(date.getTime());
    api.stepScene(date, true);
    api.pairLineIfClose(300);                     // a line across half the planet reads as noise
    if (shot.needsTrajectory) api.moveOurMarkerOnBurnPath(date);
    const p = shot.pose(clamp01(t), date);
    camera.position.copy(p.cam);
    controls.target.copy(p.tgt);
    camera.lookAt(p.tgt);
    renderer.render(scene, camera);
  }

  function start(name) {
    if (!SHOTS[name]) return;
    if (SHOTS[name].needsTrajectory && !api.trajectory()) {
      $('cineCap').textContent = 'Load a recorded burn first (this shot follows the satellite after its burn).';
      return;
    }
    state.name = name; state.t0 = performance.now(); state.paused = false;
    $('cineShot').textContent = name;
    $('cineCap').textContent = SHOTS[name].caption;
  }

  function loop(now) {
    if (!api.cinemaActive()) return;
    const shot = SHOTS[state.name];
    const elapsed = state.paused ? state.pausedAt : (now - state.t0) / 1000;
    const t = elapsed / shot.seconds;
    apply(state.name, Math.min(t, 1));
    $('cineT').textContent = `${Math.min(elapsed, shot.seconds).toFixed(1)} / ${shot.seconds}s`;
    requestAnimationFrame(loop);
  }

  addEventListener('keydown', (e) => {
    if (!api.cinemaActive()) return;
    const byKey = Object.entries(SHOTS).find(([, s]) => s.key === e.key);
    if (byKey) start(byKey[0]);
    else if (e.key === ' ') {
      e.preventDefault();
      if (state.paused) { state.t0 = performance.now() - state.pausedAt * 1000; state.paused = false; }
      else { state.pausedAt = (performance.now() - state.t0) / 1000; state.paused = true; }
    } else if (e.key === 'r' || e.key === 'R') start(state.name);
    else if (e.key === 'l' || e.key === 'L') { state.letterbox = !state.letterbox; applyFlags(); }
    else if (e.key === 'c' || e.key === 'C') { state.caption = !state.caption; applyFlags(); }
    else if (e.key === 'u' || e.key === 'U') document.body.classList.toggle('cinema-ui');
    else if (e.key === 'Escape') api.exitCinema();
  });

  // ---------- frame-accurate recording ----------
  async function record(name, fps, w, h) {
    const shot = SHOTS[name];
    if (!shot) throw new Error(`unknown shot ${name}`);
    if (shot.needsTrajectory && !api.trajectory()) throw new Error('this shot needs a recorded burn trajectory');
    $('cineHelp').hidden = true;
    renderer.setPixelRatio(1);
    renderer.setSize(w, h, false);
    renderer.domElement.style.width = '100vw';
    renderer.domElement.style.height = `${(100 * h) / w}vw`;
    camera.aspect = w / h; camera.updateProjectionMatrix();
    api.setLineResolution(w, h);
    api.scalePoints(w / 1600);                    // point sprites are sized in pixels; keep them visible at 4K
    const n = Math.round(shot.seconds * fps);
    for (let i = 0; i < n; i++) {
      apply(name, i / (n - 1));
      const blob = await new Promise((res) => renderer.domElement.toBlob(res, 'image/png'));
      const r = await fetch(`/api/frame?shot=${encodeURIComponent(name)}&i=${i}`, { method: 'POST', body: blob });
      if (!r.ok) throw new Error(`frame ${i}: HTTP ${r.status} (recording needs web/serve.py)`);
      $('cineCap').textContent = `recording ${name}: frame ${i + 1} / ${n}`;
    }
    $('cineCap').textContent = `done: ${n} frames in data/frames/${name}/`;
    document.title = `recorded ${name}`;
  }

  window.cricCinema = { SHOTS, start, record, apply };
  const rec = params.get('record');
  if (rec) {
    state.caption = true; applyFlags();
    record(rec, Number(params.get('fps') || 60), Number(params.get('w') || 3840), Number(params.get('h') || 2160))
      .catch((err) => { $('cineCap').textContent = `recording failed: ${err.message}`; });
    return;
  }
  start(state.name);
  requestAnimationFrame(loop);
}
