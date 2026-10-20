"""变量绑定的「算」这一半：现在它零产出。

**PLAN.md 九之一 ⑥。**GSS 真跑时，`oss bind` 对**五个不同构念给出了完全相同的六列**，
而且真正对的列（`paeduc` / `sei10` / `cohort` / `year`）一个都没出现。

机制：构念名是中文、列名与标签是英文 → 词元重合恒为 0 → 走兜底 →
兜底按 `(有没有标签, -缺失率)` 排序 → 20 列全有标签使第一个键成为常数 →
退化成纯按缺失率取前六。

**兜底名单跟「要绑的是哪个构念」完全无关，却仍然以「候选」的名义呈现。**
模块 docstring 说「规则给不出答案的时候，就该把问题交出去」——
但它没有交出去，而是交了一份**看起来像答案的噪声**。噪声比空白更糟。

两条修法：
① 词元重合为零时**不假装有候选**，如实说规则给不出来
② 加一张社科构念的中英对照表——这是「专为社科定制」最划算的一处
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from oss.core import binding as B
from oss.core.profiler import profile_table

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def gss_prof():
    """列名是英文缩写、没有标签——中文社科数据导出成 CSV 之后的常态。"""
    from tests.test_profiler import table

    frame = pd.read_csv(FIXTURES / "gss_like.csv")
    return profile_table(table(frame))


def names(sl) -> list[str]:
    return [c.column for c in sl.matches]


# ══════════════════════════════════════ 核心：不同构念要给出不同候选


def test_不同构念不能给出同一份名单(gss_prof) -> None:
    """**这是这条 bug 的判据。**

    「家庭背景」和「本人职业地位」是完全不同的两个东西，
    给出同一份候选说明规则根本没在看构念是什么。
    """
    a = names(B.shortlist("家庭背景（父亲教育年限）", gss_prof))
    b = names(B.shortlist("本人职业地位", gss_prof))
    assert a and b, "两个构念都该有候选"
    assert a != b, f"两个构念给出了同一份名单：{a}"


def test_中文构念能命中英文列名(gss_prof) -> None:
    """中文社科数据的常态：构念用中文说，列名是英文缩写。

    `paeduc` = 父亲受教育年限，`sei` = 社会经济地位指数。
    纯词法匹配对这种情况召回为零——**而这恰恰是最常见的情况**。
    """
    assert "paeduc" in names(B.shortlist("父亲的受教育年限", gss_prof))
    assert "sei" in names(B.shortlist("职业社会经济地位", gss_prof))
    assert "cohort" in names(B.shortlist("出生队列", gss_prof))
    assert "female" in names(B.shortlist("性别", gss_prof))


def test_命中的要排在前面(gss_prof) -> None:
    """给出候选不够，**排序要有意义**——排第一的该是最像的那个。"""
    assert names(B.shortlist("父亲的受教育年限", gss_prof))[0] == "paeduc"


def test_抽样权重不该出现在实质构念的候选里(gss_prof) -> None:
    """GSS 真跑时 `wtssps`（抽样权重）成了「本人职业地位」的候选。"""
    for c in ("本人职业地位", "家庭背景", "出生队列"):
        assert "wtssps" not in names(B.shortlist(c, gss_prof)), c


def test_英文构念也要能匹配缩写列名(gss_prof) -> None:
    """构念不一定是中文写的。"""
    assert "paeduc" in names(B.shortlist("father's education", gss_prof))
    assert "sei" in names(B.shortlist("occupational status", gss_prof))


# ══════════════════════════════════ 规则给不出来时，如实说


def test_规则给不出候选时不假装有(gss_prof) -> None:
    """**噪声比空白更糟。**

    一份跟构念无关的名单会诱导模型在里面选一个——
    而它本该看到「规则帮不上忙，你自己看全表」。
    """
    sl = B.shortlist("受访者对量子引力的看法", gss_prof)
    assert sl.rule_failed, "毫不相干的构念应当承认给不出候选"
    assert not sl.matches, "给不出就别给，不要拿缺失率最低的几列充数"
    assert "全表" in sl.note or "给不出" in sl.note, sl.note


def test_能命中时不算失败(gss_prof) -> None:
    sl = B.shortlist("父亲的受教育年限", gss_prof)
    assert not sl.rule_failed


def test_每个候选要说清为什么被选中(gss_prof) -> None:
    """「兜底」不是理由。使用者要能判断这个候选值不值得看。"""
    sl = B.shortlist("父亲的受教育年限", gss_prof)
    assert all(c.why for c in sl.matches), [(c.column, c.why) for c in sl.matches]


# ══════════════════════════════════ 词典本身的性质


def test_词典覆盖社科常见构念() -> None:
    """这张表是「专为社科定制」最划算的一处——几十行换回真正的召回。

    词典存的是**前缀片段**（`marit`），用来子串匹配真实列名（`marital_status`）。
    所以判据是「片段能不能匹配上目标列名」，不是「列名包不包含片段」。
    """
    for term, column in [
        ("户口", "hukou_urban"), ("收入", "hh_income"), ("婚姻", "marital_status"),
        ("民族", "ethnicity"), ("主观幸福感", "happiness"), ("互联网使用", "internet_use"),
        ("父亲的职业", "pasei10"), ("出生队列", "birth_cohort"),
    ]:
        hits = B.lexicon_fragments(term)
        assert any(h in column for h in hits), f"{term} 的片段 {hits} 匹配不上 {column}"


def test_词典不会把不相干的东西连起来() -> None:
    """过度匹配会让候选名单重新变成噪声。"""
    assert not (B.lexicon_fragments("教育") & B.lexicon_fragments("收入"))


def test_构念说本人时亲代变量要被压下去(gss_prof) -> None:
    """**这在地位获得研究里是要害。**

    拿 `pasei10`（父亲的社会经济地位）当「本人职业地位」，
    会变成用父亲的地位回归父亲的地位——一个接近 1 的系数和一篇废论文。
    而这种错**不会报任何错**：两列都存在、都是数字、量纲还一样。
    """
    ranked = names(B.shortlist("本人职业地位", gss_prof))
    assert ranked[0] in ("sei", "prestg10", "sei10"), ranked
    if "pasei10" in ranked:
        assert ranked.index("pasei10") > ranked.index(ranked[0])


def test_构念明说父亲时不要压亲代(gss_prof) -> None:
    """「家庭背景（父教育年限）」两个标记都有，不该被压。"""
    assert names(B.shortlist("家庭背景（父/母教育年限或父职业地位）", gss_prof))[0] == "paeduc"


# ─────────────────────────────── 数据文件自带的变量标签（2026-09-24）

def test_短标签能当行名_整句问题不能() -> None:
    """调查数据的变量标签过半是整句问题。**把它截断塞进纵轴，读者看到的是半句话**；
    留着列名，读者至少知道那是个代号、该去查码本——半句话比代号更误导。"""
    from oss.core.binding import usable_variable_label as u

    assert u("性别") == "性别"
    assert u("农业户口") == "农业户口"
    assert u("Respondent sex") == "Respondent sex"
    assert u("您的民族是：") == ""                    # 冒号结尾是问题
    assert u("[年]  您的出生日期是什么？") == ""       # 问号 + 方括号
    assert u("采访地点-省/自治区/直辖市编码") == ""     # 太长
    assert u("") == ""


def test_标签按显示宽度卡_汉字算两格() -> None:
    """按字符数卡的话，15 个汉字（约 30 格）会过关，而它当轴标签太长了。"""
    from oss.core.binding import usable_variable_label as u

    assert u("一" * 12) == "一" * 12            # 24 格，刚好
    assert u("一" * 13) == ""                   # 26 格，超了
    assert u("a" * 24) == "a" * 24              # 西文 24 格
    assert u("a" * 25) == ""


def test_文件标签排最低_模型写的压过它() -> None:
    """前两个来源都要模型填，第三个不用。**所以它是兜底不是权威**——
    模型写的名字是冲着读者去的，文件里的标签只是恰好可读。"""
    from oss.core.binding import display_labels

    got = display_labels(None, {"a2": "受访者性别"}, {"a2": "性别", "a62": "家庭总收入"})
    assert got["a2"] == "受访者性别", "计划里的短名该压过文件标签"
    assert got["a62"] == "家庭总收入", "计划没写的该由文件标签兜住"


def test_文件标签当不了行名时不进映射() -> None:
    """**留空比填半句话好**：映射里没有这一列，图上印的就是列名。"""
    from oss.core.binding import display_labels

    assert display_labels(None, {}, {"a31": "[年]  您的出生日期是什么？"}) == {}
