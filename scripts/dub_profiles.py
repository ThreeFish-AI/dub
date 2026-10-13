"""风格预设与音色缺省——dub 的「风格即数据」单一事实源。

STYLE_PRESETS 的数值是 IndexTTS-2.5 在作者本人声音样本（me-bright）上试听定档的结果，
任何一项改动都会改变 digest_indextts 的缓存键（⇒ 已缓存音频全部失效），故只增不改。
"""

from __future__ import annotations

import math

DEFAULT_VOICE = "zh-CN-YunxiNeural"
DEFAULT_RATE = "+4%"

# IndexTTS 8 维情感向量顺序（indextts/infer_v2_5.py 固定）：happy, angry, sad, afraid,
# disgusted, melancholic, surprised, calm。
#
# 有效和护栏 Σ分量×emo_alpha ≤ 0.8 是**本管线自定的口径**，不是上游行为（2026-08-20 核验）：
# 上游 infer() 从不归一化，normalize_emo_vec 全仓唯一调用点是 webui.py:665 的「自定义向量」
# 分支，且那条的 0.8 作用在**已乘 emo_bias 的和**上、且在 alpha 之前。
# emo_bias（infer_v2_5.py:493 硬编码）8 维严重不等权：
#   sad/afraid=1.0 > happy/disgusted/melancholic=0.9375 > angry=0.875 > surprised=0.6875 > calm=0.5625
# 后果：从社区/WebUI 抄来的 (vec, alpha) 经本管线复现实际**强 16%–33%**（含 calm 越重偏差越大，
# confident 档最失真），跨来源参数迁移必须重新试听定档。详见 INDEXTTS-2.5-ADVANCED.md §3.2。
#
# 另注：alpha 恰好等于「替换掉本人语调的百分比」**仅当名义向量和 = 1.0** ——
# sunny/passionate 刚好是 1.00，而 lively(0.85)/confident(0.90)/positive(0.95) 不是，
# 其 alpha 跨预设不可直接比较。
EMO_KEYS = [
    "happy",
    "angry",
    "sad",
    "afraid",
    "disgusted",
    "melancholic",
    "surprised",
    "calm",
]

# 风格预设 —— 数值为初值，可实测试听后微调。
# 可选键 "beams"：预设自带的束搜索宽度（缺省 1）。束宽会改变韵律稳定度，属风格的一部分，
# 故允许写进预设；命令行 --num-beams 显式给值时优先。束宽代价见下方 sunny-steady 注释
# （MPS 上近乎免费，CUDA 上近线性）；机器忙时想只让关键句更稳，用 --steady 混合档。
# 可选键 "sampling"：预设自带的采样参数覆盖（见 SAMPLING_DEFAULTS）。当前所有预设都不带 ——
# 任何写进预设的值都会改缓存摘要 ⇒ 整集重录，故须先 A/B 拿到证据再定档。
STYLE_PRESETS: dict[str, dict] = {
    "neutral": {"label": "中性", "vec": None, "alpha": 1.0, "df": 1.0},
    "passionate": {
        "label": "激情",
        # 高唤醒正价（happy 主载）+ 跳跃感（surprised）+ 少量 calm 锚定咬字；
        # 有效和 1.00×0.7=0.70 ≤0.8。
        # ⚠️ 校准（2026-08-20）：此处原注「df 0.97 护密集技术句清晰度」**方向写反了**。
        # duration_factor 作用于 S2M 的时间轴重采样（infer_v2_5.py:832 target_lengths），
        # df<1 → 梅尔帧更少 → 语速更快 → 每个音素分到的时间更短、咬字更紧，密集技术句
        # 应当**更糊**而非更清。护清晰度的正确方向是 df>1。0.97 的实际效果是「略快」，
        # 与本档「激情」的定位自洽，故数值保留、只更正因果表述。
        "vec": [0.70, 0, 0, 0, 0, 0, 0.20, 0.10],
        "alpha": 0.7,
        "df": 0.97,
    },
    # 以下三档的名义向量已归一到 Σvec=1.0（2026-08-20）：只有 Σvec=1.0 时 alpha 才等于
    # 「替换掉本人语调的百分比」，否则它被稀释成 α·Σvec、**跨预设不可比**。
    # alpha 同步反向缩放（α_new = α_old × Σvec_old），故有效注入 w=vec×alpha 在数学上不变。
    # **精确说法**：上游把 w 截断到 4 位小数（infer_v2_5.py:608 用 int() 截断而非四舍五入），
    # 而 0.33/0.455 一类值恰落在截断边界上，故 21 个分量里有 3 个出现 1e-4 的差异
    # （0.33→0.3299、0.455→0.4549、0.5249→0.525）。相对幅度 0.03%，远低于任何听感阈；
    # 且这三档**未被任何已上线剧集使用**（三集用 sunny-steady / passionate），即便真有
    # 差异也无影响。不要把它写成「逐项完全一致」——那是过度断言。
    # 由 tests/test_digest.py::test_preset_normalization_preserves_injection 钉死 1e-4 界。
    "lively": {
        "label": "轻快",
        "vec": [
            0.6470588,
            0,
            0,
            0,
            0,
            0,
            0.1764706,
            0.1764706,
        ],  # 原 .55/.15/.15 ÷ 0.85
        "alpha": 0.51,  # 原 0.6 × 0.85
        "df": 0.95,
    },
    "confident": {
        "label": "自信",
        "vec": [0.2777778, 0, 0, 0, 0, 0, 0, 0.7222222],  # 原 .25/.65 ÷ 0.90
        "alpha": 0.63,  # 原 0.7 × 0.90
        "df": 1.05,
    },
    "positive": {
        "label": "正能量",
        "vec": [0.7894737, 0, 0, 0, 0, 0, 0, 0.2105263],  # 原 .75/.20 ÷ 0.95
        "alpha": 0.665,  # 原 0.7 × 0.95
        "df": 1.0,
    },
    "sunny": {
        "label": "明快阳光",
        # 2026-08-19 试听定档（科普长视频推荐位）。方向由 QwenEmotion 对「轻快、爽朗、
        # 自信、阳光」推出（happy 近乎独载），强度则**人工压到 0.35**——Qwen 原始输出会顶到
        # Σ=0.8 上限，实测把克隆音高推到 199–223 Hz（说话人自然区间仅 142–163 Hz）而显假；
        # 0.35 留 65% 给本人真实语调，配 df 0.95 取「明快但不飘」。
        # 配套样本很关键：本档在 voices/me-bright.wav（me-1.mp3 --start 0.36 --duration 12）
        # 上定档，换回更闷的样本会失去明快感——见 VOICE-CLONING.md §3.3。
        "vec": [0.95, 0, 0, 0, 0, 0, 0.02, 0.03],
        "alpha": 0.35,
        "df": 0.95,
    },
    "sunny-steady": {
        "label": "明快稳健",
        # = sunny 同方向同强度同语速，只把束宽提到 3：GPT 段搜索更宽 → 韵律更收敛。
        # 实测（同文本同样本）：语调起伏 48.4 → 43.5、音节率 4.10 → 4.55，亮度基本不掉
        # （质心 1245 → 1223）——是唯一「不牺牲明快度就让语气更稳」的旋钮。
        # 代价高度依赖硬件，**不是无条件的「线性放大」**：束宽只作用于 T2S（S2M 入口的
        # codes 形状与束宽无关），而 MPS 上 T2S 只占三段耗时的 18–46%（2026-08-20 本机
        # 13 样本分段 profile：s2mel 反而占 45–73%）。整集实测 1→3 束仅 +4%
        # （EP1 v3 两遍法 1.9h → 1.98h）。故：CUDA 上近线性放大，MPS 上近乎免费。
        # 机器忙/热节流时仍可能出现数倍差，此时用 `--steady` 混合档只升关键句。
        "vec": [0.95, 0, 0, 0, 0, 0, 0.02, 0.03],
        "alpha": 0.35,
        "df": 0.95,
        "beams": 3,
    },
    # ── 以下两档是**候选**，尚未定档：新增而非改动生产档，故对三集缓存零影响。
    #    定档前必须按 INDEXTTS-2.5-ADVANCED.md §6.5 的测量协议做 A/B + 人工试听定档。
    "sunny-pure": {
        "label": "明快纯载",
        # 候选（路线图 #10）：砍掉 sunny 里的配料维度，使 Σvec=1.0 与「happy 单载」
        # 同时成立 ⇒ alpha 语义最干净（它就是被替换掉的本人语调比例）。
        # ⚠️ 立论勘误（2026-09-02）：本档原注以 emo_bias「surprised 只兑付 69%、
        # calm 56%」为依据——该折扣**在本管线不生效**。施加 bias 的 normalize_emo_vec
        # （infer_v2_5.py:488）全仓唯一调用点是 webui.py:665；infer() 内对 emo_vector
        # 的唯一变换是 :605-607 的 int(x*alpha*10000)/10000，无 bias 相乘。故本管线
        # 名义权重即实际权重，「配料维度不划算」这条理由作废，本档退回纯 alpha 语义论证。
        # 顺带：surprised 的原型数是 10（happy 只有 3，emo_num=[3,17,2,8,4,5,10,24]），
        # 表达基底并不贫乏——砍它的代价可能比原注设想的大，定档须以试听为准。
        "vec": [1.0, 0, 0, 0, 0, 0, 0, 0],
        "alpha": 0.35,
        "df": 0.95,
    },
    "sunny-clear": {
        "label": "明快清晰",
        # 候选（路线图 #11）：= sunny-steady 但 df 1.05。依据是 df 方向的勘误——
        # df<1 = 更快 = 每音素分到的时间更短 = 咬字更紧更糊，护术语密集句的清晰度
        # 正确方向是 df>1（§3.4）。明快感仍由 happy/alpha 承担，与语速正交。
        # 代价：拉长时长 ⇒ 牵动 beat 与片尾渐黑窗口，定档后须重渲。
        "vec": [0.95, 0, 0, 0, 0, 0, 0.02, 0.03],
        "alpha": 0.35,
        "df": 1.05,
        "beams": 3,
    },
    "story": {
        "label": "段落演绎",
        # 2026-09-25 第三轮试听定档（#07，试听页 .context/voice-audition/v3/）：合成单位从
        # 「句」升为「故事块」——同幕连续句拼一次请求，上游 front.py 合并短句为单段 ⇒
        # 句间韵律连续 + 自然停顿，替代逐句合成的「朗读感」（逐句/单一情绪/恒定句距
        # 三者叠加是根因，见 issue.md RSI-011）。
        # - vec/alpha 取 #07 g2 方向（surprised 主载 + happy）×0.28 有效注入；无台本块/
        #   未覆盖块用它，块情绪可被 script/narration.cues.toml 逐块覆盖（§4.5）。
        # - seed 固定 4242：试听定档 take 可复现；单块换 take 用台本 [take]（--seed-offset 是整集口径）。
        # - block: 块上限与切分参数；perform_punct 开表演标点（……→…，tts_text）。
        "vec": [1 / 3, 0, 0, 0, 0, 0, 2 / 3, 0],
        "alpha": 0.28,
        "df": 1.0,
        "beams": 1,
        "seed": 4242,
        "block": {
            "max_sentences": 3,
            "max_chars": 90,
            "tail_pad_sec": 0.18,
            "perform_punct": True,
        },
    },
}


#: edge 引擎缺省音色（按 --lang）：zh 与历史口径一致，en 取男声 Andrew。
EDGE_VOICE_BY_LANG: dict[str, str] = {"zh": DEFAULT_VOICE, "en": "en-US-AndrewNeural"}


def parse_emo_vector(spec: str) -> list[float]:
    """`happy:0.6,calm:0.2` → 8 维向量；未知键/负值/空集报错。"""
    vec = [0.0] * 8
    seen: set[str] = set()
    for part in spec.split(","):
        key, _, val = part.partition(":")
        key, val = key.strip().lower(), val.strip()
        if key not in EMO_KEYS:
            raise ValueError(f"未知情感键 {key!r}（可用：{','.join(EMO_KEYS)}）")
        if key in seen:
            raise ValueError(f"情感键重复：{key}")
        if not val:
            raise ValueError(f"情感权重缺失：{key}（格式如 happy:0.6）")
        weight = float(val)
        if not math.isfinite(weight) or weight < 0:  # isfinite 显式拦 NaN/Inf
            raise ValueError(f"情感权重必须为非负有限数值：{key}")
        vec[EMO_KEYS.index(key)] = weight
        seen.add(key)
    if not seen:
        raise ValueError("--emo-vector 不能为空")
    return vec
