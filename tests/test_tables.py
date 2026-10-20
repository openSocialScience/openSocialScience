"""论文 / 过程分离与三张表。评审对第十次验收跑的规范性意见，逐条变成机械检查：

- 「所有系数用散文报出来，没有描述统计表、没有回归表——任何量化论文这都是硬伤」
  → `{{table:descriptives}}` `{{table:main}}` `{{table:robustness}}` 从结果文件排表
- 「把研究过程当成研究内容写进论文：the automated diagnostic、an independent design audit、
  a verdict of failure、R1/R3 specification——没有任何期刊接受这个」
  → 过程词黑名单，校验拒绝
- 「图全是占位符：正文里裸露的 coef、marginal、event、robustness」
  → 根源是 pandoc 把图片替代文字排成题注；替代文字改成图注本身
- 「摘要没有任何具体数字」→ 警告
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from oss.core.contracts import (
    AnalysisResults, Descriptive, Estimate, EventStudyPoint, RobustnessRun, SpecSummary,
)
from oss.core.manuscript import (
    check, fold_captions, process_language, render, system_names, table_markdown,
)

FIXTURES = Path(__file__).parent / "fixtures"
ARCHIVE = Path(__file__).parent.parent / "docs" / "runs"


def results(**kw) -> AnalysisResults:
    base = dict(
        plan_id="p1",
        estimates={
            "main": Estimate(term="dem*internet10", coef=-0.1563, se=0.1004, p=0.121),
            "dem": Estimate(term="dem", coef=0.238, se=0.31, p=0.44),
        },
        n=2545,
        spec=SpecSummary(estimator="ols_fe", outcome="lifexp", treatment="dem*internet10",
                         controls=["dem", "internet10"], fixed_effects=["country", "year"],
                         cluster_level="country", sample_filter="year >= 1990"),
        descriptives=[Descriptive(name="lifexp", n=2545, mean=68.123, sd=9.5, min=30.0, max=84.2),
                      Descriptive(name="dem", n=2545, mean=0.6, sd=0.49, min=0, max=1)],
        robustness={
            "R1 Outcome: under-5 mortality": RobustnessRun(
                label="R1 Outcome: under-5 mortality", n=2400,
                estimates={"main": Estimate(term="dem*internet10", coef=0.02, se=0.01, p=0.03)}),
            "R2 Cluster by year": RobustnessRun(label="R2 Cluster by year", error="跑不动"),
        },
    )
    return AnalysisResults(**{**base, **kw})


def en_paper(body: str = "", tables: bool = True) -> str:
    tab = "*Table 2. Main estimates.*\n{{table:main}}\n" if tables else ""
    return f"""# Title

## Abstract
The interaction is {{{{result:main.coef}}}} (SE {{{{result:main.se}}}}), n = {{{{result:main.n}}}}.

## Introduction
Background.

## Data and Methods
Sample size {{{{result:main.n}}}}.

## Results
{tab}
The main estimate is {{{{result:main.coef}}}}, p {{{{result:main.p}}}}.
{body}

## Discussion
Limitations.
"""


# ══════════════════════════════ 一、三张表从结果文件排出来


def test_主结果表带星号_样本量_固定效应和表注() -> None:
    body, why = table_markdown("main", results(), en=True)
    assert body and not why, why
    # 英文系数表按社会学通例：标准误在下一行括号里、不设 p 列（Social Forces 范文）
    assert "| dem\\*internet10 | -0.156 |" in body, body
    assert "|  | (0.100) |" in body, "标准误在系数下一行的括号里"
    assert "| SE |" not in body and "| p |" not in body, body.splitlines()[0]
    assert "| N | 2545 |" in body
    assert "Fixed effects | country, year" in body
    assert "OLS with fixed effects" in body and "clustered by country" in body
    assert "Sample restriction: `year >= 1990`" in body
    assert "\\* p < .05, \\*\\* p < .01, \\*\\*\\* p < .001." in body, "英文档位没有 0.10 那一档"
    assert "*Note:*" in body and "*Notes:*" not in body, "范文里不论几句都是单数"


def test_稳健性表包含主规格和跑不动的那项() -> None:
    body, why = table_markdown("robustness", results(), en=True)
    assert body, why
    assert "| Main specification | -0.156 | 0.281 | 2545 |" in body, body
    assert "|  | (0.100) |" in body, "标准误在系数下一行的括号里"
    assert "| R1 Outcome: under-5 mortality | 0.020* | 0.028 | 2400 |" in body, \
        "p = 0.030 在英文档位（.05/.01/.001）下是一颗星，不是两颗"
    assert "R2 Cluster by year | — (not estimated)" in body, "跑不动的也要在表里，读者才分得出「没做」和「做了不好看」"
    assert "|  |  |  |  |" not in body, "跑不动的那一行没有标准误，不该补一行空括号"


def test_描述统计表按估计样本() -> None:
    body, why = table_markdown("descriptives", results(), en=True)
    assert body, why
    assert "| lifexp | 2545 | 68.123 | 9.500 | 30 | 84.200 |" in body, body
    assert "Estimation sample" in body


def test_老结果文件没有描述统计时要说重跑() -> None:
    body, why = table_markdown("descriptives", results(descriptives=[]), en=True)
    assert body is None and "重跑 oss run" in why


def test_按批效应表和事件研究表() -> None:
    res = results(estimates={"main": Estimate(term="ATT", coef=3.5, se=0.2, p=0.0),
                             "cohort_2006": Estimate(term="ATT, cohort 2006", coef=4.48, se=0.26, p=0.0),
                             "cohort_2012": Estimate(term="ATT, cohort 2012", coef=1.91, se=0.23, p=0.0)},
                  event_study=[EventStudyPoint(k=-2, coef=-0.1, se=0.24, n=100),
                               EventStudyPoint(k=0, coef=1.5, se=0.23, n=100)])
    body, _ = table_markdown("cohorts", res, en=True)
    assert "| 2006 | 4.480*** |" in body and "|  | (0.260) |" in body, body
    body, _ = table_markdown("event", res, en=True)
    assert "| -2 | -0.100 | 100 |" in body and "|  | (0.240) |" in body, body
    assert "| +0 | 1.500*** |" in body, body
    assert table_markdown("cohorts", results(), en=True)[0] is None, "没有按批效应就不该排出空表"


def test_英文稿的表里有中文要拒绝并指出那一格() -> None:
    res = results(robustness={"R1 换结果变量": RobustnessRun(
        label="R1 换结果变量", n=1, estimates={"main": Estimate(term="x", coef=1, se=1, p=0.5)})})
    body, why = table_markdown("robustness", res, en=True)
    assert body is None and "R1 换结果变量" in why and "plan.json" in why, why
    body, why = table_markdown("robustness", res, en=False)
    assert body and "主规格" in body, "中文稿照常"


def test_不存在的表名要列出可用的() -> None:
    body, why = table_markdown("appendix", results(), en=True)
    assert body is None and "descriptives" in why and "main" in why


def test_表占位符渲染成前后带空行的表() -> None:
    out = render(en_paper(), results())
    assert "\n\n| Variable | Estimate |\n" in out, out[:600]
    assert "{{table" not in out


def test_给人看的渲染稿里表题还在表格上方() -> None:
    """换位置只在导出模式下做——读 markdown 时表题在上方更顺。"""
    out = render(en_paper(), results())
    assert "*Table 2. Main estimates.*" in out
    assert "\n: Table 2." not in out


def test_导出模式下表题落到pandoc题注语法_排在表注之前() -> None:
    """原先表题是一行斜体正文，Word 里落成带首行缩进的普通段落；
    pandoc 的 `: 表题` 会落到 Table Caption 样式并自动排到表格上方。"""
    out = render(en_paper(), results(), cite_mode="pandoc")
    assert "*Table 2. Main estimates.*" not in out, "表题那一行被摘走了"
    assert "\n: Table 2. Main estimates.\n" in out, out[out.index("| Variable"):][:700]
    body = out[out.index("| Variable"):]
    assert body.index(": Table 2.") < body.index("*Note:*"), "题注在表注之前"


def test_导出模式下表注包进自己的样式档() -> None:
    out = render(en_paper(), results(), cite_mode="pandoc")
    note = out.index("*Note:*")
    assert out[:note].rstrip().endswith('::: {custom-style="Table Note"}'), out[note - 200:note + 80]
    assert ":::" in out[note:]


def test_导出模式下摘要与关键词各挂一档() -> None:
    src = en_paper().replace("n = {{result:main.n}}.\n",
                             "n = {{result:main.n}}.\n\n**Keywords:** growth; panel\n")
    out = render(src, results(), cite_mode="pandoc")
    assert '::: {custom-style="Abstract"}' in out
    assert '::: {custom-style="Keywords"}' in out
    assert out.index('custom-style="Abstract"') < out.index('custom-style="Keywords"')


# ══════════════════════════════ 二、过程词：审计 / 审稿 / 工具不属于正文


def test_验收跑的论文被过程词检查抓到评审点名的每一处() -> None:
    """**用真的论文回验。**评审点名：automated diagnostic、design audit、verdict、gray band、R1/R3。"""
    src = ARCHIVE / "r20260825_qog_internet-paper.md"
    if not src.exists():
        pytest.skip("归档不在")
    hits = process_language(src.read_text(encoding="utf-8"))
    joined = " ".join(hits)
    assert len(hits) >= 10, hits
    for word in ("audit", "automated", "verdict", "gray band", "R3", "for the record", "the analysis attach"):
        assert word in joined, word


def test_过程词是拒绝不是警告() -> None:
    problems, _ = check(en_paper("The independent design audit returned a verdict of failure."), results())
    assert any("研究过程的痕迹" in p and "audit" in p for p in problems), problems


def test_表格行里的规格代号不算() -> None:
    """稳健性标签 R1/R2 在表里是正当的，在散文里不是。"""
    problems, _ = check(en_paper("| R1 no controls | 1.0 |\n"), results())
    assert not any("研究过程" in p for p in problems), problems
    problems, _ = check(en_paper("The R1 specification changes the outcome."), results())
    assert any("R1" in p for p in problems)


def test_正常学术用语不被误伤() -> None:
    text = ("We audit nothing. " if False else "") + (
        "Skills acquired in school raise wages. The pipeline of graduates matters. "
        "A diagnostic test of parallel trends is reported in Table 3.")
    hits = process_language(en_paper(text))
    # "pipeline" 是过程词，这里故意留一处；skills / diagnostic test 不能被抓
    assert all("Skills" not in h and "diagnostic test" not in h for h in hits), hits


def test_中文稿也查过程词() -> None:
    text = "# 标题\n\n## 摘要\n效应 {{result:main.coef}}。\n\n## 引言\n审计报告指出两处错误。\n\n## 数据与方法\n\n## 结果\n\n## 讨论\n"
    problems, _ = check(text, results())
    assert any("审计" in p for p in problems), problems


# ── 两路信号（Task 9）：系统专名从真名现取、修改史句式；手抄的名字表退场


def test_系统专名从真名生成而不是手抄() -> None:
    """环节名、`oss` 子命令、技能名都在说明书与编号表里，生成的表自动跟着走。"""
    names = system_names()
    assert "oss run" in names and "oss paper check" in names          # 编号表里的命令串
    assert "1-profile" in names and "9-wrapup" in names               # stages/ 里的环节名
    assert "social-research" in names and "social-paper" in names     # 技能名 = 角色名
    assert any(n.startswith("环节 ") for n in names), names


def test_生成的专名表里没有会撞的普通词() -> None:
    """「说明书」那次误报的根子：手抄的名字表里混进了普通名词。

    生成的表只收真名（`1-profile`、`oss run`、`social-paper`），撞不上正文里的普通词。
    """
    assert all(len(n) < 2 or not n.isalpha() or "-" in n or " " in n or n.startswith("环节")
               for n in system_names()), system_names()
    assert "说明书" not in system_names()


def test_问卷说明书是正当的正文内容() -> None:
    """真实论文正文里的「问卷说明书」被当成技能说明书拦下——注入测试丁组唯一的误报。"""
    assert process_language("按问卷说明书，子女数包括已经去世的子女。") == []


@pytest.mark.parametrize("text", [
    "我们先试了双重差分，再改成双向固定效应。",
    "分析计划最初声明的分组为三等分，后来改用五等分重新估计。",
])
def test_修改史句式被拦(text: str) -> None:
    assert process_language(text), text


@pytest.mark.parametrize("text", [
    "各国最初把义务教育定为六年，后来改为九年。",
    "多数省份起初把起征点定在三千元，随后改成五千元。",
])
def test_政策自己的修改史不是过程话(text: str) -> None:
    assert process_language(text) == [], text


@pytest.mark.parametrize("text", [
    "核心变量由分析程序在运行时现场构造，未在数据中预先算好。",
    "工具给出的相似候选经逐个核对后没有采用。",
    "结果文件显示这一项稳健性报错，查不出具体是哪个变量。",
    "在选题阶段的评审中，这一点被明确指出过。",
])
def test_工具与评审做了什么都是过程话(text: str) -> None:
    assert process_language(text), text


def test_已知的漏拦_判词不收单字() -> None:
    """英文表里 `verdict` 是裸词，中文这边**故意不照搬**。

    `social-paper/reference/read-first.md` 明写着功效不敏感时要在局限里补一句
    「判词对折价系数与灰区上下界均不敏感」——裸收就把说明书自己教的话拦了
    （`tests/test_claim_check.py` 的「合规的稿子」用的正是这一句）。
    注入测试里这一句因此仍然漏过，**照实留着**。
    """
    assert process_language("功效判词本身是脆弱的：换一个折价系数，判词就会翻转。") == []


@pytest.mark.parametrize("text", [
    "工具变量给出的估计比最小二乘大一倍。",
    "本文按程序性正义的三个维度构造指标。",
    "医疗系统的数据显示两组的差距在缩小。",
    "本文不足以裁决替代论与互补论之争。",
])
def test_正当的中文说法不被两路信号误伤(text: str) -> None:
    assert process_language(text) == [], text


def test_专名表只生成一次() -> None:
    assert system_names() is system_names()


# ══════════════════════════════ 三、图注在下、表注在上；摘要要有数字；成品要有表


def test_图没有图注要拒绝_有就放行() -> None:
    problems, _ = check(en_paper("{{fig:coef}}\n\nSome prose."), results(), figures={"coef": "en"})
    assert any("没有图注" in p for p in problems), problems
    problems, _ = check(en_paper("{{fig:coef}}\n\n*Figure 1. Main estimates.*"), results(), figures={"coef": "en"})
    assert not any("图注" in p for p in problems), problems


def test_表没有表注要拒绝() -> None:
    problems, _ = check(en_paper(tables=False) + "\n{{table:robustness}}\n", results())
    assert any("没有表注" in p for p in problems), problems


def test_摘要没有数字只警告() -> None:
    text = en_paper().replace(
        "The interaction is {{result:main.coef}} (SE {{result:main.se}}), n = {{result:main.n}}.",
        "We find a significant effect.")
    problems, warnings = check(text, results())
    assert not any("摘要" in p for p in problems)
    assert any("摘要没有任何具体数字" in w for w in warnings), warnings


def test_没有主结果表要警告() -> None:
    _, warnings = check(en_paper(tables=False), results())
    assert any("{{table:main}}" in w for w in warnings), warnings
    _, warnings = check(en_paper(), results())
    assert not any("{{table:main}}" in w for w in warnings)


# ══════════════════════════════ 四、图片替代文字 = 图注（Word 里不再出现裸露的 coef）


def test_渲染时图片替代文字是图注() -> None:
    out = render(en_paper("{{fig:coef}}\n\n*Figure 1. Main estimates with 95% CIs.*"),
                 results(), figures={"coef": "en"})
    assert "![Figure 1. Main estimates with 95% CIs.](fig_coef.png)" in out, out
    assert "*Figure 1. Main estimates with 95% CIs.*" in out, "给人看的版本图注行留着"


def test_导出模式把图注行并进替代文字() -> None:
    out = render(en_paper("{{fig:coef}}\n\n*Figure 1. Main estimates.*"),
                 results(), figures={"coef": "en"}, fig_captions="alt")
    assert "![Figure 1. Main estimates.](fig_coef.png)" in out
    assert "*Figure 1. Main estimates.*" not in out, "Word 里题注只能出现一次"


def test_文本版导出折叠图注行() -> None:
    text = "![Figure 1. X.](fig_coef.png)\n\n*Figure 1. X.*\n\nNext paragraph."
    assert fold_captions(text) == "![Figure 1. X.](fig_coef.png)\n\nNext paragraph."


# ══════════════════════════════ 五、oss run 填描述统计与规格摘要


def test_run_plan附带估计样本的描述统计和规格摘要() -> None:
    from oss.core.analysis import run_plan
    from oss.core.contracts import AnalysisPlan

    df = pd.read_csv(FIXTURES / "did_clean.csv")
    plan = AnalysisPlan(plan_id="p", idea_id="i", estimator="ols_fe", outcome="y", treatment="post",
                        controls=["x"], fixed_effects=["unit", "year"], cluster_level="unit",
                        sample_filter="year >= 2008")
    res = run_plan(df, plan)
    names = [d.name for d in res.descriptives]
    assert names == ["y", "post", "x"], names
    assert all(d.n == len(df[df.year >= 2008]) for d in res.descriptives), "描述统计要按筛选后的估计样本算"
    assert res.spec and res.spec.fixed_effects == ["unit", "year"] and res.spec.cluster_level == "unit"
    assert res.spec.sample_filter == "year >= 2008"
    back = AnalysisResults.model_validate_json(res.model_dump_json())
    assert back.spec == res.spec and len(back.descriptives) == 3


def test_交互项拆成分量进描述统计() -> None:
    from oss.core.analysis import run_plan
    from oss.core.contracts import AnalysisPlan

    df = pd.read_csv(FIXTURES / "did_clean.csv")
    plan = AnalysisPlan(plan_id="p", idea_id="i", estimator="ols_fe", outcome="y", treatment="post*x",
                        controls=["post", "x"], interactions=["post*x"], fixed_effects=["unit", "year"],
                        cluster_level="unit")
    res = run_plan(df, plan)
    assert [d.name for d in res.descriptives] == ["y", "post", "x"]


def test_分批估计量的规格摘要用单位和时间当固定效应() -> None:
    pytest.importorskip("statspai")
    from oss.core.analysis import run_plan
    from oss.core.contracts import AnalysisPlan

    df = pd.read_csv(FIXTURES / "did_staggered.csv")
    plan = AnalysisPlan(plan_id="p", idea_id="i", estimator="did_cs", outcome="y", unit="unit",
                        time="year", treat_time="treat_time", cluster_level="unit")
    res = run_plan(df, plan)
    assert res.spec.fixed_effects == ["unit", "year"]
    body, why = table_markdown("main", res, en=True)
    assert body and "Callaway" in body and "| ATT | 3.5" in body, why or body
    body, _ = table_markdown("cohorts", res, en=True)
    assert "| 2006 |" in body and "| 2012 |" in body


def _res_mixed_outcome():
    """稳健性里混着「换了结局」的项——验收跑 r20260907_edufert_zh 的真实形状。"""
    return AnalysisResults(
        plan_id="p", n=4450,
        spec=SpecSummary(estimator="did_cs", outcome="wdi_fertility", cluster_level="国家"),
        estimates={"main": Estimate(term="处理", coef=0.020, se=0.070, p=0.775)},
        robustness={
            "结局换为中学毛入学率": RobustnessRun(
                label="结局换为中学毛入学率", n=3114, outcome="wdi_gers",
                estimates={"main": Estimate(term="处理", coef=1.171, se=2.780, p=0.674)}),
            "加入随时间变化的控制变量": RobustnessRun(
                label="加入随时间变化的控制变量", n=3908,
                estimates={"main": Estimate(term="处理", coef=-0.097, se=0.110, p=0.377)}),
        },
    )


def test_稳健性表要标出哪几项换了结局() -> None:
    """主规格 0.020 与「中学毛入学率 1.171」并排，读者会以为估计爆了——
    其实是换了单位。**同一列里放不同单位的数，表本身就在误导。**"""
    body, why = table_markdown("robustness", _res_mixed_outcome(), en=False)
    assert body, why
    assert "结局" in body.splitlines()[0], f"要有结局这一列：{body.splitlines()[0]}"
    assert "wdi_gers" in body, body
    assert "单位" in body and "不可直接比较" in body, "表注要说清为什么不能直接比"


def test_没有换结局时稳健性表不多加一列() -> None:
    """常见情况不该被这条改动搞复杂——一列都不许多。"""
    body, why = table_markdown("robustness", results(), en=True)
    assert body, why
    assert "Outcome" not in body.splitlines()[0], body.splitlines()[0]
    assert "| Main specification | -0.156 | 0.281 | 2545 |" in body


def test_稳健性表要报每一项自己的最小可测效应() -> None:
    """三位审稿人独立抓到同一件事：主结果反复交代「能分辨多小的差异」，
    稳健性只给系数和 p——**读者无从判断那个「零」有多少只是分辨力不够**。

    验收跑 r20260907_edufert_zh 里，主规格能分辨 0.196，而三项换结局的稳健性
    分别是 4.44、8.08、7.79——宽了几十倍。不报出来，「五项检验都不显著」
    读起来像五重确认，其实是四次「测不出」。
    """
    body, why = table_markdown("robustness", results(), en=True)
    assert body, why
    head = body.splitlines()[0]
    assert "MDE" in head, head
    # R1 的 se = 0.010 → MDE = 2.80 × 0.010 = 0.028
    assert "0.028" in body, body
    assert "smallest difference" in body.lower() or "最小" in body, "表注要解释 MDE 是什么"


def test_中文稳健性表的MDE列也在() -> None:
    body, _ = table_markdown("robustness", results(), en=False)
    assert "MDE" in body.splitlines()[0], body.splitlines()[0]
    assert "最小" in body, "中文表注要解释"


# ══════════════════════════════ 六、表和图里印人话，不印列名


LABELS = {"dem*internet10": "民主 × 互联网普及", "dem": "民主", "internet10": "互联网普及",
          "lifexp": "预期寿命", "country": "国家", "year": "年份"}


def test_标签解析_交互项类别哑变量与查不到的情形() -> None:
    from oss.core.manuscript import term_label

    L = {"cls_mgr": "管理与专业阶级", "decade": "调查年份", "edu_long": "受教育年限"}
    assert term_label("cls_mgr", L) == "管理与专业阶级"
    assert term_label("cls_mgr*decade", L) == "管理与专业阶级 × 调查年份"
    assert term_label("edu_long=1", L) == "受教育年限=1", "类别哑变量只换列名那一半"
    assert term_label("age2", L) == "age2", "查不到就原样——猜错了比印列名更糟"
    assert term_label("cls_mgr*decade", None) == "cls_mgr*decade", "没有标签表就什么都不做"


def test_标签来源_plan压过binding() -> None:
    from oss.core.binding import display_labels
    from oss.core.contracts import BindingReport, VariableBinding

    report = BindingReport(bindings=[
        VariableBinding(construct_name="过劳（周工时≥48 小时）", role="outcome",
                        column="overwork", status="derived"),
        VariableBinding(construct_name="性别", role="control", column="female", status="derived"),
    ])
    got = display_labels(report, {"overwork": "过劳", "cls_mgr": "管理与专业阶级"})
    assert got["female"] == "性别", "binding 里有的自动取，不用重复填"
    assert got["overwork"] == "过劳", "plan.labels 压过 binding 的构念名"
    assert got["cls_mgr"] == "管理与专业阶级", "binding 盖不到的哑变量只能从 plan 来"
    assert display_labels(None, None) == {}, "两头都没有就是空表"


def test_主结果表描述统计表稳健性表都换成标签() -> None:
    body, _ = table_markdown("main", results(), en=False, labels=LABELS)
    assert "民主 × 互联网普及" in body and "dem*internet10" not in body, body
    assert "| 国家, 年份 |" in body, "固定效应那一行也换"

    body, _ = table_markdown("descriptives", results(), en=False, labels=LABELS)
    assert "预期寿命" in body and "| lifexp |" not in body, body

    rob = results(robustness={"换结局": RobustnessRun(
        label="换结局", n=2400, outcome="u5mort",
        estimates={"main": Estimate(term="dem*internet10", coef=0.02, se=0.01, p=0.03)})})
    body, _ = table_markdown("robustness", rob, en=False,
                             labels={**LABELS, "u5mort": "五岁以下死亡率"})
    assert "预期寿命" in body and "五岁以下死亡率" in body, body


def test_不给标签时表跟以前一模一样() -> None:
    """老运行目录没有 binding.json、老计划没有 labels 字段——一个字都不该变。"""
    assert table_markdown("main", results(), en=False)[0] == \
        table_markdown("main", results(), en=False, labels={})[0]


def test_印出原始列名只警告不拒绝() -> None:
    from oss.core.manuscript import unlabeled_terms

    res = results()
    assert "dem*internet10" not in unlabeled_terms(res, LABELS)
    bare = unlabeled_terms(res, {})
    assert "dem" in bare and "lifexp" in bare
    assert "country" not in bare, "固定效应是元数据，不为它报警告"

    problems, warnings = check(en_paper(), res)
    assert not [p for p in problems if "原始列名" in str(p)], "老运行目录还得能导出"
    assert [w for w in warnings if "原始列名" in str(w)], warnings
    assert not [w for w in check(en_paper(), res, labels=LABELS)[1] if "原始列名" in str(w)]


def test_系数图跟表用同一个标签() -> None:
    """图和表必须叫同一个名字——两处分别实现迟早对不上。"""
    pytest.importorskip("matplotlib")
    import inspect

    from oss.core import figures

    src = inspect.getsource(figures.coef)
    assert "term_label" in src, "图不能自己再实现一遍解析"
    assert "labels" in inspect.signature(figures.coef).parameters


# ══════════════════════════════ 七、英文稿按社会学通例（依据：styles/social-forces/exemplars/）


def test_星号档位中英不同() -> None:
    """英文 .05/.01/.001（Social Forces 范文的表注原文，没有 0.10 那一档）；中文沿用 0.10/0.05/0.01。

    **这个改动让结果看起来更不显著**（p=0.03 从两颗星降成一颗），方向是保守的。
    """
    from oss.core.manuscript import _stars

    assert _stars(0.0005, en=True) == "***" and _stars(0.0005, en=False) == "***"
    assert _stars(0.03, en=True) == "*", "英文档位下 0.03 是一颗星"
    assert _stars(0.03, en=False) == "**", "中文档位下 0.03 是两颗星"
    assert _stars(0.08, en=True) == "", "英文没有 0.10 那一档"
    assert _stars(0.08, en=False) == "*"


def test_英文系数表的标准误在下一行括号里_中文不动() -> None:
    en_body, _ = table_markdown("main", results(), en=True)
    lines = [l for l in en_body.splitlines() if l.startswith("|")]
    assert lines[0] == "| Variable | Estimate |", lines[0]
    assert "|  | (0.100) |" in lines, en_body
    assert "| p |" not in lines[0] and "| SE |" not in lines[0]

    zh_body, _ = table_markdown("main", results(), en=False)
    assert zh_body.splitlines()[0] == "| 变量 | 估计值 | 标准误 | p |", "中文表不动"
    assert "(0.100)" not in zh_body


def test_跑不动的那一项不补一行空括号() -> None:
    body, _ = table_markdown("robustness", results(), en=True)
    after = body[body.index("R2 Cluster by year"):]
    assert not after.splitlines()[1].startswith("|  |"), after[:160]


def test_表注起头是单数Note() -> None:
    """范文 6 篇里 14 处 `Note:`、0 处 `Notes:`，不论几句都是单数。"""
    for name in ("main", "descriptives", "robustness"):
        body, _ = table_markdown(name, results(), en=True)
        assert "*Note:*" in body and "*Notes:*" not in body, (name, body[:200])


def test_英文模板有写作指引和关键词() -> None:
    from oss.core.style import skeleton, template_markdown

    md = template_markdown(skeleton(None), pack="")
    assert "**Keywords:**" in md, "多数英文社科刊要 keywords，原先英文模板没有"
    assert "a reader understands without the model" in md, "原先英文每节下面只有一个空注释"
    assert "Keep standard errors, p-values, MDE and sample size out of the abstract" in md, \
        "摘要的写作规范中英同一条（依据只量过中文，英文这条按同一道理写，见 2026-09-23 设计稿第六节）"
    assert md.index("**Keywords:**") < md.index("## Introduction")


def test_规格代号进表要报警告不拒绝() -> None:
    src = en_paper(body="*Table 3. Robustness.*\n{{table:robustness}}\n")
    problems, warnings = check(src, results())
    assert not [p for p in problems if "规格代号" in str(p)], "老运行目录还得能导出"
    hit = [w for w in warnings if "规格代号" in str(w)]
    assert hit and "R1 Outcome: under-5 mortality" in str(hit[0]), warnings
    # 写清楚换了什么的就不报
    ok = results(robustness={"Outcome: under-5 mortality": RobustnessRun(
        label="Outcome: under-5 mortality", n=2400,
        estimates={"main": Estimate(term="dem*internet10", coef=0.02, se=0.01, p=0.03)})})
    assert not [w for w in check(src, ok)[1] if "规格代号" in str(w)]


def test_英文稿筛掉中文标签而不是整篇拒掉() -> None:
    """真跑抓到的回归（`ossrun-a`：英文稿 + 中文 binding.json）。

    绑定记录的构念名跟着**数据**的语言走，论文可能是另一种语言。中文标签顺着进英文表，
    会撞上「英文稿的表不能有中文」那道硬门——**整篇稿子导不出来**。筛掉、退回原列名，
    再由「表里会印出原始列名」那条警告提示作者补英文标签。
    """
    from oss.core.manuscript import unlabeled_terms, usable_labels

    zh_binding = {"dem": "民主", "internet10": "Internet penetration", "lifexp": "预期寿命"}
    assert usable_labels(zh_binding, en=True) == {"internet10": "Internet penetration"}
    assert usable_labels(zh_binding, en=False) == zh_binding, "中文稿照用"

    body, why = table_markdown("main", results(), en=True, labels=zh_binding)
    assert body and not why, f"不该整篇拒掉：{why}"
    assert "民主" not in body and "| dem |" in body, body
    assert "dem" in unlabeled_terms(results(), usable_labels(zh_binding, en=True)), "落回警告，作者才知道要补"
