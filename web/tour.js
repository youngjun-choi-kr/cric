// First-visit tour and glossary tooltips. Nothing here talks to the server.
//
// Glossary: any element with data-gl="<term>" gets a tooltip on hover or tap.
// Tour: runs once per browser (localStorage), and again from the "?" button.

export const GLOSSARY = {
  tca: 'TCA: time of closest approach, the moment the two objects are nearest each other.',
  pc: 'Pc: probability of collision at this encounter. Operators commonly act at 1 in 10,000 (1e-4).',
  tle: 'TLE / GP data: public orbit elements from CelesTrak. Good for finding candidates (about 1 km error), not for deciding a burn.',
  cdm: 'CDM: a conjunction data message with precise orbits and covariance, from the satellite operator or the space surveillance network.',
  covariance: 'Covariance: how uncertain each position is. Pc depends on it; with TLE data CRIC has to assume it.',
  diluted: 'Diluted: the uncertainty is so large that Pc looks small only because we know little. Better data could raise it.',
  sensitivity: 'Pc is recomputed with the assumed uncertainty halved and doubled and the object size varied. Robust means the risk level holds across that range.',
  dv: 'Δv: the change in velocity a burn gives, in metres per second. It costs propellant.',
  kp: 'Kp: NOAA\'s 0–9 index of geomagnetic activity. Storms heat the upper atmosphere, increase drag and make orbit predictions worse.',
  rescreen: 'Every burn option is checked against the whole catalog so the new orbit does not create another close approach.',
  validator: 'Hard rules in code that every decision passes through. They can only make a decision safer, never riskier.',
  vote3: 'Three independent model runs; the majority answer stands, a split goes to a person. Computed from the repeated runs.',
  violating: 'Burns the rules forbid: unverified propulsion, a blackout, over the Δv cap, or deciding on screening-grade data with time left.',
  textonly: 'Scenarios where the deciding fact is only in free-text notes (several languages). A coded policy cannot read them.',
};

export function initGlossary() {
  const tip = document.createElement('div');
  tip.id = 'gltip';
  tip.hidden = true;
  document.body.appendChild(tip);
  const show = (el) => {
    const text = GLOSSARY[el.dataset.gl];
    if (!text) return;
    tip.textContent = text;
    tip.hidden = false;
    const r = el.getBoundingClientRect();
    const x = Math.min(window.innerWidth - 276, Math.max(8, r.left));
    tip.style.left = `${x}px`;
    tip.style.top = `${r.bottom + 6 + tip.offsetHeight > window.innerHeight ? r.top - tip.offsetHeight - 6 : r.bottom + 6}px`;
  };
  document.addEventListener('mouseover', (e) => { const el = e.target.closest('[data-gl]'); if (el) show(el); });
  document.addEventListener('mouseout', (e) => { if (e.target.closest('[data-gl]')) tip.hidden = true; });
  document.addEventListener('click', (e) => { const el = e.target.closest('[data-gl]'); if (el) show(el); else tip.hidden = true; });
}

const STEPS = [
  { el: '#hud', title: 'Real orbits, right now',
    text: 'Every dot is a real tracked object from CelesTrak: active satellites and public debris, moved with SGP4.' },
  { el: '#list', title: 'Close approaches',
    text: 'Pairs predicted to pass within 2 km in the next day. Recorded runs are marked; pick one to fly the camera there.' },
  { el: '#inc header', title: 'Pick a situation and run it',
    text: 'Choose what is going on (quiet day, precise data, an imaging pass, safe mode...). Replay a recorded run for free, or run it live on Nemotron.' },
  { el: '#incBody', title: 'Watch the agent work',
    text: 'Space weather, satellite dossiers with cited sources, physics-checked burn options, then the decision: three Nemotron Super votes.' },
  { el: '#readout', title: 'Code checks, model reads, a person decides',
    text: 'The safety validator applies hard rules in code. The last step is yours: approve or reject. CRIC never commands a spacecraft.' },
  { el: '#evalp', title: 'Measured, not claimed',
    text: 'Accuracy on scenarios whose answers were fixed before any model ran, against rule baselines. Hover the column names for definitions.' },
];

export function initTour() {
  const help = document.createElement('button');
  help.id = 'tourBtn';
  help.textContent = '?';
  help.title = 'Show the tour';
  document.body.appendChild(help);
  const box = document.createElement('div');
  box.id = 'tour';
  box.hidden = true;
  box.innerHTML = '<div class="ring"></div><div class="card"><div class="n"></div><b class="t"></b><div class="x"></div>' +
    '<div class="btns"><button class="skip">Skip</button><button class="next primary">Next</button></div></div>';
  document.body.appendChild(box);
  let i = 0;
  const done = () => { box.hidden = true; try { localStorage.setItem('cric-tour', 'done'); } catch { /* private mode */ } };
  const render = () => {
    const s = STEPS[i], el = document.querySelector(s.el);
    const r = el?.getBoundingClientRect();             // offsetParent is null for fixed panels, so test the size
    if (!r || !r.width || !r.height) { i++; return i < STEPS.length ? render() : done(); }
    const ring = box.querySelector('.ring'), card = box.querySelector('.card');
    Object.assign(ring.style, { left: `${r.left - 4}px`, top: `${r.top - 4}px`, width: `${r.width + 8}px`, height: `${r.height + 8}px` });
    box.querySelector('.n').textContent = `${i + 1} / ${STEPS.length}`;
    box.querySelector('.t').textContent = s.title;
    box.querySelector('.x').textContent = s.text;
    box.querySelector('.next').textContent = i === STEPS.length - 1 ? 'Done' : 'Next';
    const w = 280, right = r.right + 12 + w < window.innerWidth;
    card.style.left = `${right ? r.right + 12 : Math.max(8, r.left - w - 12)}px`;
    card.style.top = `${Math.min(Math.max(8, r.top), window.innerHeight - 170)}px`;
  };
  const start = () => { i = 0; box.hidden = false; render(); };
  box.querySelector('.next').onclick = () => { i++; if (i >= STEPS.length) done(); else render(); };
  box.querySelector('.skip').onclick = done;
  help.onclick = start;
  addEventListener('resize', () => { if (!box.hidden) render(); });
  let seen = false;
  try { seen = localStorage.getItem('cric-tour') === 'done'; } catch { /* storage blocked: show it */ }
  const quiet = /[?&](cinema|ui|notour)=/.test(location.search);
  if (!seen && !quiet) setTimeout(start, 600);
}
