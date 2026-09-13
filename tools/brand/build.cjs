/* Export the approved pixel logo. Node + sharp are build tools, not app dependencies. */
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const moduleIndex = process.argv.indexOf('--modules');
const sharp = require(require.resolve('sharp', moduleIndex >= 0 ? {paths: [process.argv[moduleIndex + 1]]} : undefined));
const source = path.join(root, 'docs/brand/pixel/snowbeam-pixel.png');
const assets = path.join(root, 'src/snowbeam/assets');

async function main() {
  fs.mkdirSync(assets, {recursive: true});
  for (const size of [16, 24, 32, 48, 64, 128, 256, 512]) {
    await sharp(source)
      .resize(size, size, {kernel: 'nearest'})
      .png()
      .toFile(path.join(assets, `snowbeam-${size}.png`));
  }
  console.log('Built pixel launcher PNGs from the approved source.');
}

main().catch(error => {console.error(error.message); process.exitCode = 1;});
