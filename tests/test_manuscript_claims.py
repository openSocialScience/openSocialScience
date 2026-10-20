"""`check_claims` 的七条检查，一条一个纯函数——单独调得动，才单测得了。

拆之前七条全是一个 168 行函数里的内联分支：想在交付稿上只跑「局限写没写功效」，
或者评测想单测「符号说反」一条，都只能整篇跑一遍 `check_claims`。
拆完**行为一个字不变**（文案与编号都不动），这一份测试盯的是两件事：
七个函数各自调得动，以及各自的判据边界没有在拆的时候走样。
"""

from __future__ import annotations

from oss.core.contracts import (
    AnalysisResults,
    Estimate,
    LiteratureCorpus,
    PaperRecord,
    PowerReport,
    RobustnessRun,
)
from oss.core.findings import code_of
from oss.core.manuscript import (
    check_claims,
    direction_warnings,
    limits_collinearity_problems,
    limits_power_problems,
    mde_warnings,
    paraphrase_warnings,
    proof_warnings,
    significance_problems,
)


def _power(**kw) -> PowerReport:
    return PowerReport(**{**dict(idea_id="i", power=0.4, verdict="fail"), **kw})


def _results(vif: str = "", robust_vif: str = "") -> AnalysisResults:
    est = {"main": Estimate(term="x", coef=0.2, se=0.05, p=0.001)}
    res = AnalysisResults(plan_id="p", n=100, estimates=est,
                          diagnostics={"共线诊断（VIF）": vif})
    if robust_vif:
        res.robustness = {"R1": RobustnessRun(
            label="R1", n=90, estimates=est,
            diagnostics={"共线诊断（VIF）": robust_vif})}
    return res


_BREEN = PaperRecord(
    paper_id="W2079007828",
    title="Educational Expansion and Social Mobility in the 20th Century",
    abstract=("Sociologists consider inequality in educational attainment to be a major "
              "cause of inequality between people. I measure the effect of educational "
              "expansion on social mobility in Britain, Sweden and Germany."),
    year=2010, authors=["Richard Breen"], doi="10.1353/sof.2010.0076", source="openalex")


# ══════════════════════════════════ 显著性：硬拦，没有歧义


def test_显著性纯函数可以单独调() -> None:
    out = significance_problems("这一效应显著", "did.p", "0.42")
    assert out and code_of(out[0]) == "claims.significance"
    assert not significance_problems("这一效应不显著", "did.p", "0.42")
    assert significance_problems("这一效应不显著", "did.p", "0.001")


def test_显著性只判p值的占位符() -> None:
    """判据挂在字段名上：`.coef` 的占位符轮不到这一条。"""
    assert not significance_problems("这一效应显著", "did.coef", "0.42")


def test_不仅显著不算否定() -> None:
    """「不仅显著」是在强调显著，不是否定——老行为不许在拆分时退掉。"""
    assert significance_problems("不仅显著而且稳健", "did.p", "0.42")


def test_不带显著二字的三种说反都拦() -> None:
    """**真实论文讲 p 值的句子多半不带「显著」二字**（真实底板 8 句里一句都没有）。

    这三种写法是评测 injection-v2 逐条核过的，不是猜的。
    """
    for s in ("在 5% 水平上可以拒绝系数为零的原假设",
              "该系数在统计上可以与零区分开",
              "该效应明确不为零"):
        assert significance_problems(s, "did.p", "0.42"), s


def test_否定说法不许被判成说显著() -> None:
    """补词表最可能的反噬：把「拒绝」「区分」的**否定式**读成在说显著。"""
    for s in ("不能拒绝系数为零的原假设", "在统计上与零区分不开",
              "在 5% 水平上不能拒绝为零的原假设", "无法与零区分开",
              "未能拒绝原假设", "该效应难以拒绝零假设"):
        assert not significance_problems(s, "did.p", "0.42"), s


# ══════════════════════════════════ 显著性：不带 p 占位符的句子


def _results_p(p: float = 0.42) -> AnalysisResults:
    return AnalysisResults(plan_id="p", n=100,
                           estimates={"did": Estimate(term="x", coef=0.2, se=0.05, p=p)})


def _sig(paper: str, res: AnalysisResults | None = None) -> list[str]:
    problems, _ = check_claims(paper, res if res is not None else _results_p())
    return [x for x in problems if code_of(x) == "claims.significance"]


def test_没有占位符的说反也拦() -> None:
    """同句里有指向这个估计的别的占位符 → 拿它的 p 来比。

    摘要与结论最常下显著性断言，而那两处最不带 p 占位符。
    """
    assert _sig("## 结果\n处理效应为 {{result:did.coef}}，该效应显著。\n")


def test_字面数字对上某个p才判() -> None:
    """同句里的字面数字恰好等于某个已知 p 的渲染写法 → 拿那个 p 来比。"""
    assert _sig("## 摘要\n主效应的 p 值为 0.420，该效应显著。\n")
    assert not _sig("## 摘要\n样本共 100 人，该效应显著。\n")


def test_文献综述里说别人显著不误报() -> None:
    """对不上就不判——**宁可漏，不可误报**。"""
    assert not _sig("## 文献\n既往研究发现该效应显著。\n")


def test_带p占位符的句子不重复报() -> None:
    """老那一路已经管了，新增的一路不许再报一遍。"""
    assert len(_sig("## 结果\n处理效应显著（p = {{result:did.p}}）。\n")) == 1


# ══════════════════════════════════ 符号：只警告，交互项整个不查


def test_符号纯函数可以单独调() -> None:
    out = direction_warnings("该效应在下降", "did.coef", "0.188")
    assert out and code_of(out[0]) == "claims.direction"
    assert direction_warnings("该效应在上升", "did.coef", "-0.188")
    assert not direction_warnings("该效应在上升", "did.coef", "0.188")


def test_符号不查交互项() -> None:
    assert not direction_warnings("方向不是向上转移，而是差距扩大", "a*b.coef", "-0.069")


def test_一句话里两个方向都有就不判() -> None:
    assert not direction_warnings("从上升转为下降", "did.coef", "0.188")


# ══════════════════════════════════ 「证明」：只在结果与稳健性上开


def test_证明纯函数可以单独调() -> None:
    out = proof_warnings("这证明了因果", "result")
    assert out and code_of(out[0]) == "claims.prove"
    assert proof_warnings("这证明了因果", "robust")
    assert not proof_warnings("这证明了因果", "cite")
    assert not proof_warnings("这只是一处关联", "result")


# ══════════════════════════════════ 最小可探测效应当成排除下界


def test_MDE纯函数可以单独调() -> None:
    out = mde_warnings("最小可探测效应是 0.05，因此可以排除更大的效应。")
    assert out and code_of(out[0]) == "claims.mde_exclusion"


def test_MDE说不能排除是正确用法() -> None:
    assert not mde_warnings("最小可探测效应是 0.05，不能排除更大的效应。")


def test_MDE的英文误用跨句也要抓到() -> None:
    """**这条检查要的窗口比句子宽。**病症天生跨句：MDE 在前一句、「排除」在后一句。

    切到句子就抓不到——Task 1 把这里并进 `textspan.sentences()` 时踩过这个坑，
    两套考卷与全量测试都没测出来，所以补这一条盯着。
    """
    out = mde_warnings("The minimum detectable effect is 0.05. "
                       "We can therefore rule out effects larger than that.")
    assert out, "跨句的英文误用漏了——这条检查的窗口不能按句子切"


def test_MDE的窗口在段落之间不串() -> None:
    """换行照旧是边界：上一段提了 MDE，下一段讲样本限制，不该凑成一条。"""
    assert not mde_warnings("最小可探测效应是 0.05。\n排除 2021 年那一轮之后重估。")


# ══════════════════════════════════ 局限：功效 / 精度


def test_局限纯函数可以单独调() -> None:
    out = limits_power_problems("## 局限\n样本不大。", _power(verdict="fail"))
    assert out and code_of(out[0]) == "claims.limits_power"
    assert not limits_power_problems("## 局限\n功效不足。", _power(verdict="fail"))
    assert not limits_power_problems("## 局限\n样本不大。", _power(verdict="pass"))
    assert not limits_power_problems("## 局限\n样本不大。", None)


def test_局限按精度判的走精度那一路() -> None:
    p = _power(verdict="gray", mode="precision")
    out = limits_power_problems("## 局限\n样本不大。", p)
    assert out and "精度" in out[0]
    assert not limits_power_problems("## 局限\n精度只够分辨半个标准差。", p)
    assert not limits_power_problems("## 局限\n功效不足。", p), "写「功效」也照旧认"


def test_局限只剩占位符不算写了功效() -> None:
    """`{{power:power}}` 的名字里自带 power——不抹掉占位符，局限里只剩这一个数也算交代过了。"""
    assert limits_power_problems("## 局限\n这一数值约为 {{power:power}}。", _power(verdict="gray"))
    assert limits_power_problems("## 局限\n这一数值约为 {{power:precision_halfwidth}}。",
                                 _power(verdict="gray", mode="precision"))


def test_写统计检验力或检验效能算交代了功效() -> None:
    """只认「功效」二字的时候，作者用标准同义说法反被判成没交代（评测 injection-v2 丁组的误报）。"""
    p = _power(verdict="gray")
    assert not limits_power_problems("## 局限\n统计检验力有限，小效应测不出。", p)
    assert not limits_power_problems("## 局限\n主效应的检验效能并不充裕。", p)
    assert not limits_power_problems("## Limitations\nstatistical power is limited.", p)


def test_写区间半宽或分辨不出算交代了精度() -> None:
    p = _power(verdict="gray", mode="precision")
    assert not limits_power_problems("## 局限\n主要比较的 95% 区间半宽只比有意义的差别略窄。", p)
    assert not limits_power_problems("## 局限\n这份样本能分辨的差别有限，更小的差别分辨不出来。", p)


def test_把握不足不算提了功效() -> None:
    """因果底板的局限里「平行趋势检验……把握不足」讲的是别的事：「把握」不能进同义词表。"""
    assert limits_power_problems("## 局限\n处理前趋势检验对线性违背把握不足。", _power(verdict="gray"))


def test_说功效充足按硬问题拦() -> None:
    out = limits_power_problems("## 局限\n本文的统计功效充足，足以检出有意义的效应。", _power(verdict="fail"))
    assert out and code_of(out[0]) == "claims.limits_power_contra"
    out = limits_power_problems("## 局限\n这份样本的精度足够，能分辨出有意义的差别。",
                                _power(verdict="gray", mode="precision"))
    assert out and code_of(out[0]) == "claims.limits_power_contra"


def test_说反优先于提到了() -> None:
    """放宽同义说法之后「统计检验力充足」两边都命中——按硬问题报，不算交代过了。"""
    out = limits_power_problems("## 局限\n本文的统计检验力充足。", _power(verdict="gray"))
    assert out and code_of(out[0]) == "claims.limits_power_contra"


def test_讨论与局限这种标题取得到() -> None:
    """合并标题下这条检查不许失灵：取不到节就等于整条放行。"""
    for head in ("## 讨论与局限", "## 结论与局限", "## Discussion and Limitations"):
        assert limits_power_problems(f"{head}\n无关内容。", _power(verdict="fail")), head


# ══════════════════════════════════ 局限：共线


def test_共线纯函数可以单独调() -> None:
    res = _results(vif="严重：x 的 VIF 为 12.3")
    out = limits_collinearity_problems("## 局限\n无。", res)
    assert out and code_of(out[0]) == "claims.limits_collinearity"
    assert not limits_collinearity_problems("## 局限\n存在共线。", res)
    assert not limits_collinearity_problems("## 局限\n无。", _results(vif="轻微"))


def test_共线每一项稳健性各自都要看() -> None:
    """只扫顶层的话，补充检验里的严重共线会整个漏掉——三轮互盲审稿抓到过。"""
    out = limits_collinearity_problems("## 局限\n无。", _results(robust_vif="严重：VIF 20"))
    assert out and "R1" in out[0]


def test_共线写在引言里不算交代() -> None:
    res = _results(vif="严重：x 的 VIF 为 12.3")
    assert limits_collinearity_problems("## 引言\n共线不是问题\n## 局限\n无", res)


def test_共线写在方法或结果节里算交代了() -> None:
    """诊断报在方法一节，交代也多半写在那里；真跑稿子里还有写在结果一节的。"""
    res = _results(vif="严重：x 的 VIF 为 12.3")
    assert not limits_collinearity_problems("## 数据与方法\n两者严重共线。\n## 局限\n无", res)
    assert not limits_collinearity_problems("## 结果\n年龄与队列近共线，不可单独解读。\n## 局限\n无", res)


def test_共线换标准说法也算交代了() -> None:
    res = _results(vif="严重：x 的 VIF 为 12.3")
    assert not limits_collinearity_problems("## 数据与方法\n两者的方差膨胀因子很大。\n## 局限\n无", res)
    assert not limits_collinearity_problems("## Data and Methods\nmulticollinearity is severe.\n## 局限\n无", res)


def test_只说高度相关不算交代共线() -> None:
    """「两个变量高度相关」没说系数不可单独解读——这一条考卷上算漏写，不能当成交代过了。"""
    res = _results(vif="严重：x 的 VIF 为 12.3")
    assert limits_collinearity_problems("## 数据与方法\n两种口径高度相关。\n## 局限\n无", res)


def test_说没有共线按硬问题拦() -> None:
    res = _results(vif="严重：x 的 VIF 为 12.3")
    out = limits_collinearity_problems("## 数据与方法\n两者之间没有明显的共线问题。\n## 局限\n无", res)
    assert out and code_of(out[0]) == "claims.limits_collinearity_contra"


# ══════════════════════════════════ 转述存疑


def test_转述纯函数可以单独调() -> None:
    corpus = LiteratureCorpus(corpus_id="c", records=[_BREEN])
    out = paraphrase_warnings("{{cite:W2079007828}} 发现城乡户口差异决定了健康水平。", corpus)
    assert out and code_of(out[0]) == "claims.paraphrase"
    assert not paraphrase_warnings("{{cite:W2079007828}} 发现教育扩张促进了社会流动。", corpus)
    assert not paraphrase_warnings("一句没有引用的话。", corpus)
    assert not paraphrase_warnings("{{cite:W2079007828}} 发现了什么。", None)
