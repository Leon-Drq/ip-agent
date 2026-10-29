# 可选本地引擎

桌面包内置应用服务和界面。GPU 推理需要另外配置，优先使用「连接已有工作室」复用已验证环境。

嵌入式本地引擎属于高级配置，不能保证任意新版本模型直接兼容。先核对模型的官方硬件要求和许可。本版本的 CUDA 工作流需要 NVIDIA；Mac 用户使用 API 或连接一台具备 NVIDIA 显卡的工作室。

默认引擎根目录是桌面数据文件夹的 `workspace/`。开发运行可通过 `IP_AGENT_WORKSPACE` 改变根目录。路径均不包含开发者的个人绝对目录。

| 引擎 | 代码 / 权重位置 | 独立 Python 环境 |
| --- | --- | --- |
| GPT-SoVITS | `vendor/GPT-SoVITS`，官方预训练权重位置 | `.venv` |
| LatentSync 1.5 | `vendor/LatentSync`、`models/sd-vae-ft-mse` | `.venv` |
| IndexTTS 2 | `vendor/IndexTTS` 及其 `checkpoints/` | `.indexvenv` |
| VoxCPM2 / CosyVoice 3 | `models/VoxCPM2` / `models/CosyVoice3`，对应官方代码包 | `.speechvenv` |
| MuseTalk | `vendor/MuseTalk` | `.venv` |
| Whisper | `models/faster-whisper-large-v3-turbo` | `.whispervenv` |
| Qwen3-VL | `models/Qwen3-VL-4B` | `.visionvenv` |

Windows 环境解释器为 `Scripts/python.exe`，其他系统为 `bin/python`。模型校验项在 `backend/model_registry.py`、`backend/extraction_config.py`、`backend/remix_screenplay.py` 中列明；`ready.json` 等标记只应在模型和依赖验证后创建，不能靠空文件伪装可用。

独立转写支持 `STUDIO_WHISPER_PYTHON`、`STUDIO_WHISPER_MODEL`；链接下载器支持 `STUDIO_VIDEO_DOWNLOADER`。下载器属于用户另行安装的 `video-link-downloader` 技能，本项目不包含它。现有转写工作流要求同时配置下载器。

本仓库提供适配器而不是一键安装全部模型的管理器。尚未验证的平台或模型组合应显示未就绪，不应自动切换到不匹配的模型生成。
