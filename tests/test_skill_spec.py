"""SKILL.md 与 Agent Skills 规范的合规执法（移植自 vibe-video tests/test_skill_spec.py，口径同源）。

规范来源：agentskills.io/specification、官方参考校验器 skills-ref（strictyaml 解析）、
Anthropic skill authoring best practices。本文件不引入 YAML 依赖（不变量 14），改用一个
**比 strictyaml 更严**的单行子集解析器：本解析器接受的 frontmatter，strictyaml 与 PyYAML
都按同一语义解析；flow style、块标量、隐式类型一律拒收。之所以宁严勿宽：Claude Code 遇
YAML 解析失败会静默丢弃全部 frontmatter（description 退化为正文首行，自动触发失效），
而迁移前的 flow-style metadata 正是被 skills-ref 拒收的形态。

覆盖：
  1. frontmatter 语法子集 + 字段 ⊆ 规范六字段 + 各字段约束（含迁移前形态的回归用例）
  2. name 与安装目录名（包装器解析器里的 skills/<name>）一致
  3. 正文体量预算 + 加载期替换/执行标记禁令
  4. 长参考文档（>100 行）须有目录行
  5. evals/ 结构合法
  6. 全仓 Markdown 相对链接可达（迁移安全网，补 test_docs_paths 受检面之外的文档）
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dub_paths import skill_root

SKILL_ROOT = skill_root()
SKILL_MD = SKILL_ROOT / "SKILL.md"

#: 规范定义的全部顶层字段；其余键 skills-ref 与 claude.ai / API 上传均硬失败。
SPEC_FIELDS = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
#: 规范上限（skills-ref 常量 MAX_SKILL_NAME_LENGTH / MAX_DESCRIPTION_LENGTH /
#: MAX_COMPATIBILITY_LENGTH）。
NAME_MAX, DESCRIPTION_MAX, COMPATIBILITY_MAX = 64, 1024, 500
#: Anthropic 平台保留词（name 不得包含）。
RESERVED_WORDS = ("anthropic", "claude")
#: 规范建议正文 <500 行且 <5000 token。按汉字约 1.3 token/字、其余约 3.2 字符/token
#: 估算，当前中英比例下 8000 字符约 4.9k token，恰在建议线内。
BODY_MAX_CHARS, BODY_MAX_LINES = 8000, 500
#: Anthropic best practices：超过 100 行的参考文件须有目录（防只预览前若干行漏看）。
TOC_THRESHOLD_LINES = 100
TOC_PREFIX = "**目录**："

KEY_RE = re.compile(r"^([a-z][a-z0-9-]*):(?: (.*))?$")
SUBKEY_RE = re.compile(r"^  ([a-z][a-z0-9_-]*): (.*)$")
DQ_RE = re.compile(r'^"((?:[^"\\]|\\.)*)"$')
#: YAML 普通标量不得以这些指示符开头（否则被解析为序列/映射/锚点/块标量等）。
PLAIN_BAD_START = set("-?:,[]{}#&*!|>'\"%@`")
#: YAML 1.1 隐式类型字面量：PyYAML 读成布尔/空/数字，strictyaml 读成字符串——两边语义
#: 分叉，普通标量里一律拒收（需要时加双引号）。
IMPLICIT_TYPE_RE = re.compile(
    r"^(?:true|false|yes|no|on|off|null|~|[-+]?(?:\d[\d_]*)?\.?\d+(?:e[-+]?\d+)?)$",
    re.I,
)


def _scalar(raw: str, where: str) -> str:
    """单行标量：双引号串，或不含 YAML 结构记号的普通标量。"""
    if m := DQ_RE.match(raw):
        return m.group(1)
    assert raw, f"{where}: 值为空"
    assert raw[0] not in PLAIN_BAD_START, (
        f"{where}: 普通标量以 YAML 指示符 {raw[0]!r} 开头（flow style / 块标量 / "
        "锚点等不在允许子集内，需要时整体加双引号）"
    )
    assert ": " not in raw and " #" not in raw and not raw.endswith(":"), (
        f"{where}: 普通标量含 ASCII ': ' 或 ' #'（YAML 会误解析为映射或注释）"
    )
    assert not IMPLICIT_TYPE_RE.match(raw), (
        f"{where}: 普通标量 {raw!r} 是 YAML 隐式类型字面量（布尔/空/数字），须加双引号"
    )
    return raw


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """→ (字段, 正文)。不在允许子集内即 AssertionError（报错点名行号）。"""
    assert text.startswith("---\n"), "SKILL.md 须以 '---' 行开头"
    end = text.find("\n---\n", 4)
    assert end != -1, "frontmatter 缺闭合 '---' 行"
    block, body = text[4:end], text[end + 5 :]
    # skills-ref 以 content.split("---", 2) 切分，frontmatter 内任何 '---' 都会切错
    assert "---" not in block, "frontmatter 内出现 '---'（skills-ref 朴素切分会错位）"
    fields: dict = {}
    current_map: dict | None = None
    for no, line in enumerate(block.split("\n"), start=2):
        where = f"SKILL.md:{no}"
        assert "\t" not in line, f"{where}: frontmatter 含制表符"
        if not line.strip():
            continue
        if line.startswith(" "):
            m = SUBKEY_RE.match(line)
            assert m and current_map is not None, (
                f"{where}: 缩进行只允许作块式映射的两空格子键"
            )
            key, raw = m.groups()
            assert key not in current_map, f"{where}: 子键 {key!r} 重复"
            assert DQ_RE.match(raw), (
                f"{where}: metadata 值须加双引号（防 1.0 之类被别的解析器读成数字）"
            )
            current_map[key] = _scalar(raw, where)
            continue
        m = KEY_RE.match(line)
        assert m, f"{where}: 无法识别的 frontmatter 行"
        key, raw = m.groups()
        assert key not in fields, f"{where}: 键 {key!r} 重复"
        if raw is None:
            current_map = fields[key] = {}
        else:
            current_map = None
            fields[key] = _scalar(raw, where)
    return fields, body


@pytest.fixture(scope="module")
def skill() -> tuple[dict, str]:
    return parse_frontmatter(SKILL_MD.read_text(encoding="utf-8"))


# ── 1. frontmatter 语法子集与字段约束 ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("frontmatter", "needle"),
    [
        # 迁移前的真实形态：skills-ref 报 "disallowed JSONesque flow mapping"
        ('name: x\nmetadata: {version: "1.0.0"}', "指示符"),
        ("name: x\ndescription: a\nname: y", "重复"),
        ("name: x\ndescription: Use when: always", "': '"),
        ("name: x\ndescription: a # b", "' #'"),
        ("name: x\ndescription: >\n  folded", "指示符"),
        ("name: x\nmetadata:\n  version: 1.0", "双引号"),
        ("name: x\nmetadata:\n\tversion: 1.0", "制表符"),
        ("name: x\nlicense: 1.0", "隐式类型"),
        ("name: x\ndescription: yes", "隐式类型"),
    ],
)
def test_parser_rejects_yaml_outside_subset(frontmatter, needle):
    """解析器本身须对这些形态变红，否则上面的「宁严勿宽」只是纸面。"""
    with pytest.raises(AssertionError, match=re.escape(needle)):
        parse_frontmatter(f"---\n{frontmatter}\n---\nbody\n")


def test_frontmatter_fields_are_spec_only(skill):
    fields, _ = skill
    assert {"name", "description"} <= fields.keys(), "缺必填字段 name / description"
    extra = sorted(fields.keys() - SPEC_FIELDS)
    assert not extra, (
        f"frontmatter 出现规范外字段 {extra}：skills-ref 与 claude.ai / API 上传会硬失败；"
        "Claude Code 专有键会牺牲可移植性，引入前须显式决策"
    )


def test_name_follows_spec(skill):
    name = skill[0]["name"]
    assert isinstance(name, str) and len(name) <= NAME_MAX
    assert NAME_RE.match(name), f"name {name!r} 须为小写字母数字与单个连字符"
    assert not any(w in name for w in RESERVED_WORDS), f"name 含保留词：{name!r}"


def test_description_follows_spec(skill):
    desc = skill[0]["description"]
    assert isinstance(desc, str) and 1 <= len(desc) <= DESCRIPTION_MAX
    assert "<" not in desc and ">" not in desc, (
        "description 含尖括号：Anthropic API 拒收 XML 标签"
    )
    assert "Use when" in desc, "description 须写明触发时机（'Use when …' 子句）"


def test_optional_fields_follow_spec(skill):
    fields = skill[0]
    if "compatibility" in fields:
        assert isinstance(fields["compatibility"], str)
        assert 1 <= len(fields["compatibility"]) <= COMPATIBILITY_MAX
    if "metadata" in fields:
        meta = fields["metadata"]
        assert isinstance(meta, dict) and meta, "metadata 须为非空块式映射"
        assert all(isinstance(v, str) for v in meta.values())
    if "allowed-tools" in fields:
        tools = fields["allowed-tools"]
        assert isinstance(tools, str) and "," not in tools, (
            "allowed-tools 按规范为空格分隔"
        )


# ── 3. 正文体量与加载期标记 ──────────────────────────────────────────────────


def test_body_within_budget(skill):
    body = skill[1]
    assert len(body) <= BODY_MAX_CHARS, (
        f"SKILL.md 正文 {len(body)} 字符 > {BODY_MAX_CHARS}（约 5k token 建议线）："
        "把参考性内容下沉到 references/，SKILL.md 只留每次激活都需要的路由与不变量"
    )
    assert body.count("\n") < BODY_MAX_LINES


def test_body_has_no_load_time_markers(skill):
    """Claude Code 在加载 skill 时执行 `!`+反引号 与 ```! 块（失败即中止整个调用），
    并替换 $ARGUMENTS / $N——路由壳是纯文档，不得意外触发。"""
    body = skill[1]
    assert not re.search(r"(?:^|\s)!`", body), "正文含加载期执行标记 !`"
    assert not re.search(r"^```!", body, re.M), "正文含加载期执行块 ```!"
    assert not re.search(r"\$(?:ARGUMENTS\b|\d)", body), "正文含参数替换占位"


# ── 4. 长参考文档目录行 ──────────────────────────────────────────────────────


def _outside_fences(text: str) -> list[str]:
    out, fence = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if not fence:
            out.append(line)
    return out


def long_reference_docs() -> list[Path]:
    """Agent 按需加载的规格面：references/ 下全部文档。"""
    docs = sorted((SKILL_ROOT / "references").glob("*.md"))
    return [d for d in docs if d.read_text(encoding="utf-8").count("\n") > 100]


@pytest.mark.parametrize(
    "doc", long_reference_docs(), ids=lambda p: p.relative_to(SKILL_ROOT).as_posix()
)
def test_long_reference_docs_have_toc(doc):
    lines = _outside_fences(doc.read_text(encoding="utf-8"))
    first_h2 = next(i for i, ln in enumerate(lines) if ln.startswith("## "))
    toc = next((ln for ln in lines[:first_h2] if ln.startswith(TOC_PREFIX)), None)
    assert toc, (
        f"{doc.name} 超过 {TOC_THRESHOLD_LINES} 行却无目录：在首个 ## 之前加一行 "
        f"`{TOC_PREFIX}A · B · …`"
    )
    for ln in lines[first_h2:]:
        if ln.startswith("## "):
            short = re.split(r"[（(]", ln[3:], maxsplit=1)[0].strip()
            assert short in toc, f"{doc.name} 目录行缺章节「{short}」"


# ── 5. evals/ 结构 ───────────────────────────────────────────────────────────


def test_output_evals_are_well_formed(skill):
    data = json.loads((SKILL_ROOT / "evals" / "evals.json").read_text("utf-8"))
    assert data["skill_name"] == skill[0]["name"]
    cases = data["evals"]
    assert len(cases) >= 3, "Anthropic checklist：至少 3 个评测场景"
    ids = [c["id"] for c in cases]
    assert all(isinstance(i, int) for i in ids) and len(set(ids)) == len(ids)
    for c in cases:
        assert c["prompt"].strip() and c["expected_output"].strip()
        for f in c.get("files", []):
            assert (SKILL_ROOT / f).is_file(), f"eval {c['id']} 引用的输入不存在：{f}"
        if "assertions" in c:
            assert c["assertions"] and all(a.strip() for a in c["assertions"])


def test_trigger_evals_are_balanced():
    queries = json.loads(
        (SKILL_ROOT / "evals" / "trigger-evals.json").read_text("utf-8")
    )
    texts = [q["query"] for q in queries]
    assert all(t.strip() for t in texts) and len(set(texts)) == len(texts)
    assert all(isinstance(q["should_trigger"], bool) for q in queries)
    positives = sum(q["should_trigger"] for q in queries)
    assert positives >= 8 and len(queries) - positives >= 8, (
        "agentskills 指南：正例与近邻负例各 8–10 条"
    )


# ── 6. 全仓 Markdown 相对链接 ────────────────────────────────────────────────

MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
EXTERNAL_LINK_RE = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|#)")


def repo_markdown() -> list[Path]:
    """全仓 Markdown，排除隐藏目录（docs/.agents 除外）与 assets/——模板在实例化
    位置解析相对路径，不以 skill 仓为锚。"""
    out = []
    for p in sorted(SKILL_ROOT.rglob("*.md")):
        parts = p.relative_to(SKILL_ROOT).parts
        if parts[0] == "assets" or "node_modules" in parts:
            continue
        if any(s.startswith(".") and s != ".agents" for s in parts):
            continue
        out.append(p)
    return out


def test_repo_markdown_relative_links_resolve():
    broken = []
    for doc in repo_markdown():
        for line in _outside_fences(doc.read_text(encoding="utf-8")):
            for target in MD_LINK_RE.findall(line):
                path = target.split("#", 1)[0]
                if EXTERNAL_LINK_RE.match(target) or not path:
                    continue
                if not (doc.parent / path).exists():
                    rel = doc.relative_to(SKILL_ROOT).as_posix()
                    broken.append(f"{rel} → {target}")
    assert not broken, "相对链接指向不存在的文件：\n  " + "\n  ".join(broken)


def test_repo_markdown_free_of_merge_conflict_markers():
    """全仓 Markdown 不得残留 git 冲突标记（RSI-039 评审加固：冲突标记曾全绿入库）。

    只查行首 '<<<<<<<' / '>>>>>>>'（git 冲突形态），不查 '======='——setext 标题
    下划线会假阳性；代码围栏内亦不计（_outside_fences）。
    """
    offenders = []
    for doc in repo_markdown():
        for i, line in enumerate(_outside_fences(doc.read_text(encoding="utf-8")), 1):
            if line.startswith(("<<<<<<<", ">>>>>>>")):
                rel = doc.relative_to(SKILL_ROOT).as_posix()
                offenders.append(f"{rel}:{i}: {line[:50]}")
    assert not offenders, "Markdown 残留 git 冲突标记：\n  " + "\n  ".join(offenders)


# ── 7. 开篇钩子规范跨文档一致性（RSI-039） ────────────────────────────────────
