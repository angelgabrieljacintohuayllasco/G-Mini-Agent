// Copies the subset of three.js / @pixiv/three-vrm needed by the skin renderer
// into electron/src/vendor/, so skin.html can load them via an importmap
// without a bundler. Run with: npm run vendor:skin
'use strict';

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const NM = path.join(ROOT, 'node_modules');
const VENDOR = path.join(ROOT, 'src', 'vendor');

function copyFile(src, dest) {
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(src, dest);
  console.log('copied', path.relative(ROOT, dest));
}

function copyDir(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, entry.name);
    const d = path.join(dest, entry.name);
    if (entry.isDirectory()) copyDir(s, d);
    else copyFile(s, d);
  }
}

// three.module.js
copyFile(
  path.join(NM, 'three', 'build', 'three.module.js'),
  path.join(VENDOR, 'three', 'three.module.js')
);

// jsm addons used by skin loaders (GLTF/FBX + transitive deps)
const jsmFiles = [
  ['loaders/GLTFLoader.js', 'loaders/GLTFLoader.js'],
  ['loaders/FBXLoader.js', 'loaders/FBXLoader.js'],
  ['loaders/DRACOLoader.js', 'loaders/DRACOLoader.js'],
  ['libs/fflate.module.js', 'libs/fflate.module.js'],
  ['curves/NURBSCurve.js', 'curves/NURBSCurve.js'],
  ['curves/NURBSUtils.js', 'curves/NURBSUtils.js'],
  ['utils/BufferGeometryUtils.js', 'utils/BufferGeometryUtils.js'],
];
const JSM = path.join(NM, 'three', 'examples', 'jsm');
for (const [src, dest] of jsmFiles) {
  copyFile(path.join(JSM, src), path.join(VENDOR, 'three', 'jsm', dest));
}

// draco decoder (gltf subset only)
copyDir(
  path.join(JSM, 'libs', 'draco', 'gltf'),
  path.join(VENDOR, 'three', 'jsm', 'libs', 'draco', 'gltf')
);

// @pixiv/three-vrm
copyFile(
  path.join(NM, '@pixiv', 'three-vrm', 'lib', 'three-vrm.module.js'),
  path.join(VENDOR, 'three-vrm', 'three-vrm.module.js')
);

console.log('Vendor copy complete.');
