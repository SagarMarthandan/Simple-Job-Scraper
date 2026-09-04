#!/usr/bin/env node
/**
 * Fetch Indeed job descriptions using Playwright + stealth plugin.
 *
 * Indeed blocks plain requests (401/403), TinyFish (target_http_error),
 * and vanilla headless Playwright (Cloudflare "Request Blocked").
 * The workaround: playwright-extra with stealth plugin + mobile URL path
 * (/m/viewjob instead of /viewjob) bypasses both Cloudflare and the login wall.
 *
 * Usage: node indeed_playwright_fetch.js <url1> <url2> ...
 * Output: JSON array [{"url": "...", "text": "..."}] on stdout.
 *
 * Dependencies (install in this directory):
 *   npm install playwright playwright-extra puppeteer-extra-plugin-stealth
 *   npx playwright install chromium
 */

const { chromium } = require('playwright-extra');
const stealth = require('puppeteer-extra-plugin-stealth')();
chromium.use(stealth);

const MOBILE_UA =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) ' +
  'AppleWebKit/605.1.15 (KHTML, like Gecko) ' +
  'Version/17.0 Mobile/15E148 Safari/604.1';

/** Convert desktop Indeed URL to mobile path. */
function toMobileUrl(url) {
  if (url.includes('/m/viewjob')) return url;        // already mobile
  return url.replace('/viewjob', '/m/viewjob');
}

async function fetchOne(browser, url) {
  const mobileUrl = toMobileUrl(url);
  const context = await browser.newContext({
    userAgent: MOBILE_UA,
    viewport: { width: 375, height: 812 },
    isMobile: true,
  });
  const page = await context.newPage();
  try {
    await page.goto(mobileUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(3000);
    const text = await page.evaluate(() => {
      const el =
        document.querySelector('#jobDescriptionText') ||
        document.querySelector('.jobsearch-jobDescriptionText') ||
        document.querySelector('[data-testid="jobsearch-jobDescriptionText"]') ||
        document.querySelector('#jobDescription') ||
        document.querySelector('.jobDescription');
      return el ? el.innerText : '';
    });
    return { url, text };
  } finally {
    await page.close();
    await context.close();
  }
}

(async () => {
  const urls = process.argv.slice(2);
  if (!urls.length) {
    console.log('[]');
    process.exit(0);
  }

  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  const results = [];
  // Sequential — 1 browser, 1 page at a time (avoids rate-limiting)
  for (const url of urls) {
    try {
      const r = await fetchOne(browser, url);
      results.push(r);
    } catch (e) {
      results.push({ url, text: '', error: e.message });
    }
  }

  await browser.close();
  console.log(JSON.stringify(results));
})();
