import { chromium } from 'playwright-core';
const URL = 'http://127.0.0.1:8091/';
const b = await chromium.launch({ channel: 'chrome' });
const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
const p = await ctx.newPage();
let failed = 0;
const check = (name, ok, detail='') => { console.log(`${ok?'PASS':'FAIL'}  ${name}${detail?'  '+detail:''}`); if(!ok) failed++; };

// --- 1. model picker opens unclipped ---
await p.goto(URL, { waitUntil: 'domcontentloaded' });
await p.waitForTimeout(1200);
const branding = await p.evaluate(() => ({
  title: document.title,
  name: document.querySelector('.brand-name')?.textContent,
  icon: document.querySelector('.brand-mark img')?.getAttribute('src'),
}));
check('Fermi branding is visible',
  branding.title === 'Fermi' && branding.name === 'Fermi' && branding.icon === '/mira/icon.png',
  JSON.stringify(branding));
const layout = await p.evaluate(() => {
  const app = document.getElementById('app')?.getBoundingClientRect();
  const side = document.getElementById('side')?.getBoundingClientRect();
  const main = document.getElementById('main')?.getBoundingClientRect();
  return { viewport: innerWidth, app: app?.width, side: side?.width,
           main: main?.width, sameRow: side?.top === main?.top };
});
check('root layout fills the window in one row',
  layout.sameRow && layout.app === layout.viewport &&
  Math.abs((layout.side || 0) + (layout.main || 0) - layout.viewport) < 1,
  JSON.stringify(layout));
await p.click('#pickerBtn');
await p.waitForTimeout(400);
const menu = await p.evaluate(() => {
  const m = document.getElementById('menu');
  if (!m) return null;
  const r = m.getBoundingClientRect();
  const hit = (x,y) => { const e = document.elementFromPoint(x,y); return !!(e && (m === e || m.contains(e))); };
  return { top: r.top, height: r.height,
           hitTop: hit(r.left+10, r.top+8), hitMid: hit(r.left+r.width/2, r.top+r.height/2) };
});
check('picker menu opens, top/mid unclipped', !!menu && menu.hitTop && menu.hitMid && menu.height > 80, JSON.stringify(menu));
await p.keyboard.press('Escape');
await p.click('#log'); // close
await p.waitForTimeout(200);

// --- 2. agent waiting timer survives chat switch ---
await p.evaluate(() => { window.fetch = () => new Promise(() => {}); }); // hang agent+status calls
await p.fill('#input', '生成一段晨雾森林视频');
await p.keyboard.press('Meta+Enter');
await p.waitForTimeout(1600);
const t1 = await p.evaluate(() => document.querySelector('#log .waiting .secs')?.textContent || 'MISSING');
// switch away: click 新建对话, then back into the first chat row
await p.waitForTimeout(2000);
await p.click('#newChat');
await p.waitForTimeout(600);
const rows = await p.$$eval('#chats .chat-row', els => els.length);
const first = await p.$('#chats .chat-row');
if (first) { await first.click(); }
await p.waitForTimeout(1400);
const t2 = await p.evaluate(() => document.querySelector('#log .waiting .secs')?.textContent || 'MISSING');
const num = t => parseInt((t||'').replace(/[^0-9]/g,''), 10);
check(`waiting timer continuous (${t1} -> ${t2})`, num(t1) >= 1 && num(t2) >= num(t1) + 3 && num(t2) < 120, `rows=${rows}`);
await p.screenshot({ path: '/tmp/mira_newui_waiting.png' });

// --- 3. media resume: seeded running video record keeps its startedAt ---
const ctx2 = await b.newContext({ viewport: { width: 1280, height: 800 } });
const p2 = await ctx2.newPage();
await p2.addInitScript(() => {
  if (!localStorage.getItem("argus.chats")) {
    localStorage.setItem("argus.chats", JSON.stringify([{id:"t1", title:"video test", messages:[
      {role:"user", content:"make a video"},
      {role:"assistant", content:"", kind:"video", status:"running", prompt:"make a video"},
    ]}]));
  }
});
await p2.goto(URL, { waitUntil: 'domcontentloaded' });
await p2.waitForTimeout(1200);
await p2.click('#chats .chat-row');
await p2.waitForTimeout(3500);
const resume = await p2.evaluate(() => {
  const card = document.querySelector('#log .media-card');
  const stored = JSON.parse(localStorage.getItem("argus.chats"))[0]?.messages?.find(m => m.kind === "video");
  return { cardText: card?.textContent?.slice(0,80) || '', startedAt: stored?.startedAt ?? null, status: stored?.status };
});
check('resume persists startedAt', !!resume.startedAt, JSON.stringify(resume));
check('resume shows running card (not failed)', resume.status === 'running' && /生成|下载|恢复|处理/.test(resume.cardText), resume.cardText);

// --- 4. long-running music is visibly active instead of looking stuck at 0/1 ---
const ctx3 = await b.newContext({ viewport: { width: 1280, height: 800 } });
const p3 = await ctx3.newPage();
await p3.route('**/mira/jobs/music-diag', route => route.fulfill({ json: {
  id: 'music-diag', kind: 'music', status: 'running', total: 1,
  completed: 0, failed: 0, updated: Date.now() / 1000 - 600,
  spec: { duration_seconds: 60 }, items: [],
}}));
await p3.addInitScript(() => localStorage.setItem('argus.chats', JSON.stringify([{
  id: 'music-diag-chat', title: 'music', messages: [
    {role: 'assistant', content: '任务已创建，正在后台生成。', tasks: ['music-diag'], status: 'complete'},
  ],
}])));
await p3.goto(URL, { waitUntil: 'domcontentloaded' });
await p3.click('#chats .chat-row');
await p3.waitForTimeout(300);
const longMusic = await p3.evaluate(() => ({
  value: document.querySelector('.task-progress progress')?.getAttribute('value'),
  hint: document.querySelector('.task-hint')?.textContent || '',
}));
check('long music uses indeterminate progress with an honest duration hint',
  longMusic.value === null && /十几分钟/.test(longMusic.hint), JSON.stringify(longMusic));

// --- 5. a completed creation stays visible and can be generated again ---
const ctx4 = await b.newContext({ viewport: { width: 1280, height: 800 } });
const p4 = await ctx4.newPage();
await p4.route('**/mira/jobs/music-done', route => route.fulfill({ json: {
  id: 'music-done', kind: 'music', status: 'complete', total: 1,
  completed: 1, failed: 0, updated: Date.now() / 1000,
  items: [{ position: 1, status: 'complete', output: '/mira/music/output.wav', quality_score: 9 }],
}}));
await p4.addInitScript(() => localStorage.setItem('argus.chats', JSON.stringify([{
  id: 'music-done-chat', title: 'music done', messages: [
    {role: 'assistant', content: '任务已完成。', tasks: ['music-done'], status: 'complete'},
  ],
}])));
await p4.goto(URL, { waitUntil: 'domcontentloaded' });
await p4.click('#chats .chat-row');
await p4.waitForTimeout(300);
const repeatLabel = await p4.locator('.task-actions button').textContent();
check('completed task offers generation retry', repeatLabel === '再次生成', repeatLabel || 'MISSING');
await b.close();
process.exit(failed ? 1 : 0);
