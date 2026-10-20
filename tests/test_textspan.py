"""切句与取节：全仓库共用的那两件小事。"""

from __future__ import annotations

from pathlib import Path

from oss.core import textspan

#: 仓库根。扫源码用——`re.compile` 自己带缓存，同一个式子写两遍拿到的是同一个对象，
#: 用 `is` 比对照不出「有没有抄第二份」。
#: 扫 `oss/` 与 `evals/` 两处，跟 `test_no_duplicate_rules` 同一个范围：判分器抄一份也算抄。
_ROOT = Path(__file__).resolve().parents[1]
_SCAN = ("oss", "evals")

# ─────────────────────────── 切句


def test_切句不把_et_al_的句点当句末():
    out = textspan.sentences("Smith et al. (2020) find X. Next sentence.")
    assert len(out) == 2


def test_句末标点表全仓库只有一份():
    """照抄一份长得一样的，下次改判据又要漏掉一处——只许 `textspan` 里有这一份。

    **唯一的例外是裸跑对照的措辞快照**（`evals/barerun/wording_snapshot.py`）。
    那一份是**故意冻住的**：裸跑对照的判分器不许依赖被测系统（读了 OSS 的产物就是拿自家的
    尺子量别人），词表一改历史结果也不可复现，所以它连这张表一起抄了过去、记了来源提交号。
    这条扫描要防的「改了判据漏掉一处」，在那边由 `tests/test_barerun_score.py` 里
    「快照的词表与来源一字不差」「快照连去依赖的那两块也照抄」两条管着——上游一改那两条就红，
    提醒人去判断这次要不要跟进。**别的地方照样只许有一份。**
    """
    mark = "(?<!" + chr(92) + "bet al)"
    hits = sorted(p.relative_to(_ROOT).as_posix() for base in _SCAN
                  for p in (_ROOT / base).rglob("*.py")
                  if mark in p.read_text(encoding="utf-8"))
    # evals/ 不随仓库分发，那份快照在公开检出里本就不在——它是允许存在的例外，不是必须存在
    allowed = {"evals/barerun/wording_snapshot.py", "oss/core/textspan.py"}
    assert set(hits) <= allowed and "oss/core/textspan.py" in hits, hits


def test_切句的起止能切回原文():
    text = "第一句。第二句。"
    out = textspan.sentences(text)
    assert [(a, b) for a, b, _ in out] == [(0, 4), (4, 8)]
    assert all(text[a:b] == s for a, b, s in out)


def test_换行与分号也算边界而只有空白的不成句():
    assert [s for _, _, s in textspan.sentences("甲；乙\n\n丙")] == ["甲；", "乙\n", "丙"]


def test_没有句末标点的整段也是一句():
    assert [s for _, _, s in textspan.sentences("没有句号的一段话")] == ["没有句号的一段话"]


# ─────────────────────────── 取节


def test_取节认中文也认英文也剥编号前缀():
    assert "样本量偏小" in textspan.section_body("## 三、研究局限\n样本量偏小。", "limitations")
    assert "small" in textspan.section_body("## Limitations\nsmall sample.", "limitations")


def test_取节命中第一个给定的节名():
    t = "## 讨论\nD\n\n## 局限\nL\n"
    assert textspan.section_body(t, "limitations", "discussion").strip() == "L"


def test_取节取不到给空串():
    assert textspan.section_body("## 讨论\nD\n", "limitations") == ""
    assert textspan.section_body("## 讨论\nD\n") == ""


def test_取节吃得下_PDF_抽出来的纯文本():
    """范文是 PDF 抽出来的，标题行没有 `##` 记号——这是四份取节里只有这一份认得的。"""
    assert "small" in textspan.section_body("Limitations\nsmall sample.\n", "limitations")


def test_合并标题落在讨论那一节_局限退到讨论取得到():
    """「讨论与局限」这类合并标题：节名表是 `^` 锚定的，它归到 discussion，不归到 limitations。

    实施计划 Task 7 担心的是它「两边都取不到、检查整个失灵」——**并没有**：
    `section_body(text, "limitations", "discussion")` 退到 discussion 就取到了正文，
    所以局限那几条检查换接之前不必放宽节名表。这三条把这个行为钉住。
    """
    for head in ("## 讨论与局限", "## 结论与局限", "## Discussion and Limitations"):
        t = f"{head}\n正文一句。\n"
        assert "limitations" not in textspan.section_spans(t), head
        assert "正文一句" in textspan.section_body(t, "limitations", "discussion"), head


def test_子小节下的节一直吃到下一个大节():
    """跟 `manuscript._section_body` 的差别之一：旧版切到下一个 `###` 就停，这一份吃到下一个认得的节。

    成稿里 `### 局限` 后面常跟着 `### 下一步`，两者说的是同一件事的两面，宽一点只会更宽容。
    """
    t = "## 讨论\nD\n\n### 局限\nL\n\n### 下一步\nN\n\n## 参考文献\nR\n"
    body = textspan.section_body(t, "limitations")
    assert "L" in body and "N" in body and "R" not in body


def test_各节的起止首尾相接():
    t = "## 摘要\nA\n\n## 引言\nB\n"
    spans = textspan.section_spans(t)
    assert list(spans) == ["abstract", "introduction"]
    assert spans["abstract"][1] == spans["introduction"][0]
