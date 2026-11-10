// tests/repro 下浏览器脚本共用的小工具：定位 puppeteer-core / Chrome，读取被测地址，并拒绝对正式服务（3000 端口）运行。
const fs = require('fs');
const path = require('path');
const os = require('os');

function findPuppeteerCore() {
  if (process.env.PUPPETEER_CORE) return process.env.PUPPETEER_CORE;
  try { return require.resolve('puppeteer-core'); } catch (e) {}
  const npx = path.join(os.homedir(), '.npm', '_npx');
  for (const dir of (fs.existsSync(npx) ? fs.readdirSync(npx) : [])) {
    const candidate = path.join(npx, dir, 'node_modules', 'puppeteer-core');
    if (fs.existsSync(path.join(candidate, 'package.json'))) return candidate;
  }
  throw new Error('puppeteer-core not found; set PUPPETEER_CORE');
}

function testBase() {
  const base = (process.env.REPRO_BASE || 'http://127.0.0.1:3100').replace(/\/+$/, '');
  if (new URL(base).port === '3000') {
    console.error('refusing to run against port 3000 (the real service); start an isolated instance with run_test_instance.sh');
    process.exit(2);
  }
  return base;
}

async function launchBrowser() {
  const puppeteer = require(findPuppeteerCore());
  return puppeteer.launch({
    executablePath: process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    headless: true,
    args: ['--no-first-run', '--no-default-browser-check'],
  });
}

function writeResults(defaultName, data) {
  const out = process.env.REPRO_RESULTS || path.join(__dirname, defaultName);
  fs.writeFileSync(out, JSON.stringify(data, null, 2));
  return out;
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

module.exports = { findPuppeteerCore, testBase, launchBrowser, writeResults, sleep };
