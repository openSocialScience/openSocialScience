"""阶段 2 的说明书布局：短路由器 + manifest + 四栏契约 + 规则 + 按需散文。

事故（2026-08-28）：740 行的说明书里，"审计 FAIL、替代设计穷尽之后"这一格是空的，
两个模型把它填出了相反的答案。**散文留白，四栏不留白**——
输入、产出、完成条件、禁止四栏都得写满，写不满就说明这一环没想清楚。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1] / ".claude" / "skills"
RESEARCH = ROOT / "social-research"
STAGES = ("1-profile", "2-literature", "3-ideas", "4-feasibility", "5-plan",
          "6-run", "7-paper", "7.5-identification", "8-review", "9-wrapup")
FOUR = ("## 输入", "## 产出", "## 完成条件", "## 禁止")


def test_路由器不超过一百行():
    n = len((RESEARCH / "SKILL.md").read_text(encoding="utf-8").splitlines())
    assert n <= 100, f"路由器 {n} 行——超过 100 行就不是路由器了"


def test_路由器保留宿主兼容的三样():
    t = (RESEARCH / "SKILL.md").read_text(encoding="utf-8")
    assert "$ARGUMENTS" in t
    assert "宿主不替换占位符" in t, "dsh 不做替换，这句注释不能丢"
    m = re.match(r"\A---\n(.*?)\n---\n", t, re.S)
    assert m, "frontmatter 不是以 --- 开头的一段"
    front = m.group(1)          # 不能按 "---" 切：argument-hint 自己就含 ---
    for k in ("name:", "description:", "argument-hint:", "allowed-tools:"):
        assert k in front, f"frontmatter 缺 {k}"


def test_每个阶段都有一份四栏契约():
    for s in STAGES:
        p = RESEARCH / "stages" / f"{s}.md"
        assert p.exists(), f"缺 {p}"
        t = p.read_text(encoding="utf-8")
        for h in FOUR:
            assert any(l.startswith(h) for l in t.splitlines()), f"{s} 缺「{h}」"


def test_五份规则都在():
    for r in ("iron-laws", "input-and-rundir", "stop-conditions", "ask-user", "explore"):
        assert (RESEARCH / "rules" / f"{r}.md").exists(), f"缺 rules/{r}.md"


def test_散文有地方放():
    """.notes.md 是"没删掉"的证据。9 个环节有散文要挪（9-wrapup 太短，不单独立）。"""
    got = sorted(p.stem.replace(".notes", "") for p in (RESEARCH / "stages").glob("*.notes.md"))
    assert got == sorted(STAGES[:-1]), got


@pytest.mark.parametrize("skill", ["social-research", "social-paper", "social-idea",
                                   "social-review", "social-identification"])
def test_每份技能都有manifest(skill):
    assert (ROOT / skill / "manifest.yaml").exists(), f"{skill} 缺 manifest.yaml"


@pytest.mark.parametrize("skill", ["social-paper", "social-idea", "social-review"])
def test_超过一百五十行的技能拆过了(skill):
    d = ROOT / skill
    n = len((d / "SKILL.md").read_text(encoding="utf-8").splitlines())
    assert n <= 100, f"{skill}/SKILL.md {n} 行"
    assert (d / "contract.md").exists(), f"{skill} 缺 contract.md"
    t = (d / "contract.md").read_text(encoding="utf-8")
    for h in FOUR:
        assert any(l.startswith(h) for l in t.splitlines()), f"{skill}/contract.md 缺「{h}」"


def test_没过阈值的技能不拆():
    """不拆是**写下来的决定**，不是「行数还没到」。

    原来钉的是 `SKILL.md < 150 行` 和「没有 contract.md」——两条都在量副产品。
    正文多写两段就变红，而变红说明不了任何事（真该拆了就拆，不该是测试挡着）；
    反过来，把 contract.md 删掉、内容照样分散在别处，这两条也照样绿。
    钉 manifest：`always_load` 只有 SKILL.md、没有分层轴 = 这份技能声明了自己不分层。
    """
    d = ROOT / "social-identification"
    m = yaml.safe_load((d / "manifest.yaml").read_text(encoding="utf-8"))
    assert m["always_load"] == ["SKILL.md"], m.get("always_load")
    # `axes: {}` 是把「不分层」显式写下来，跟压根没这个键等价——两种写法都算没轴。
    assert not m.get("axes"), f"声明了分层轴就不叫不拆了：{m.get('axes')}"
