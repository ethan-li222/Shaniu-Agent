# Shaniu · 让 AI 开口说话，动手创作

**一个能聊天、会用工具，还能帮你制作视频的数字人助手。**

灵感来自《魔幻手机》中的傻妞。打开网页，选择酷、喜、乖、羞，让 AI 用不同风格与你交流；接上数字人，让回答拥有声音和口型；启用工具，把一句需求变成笔记、资料查询或一段视频。

Shaniu 将实时数字人、可扩展 Agent 和 MoneyPrinter 视频引擎放进同一个工作台。从流式对话到视频成品，提交、进度、播放和下载都在聊天中完成。

**喜欢这个项目？欢迎点亮右上角的 ⭐ Star，支持 Shaniu 继续成长！**

## 值得一试

- **🗣️ 看得见的对话**：WebRTC 分句播放数字人回答，支持打断、字幕开关与逐条回放。
- **🎭 四种交流风格**：酷、喜、乖、羞随时切换，搭配可选人物与语音参数。
- **🧰 工具由你决定**：内置 5 组 15 项工具，自主绑定 stdio MCP、选择文本 Skill，按需开放能力。
- **🎬 一句话开始视频创作**：内置 MoneyPrinter，串联文案、素材检索、配音、字幕与剪辑，自动跟踪制作进度。
- **✨ 成片还能继续打磨**：添加动效标题、要点卡、音效与配乐，或裁剪、改比例、压缩、提取封面和字幕。
- **💾 保留你的交流**：本地保存会话与确认过的记忆，刷新后继续聊天、查看任务与播放成品。

可以先用无需模型密钥或 GPU 的演示模式体验网页，再接入自己的模型、数字人环境和视频工具。

## 试着对傻妞说

> “用开心一点的语气，帮我安排今天的学习计划。”
>
> “使用 MoneyPrinter，做一个带字幕的雪豹科普视频。”
>
> “把刚才的视频剪成 15 秒竖屏短片，再提取一张封面。”

工具任务需要先勾选对应工具组，数字人和视频制作需要完成下方环境配置。

## 功能与运行条件

| 功能 | 需要准备什么 |
| --- | --- |
| 演示聊天、情绪选择、历史、用户记忆 | Python 3.11+、Node.js 22；默认无需模型密钥 |
| 真实聊天、工具调用、文本 Skill | Chat Completions 兼容接口与自己的 API Key |
| 内置文件、资料、网页工具 | 工具组勾选；网页读取需要网络 |
| 自定义 MCP 工具 | 安装 MCP SDK（基础依赖已包含），配置可信 stdio 服务并发现工具 |
| 数字人 MP4、实时 WebRTC、回放 | 单独 Python 3.12 媒体环境、CUDA PyTorch、AIAvatar 源码、权重和人物素材；完整链路主要验证于 Windows |
| MoneyPrinter 制作视频/音频/字幕 | 引擎代码已内置；安装视频依赖，配置自己的素材服务、LLM 和语音，启动 API |
| 动效增强、视频后期 | 已完成的 MoneyPrinter 视频、媒体环境；动效另需 Chrome/Chromium |

当前内置 5 组 15 项工具；用户绑定发现的外部工具另计。视频画面主要通过检索素材、剪辑和确定性动效得到，未接入 AI 视频画面生成模型。

## 快速启动

在本仓库根目录执行。需要本机已安装 Python 和 Node.js，首次安装需要网络。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
Copy-Item .env.example .env
cd frontend
npm.cmd ci
npm.cmd run build
cd ..
.\.venv\Scripts\python.exe run.py
```

访问 <http://127.0.0.1:8011>。默认 `SHANIU_PROVIDER=demo`，显示的是演示回答，用于确认网页、会话与交互可运行。没有配置数字人和 MoneyPrinter 时，其相关能力尚不可用。

Windows 也可以在安装和构建后双击 `start.bat`。Linux/macOS 的基础文字模式可用 `.venv/bin/python` 替换上述解释器路径，`npm` 替换 `npm.cmd`；Windows 本地语音和完整 CUDA 媒体链路不保证跨平台等价可用。

前端开发可在 `frontend` 目录运行 `npm.cmd run dev`，同时保持后端运行。后端正式提供网页时需要先构建 `frontend/dist`，它由源码生成，不提交到仓库。

## 配置真实模型

只修改自己的 `.env`，不要提交这个文件：

```dotenv
SHANIU_PROVIDER=compatible
SHANIU_API_BASE=https://your-provider.example/v1
SHANIU_API_KEY=your-own-key
SHANIU_MODEL=your-model-name
SHANIU_STREAM=true
```

`API_BASE` 填接口基础地址，不是完整的 `/chat/completions` URL。供应商应支持所需的聊天协议；工具调用还需要模型支持 `tool_calls`。真实模型请求可能产生费用。

## 数字人环境与人物素材

源码导入 AIAvatar 的 Wav2Lip 模型代码，但本仓库不打包该仓库、模型权重或第三方人物图片。默认查找本仓库相邻的 `../AIAvatar`，也可通过 `SHANIU_AIAVATAR_PATH` 指定路径。

Windows 示例，先在本仓库根目录创建独立媒体环境：

```powershell
git clone https://github.com/shibing624/AIAvatar.git ../AIAvatar
py -3.12 -m venv .venv-media
.\.venv-media\Scripts\python.exe -m pip install -r requirements-media.txt
```

按照 [PyTorch 安装页面](https://pytorch.org/get-started/locally/) 选择适配显卡和驱动的 CUDA PyTorch，安装到 `.venv-media`。验证 CUDA：

```powershell
.\.venv-media\Scripts\python.exe -c "import torch; print(torch.cuda.is_available())"
```

确认并接受模型与人物素材各自的用途条款后，再下载默认人物和权重：

```powershell
.\.venv-media\Scripts\python.exe scripts/prepare_avatar.py
```

`--all` 可准备三套上游人物，下载内容保存在忽略提交的 `data/avatar/`。完整下载较大，下载地址与坐标等实现见脚本。人物及权重不因本仓库使用 MIT 就自动获得相同的授权。

中文语音默认使用 Windows 已安装的中文桌面语音；也可配置 `SHANIU_TTS_PROVIDER=edge` 使用在线语音，在线服务可能失败。准备完成后重启业务服务，再在网页连接实时数字人。本机 WebRTC 是已验证范围，未提供公网 TURN 部署方案。

自定义人物可通过 `scripts/prepare_shaniu.py --image <图片路径>` 准备，需要校准人脸区域并确保图片具备使用权限。

## 启用内置 MoneyPrinterTurbo

`vendor/moneyprinter/` 提供视频制作 API，以独立本机进程运行，通过 Shaniu 页面提交和管理任务。

1. 在项目根目录创建视频运行环境：

```powershell
py -3.12 -m venv .venv-moneyprinter
.\.venv-moneyprinter\Scripts\python.exe -m pip install -e .
.\.venv-moneyprinter\Scripts\python.exe -m pip install -r requirements-moneyprinter.txt
.\.venv-moneyprinter\Scripts\python.exe scripts/setup_moneyprinter.py
```

2. 初始化会将干净模板复制到 `data/moneyprinter/config.toml`，已存在的配置不会覆盖。打开该私有文件，填写自己的 `pexels_api_keys` 或 `pixabay_api_keys`；默认请求使用 Pexels。引擎源目录不保存真实配置。

3. 在 `.env` 配置模型与启动选项：

```dotenv
SHANIU_MPT_ENABLED=true
SHANIU_MPT_URL=http://127.0.0.1:8080
SHANIU_MPT_PROJECT=vendor/moneyprinter
SHANIU_MPT_CONFIG=data/moneyprinter/config.toml
SHANIU_MPT_LLM_PROVIDER=compatible
SHANIU_MPT_LLM_API_BASE=https://your-provider.example/v1
SHANIU_MPT_LLM_MODEL=your-model-name
SHANIU_MPT_LLM_API_KEY=your-own-key
```

视频模型密钥留空时可复用聊天密钥。启动引擎，再启动/重启 Shaniu：

```powershell
.\.venv-moneyprinter\Scripts\python.exe scripts/start_moneyprinter.py
```

Windows 也可双击 `start_moneyprinter.bat`。需要使用自己的外部引擎时，仍可用 `--project` 和 `--python` 指定目录、解释器；外部原版需要自己的现有 `config.toml`。网页勾选 MoneyPrinter 工具组后再发送制作要求。提交回执不是制作完成，卡片会自动显示阶段、进度、失败原因和成品。

引擎基于 MoneyPrinterTurbo v1.2.6，配置、任务状态和生成文件位于 `data/moneyprinter/`。

字幕自动使用本机已安装的中文字体，也可在 `.env` 指定 `SHANIU_MPT_FONT_FILE`。Windows 通常可使用系统微软雅黑，Linux 可安装 Noto CJK 并指定路径。仓库不分发原项目附带的微软/苹果字体及音乐。默认不加背景音乐，直接 API 请求随机音乐但没有本地曲库时也会关闭音乐；自行配置的音乐须有使用权限。

不调用模型或素材 API 的本地合成检查可使用：

```powershell
.\.venv-moneyprinter\Scripts\python.exe scripts/check_bundled_moneyprinter.py --subtitles
```

该检查使用合成音频和画面，验证 API、字幕、视频合成与解码，不访问在线模型、配音或素材服务。可省略 `--subtitles` 关闭字幕检查。

### 配音与任务恢复

默认使用 Edge 在线配音，Windows 中文任务可在失败时回退到已安装的中文桌面语音。在 `.env` 配置后重启视频服务：

```dotenv
SHANIU_MPT_TTS_PROVIDER=edge
SHANIU_MPT_TTS_FALLBACK=true
```

将 provider 改为 `windows` 可直接使用本地配音；将 fallback 改为 `false` 可关闭自动回退。

对于已登记、保存了文案与搜索词的配音失败任务，确认没有其他制作任务运行后，可替换下面的 `YOUR_TASK_ID` 手动恢复：

```powershell
.\.venv-moneyprinter\Scripts\python.exe scripts/resume_moneyprinter_audio_task.py YOUR_TASK_ID
```

恢复会复用原文案，在同一任务 ID 下重新配音、获取素材并合成视频。

## 动效增强与视频后期

这两项功能需要已完成的 MoneyPrinter 视频及 `.venv-media` 环境。动效使用 Chrome/Chromium，可通过 `CHROME_PATH` 指定浏览器；后期使用 imageio-ffmpeg 提供的 FFmpeg。

**动效增强：** 在视频卡片中展开「动效增强」，选择清爽图文或深色强调，按需启用字幕、动作音效，并上传配乐。提交后自动更新进度，完成即可播放增强版，下载 MP4、工程 ZIP 和检查报告。原成片保留。配乐上限 20 MB，须覆盖视频时长；动态字幕需要原任务已有 SRT。

**视频后期：** 勾选「视频后期」工具组，在视频卡片中选择裁剪、改比例、压缩、封面、字幕转换或成片检查。也可直接向 Agent 提出处理要求。

| 操作 | 选项 |
| --- | --- |
| 裁剪 | 指定开始、结束时间，字幕同步裁剪并从零计时 |
| 改比例 | 9:16、16:9、1:1；模糊背景或居中裁切 |
| 压缩 | CRF 18–35，数值越大压缩越强 |
| 封面 | 指定时间点导出 JPG |
| 字幕 | SRT、VTT、ASS 格式转换 |
| 检查 | 完整解码、媒体与音量信息、抽帧核对图 |

处理原成片会保留已有音乐与烧录字幕；选择「无字幕底片与原旁白」可重新控制字幕，但不包含原成片配乐。两类任务均不覆盖普通版，当前仅处理 MoneyPrinter 普通视频，不接受任意上传视频。

## 工具与 Skill

点击「工具与 Skill」打开管理面板，按组勾选内置工具；未勾选的能力不会向模型开放。已提交的视频任务在关闭面板或取消勾选后继续运行。

外部 MCP 支持 stdio：填写可执行程序、args 和 env，测试连接并发现工具后勾选使用。Skill 支持导入或编辑文本指令，随下一轮对话生效；示例位于 `skills/`。工具执行仍受本轮授权限制。

## 目录结构

```text
backend/shaniu/    业务 API、模型、存储、工具、媒体任务管理
frontend/src/      Vue 聊天、工具管理、视频与后期界面
scripts/           独立媒体进程、MCP Server、引擎包装、检查脚本
motion/            动效页面及 Chrome 渲染
skills/            可导入的文本流程示例
vendor/            MoneyPrinter API 引擎、Adu/Easel 基础代码及各自许可证
tests/             自动化回归
```

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd frontend
npm.cmd run build
cd ..
```

基础回归使用临时数据和模拟服务，不要求真实模型或 GPU；可选媒体测试是否跳过取决于环境。`check_*.py` 中的真实模型、素材、在线语音与 GPU 检查需要自行准备依赖，有的会访问网络或产生费用，执行前阅读脚本说明。

## 运行限制

默认监听 `127.0.0.1`，使用单业务进程和浏览器 Cookie 区分数据归属。WebRTC 面向本机网络；自定义 MCP 会执行配置的本机程序，应仅绑定可信服务。

## 支持 Shaniu

如果 Shaniu 帮你做出了第一段数字人回答或第一支视频，欢迎给项目一个 **⭐ Star**，也欢迎分享使用体验。

遇到问题可以提交 Issue；有新工具、人物适配或视频模板的想法，欢迎通过 Pull Request 一起完善。

## 许可证与来源

原创代码使用 [MIT](LICENSE)。第三方代码保留原许可证与版权声明：

- [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo)：视频引擎，[MIT](vendor/moneyprinter/LICENSE)。
- [Adu Motion Video](https://github.com/adunext/adu-motion-video)：动效、字幕与音效原语，[MIT](vendor/adu-motion/LICENSE) 及 [第三方声明](vendor/adu-motion/THIRD_PARTY.md)。
- [Easel](https://github.com/ZJU-REAL/Easel)：视频和字幕操作，[Apache-2.0](vendor/easel-media/LICENSE)。
- [AIAvatar](https://github.com/shibing624/AIAvatar)：Wav2Lip 接入参考；运行时从单独安装的仓库导入模型代码。

来源和修改记录见各 vendor 目录的 `SOURCE.json`。模型权重、人物图片、字体和视频素材遵循各自授权，不属于本项目 MIT 许可范围。
