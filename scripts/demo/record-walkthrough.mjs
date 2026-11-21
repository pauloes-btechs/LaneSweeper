// Records the product walkthrough for the demo reel against a local demo stack (API :8787 in demo mode, console :5173).
// Fictional tenant, simulated robots, offline AI. Output: a 1920×1080 WebM per run in $OUT (default /tmp/lk-rec).
// The supervisor approval here is the scripted local demo (same as the E2E test), never a hosted or real approval.
import { chromium } from 'playwright-core';

const BASE = process.env.LK_CONSOLE_URL ?? 'http://127.0.0.1:5173';
const API = process.env.LK_API_URL ?? 'http://127.0.0.1:8787';
const OUT = process.env.OUT ?? '/tmp/lk-rec';
const CHROME = process.env.CHROME_PATH ?? '/usr/bin/google-chrome';
const size = { width: 1920, height: 1080 };

const browser = await chromium.launch({ executablePath: CHROME, args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'] });
const ctx = await browser.newContext({ viewport: size, deviceScaleFactor: 1, recordVideo: { dir: OUT, size } });
await ctx.addInitScript(() => { document.addEventListener('DOMContentLoaded', () => { document.documentElement.style.zoom = '1.35'; }); });
const page = await ctx.newPage();
const marks = [];
const t0 = Date.now();
const mark = (label) => marks.push({ t: (Date.now() - t0) / 1000, label });

async function caption(n, text) {
  mark(text);
  await page.evaluate(([n, text]) => {
    let el = document.getElementById('__cap');
    if (!el) {
      el = document.createElement('div'); el.id = '__cap';
      el.style.cssText = 'position:fixed;left:32px;bottom:32px;z-index:99999;max-width:1100px;padding:14px 22px;border-radius:12px;' +
        'background:rgba(10,14,20,.86);color:#fff;font:600 28px "IBM Plex Sans",system-ui;box-shadow:0 8px 30px rgba(0,0,0,.35);border-left:6px solid #2f8f5b';
      document.body.appendChild(el);
    }
    el.innerHTML = `<span style="color:#7fd3a3;font:600 18px 'IBM Plex Mono',monospace;letter-spacing:.08em">STEP ${n} · SIMULATED</span><br>${text}`;
  }, [n, text]);
}
const pause = (ms) => page.waitForTimeout(ms);
const signIn = async (who) => { await page.selectOption('select[aria-label="Demo identity"]', who); await pause(1200); };

await fetch(`${API}/v1/demo/reset-fleet`, { method: 'POST' });

// 1. Rider assistant drafts (the reel shows the glasses moment on AI footage; here we show the assistant)
await page.goto(`${BASE}/glasses`);
await page.evaluate(() => localStorage.removeItem('lk.rider.drafts'));
await page.reload();
await page.getByRole('button', { name: 'Pair simulated glasses' }).click();
await page.getByText(/Paired device/).waitFor();
await page.getByRole('button', { name: 'Start ride' }).click();
const draft = page.getByRole('group', { name: 'Drafted report' }).first();
await draft.waitFor({ timeout: 25000 });
await page.getByRole('button', { name: 'Pause ride' }).click();
await page.addStyleTag({ content: '.grid.g-side{grid-template-columns:1fr!important}.grid.g-side>.card:first-child{display:none!important}' });
await draft.scrollIntoViewIfNeeded();
mark('SEG drafts');
await caption(2, 'The rider’s AI assistant drafts the report: snapshots, GPS fix, form fields. Nothing is sent yet.');
await pause(5000);
await caption(2, 'One press of the glasses button approves it. That’s the rider’s authorisation.');
await pause(1500);
await page.evaluate(() => [...document.querySelectorAll('button')].find((b) => b.textContent?.startsWith('Glasses button'))?.click());
const sub = page.getByText(/you approved the draft; submitted as LK-\d+/).first();
await sub.waitFor({ state: 'attached', timeout: 20000 }).catch(async (e) => { console.error((await page.locator('main').innerText()).slice(0, 3000)); throw e; });
const ref = (await sub.textContent())?.match(/LK-\d+/)?.[0] ?? 'a ticket';
await caption(2, `Approved by the rider and submitted as ${ref}.`);
await pause(3500);
mark('END');

// 3. Supervisor approval (human in the loop)
await page.goto(`${BASE}/console/agents`);
await signIn('supervisor');
mark('SEG approval');
await page.getByText('dispatch simulated mission').first().waitFor({ timeout: 20000 });
await caption(3, 'Robots never move on an AI decision. A supervisor approves the simulated dispatch, with a reason.');
await pause(3500);
await page.fill('textarea', 'Reviewed window, battery and route; no closures');
await pause(1200);
await page.getByRole('button', { name: 'Approve simulated dispatch' }).first().click();
await page.getByText(/Approved and graph resumed/).waitFor({ timeout: 30000 });
await pause(3000);
mark('END');

// 4. Mission: the reel uses a dedicated high-quality twin render instead of this software-GL page.
await page.goto(`${BASE}/console/missions`);
await page.locator('table.data tbody tr').first().click();
await page.getByText('Telemetry replay').waitFor();

// 5. Ticket verified with audit trail
await page.goto(`${BASE}/console/tickets`);
await page.locator('table.data tbody tr').first().click();
await page.getByText('Audit trail').waitFor();
mark('SEG ticket');
await caption(6, 'Verified closed, with a full audit trail: who approved what, when, and why.');
await pause(5000);
mark('END');

// 6. Public run report
const reports = await (await fetch(`${API}/v1/public/run-reports`)).json();
await page.goto(`${BASE}/r/${reports[0].slug}`);
await page.getByText('Lane cells verified cleared').waitFor();
mark('SEG report');
await caption(7, 'The public sees aggregate results only: no riders, no raw locations, all labelled SIMULATED.');
await pause(5000);
mark('END');

await ctx.close();
await browser.close();
console.log(JSON.stringify({ video: await page.video().path(), marks }));
