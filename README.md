# IP智能体

一个把数字人、声音克隆、视频文案与视频复刻放在一起的开源桌面工作室。

[下载安装包](https://github.com/Leon-Drq/ip-agent/releases/latest) · [反馈问题](https://github.com/Leon-Drq/ip-agent/issues) · [构建状态](https://github.com/Leon-Drq/ip-agent/actions)

**Windows、Mac 苹果芯片、Mac Intel 三个独立安装包。** 内置界面和应用服务，无需用户安装 Python 或 Node.js。首次启动选择「本机工作室」，或连接已有的兼容服务，复用其中的模型和作品。

## 下载与安装

| 电脑 | 下载文件 | 安装方式 |
| --- | --- | --- |
| Windows 10/11，64 位 Intel / AMD | `IP-Agent-版本-win-x64.exe` | 运行安装向导，可选择安装位置 |
| Mac，Apple Silicon（M 系列） | `IP-Agent-版本-mac-arm64.dmg` | 打开后拖入「应用程序」 |
| Mac，Intel | `IP-Agent-版本-mac-x64.dmg` | 打开后拖入「应用程序」 |

Mac 构建在 macOS 15 原生环境验证；更旧系统未验证。Windows ARM 暂无原生安装包。

当前社区预览版尚未使用付费的 Windows 代码签名证书或 Apple Developer ID 公证。系统可能显示未知开发者提示；先核对下载来自本仓库及 SHA256 校验值，再使用系统提供的“仍要运行 / 仍要打开”。不要关闭系统整体安全防护。Mac 也提供 ZIP 备用分发。

## 第一次使用

1. 打开应用，选择 **本机工作室**。
2. 点击 **下载并安装媒体工具**；也可选择已有的、包含 `ffmpeg` 和 `ffprobe` 的目录。工具从上游下载，自动校验 SHA256，约 40–60 MB。
3. 进入工作室，在 **模型配置** 中填写自己的服务商 API Key，每类选一个当前模型。
4. 上传素材，保存声音和人物，创作作品。云端视频会先显示预计积分并要求确认。

如果已经运行网页版，在启动页选择 **连接已有工作室**，填写如 `http://127.0.0.1:7865`。这个模式直接使用原服务的数据和模型，不迁移、不覆盖原目录。连接另一台电脑时，使用该服务的 HTTPS 地址。云端模型可以在两种 Mac 和 Windows 上使用；模型调用和克隆音色可能产生服务商费用。

随时通过菜单 **工作室 → 工作环境** 切换连接。`Ctrl+,` / `⌘+,` 打开设置。

## 功能与支持范围

| 功能 | 内置工作室 | 使用条件 |
| --- | --- | --- |
| 数字人、声音资料库与作品管理 | 支持 | 素材保存在本机，可复用、删除和恢复作品 |
| MiniMax / ElevenLabs 声音克隆、情感与配音 | 支持 API 适配 | 自备 Key、账号权限及额度 |
| HypiHub 角色替换、视频与图片生成 | 支持 API 适配 | 自备 Key，使用当前兼容模型；素材上传到所选服务 |
| 剧本、角色、门店及分镜确认界面 | 支持 | 自动反推剧本另需本地 Qwen3-VL / Whisper 引擎 |
| GPT-SoVITS、IndexTTS 2、VoxCPM2、CosyVoice 3 | 可选引擎适配 | 不包含权重、CUDA 或推理环境 |
| LatentSync、MuseTalk 对口型 | 可选引擎适配 | 当前实现需要 NVIDIA CUDA，不宣称支持 Mac 本地推理 |
| 视频文案、时间轴截取与下载 | 支持界面和媒体处理 | 转写需 Whisper；链接下载需另装 `video-link-downloader` |
| 连接现有工作室 | 支持 | 现有服务正常运行，能力以该服务为准 |

**本项目开源的是工作室代码，不是免费无限的云端模型服务。** 未安装的本地引擎会显示未就绪。安装桌面端不会自动下载数十 GB 的模型，也不会把 CUDA 引擎变成可在 Apple GPU 上运行的模型。

第三方 API 能力、模型标识与价格会变化。本次发布不使用真实账号进行付费生成验证；现有功能逻辑来自原工作室，跨平台适配通过隔离测试验证。实际生成效果由模型、素材及参数决定。

## 数据和隐私

- Windows：`%APPDATA%/ip-agent/`；Mac：`~/Library/Application Support/ip-agent/`（实际位置可从菜单打开）。
- 作品、上传素材、项目位于 `workspace/data/`。卸载默认保留资料。
- API Key 以 AES-GCM 加密保存；主密钥由 Windows DPAPI / macOS Keychain 保护，不发到页面，不包含在安装包或源码中。
- 内置服务只监听 `127.0.0.1` 的临时端口，每次启动生成独立访问令牌。
- 云端生成会将选中的素材和提示词提交给对应服务；本地模式不会自动上传你的整个资料库。
- 备份 `workspace/` 可保留素材和项目。跨电脑恢复后需要重新连接 API 账号，系统加密密钥不可直接迁移。

仅对你拥有使用权或已获授权的人像、声音与视频进行创作。生成内容及第三方服务的许可条件由使用者核对。

## 开发与构建

需要 Node.js 22+、Python 3.11。每个平台在对应的系统和芯片上构建，不交叉混用原生 Python 依赖。

```sh
npm ci
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS: source .venv/bin/activate
python -m pip install -r backend/requirements.txt pyinstaller==6.18.0
npm test
python -m unittest discover -s tests -p "test_*.py"
python scripts/smoke_backend.py
npm start
```

构建安装包：

```sh
python scripts/icon.py
python scripts/build_backend.py
python scripts/smoke_backend.py --frozen
npm run dist -- --win --x64     # Windows
npm run dist -- --mac --arm64   # Apple Silicon
npm run dist -- --mac --x64     # Intel Mac
```

安装包在 `release/`。GitHub Actions 会在 Windows x64、macOS arm64 与 macOS x64 上分别测试、冻结服务并打包。`v*` 标签触发草稿 Release，维护者检查产物后发布。

### 可选本地引擎

最容易的方式是连接已有工作室。高级用户也可在 `workspace/` 配置模型及各自隔离环境，见 [本地引擎说明](docs/local-engines.md)。本仓库不分发第三方模型权重、闭源人脸修复模型或厂商整合包。

### 代码结构

```text
desktop/        Electron 主进程、初始设置页、安全存储和媒体工具安装
backend/        FastAPI、任务管理、模型适配与现有工作室前端
scripts/        打包、原生服务检查、图标生成
tests/          安全边界、密钥加密和上传协议测试
.github/        三平台构建与发布流程
```

## 许可

本项目原创代码使用 [MIT](LICENSE)。依赖、可选模型、FFmpeg 和服务商 API 按各自许可使用，详见 [第三方声明](THIRD_PARTY_NOTICES.md)。Hypit SDK 和模型权重没有打包进本应用；HypiHub 使用独立 HTTP 适配器连接。
