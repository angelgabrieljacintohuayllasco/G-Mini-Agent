#!/usr/bin/env node
/**
 * Descarga uv (gestor de Python de Astral) para empaquetarlo con la app.
 * El instalador no trae Python: la app usa uv la primera vez que arranca.
 *
 *   node scripts/fetch-uv.js            # plataforma actual
 *   node scripts/fetch-uv.js win linux  # varias
 */
'use strict';

const { execFileSync } = require('child_process');
const fs = require('fs');
const https = require('https');
const os = require('os');
const path = require('path');

const UV_VERSION = '0.12.23';
const TARGETS = {
    win: { asset: 'uv-x86_64-pc-windows-msvc.zip', binary: 'uv.exe' },
    linux: { asset: 'uv-x86_64-unknown-linux-gnu.tar.gz', binary: 'uv' },
};

function download(url, dest, redirects = 0) {
    return new Promise((resolve, reject) => {
        https.get(url, { headers: { 'User-Agent': 'g-mini-build' } }, (res) => {
            if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location && redirects < 5) {
                res.resume();
                resolve(download(res.headers.location, dest, redirects + 1));
                return;
            }
            if (res.statusCode !== 200) {
                res.resume();
                reject(new Error(`HTTP ${res.statusCode} al bajar ${url}`));
                return;
            }
            const out = fs.createWriteStream(dest);
            res.pipe(out);
            out.on('finish', () => out.close(resolve));
            out.on('error', reject);
        }).on('error', reject);
    });
}

function findFile(dir, name) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
            const found = findFile(full, name);
            if (found) return found;
        } else if (entry.name === name) {
            return full;
        }
    }
    return null;
}

async function fetchTarget(platform) {
    const target = TARGETS[platform];
    if (!target) throw new Error(`plataforma sin uv configurado: ${platform}`);
    const outDir = path.join(__dirname, '..', 'build', 'uv', platform);
    const outFile = path.join(outDir, target.binary);
    const stamp = path.join(outDir, '.version');
    if (fs.existsSync(outFile) && fs.existsSync(stamp) && fs.readFileSync(stamp, 'utf8').trim() === UV_VERSION) {
        console.log(`uv ${UV_VERSION} (${platform}) ya está en ${outFile}`);
        return;
    }
    const work = fs.mkdtempSync(path.join(os.tmpdir(), 'gmini-uv-'));
    try {
        const archive = path.join(work, target.asset);
        const url = `https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${target.asset}`;
        console.log(`Bajando ${url}`);
        await download(url, archive);
        // .zip solo lo abre bsdtar: en Windows el de System32, no el tar de Git Bash
        // (que además lee "C:" como un host remoto).
        const tar = process.platform === 'win32'
            ? path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'tar.exe')
            : 'tar';
        execFileSync(tar, ['-xf', archive, '-C', work], { stdio: 'inherit' });
        const binary = findFile(work, target.binary);
        if (!binary) throw new Error(`${target.binary} no está en ${target.asset}`);
        fs.mkdirSync(outDir, { recursive: true });
        fs.copyFileSync(binary, outFile);
        if (platform !== 'win') fs.chmodSync(outFile, 0o755);
        fs.writeFileSync(stamp, UV_VERSION);
        console.log(`uv ${UV_VERSION} (${platform}) listo en ${outFile}`);
    } finally {
        fs.rmSync(work, { recursive: true, force: true });
    }
}

(async () => {
    const wanted = process.argv.slice(2);
    const platforms = wanted.length ? wanted : [process.platform === 'win32' ? 'win' : 'linux'];
    for (const platform of platforms) await fetchTarget(platform);
})().catch((err) => {
    console.error(err.message);
    process.exit(1);
});
