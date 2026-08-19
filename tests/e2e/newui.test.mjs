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
await b.close();
process.exit(failed ? 1 : 0);
