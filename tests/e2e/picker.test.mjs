import { chromium } from 'playwright-core';
const b = await chromium.launch({ channel: 'chrome' });
const p = await b.newPage({ viewport: { width: 1280, height: 800 } });
await p.goto('http://127.0.0.1:8091/', { waitUntil: 'networkidle' }).catch(()=>{});
await p.waitForTimeout(1200);
await p.click('#pickerBtn');
await p.waitForTimeout(400);
const m = await p.$eval('#menu', el => {
  el.classList.add('open'); // ensure open even if fetch pending
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  return { top: r.top, bottom: r.bottom, height: r.height, display: cs.display, z: cs.zIndex };
});
// does the visible area of the menu get clipped by any ancestor?
const clip = await p.evaluate(() => {
  let el = document.getElementById('menu'), out = [];
  for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
    const cs = getComputedStyle(a);
    if (/(hidden|clip|auto|scroll)/.test(cs.overflow + cs.overflowX + cs.overflowY)) {
      const r = a.getBoundingClientRect(), m = el.getBoundingClientRect();
      out.push({ id: a.id, overflow: cs.overflow, clipsTop: a.scrollTop, topEdge: r.top, menuTop: m.top, clipped: m.top < r.top });
    }
  }
  return out;
});
console.log('menu rect:', JSON.stringify(m));
console.log('clipping ancestors:', JSON.stringify(clip));
// visual check: is every part of the menu actually painted? compare elementFromPoint at menu's top-left corner and center
const probe = await p.evaluate(() => {
  const m = document.getElementById('menu').getBoundingClientRect();
  const pts = [[m.left+10, m.top+10],[m.left+m.width/2, m.top+m.height/2],[m.left+10, m.bottom-10]];
  return pts.map(([x,y]) => { const e = document.elementFromPoint(x,y); return { x: Math.round(x), y: Math.round(y), hit: e ? (e.id || e.className || e.tagName) : 'null' }; });
});
console.log('hit-test:', JSON.stringify(probe));
await p.screenshot({ path: 'tests/e2e/out_picker.png' });
await b.close();
