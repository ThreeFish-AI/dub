# 知识索引（全仓文档的唯一索引）

本文件是仓内文档的全量索引。README「文档地图」只列入口文档并指向这里；文档目录有增删、改名或迁移时，同一次提交里同步本表。

## 入口与路由

| 文档 | 内容 | 读者 |
|---|---|---|
| [SKILL.md](../../SKILL.md) | Skill 路由壳：任务分流、命令速查、授权铁律、服务生命周期、关键不变量、按需加载 | Agent（激活即加载） |
| [README.md](../../README.md) | 门面：能做什么、安装、快速上手、文档地图、许可 | 人 |
| [CHANGELOG.md](../../CHANGELOG.md) | 版本史 | 人 / Agent |

## 机制契约（references/）

| 文档 | 内容 |
|---|---|
| [CONTRACT.md](../../references/CONTRACT.md) | CLI 契约：`say` 全部参数、回执 JSON、退出码、缓存与文件名、`server` 子命令 |
| [ENGINES.md](../../references/ENGINES.md) | 双引擎契约与怪癖：edge、IndexTTS 服务 HTTP 契约、长文本分块、朗读陷阱、故障排障、许可 |
| [STYLES.md](../../references/STYLES.md) | 风格预设表、story 的机制、改预设的纪律 |
| [ENV.md](../../references/ENV.md) | 环境变量注册表、解析顺序、挂载位置、与 vibe-video 的对应 |
| [VOICE-REFS.md](../../references/VOICE-REFS.md) | 声音样本录制规范、指纹校验、保管与授权 |
| [rsi-hook.md](../../references/rsi-hook.md) | RSI 薄钩子指针 |

## 台账与评测

| 文档 | 内容 |
|---|---|
| [docs/.agents/issue.md](issue.md) | Issue 台账（`RSI-xxx`，六段式） |
| [evals/README.md](../../evals/README.md) | 评测说明：输出质量评测与触发评测 |
