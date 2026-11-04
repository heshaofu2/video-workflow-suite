## 状态

本项目把视频 CLI、共享规则、领域 Skills 和 Workflow 放在同一个仓库维护。当前版本为 `0.1.0`，通过公开 GitHub 仓库直接用 npx 安装，无需 npm 登录。源码位于 [heshaofu2/video-workflow-suite](https://github.com/heshaofu2/video-workflow-suite)。目前暂不发布到 npm。

采用 [MIT 许可](LICENSE)。

运行依赖为 Node.js 20+、Python 3.10+、FFmpeg/FFprobe。首版语音识别支持 Apple Silicon Mac，统一使用 MLX Whisper large-v3-turbo。其他平台可安装说明与部分本地处理功能，但不提供 MLX 识别环境自动准备。

## 给其他人安装

在希望使用这些 Skill 的项目目录中运行：

```bash
npx github:heshaofu2/video-workflow-suite install --local --prepare
```

需要固定首版时可用：

```bash
npx github:heshaofu2/video-workflow-suite#v0.1.0 install --local --prepare
```

安装默认只作用于当前项目，也可加 `--project /path/to/project`。程序复制全部八个 Skill 与 CLI 到项目的 `.agents/skills/`，并在 `.agents/video-workflow-install.json` 登记版本和文件校验值。不会向全局 Skill 目录安装。

`--prepare` 显式允许准备本项目所需的 MLX Python 环境和约 1.6GB 权重。已有可用环境及 Hugging Face 权重会被复用，不重新复制模型。缺环境时优先用现有 uv 创建 Python 3.12 环境；没有 uv 时可用现有 Python 创建隔离环境。不会给系统 Python 安装包，也不会自动安装 Homebrew 或改动系统 FFmpeg。

若只希望先安装程序和说明，不准备依赖，省略 `--prepare`。缺环境时会返回 `setup_required`，之后可执行：

```bash
npx github:heshaofu2/video-workflow-suite init --local --prepare
npx github:heshaofu2/video-workflow-suite doctor --local
```

如自动发现未覆盖你的已有环境，可用 `init --python /path/to/python --model-path /path/to/snapshot` 指定。说话人分离是可选能力：自动发现现有环境，不额外下载这套可选依赖或模型。

## 使用与更新

安装后让助手使用 `$video-workflow`，或直接运行项目内的 `.agents/skills/video-cli capabilities`。新对话中可发现项目 Skills。

需要更新时，在同一项目中运行：

```bash
npx github:heshaofu2/video-workflow-suite update --local
```

更新仅替换登记过且未被修改的程序文件。发现本地修改时，在写入前停止；用户自己的文件、运行环境及 `video-shared/local.json` 保留。需要重新检测本机配置时再执行 `init`。安装与更新不会自行修改使用者项目的 Git 分支。

## 源码维护

- `bin/video-workflow.mjs`：项目内安装、更新、初始化与检查。
- `video-cli`：本地 Python CLI 入口。
- `video-shared/`：公共规则与唯一执行实现。
- 各领域 Skill：各自的业务判断与命令路由。
- `video-workflow/`：多步骤目标的编排。
- `video-shared/local.json`：当前机器配置，不进入 Git 和安装包。
- `.archive/`：保留的旧版本资料，不进入 Git 和安装包。

程序源码由 Git 管理，安装到使用者项目的 `.agents/skills/`；不维护全局 Skill 副本。模型权重、视频素材与 Python 环境不放进程序仓库。在源码仓库内试装产生的 `.agents/` 也不会进入 Git。

验证命令：

```bash
npm test
npm run pack:check
npm run pack:local
npm run smoke:package
```

## 本地安装包

开发者也可以把 `npm run pack:local` 生成的压缩包交给他人，在其项目目录内安装：

```bash
npx --package=/path/to/video-workflow-suite-0.1.0.tgz video-workflow install --local --prepare
```

发布新版本前应更新版本号并完成测试，再创建相应 Git 标签。Git 提交、远端推送和发布按明确授权执行。当前 `private: true` 防止误发布 npm，后续需要发布时再明确启用。
