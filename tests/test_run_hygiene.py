"""真跑一次暴露出来的四条「安静地错」。

这四条的共同形状：**没有任何东西会报错，看起来一切正常。**
一条一条单看都像小事，合起来构成同一类风险——
留痕、帮助文字、敏感性表、装的代码，全是**用来判断可不可信的元信息**。
元信息错了，比结果错了更难发现，因为没人会去核它。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from oss.core.power import GRAY_BANDS, sensitivity
from oss.core.contracts import PowerReport, TraceEvent
from oss.host.base import RunDir

REPO = Path(__file__).resolve().parents[1]


# ------------------------------------------------- ① 敏感性表的第一列


def test_扫学科灰区时报的是学科名不是序号():
    """表上打出「取 3 时翻为 fail」时，那个 3 其实是 psychology。

    读者会把它当成一个参数取值去理解——而敏感性分析存在的全部意义
    就是让人**看得懂「改哪个参数会翻盘」**。标错了列，这张表反过来误导人。
    实测记录：审稿模型正是被这一列误导，提了一条基于误读的意见。
    """
    table, notes, _ = sensitivity(0.05, 6000, metric="beta", n_clusters=2000,
                                  discipline="sociology")
    first_col = [row[0] for row in table["gray_band"]]
    assert set(first_col) == set(GRAY_BANDS), "第一列必须是学科名"
    assert all(isinstance(v, str) for v in first_col)

    band_note = next(n for n in notes if "灰区" in n)
    assert not any(f"取 {i} 时" in band_note for i in range(len(GRAY_BANDS))), \
        "不许把序号当成取值打出来"


def test_学科是类别所以列举不取区间():
    """`communication–sociology` 这种区间读不通。类别只能列举。"""
    _, notes, _ = sensitivity(0.06, 400, metric="d", discipline="sociology")
    band_note = next(n for n in notes if "灰区" in n)
    if "会翻盘" in band_note:
        assert "–" not in band_note.split("当前")[-1], "类别不该被写成区间"


def test_契约允许第一列是字符串():
    """契约把第一列限死成数字，代码就只好存序号——**契约逼出了那个 bug**。"""
    table, _, _ = sensitivity(0.05, 6000, metric="beta", discipline="sociology")
    PowerReport(idea_id="i1", effect_size_raw=0.05, effect_size_adjusted=0.03,
                n_raw=6000, power=0.6, verdict="gray",
                sensitivity_table=table)          # 不抛就算过


# ------------------------------------------------- ② 留痕的流水号


def test_流水号跨进程接着数(tmp_path):
    """每个 `oss xxx` 都是独立进程。

    原先流水号是进程内计数器，于是**每一条留痕的 seq 都是 1**——
    真跑一次 40 多步，40 多个 1。留痕的用处就是事后复原谁先谁后，
    全是 1 等于这个字段不存在，**而它看起来又像是存在的**。
    """
    for i in range(3):
        rd = RunDir(root=tmp_path, run_id="r1")     # 每次都是新对象，模拟新进程
        rd.append_trace(TraceEvent(seq=rd.next_seq(), step="profile",
                                   command=f"oss x{i}", outcome="ok"))
    seqs = [json.loads(l)["seq"]
            for l in (tmp_path / "r1" / "trace.jsonl").read_text().splitlines() if l.strip()]
    assert seqs == [1, 2, 3]


def test_同一进程内也接着数(tmp_path):
    rd = RunDir(root=tmp_path, run_id="r2")
    assert [rd.next_seq() for _ in range(3)] == [1, 2, 3]


# ------------------------------------------------- ③ 帮助文字不许手写


def test_run的帮助文字要跟支持的估计量对得上():
    """帮助文字过期比没有帮助文字更糟。

    原先写死成「只支持 OLS + 固定效应」，而工具箱早已支持五种。
    下一个模型读到那句就不会去用那四种因果方法——
    **能力装好了，却因为一句旧文案而用不上。**
    """
    from oss.core.analysis import SUPPORTED_ESTIMATORS

    # **终端够宽才看得见全部。**帮助文字是从 `SUPPORTED_ESTIMATORS` 拼的，
    # 清单一长，默认 80 列就把它截成「……」，于是这条测试报的是
    # 「帮助文字没提 describe」——而真相是帮助文字是全的、只是被终端截了。
    # 加第十一个估计量（iptw）时正好撞上。
    #
    # **还要把「强制当终端」的变量摘掉。**typer 看到 GITHUB_ACTIONS / FORCE_COLOR /
    # PY_COLORS 就让 rich 强行按终端渲染，而 TERM=dumb 的终端 rich 一律当 80 列，
    # COLUMNS 根本不看——于是本地全绿、GitHub Actions 上年年报「帮助文字没提 describe」。
    env = {k: v for k, v in os.environ.items()
           if k not in ("GITHUB_ACTIONS", "FORCE_COLOR", "PY_COLORS")}
    env.update({"COLUMNS": "400", "TERMINAL_WIDTH": "400", "TERM": "dumb"})
    out = subprocess.run([sys.executable, "-m", "oss.cli.main", "run", "--help"],
                         capture_output=True, text=True, cwd=REPO, env=env)
    text = (out.stdout + out.stderr).replace("\n", "")
    for est in SUPPORTED_ESTIMATORS:
        assert est in text, f"帮助文字没提 {est}"


# ------------------------------------------------- ④ 装的是哪份代码


def test_doctor_会查装的和仓库是不是同一份():
    """**这一条排在 doctor 最前面，因为它错了后面每一条都不算数。**

    真跑一次时发现：PATH 上的 `oss` 加载的是几个月前一份非可编辑安装，
    分析模块 294 行（仓库 1299 行）、一个因果估计量都没有。
    链条照跑、产出照出、doctor 全绿——**近半年的工作从命令行完全够不着。**

    而它验不出来：`python -c "import oss"` 会先看见当前目录里的仓库，
    给出一个假的通过；真正的命令行脚本不把当前目录放进搜索路径。
    **查的地方跟跑的地方不是同一个，等于没查。**
    """
    out = subprocess.run([sys.executable, "-m", "oss.cli.main", "doctor"],
                         capture_output=True, text=True, cwd=REPO)
    lines = [l for l in out.stdout.splitlines() if "加载的是哪份代码" in l]
    assert lines, "doctor 必须报告加载的是哪份代码"
    assert lines[0].startswith("✓"), lines[0]
    assert str(REPO / "oss") in lines[0]


def test_装错时这道门必须真的响():
    """**只测得到通过的那一支，等于没测这道门。**

    上一条只能证明「现在装对了」。装错时会不会报警是另一回事——
    而那恰恰是这道门存在的全部理由。
    """
    from oss.cli.main import _code_provenance

    ok, detail = _code_provenance(pkg=Path("/opt/py/site-packages/oss"),
                                  repo=REPO / "oss")
    assert not ok
    assert "不是同一份" in detail
    assert "site-packages" in detail and str(REPO / "oss") in detail
    assert "-e" in detail, "报警必须带上怎么修"


# ------------------------------------------------- ⑤ 说明书铺过去了没有


def test_说明书改了没重铺要报警(tmp_path):
    """**「铺过了」不等于「铺的是当前这一版」。**

    跟「装的代码是旧的」是同一类陷阱，而且更隐蔽：模型读的永远是全局那份，
    改了源头忘了重铺**改动一行都不生效**，而所有检查都会通过——
    因为源头确实改对了。说明书已经改过 22 次，只要有一次忘了重铺，
    后面每一次真跑验的都是旧说明书，**验出来的问题还会被当成新问题去修**。
    """
    from oss.cli.main import _skills_drift

    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "s1").mkdir(parents=True)
    (dst / "s1").mkdir(parents=True)
    (src / "s1" / "SKILL.md").write_text("新版", encoding="utf-8")
    (dst / "s1" / "SKILL.md").write_text("新版", encoding="utf-8")
    assert _skills_drift(src, dst)[0], "一样就该过"

    (src / "s1" / "SKILL.md").write_text("改了一行", encoding="utf-8")
    ok, detail = _skills_drift(src, dst)
    assert not ok and "已过期 s1" in detail
    assert "install-skills" in detail, "报警必须带上怎么修"


def test_压根没铺过的说明书也要报出来(tmp_path):
    from oss.cli.main import _skills_drift

    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "s2").mkdir(parents=True)
    (src / "s2" / "SKILL.md").write_text("x", encoding="utf-8")
    dst.mkdir()
    ok, detail = _skills_drift(src, dst)
    assert not ok and "没铺 s2" in detail


# ------------------------------------------------- ⑥ 图里有、文件里没有


def test_写了交互项没声明分箱要在诊断里说出来():
    """交互项 `a × b` 的全部含义就是「a 的斜率随 b **线性**变化」。

    那是一个假定，分箱是它的实际形状。计划里有交互项却没声明分箱，
    `oss run` 不该闷声跳过——**等到写完论文才发现，代价是整节重写**。

    不自动补算：「没在计划里声明就不跑」是事前锁定的规矩，
    工具悄悄加一项没声明的分析，这条规矩就废了。
    """
    import numpy as np
    import pandas as pd
    from oss.core.analysis import run_plan
    from oss.core.contracts import AnalysisPlan

    rng = np.random.default_rng(3)
    n, t = 120, 3
    df = pd.DataFrame({
        "pid": np.repeat(np.arange(n), t),
        "wave": np.tile(np.arange(1, t + 1), n),
        "x": rng.normal(size=n * t),
    })
    df["w2"] = (df.wave == 2).astype(float)
    df["y"] = 0.3 * df.x - 0.2 * df.x * df.w2 + rng.normal(size=n * t)

    base = dict(plan_id="p", idea_id="i", estimator="ols_fe", outcome="y",
                treatment="x", fixed_effects=["pid"], cluster_level="pid",
                interactions=["x*w2"])

    res = run_plan(df, AnalysisPlan(**base))
    assert not res.binned
    assert "分箱" in res.diagnostics, "没算就要说没算"
    assert "没算" in res.diagnostics["分箱"] and "binned" in res.diagnostics["分箱"]

    # 补上声明之后这条必须消失——**警报天天响就等于没有警报**
    ok = run_plan(df, AnalysisPlan(**base, binned={"by": "wave", "bins": 3}))
    assert ok.binned
    assert "分箱" not in ok.diagnostics


def test_没有交互项时不该报分箱():
    import numpy as np
    import pandas as pd
    from oss.core.analysis import run_plan
    from oss.core.contracts import AnalysisPlan

    rng = np.random.default_rng(4)
    df = pd.DataFrame({"pid": np.repeat(np.arange(60), 2),
                       "x": rng.normal(size=120)})
    df["y"] = 0.3 * df.x + rng.normal(size=120)
    res = run_plan(df, AnalysisPlan(plan_id="p", idea_id="i", estimator="ols_fe",
                                    outcome="y", treatment="x", fixed_effects=["pid"]))
    assert "分箱" not in res.diagnostics


def test_论文拿分箱当证据而文件里没有就拒绝():
    """**被跨模型审稿抓到两次的同一个问题。**

    第一次原话：「分箱估计被指定为『读者应以分箱图为准』的形状依据，
    但 results.json 中没有分箱估计的输出，读者无法核实。」
    第二次是在完全独立的一次运行里——不同题目、不同数据、不同执行模型。
    **同一个坑踩两次，说明缺的是一道门，不是一次疏忽。**
    """
    from oss.core.contracts import AnalysisResults, BinnedSlope, Estimate
    from oss.core.manuscript import _binned_without_record

    empty = AnalysisResults(plan_id="p", n=10,
                            estimates={"main": Estimate(term="x", coef=1, se=1, p=0.01)})

    hits = _binned_without_record("分箱斜率诊断显示，斜率呈非单调形态。", empty)
    assert hits and "只存在于图片里" in hits[0]
    assert "plan.json" in hits[0] and "删掉这个断言" in hits[0], "拦下来要给出路"

    有 = empty.model_copy(update={"binned": {
        "全样本": [BinnedSlope(group="全样本", bin_index=1, center=0, slope=1, se=1, n=5)]}})
    assert not _binned_without_record("分箱斜率诊断显示，斜率呈非单调形态。", 有)


def test_只是提到分箱不算拿它当证据():
    """光提到「分箱」不拦——「本文未做分箱估计」是正当写法。

    **警报天天响就等于没有警报。**
    """
    from oss.core.contracts import AnalysisResults, Estimate
    from oss.core.manuscript import _binned_without_record

    empty = AnalysisResults(plan_id="p", n=10,
                            estimates={"main": Estimate(term="x", coef=1, se=1, p=0.01)})
    for text in ("本文未做分箱估计，因此不对函数形式作断言。",
                 "分箱估计是一种验证函数形式的手段。",
                 "样本量不足，分箱估计无法执行。"):
        assert not _binned_without_record(text, empty), text


def test_这次真跑的论文会被这道门拦下来():
    """归档时正是靠这道门发现的：论文两处拿分箱当形状依据，而结果文件里是空的。

    夹具来自 `docs/runs/`，那个目录不随仓库分发（只在本机留着），所以取不到就跳过。
    """
    if not (REPO / "docs" / "runs" / "r20260824_relinc-results.json").exists():
        pytest.skip("docs/runs/ 不在这份检出里")
    from oss.core.contracts import AnalysisResults
    from oss.core.manuscript import _binned_without_record

    run = REPO / "docs" / "runs"
    res = AnalysisResults.load(run / "r20260824_relinc-results.json")
    paper = (run / "r20260824_relinc-paper.md").read_text(encoding="utf-8")
    assert not res.binned
    hits = _binned_without_record(paper, res)
    assert hits and "非单调" in hits[0]


# ------------------------------------------------- ⑦ 量纲被误判成共线


def _panel(n_units=40, t=8, pop_scale=1.0):
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(9)
    df = pd.DataFrame({
        "unit": np.repeat(np.arange(n_units), t),
        "year": np.tile(np.arange(t), n_units),
    })
    df["x"] = rng.normal(size=len(df))
    df["z"] = rng.normal(size=len(df))
    # 人口：跨九个数量级，像真数据一样
    df["pop"] = rng.uniform(9e3, 1.4e9, len(df)) * pop_scale
    df["y"] = 0.5 * df.x - 0.3 * df.z + rng.normal(size=len(df))
    return df


def _fit(df, controls):
    from oss.core.analysis import run_plan
    from oss.core.contracts import AnalysisPlan

    return run_plan(df, AnalysisPlan(
        plan_id="p", idea_id="i", estimator="ols_fe", outcome="y", treatment="x",
        controls=controls, fixed_effects=["unit", "year"], cluster_level="unit"))


def test_跨九个数量级的列不该被判成共线():
    """**真跑一次时踩到的**：`pop` 从九千跨到十四亿，`X'X` 元素跨十八个数量级，

    条件数 7.28e15——`matrix_rank` 的容差吞掉两个真实维度，满秩矩阵判成 3/5。
    系统随后报「某某与某某完全共线」，**点的是无辜的变量**，
    而使用者会照着提示删掉一个本该留下的控制变量。

    「点名了却归错因，比不说更糟」这条纪律，被自己的容差破坏了。
    """
    res = _fit(_panel(), ["z", "pop"])
    assert res.n > 0, "不该被拒"
    assert "量纲" in res.diagnostics
    assert "`pop`" in res.diagnostics["量纲"]
    assert "不要为此删变量" in res.diagnostics["量纲"] or "删掉本该留下" in res.diagnostics["量纲"]


def test_标准化只改刻度不改估计值():
    """**换算是精确的，不是近似。**`β = β_s / 尺度`。

    这条不成立的话，上一条修好的就不是 bug 而是换了个错法。
    """
    import numpy as np

    a = _fit(_panel(pop_scale=1.0), ["z", "pop"])
    b = _fit(_panel(pop_scale=1e-6), ["z", "pop"])      # 人口换成「百万人」
    assert np.isclose(a.estimates["main"].coef, b.estimates["main"].coef, rtol=1e-6)
    assert np.isclose(a.estimates["main"].se, b.estimates["main"].se, rtol=1e-6)
    # 人口自己的系数按刻度换算，恰好差 1e6 倍。
    # **这条以前会失败**，因为系数一律 round 到 6 位小数——
    # 原刻度下的真实系数是 1e-11，存进去变成 `0.0`，
    # 读起来就是「完全没有效应」，而且看不出是舍入造成的。
    assert a.estimates["pop"].coef != 0.0, "极小的真实系数不能被舍成 0"
    assert np.isclose(a.estimates["pop"].coef * 1e6, b.estimates["pop"].coef, rtol=1e-4)


def test_取整要同时保底小数位和有效数字():
    """只按小数位 → 1e-11 变 0.0；只按有效数字 → −1.1320851 变 −1.13209，比原来还粗。"""
    from oss.core.analysis import _sig

    assert _sig(-1.1320851809790873) == -1.132085      # 小数位保底 6
    assert _sig(-1.1e-11) == -1.1e-11                  # 有效数字保底 6
    assert _sig(0.0) == 0.0


def test_量纲正常时不报量纲():
    """**警报天天响就等于没有警报。**"""
    res = _fit(_panel(pop_scale=1e-9), ["z", "pop"])
    assert "量纲" not in res.diagnostics


def test_真的共线还是要拦住():
    """标准化之后**仍然**不满秩，那就是真的共线——不能因为修了量纲就放行。"""
    import pytest as _pytest
    from oss.core.analysis import PlanRejected

    df = _panel()
    df["x2"] = df.x * 2.0                     # 完全共线
    with _pytest.raises(PlanRejected):
        _fit(df, ["z", "x2"])


def test_列范数不能用标准差代替():
    """第一版用标准差、`sd <= 0` 时换成 1。**那个保护不够。**

    两阶段最小二乘投影后常数列的标准差是 4.7e-17——不是 0，只是接近 0，
    于是被除以 4.7e-17、放大到 1e16，条件数从 3 变成 8.1e32。
    **一个用来修数值灾难的改动，自己制造了同一类灾难。**
    """
    import numpy as np
    from oss.core.analysis import _col_scale

    X = np.column_stack([np.full(500, 1.0) + 1e-17 * np.random.default_rng(1).normal(size=500),
                         np.random.default_rng(2).normal(size=500)])
    scale = _col_scale(X)
    assert scale[0] > 1.0, "常数列的尺度必须是个健康的数，不能是 4.7e-17"
    Xs = X / scale
    assert np.linalg.cond(Xs.T @ Xs) < 1e6, "标准化不该把条件数搞坏"


# ------------------------------------------------- ⑧ 分箱对着乘积算斜率


def test_主处理是交互项时分箱必须点名分量():
    """`binned.treatment` 留空 → 用主处理变量。而主处理变量是**一个乘积**。

    「乘积的分箱斜率」算得出来但没有实质含义，**而它长得跟真的一样**：
    真跑一次时初稿把 4.144/0.238/0.070 写成「民主的斜率」，
    补上 treatment 重跑才拿到 7.700/6.190/3.059/2.831/2.404。
    **是审稿模型抓到的，不是这道门拦下的。**
    """
    import pytest as _pytest
    from oss.core.analysis import PlanRejected, run_plan
    from oss.core.contracts import AnalysisPlan

    df = _panel()
    base = dict(plan_id="p", idea_id="i", estimator="ols_fe", outcome="y",
                treatment="x*z", controls=["x", "z"], interactions=["x*z"],
                fixed_effects=["unit"], cluster_level="unit")

    with _pytest.raises(PlanRejected, match="是一个交互项"):
        run_plan(df, AnalysisPlan(**base, binned={"by": "z"}))

    ok = run_plan(df, AnalysisPlan(**base, binned={"by": "z", "treatment": "x", "bins": 3}))
    assert ok.binned, "点名分量之后就该算得出来"
