import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { parseArgs } from 'node:util';
import { PACKAGE_ROOT } from '../bin/video-workflow.mjs';

const { values } = parseArgs({ options: { audio: { type: 'string' }, prepare: { type: 'boolean' } } });
const metadata = JSON.parse(fs.readFileSync(path.join(PACKAGE_ROOT, 'package.json'), 'utf8'));
const archive = path.join(PACKAGE_ROOT, 'dist', `${metadata.name}-${metadata.version}.tgz`);
if (!fs.existsSync(archive)) throw new Error('先运行 npm run pack:local');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'video-package-smoke-'));
const project = path.join(temporary, '新项目 with spaces');
fs.mkdirSync(project);
const prefix = ['--offline', '--yes', '--loglevel=error', '--cache', path.join(temporary, 'npm-cache'),
  `--package=${archive}`, 'video-workflow'];
function run(...args) {
  const result = spawnSync('npx', [...prefix, ...args], { cwd: project, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
  if (result.status !== 0) throw new Error(result.stdout + result.stderr);
  return JSON.parse(result.stdout);
}
try {
  const install = run('install', '--local', '--project', project, ...(values.prepare ? ['--prepare'] : []));
  if (install.data.skills.length !== 8) throw new Error('没有安装全部领域与共享 Skill');
  const configFile = path.join(project, '.agents', 'skills', 'video-shared', 'local.json');
  const before = fs.existsSync(configFile) ? fs.readFileSync(configFile) : null;
  const capability = run('--project', project, 'capabilities');
  if (capability.data.transcription_engine !== 'mlx_whisper') throw new Error('安装后引擎发生变化');
  run('doctor', '--project', project);
  if (before && !fs.readFileSync(configFile).equals(before)) throw new Error('doctor 改写了配置');
  const update = run('update', '--local', '--project', project);
  if (update.data.changed_files !== 0) throw new Error('同版本更新没有保持幂等');
  if (before && !fs.readFileSync(configFile).equals(before)) throw new Error('update 改写了配置');
  let words = null;
  if (values.audio) {
    if (run('doctor', '--project', project, '--transcription').data.runtime.transcription.status !== 'ready') {
      throw new Error('真实识别测试需要已经准备好的本地 MLX 环境；请显式使用 --prepare');
    }
    run('--project', project, 'transcript', 'run', path.resolve(values.audio), '--output-dir', path.join(project, 'output'), '--output-name', 'transcript');
    const transcript = JSON.parse(fs.readFileSync(path.join(project, 'output', 'transcript.json'), 'utf8'));
    words = transcript.word_segments.length;
    if (!words || !fs.existsSync(path.join(project, 'output', 'transcript.srt'))) throw new Error('没有生成词级时间或字幕');
  }
  const record = { package: path.basename(archive), version: metadata.version, skills: 8,
    installation: 'npx --package local tarball, fresh project and fresh npm cache',
    npm_execution_offline: true, runtime: install.data.runtime.status,
    doctor_read_only: true, same_version_update_idempotent: true, private_config_preserved: true,
    real_transcription_words: words,
    package_sha256: crypto.createHash('sha256').update(fs.readFileSync(archive)).digest('hex') };
  fs.writeFileSync(path.join(PACKAGE_ROOT, 'dist', 'install-verification.json'), JSON.stringify(record, null, 2) + '\n');
  console.log(JSON.stringify(record));
} finally {
  fs.rmSync(temporary, { recursive: true, force: true });
}
