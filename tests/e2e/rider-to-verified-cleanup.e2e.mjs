// Browser E2E: rider report → ticket → supervisor approval in the console → simulated mission → verified ticket →
// public run report; plus role checks (viewer cannot approve; operator sees evidence). Needs the API on :8787 (demo
// mode) and the console on :5173. Uses the system Chrome. Exits non-zero on any failed assertion.
import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';

const BASE = process.env.LK_CONSOLE_URL ?? 'http://127.0.0.1:5173';
const API = process.env.LK_API_URL ?? 'http://127.0.0.1:8787';
const CHROME = process.env.CHROME_PATH ?? '/usr/bin/google-chrome';
const shots = process.env.LK_E2E_SHOTS;

const browser = await chromium.launch({ executablePath: CHROME, args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1400, height: 950 } });
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(String(e)));
const step = async (name, fn) => { process.stdout.write(`• ${name} … `); await fn(); console.log('ok'); if (shots) await page.screenshot({ path: `${shots}/${name.replace(/\W+/g, '-')}.png` }); };
const signIn = async (who) => { await page.selectOption('select[aria-label="Demo identity"]', who); await page.waitForTimeout(1200); };

await fetch(`${API}/v1/demo/reset-fleet`, { method: 'POST' });

await step('rider submits a glass report after stopping', async () => {
  await page.goto(`${BASE}/rider`);
  await page.getByLabel(/I have stopped somewhere safe/).check();
  await page.getByLabel('Nearest spot').selectOption({ label: '1st Ave & E 57th St' });
  await page.getByRole('button', { name: 'Send report' }).click();
  await page.getByRole('status').waitFor();
  const text = await page.getByRole('status').innerText();
  assert.match(text, /Logged as LK-\d+/);
});

await step('viewer sees the approval queue but cannot approve', async () => {
  await page.goto(`${BASE}/console/agents`);
  await signIn('viewer');
  await page.getByText('dispatch simulated mission').first().waitFor();
  await page.fill('textarea', 'viewer trying to approve');
  await page.getByRole('button', { name: 'Approve simulated dispatch' }).first().click();
  await page.getByText(/lack mission\.approve|roles/).first().waitFor();
});

await step('supervisor approves with a reason and the graph resumes', async () => {
  await signIn('supervisor');
  await page.reload();
  await page.getByText('dispatch simulated mission').first().waitFor();
  await page.fill('textarea', 'Reviewed window, battery and route; no closures');
  await page.getByRole('button', { name: 'Approve simulated dispatch' }).first().click();
  await page.getByText(/Approved and graph resumed → verified_closed/).waitFor({ timeout: 20000 });
});

await step('mission twin shows telemetry and a cleared verdict', async () => {
  await page.goto(`${BASE}/console/missions`);
  await page.locator('table.data tbody tr').first().click();
  await page.getByText('Telemetry replay').waitFor();
  await page.getByText(/after-frame clear/).first().waitFor();
});

await step('ticket is verified closed with an audit trail', async () => {
  await page.goto(`${BASE}/console/tickets?status=verified_closed`);
  await page.goto(`${BASE}/console/tickets`);
  await page.locator('table.data tbody tr').first().click();
  await page.getByText('Audit trail').waitFor();
  await page.getByText('verification').first().waitFor();
});

await step('public run report shows aggregates and SIMULATED provenance', async () => {
  const reports = await (await fetch(`${API}/v1/public/run-reports`)).json();
  assert.ok(reports.length > 0);
  await page.goto(`${BASE}/r/${reports[0].slug}`);
  await page.getByText('Lane cells verified cleared').waitFor();
  const body = await page.locator('main').innerText();
  assert.match(body, /SIMULATED/);
  assert.doesNotMatch(body, /approved_by|rider note|\blat\b|\blng\b|latitude|longitude/i);
});

await step('glasses simulator warns locally', async () => {
  await page.goto(`${BASE}/glasses`);
  await page.getByRole('button', { name: 'Start ride' }).click();
  await page.getByRole('alert').filter({ hasText: 'Possible debris ahead' }).waitFor({ timeout: 15000 });
});

await step('supervisor resumes a safety-paused mission from the console', async () => {
  const tok = (await (await fetch(`${API}/v1/demo/token/supervisor`, { method: 'POST' })).json()).token;
  const H = { 'content-type': 'application/json', authorization: `Bearer ${tok}` };
  const run = await (await fetch(`${API}/v1/demo/scenario/G`, { method: 'POST' })).json();   // twin: lost link
  const appr = (await (await fetch(`${API}/v1/approvals`, { headers: H })).json()).find((a) => a.thread_id === run.thread_id && a.status === 'pending');
  await fetch(`${API}/v1/approvals/${appr.id}/decide`, { method: 'POST', headers: H, body: JSON.stringify({ approve: true, reason: 'e2e: link-loss drill' }) });
  const paused = await (await fetch(`${API}/v1/threads/${encodeURIComponent(run.thread_id)}/resume`, { method: 'POST', headers: H, body: JSON.stringify({ kind: 'approval' }) })).json();
  assert.ok(paused.mission_id, JSON.stringify(paused));
  await page.goto(`${BASE}/console/missions/${paused.mission_id}`);
  await page.getByRole('region', { name: 'Safety pause' }).waitFor();
  await page.getByRole('button', { name: 'Supervisor resume' }).click();
  await page.getByText(/Resume accepted by the local safety controller/).waitFor({ timeout: 20000 });
  await page.getByRole('region', { name: 'Safety pause' }).waitFor({ state: 'detached' });
});

assert.deepEqual(pageErrors, [], `page errors: ${pageErrors.join('\n')}`);
await browser.close();
console.log('E2E passed');
