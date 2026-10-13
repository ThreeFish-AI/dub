# CLI 契约

**目录**：[一、入口](#一入口) · [二、say](#二say) · [三、回执 JSON](#三回执-json) · [四、退出码](#四退出码) · [五、缓存与文件名](#五缓存与文件名) · [六、server 子命令](#六server-子命令) · [七、styles](#七styles)

入口脚本 `scripts/dub.py`，脚本当黑盒：先 `--help`。路径变量 `$T` = 本 skill 根目录。

## 一、入口

```bash
python3 $T/scripts/dub.py {say|styles|server} …
```

| 引擎 | 调用前缀 |
|---|---|
| edge | `uv run --no-project --with edge-tts --with mutagen` |
| index-tts | `uv run --no-project --with mutagen --with soundfile --with numpy` |
| `styles` / `server` | 纯 stdlib，直接 `python3` 即可 |

第三方库全部惰性 import：缺哪个才报哪个，不会因为没带 `--with` 而在无关路径上失败。

## 二、say

| 参数 | 说明 |
|---|---|
| `--text` / `--text-file` | 二选一，必填其一；文本 strip 后为空即报错 |
| `--engine edge\|index-tts` | 默认 `edge` |
| `--lang zh\|en` | 默认 `zh`；决定标点映射、edge 缺省音色、index-tts 的 `lang` 码 |
| `--voice` | [edge] 音色名；缺省按 `--lang` 取 |
| `--rate` | [edge] 如 `+4%`、`-10%`；缺省 `+4%` |
| `--style` | [index-tts] 预设名，缺省 `story`；见 [STYLES](STYLES.md) |
| `--ref` | [index-tts] 参考样本 wav，必填 |
| `--expect-ref-sha1` | [index-tts] 样本 sha1 前 12 位，不符即拒绝 |
| `--final-voice` | [index-tts] 本人显式要求的具名授权，缺则硬失败（RSI-040） |
| `--server` | [index-tts] 服务地址，缺省 `DUB_INDEXTTS_SERVER` 或 `http://127.0.0.1:8766` |
| `--keep-server` | [index-tts] 合成后不停服务 |
| `--no-store` | [index-tts] 不读写版本库 |
| `--out-dir` | 输出目录，缺省 `DUB_OUTPUT_DIR` |
| `--out-name` | 输出文件名（不含 `.mp3`），只能是纯文件名：含路径分隔符、`.`、`..` 或空串即退出码 2 |
| `--force` | 忽略缓存强制重合成 |

## 三、回执 JSON

stdout **最后一行**，其余进度信息走 stderr：

```json
{"path": "…/今天我们拆解的是-agent-skills.623d54fa98dd.mp3", "durationSec": 3.36, "sampleRate": 24000, "engine": "edge", "style": "zh-CN-YunxiNeural", "cached": false, "chunks": 1}
```

| 字段 | 含义 |
|---|---|
| `path` | 产物绝对路径 |
| `durationSec` | mutagen 实测时长，保留 3 位 |
| `sampleRate` | edge 恒 24000；index-tts 为 22050 |
| `engine` | `edge` / `index-tts` |
| `style` | edge 为实际音色名；index-tts 为预设名 |
| `cached` | 整段命中缓存时为 `true`，此时未发出任何合成请求 |
| `chunks` | index-tts 的分块数；edge 恒为 1 |

## 四、退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 运行期失败（网络、服务、IO；消息带可操作提示） |
| 2 | 用法错误（缺参数、未授权、样本指纹不符、未知风格、`--out-name` 非法） |

缺输出目录由 `dub_paths.output_dir` 以 `sys.exit(消息)` 大声退出（退出码 1）。

## 五、缓存与文件名

- 默认文件名 `<文本 slug>.<摘要前 12 位>.mp3`：slug 保留字母数字与汉字、其余折成 `-`、截 24 字；摘要由引擎与全部影响音频的参数派生；index-tts 的整段摘要在各块摘要之外还含块间静音时长，该常量一改整段成品即换名（块级版本库不受影响）。
- 同文本同参数再跑，目标文件已存在即直接返回（`cached:true`），不联网、不起服务。
- index-tts 另有**块级版本库**：每个块按 digest 存入 `DUB_STORE`（缺省 `<DUB_DATA_DIR>/tts-store`），文本只改一句只重合成变更的块；换样本或换风格会换 digest 自然未命中。
- `--force` 同时绕过整段缓存与版本库。

## 六、server 子命令

| 动作 | 行为 |
|---|---|
| `start` | 已健康 → 复用；端口被非 tts 进程占用 → 拒绝；否则拉起并等就绪（至多 300 s；超时或被 Ctrl-C 中断时回收本次拉起的进程，不留孤儿）。回执 `started:true` 才表示是 dub 拉起的 |
| `stop` | 无服务 → `not-running`；监听者非 tts 服务 → `foreign`（不动手）；空闲 → 立即 `stopped`；被占用 → 起有界 watchdog，回执 `watchdog` 与日志路径 |
| `status` | 回 `healthy`、`health` 原文、`listeners` |
| `doctor` | 体检环境并打印，缺项只提示不失败 |

watchdog 判忙：`/health.synthesizing`，或存在**其他进程**连向服务端口的 ESTABLISHED 连接；停服需**连续**空闲（缺省 90 s）；缺省最长巡检 2 小时，超时放弃并保留服务；日志在 `<DUB_DATA_DIR>/logs/watchdog-<port>.log`。

## 七、styles

打印全部预设的向量和、alpha、df、beams、seed 与说明，并列出 edge 缺省音色与语速。
