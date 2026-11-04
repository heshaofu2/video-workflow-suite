import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { PACKAGE_ROOT, bundleFiles, installFiles } from '../bin/video-workflow.mjs';

function project(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'video-install-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

function run(...args) {
  const result = spawnSync(process.execPath, [path.join(PACKAGE_ROOT, 'bin', 'video-workflow.mjs'), ...args], { encoding: 'utf8' });
  return { ...result, json: JSON.parse(result.stdout) };
}

test('bundle contains all eight skills and the executable, not local settings or archives', () => {
  const files = bundleFiles();
  assert.equal(files.filter((file) => file.endsWith('/SKILL.md')).length, 8);
  assert.ok(files.includes('video-cli'));
  assert.ok(files.includes('video-shared/scripts/commands/transcript/transcribe.py'));
  assert.ok(!files.some((file) => /(^|\/)(\.archive|local\.json|__pycache__|\.venv)(\/|$)/.test(file)));
});

test('fresh installation is contained in the requested project', (t) => {
  const root = project(t);
  const result = installFiles(root);
  assert.equal(result.skills.length, 8);
  assert.ok(result.changed_files > 0);
  assert.ok(fs.existsSync(path.join(root, '.agents/skills/video-cli')));
  assert.ok(fs.existsSync(path.join(root, '.agents/skills/video-shared/scripts/video_cli.py')));
  assert.ok(!fs.existsSync(result.config));
  const marker = JSON.parse(fs.readFileSync(result.marker, 'utf8'));
  assert.equal(marker.skillsRoot, path.join(root, '.agents/skills'));
  assert.ok(!Object.hasOwn(marker.files, 'video-shared/local.json'));
});

test('same package can be installed repeatedly without replacing unchanged files', (t) => {
  const root = project(t);
  const first = installFiles(root);
  const file = path.join(first.skills_root, 'video-workflow/SKILL.md');
  const modified = fs.statSync(file).mtimeMs;
  assert.equal(installFiles(root).changed_files, 0);
  assert.equal(fs.statSync(file).mtimeMs, modified);
});

test('foreign conflicting skill is preserved and install makes no partial writes', (t) => {
  const root = project(t);
  const file = path.join(root, '.agents/skills/video-workflow/SKILL.md');
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, 'user content');
  assert.throws(() => installFiles(root), { code: 'local_changes' });
  assert.equal(fs.readFileSync(file, 'utf8'), 'user content');
  assert.ok(!fs.existsSync(path.join(root, '.agents/skills/video-cli')));
});

test('update refuses local edits before changing other managed files', (t) => {
  const root = project(t);
  const installed = installFiles(root);
  const file = path.join(installed.skills_root, 'video-workflow/SKILL.md');
  fs.appendFileSync(file, '\nlocal changes\n');
  const launcher = fs.readFileSync(path.join(installed.skills_root, 'video-cli'));
  assert.throws(() => installFiles(root, true), { code: 'local_changes' });
  assert.deepEqual(fs.readFileSync(path.join(installed.skills_root, 'video-cli')), launcher);
  assert.match(fs.readFileSync(file, 'utf8'), /local changes/);
});

test('update preserves private config byte for byte', (t) => {
  const root = project(t);
  const installed = installFiles(root);
  const config = '{"transcription":{"engine":"mlx_whisper","python":"my-existing-python"},"personal":"keep"}\n';
  fs.writeFileSync(installed.config, config);
  const result = run('update', '--local', '--project', root);
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.equal(result.json.data.runtime.status, 'preserved');
  assert.equal(fs.readFileSync(installed.config, 'utf8'), config);
});

test('only unchanged files owned by an older manifest can be removed on update', (t) => {
  const root = project(t);
  const installed = installFiles(root);
  const stale = path.join(installed.skills_root, 'video-shared/retired.py');
  const own = path.join(installed.skills_root, 'video-shared/personal.txt');
  fs.writeFileSync(stale, 'old package file');
  fs.writeFileSync(own, 'keep');
  const marker = JSON.parse(fs.readFileSync(installed.marker, 'utf8'));
  marker.files['video-shared/retired.py'] = crypto.createHash('sha256').update('old package file').digest('hex');
  fs.writeFileSync(installed.marker, JSON.stringify(marker));
  installFiles(root, true);
  assert.ok(!fs.existsSync(stale));
  assert.equal(fs.readFileSync(own, 'utf8'), 'keep');
});

test('symlinked installation directory cannot redirect writes outside the project', (t) => {
  const root = project(t);
  const outside = project(t);
  fs.mkdirSync(path.join(root, '.agents'));
  fs.symlinkSync(outside, path.join(root, '.agents/skills'), 'dir');
  assert.throws(() => installFiles(root), { code: 'symlink_target' });
  assert.deepEqual(fs.readdirSync(outside), []);
});

test('update cannot be used to claim an unmanaged project', (t) => {
  assert.throws(() => installFiles(project(t), true), { code: 'not_installed' });
});

test('installed CLI works without the source checkout or local model settings', (t) => {
  const root = project(t);
  const installed = installFiles(root);
  const result = spawnSync('python3', [path.join(installed.skills_root, 'video-cli'), 'capabilities'], { encoding: 'utf8', cwd: root });
  assert.equal(result.status, 0, result.stderr);
  const data = JSON.parse(result.stdout);
  assert.equal(data.data.transcription_model, 'mlx-community/whisper-large-v3-turbo');
  assert.ok(!fs.existsSync(installed.config));
  for (const file of bundleFiles().filter((name) => name.endsWith('.md'))) {
    const installedFile = path.join(installed.skills_root, file);
    const content = fs.readFileSync(installedFile, 'utf8');
    for (const match of content.matchAll(/\]\(([^)]+)\)/g)) {
      if (match[1].includes('://') || match[1].startsWith('#')) continue;
      assert.ok(fs.existsSync(path.resolve(path.dirname(installedFile), match[1].split('#')[0])),
        `安装后引用失效：${file} -> ${match[1]}`);
    }
  }
});

test('bad explicit environment is reported without a global installation', (t) => {
  const root = project(t);
  installFiles(root);
  const result = run('init', '--local', '--project', root, '--python', path.join(root, 'missing-python'));
  assert.equal(result.status, 1);
  assert.equal(result.json.error.code, 'invalid_python');
  assert.ok(!fs.existsSync(path.join(root, '.agents/video-runtime')));
});

test('unknown installation flags fail rather than changing scope', (t) => {
  const root = project(t);
  const result = run('install', '--project', root, '--global');
  assert.equal(result.status, 1);
  assert.ok(!fs.existsSync(path.join(root, '.agents')));
});

test('a dangling symlink also cannot redirect installation', (t) => {
  const root = project(t);
  const destination = path.join(root, '.agents/skills/video-cli');
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.symlinkSync(path.join(root, 'outside-target'), destination);
  assert.throws(() => installFiles(root), { code: 'symlink_target' });
  assert.ok(!fs.existsSync(path.join(root, 'outside-target')));
});

test('npm-style executable symlinks start the installer entry point', (t) => {
  const root = project(t);
  const link = path.join(root, 'video-workflow');
  fs.symlinkSync(path.join(PACKAGE_ROOT, 'bin/video-workflow.mjs'), link);
  const result = spawnSync(process.execPath, [link, '--version'], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), '0.1.0');
});
