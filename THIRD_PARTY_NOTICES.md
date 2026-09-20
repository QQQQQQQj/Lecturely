# 第三方组件声明（THIRD_PARTY_NOTICES）

Lecturely 在学习、选型与构建过程中参考并使用了以下开源项目。本文件记录实际复用/参考情况与来源。

## 主底座（代码依赖）

### WhisperLiveKit
- 来源：QuentinFuxa/WhisperLiveKit
- 许可：**Apache-2.0**
- 用途：**主代码基座**。Lecturely 通过 pip 依赖 `whisperlivekit` 使用其流式 ASR 内核
  （`TranscriptionEngine` / `AudioProcessor` / LocalAgreement 流式策略 / Silero VAD / faster-whisper 后端）。
- 说明：Lecturely 自建了音频采集、持久化、翻译、AI、前端等全部产品层；WhisperLiveKit 仅作为 ASR 引擎依赖。
  未使用其 NLLB 翻译栈（许可证原因，见 LICENSE_COMPLIANCE.md）。

## 参考项目（设计/思路借鉴，未直接复制受限制代码）

| 项目 | 许可 | 借鉴内容 | 复用方式 |
|---|---|---|---|
| LiveTranslate (TheDeathDragon) | MIT | WASAPI loopback 系统音频采集、设备枚举、默认设备跟踪、混音思路 | 思路移植（重写为 PyAudioWPatch 异步 AudioSource，**未引入其 PyQt6/GPL 部分**） |
| Meetily (Zackriya-Solutions) | MIT | 会话/录音持久化模型、回放时间戳、AI Provider 抽象、双路混音软限幅 | 思路借鉴（Rust→Python 重写） |
| LiveCaptions-Translator (SakiRinn) | Apache-2.0 | 翻译 Provider 注册表、上下文 few-shot、最新优先调度、Overlay 交互 | 思路借鉴（C#→Python/TS 重写） |
| live-translation-local (kqb) | MIT | 增量持久化思想、句缓冲成句、反幻觉 | 思路借鉴 |
| WhisperLive (collabora) | MIT | partial/committed 句子确认协议（作为校验对照） | 思路借鉴 |
| MTranServer (xxnuo) | Apache-2.0 | 本地离线翻译服务（作为可选 sidecar 集成，不复制代码） | 独立服务集成 |

## 关键运行时依赖

| 依赖 | 许可 | 用途 |
|---|---|---|
| faster-whisper / CTranslate2 | MIT | ASR 推理 |
| Silero VAD | MIT | 语音活动检测 |
| PyAudioWPatch | MIT | Windows WASAPI loopback 采集 |
| sounddevice / PortAudio | MIT | 麦克风采集 |
| soxr | LGPL | 高质量重采样 |
| FFmpeg (imageio-ffmpeg) | LGPL-2.1+ | 音频解码（子进程聚合调用） |
| FastAPI / Starlette / uvicorn | MIT / BSD | Web 框架 |
| SQLAlchemy / aiosqlite | MIT | 数据库 |
| React / Vite / Zustand | MIT | 前端 |
| Ollama 模型（qwen2.5 / bge-m3） | 见各模型许可 | AI 纪要/问答/翻译/检索 |

## 字体与图标
- 前端使用系统字体栈与 Unicode 符号图标，未引入受版权保护的图形资源。
- 视觉风格参考 LecSync 的布局/留白思路，**未复制其 Logo、品牌名称或受保护图形**。
