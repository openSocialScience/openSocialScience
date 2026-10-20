"""论文校验与占位符渲染的测试。

**这里防的是整套系统最危险的失败模式**：产出一篇排版漂亮、引用齐全、
数字是手打的论文。

它危险在于**看不出来**——手打的 `0.032` 跟真的一模一样。
所以拦截必须是硬的，而且每条拦截都要有测试。
"""

from __future__ import annotations

import pytest

from oss.core.contracts import AnalysisResults, Estimate, PowerReport
from oss.core.manuscript import (
    BARE_DECIMAL,
    REQUIRED_SECTIONS,
    ManuscriptRejected,
    check,
    placeholders_in,
    render,
)


def results(**kw) -> AnalysisResults:
    base = dict(
        plan_id="p1",
        estimates={
            "main": Estimate(term="treat", coef=1.2496, se=0.1538, p=0.0000004),
            "edu": Estimate(term="edu_years", coef=-0.336, se=0.1999, p=0.0923),
        },
        n=360,
    )
    return AnalysisResults(**{**base, **kw})


def power_report() -> PowerReport:
    return PowerReport(
        idea_id="i1", effect_size_raw=0.5, effect_size_adjusted=0.3,
        n_raw=360, n_effective=147, n_clusters=12, icc=0.05, design_effect=2.45,
        power=0.4437, verdict="gray", gray_band=(0.4, 0.8), discipline="sociology",
    )


def paper(body: str = "") -> str:
    """一篇最小的合规稿子，方便在它上面改一处来测。"""
    return f"""# 标题

## 摘要
效应为 {{{{result:main.coef}}}}（标准误 {{{{result:main.se}}}}）。

## 引言
背景。

## 数据与方法
样本量 {{{{result:main.n}}}}。

## 结果
主效应 {{{{result:main.coef}}}}，p {{{{result:main.p}}}}。

## 讨论
局限。
{body}"""


# ══════════════════════════════════════════════ 核心：裸数字必须被拦


def test_合规的稿子能过() -> None:
    problems, _ = check(paper(), results())
    assert not problems


def test_手打小数被拒绝() -> None:
    """**这是这个模块存在的全部理由。**

    手打的 0.032 跟真的一模一样，看不出来——所以只能硬拦。
    """
    problems, _ = check(paper("\n主效应为 0.032。"), results())
    assert problems
    assert "裸小数 0.032" in problems[0]
    assert "占位符" in problems[0]


def test_多处裸小数逐条报出来() -> None:
    problems, _ = check(paper("\n系数 1.25，标准误 0.15。"), results())
    assert len(problems) == 2


def test_报出裸小数在第几行() -> None:
    """稿子长了之后，不给行号等于没报。"""
    problems, _ = check(paper("\n\n\n效应 0.99。"), results())
    assert "第 1" not in problems[0], "行号该指向真实位置，不是第 1 行"


@pytest.mark.parametrize("s", ["p = .004", "p < .001", "标准误0.123", "为0.123。",
                               "系数 0.123", "-0.45", "系数为-.917"])
def test_手打小数的各种写法都拦(s: str) -> None:
    """注入测试量出来的三类漏：小数点前省掉 0、小数紧挨汉字、p 值写成「< .001」。

    「标准误 0.123」（中间有空格）一直拦得住，「标准误0.123」反倒漏过——
    最反直觉的一处，根因是前置断言原先用 \\w，而汉字在 Python 里算词字符。
    """
    assert BARE_DECIMAL.findall(s), s


@pytest.mark.parametrize("s", ["v1.2.3", "figure2.png", "results.json", "prep.py",
                               "{{result:did.coef}}", "{{robust:X/main.se}}",
                               "10.1086/123456", "doi:10.1086/123456"])
def test_版本号文件名占位符编号不误伤(s: str) -> None:
    """放宽判据最容易反噬的地方：版本号、文件名、占位符路径、DOI 都长得像小数。"""
    assert not BARE_DECIMAL.findall(s), s


def test_小数紧挨汉字也拒绝整篇() -> None:
    """走完整条 check：两层豁免（抹代码与占位符、产出里真有的数）照旧先跑，拦的是判据这一关。"""
    problems, _ = check(paper("\n标准误0.777。"), results())
    assert any("裸小数 0.777" in p for p in problems), problems


# ─────────────────────────────────── 整数：不拦，但疑似结果要警告


def test_年份不算裸数字() -> None:
    problems, _ = check(paper("\n数据覆盖 2013 至 2021 年。"), results())
    assert not problems


def test_节号不算裸数字() -> None:
    problems, _ = check(paper("\n见第 3 节与表 12。"), results())
    assert not problems


def test_手打的样本量是警告不是拒绝() -> None:
    """整数不拦——年份、样本量、序号都是整数，全拦住就没法写了。

    但如果它恰好等于结果里的值，那多半是手打的。
    """
    problems, warnings = check(paper("\n样本为 360 个观测。"), results())
    assert not problems, "整数不该拦住渲染"
    assert any("360" in w for w in warnings)


def test_跟结果无关的整数不警告() -> None:
    _, warnings = check(paper("\n问卷共 47 道题。"), results())
    assert not any("47" in w for w in warnings)


# ─────────────────────────────────────── 代码块与行内代码要忽略


def test_代码块里的数字不算() -> None:
    """正文里贴一段代码示例不该被当成编造的结果。"""
    body = "\n```python\nalpha = 0.05\n```\n"
    problems, _ = check(paper(body), results())
    assert not problems


def test_行内代码里的阈值不算() -> None:
    """写 `p < 0.05` 这种阈值约定是正常的学术表述。"""
    problems, _ = check(paper("\n采用 `p < 0.05` 作为显著性标准。"), results())
    assert not problems


# ══════════════════════════════════════════════════ 节完整性


def test_少一节就拒绝() -> None:
    text = paper().replace("## 讨论", "## 结语")
    problems, _ = check(text, results())
    assert any("讨论" in p for p in problems)


def test_五节都是必需的() -> None:
    for names in REQUIRED_SECTIONS:
        text = paper().replace(f"## {names[0]}", "## 别的")
        problems, _ = check(text, results())
        assert any(names[0] in p for p in problems), f"少了「{names[0]}」却没被拦"


def test_英文节名也认() -> None:
    """英文论文用 Abstract/Introduction/... 当节名，不该被判成缺节。"""
    text = paper()
    for zh, en in (("摘要", "Abstract"), ("引言", "Introduction"),
                   ("数据与方法", "Data and Methods"), ("结果", "Results"),
                   ("讨论", "Discussion")):
        text = text.replace(f"## {zh}", f"## {en}")
    problems, _ = check(text, results())
    assert not any("缺「" in p for p in problems), problems


# ═════════════════════════════════ 三个命名空间：真写一篇论文才发现的缺口


def test_功效与诊断也能被引用() -> None:
    """**只做 result 命名空间是不够的。**

    诚实的局限那一节要写功效、设计效应、有效样本量——这些在 power.json 里。
    不给占位符的话，skill 只能手打，而那正是这套机制要防的事。

    这个缺口是**真写一篇论文时被校验拦住才发现的**。
    """
    text = paper(
        "\n功效 {{power:power}}，设计效应 {{power:design_effect}}，"
        "组内 R² {{diag:组内 R²}}。"
    )
    res = results(diagnostics={"组内 R²": "0.1406"})
    problems, _ = check(text, res, power_report())
    assert not problems

    out = render(text, res, power_report())
    assert "0.444" in out and "2.450" in out and "0.1406" in out


def test_没有power时引用功效会被拒绝() -> None:
    problems, _ = check(paper("\n功效 {{power:power}}。"), results(), None)
    assert any("没有 power.json" in p for p in problems)


def test_诊断里没有的键被拒绝并列出可用的() -> None:
    res = results(diagnostics={"组内 R²": "0.14"})
    problems, _ = check(paper("\nF 统计量 {{diag:F统计量}}。"), res, power_report())
    assert any("组内 R²" in p for p in problems), "要列出可用的诊断项"


def test_功效报告里没有的字段被拒绝() -> None:
    problems, _ = check(paper("\n{{power:nonexistent}}"), results(), power_report())
    assert any("功效报告里没有" in p for p in problems)


# ══════════════════════════════════════════════ 占位符解析


def test_引用不存在的估计被拒绝并列出可用的() -> None:
    text = paper().replace("{{result:main.se}}", "{{result:tab9.se}}")
    problems, _ = check(text, results())
    assert any("tab9" in p and "main" in p for p in problems), "要列出可用的估计名"


def test_引用不存在的字段被拒绝() -> None:
    text = paper().replace("{{result:main.se}}", "{{result:main.tstat}}")
    problems, _ = check(text, results())
    assert any("tstat" in p and "不存在" in p for p in problems)
    assert any("coef" in p for p in problems), "要列出可用的字段"


def test_全文没有占位符要警告() -> None:
    """一篇实证论文不引用自己的估计结果是可疑的。"""
    text = "# 标题\n" + "".join(f"\n## {s}\n内容。\n" for s in REQUIRED_SECTIONS)
    _, warnings = check(text, results())
    assert any("没有任何占位符" in w for w in warnings)


def test_列出全文用到的占位符() -> None:
    used = placeholders_in(paper())
    assert "{{result:main.coef}}" in used
    assert len(used) == 4  # coef / se / p / n，去重后


# ══════════════════════════════════════════════════ 渲染


def test_渲染把占位符换成数字() -> None:
    out = render(paper(), results())
    assert "{{" not in out
    assert "1.250" in out
    assert "360" in out


def test_极小的p值不报具体数值() -> None:
    """p = 0.0000004 报成「< 0.001」是学术惯例——
    报具体数值反而暗示了一种不存在的精度。"""
    assert "< 0.001" in render(paper(), results())


def test_解析不了就抛绝不静默留空() -> None:
    """**留空的话正文会变成「效应为  （标准误 ）」**——
    看起来像排版问题，不像数据缺失。那才是最坏的。
    """
    text = paper().replace("{{result:main.se}}", "{{result:ghost.se}}")
    with pytest.raises(ManuscriptRejected, match="ghost"):
        render(text, results())


def test_渲染后的数字跟结果一致() -> None:
    r = results()
    out = render("值 {{result:main.coef}}，样本 {{result:main.n}}", r)
    assert f"{r.estimates['main'].coef:.3f}" in out
    assert str(r.n) in out


def test_渲染后的稿子自己也能过校验() -> None:
    """渲染之后正文里全是裸数字——这是正常的，因为它已经是成品了。

    所以**校验只对源稿有意义**，不该拿去校验渲染产物。
    这个测试记录这个边界，免得以后有人把它接进流水线。
    """
    out = render(paper(), results())
    problems, _ = check(out, results())
    assert problems, "渲染产物必然有裸数字——它不该再过一遍校验"


# ═════════════════════════ 第四个命名空间：稳健性（真跑一次才发现的缺口）


def robust_results() -> AnalysisResults:
    from oss.core.contracts import RobustnessRun

    r = results()
    r.robustness = {
        "R1 结果换职业声望": RobustnessRun(
            label="R1 结果换职业声望",
            estimates={"main": Estimate(term="treat", coef=1.0921, se=0.1608, p=0.0000001)},
            n=340,
        ),
        "R9 跑不动的": RobustnessRun(label="R9 跑不动的", error="筛完没有样本了"),
    }
    return r


def test_稳健性结果能被引用() -> None:
    """**PLAN.md 九之一 ⑤。**

    GSS 真跑时，七项稳健性只能用「同号同显著」一句话带过——
    因为 `results.json` 只存一个规格，想报具体数值就只能手打，
    而手打会被校验拦下。**诚实的写法被机制堵死了。**
    """
    text = paper("\n稳健性：{{robust:R1 结果换职业声望/main.coef}}"
                 "（标准误 {{robust:R1 结果换职业声望/main.se}}）。")
    res = robust_results()
    problems, _ = check(text, res)
    assert not problems, problems
    out = render(text, res)
    assert "1.092" in out and "0.161" in out


def test_引用不存在的稳健性要列出可用的() -> None:
    text = paper("\n见 {{robust:R7 不存在的/main.coef}}。")
    problems, _ = check(text, robust_results())
    assert any("R1 结果换职业声望" in p for p in problems), "要列出可用的 label"


def test_引用跑不动的稳健性要被拒绝() -> None:
    """**跑不动的那一项没有结果，引用它就是编数字。**"""
    text = paper("\n见 {{robust:R9 跑不动的/main.coef}}。")
    problems, _ = check(text, robust_results())
    assert any("跑不动" in p or "没有结果" in p for p in problems), problems


# ─────────────────────────── 对比句：两个主效应必须是同一个量


def _mixed_results() -> AnalysisResults:
    from oss.core.contracts import RobustnessRun

    return results(
        estimates={
            "main": Estimate(term="treat", coef=0.127, se=0.75, p=0.865),
            "treat*net": Estimate(term="treat*net", coef=0.9, se=0.8, p=0.3),
        },
        robustness={
            "R0全样本": RobustnessRun(label="R0全样本", n=8541, estimates={
                "main": Estimate(term="treat", coef=1.522, se=0.77, p=0.048),
                "treat*net": Estimate(term="treat*net", coef=-0.3, se=0.8, p=0.7)}),
            "R11不含交互项": RobustnessRun(label="R11不含交互项", n=5248, estimates={
                "main": Estimate(term="treat", coef=0.74, se=0.83, p=0.372)}),
        },
    )


def test_一句里对比含交互项与不含交互项的主效应要警告():
    """验证跑的摘要：「平均效应 0.740（R11，不含交互项）……放回左删失国家升到 1.522（R0，含交互项）」。
    审稿人抓到了前一半（0.127 是条件效应），执行者补了 R11，却拿它去跟 R0 比。"""
    p = paper("平均效应为 {{robust:R11不含交互项/main.coef}}，放回全样本升到 {{robust:R0全样本/main.coef}}。")
    _, warnings = check(p, _mixed_results())
    assert any("不是同一个量" in w for w in warnings), warnings


def test_同一设定下两个样本对比不警告():
    p = paper("主规格为 {{result:main.coef}}，全样本为 {{robust:R0全样本/main.coef}}。")
    _, warnings = check(p, _mixed_results())
    assert not any("不是同一个量" in w for w in warnings), warnings


def test_分两句写就不算对比():
    p = paper("平均效应为 {{robust:R11不含交互项/main.coef}}。全样本的参照组条件效应为 {{robust:R0全样本/main.coef}}。")
    _, warnings = check(p, _mixed_results())
    assert not any("不是同一个量" in w for w in warnings), warnings


# ─────────────────────────── 最小可探测效应：由标准误算出，可引用


def test_最小可探测效应由标准误算出可引用():
    """验证跑里执行者手算了交互项的 MDE（2.2–2.8 年），那是零结果论文最要紧的一句，不该靠手算。"""
    r = results()
    assert abs(r.estimates["main"].mde - 2.8 * 0.1538) < 1e-6
    out = render(paper("最小可探测效应 {{result:main.mde}}。"), r)
    assert "0.431" in out
    reloaded = AnalysisResults.model_validate_json(r.model_dump_json())
    assert reloaded.estimates["main"].mde == r.estimates["main"].mde, "存进文件再读回来也一样"


# ─────────────────────────── 派生列：脚本里造的、分析用到的，论文里必须提到


def test_从构造脚本里抽派生列():
    from oss.core.manuscript import derived_columns

    script = """
d['post_dem'] = ((d.dem_year.notna()) & (d.year >= d.dem_year)).astype(int)
d["internet"] = d.it_net_user_zs.where(d.year >= 1990, 0.0)
d.ln_pop = np.log(d['pop'])
d.loc[d.internet.isna(), ['net_low']] = np.nan
if d['post_dem'] == 1: pass
"""
    assert derived_columns(script) == ["post_dem", "internet", "ln_pop"]


def test_分析用到的派生列论文没提就报():
    from oss.core.manuscript import unmentioned_derived

    paper_text = "We code post_dem as one from the first transition onward."
    used = {"post_dem", "internet", "lifexp"}
    assert unmentioned_derived(paper_text, used, ["post_dem", "internet", "ln_pop"]) == ["internet"]


def test_表格里各行的主效应不算一句话里的对比():
    """真论文的表 1 把 12 个规格列在一起——按段落切会把整张表当成一句话报警。"""
    table = ("| Spec | Coef |\n|---|---|\n"
             "| Average, no interactions | {{robust:R11不含交互项/main.coef}} |\n"
             "| Full sample | {{robust:R0全样本/main.coef}} |")
    _, warnings = check(paper(table), _mixed_results())
    assert not any("不是同一个量" in w for w in warnings), warnings


# ─────────────────────────── 引用：叙述式 / 括注式；导出时交给 citeproc


def _corpus():
    from oss.core.contracts import LiteratureCorpus, PaperRecord

    return LiteratureCorpus(corpus_id="c", queries=[], records=[
        PaperRecord(paper_id="10.1/abc", title="A paper", year=2007,
                    authors=["Masayuki Kudamatsu"], doi="10.1/abc", venue="Journal of X")])


def test_括注式引用与叙述式引用():
    out = render(paper("如 {{cite:10.1/abc}} 所示，另见 {{citep:10.1/abc}}。"), results(), corpus=_corpus())
    assert "Kudamatsu (2007)" in out and "（Kudamatsu，2007）" in out


def test_pandoc模式渲染引用键与书目():
    from oss.core.manuscript import cite_key, csl_json

    c = _corpus()
    src = paper("如 {{cite:10.1/abc}} 所示 {{citep:10.1/abc}}。\n\n## 参考文献\n\n{{references}}")
    out = render(src, results(), corpus=c, cite_mode="pandoc")
    key = cite_key("10.1/abc")
    assert f"@{key} 所示 [@{key}]" in out and "::: {#refs}" in out
    bib = csl_json(src, c)
    assert bib[0]["id"] == key
    assert bib[0]["author"][0] == {"family": "Kudamatsu", "given": "Masayuki"}
    assert bib[0]["container-title"] == "Journal of X" and bib[0]["issued"] == {"date-parts": [[2007]]}
    assert csl_json(paper("没引用。"), c) == [], "只列被引用过的"


def test_风格包的骨架可以替换通用五节():
    p = paper().replace("## 讨论", "## Conclusion")
    problems, _ = check(p, results(), required_sections=(("Abstract", "摘要"), ("Conclusion",)))
    assert not any("缺「" in x for x in problems), problems
    problems, _ = check(p, results(), required_sections=(("Limitations",),))
    assert any("缺「Limitations」" in x for x in problems)


# ─────────────────────────── 括注式一次引几篇


def _corpus2():
    from oss.core.contracts import LiteratureCorpus, PaperRecord

    return LiteratureCorpus(corpus_id="c", queries=[], records=[
        PaperRecord(paper_id="W1", title="A", year=2019, authors=["Thomas Bollyky", "Tara Templin", "Simon Wigley"],
                    venue="Lancet", volume="393", issue="10181", pages="1628–1640"),
        PaperRecord(paper_id="W2", title="B", year=2012, authors=["Masayuki Kudamatsu"]),
    ])


def test_括注式多篇合成一个括号():
    """验收跑里执行者写 {{citep:a,b}} 解析不了，只好连写「(A 2019) (B 2012)」。"""
    c = _corpus2()
    en = "## Abstract\n\nx\n\n## Introduction\n\nWell established {{citep:W1;W2}}.\n\n## Data and Methods\n\ny\n\n## Results\n\nz\n\n## Discussion\n\nw\n"
    out = render(en, results(), corpus=c)
    assert "(Bollyky et al. 2019; Kudamatsu 2012)" in out, out
    pd_out = render(en, results(), corpus=c, cite_mode="pandoc")
    assert "[@ref_W1; @ref_W2]" in pd_out, pd_out


def test_多篇里有一篇不在语料里整个拒绝():
    problems, _ = check(paper("如 {{citep:W1;nope}} 所示。"), results(), corpus=_corpus2())
    assert any("nope" in p for p in problems)


def test_书目带卷期页不再排成ahead_of_print():
    from oss.core.manuscript import csl_json

    bib = csl_json("{{citep:W1;W2}}", _corpus2())
    by = {b["id"]: b for b in bib}
    assert by["ref_W1"]["volume"] == "393" and by["ref_W1"]["issue"] == "10181" and by["ref_W1"]["page"] == "1628–1640"
    assert "volume" not in by["ref_W2"], "没有的字段不能编"


# ══════════════════════════════ 编号体例下叙述式引用要留住作者名


def test_编号体例下叙述式引用带作者名():
    """`@key` 在作者-年份体例下排成「Kudamatsu (2007)」，能当主语；
    换成 GB/T 顺序编码就只剩一个上标 `[1]`——**作者名整个丢掉**，
    正文变成「[1] 把它写成了……」，一个编号当句子主语，中文里不成句。

    验收跑 r20260907_edufert_zh 交付的 Word 里 33 处引用有 25 处是这个毛病，
    而审稿人看的 paper_rendered.md 里是「Caldwell (1980) 把它写成了……」——
    **交付的那一版从来没有以最终形态被审过。**
    """
    from oss.core.manuscript import cite_key

    c = _corpus()
    key = cite_key("10.1/abc")
    src = paper("如 {{cite:10.1/abc}} 所指出，另见 {{citep:10.1/abc}}。")

    num = render(src, results(), corpus=c, cite_mode="pandoc", numeric_citations=True)
    assert f"Kudamatsu[-@{key}] 所指出" in num, num
    assert f"[@{key}]" in num, "括注式在编号体例下照旧——[1] 本来就是对的"

    ad = render(src, results(), corpus=c, cite_mode="pandoc")
    assert f"@{key} 所指出" in ad and f"Kudamatsu[-@{key}]" not in ad, ad


def test_编号体例下多作者按等排():
    from oss.core.contracts import LiteratureCorpus, PaperRecord
    from oss.core.manuscript import cite_key

    c = LiteratureCorpus(corpus_id="c", queries=[], records=[
        PaperRecord(paper_id="10.1/xyz", title="T", year=2008, doi="10.1/xyz",
                    authors=["Sandra Black", "Paul Devereux", "Kjell Salvanes"])])
    out = render(paper("{{cite:10.1/xyz}} 发现。"), results(), corpus=c,
                 cite_mode="pandoc", numeric_citations=True)
    assert f"Black 等[-@{cite_key('10.1/xyz')}] 发现" in out, out


# ══════════════════════════════ 标题前的空行：pandoc 认不出就会裸露在正文里


def test_紧贴正文的标题渲染时补空行() -> None:
    """十一轮 CGSS 那篇成稿上出过：结论末段直接跟着 `## Abstract`，
    pandoc 把它并进上一段，Word 里出现一行裸露的「## Abstract」。"""
    from oss.core.manuscript import normalize_headings

    assert normalize_headings("末段。\n## Abstract\n\n正文") == "末段。\n\n## Abstract\n\n正文"
    assert normalize_headings("末段。\n\n## Abstract") == "末段。\n\n## Abstract", "已经有空行就不动"
    assert normalize_headings("## 一\n## 二") == "## 一\n## 二", "标题接标题 pandoc 认得出"
    assert normalize_headings("```\n# 这是代码里的井号\n```") == "```\n# 这是代码里的井号\n```"


def test_渲染出口会补上标题前的空行() -> None:
    src = paper().replace("\n\n## 讨论", "\n## 讨论")
    assert "\n## 讨论" in src and "\n\n## 讨论" not in src
    assert "\n\n## 讨论" in render(src, results())


def test_校验把认不出的标题报成警告() -> None:
    src = paper().replace("\n\n## 讨论", "\n## 讨论")
    problems, warnings = check(src, results())
    assert not [p for p in problems if "空行" in str(p)], "空白是机械可补的，不该拒稿"
    assert [w for w in warnings if "没有空行" in str(w)], warnings


# ══════════════════════════════ 摘要的写作规范
#
# 依据：2026-09-23 实测 59 篇已发表的中文摘要（人口研究、中国人口科学、管理世界等）——
# **一篇都没有标准误或 p 值**，字数中位 230；而我们生成的 8 篇 100% 带统计量、中位 480 字。
# 设计稿 docs/design/2026-09-23-abstract-writing.md。


def _zh_abstract(body: str, *, tail_en: str = "") -> str:
    return (f"# 标题\n\n## 摘要\n\n{body}\n\n## 引言\n\n背景。\n\n## 数据与方法\n\n样本。\n\n"
            f"## 结果\n\n结果。\n\n## 讨论\n\n讨论。\n\n## 参考文献\n\n条目\n" + tail_en)


def test_摘要里的统计量要报警告() -> None:
    from oss.core.manuscript import _abstract_statistics

    好 = _zh_abstract("城乡过劳概率之差为 {{result:main.coef}} 个百分点，城镇更低。")
    assert not _abstract_statistics(好), "效应量要留着——这是判据的另一半"
    for 坏 in ("标准误 {{result:main.se}}", "p = {{result:main.p}}",
               "最小可探测效应 {{result:main.mde}}", "n = {{result:main.n}}",
               "功效 {{power:achieved}}", "稳健性 {{robust:不加权/main.p}}"):
        assert _abstract_statistics(_zh_abstract(f"效应为 {{{{result:main.coef}}}}，{坏}。")), 坏
    # `{{diag:}}` 不禁：诊断里什么都有，而且有正当的摘要用法——弱工具那篇的
    # 「第一阶段有效 F 只有 X，常规区间不能用来推断」就是它的核心告诫
    assert not _abstract_statistics(_zh_abstract("有效 F 只有 {{diag:有效 F}}，区间不能用。"))


def test_中文摘要超三百字要报警告_英文不判() -> None:
    from oss.core.manuscript import ABSTRACT_MAX_ZH, _abstract_length

    assert not _abstract_length(_zh_abstract("本" * ABSTRACT_MAX_ZH)), "正好 300 字不报"
    hit = _abstract_length(_zh_abstract("本" * (ABSTRACT_MAX_ZH + 1)))
    assert hit and "301 字" in str(hit[0]), hit
    en = ("# T\n\n## Abstract\n\n" + "word " * 500 + "\n\n## Introduction\n\nx.\n\n"
          "## Data and Methods\n\nx.\n\n## Results\n\nx.\n\n## Discussion\n\nx.\n")
    assert not _abstract_length(en), "英文没有等价的硬规范，风格包那条路在管"


def test_取摘要按稿子的语言挑那一节() -> None:
    """中文稿文末多半还有一段英文摘要。先找 Abstract 会量到英文那一段——
    `ossrun-c` 的中文摘要 477 字，照旧写法量出来是英文摘要的长度，长度门整个失灵。"""
    from oss.core.manuscript import _abstract_body, _abstract_length

    src = _zh_abstract("中文摘要正文。", tail_en="\n## Abstract\n\nEnglish abstract body.\n")
    assert "中文摘要正文" in _abstract_body(src) and "English" not in _abstract_body(src)
    assert _abstract_length(_zh_abstract("本" * 400, tail_en="\n## Abstract\n\nShort.\n")), \
        "中文那段超长就要报，不能被文末英文摘要遮住"


def test_摘要的三条规范都是警告不是拒绝() -> None:
    """写作规范不同于事实错误：旧稿会大面积命中，拒绝会把老运行目录全堵死。"""
    src = _zh_abstract("效应 {{result:main.coef}}（标准误 {{result:main.se}}）。" + "本" * 400)
    problems, warnings = check(src, results())
    assert not [p for p in problems if "摘要" in str(p)], problems
    assert [w for w in warnings if "统计量" in str(w)], warnings
    assert [w for w in warnings if "超过" in str(w)], warnings


def test_关键词那一行不算进摘要字数() -> None:
    from oss.core.manuscript import _abstract_body

    src = _zh_abstract("摘要正文。\n\n**关键词**：过劳；阶级；工时")
    assert "关键词" not in _abstract_body(src) and "摘要正文" in _abstract_body(src)
