#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';

export const PACKAGE_ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const metadata = JSON.parse(fs.readFileSync(path.join(PACKAGE_ROOT, 'package.json'), 'utf8'));
const MODEL = 'mlx-community/whisper-large-v3-turbo';
const SKILLS = metadata.videoWorkflow.skills;

class InstallError extends Error {
  constructor(code, message, details) {
    super(message);
    this.code = code;
    this.details = details;
  }
}

const hash = (value) => crypto.createHash('sha256').update(value).digest('hex');
const readJSON = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));

function defaultProject() {
  const cwd = process.cwd();
  return cwd === PACKAGE_ROOT && path.basename(path.dirname(cwd)) === '.agents'
    ? path.dirname(path.dirname(cwd)) : cwd;
}

function locations(project) {
  const root = path.resolve(project);
  const skills = path.join(root, '.agents', 'skills');
  return { project: root, skills, config: path.join(skills, 'video-shared', 'local.json'),
    marker: path.join(root, '.agents', 'video-workflow-install.json'),
    runtime: path.join(root, '.agents', 'video-runtime') };
}

function assertLocalPath(root, target) {
  const relative = path.relative(root, target);
  if (relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new InstallError('unsafe_path', '安装文件超出了项目目录');
  }
  let current = path.resolve(root);
  const isLink = (file) => {
    try { return fs.lstatSync(file).isSymbolicLink(); }
    catch (error) { if (error.code === 'ENOENT') return false; throw error; }
  };
  for (const part of relative.split(path.sep)) {
    if (isLink(current)) {
      throw new InstallError('symlink_target', `目标包含链接，不能通过它改写其他目录：${current}`);
    }
    current = path.join(current, part);
  }
  if (isLink(target)) {
    throw new InstallError('symlink_target', `目标文件是链接：${target}`);
  }
}

export function bundleFiles() {
  const files = ['video-cli', 'README.md', 'LICENSE'];
  function walk(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (['__pycache__', 'tests', '.venv', '.git'].includes(entry.name)) continue;
      const file = path.join(directory, entry.name);
      if (entry.isSymbolicLink()) throw new InstallError('source_symlink', `安装源不能含链接：${file}`);
      if (entry.isDirectory()) walk(file);
      else if (entry.name !== 'local.json' && (
        /\.(md|yaml|py|sh)$/.test(entry.name) ||
        ['requirements-mlx.txt', 'local.example.json'].includes(entry.name)
      )) files.push(path.relative(PACKAGE_ROOT, file));
    }
  }
  for (const skill of SKILLS) walk(path.join(PACKAGE_ROOT, skill));
  return files.sort();
}

export function installFiles(project, update = false) {
  const target = locations(project);
  assertLocalPath(target.project, target.skills);
  assertLocalPath(target.project, target.marker);
  const previous = fs.existsSync(target.marker) ? readJSON(target.marker) : null;
  if (previous && (previous.package !== metadata.name || previous.skillsRoot !== target.skills)) {
    throw new InstallError('invalid_manifest', '项目安装记录不属于本程序或路径已变化');
  }
  if (update && !previous) throw new InstallError('not_installed', '项目尚未安装，请先执行 install --local');
  const files = Object.fromEntries(bundleFiles().map((name) => [name, hash(fs.readFileSync(path.join(PACKAGE_ROOT, name)))]));
  const oldFiles = previous?.files ?? {};
  for (const name of new Set([...Object.keys(oldFiles), ...Object.keys(files)])) {
    const destination = path.join(target.skills, name);
    assertLocalPath(target.project, destination);
    if (!fs.existsSync(destination)) continue;
    if (!fs.statSync(destination).isFile()) throw new InstallError('path_conflict', `文件位置已被目录占用：${destination}`);
    const current = hash(fs.readFileSync(destination));
    const expected = oldFiles[name] ?? files[name];
    if (current !== expected) throw new InstallError('local_changes', `检测到本地修改，安装前未改动任何文件：${destination}`);
  }
  let changed = 0;
  for (const [name, digest] of Object.entries(files)) {
    const destination = path.join(target.skills, name);
    if (fs.existsSync(destination) && hash(fs.readFileSync(destination)) === digest) continue;
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.copyFileSync(path.join(PACKAGE_ROOT, name), destination);
    changed++;
  }
  for (const name of Object.keys(oldFiles)) {
    if (!Object.hasOwn(files, name)) fs.unlinkSync(path.join(target.skills, name));
  }
  fs.chmodSync(path.join(target.skills, 'video-cli'), 0o755);
  fs.mkdirSync(path.dirname(target.marker), { recursive: true });
  fs.writeFileSync(target.marker, JSON.stringify({ package: metadata.name, version: metadata.version,
    skillsRoot: target.skills, files }, null, 2) + '\n');
  return { project: target.project, skills_root: target.skills, config: target.config,
    marker: target.marker, version: metadata.version, skills: SKILLS, changed_files: changed };
}

function pythonProbe(python, transcription = false) {
  const result = spawnSync(python, ['-c', `import importlib.util,json,sys
info = {"python":sys.executable,"version":list(sys.version_info[:2])}
if sys.argv[1] == 'transcription':
    info.update(mlx=importlib.util.find_spec("mlx_whisper") is not None,
                whisperx=importlib.util.find_spec("whisperx") is not None)
print(json.dumps(info))`, transcription ? 'transcription' : 'editing'],
    { encoding: 'utf8', timeout: 5000 });
  if (result.status !== 0) return null;
  try {
    const info = JSON.parse(result.stdout);
    return info.version[0] === 3 && info.version[1] >= 10 ? info : null;
  } catch { return null; }
}

function directories(root) {
  if (!fs.existsSync(root)) return [];
  return fs.readdirSync(root, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() || entry.isSymbolicLink()).map((entry) => path.join(root, entry.name));
}

function pythonCandidates(target, existing, explicit) {
  const home = os.homedir();
  const candidates = [explicit, existing?.transcription?.python, existing?.transcription?.diarization_python, process.env.VIDEO_MLX_PYTHON,
    path.join(target.runtime, 'bin', 'python'),
    process.env.VIRTUAL_ENV && path.join(process.env.VIRTUAL_ENV, 'bin', 'python'),
    process.env.CONDA_PREFIX && path.join(process.env.CONDA_PREFIX, 'bin', 'python'),
    path.join(target.project, '.venv', 'bin', 'python'), 'python3', 'python'];
  for (const group of directories(path.join(home, '.cache', 'uv', 'environments-v2'))) {
    for (const env of directories(group)) candidates.push(path.join(env, 'bin', 'python'));
  }
  for (const root of [path.join(home, 'miniconda3', 'envs'), path.join(home, 'anaconda3', 'envs'), '/opt/anaconda3/envs']) {
    for (const env of directories(root)) candidates.push(path.join(env, 'bin', 'python'));
  }
  return [...new Set(candidates.filter(Boolean))];
}

function modelValid(directory) {
  return directory && ['config.json', 'weights.safetensors'].every((file) => {
    try { return fs.statSync(path.join(directory, file)).isFile(); } catch { return false; }
  });
}

function cachedModel(existing, explicit) {
  if (explicit && !modelValid(explicit)) throw new InstallError('invalid_model_path', '指定的模型目录缺少 config.json 或 weights.safetensors');
  for (const candidate of [explicit, existing?.transcription?.model_path]) {
    if (modelValid(candidate)) return path.resolve(candidate);
  }
  const cache = process.env.HF_HUB_CACHE || process.env.HUGGINGFACE_HUB_CACHE ||
    path.join(process.env.HF_HOME || path.join(os.homedir(), '.cache', 'huggingface'), 'hub');
  const repo = path.join(cache, 'models--mlx-community--whisper-large-v3-turbo');
  const ref = path.join(repo, 'refs', 'main');
  if (fs.existsSync(ref)) {
    const preferred = path.join(repo, 'snapshots', fs.readFileSync(ref, 'utf8').trim());
    if (modelValid(preferred)) return preferred;
  }
  return directories(path.join(repo, 'snapshots')).find(modelValid) ?? null;
}

function hasCommand(command, args = ['--version']) {
  return spawnSync(command, args, { stdio: 'ignore' }).status === 0;
}

function prepareCommand(command, args) {
  const result = spawnSync(command, args, { stdio: ['inherit', 2, 2] });
  if (result.status !== 0) throw new InstallError('preparation_failed', `环境准备失败：${command}`, { exit_code: result.status });
}

export function initializeRuntime(project, options = {}) {
  const target = locations(project);
  if (!fs.existsSync(path.join(target.skills, 'video-cli'))) {
    throw new InstallError('not_installed', '先执行 install --local，再初始化本项目');
  }
  assertLocalPath(target.project, target.config);
  assertLocalPath(target.project, target.runtime);
  const existing = fs.existsSync(target.config) ? readJSON(target.config) : {};
  let python = null;
  let diarization = null;
  for (const candidate of pythonCandidates(target, existing, options.python)) {
    const probe = pythonProbe(candidate, true);
    if (!probe) continue;
    if (!python && probe.mlx) python = probe.python;
    if (!diarization && probe.whisperx) diarization = probe.python;
  }
  if (options.python && !pythonProbe(options.python, true)?.mlx) {
    throw new InstallError('invalid_python', '指定解释器中没有可用的 mlx_whisper；本次未安装依赖');
  }
  let model = cachedModel(existing, options['model-path']);
  const supported = process.platform === 'darwin' && process.arch === 'arm64';
  const missing = [];
  if (!hasCommand('ffmpeg', ['-version']) || !hasCommand('ffprobe', ['-version'])) missing.push('ffmpeg/ffprobe');
  if (!supported) missing.push('Apple Silicon macOS（语音识别）');
  if (options.prepare && supported) {
    if (missing.includes('ffmpeg/ffprobe')) {
      throw new InstallError('ffmpeg_missing', '请先准备 FFmpeg（macOS 可用 brew install ffmpeg），本程序不改动系统包管理器');
    }
    if (!python) {
      if (fs.existsSync(target.runtime) && !fs.existsSync(path.join(target.runtime, 'pyvenv.cfg'))) {
        throw new InstallError('runtime_conflict', `已有目录不是本项目 Python 环境：${target.runtime}`);
      }
      const hasUv = hasCommand('uv');
      if (!fs.existsSync(path.join(target.runtime, 'pyvenv.cfg'))) {
        if (hasUv) prepareCommand('uv', ['venv', '--python', '3.12', target.runtime]);
        else {
          const base = pythonCandidates(target, existing).find((candidate) => pythonProbe(candidate));
          if (!base) throw new InstallError('python_missing', '请先安装 Python 3.10+ 或 uv');
          prepareCommand(base, ['-m', 'venv', target.runtime]);
        }
      }
      python = path.join(target.runtime, 'bin', 'python');
      const requirements = path.join(target.skills, 'video-shared', 'requirements-mlx.txt');
      prepareCommand(hasUv ? 'uv' : python, hasUv
        ? ['pip', 'install', '--python', python, '-r', requirements]
        : ['-m', 'pip', 'install', '-r', requirements]);
    }
    if (!model) {
      process.stderr.write('首次下载 MLX Whisper large-v3-turbo，约 1.6GB；后续复用同一 Hugging Face 缓存。\n');
      const result = spawnSync(python, ['-c',
        'from huggingface_hub import snapshot_download; import json; print(json.dumps(snapshot_download(repo_id="mlx-community/whisper-large-v3-turbo", allow_patterns=["config.json","weights.safetensors"])))'],
        { encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] });
      if (result.status !== 0) throw new InstallError('model_download_failed', '模型下载失败；已完成的缓存保留，可修复网络后重试');
      model = JSON.parse(result.stdout);
    }
  }
  if (!python) missing.push('MLX Whisper Python 环境');
  if (!model) missing.push('MLX Turbo 本地权重');
  if (python && model && supported && options.write !== false) {
    const transcription = { engine: 'mlx_whisper', python, model_path: model };
    if (diarization) transcription.diarization_python = diarization;
    fs.writeFileSync(target.config, JSON.stringify({ ...existing, transcription }, null, 2) + '\n');
  }
  return { status: missing.length ? 'setup_required' : 'ready', model: MODEL,
    config: python && model && supported ? target.config : null, missing,
    next: missing.length ? '通过同一安装包运行 init --local --prepare（先满足列出的系统要求）' : null };
}

function emit(data) { process.stdout.write(JSON.stringify({ ok: true, data }) + '\n'); }

function editingPython() {
  return ['python3', 'python'].find((candidate) => pythonProbe(candidate));
}

function checkEditingRuntime(project, transcription = false) {
  const python = editingPython();
  if (!python) return { status: 'setup_required', missing: ['Python 3.10+'],
    transcription: { status: 'not_checked' } };
  const result = spawnSync(python, [path.join(locations(project).skills, 'video-cli'), 'doctor',
    ...(transcription ? ['--transcription'] : [])], { encoding: 'utf8' });
  if (result.status !== 0) throw new InstallError('check_failed', result.stdout || result.stderr);
  return JSON.parse(result.stdout).data;
}

function help() {
  process.stdout.write(`video-workflow ${metadata.version}\n\n` +
    'install --local [--project 目录] [--prepare]   项目内安装；--prepare 可选准备 MLX 转写\n' +
    'update  --local [--project 目录]              从当前包更新，拒绝覆盖本地修改\n' +
    'init    --local [--project 目录] [--prepare]   可选：复用或准备 MLX 转写环境和权重\n' +
    'doctor  [--project 目录] [--transcription]    检查剪辑环境；可选检查 MLX 转写\n' +
    '--project 目录 transcript|clip|dedup|subtitle … 转发已安装的本地 CLI\n\n' +
    'init 也支持 --python 和 --model-path 显式指定现有环境。默认不安装全局 Skill。\n');
}

export function main(argv = process.argv.slice(2)) {
  try {
    if (!argv.length || ['--help', '-h'].includes(argv[0])) { help(); return 0; }
    if (argv[0] === '--version') { process.stdout.write(metadata.version + '\n'); return 0; }
    let project = defaultProject();
    if (argv[0] === '--project') { project = path.resolve(argv[1]); argv = argv.slice(2); }
    const command = argv[0];
    if (['install', 'update', 'init', 'doctor'].includes(command)) {
      const { values } = parseArgs({ args: argv.slice(1), options: {
        local: { type: 'boolean' }, project: { type: 'string' }, prepare: { type: 'boolean' },
        python: { type: 'string' }, 'model-path': { type: 'string' }, transcription: { type: 'boolean' },
        help: { type: 'boolean', short: 'h' },
      } });
      if (values.help) { help(); return 0; }
      project = values.project ?? project;
      if (command === 'install' || command === 'update') {
        const installed = installFiles(project, command === 'update');
        const runtime = checkEditingRuntime(project);
        if (values.prepare || values.python || values['model-path']) {
          runtime.transcription = initializeRuntime(project, values);
        }
        emit({ ...installed, runtime });
      } else if (command === 'init') emit(initializeRuntime(project, values));
      else {
        const target = locations(project);
        if (!fs.existsSync(path.join(target.skills, 'video-cli'))) throw new InstallError('not_installed', '项目尚未安装');
        emit({ project: target.project, installed: fs.existsSync(target.marker), runtime: checkEditingRuntime(project, values.transcription) });
      }
      return 0;
    }
    const target = locations(project);
    const cli = path.join(target.skills, 'video-cli');
    if (!fs.existsSync(cli)) throw new InstallError('not_installed', '先在项目中执行 install --local');
    const python = editingPython();
    if (!python) throw new InstallError('python_missing', '请先安装 Python 3.10+；剪辑不需要 MLX 转写环境');
    return spawnSync(python, [cli, ...argv], { stdio: 'inherit' }).status ?? 1;
  } catch (error) {
    process.stdout.write(JSON.stringify({ ok: false, error: {
      code: error.code || 'installation_failed', message: error.message,
      ...(error.details ? { details: error.details } : {}),
    } }) + '\n');
    return 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(fs.realpathSync(process.argv[1])).href) {
  process.exitCode = main();
}
