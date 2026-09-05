import { chromium } from '@playwright/test';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const staticRoot = path.join(root, 'flowgency', 'static');
const source = path.join(staticRoot, 'icon.svg');
const outputs = [
  ['favicon-16.png', 16, 1],
  ['favicon-32.png', 32, 1],
  ['apple-touch-icon.png', 180, 1],
  ['icon-192.png', 192, 1],
  ['icon-192-maskable.png', 192, 0.8],
  ['icon-512.png', 512, 1],
  ['icon-512-maskable.png', 512, 0.8],
];

const sourceText = await readFile(source, 'utf-8');
if (!sourceText.includes('id="flowgency-tree"')) {
  throw new Error('canonical icon is missing the Flowgency tree group');
}
const svgDataUrl = 'data:image/svg+xml;base64,' + Buffer.from(sourceText).toString('base64');

const browser = await chromium.launch();
try {
  for (const [name, size, scale] of outputs) {
    const page = await browser.newPage({
      viewport: { width: size, height: size },
      deviceScaleFactor: 1,
    });
    const inset = Math.round((size * (1 - scale)) / 2);
    const imageSize = size - inset * 2;
    await page.setContent(`
      <style>
        * { box-sizing: border-box; }
        html, body { margin: 0; width: 100%; height: 100%; overflow: hidden; }
        body { display: grid; place-items: center; background: #f3efe5; }
        img { width: ${imageSize}px; height: ${imageSize}px; }
      </style>
      <img id="icon" src="${svgDataUrl}" alt="">
    `);
    await page.locator('#icon').waitFor({ state: 'visible' });
    const loaded = await page.locator('#icon').evaluate(img => img.complete && img.naturalWidth > 0);
    if (!loaded) throw new Error(name + ': image failed to decode');
    await page.screenshot({ path: path.join(staticRoot, name) });
    await page.close();
    console.log('rendered', name, size + 'x' + size, 'scale=' + scale);
  }

  const maskableInset = Math.round(512 * 0.1);
  const artSize = 512 - maskableInset * 2;
  const maskableSvg =
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">\n` +
    `  <rect width="512" height="512" fill="#f3efe5"/>\n` +
    `  <image href="${svgDataUrl}" x="${maskableInset}" y="${maskableInset}" ` +
    `width="${artSize}" height="${artSize}"/>\n` +
    `</svg>\n`;
  await writeFile(path.join(staticRoot, 'icon-maskable.svg'), maskableSvg, 'utf-8');
  console.log('wrote icon-maskable.svg');

  await writeFile(path.join(root, 'screenshots', 'logo.svg'), sourceText, 'utf-8');
  await writeFile(path.join(root, 'screenshots', 'logo-light.svg'), sourceText, 'utf-8');
  console.log('wrote logo.svg and logo-light.svg');
} finally {
  await browser.close();
}
