// Renders the digital twin frame by frame (deterministic, software GL is fine) into $OUT/twin-%04d.jpg.
// Needs the console dev server (npx -w apps/console vite --port 5199). Assemble with ffmpeg afterwards.
import { chromium } from 'playwright-core';
const OUT = process.env.OUT ?? '/tmp/lk-twin';
const N = Number(process.env.FRAMES ?? 168);
const b = await chromium.launch({ executablePath: process.env.CHROME_PATH ?? '/opt/pw-browsers/chromium-1243/chrome-linux64/chrome',
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
const pg = await b.newPage({ viewport: { width: 1920, height: 1080 } });
pg.on('pageerror', (e) => console.error('pageerror', e.message));
await pg.goto(`http://localhost:5199/dev/twin-render.html?frames=${N}&q=${process.env.Q ?? 'hd'}`);
await pg.waitForFunction('window.__ready', null, { timeout: 120000 });
await pg.evaluate(() => window.__frame(0));
await pg.waitForTimeout(8000);   // let textures load
for (let i = Number(process.env.START ?? 0); i < N; i++) {
  await pg.evaluate((i) => window.__frame(i), i);
  await pg.screenshot({ path: `${OUT}/twin-${String(i).padStart(4, '0')}.jpg`, type: 'jpeg', quality: 92 });
  if (i % 24 === 0) console.log('frame', i);
}
await b.close();
