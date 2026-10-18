"use strict";

const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..", "..");
const files = ["three.module.js", "three.core.js"];
const srcDir = path.join(root, "node_modules", "three", "build");
const destDir = path.join(root, "backend", "static", "vendor", "three");

fs.mkdirSync(destDir, { recursive: true });
for (const name of files) {
  const src = path.join(srcDir, name);
  if (!fs.existsSync(src)) {
    console.error(`missing ${src} - run: npm install`);
    process.exit(1);
  }
  fs.copyFileSync(src, path.join(destDir, name));
}
console.log(`copied ${files.join(", ")} -> backend/static/vendor/three/`);
