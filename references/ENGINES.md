# 双引擎契约与怪癖

**目录**：[一、选哪个引擎](#一选哪个引擎) · [二、edge](#二edge) · [三、index-tts 服务与 HTTP 契约](#三index-tts-服务与-http-契约) · [四、长文本与分块](#四长文本与分块) · [五、朗读陷阱](#五朗读陷阱) · [六、故障与排障](#六故障与排障) · [七、许可与合规](#七许可与合规)

事实来源是 vibe-video 的长跑实测与 edge-tts / IndexTTS-2.5 上游源码；改动本文件的数字前先对账来源。

## 一、选哪个引擎

| | edge | index-tts 2.5 |
|---|---|---|
| 声音 | 微软预置音色 | 本人声音克隆 |
| 依赖 | 联网，免密钥 | 本地服务，约 10 GiB 常驻内存 |
| 速度 | 秒级 | 实时率约 9–13（1 秒音频约 9–13 秒） |
| 输出 | 24 kHz / 48 kbps / mono MP3（上游写死） | 22.05 kHz / mono MP3（VBR，约 50 kbps） |
| 授权 | 无 | 须本人显式授权（RSI-040） |
| 风格 | 仅 `--voice` / `--rate` | 预设风格（[STYLES](STYLES.md)） |

默认 edge。index-tts 仅在用户明确要求本人声音时使用，Agent 不得自行提议。

## 二、edge

- 实现：`edge_tts.Communicate(text, voice, rate=…).save()`；文本超 4096 字节由库内自行切分，所以 dub 对 edge 不做客户端分块。
- 缺省音色：zh → `zh-CN-YunxiNeural`，en → `en-US-AndrewNeural`；缺省语速 `+4%`。其他中文男声 `YunjianNeural`、`YunyangNeural`，女声 `XiaoxiaoNeural`、`XiaoyiNeural`；全表 `uvx edge-tts --list-voices`。
- `rate` 必须匹配 `^[+-]\d+%$`，否则库抛 `ValueError`；负值在 CLI 里要写成 `--rate=-10%`。
- 版本：edge-tts 7.2.8（LGPL-3.0，经 uv 解析，不 vendor）。

## 三、index-tts 服务与 HTTP 契约

服务脚本是 `scripts/dub_tts_server.py`（FastAPI），必须在 index-tts checkout 的 uv 环境内按**绝对路径**运行，cwd 为 checkout 根：

```bash
cd ~/tools/index-tts && uv run --frozen --with fastapi --with uvicorn --with soundfile --with numpy --with lameenc \
  python $T/scripts/dub_tts_server.py --model-dir checkpoints --indextts-version 2.5 --host 127.0.0.1 --port 8766
```

日常不必手敲，`dub.py server start` 就是这条命令。服务只监听 127.0.0.1，无鉴权，`ref_path` 是**服务端本地绝对路径**。

### `GET /health`

返回 `ok, version, device, mps_mem_limit_gib, synthesizing, dtype, encoder` 与若干 `supports_*` 能力位。`synthesizing` 是 watchdog 判忙的依据之一。

### `POST /synthesize`（JSON）

| 字段 | 缺省 / 范围 |
|---|---|
| `text`、`ref_path` | 必填，`ref_path` 为绝对路径 |
| `emo_vector` | 8 维：happy, angry, sad, afraid, disgusted, melancholic, surprised, calm；非负 |
| `emo_ref_path` / `emo_text` | 与 `emo_vector` 互斥，三选一；`emo_text` 需服务端带 `--use-qwen-emo` |
| `emo_alpha` | 1.0（0–1）；**有效和 Σvec×alpha ≤ 0.8**，否则 400 |
| `duration_factor` | 1.0（0.5–2.0，>1 更慢更清晰） |
| `lang` | `ZH` / `EN` 等 |
| `num_beams` | 1（1–5） |
| `temperature` / `top_p` / `top_k` | 0.8 / 0.8 / 30（`top_k=1` 被拒） |
| `length_penalty` / `repetition_penalty` | 0.0 / 10.0 |
| `max_mel_tokens` | 1500（50–1815，约 30 s；硬顶约 36.2 s） |
| `interval_silence` | 200 ms |
| `text_normalization` | true（别关，数字/百分号读法靠它） |
| `seed` | 无；给定即可复现 |
| `block` | `{weights[], discard_sec, tail_pad_sec}`，块模式（见下） |

- 响应（普通）：MP3 字节 + 头 `X-Audio-Format`（`mp3`，缺编码器时退化为 `wav`——dub 客户端会硬拒）、`X-Duration-Sec`。
- 响应（带 `block`）：JSON `{split, clips[{audio(b64), durationSec, format}], cuts, seams}`；`split:"failed"` 是可降级的业务结果，仍返回 200。
- 错误：400（校验失败/文件缺失，不可重试）、422（pydantic）、503（模型还在加载）、500（NaN 或 MPS OOM，附可操作文案）。
- 推理由单个 asyncio 锁串行，客户端并发恒为 1。

### 采样参数与缓存键

采样参数只有**被显式改过**才进缓存摘要，全默认时摘要与历史逐字节相同——这是已上线 15 集近千句缓存零失效的前提。摘要格式 `indextts|{tag}|{ref_sha1}|{lang}|{style}|{vec}|{alpha!r}|{df!r}|{text}` 后接可选后缀（`|beams=N`、`|emoref=`、`|emotext=`、采样 `k=v`、`|block=…`），新增字段只许追加在末尾且默认省略。改动前先读 vibe-video 与 dub 两边的 `test_digest.py` 黄金哈希。

## 四、长文本与分块

- 上游单请求 mel token 上限 1815（约 36 s），**超出的尾部文本静默不读**；文本超过 118 token 还会被上游按标点二次切段并插 200 ms 硬静音。
- dub 的 chunker（`dub_textprep.split_chunks`）按句界贪心装箱，每块 ≤3 句且 ≤90 字（取自 story 预设的 `block` 配置），逐块合成后由 `dub_audio.concat_mp3` 解码、块间补 200 ms 静音、重编码为单个 MP3。
- 拼接用 soundfile（libsndfile ≥1.1 可读写 MP3），本机无需 ffmpeg；各块采样率不一致时报错而非静默重采样。
- 单块（短文本）直通，不做重编码。
- 局限：dub 的分块是纯文本贪心装箱，不复现 vibe-video 台本侧「块情感 cue / 同幕边界 / DP 窗口」的跨句韵律；单句或短文本用 story 时只得到向量、alpha、seed 与表演标点，没有块内跨句韵律。

## 五、朗读陷阱

| 写法 | 读成 | 处理 |
|---|---|---|
| `2026 年`（年份与「年」间有空格） | 两千零二十六年 | 写 `2026年` |
| `2.5.1` | 二点五点一 | 写成「2.5 版」或拆开 |
| `3-5 倍` | 三减五倍 | 写「三到五倍」 |
| `±3%` | 百分之正负三 | 写「正负百分之三」 |
| `10x` | 十 x | 写「十倍」 |
| `1080P` | 按基数词读 | 按需改写 |
| `≈` | 不发音 | 写「约」 |
| 整句无汉字 | 走英文归一化（`IndexTTS 2.5` → two point five） | 补汉字或接受英文读法 |

- 标点映射（`tts_text`，仅 zh）：`——` → `，`；`……` → `。`（story 的表演标点档保留为 `…`）。
- 以英文单词收尾并带全角引号句号的句子，尾部缺陷率约 50%（如「Context」读成「Context text」）。
- 其余读法已被验证正确的写法：`0.5~1.0 秒`、`9:30`；上游归一化幂等，预先写成中文读法是安全的。
- 发音标注 `<字|读音>` 是 vibe-video 逐稿流水线的能力，dub 的 `say` 不解析它。
- dub 只做标点映射，不做朗读陷阱 lint；写稿期的 lint 门留在 vibe-video 的 `check_script.py`。

## 六、故障与排障

| 现象 | 原因与处理 |
|---|---|
| `/health` 绿但合成全 500 | MPS 缓存累积（约 40 分钟）。服务端每次合成后 `empty_cache`，并按 `--mps-mem-limit-gib`（缺省 min(0.90×推荐, 16)）设水位线；仍击穿则按端口重启 |
| 500 且文案以「MPS 显存上限不足」开头 | 确定性 OOM，句/块内在需求超上限，重试只是空跑；客户端不重试，调高上限或缩短文本 |
| 冷启动慢 | 模型加载约 1–2 分钟，`start` 等待至多 300 s |
| 端口被占 | 若占用者不是 tts 服务，dub 拒绝启动也拒绝终止；先查 `lsof -nP -iTCP:8766` |
| edge 403 / 握手失败 | 上游 rany2/edge-tts#490 的 Sec-MS-GEC 地域性回归；`uvx edge-tts@latest` 升级，或经 `HTTPS_PROXY` 走代理 |
| edge `NoAudioReceived` | 音色名/参数非法、文本空或仅标点，或间歇性故障（#473） |
| edge 空文本 | 上游会写 0 字节文件且退出 0；dub 在入口拒绝空文本 |
| 同一文本音色不稳 | 固定 `seed`（story 预设已带 4242）；单句补丁不要临时加 `--seed`，它会进缓存键 |

服务生命周期铁律：只按**端口**定位进程，绝不 `pkill -f`；停服前要求连续空闲（逐请求短连接的客户端在句间有毫秒级空窗，单次采样会误杀别人长跑中途的服务）；watchdog 每个 tick 复核监听 PID 与 cmdline，防 PID 复用。

## 七、许可与合规

- IndexTTS 采用 bilibili 模型许可证，商用需另行取得许可。
- edge-tts 是微软在线服务的非官方客户端，产出须遵守微软条款，含按目标平台要求标注合成语音。
- 声音克隆涉及生物特征，规范见 [VOICE-REFS](VOICE-REFS.md)。
