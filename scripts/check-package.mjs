import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { PACKAGE_ROOT, bundleFiles } from '../bin/video-workflow.mjs';

const result = spawnSync('npm', ['pack', '--dry-run', '--json', '--ignore-scripts'], {
  cwd: PACKAGE_ROOT, encoding: 'utf8', maxBuffer: 8 * 1024 * 1024,
});
if (result.status !== 0) throw new Error(result.stderr);
const listing = JSON.parse(result.stdout)[0];
const files = new Set(listing.files.map((file) => file.path));
for (const file of bundleFiles()) {
  if (!files.has(file)) throw new Error(`安装包缺少运行文件：${file}`);
}
for (const file of files) {
  if (/(^|\/)(local\.json|\.archive|\.git|\.venv|__pycache__)(\/|$)/.test(file) ||
      /\.(mp4|mkv|wav|safetensors|onnx|pyc|zip)$/.test(file)) {
    throw new Error(`安装包包含本机配置、历史数据或模型：${file}`);
  }
  const content = fs.readFileSync(path.join(PACKAGE_ROOT, file), 'utf8');
  if (/\/(Users|home)\/[^/\s]+\//.test(content) || /\/opt\/anaconda3\/envs\/[^/\s]+\//.test(content)) {
    throw new Error(`安装包含机器固定路径：${file}`);
  }
}
console.log(`安装包检查通过：${files.size} 个文件，${listing.size} 字节压缩包，不含模型、本机配置或归档。`);
