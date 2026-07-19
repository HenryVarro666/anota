/* Regenerate README screenshots against a running `run.py --demo` on :8420.
   Usage:  npm i puppeteer-core && node docs/make_screenshots.js  */
const puppeteer = require("puppeteer-core");

const CHROME = process.env.CHROME_BIN ||
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const BASE = process.env.ANOTA_URL || "http://localhost:8420";
const OUT = __dirname + "/screenshots";

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new" });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 860, deviceScaleFactor: 2 });

  const prep = async (theme) => {
    await page.goto(BASE, { waitUntil: "networkidle0" });
    await page.evaluate((t) => {
      localStorage.setItem("anota_annotator", "alice");
      localStorage.setItem("anota_theme", t);
    }, theme);
    await page.goto(BASE, { waitUntil: "networkidle0" });
    await page.waitForSelector(".card", { timeout: 5000 });
  };

  require("fs").mkdirSync(OUT, { recursive: true });

  await prep("light");
  await page.screenshot({ path: OUT + "/annotate-light.png" });

  await page.click('.tab[data-tab="dashboard"]');
  await page.waitForSelector(".matrix", { timeout: 5000 });
  await page.screenshot({ path: OUT + "/dashboard-light.png" });
  const cal = await page.$("#judge-cal-card");
  if (cal) await cal.screenshot({ path: OUT + "/judge-calibration.png" });

  await prep("dark");
  await page.click('.tab[data-tab="review"]');
  await page.waitForSelector(".rq-item", { timeout: 5000 });
  await page.click(".rq-item");
  await page.waitForSelector(".threeway", { timeout: 5000 });
  await page.screenshot({ path: OUT + "/review-dark.png" });

  await browser.close();
  console.log("wrote 3 screenshots to", OUT);
})();
