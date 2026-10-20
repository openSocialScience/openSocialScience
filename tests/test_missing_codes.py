"""缺失值代码检测：99 = 拒答、-99 = 缺失这类约定，在文件里就是普通数字。

**算进平均数就全错，而且不会报任何错。**这是用户带自己的调查数据时几乎必踩的坑，
而二十次验收跑全用公开数据，一次都没撞上。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from oss.core.profiler import _sentinel_hits


def _col(values, n_sentinel=0, sentinel=99):
    return pd.Series(list(values) + [sentinel] * n_sentinel)


def test_年龄里的99被认出来() -> None:
    rng = np.random.default_rng(0)
    hits = _sentinel_hits(_col(rng.integers(18, 86, 500), 40, 99))
    assert [h[0] for h in hits] == [99.0], hits
    assert hits[0][1] == 40


def test_李克特量表里的9被认出来() -> None:
    """1–7 量表，9 = 拒答。空档只有 2，也得抓到。"""
    rng = np.random.default_rng(1)
    hits = _sentinel_hits(_col(rng.integers(1, 8, 400), 30, 9))
    assert [h[0] for h in hits] == [9.0], hits


def test_负数缺失码也认() -> None:
    rng = np.random.default_rng(2)
    hits = _sentinel_hits(_col(rng.normal(50000, 12000, 600).round(), 25, -99))
    assert [h[0] for h in hits] == [-99.0], hits


def test_百分比里合法的99不误报() -> None:
    """0–100 的百分比，99 是真值——**它跟 98、97 是连着的，没有空档**。"""
    rng = np.random.default_rng(3)
    assert _sentinel_hits(pd.Series(rng.integers(0, 101, 800))) == []


def test_年份不误报() -> None:
    assert _sentinel_hits(pd.Series(list(range(1990, 2021)) * 30)) == []


def test_太少的行不判() -> None:
    """样本太小时分布看不出空档，宁可不判。"""
    assert _sentinel_hits(_col(range(18, 60), 5, 99)) == []


def test_零星几个不算() -> None:
    """真有人 99 岁。**要有量**才算代码。"""
    rng = np.random.default_rng(4)
    assert _sentinel_hits(_col(rng.integers(18, 86, 900), 3, 99)) == []


def test_非数值列直接跳过() -> None:
    assert _sentinel_hits(pd.Series(["甲", "乙", "丙"] * 100)) == []


# ═══════════ 取值标签是原文，不是猜测（CGSS2017 真跑暴露）

def test_标签写明的码不设门槛就认() -> None:
    """`.sav`/`.dta` 每个码都带一句人话。**文件说了的事不需要够不够量。**

    分布判据要求「至少 10 个且占 0.5%」，因为那是猜的，宁可漏。
    标签不是猜的——写着「拒绝回答」就是拒绝回答，出现两次也是两次。
    """
    s = _col(range(1, 6), 2, 99)
    hits = _sentinel_hits(s, {"1": "很不健康", "5": "很健康", "99": "拒绝回答"})
    assert hits == [(99.0, 2)], hits


def test_大数码不许塌成同一个键() -> None:
    """CGSS 个人年收入的 9999997/9999998/9999999 是三个不同的码。

    用 `:g` 格式化它们全变成 `1e+07`——当字典键就是三个并成一个，
    两个码连同人数一起消失，而画像照样报得理直气壮。
    """
    from oss.core.profiler import _fmt_code

    assert {_fmt_code(v) for v in (9999997.0, 9999998.0, 9999999.0)} == {
        "9999997", "9999998", "9999999"}

    rng = np.random.default_rng(11)
    s = pd.Series(list(rng.integers(0, 200000, 500)) + [9999997] * 265
                  + [9999998] * 197 + [9999999] * 218)
    hits = _sentinel_hits(s, {"9999997": "不适用", "9999998": "不知道",
                              "9999999": "拒绝回答"})
    assert hits == [(9999997.0, 265), (9999998.0, 197), (9999999.0, 218)], hits


def test_含不知道三个字的实质选项不误判() -> None:
    """CGSS2017 真有一个选项叫「不知道什么是上网」——**那是回答，不是拒答**。

    把它抹成缺失，等于把一整类受访者悄悄删掉，而且没人会发现。
    """
    from oss.core.profiler import _label_says_missing

    assert not _label_says_missing("不知道什么是上网")
    assert not _label_says_missing("说不上同意不同意")
    assert _label_says_missing("不知道")
    assert _label_says_missing("98. 不知道")
    assert _label_says_missing("不知道/拒绝回答")
    assert _label_says_missing("Refused")
    assert _label_says_missing("refused to answer")
    assert _label_says_missing("IAP")
    assert not _label_says_missing("Not at all satisfied")


def test_标签写着实质含义的码不再被猜成缺失() -> None:
    """教育程度里 8 = 技校。**文件已经回答过的问题，不该由分布形状去推翻。**"""
    rng = np.random.default_rng(12)
    s = pd.Series(list(rng.integers(1, 7, 400)) + [8] * 60)   # 1–6 之后空了个 7
    assert _sentinel_hits(s) == [(8.0, 60)], "没标签时按老判据仍然报"
    assert _sentinel_hits(s, {"8": "技校"}) == [], "标签说它是技校，就不是缺失码"


def test_没标签的列照旧走分布判据() -> None:
    """有值标签的文件里也会有裸列（CGSS 的周工作小时就没有标签）。"""
    rng = np.random.default_rng(13)
    hits = _sentinel_hits(_col(rng.integers(18, 86, 500), 40, 99), {})
    assert [h[0] for h in hits] == [99.0], hits


def test_画像把标签原文一并留下() -> None:
    """模型要照着写 prep.py，就得看得见「9999997 = 不适用」这句原文。"""
    from oss.core.profiler import profile_table
    from tests.test_profiler import table

    rng = np.random.default_rng(14)
    frame = pd.DataFrame({
        "id": range(400),
        "income": list(rng.integers(0, 90000, 340)) + [9999997] * 60,
    })
    prof = profile_table(table(frame, value_labels={"income": {"9999997": "不适用"}}))
    assert prof.labeled_missing_codes["income"] == {"9999997": "不适用"}
    assert prof.suspected_missing_codes["income"] == [(9999997.0, 60)]
    w = " ".join(prof.warnings)
    assert "文件自己写明的" in w and "不适用" in w


# ═══════════ .dta 的值标签要按列名接（CGSS2017 真跑暴露）

def test_stata值标签按列名接而不是按标签集名() -> None:
    """**Stata 的值标签是一张张具名的表，变量再挂到某张表上。**

    `StataReader.value_labels()` 给的是 `{标签集名: {码: 含义}}`——不是列名。
    照列名用它，接上的只有碰巧同名的那几个：CGSS2017 的 `.dta` 上量过，
    **619 组值标签只接上 1 组**（`isurban`，因为它恰好是小写）。

    后果不是报错，是缺失码探测在真调查数据上整个失效——
    个人年收入的 `9999997=不适用` 认不出来，均值就还是 58 万。
    """
    import pandas as pd
    import pytest
    pyreadstat = pytest.importorskip("pyreadstat")
    import tempfile, pathlib
    from oss.core.loaders import load_any

    tmp = pathlib.Path(tempfile.mkdtemp())
    p = tmp / "w.dta"
    # CGSS 就是这么存的：标签集名是列名的大写，跟列名不相等
    pyreadstat.write_dta(
        pd.DataFrame({"a8a": [30000.0, 9999997.0], "a15": [3.0, 99.0]}), str(p),
        variable_value_labels={"a8a": {9999997.0: "不适用"}, "a15": {99.0: "拒绝回答"}})

    t = load_any(str(p))
    assert t.value_labels.get("a8a") == {"9999997": "不适用"}
    assert t.value_labels.get("a15") == {"99": "拒绝回答"}


# ═══════════ 连号的哨兵码要整段一起判（评测第 1 步·陷阱集坑 3）

def test_连号的哨兵码整段一起判() -> None:
    """ISCO 的 9997 / 9998 / 9999 挨着出现——CGSS 真数据就是这个样子。

    逐个跟「其余取值」比，三个互相挨着，空档永远只有 1，**三个一个都不报**；
    9997 本身还不在猜的码表里。整段并成一组再跟其余取值比，三个才都露出来。
    """
    rng = np.random.default_rng(21)
    real = [1120, 2221, 3113, 4110, 5120, 6111, 7112, 8322, 9112, 9629]
    s = pd.Series(list(rng.choice(real, 555)) + [9997] * 15 + [9998] * 15 + [9999] * 15)
    assert [h[0] for h in _sentinel_hits(s)] == [9997.0, 9998.0, 9999.0], _sentinel_hits(s)


def test_量表的8与9一起出现时两个都认() -> None:
    """1–5 的量表，8 = 无法选择、9 = 拒答，两个码都在。

    老判据下 9 跟 8 只隔 1、8 又「夹在中间」——两个都漏。
    """
    rng = np.random.default_rng(22)
    s = pd.Series(list(rng.integers(1, 6, 500)) + [8] * 30 + [9] * 30)
    assert [h[0] for h in _sentinel_hits(s)] == [8.0, 9.0], _sentinel_hits(s)


def test_负的连号缺失码也整段判() -> None:
    rng = np.random.default_rng(23)
    s = pd.Series(list(rng.integers(1, 8, 500)) + [-9] * 30 + [-8] * 30)
    assert [h[0] for h in _sentinel_hits(s)] == [-9.0, -8.0], _sentinel_hits(s)


def test_整段贴着真值就不报() -> None:
    """0–100 的百分比：98、99 跟 97、96……连成一片，整段跟其余取值没有空档。"""
    rng = np.random.default_rng(24)
    assert _sentinel_hits(pd.Series(rng.integers(0, 101, 800))) == []


def test_整段里量不够的那个码不报() -> None:
    """**「有量」这条对段里每个码仍然管用**：一个 99 岁的老人不是代码。"""
    rng = np.random.default_rng(25)
    s = pd.Series(list(rng.integers(18, 86, 500)) + [98] * 40 + [99] * 2)
    assert [h[0] for h in _sentinel_hits(s)] == [98.0], _sentinel_hits(s)


# ═══════════ −1 进码表（陷阱集坑 3 的 −1 变体），外加压住 ±1 效应编码的那条豁免

def test_职业码里的负1认得出() -> None:
    """CFPS 一类数据把 −1 当「不适用」，ISCO 这种大整数码里它在最下端、隔得老远。

    −1 原先根本不在猜的码表里，陷阱集坑 3 的 `isco_neg1` 是**诚实的 0**。
    """
    rng = np.random.default_rng(26)
    real = [1120, 2221, 3113, 4110, 5120, 6111, 7112, 8322, 9112, 9629]
    s = pd.Series(list(rng.choice(real, 570)) + [-1] * 30)
    assert [h[0] for h in _sentinel_hits(s)] == [-1.0], _sentinel_hits(s)


def test_正负1的效应编码不当成缺失码() -> None:
    """**加 −1 唯一必然误报的那一类**：取值只有 −1 与 1 的 ±1 编码（男 = 1、女 = −1，
    或对比编码）。−1 在最下端、空档正好 2、量也够，三条判据全过。

    豁免：`−1` 之外还剩不到 3 个不同取值时不报——±1 编码只剩一个取值 1。
    """
    rng = np.random.default_rng(27)
    s = pd.Series(rng.choice([-1, 1], 600))
    assert _sentinel_hits(s) == [], _sentinel_hits(s)


def test_正负1加0的三值编码也不当成缺失码() -> None:
    """−1 / 0 / 1 的三值编码：这一类靠「整段最外面那个码要在一头」自己就挡住了。"""
    rng = np.random.default_rng(28)
    s = pd.Series(rng.choice([-1, 0, 1], 600))
    assert _sentinel_hits(s) == [], _sentinel_hits(s)


def test_负1豁免只压取值太少的列() -> None:
    """豁免要窄：其余取值有 3 个以上的，−1 照旧报——李克特 1–5 加一个 −1。"""
    rng = np.random.default_rng(29)
    s = pd.Series(list(rng.integers(1, 6, 500)) + [-1] * 40)
    assert [h[0] for h in _sentinel_hits(s)] == [-1.0], _sentinel_hits(s)


def test_别的缺失码不受这条豁免影响() -> None:
    """豁免只挂在 −1 上。0/1 的哑变量加一个 −9，其余取值只有两个，照旧要报。"""
    rng = np.random.default_rng(30)
    s = pd.Series(list(rng.choice([0, 1], 560)) + [-9] * 40)
    assert [h[0] for h in _sentinel_hits(s)] == [-9.0], _sentinel_hits(s)


# ═══════════ 缺失标签按段认，不要求整串就是那句话（陷阱集坑 4）

def test_标签后面跟着括号里的说明也认得出() -> None:
    """CGSS 真实写法：「不适用（上周没有工作）」。

    掐掉「不适用」还剩六个字，整串判会当成实质选项——按段判才认得出。
    """
    from oss.core.profiler import _label_says_missing

    assert _label_says_missing("不适用（上周没有工作）")
    assert _label_says_missing("不知道（没听说过这项政策）")
    assert _label_says_missing("9. 拒绝回答（涉及个人隐私）")
    assert _label_says_missing("Not applicable (did not work last week)")


def test_按段认也不许误伤实质选项() -> None:
    """**这是这条改动的全部风险所在。**含着那句话的实质选项一个都不能被抹掉。"""
    from oss.core.profiler import _label_says_missing

    assert not _label_says_missing("不知道什么是上网")
    assert not _label_says_missing("说不上同意不同意")
    assert not _label_says_missing("不适用于本人目前的情况")
    assert not _label_says_missing("其他（包括无法回答上述问题的情形）")
    assert not _label_says_missing("Not at all satisfied")


# ═══════════ 进了回归的列在 oss run 复查（陷阱集坑 1：画像认出来了，后面没人管）

def _reg_frame(code: float | None = 99.0, n_coded: int = 40, n: int = 400, seed: int = 31):
    """收入 ~ 城乡 + 年龄；`code` 不是 None 时把 `n_coded` 个人的年龄换成那个码。"""
    rng = np.random.default_rng(seed)
    real = n - n_coded if code is not None else n
    age = list(rng.integers(18, 86, real)) + ([code] * n_coded if code is not None else [])
    return pd.DataFrame({
        "income": rng.normal(50000, 12000, n).round(),
        "urban": rng.integers(0, 2, n),
        "age": age,
        "score": rng.integers(0, 101, n),          # 0–100 分：99 是真值
    })


def _reg_case(frame, value_labels=None, **kw):
    """(计划, 画像, 帧)。画像照原始数据画，帧是分析真正要跑的那一份。"""
    from oss.core.contracts import AnalysisPlan
    from oss.core.profiler import profile_table
    from tests.test_profiler import table

    base = dict(plan_id="p1", idea_id="i1", estimator="ols_fe",
                outcome="income", treatment="urban", controls=["age"])
    plan = AnalysisPlan(**{**base, **kw})
    return plan, profile_table(table(frame, value_labels=value_labels or {}))


def test_标签写明是缺失码的列进了回归就拒跑() -> None:
    """一整列 99 一路算进回归、写进论文——画像提醒过，可是后面没有一道门看它。"""
    from oss.core.analysis import validate_plan
    from oss.core.findings import code_of

    frame = _reg_frame()
    plan, prof = _reg_case(frame, {"age": {"99": "拒绝回答"}})
    problems, _ = validate_plan(plan, prof, frame)
    assert any(code_of(p) == "plan.columns.missing_code_labeled" for p in problems), problems


def test_只是分布上像的只警告并给出可执行的下一步() -> None:
    """**拒跑是很重的处置。**没有标签撑着就只能是猜的——99 岁的人真的存在。"""
    from oss.core.analysis import validate_plan
    from oss.core.findings import code_of

    frame = _reg_frame()
    plan, prof = _reg_case(frame)
    problems, warnings = validate_plan(plan, prof, frame)
    assert not problems, problems
    hit = [w for w in warnings if code_of(w) == "plan.columns.missing_code_suspected"]
    assert hit, warnings
    assert "prep.py" in hit[0], hit[0]


def test_prep改过之后不再拦() -> None:
    """**为什么在分析帧上现查，而不是把画像的字段传下去。**

    画像跑在原始数据上，分析跑在 `prep.py` 处理过的帧上。传字段只知道
    「原始数据里有」，不知道「处理之后还在不在」——作者已经把 99 改成缺失了，
    那就不该再拦。
    """
    from oss.core.analysis import validate_plan
    from oss.core.findings import code_of

    raw = _reg_frame()
    plan, prof = _reg_case(raw, {"age": {"99": "拒绝回答"}})
    prepped = raw.assign(age=raw["age"].replace(99, np.nan))
    problems, warnings = validate_plan(plan, prof, prepped)
    assert not [p for p in problems if code_of(p).startswith("plan.columns.missing_code")], problems
    assert not [w for w in warnings if code_of(w).startswith("plan.columns.missing_code")], warnings


def test_正当的99不拦也不警告() -> None:
    """99 分（0–100 连着）、99 岁（跟 98、97 连着）——干净双胞胎一条都不该响。"""
    from oss.core.analysis import validate_plan
    from oss.core.findings import code_of

    frame = _reg_frame(code=None)
    plan, prof = _reg_case(frame, controls=["age", "score"])
    problems, warnings = validate_plan(plan, prof, frame)
    assert not [x for x in [*problems, *warnings] if code_of(x).startswith("plan.columns.missing_code")]


def test_固定效应与聚类层级不查缺失码() -> None:
    """**编号为 99 的省份是正当取值。**固定效应里的 99 是一个类别号，不是一个数值。"""
    from oss.core.analysis import validate_plan
    from oss.core.findings import code_of

    rng = np.random.default_rng(32)
    frame = _reg_frame(code=None).assign(
        province=list(rng.integers(1, 31, 360)) + [99] * 40)
    plan, prof = _reg_case(frame, {"province": {"99": "拒绝回答"}},
                           controls=["age"], fixed_effects=["province"], cluster_level="province")
    problems, warnings = validate_plan(plan, prof, frame)
    assert not [x for x in [*problems, *warnings] if code_of(x).startswith("plan.columns.missing_code")]


def test_没给帧就不查_老调用照旧() -> None:
    from oss.core.analysis import validate_plan
    from oss.core.findings import code_of

    plan, prof = _reg_case(_reg_frame(), {"age": {"99": "拒绝回答"}})
    problems, warnings = validate_plan(plan, prof)
    assert not [x for x in [*problems, *warnings] if code_of(x).startswith("plan.columns.missing_code")]


def test_并段不许把类别号整片报成缺失() -> None:
    """**真实数据上量出来的误报**（`runs/r20260209_compulsory_edu`，政体类型）。

    取值是 1–5 与 9–12：9 在码表里、9–12 连成一段、段又贴着最大值，
    只按「整段跟其余取值比空档」判，占四成行的第 9 类会被报成缺失码。
    段最外面的 12 不是约定码——**哨兵岛的锚是 9999、99、−9 这类码**，不是类别号。
    """
    rng = np.random.default_rng(26)
    s = pd.Series(list(rng.choice([1, 2, 3, 4, 5], 1060))
                  + [9] * 1350 + [10] * 46 + [11] * 178 + [12] * 331)
    assert _sentinel_hits(s) == [], _sentinel_hits(s)


def test_并段要求空档比段本身宽() -> None:
    """{1,2} 之外的 5–9 是量表的一截，不是一撮代码：段跨度 4，空档只有 3。"""
    rng = np.random.default_rng(27)
    s = pd.Series(list(rng.choice([1, 2], 300))
                  + list(rng.choice([5, 6, 7, 8], 300)) + [9] * 100)
    assert _sentinel_hits(s) == [], _sentinel_hits(s)


def test_oss_run真跑一遍也拦得住(tmp_path) -> None:
    """**光有判据不算补上洞**——画像早就认得出，可是一直没有一处代码读它。

    这一条真跑 `oss run`：判据要真的被计划的门调到，帧要真的传进去。
    """
    import json

    import pytest
    pyreadstat = pytest.importorskip("pyreadstat")
    from oss.api import analysis as analysis_api
    from oss.api import errors
    from oss.api import profile as profile_api

    run = tmp_path / "r"
    run.mkdir()
    rng = np.random.default_rng(7)
    n = 400
    frame = pd.DataFrame({
        "income": rng.normal(50000.0, 12000, n).round(),
        "urban": rng.integers(0, 2, n).astype(float),
        "age": np.array(list(rng.integers(18, 86, n - 60)) + [99.0] * 60, dtype=float),
    })
    pyreadstat.write_sav(frame, str(run / "data.sav"),
                         variable_value_labels={"age": {99.0: "拒绝回答"}})
    profile_api.profile(str(run / "data.sav"), run=str(run))
    (run / "ideas.json").write_text(json.dumps({"direction": "x", "ideas": []},
                                               ensure_ascii=False), encoding="utf-8")
    (run / "plan.json").write_text(json.dumps({
        "plan_id": "p001", "idea_id": "i001", "estimator": "ols_fe",
        "outcome": "income", "treatment": "urban", "controls": ["age"]}), encoding="utf-8")

    with pytest.raises(errors.Refused) as caught:
        analysis_api.run(str(run))
    assert any("拒绝回答" in p for p in caught.value.problems), caught.value.problems


def test_标签太多的列画像存不下_改从数据文件拿() -> None:
    """**最该拦的那种列，画像里恰恰是空的。**

    `profile_table` 给每个变量留的取值标签有条数上限（`max_unique_for_labels`），
    职业码这种几百个标签的列超了限就一条不留——而哨兵码正是长在这种列上。
    所以标签先取分析这份文件自带的，没有才退回画像里记的。
    """
    from oss.core.analysis import validate_plan
    from oss.core.findings import code_of

    rng = np.random.default_rng(33)
    real = list(range(1000, 1060))                 # 60 个真编码：超过画像的 50 条上限
    frame = _reg_frame(code=None).assign(
        isco=list(rng.choice(real, 360)) + [9999] * 40)
    vl = {"isco": {str(c): f"职业{c}" for c in real} | {"9999": "不适用"}}
    plan, prof = _reg_case(frame, vl, controls=["age", "isco"])
    assert not prof.var("isco").value_labels, "前提：画像确实存不下这么多标签"

    _, warnings = validate_plan(plan, prof, frame)
    assert any(code_of(w) == "plan.columns.missing_code_suspected" for w in warnings), \
        "只有画像那份标签时，9999 只能按分布猜——警告"

    problems, _ = validate_plan(plan, prof, frame, vl)
    assert any(code_of(p) == "plan.columns.missing_code_labeled" for p in problems), \
        "数据文件自带标签时，「不适用」是原话——拒跑"


def test_作者已经把那些行筛掉就不再拦() -> None:
    """判的是**估计样本**：`sample_filter` 把带码的行排掉了，门就不该再响。"""
    from oss.core.analysis import validate_plan
    from oss.core.findings import code_of

    frame = _reg_frame()
    plan, prof = _reg_case(frame, {"age": {"99": "拒绝回答"}}, sample_filter="age != 99")
    problems, warnings = validate_plan(plan, prof, frame)
    assert not [x for x in [*problems, *warnings] if code_of(x).startswith("plan.columns.missing_code")]
