import { chromium } from 'playwright-core';
const b = await chromium.launch({ channel: 'chrome' });
const p = await (await b.newContext({ viewport:{width:1280,height:800} })).newPage();
p.on('pageerror', e => console.log('PAGEERROR:', e.message));
await p.goto('http://127.0.0.1:8091/', { waitUntil: 'domcontentloaded' });
await p.waitForTimeout(600);
await p.evaluate(() => { window.fetch = () => new Promise(() => {}); }); // hang the agent call
await p.evaluate(() => { document.getElementById("input").value = "生成一段晨雾森林视频"; send(); });
await p.waitForTimeout(1600);
const id = await p.evaluate(() => curId);
const s1 = await p.evaluate(() => document.querySelector("#log .waiting .secs")?.textContent);
console.log("planning t≈1s:", s1);
await p.waitForTimeout(5000);
await p.evaluate(() => newChat());          // 切走
await p.waitForTimeout(800);
await p.evaluate(i => openChat(i), id);     // 切回
await p.waitForTimeout(1400);
const s2 = await p.evaluate(() => document.querySelector("#log .waiting .secs")?.textContent);
console.log("after switch-back:", s2);
const num = t => parseInt((t||"0").replace(/[^0-9]/g,""), 10);
console.log("timer continuous (expect >=6):", num(s2) >= 6 && num(s2) < 30 ? "PASS" : `FAIL (${s2})`);
await p.screenshot({ path: 'tests/e2e/out_waiting.png' });
await b.close();
