# 风格预设

**目录**：[一、风格是什么](#一风格是什么) · [二、预设表](#二预设表) · [三、story 的机制](#三story-的机制) · [四、改预设的纪律](#四改预设的纪律)

## 一、风格是什么

index-tts 的「风格」是一组声学参数，叠加在本人声音样本之上：

- `vec`：8 维情感向量（happy, angry, sad, afraid, disgusted, melancholic, surprised, calm）；
- `alpha`：情感强度，叠加后语调被替换的比例；
- `df`：`duration_factor`，>1 更慢更清晰；
- `beams`：束搜索宽度，改变韵律稳定度；
- `seed`：固定采样种子，保证同句可复现；
- `block`：段落演绎配置（story 独有）。

edge 没有「风格」，只有音色 `--voice` 与语速 `--rate`。

数据源是 `scripts/dub_profiles.py` 的 `STYLE_PRESETS`，与 vibe-video 同源，校准注释保留在代码里。运行 `dub.py styles` 看当前表。

## 二、预设表

| 风格 | 向量和 | alpha | df | beams | seed | 一句话 |
|---|---|---|---|---|---|---|
| `neutral` | — | 1.0 | 1.0 | 1 | — | 中性，不注入情感 |
| `passionate` | 1.00 | 0.7 | 0.97 | 1 | — | 激情 |
| `lively` | 1.00 | 0.51 | 0.95 | 1 | — | 轻快 |
| `confident` | 1.00 | 0.63 | 1.05 | 1 | — | 自信 |
| `positive` | 1.00 | 0.665 | 1.0 | 1 | — | 正能量 |
| `sunny` | 1.00 | 0.35 | 0.95 | 1 | — | 明快阳光 |
| `sunny-steady` | 1.00 | 0.35 | 0.95 | 3 | — | 明快稳健（sunny + 3 束，定稿档） |
| `sunny-pure` | 1.00 | 0.35 | 0.95 | 1 | — | 候选，明快纯载 |
| `sunny-clear` | 1.00 | 0.35 | 1.05 | 3 | — | 候选，明快清晰 |
| `story` | 1.00 | 0.28 | 1.0 | 1 | 4242 | 段落演绎，讲故事的口吻 |

怎么选：科普解说默认 `story`；要明快稳定用 `sunny-steady`；要情绪更饱满用 `passionate`；不想注入任何情感用 `neutral`。

## 三、story 的机制

`story` = 向量 `[1/3, 0, 0, 0, 0, 0, 2/3, 0]`（happy 1/3 + surprised 2/3）、alpha 0.28、seed 4242、`block = {max_sentences: 3, max_chars: 90, tail_pad_sec: 0.18, perform_punct: true}`。

- **表演标点**：`……` 保留为单个 `…`，上游把它当独立 token 并给拖长停顿；其他风格仍压成 `。`。
- **分块**：长文本按 ≤3 句 / ≤90 字装箱，每块一次请求，块间补 200 ms 静音。
- **预设校准在 me-bright 样本上**：alpha 与向量是针对作者本人 12 秒样本调出来的，换样本需重新试听定档。
- 单句/短文本用 story 只得到向量、alpha、seed 和表演标点；块内跨句韵律需要多句成块才有。

## 四、改预设的纪律

任何写进预设的数值都会改缓存摘要，**已缓存音频全部失效**，所以：

1. 只增不改：要调参，加一个新名字的预设，不覆盖旧的；
2. 先 A/B：拿到试听证据再定档，不凭直觉改；
3. 跨来源迁移参数要重新试听——上游 `emo_bias` 对 8 个维度严重不等权（sad/afraid=1.0 > happy/disgusted/melancholic=0.9375 > angry=0.875 > surprised=0.6875 > calm=0.5625），从社区抄来的 `(vec, alpha)` 在本管线里实际更强 16%–33%；
4. 有效和护栏 Σvec×alpha ≤ 0.8 是本管线自定口径，服务端会 400。

改动后 dub 与 vibe-video 两边的 `test_digest.py` 都要跑，黄金哈希不变才算没动缓存契约。
