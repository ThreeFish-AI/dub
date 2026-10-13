# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Changed

- 仓库与 Skill 更名 `dub` → `vibe-tts`：入口 `scripts/vibe_tts.py`、库模块 `vibe_tts_*` 前缀、六个环境变量 `VIBE_TTS_*`、数据目录与缓存盐同步更名；黄金哈希与引擎摘要格式不变（与 vibe-video 跨仓同值）。

### Added

- 首版：把 vibe-video 的配音（TTS）机制抽成独立 Skill。`dub.py say` 以文本 + 风格产出 mp3，提供 `edge`（微软预置音色）与 `index-tts`（IndexTTS-2.5 本地声音克隆）两个引擎，输出到 `DUB_OUTPUT_DIR`。
- index-tts 服务按需自动拉起；收尾时若实例仍被其他进程占用，不杀，起有界 watchdog 巡检至连续空闲后再停。
- 风格预设（含 story 段落演绎）与缓存摘要格式搬运自 vibe-video 且逐字节不变，以黄金哈希钉死；相关测试一并迁入，vibe-video 不再留副本。
