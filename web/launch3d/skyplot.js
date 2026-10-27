// Sky track of a pass (polar plot) and the Sun's elevation at a place: for photo opportunities over home.
const D2R = Math.PI / 180;

// sky track of one pass: azimuth around, elevation inward (90° at the center)
export function skyPlot(p, home) {
  const c = document.getElementById('skyPlot'); if (!c || !p?.track) return;
  const g = c.getContext('2d'), R = c.width / 2 - 26, cx = c.width / 2, cy = c.height / 2;
  const xy = (az, el) => { const r = R * (1 - el / 90), a = az * D2R; return [cx + r * Math.sin(a), cy - r * Math.cos(a)]; };
  g.clearRect(0, 0, c.width, c.height);
  g.strokeStyle = 'rgba(120,160,255,.3)'; g.lineWidth = 1.5;
  for (const el of [0, 30, 60]) { g.beginPath(); g.arc(cx, cy, R * (1 - el / 90), 0, 7); g.stroke(); }
  g.fillStyle = 'rgba(219,230,255,.75)'; g.font = '20px sans-serif'; g.textAlign = 'center';
  [['N', 0], ['E', 90], ['S', 180], ['W', 270]].forEach(([n, az]) => { const [x, y] = xy(az, -12); g.fillText(n, x, y + 7); });
  g.strokeStyle = '#4fe3a1'; g.lineWidth = 4; g.beginPath();
  p.track.forEach(([az, el], i) => { const [x, y] = xy(az, Math.max(0, el)); i ? g.lineTo(x, y) : g.moveTo(x, y); }); g.stroke();
  const [x0, y0] = xy(p.track[0][0], p.track[0][1]); g.fillStyle = '#4fe3a1'; g.beginPath(); g.arc(x0, y0, 6, 0, 7); g.fill();
  g.fillStyle = 'rgba(219,230,255,.85)'; g.textAlign = 'left'; g.font = '18px sans-serif';
  g.fillText(`${home} · ${p.max_utc.slice(11, 16)} UTC · max ${p.max_elevation_deg}°`, 10, c.height - 10);
}
export function sunElev(t, lat, lon) {                     // same almanac as the server (cric.launch.sun)
  const doy = (t - Date.UTC(t.getUTCFullYear(), 0, 0)) / 864e5, gm = 2 * Math.PI / 365.25 * (doy - 1);
  const decl = 0.006918 - 0.399912 * Math.cos(gm) + 0.070257 * Math.sin(gm) - 0.006758 * Math.cos(2 * gm) + 0.000907 * Math.sin(2 * gm) - 0.002697 * Math.cos(3 * gm) + 0.00148 * Math.sin(3 * gm);
  const eot = 229.18 * (0.000075 + 0.001868 * Math.cos(gm) - 0.032077 * Math.sin(gm) - 0.014615 * Math.cos(2 * gm) - 0.040849 * Math.sin(2 * gm));
  const ha = ((t.getUTCHours() * 60 + t.getUTCMinutes() + eot + 4 * lon) / 4 - 180) * D2R, la = lat * D2R;
  return Math.asin(Math.sin(la) * Math.sin(decl) + Math.cos(la) * Math.cos(decl) * Math.cos(ha)) / D2R;
}

