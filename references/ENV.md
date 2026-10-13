# 环境变量与路径解析

**目录**：[一、注册表](#一注册表) · [二、解析顺序](#二解析顺序) · [三、挂载位置](#三挂载位置) · [四、与 vibe-video 的对应](#四与-vibe-video-的对应)

原则：**机器属性只走环境变量**，不写进受版本控制的文件；找不到即大声退出，绝不回退猜测。

## 一、注册表

| 变量 | 缺省 | 作用 |
|---|---|---|
| `DUB_OUTPUT_DIR` | 无（缺则 `say` 大声退出） | `say` 的统一输出根；`--out-dir` 优先 |
| `DUB_DATA_DIR` | `~/Library/Application Support/dub` | 版本库 `tts-store/`、服务状态与 `logs/` |
| `DUB_INDEXTTS_SERVER` | `http://127.0.0.1:8766` | IndexTTS 服务地址；`--server` 优先 |
| `DUB_INDEX_TTS_ROOT` | `~/tools/index-tts` | index-tts checkout，服务在其 uv 环境内运行（cwd） |
| `DUB_STORE` | `<DUB_DATA_DIR>/tts-store` | 内容寻址缓存根；置**空串**关闭 |
| `DUB_HOME` | 无 | 外部消费者（如 vibe-video）定位本 skill 的根目录 |

持久配置 `DUB_OUTPUT_DIR`：写进 `~/.claude/settings.json` 的 `env` 块，例如 `"DUB_OUTPUT_DIR": "~/Documents/dub-output"`，所有会话自动带上。

## 二、解析顺序

| 对象 | 顺序 |
|---|---|
| 输出目录 | `--out-dir` → `DUB_OUTPUT_DIR` → 大声退出 |
| 服务地址 | `--server` → `DUB_INDEXTTS_SERVER` → `http://127.0.0.1:8766` |
| 数据根 | `DUB_DATA_DIR` → `~/Library/Application Support/dub` |
| 版本库 | `--no-store` → `DUB_STORE`（空串=关）→ `<数据根>/tts-store` |
| index-tts 根 | `DUB_INDEX_TTS_ROOT` → `~/tools/index-tts` |
| 定位 dub 本身 | `DUB_HOME` → `~/.claude/skills/dub` → `~/.agents/skills/dub` |

定位 dub 时每个候选必须同时含 `SKILL.md` 与 `scripts/dub.py`，全部落空则列出所有候选与安装命令后退出。

## 三、挂载位置

```bash
git clone https://github.com/ThreeFish-AI/dub ~/Documents/projects/aurelius/dub
ln -sfn ~/Documents/projects/aurelius/dub ~/.claude/skills/dub      # Claude Code
ln -sfn ~/Documents/projects/aurelius/dub ~/.agents/skills/dub      # 其他宿主 / Codex 路由
```

务必用 `ln -sfn`：目标已是指向目录的软链时，不带 `-n` 的 `ln` 会顺着链接在仓库内创建嵌套软链（自指无限嵌套），本仓 `.gitignore` 里的 `/dub` 就是这个守卫。挂载后需重启会话才能发现新 skill。

验证：`python3 ~/.claude/skills/dub/scripts/dub.py styles` 能列出风格表。

## 四、与 vibe-video 的对应

vibe-video 把 TTS 机制原语上收到 dub，并保留自己的工作区级变量：

| vibe-video 变量 | 作用 | 与 dub 的关系 |
|---|---|---|
| `VIBE_VIDEO_TTS_STORE` | 分集配音的版本库根 | vibe-video 自己的库，缺省目录不变，golden digest 不变，所以存量缓存零失效；dub 的 `DUB_STORE` 是独立的库 |
| `VIBE_VIDEO_INDEX_TTS_ROOT` | 冷重启服务用的 checkout | 与 `DUB_INDEX_TTS_ROOT` 同义，各自读各自的 |
| `INDEXTTS_SERVER` | 服务地址 | 与 `DUB_INDEXTTS_SERVER` 同义，各自读各自的 |

两边的服务脚本是同一份代码（vibe-video 委托 dub 的 `dub_tts_server.py`），同一台机器同一时刻只需要一个服务实例，端口 8766 上无论谁拉起都可复用。
