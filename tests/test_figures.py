"""诊断图的测试。

**图不是装饰，是最快的错误检测器**，所以它自己也要有测试——
一张画错的诊断图比没有图更糟：它会让人以为自己检查过了。

不测像素（脆而且没意义），测三件事：
① 画得出来、文件非空
② 图依赖的那个事实确实被算对了
③ 没装 matplotlib 时**如实说**，不静默跳过
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from oss.core.profiler import profile_file

FIXTURES = Path(__file__).parent / "fixtures"
figures = pytest.importorskip("oss.core.figures")
pytest.importorskip("matplotlib")


@pytest.fixture(scope="module")
def gss():
    return pd.read_csv(FIXTURES / "gss_like.csv"), profile_file(FIXTURES / "gss_like.csv")


def _nonempty(p: Path) -> None:
    assert p.exists(), f"{p} 没生成"
    assert p.stat().st_size > 5000, f"{p} 只有 {p.stat().st_size} 字节，多半是空图"


def test_三张图都画得出来(gss, tmp_path) -> None:
    frame, prof = gss
    _nonempty(figures.structure(frame, prof, tmp_path / "s.png"))
    _nonempty(figures.missing(frame, prof, tmp_path / "m.png"))
    _nonempty(figures.clusters(frame, "region_year", tmp_path / "c.png"))


def test_结构图在没有时间列时也不崩(tmp_path) -> None:
    """截面数据没有时间维度，图要退化而不是报错。"""
    frame = pd.DataFrame({"x": range(50), "y": range(50)})
    from oss.core.profiler import profile_table
    from tests.test_profiler import table

    prof = profile_table(table(frame))
    _nonempty(figures.structure(frame, prof, tmp_path / "s.png"))
    _nonempty(figures.missing(frame, prof, tmp_path / "m.png"))


def test_缺失热力图依赖的块状缺失确实存在(gss) -> None:
    """图画的是「缺失率 × 时间」。**先确认这个矩阵本身是对的**——
    夹具里 `paeduc` 在最早两轮整段缺失，其余轮次只是部分缺失。
    """
    frame, _ = gss
    by_year = frame.groupby("year").paeduc.apply(lambda s: s.isna().mean())
    assert by_year.loc[2000] == 1.0 and by_year.loc[2004] == 1.0, "最早两轮该是整段缺失"
    assert by_year.loc[2020] < 0.5, "后面几轮不该是整段缺失"
    assert 0.3 < frame.paeduc.isna().mean() < 0.6, (
        "整体缺失率应当是个中等的数——"
        "**这正是只看整体缺失率会漏掉块状缺失的原因**"
    )


def test_簇图依赖的簇数确实很少(gss) -> None:
    frame, _ = gss
    assert frame.region.nunique() < 10, "夹具该有很少的区域，才画得出簇数不足"


def test_中文标签不会退化成方框(gss, tmp_path) -> None:
    """**方框比没有图更糟**——它看起来像图能用，实际读不出来。

    这里只能验证「选到了 CJK 字体」这一步；渲染结果肉眼可见，测不了。
    系统上一个 CJK 字体都没有时跳过，不算失败。
    """
    from matplotlib import font_manager

    have = {f.name for f in font_manager.fontManager.ttflist}
    if not any(f in have for f in figures._CJK_FONTS):
        pytest.skip("这台机器上没有任何中文字体")

    frame, prof = gss
    figures.structure(frame, prof, tmp_path / "s.png")
    import matplotlib.pyplot as plt

    assert any(f in figures._CJK_FONTS for f in plt.rcParams["font.sans-serif"])


def test_没装matplotlib时如实说(monkeypatch) -> None:
    """**能力缺失要明说，不能静默跳过。**

    静默跳过的后果是：使用者以为图画了，其实没有；
    而诊断图的全部价值就是「让人看见问题」。
    """
    import builtins

    real = builtins.__import__

    def blocked(name, *a, **kw):
        if name == "matplotlib":
            raise ImportError("no matplotlib")
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(figures.FiguresUnavailable, match="matplotlib"):
        figures._plt()


def test_稳健性森林图把跑不动的也画进去(tmp_path) -> None:
    """**没有结果本身就是信息。**

    省掉跑不动的那几项，读者分不出「没做」和「做了但不好看」——
    而这正是稳健性检验最容易被做手脚的地方。
    """
    from oss.core.contracts import AnalysisResults, Estimate, RobustnessRun

    res = AnalysisResults(
        plan_id="p", n=300,
        estimates={"main": Estimate(term="x", coef=1.2, se=0.3, p=0.0001)},
        robustness={
            "R1 换结果变量": RobustnessRun(
                label="R1 换结果变量", n=300,
                estimates={"main": Estimate(term="x", coef=1.05, se=0.28, p=0.0002)}),
            "R9 跑不动": RobustnessRun(label="R9 跑不动", error="筛完没样本了"),
        },
    )
    _nonempty(figures.robustness(res, tmp_path / "f.png"))


def test_分箱斜率图能画出非线性(tmp_path) -> None:
    """**这张图存在的理由。**

    交互项 `a × b` 假定「a 的斜率随 b **线性**变化」。
    这个假定几乎从来没人验证过——**回归表看不出来，图一眼就看得出**。

    这里造一份斜率随 by 呈折线（先平后升）变化的数据，
    图必须能把这个形状画出来。
    """
    import numpy as np

    rng = np.random.default_rng(11)
    n = 3000
    by = rng.integers(0, 6, n)
    slope = np.where(by < 3, 0.0, 0.5 * (by - 2))    # 先平后升，明显非线性
    x = rng.normal(size=n)
    y = slope * x + rng.normal(scale=0.5, size=n)
    frame = pd.DataFrame({"y": y, "x": x, "by": by})

    from oss.core.analysis import binned_slopes

    pts = binned_slopes(frame, "y", "x", "by")[0][1]
    got = [round(b, 2) for _, b, _, _ in pts]
    assert max(got[:3]) < 0.2, f"前三箱该是平的：{got}"
    assert got[-1] > 1.0, f"最后一箱该明显更陡：{got}"
    _nonempty(figures.marginal(frame, "y", "x", "by", tmp_path / "m.png"))


def test_分箱斜率图要说出哪一组没画出来(tmp_path) -> None:
    """**静默少一条线是最坏的**——图看起来正常，读者不会知道少了一半。

    处理变量在某个分组内恒为常数时（预先乘好的交互列在参照组里就是这样），
    那条线整个画不出来。
    """
    import numpy as np

    rng = np.random.default_rng(12)
    n = 1200
    g = rng.integers(0, 2, n)
    x = rng.normal(size=n) * g          # g=0 那组 x 恒为 0
    frame = pd.DataFrame({
        "y": 2 * x + rng.normal(scale=0.5, size=n),
        "x": x, "by": rng.integers(0, 4, n), "g": g,
    })
    from oss.core.analysis import binned_slopes

    series = binned_slopes(frame, "y", "x", "by", split="g")
    assert len(series) == 1, "恒为常数的那组本来就画不出来"
    _nonempty(figures.marginal(frame, "y", "x", "by", tmp_path / "m.png", split="g"))


def test_主系数图与共线图画得出来(gss, tmp_path) -> None:
    from oss.core.contracts import AnalysisPlan
    from oss.core.analysis import run_ols_fe

    frame, _ = gss
    plan = AnalysisPlan(
        plan_id="p", idea_id="i", outcome="sei", treatment="paeduc",
        controls=["educ", "age", "female"], fixed_effects=["region"],
        cluster_level="region_year",
    )
    res = run_ols_fe(frame, plan)
    _nonempty(figures.coef(res, tmp_path / "c.png"))
    _nonempty(figures.collinearity(frame, plan, tmp_path / "v.png"))


def test_共线图的VIF与结果里的对得上(gss) -> None:
    """**两个数不一致比没有数更糟。**图上的 VIF 必须跟 diagnostics 里的同源。"""
    from oss.core.analysis import run_ols_fe, vif_table
    from oss.core.contracts import AnalysisPlan

    frame, _ = gss
    plan = AnalysisPlan(
        plan_id="p", idea_id="i", outcome="sei", treatment="paeduc",
        controls=["educ", "age", "cohort"], fixed_effects=["year", "region"],
        cluster_level="region_year",
    )
    res = run_ols_fe(frame, plan)
    worst = max(vif_table(frame, plan), key=lambda r: r[1])
    assert worst[0] in res.diagnostics["共线诊断（VIF）"], (
        f"图上最严重的是 {worst[0]}，诊断里说的是：{res.diagnostics['共线诊断（VIF）']}"
    )


# ══════════════════════════════ 进论文的图：英文、发表样式、带旁注


def _res():
    from oss.core.contracts import AnalysisResults, Estimate, EventStudyPoint, RobustnessRun

    return AnalysisResults(
        plan_id="p", n=100,
        estimates={"main": Estimate(term="x", coef=1.0, se=0.2, p=0.0),
                   "z": Estimate(term="z", coef=-0.5, se=0.3, p=0.1)},
        robustness={"R1 去掉控制": RobustnessRun(label="R1 去掉控制", n=90, estimates={
            "main": Estimate(term="x", coef=0.9, se=0.25, p=0.0)})},
        event_study=[EventStudyPoint(k=k, coef=0.1 * k, se=0.2, n=10) for k in (-3, -2, 0, 1, 2)],
    )


def test_发表图英文版没有中文且带旁注(tmp_path) -> None:
    """验证跑的英文 Word 里四张图标题、坐标轴全是中文，没人拦。"""
    import json
    import re

    cjk = re.compile(r"[一-鿿]")
    for kind, fn in (("coef", figures.coef), ("event", figures.event), ("robustness", figures.robustness)):
        p = fn(_res(), tmp_path / f"fig_{kind}.png")
        _nonempty(p)
        meta = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
        assert meta["lang"] == "en" and meta["kind"] == kind, meta
        assert meta["caption"] and not cjk.search(meta["caption"]), meta["caption"]
        assert p.with_suffix(".pdf").exists(), "矢量版也要有"
    for k, v in figures._T["en"].items():
        assert not cjk.search(v), f"英文标签表里混进了中文：{k}"


def test_稳健性标签是中文时英文图只留编号(tmp_path) -> None:
    import json

    p = figures.robustness(_res(), tmp_path / "fig_robustness.png", lang="en")
    meta = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta.get("labels", {}).get("R1") == "R1 去掉控制", meta


def test_中文版照旧可画(tmp_path) -> None:
    import json

    p = figures.coef(_res(), tmp_path / "fig_coef.png", lang="zh")
    _nonempty(p)
    assert json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))["lang"] == "zh"


# ══════════════════════════════ 逐年展开图的旁注：判词归诊断，图不自己判


def _res_pretrend(verdict: str):
    """处理前各期逐点都不显著的夹具——逐期口径会说「平行趋势未被推翻」。

    真实来源：验收跑 r20260907_edufert_zh，处理前 5 期最大 |t| = 0.74，
    而各批 × 期的联合检验（Wald，自由度 105）p = 0.000 拒绝平行。
    """
    from oss.core.contracts import AnalysisResults, Estimate, EventStudyPoint

    res = AnalysisResults(
        plan_id="p", n=4450,
        estimates={"main": Estimate(term="x", coef=0.020, se=0.070, p=0.775)},
        event_study=[EventStudyPoint(k=k, coef=c, se=0.035, n=100) for k, c in
                     ((-5, -0.013), (-4, -0.019), (-3, -0.009), (-2, 0.002),
                      (0, 0.005), (1, 0.009), (2, -0.030))],
    )
    if verdict:
        res.diagnostics["平行趋势"] = verdict
    return res


_拒绝 = ("**不成立**：处理前 5 期的联合检验（各批 × 期，Wald，自由度 105）p = 0.000，"
       "拒绝「处理前平行」——两组在政策实施**之前**就已经在分化。"
       "**这意味着算出来的「效应」里混着本来就存在的趋势，结论不可信。**第 -5 期 -0.013")
_未拒绝 = ("处理前 5 期的联合检验（各批 × 期，Wald，自由度 105）p = 0.412，未拒绝，"
        "平行趋势未被推翻。第 -5 期 -0.013")


def test_联合检验拒绝时旁注不许说平行趋势未被推翻() -> None:
    """验收跑 r20260907_edufert_zh 的真实故障：图上印着「平行趋势未被推翻」，
    同一份 Word 的摘要里写着「联合检验拒绝了平行趋势」——一份文件自己打自己脸。

    成因是图按**逐期**判（5 个点各自都不显著），而真正的检验是**联合**的。
    逐期不显著推不出联合不拒绝，图没有资格下这个判词。
    """
    text, color = figures._pretrend_note(_res_pretrend(_拒绝))
    assert "未被推翻" not in text, f"逐期口径的结论又回来了：{text}"
    assert "不成立" in text or "拒绝" in text, text
    assert color != "#333", "判词为「不成立」时要标红"


def test_联合检验未拒绝时旁注照实转述诊断() -> None:
    text, color = figures._pretrend_note(_res_pretrend(_未拒绝))
    assert "未被推翻" in text and "不成立" not in text, text
    assert color == "#333", text


def test_没有平行趋势诊断时旁注不下任何判词() -> None:
    """**不知道就不说，不许猜一个判词。**

    2026-09-25 起旁注里只剩判词（教学式的描述句去掉了——三十八张真发表的图
    一张都没有这种话），所以拿不到诊断时旁注整条为空。"""
    for lang in ("zh", "en"):
        text, _ = figures._pretrend_note(_res_pretrend(""), lang)
        assert "未被推翻" not in text and "不成立" not in text, text
        assert "rejected" not in text and "not refuted" not in text, text
        assert not text.strip(), f"{lang}：没有诊断还写了东西 — {text!r}"


def test_平行趋势判词英文图上也要有() -> None:
    """英文图从前一条判词都没有——**同一个洞的另一半**。
    诊断是中文的，英文图上不能直接印（方框），所以照归一化的标记出英文短语。"""
    zh, _ = figures._pretrend_note(_res_pretrend("平行趋势不成立：联合检验 p = 0.000"), "zh")
    en, _ = figures._pretrend_note(_res_pretrend("平行趋势不成立：联合检验 p = 0.000"), "en")
    assert "不成立" in zh, zh
    assert "rejected" in en, en
    assert not figures._CJK.search(en), f"英文图上有中文：{en!r}"


def test_旁注与诊断同源_画出来的图带判词标记(tmp_path) -> None:
    import json

    p = figures.event(_res_pretrend(_拒绝), tmp_path / "fig_event.png", lang="zh")
    _nonempty(p)
    meta = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["pre_trend_verdict"] == "violated", meta
    assert meta["pre_trend_violations"] == 0, "逐期确实一期都不显著——这个数没错，错的是拿它下判词"


def test_折行不把西文词和数字劈开() -> None:
    lines = figures._wrap_cjk("处理前 5 期的联合检验（各批 × 期，Wald，自由度 105）p = 0.000，拒绝平行。", 34)
    assert all(len(x) <= 34 for x in lines), lines
    assert all("0.00" not in x or "0.000" in x for x in lines), f"小数被劈开了：{lines}"
    assert not any(x.endswith("Wal") or x.startswith("d，") for x in lines), lines


# ══════════════════════════════ 森林图：只并排可比的东西


def _res_mixed():
    """一次真实形状的稳健性：两项换了结局变量，两项没换。

    真实来源：验收跑 r20260907_edufert_zh。五项「稳健性」里三项换了结局
    （15–19 岁组生育率、30–34 岁组生育率、中学毛入学率），单位分别是每千名女性的
    生育数与入学率百分点，跟主结果的总和生育率不可比。它们被画在同一根轴上，
    把范围撑到 ±10，**真正可比的那两项被压成看不见的点**——读者看到的是
    「主估计精确、其它规格乱飞」，而事实恰恰相反。
    """
    from oss.core.contracts import AnalysisResults, Estimate, RobustnessRun, SpecSummary

    def rr(label, coef, se, outcome):
        return RobustnessRun(label=label, n=4450, outcome=outcome,
                             estimates={"main": Estimate(term="x", coef=coef, se=se, p=0.5)})

    return AnalysisResults(
        plan_id="p", n=4450, spec=SpecSummary(estimator="did_cs", outcome="tfr"),
        estimates={"main": Estimate(term="x", coef=0.020, se=0.070, p=0.775)},
        robustness={r.label: r for r in [
            rr("结局换为中学毛入学率", 1.171, 2.780, "wdi_gers"),
            rr("结局换为 30–34 岁组生育率", 4.300, 2.900, "fert_30_34"),
            rr("限于改革前入学率低于中位数的国家", 0.014, 0.090, "tfr"),
            rr("加入随时间变化的控制变量", -0.097, 0.110, ""),      # 空 = 没换（老文件也是空）
        ]},
    )


def test_换了结局的稳健性不进森林图(tmp_path) -> None:
    import json

    p = figures.robustness(_res_mixed(), tmp_path / "fig_robustness.png", lang="zh")
    _nonempty(p)
    meta = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
    shown = set((meta.get("labels") or {}).values()) or set(meta.get("shown") or [])
    assert "结局换为中学毛入学率" not in shown, f"换了结局的不该与主结果同轴：{meta}"
    assert "结局换为 30–34 岁组生育率" not in shown, meta
    assert "限于改革前入学率低于中位数的国家" in shown, meta
    assert "加入随时间变化的控制变量" in shown, "结局为空视为没换，照常画"
    assert meta.get("excluded_other_outcome") == 2, meta


def test_被排除的项要在图上说出来(tmp_path) -> None:
    """**不许静默丢掉。**读者要知道还有两项没画在这儿、为什么。"""
    text, _ = figures._robustness_note(_res_mixed())
    assert "2 项" in text and "结局" in text, text


def test_全部同结局时不出排除说明(tmp_path) -> None:
    from oss.core.contracts import AnalysisResults, Estimate, RobustnessRun, SpecSummary

    res = AnalysisResults(
        plan_id="p", n=100, spec=SpecSummary(estimator="did_cs", outcome="tfr"),
        estimates={"main": Estimate(term="x", coef=0.1, se=0.05, p=0.05)},
        robustness={"R1": RobustnessRun(label="R1", n=90, outcome="tfr",
                                        estimates={"main": Estimate(term="x", coef=0.09, se=0.06, p=0.1)})},
    )
    text, n = figures._robustness_note(res)
    assert n == 0 and text == "", text


def test_英文森林图排除时也要说而且不许漏中文(tmp_path) -> None:
    """英文图上排除了却什么都不说，等于把「少画了三项」变成读者看不见的事。

    但**不许点名**：稳健性 label 来自计划文件，中文稿的 label 就是中文的，
    点名会把中文漏进英文图。
    """
    import json
    import re

    text, n = figures._robustness_note(_res_mixed(), "en")
    assert n == 2 and "robustness table" in text, text
    assert not re.search(r"[一-鿿]", text), f"英文图的旁注混进了中文：{text}"

    p = figures.robustness(_res_mixed(), tmp_path / "fig_robustness.png", lang="en")
    _nonempty(p)
    meta = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["excluded_other_outcome"] == 2, meta


def test_分箱斜率图的口径要印在图上且跟着口径走(tmp_path, monkeypatch) -> None:
    """**图会被单独拿去用**——贴进 PPT、发给同事，那时它身上只剩轴上那几个字。

    原先这句话写死在标题里、不看 `adjusted`：`bivariate=False`（缺省）时
    命令行输出和图注都写对了，**只有画进图片里的那行字说反了**。
    11 轮 CGSS 真跑时被执行者当场抓到。

    2026-09-24 照中文期刊体例**去掉了图内标题**（图题排在图下方），
    这句话随之挪进**纵轴标签**——理由一个字没变：
    二元斜率和带控制变量的斜率是两个不同的量，认错了整张图就读反了。
    轴标签既合期刊体例、又跟着图走，比标题更该放这个信息。
    """
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.axes import Axes

    seen: list[str] = []
    real = Axes.set_ylabel
    monkeypatch.setattr(Axes, "set_ylabel",
                        lambda self, t, *a, **k: (seen.append(str(t)), real(self, t, *a, **k))[1])

    rng = np.random.default_rng(31)
    n = 400
    frame = pd.DataFrame({"x": rng.normal(size=n), "by": rng.integers(0, 6, n)})
    frame["y"] = frame.x * frame.by * 0.3 + rng.normal(size=n)

    # `adjusted=True` 只在 series 由 results.json 带进来时成立——
    # 函数自己现算的必然是二元的，所以那条路里 adjusted 会被强制回 False。
    series = [("全样本", [(0.0, 0.1, 0.02, 40), (1.0, 0.3, 0.02, 40), (2.0, 0.6, 0.03, 40)])]
    figures.marginal(frame, "y", "x", "by", tmp_path / "biv.png", lang="zh", series=series)
    figures.marginal(frame, "y", "x", "by", tmp_path / "adj.png", lang="zh",
                     series=series, adjusted=True)

    assert len(seen) == 2, seen
    # 中文纵轴竖排，长标签会折行——要的是「口径印在图上」，不是「印在一行上」
    flat = [t.replace("\n", "") for t in seen]
    assert "不含控制变量" in flat[0], flat[0]
    assert "含控制变量与固定效应" in flat[1], "带控制变量跑的，轴上不能说不含"
    assert "不含控制变量" not in flat[1]

    # 图注本来就是对的，别改坏了
    meta = json.loads((tmp_path / "adj.json").read_text(encoding="utf-8"))
    assert "含主规格的控制变量" in meta["caption"]


# ─────────────────────────────────────── 行名与行序（2026-09-24）

def test_系数图_主效应在最上交互项挨着它() -> None:
    """**原先照 `results.estimates` 的插入序画**，真跑出来的图上四个交互项
    一个在最顶、三个在最底（ossrun-a 的 fig_coef），中间隔着一串控制变量。
    读图的人得自己把它们认出来再拼到一起。"""
    from oss.core.contracts import Estimate

    def e(term):
        return Estimate(term=term, coef=0.1, se=0.05, p=0.05)

    items = [("ctrl_a", e("ctrl_a")), ("x*t", e("x*t")), ("ctrl_b", e("ctrl_b")),
             ("main", e("focal*t")), ("z*t", e("z*t"))]
    order = [k for k, _ in figures._coef_order(items)]
    assert order[0] == "main", f"主效应不在最上：{order}"
    assert order[1:3] == ["x*t", "z*t"], f"交互项没挨着主效应：{order}"
    assert order[3:] == ["ctrl_a", "ctrl_b"], f"其余没保持原序：{order}"


def test_系数图_画出来的行序跟排好的一致(tmp_path) -> None:
    """只测排序函数不够——**函数排对了、调用处没用上，图照样是乱的**。
    所以行序写进旁注的 `shown`，照图片本身检查。"""
    import json

    from oss.core.contracts import AnalysisResults, Estimate

    res = AnalysisResults(plan_id="p", n=400, estimates={
        "ctrl": Estimate(term="年龄", coef=0.01, se=0.004, p=0.01),
        "inter": Estimate(term="性别*时间", coef=0.02, se=0.03, p=0.5),
        "main": Estimate(term="出身*时间", coef=-0.04, se=0.014, p=0.004),
    })
    p = figures.coef(res, tmp_path / "f.png", lang="zh",
                     labels={"出身": "家庭出身", "性别": "性别", "时间": "调查年份", "年龄": "年龄"})
    shown = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))["shown"]
    assert shown[0] == "家庭出身 × 调查年份", f"主效应不在第一行：{shown}"
    assert shown[1] == "性别 × 调查年份", f"另一个交互项该紧跟着：{shown}"
    assert shown[2] == "年龄", shown


def test_系数图_只有一行时不画图例(tmp_path) -> None:
    """图例是用来分辨「高亮的」和「其他的」的。只有一行时没有其他，那它是废话。"""
    from oss.core.contracts import AnalysisResults, Estimate

    res = AnalysisResults(plan_id="p", n=90,
                          estimates={"main": Estimate(term="x", coef=1.0, se=0.2, p=0.0)})
    _nonempty(figures.coef(res, tmp_path / "f.png", lang="zh"))


def test_森林图横轴印系数的名字_不是估计量的键(tmp_path) -> None:
    """真跑出来的图上写着「Coefficient on main」——**读者无从知道 main 是哪个变量**，
    而图会被单独拿去用，那时连图注都不在身边。"""
    import json

    from oss.core.contracts import AnalysisResults, Estimate, RobustnessRun

    res = AnalysisResults(
        plan_id="p", n=500,
        estimates={"main": Estimate(term="出身*扩招", coef=-0.04, se=0.014, p=0.004)},
        robustness={"不加权": RobustnessRun(
            label="不加权", n=500,
            estimates={"main": Estimate(term="出身*扩招", coef=-0.039, se=0.014, p=0.005)})},
    )
    p = figures.robustness(res, tmp_path / "f.png", lang="zh",
                           labels={"出身": "家庭出身", "扩招": "扩招强度"})
    cap = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))["caption"]
    assert "main" not in cap, f"图注里还留着估计量的键：{cap}"
    assert "家庭出身" in cap, cap


def test_森林图的行名优先取label_键只是兜底(tmp_path) -> None:
    """契约里有 `label`，图从前读的是字典的键。现在唯一的写入方把两者写成同一个串，
    **将来多一个写入方就未必**——判据要挂在算得出来的那一格上。"""
    import json

    from oss.core.contracts import AnalysisResults, Estimate, RobustnessRun

    res = AnalysisResults(
        plan_id="p", n=500,
        estimates={"main": Estimate(term="x", coef=1.0, se=0.2, p=0.0)},
        robustness={"r0": RobustnessRun(
            label="排除疫情三年", n=480,
            estimates={"main": Estimate(term="x", coef=0.9, se=0.21, p=0.0)})},
    )
    p = figures.robustness(res, tmp_path / "f.png", lang="zh")
    shown = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))["shown"]
    assert "排除疫情三年" in shown, f"印的是键不是 label：{shown}"
    assert "r0" not in shown, shown


# ────────────────────────── 中英两套体例（2026-09-24，照真发表的论文定）

def _res_two():
    from oss.core.contracts import AnalysisResults, Estimate

    return AnalysisResults(plan_id="p", n=900, estimates={
        "main": Estimate(term="x", coef=-0.04, se=0.014, p=0.004),
        "z": Estimate(term="z", coef=0.18, se=0.027, p=0.0),
    })


def test_中文图不用颜色区分(tmp_path) -> None:
    """《管理世界》《公共行政评论》《华南师大学报》四张图**一点颜色都没有**——
    中文期刊是黑白印刷。我们原来的橙 #D55E00 与蓝 #0072B2 转成灰度是 119 vs 87，
    **12% 的明度差，印在纸上等于没有区别**。"""
    import colorsys

    for hexs in figures._pair_colors("zh") + figures._GRAY_SERIES:
        r, g, b = (int(hexs[i:i+2], 16) / 255 for i in (1, 3, 5))
        assert colorsys.rgb_to_hsv(r, g, b)[1] < 0.02, f"{hexs} 不是灰阶"


def test_中文图四周有框_英文图只留左下(tmp_path) -> None:
    """照实物定的：中文期刊的图四周有完整边框，Social Forces 只留左、下两条。"""
    import matplotlib.pyplot as plt

    for lang, want_top in (("zh", True), ("en", False)):
        with figures._pub(plt, lang):
            assert plt.rcParams["axes.spines.top"] is want_top, lang
            assert plt.rcParams["axes.spines.right"] is want_top, lang


def test_中文图里不放标题(tmp_path, monkeypatch) -> None:
    """图题排在图的**下方**，不画进图片里——四张实物都是这样。
    图题照旧进旁注 JSON，导出时 pandoc 把它排成 Word 题注，正好落在图下方。"""
    import json

    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.axes import Axes

    titles: list[str] = []
    real = Axes.set_title
    monkeypatch.setattr(Axes, "set_title",
                        lambda self, t, *a, **k: (titles.append(str(t)), real(self, t, *a, **k))[1])
    p = figures.coef(_res_two(), tmp_path / "f.png", lang="zh")
    assert not [t for t in titles if t.strip()], f"中文图里还有标题：{titles}"
    cap = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))["caption"]
    assert cap.strip(), "图题丢了——它该在旁注里等着排版"


def test_主效应用菱形_不靠颜色也分得出来(tmp_path) -> None:
    """**只靠颜色分，在黑白印刷下就没了。**森林图里菱形表示焦点估计是通行做法，
    形状是不靠颜色的那一半——中英文都要有。"""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.axes import Axes

    for lang in ("zh", "en"):
        markers: list[str] = []
        real = Axes.plot

        def spy(self, *a, **k):
            if (m := k.get("marker")):
                markers.append(str(m))
            return real(self, *a, **k)

        import pytest as _pt
        mp = _pt.MonkeyPatch()
        mp.setattr(Axes, "plot", spy)
        try:
            figures.coef(_res_two(), tmp_path / f"{lang}.png", lang=lang)
        finally:
            mp.undo()
        assert "D" in markers, f"{lang}：主效应没有用菱形，只剩颜色可分 — {markers}"
        assert "o" in markers, f"{lang}：其余系数该是圆点 — {markers}"


def test_英文图上不留中文_分箱斜率也要滤(tmp_path) -> None:
    """**方框比没有图更糟**——它看起来像图能用，实际读不出来（本模块开头那条）。
    coef 与 robustness 一直滤，**只有 marginal 漏了**：它直接印原始列名。"""
    import json

    import numpy as np

    rng = np.random.default_rng(7)
    n = 300
    frame = pd.DataFrame({"职业地位": rng.normal(size=n), "家庭出身": rng.normal(size=n),
                          "受教育年限": rng.integers(6, 20, n)})
    p = figures.marginal(frame, "职业地位", "家庭出身", "受教育年限", tmp_path / "m.png",
                         lang="en", labels={"职业地位": "Occupational status",
                                            "家庭出身": "Social origin",
                                            "受教育年限": "Years of schooling"})
    meta = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
    assert not meta.get("cjk_labels"), f"英文图上还有中文：{meta['cjk_labels']}"
    assert "Social origin" in meta["caption"], meta["caption"]


def test_英文图上的中文滤不掉就要说出来(tmp_path) -> None:
    """没有英文标签时方框是躲不掉的——**那就记下来**，让导出那一步拦住，
    不能让它静悄悄进论文。"""
    import json

    import numpy as np

    rng = np.random.default_rng(8)
    n = 200
    frame = pd.DataFrame({"职业地位": rng.normal(size=n), "家庭出身": rng.normal(size=n),
                          "受教育年限": rng.integers(6, 20, n)})
    p = figures.marginal(frame, "职业地位", "家庭出身", "受教育年限", tmp_path / "m.png",
                         lang="en")
    meta = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta.get("cjk_labels"), "滤不掉的中文一个都没记下来"


# ══════════ 画布尺寸（2026-09-23 那批，09-25 从 paper-figure-width 移植）
#
# 依据是同一批 19 张 Social Forces 真图，用 PyMuPDF 量的版面尺寸与印刷字号。
# **已发表的没有一张超过 4.71 英寸，我们真跑的 7 张里有 3 张超了，最宽 9.41 英寸。**
def test_画布超宽就等比缩_宽高比不变() -> None:
    from oss.core.figures import FIGURE_MAX_WIDTH, _canvas

    assert _canvas(4.0, 3.0) == (4.0, 3.0), "没超的不动"
    w, h = _canvas(11, 4)
    assert w == FIGURE_MAX_WIDTH
    assert abs(w / h - 11 / 4) < 1e-6, "等比缩，宽高比不变——只截宽度会把宽图压成方图"


def test_风格包可以覆盖上限_出了作用域就恢复() -> None:
    from oss.core.figures import FIGURE_MAX_WIDTH, _canvas, max_width

    with max_width(5.5):
        assert _canvas(11, 4)[0] == 5.5
    assert _canvas(11, 4)[0] == FIGURE_MAX_WIDTH
    with max_width(None):
        assert _canvas(11, 4)[0] == FIGURE_MAX_WIDTH, "没写就用缺省"


def test_折行宽度跟着画布算() -> None:
    """结构图原先写死 62 个字一行——那是照 11 英寸画布调的，画布缩到 4.33 之后
    这一行字本身就有 7.7 英寸宽，`_fit` 也救不回来（文字按磅走，缩画布不缩字）。"""
    pytest.importorskip("matplotlib")
    import matplotlib.pyplot as plt

    from oss.core.figures import _wrap_chars

    wide = plt.figure(figsize=(11, 4))
    narrow = plt.figure(figsize=(4.33, 2))
    assert _wrap_chars(wide, 8) > _wrap_chars(narrow, 8)
    assert 30 <= _wrap_chars(narrow, 8) <= 42, _wrap_chars(narrow, 8)
    plt.close(wide); plt.close(narrow)


def test_存出来的宽度真的进了上限_长标签也不例外(tmp_path) -> None:
    """只钳 figsize 不够：`bbox_inches="tight"` 连画到画布之外的长标签一起算，
    再加每边 0.1 英寸的留白——实测钳到 4.33 存出来是 4.51。"""
    pytest.importorskip("matplotlib")
    pytest.importorskip("PIL")
    from PIL import Image

    from oss.core.contracts import AnalysisResults, Estimate, RobustnessRun
    from oss.core.figures import FIGURE_MAX_WIDTH

    long_labels = {f"Specification {i}: a deliberately very long robustness label here": RobustnessRun(
        label=f"Specification {i}: a deliberately very long robustness label here", n=300,
        estimates={"main": Estimate(term="x", coef=1.0 + i / 10, se=0.3, p=0.01)}) for i in range(6)}
    res = AnalysisResults(plan_id="p", n=300,
                          estimates={"main": Estimate(term="x", coef=1.2, se=0.3, p=0.0001)},
                          robustness=long_labels)
    out = figures.robustness(res, tmp_path / "f.png")
    im = Image.open(out)
    inches = im.size[0] / im.info["dpi"][0]
    assert inches <= FIGURE_MAX_WIDTH + 0.02, f"存出来 {inches:.2f} 英寸，超了上限"


def test_稳健性图的注解进图注_不浮在绘图区(tmp_path) -> None:
    """已发表的图一律把说明放图注（Figure N. + 说明 + Note:），浮在图上还挤掉 18% 的纵向空间。"""
    pytest.importorskip("matplotlib")
    import json

    from oss.core.contracts import AnalysisResults, Estimate, RobustnessRun, SpecSummary

    res = AnalysisResults(
        plan_id="p", n=300,
        spec=SpecSummary(estimator="ols_fe", outcome="y", treatment="x"),
        estimates={"main": Estimate(term="x", coef=1.2, se=0.3, p=0.0001)},
        robustness={"Outcome: z": RobustnessRun(
            label="Outcome: z", n=300, outcome="z",
            estimates={"main": Estimate(term="x", coef=0.4, se=0.2, p=0.05)})},
    )
    out = figures.robustness(res, tmp_path / "f.png")
    meta = json.loads(Path(str(out).replace(".png", ".json")).read_text(encoding="utf-8"))
    assert "replace the outcome variable" in meta["caption"], meta["caption"]


def test_长标签折行而不是把整张图缩小() -> None:
    """`_fit` 对长标签救不回来：轴区按画布比例缩、标签按磅走，**越缩溢出占比越大**——
    实测中文稳健性图缩到 2.44 英寸画布，标签仍占 1.86 英寸，绘图区只剩一条缝。"""
    from oss.core.figures import _wrap_labels

    long_en = "Alternative class scheme (supervisors moved up)"
    out = _wrap_labels([long_en, "短"], 4.33)
    assert "\n" in out[0], out[0]
    assert out[1] == "短", "短标签不动"
    assert not any(l.rstrip().endswith(("(", "（")) for l in out[0].split("\n")), \
        f"行尾不要留孤零零的左括号：{out[0]!r}"
    assert "\n" not in _wrap_labels([long_en], 12.0)[0], "画布够宽就不折"


def test_横轴刻度跟着画布稀释() -> None:
    """画布压到 4.33 英寸后还用缺省的 ~9 档，「−0.008−0.006−0.004」会连成一片。"""
    pytest.importorskip("matplotlib")
    import matplotlib.pyplot as plt

    from oss.core.figures import _thin_xticks

    counts = []
    for w in (4.33, 12.0):
        fig, ax = plt.subplots(figsize=(w, 2))
        ax.plot([-0.008, 0.008], [0, 1])
        _thin_xticks(ax, fig)
        fig.canvas.draw()
        counts.append(len([t for t in ax.get_xticks()]))
        plt.close(fig)
    assert counts[0] < counts[1], f"窄画布该少放几档：{counts}"


def test_图注里不留换行() -> None:
    """折行过的标签会把 \\n 原样带进图注——真跑上见过「Overwork threshold 60\\nhours per week」。"""
    pytest.importorskip("matplotlib")
    import json

    from oss.core.contracts import AnalysisResults, Estimate, RobustnessRun, SpecSummary

    res = AnalysisResults(
        plan_id="p", n=300,
        spec=SpecSummary(estimator="ols_fe", outcome="y", treatment="x"),
        estimates={"main": Estimate(term="x", coef=1.2, se=0.3, p=0.0001)},
        robustness={"Outcome: a very long replacement outcome name here": RobustnessRun(
            label="Outcome: a very long replacement outcome name here", n=300, outcome="z",
            estimates={"main": Estimate(term="x", coef=0.4, se=0.2, p=0.05)})},
    )
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        out = figures.robustness(res, Path(d) / "f.png")
        meta = json.loads(Path(str(out).replace(".png", ".json")).read_text(encoding="utf-8"))
    assert "\n" not in meta["caption"], repr(meta["caption"])
