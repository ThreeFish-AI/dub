# dub

把一段文字配成 `.mp3`：给文本和风格，选引擎，产出落到环境变量指定的统一目录。

- **edge**：微软预置音色，联网免密钥，几秒出声。
- **index-tts 2.5**：本地 IndexTTS-2.5 声音克隆，用你自己的声音、`story` 等段落演绎风格朗读。

```bash
# 默认 edge
uv run --no-project --with edge-tts --with mutagen scripts/dub.py say \
  --text "今天我们拆解的是：Agent Skills"

# 本人声音克隆 + story 风格（须本人显式授权，见下）
uv run --no-project --with mutagen --with soundfile --with numpy scripts/dub.py say \
  --engine index-tts --final-voice --style story \
  --ref ~/voices/me-bright.wav --expect-ref-sha1 54b699cce97f \
  --text "今天我们拆解的是：Agent Skills"
```

stdout 最后一行是回执 JSON：

```json
{"path": "…/今天我们拆解的是-agent-skills.847fb978afff.mp3", "durationSec": 3.471, "sampleRate": 22050, "engine": "index-tts", "style": "story", "cached": false, "chunks": 1}
```

## 核心能力

- **一句话出 mp3**：文本来源 `--text` 或 `--text-file`；输出目录 `--out-dir`，缺省读 `DUB_OUTPUT_DIR`，两者皆无则大声退出，不替你选落点。
- **风格即数据**：10 个风格预设（`story`、`sunny-steady`、`passionate` …），与 vibe-video 同源；`dub.py styles` 查看。
- **长文本不丢字**：index-tts 上游单请求约 36 秒封顶、超出的尾部会被静默吞掉，dub 按句界分块逐块合成再拼接，不需要 ffmpeg。
- **缓存**：同文本同参数直接命中；index-tts 另有块级版本库，改一句只重合成变更的块。
- **服务按需起停**：index-tts 服务未就绪时自动拉起，合成完只停**自己拉起的**实例；若仍被其他进程占用，不杀，起有界 watchdog 巡检到连续空闲再停。

## 安装

前置：[uv](https://docs.astral.sh/uv/)；index-tts 引擎另需 macOS、本地 `~/tools/index-tts`（IndexTTS-2.5 权重，约 10 GiB 常驻内存）与你自己的声音样本。

```bash
git clone https://github.com/ThreeFish-AI/dub ~/Documents/projects/aurelius/dub
ln -sfn ~/Documents/projects/aurelius/dub ~/.claude/skills/dub     # Claude Code
ln -sfn ~/Documents/projects/aurelius/dub ~/.agents/skills/dub     # 其他宿主
```

务必用 `ln -sfn`：不带 `-n` 时，目标已是目录软链会在仓内造出自指嵌套。装好后重启会话才能发现新 skill。

验证：`python3 ~/.claude/skills/dub/scripts/dub.py styles` 能列出风格表。

指定统一输出目录（建议写进 `~/.claude/settings.json` 的 `env` 块）：

```json
{ "env": { "DUB_OUTPUT_DIR": "~/Documents/dub-output" } }
```

## 声音与授权

index-tts 用的是**本人声音克隆**，这是生物特征资产：

- 默认引擎是 edge；index-tts 须由本人明确要求并显式带 `--final-voice`，缺此 flag 的实跑硬失败。Agent 不得自行提议或替你补上。
- 样本 wav 永不入库（`.gitignore` 已忽略 `*.wav` / `*.mp3`），不贴进对话、不外传；用 `--expect-ref-sha1` 防样本被悄悄换掉。
- 他人的声音须有书面同意。详见 [VOICE-REFS](references/VOICE-REFS.md)。

## 文档地图

| 文档 | 内容 |
|---|---|
| [SKILL.md](SKILL.md) | Agent 入口：任务分流、命令速查、授权铁律 |
| [CONTRACT](references/CONTRACT.md) | CLI 全部参数、回执字段、退出码 |
| [ENGINES](references/ENGINES.md) | 双引擎契约、服务 HTTP 接口、朗读陷阱、排障 |
| [STYLES](references/STYLES.md) | 风格预设表与改预设的纪律 |
| [ENV](references/ENV.md) | 环境变量与路径解析 |
| [VOICE-REFS](references/VOICE-REFS.md) | 声音样本录制、保管与授权 |
| [knowledge-map](docs/.agents/knowledge-map.md) | 全仓文档索引 |

## 开发

```bash
uv run --no-project --with pytest --with numpy --with mutagen --with soundfile \
  --with fastapi --with httpx --with pydantic --with uvicorn python -m pytest tests -q
uv run pre-commit install    # ruff lint + format（钉版 v0.14.5）
```

本仓刻意不是 Python 包（无 `[project]`），脚本一律 `uv run --no-project` 并在调用点 `--with` 注入依赖。

## 相邻 Skill

- [vibe-video](https://github.com/ThreeFish-AI/vibe-video)：整集科普视频流水线，其配音阶段委托本仓的机制原语。
- [rsi](https://github.com/ThreeFish-AI/rsi)：使用中发现的缺陷与经验，经此回路沉淀。

## 许可与合规

MIT。IndexTTS 采用 bilibili 模型许可证，商用需另行取得许可；edge-tts 是微软在线服务的非官方客户端，产出须遵守微软条款，含按平台要求标注合成语音。
