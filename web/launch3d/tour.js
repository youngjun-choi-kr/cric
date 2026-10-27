// First-visit tour of Mission Studio: four callouts, skippable, never shown again once closed (?notour=1 skips it).
const STEPS = [
  { el: '#methodTabs', title: '1 · Your rocket', text: 'Pick a real rocket, drop in a spec sheet, or type your own numbers. The hangar shows it to scale. X-ray shows where each number came from.' },
  { el: '#targetText', title: 'Your orbit, in a sentence', text: 'Describe your satellite and orbit in plain words. Nemotron Nano turns it into an orbit.' },
  { el: '#flyBtn', title: 'Fly it', text: 'Watch your rocket fly. If it falls short, ask for fixes. Each one is test-flown first.' },
  { el: '#steps', title: '2 · Launch window · 3 · Operate', text: 'Next, pick when to launch. Then CRIC looks after the satellite, and you make the final calls.' },
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
