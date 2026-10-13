# Issue ledger（RSI 回路的登记面）

本仓缺陷与改进的唯一登记处，由 [rsi-hook](../../references/rsi-hook.md) 所指的 RSI 回路消费。编号 `RSI-xxx` 三位递增，从 RSI-001 起。

纪律：

- 发现即登记，新条目追加文件末尾；同一问题只维护一处，复发不新开编号（在原条目追加带日期的复盘点）；
- 条目字段六段式：表因 / 根因 / 定性 / 处理方式 / 后续防范 / 同类问题影响（可选）；
- 登记本身不等于启动处理——处理走 RSI 回路。

---

## RSI-001 配音机制深埋在视频流水线里，无法独立调用

**表因**：要把一句话配成 mp3，只能走 vibe-video 的整集流水线（`narration.json` → 逐句合成 → manifest），或手拼 `tts_sample.py`（仅 index-tts、依赖工作区路径、输出落在工程目录内）。edge 引擎没有任何「文本 → mp3」的独立入口，输出目录也没有环境变量可控。

**根因**：TTS 机制（风格预设、digest 缓存、文本归一化、服务端、客户端原语）是随视频流水线一起长出来的，从未与「逐句编排 + 时间轴」分层；`INDEXTTS_SERVER` 在 vibe-video 的 `config.py` 注册却没接进 `tts.py`（默认地址写死），说明机制层与编排层的契约本来就是松的。

**定性**：非阻断改进（机制缺口，不卡视频制作，但让配音能力无法复用）。

**处理方式**：抽成独立 Skill dub——`dub.py say` 以文本 + 风格产出 mp3，edge 与 index-tts 两引擎，输出到 `DUB_OUTPUT_DIR`；服务端 vendor 自 vibe-video 的 `tts_server.py`，仅把两处 `from tts import` 改指 `dub_tts_blocks`；摘要格式、风格预设与 vibe-video 逐字节同源，以黄金哈希钉死。vibe-video 同步改为委托 dub（另见其台账）。

**后续防范**：

- 机制原语只住 dub，vibe-video 不得再内联或另拷一份；黄金哈希与预设不变量的测试同样只住 dub（`tests/test_digest.py` 等），vibe-video 经委托自动继承，不留镜像副本；
- dub 的库模块一律 `dub_` 前缀，由 `tests/test_module_names.py` 钉死，防止 vibe-video 把 dub 的 `scripts/` 插进 `sys.path` 后同名模块互相遮蔽；
- 预设只增不改，改任一已上线预设的数值都会让已缓存音频全部失效。

**同类问题影响**：任何把「机制」和「编排」揉在一起的 Skill 都有同样的复用障碍；抽取时先找耦合点（本次只有 `tts_server.py` 对 `tts.py` 的两处 import）再决定切口，比先设计新接口更稳。
