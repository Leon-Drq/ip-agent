# 第三方组件与服务

IP智能体原创代码为 MIT。以下项目不因本仓库的 MIT 而改变其许可。

- Electron：MIT；包含 Chromium、Node.js 等及其许可。安装包保留 Electron 的 `LICENSE` / `LICENSES.chromium.html`。
- Python：PSF License；由 PyInstaller 冻结为独立应用服务。PyInstaller 使用 GPL 及允许生成应用分发的 bootloader exception。
- FastAPI、Starlette、Pydantic、HTTPX、Uvicorn、python-multipart：各自 MIT / BSD / Apache 等许可，安装包收集其 distribution metadata 和许可文件。
- cryptography：Apache-2.0 / BSD-3-Clause；其 OpenSSL 依赖遵循随库分发的许可。
- FFmpeg / FFprobe：**不包含在安装包内**。用户可选择本地工具，或从 [ffmpeg-static 上游发布](https://github.com/eugeneware/ffmpeg-static/releases/tag/b6.1.1) 下载固定版本。该上游以 GPL-3.0 分发工具，[源项目与构建来源](https://github.com/eugeneware/ffmpeg-static)。二次分发 FFmpeg 时必须自行履行对应许可与源码提供义务。
- HypiHub：外部收费 API。使用独立实现的 HTTP 协议适配器；**不包含 `@hypit/hypit` 或其受额外限制的 SDK 源码**。接口资料：[开发文档](https://hypit.ai/developers/api/)；服务账号与生成费用受服务商条款约束。
- MiniMax、ElevenLabs：外部收费 API，模型及音色使用遵循各自条款。应用的开源许可不授予服务使用额度或模型权利。
- GPT-SoVITS、IndexTTS、VoxCPM、CosyVoice、LatentSync、MuseTalk、Whisper、Qwen3-VL 等是可选引擎：不包含源码仓库、权重或运行环境；用户应从官方来源安装并核对对应版本代码、权重、商业使用及声音克隆许可。
- GN v1.4 为兼容既有工作室的可选模型标识，**不分发模型，不授予其使用许可**。

发行构建会将 Python 包的 dist-info（含许可）保存在服务目录的 `_internal/` 中。开发和构建工具仅作为开发依赖，不作为模型产品重新授权。
