# 许可合规说明（LICENSE_COMPLIANCE）

本文件说明 Lecturely 在选型与构建过程中对七个参考项目及依赖的许可证审查结论与合规措施。

## 1. 七个参考项目许可证

| 项目 | 许可证 | 结论 |
|---|---|---|
| WhisperLiveKit | Apache-2.0 | ✅ 可作主底座（保留 LICENSE/版权声明） |
| WhisperLive | MIT | ✅ 可借鉴（保留版权声明） |
| LiveTranslate | MIT | ✅ 可借鉴；**但其 PyQt6 依赖为 GPL，Lecturely 未引入任何 PyQt6 代码** |
| Meetily | MIT | ✅ 可借鉴（保留版权声明） |
| LiveCaptions-Translator | Apache-2.0 | ✅ 可借鉴（保留 NOTICE）；其 "Google2" 翻译键有合规风险，**Lecturely 未采用** |
| live-translation-local | MIT | ✅ 可借鉴 |
| MTranServer | Apache-2.0 | ✅ 可作为独立服务集成 |

> 全部为宽松许可证（MIT / Apache-2.0），无 GPL 传染性阻断。Lecturely 对参考项目以"思路借鉴 + 重写"为主，
> 仅 WhisperLiveKit 作为 pip 依赖直接使用（Apache-2.0 允许）。

## 2. 关键合规决策

### 2.1 弃用 NLLB 翻译模型（重要）
- WhisperLiveKit 默认翻译栈使用 **NLLB-200 distilled** 权重，其许可为 **CC-BY-NC-4.0（非商业用途）**。
- 为避免商业使用风险，**Lecturely 完全不使用 NLLB**，改用：
  - 本地：MTranServer（Apache-2.0 代码；其翻译模型需逐对核实，常用 en↔zh 来自 Mozilla，需遵循其许可）/ Ollama 开源模型
  - 在线：任意 OpenAI 兼容 API
- 见 ADR-004。

### 2.2 不引入 PyQt6（GPL）
- LiveTranslate 使用 PyQt6（GPL）。Lecturely 仅移植其 WASAPI 采集**思路**，用 MIT 的 PyAudioWPatch 重写，
  前端为 Web（React），**不含任何 GPL 代码**。

### 2.3 FFmpeg（LGPL）
- FFmpeg 以 **LGPL-2.1+** 许可。Lecturely 通过 imageio-ffmpeg 提供的二进制以**独立子进程**方式调用（聚合，非静态链接衍生），
  符合 LGPL 对"聚合"的要求。用户可自行替换 FFmpeg 二进制。
- 未启用 GPL-only 组件（如 x264）；音频解码使用 LGPL 范围内的能力。

### 2.4 soxr（LGPL）
- soxr 为 LGPL，以动态链接库形式使用，符合 LGPL 要求。

## 3. 模型权重许可

| 模型 | 许可 | 说明 |
|---|---|---|
| faster-whisper (Whisper) | MIT | ASR，可商用 |
| Silero VAD | MIT | 可商用 |
| Ollama qwen2.5 | Apache-2.0（Qwen 系） | AI 生成，可商用（遵循 Qwen 许可条款） |
| Ollama bge-m3 | MIT/Apache（BAAI） | 嵌入检索 |
| MTranServer 翻译模型 | 逐对核实（Mozilla） | 使用前需确认目标语言对模型许可 |

> 模型权重许可与代码许可分开审计。Lecturely 默认不捆绑任何模型权重，由用户在首次使用时按需下载。

## 4. 合规措施清单

- [x] 主底座 WhisperLiveKit 保留 Apache-2.0 LICENSE 与版权声明（pip 包自带）
- [x] 弃用 NLLB（CC-BY-NC）翻译栈
- [x] 不引入 PyQt6（GPL）
- [x] FFmpeg/soxr 以 LGPL 兼容方式使用（子进程/动态链接）
- [x] 不复制 LiveCaptions-Translator 的合规风险翻译键
- [x] 视觉不复制 LecSync 的 Logo/品牌/受保护图形
- [x] API Key 等密钥仅本地存储，日志脱敏，`.env` 不入库
- [x] THIRD_PARTY_NOTICES.md 记录实际复用/参考来源
