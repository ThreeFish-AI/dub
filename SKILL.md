---
name: dub
description: 把一段文字配成 .mp3 配音：输入要配音的内容和配音风格，选 edge（微软预置音色，联网免密钥）或 index-tts 2.5（本地声音克隆，story 等段落演绎风格），产出 mp3 到环境变量 DUB_OUTPUT_DIR 指定的统一目录；index-tts 按需自动拉起并在空闲后收尾。Use when 用户要把一句话或一段稿子转成语音文件、给文字配音、用本人克隆声音朗读、指定 story 或其他风格出声、起停或体检本地 IndexTTS 服务，或询问配音引擎与风格怎么选——即使没有点名 dub。不用于整集视频制作流水线（走 vibe-video）、剪辑转码已有音频、语音识别转写、通用音乐音效生成，或与配音无关的文本改写。
license: MIT
compatibility: edge 引擎仅需 uv 与联网；index-tts 引擎需 macOS（MPS）、本地 ~/tools/index-tts checkout（IndexTTS-2.5 权重，约 10 GiB 常驻内存）、本人声音样本 wav，以及用户显式授权；宿主须能执行 Bash。
metadata:
  version: "0.1.0"
  author: "ThreeFish-AI"
  source: "extracted from ThreeFish-AI/vibe-video TTS stage"
allowed-tools: Read Write Edit Glob Grep Bash
---

# dub：文本 + 风格 → mp3

一句话入口：`dub.py say` 把文字合成 mp3，写进 `DUB_OUTPUT_DIR`，stdout 最后一行是 JSON 回执。

脚本当黑盒：先 `--help`，不必读源码。路径变量 `$T` = 本 skill 根目录（`${CLAUDE_SKILL_DIR}`）。

## 任务分流

| 用户要什么 | 做什么 |
|---|---|
| 把一句话/一段稿子配成 mp3（没指定声音） | edge 引擎：`say --text …`（默认引擎，无需授权） |
| 用**本人**克隆声音、story 等风格朗读 | index-tts 引擎，见下方「授权铁律」 |
| 看有哪些风格 | `python3 $T/scripts/dub.py styles` |
| 起/停/查本地 IndexTTS 服务、体检环境 | `python3 $T/scripts/dub.py server start\|stop\|status\|doctor` |
| 整集视频的逐句配音、manifest、时间轴 | 不在本 skill——走 vibe-video |

## 命令速查

```bash
# edge（默认）：联网，免密钥，24 kHz mono
uv run --no-project --with edge-tts --with mutagen $T/scripts/dub.py say \
  --text "今天我们拆解的是：Agent Skills" [--lang en] [--voice zh-CN-XiaoxiaoNeural] [--rate +4%]

# index-tts：本人声音克隆（须授权），story 风格，22.05 kHz mono
uv run --no-project --with mutagen --with soundfile --with numpy $T/scripts/dub.py say \
  --engine index-tts --final-voice --style story --ref <本人样本.wav> \
  --expect-ref-sha1 <样本 sha1 前 12 位> --text "今天我们拆解的是：Agent Skills"
```

- 文本来源二选一：`--text "…"` 或 `--text-file path`。
- 输出目录：`--out-dir` 优先，否则读 `DUB_OUTPUT_DIR`；两者皆无则大声退出，不替用户选落点。
- 文件名默认 `<文本slug>.<摘要12位>.mp3`；同参数同文本再跑直接命中缓存（回执 `cached:true`），`--force` 重造，`--out-name` 自定名。
- 回执 JSON：`{path, durationSec, sampleRate, engine, style, cached, chunks}`——把 `path` 原样报给用户。

## 授权铁律（RSI-040）

index-tts 用的是**本人声音克隆**（生物特征资产），默认不启用：

1. 默认走 edge。**Agent 不得自行提议、选用 index-tts，也不得替用户补加 `--final-voice`。**
2. 只有用户在对话里明确要求「用我的声音 / 克隆声音 / story 风格」时，才用 index-tts 并带 `--final-voice`。
3. 声音样本 wav 永不入库、不贴进对话、不外传；换样本会让音色整体漂移，所以带 `--expect-ref-sha1` 校验指纹。
4. 他人的声音须有书面同意，见 [VOICE-REFS](references/VOICE-REFS.md)。

## 服务生命周期（index-tts）

- `say --engine index-tts` 在服务未就绪时自动拉起（冷启动约 1–2 分钟），合成完收尾：**只停自己拉起的实例**，复用别人起的服务则不动。
- 收尾时若实例仍被其他进程使用，不杀，改起有界 watchdog（默认最长 2 小时）巡检，连续空闲后才停；日志在 `~/Library/Application Support/dub/logs/`。
- 想连续多次合成不反复冷启动：加 `--keep-server`，全部做完再 `server stop`。
- 只按端口定位进程，绝不 `pkill -f`；端口被非 tts 进程占用时拒绝启动。

## 关键不变量

- 风格是数据：预设参数住 `scripts/dub_profiles.py`，与 vibe-video 同源；缓存键由参数派生，**改任一预设数值会让已缓存音频全部失效**，只增不改。
- index-tts 长文本由 chunker 按 ≤3 句 / ≤90 字分块逐块合成再拼接（上游单请求约 36 秒上限，超出的尾部会被静默吞掉）。
- 写给 TTS 的年份别带空格（`2026 年` 会被读成「两千零二十六年」，写 `2026年`）；整句无汉字会被当英文归一化。完整陷阱表见 [ENGINES](references/ENGINES.md)。
- edge 偶发 403 / NoAudioReceived 是上游已知问题，脚本有界重试并给出 `--proxy` 提示；仍失败时如实报错，不改用 index-tts 顶替。

## 环境变量

| 变量 | 作用 |
|---|---|
| `DUB_OUTPUT_DIR` | 统一输出根（`--out-dir` 优先） |
| `DUB_DATA_DIR` | 版本库与日志（缺省 `~/Library/Application Support/dub`） |
| `DUB_INDEXTTS_SERVER` | 服务地址（缺省 `http://127.0.0.1:8766`） |
| `DUB_INDEX_TTS_ROOT` | index-tts checkout（缺省 `~/tools/index-tts`） |
| `DUB_STORE` | 内容寻址缓存根（置空串关闭） |
| `DUB_HOME` | 外部消费者定位本 skill |

完整表与解析顺序见 [ENV](references/ENV.md)。

## 按需加载

| 文档 | 何时读 |
|---|---|
| [CONTRACT](references/CONTRACT.md) | 要精确的 CLI 参数、回执字段、退出码 |
| [ENGINES](references/ENGINES.md) | 排障：服务 HTTP 契约、edge 403、MPS 显存、朗读陷阱 |
| [STYLES](references/STYLES.md) | 选风格、读懂预设参数 |
| [ENV](references/ENV.md) | 配置环境变量、挂载位置 |
| [VOICE-REFS](references/VOICE-REFS.md) | 录制/保管声音样本、授权与隐私 |

## 相邻技能

- **vibe-video**：整集视频流水线，其配音阶段的机制原语（预设、digest、服务端、客户端）上收到本 skill，vibe-video 通过 `DUB_HOME` / `~/.claude/skills/dub` 定位并委托。
- **rsi**：使用本 skill 时发现的缺陷或经验，经 rsi 回路沉淀。

## 自改进回路（RSI）

使用本 skill 时发现自身缺陷（门禁判不了、文档自相矛盾、死链、宿主能力不符且无降级路径）或可沉淀的经验：只捕获、不打断当前任务，记到 `.temp/rsi/dub/backlog.md`，事后由 rsi skill 统一处理。细则见 [rsi-hook](references/rsi-hook.md)。
