// First-visit tour of Mission Studio: four callouts, skippable, never shown again once closed (?notour=1 skips it).
const STEPS = [
  { el: '#methodTabs', title: '1 · Your rocket', text: 'Pick a published launcher, drop a spec sheet photo or PDF, or type your own numbers. The hangar shows it at true scale next to the others; X-ray colours each part by where its number came from.' },
  { el: '#targetText', title: 'Your orbit, in a sentence', text: 'Describe the satellite and orbit in any words. Nemotron Nano reads it; code works out the inclination.' },
  { el: '#flyBtn', title: 'Fly it', text: 'A 3-DOF simulation flies the ascent and shows it up close. If it falls short, ask for fixes: each one is flown again before you see it.' },
  { el: '#steps', title: '2 · Launch window · 3 · Operate', text: 'Then choose when to launch (weather, orbit timing, collision screening, the launch team’s votes) and run the satellite with CRIC: passes over home, close approaches, your approval.' },
];

export function startTour() {
  let done = false;
  try { done = localStorage.getItem('studioTour') === 'done'; } catch { /* private mode: show it */ }
  if (done || /[?&](notour|cinema|record)=/.test(location.search)) return;
  let i = 0, hi = null;
  const box = document.createElement('div'); box.id = 'tour'; document.body.append(box);
  const close = () => { box.remove(); hi?.classList.remove('tour-hi'); try { localStorage.setItem('studioTour', 'done'); } catch { /* ignore */ } };
  const show = () => {
    const s = STEPS[i], el = document.querySelector(s.el);
    hi?.classList.remove('tour-hi'); hi = el; el?.classList.add('tour-hi');
    box.innerHTML = `<b>${s.title}</b>${s.text}<div class="ctl"><button id="tourSkip">Skip</button>
      <span class="dim small">${i + 1} / ${STEPS.length}</span><button id="tourNext" class="primary">${i + 1 < STEPS.length ? 'Next' : 'Start'}</button></div>`;
    const r = el ? el.getBoundingClientRect() : { right: 400, top: 120, bottom: 140, left: 16 };
    const left = r.right + 340 < innerWidth ? r.right + 14 : Math.max(16, Math.min(innerWidth - 336, r.left));
    const top = r.right + 340 < innerWidth ? Math.max(16, r.top) : r.bottom + 12;
    Object.assign(box.style, { left: `${left}px`, top: `${Math.min(top, innerHeight - 200)}px` });
    box.querySelector('#tourSkip').onclick = close;
    box.querySelector('#tourNext').onclick = () => { if (++i < STEPS.length) show(); else close(); };
  };
  show();
}
