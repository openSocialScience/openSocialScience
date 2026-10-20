"""仿真骨架 T3：接轴两道门——声明与货箱要对得上。

仿真跟另外三类不同：**链早就跑通了**（建箱 → 装箱 → 验货 → 入库，加论文期五道门），
缺的是研究类型那根轴没接上。这几条门就是那根轴：

- 声明为仿真 → 必须写仿真块、必须有入库的货箱
- 盘上有货箱 → 必须声明为仿真（**这一条对作者有利**，见门里的话）
- 要比较的条件 → 必须在货箱的配料单里

**判据优先看盘上有没有货箱**：那是事实，而「这是不是仿真研究」只是一句声明
（测量那一批九个绕行洞的教训：把门从一个声明字段挪到另一个声明字段不算修好）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from oss.core.analysis import validate_plan
from oss.core.contracts import AnalysisPlan, SimulationSpec
from oss.core.findings import code_of
from oss.core.profiler import profile_file

FIX = Path(__file__).parent / "fixtures"

SPEC = SimulationSpec(mechanism="邻里同质化偏好使住户迁出异质社区",
                      pattern="隔离指数随偏好阈值单调上升", stands_in_for_humans=False)


def _profile():
    return profile_file(FIX / "tiny.csv")


def _plan(**kw) -> AnalysisPlan:
    return AnalysisPlan(plan_id="p", idea_id="i1", estimator="ols_fe", outcome="income",
                        treatment="treat", **kw)


def _state(conditions: dict | None = None, **meta) -> dict:
    return {"box": "/tmp/box", "downgrade": False, "single_condition": False, "warnings": [],
            "meta": {"simulator": "oasis", "n_reps": 5,
                     "conditions": conditions if conditions is not None
                     else {"threshold_low": "阈值 0.2", "threshold_high": "阈值 0.5"}} | meta}


def _codes(plan, state=None):
    problems, warnings = validate_plan(plan, _profile(), sim_state=state)
    return {code_of(p) for p in problems}, {code_of(w) for w in warnings}


# ═══════════════════════ 一、S1：声明为仿真就必须写仿真块


def test_声明为仿真却不写仿真块_拒跑() -> None:
    problems, _ = _codes(_plan(research_type="simulation"), _state())
    assert "plan.simulation.spec_missing" in problems, problems


def test_把仿真搁进also也要写仿真块() -> None:
    """跟上一条同一个形状，只是换了一栏声明——先自建指数再估影响就是这种写法。"""
    problems, _ = _codes(_plan(research_type="causal", also=["simulation"]), _state())
    assert "plan.simulation.spec_missing" in problems, problems


def test_写了仿真块就不拦() -> None:
    problems, _ = _codes(_plan(research_type="simulation", simulation=SPEC), _state())
    assert "plan.simulation.spec_missing" not in problems, problems


def test_别的研究类型不受这一条影响() -> None:
    problems, _ = _codes(_plan(research_type="descriptive"), None)
    assert not {c for c in problems if c.startswith("plan.simulation")}, problems


# ═══════════════════════ 二、S1'：声明为仿真就必须有入库的货箱


def test_声明为仿真却没有货箱_拒跑() -> None:
    """把模拟输出当普通 CSV 直接喂进来，论文期那五道仿真门一道都不会响。"""
    problems, _ = _codes(_plan(research_type="simulation", simulation=SPEC), None)
    assert "plan.simulation.box_missing" in problems, problems


def test_没有货箱那条要说清五道门会静默() -> None:
    """处方要说清后果——只说「去入库」，作者不知道为什么非入不可。"""
    problems, _ = validate_plan(_plan(research_type="simulation", simulation=SPEC),
                                _profile(), sim_state=None)
    hit = [p for p in problems if code_of(p) == "plan.simulation.box_missing"]
    assert hit and "一道都不会响" in hit[0] and "oss sim import" in hit[0]


def test_有货箱就不拦() -> None:
    problems, _ = _codes(_plan(research_type="simulation", simulation=SPEC), _state())
    assert "plan.simulation.box_missing" not in problems, problems


# ═══════════════════════ 三、S2：有货箱就必须声明为仿真


def test_有货箱却没声明为仿真_拒跑() -> None:
    problems, _ = _codes(_plan(research_type="causal"), _state())
    assert "plan.simulation.undeclared" in problems, problems


def test_这一条要说清它对作者有利() -> None:
    """不声明成仿真，措辞检查不给「在本模型内」开豁免，作者反而会被因果措辞门误拦。"""
    problems, _ = validate_plan(_plan(research_type="causal"), _profile(), sim_state=_state())
    hit = [p for p in problems if code_of(p) == "plan.simulation.undeclared"]
    assert hit and "对你有利" in hit[0]
    # 三条出路都要给：主类型、also、把档案删掉
    assert "also" in hit[0] and "sim_state.json 删掉" in hit[0]


@pytest.mark.parametrize("kw", [{"research_type": "simulation"}, {"also": ["simulation"]}])
def test_两栏哪一栏声明了都算(kw: dict) -> None:
    problems, _ = _codes(_plan(simulation=SPEC, **kw), _state())
    assert "plan.simulation.undeclared" not in problems, problems


def test_没货箱也没声明_一条都不响() -> None:
    problems, warnings = _codes(_plan(research_type="causal"), None)
    assert not {c for c in problems | warnings if c.startswith("plan.simulation")}


# ═══════════════════════ 四、要比较的条件得在货箱里


def test_要比较的条件不在货箱里_拒跑() -> None:
    """打错一个字母不会有任何别的地方报错，论文里那句比较就是凭空的。"""
    spec = SimulationSpec(mechanism="x", pattern="y",
                          conditions_compared=["threshold_low", "threshold_hihg"], stands_in_for_humans=False)
    problems, _ = validate_plan(_plan(research_type="simulation", simulation=spec),
                                _profile(), sim_state=_state())
    hit = [p for p in problems if code_of(p) == "plan.simulation.condition_unknown"]
    assert hit, problems
    assert "threshold_hihg" in hit[0] and "threshold_high" in hit[0], "要把货箱里有哪些条件列出来"


def test_条件名都对得上_不拦() -> None:
    spec = SimulationSpec(mechanism="x", pattern="y",
                          conditions_compared=["threshold_low", "threshold_high"], stands_in_for_humans=False)
    problems, _ = _codes(_plan(research_type="simulation", simulation=spec), _state())
    assert "plan.simulation.condition_unknown" not in problems, problems


def test_货箱的配料单没有条件那一栏_不冤枉() -> None:
    """核不了就别报。"""
    spec = SimulationSpec(mechanism="x", pattern="y", conditions_compared=["whatever"], stands_in_for_humans=False)
    problems, _ = _codes(_plan(research_type="simulation", simulation=spec), _state(conditions={}))
    assert "plan.simulation.condition_unknown" not in problems, problems


# ═══════════════════════ 五、货箱里不止一个条件却一个都没声明：只警告


def test_货箱里两个条件而计划一个都没声明_警告() -> None:
    problems, warnings = _codes(_plan(research_type="simulation", simulation=SPEC), _state())
    assert "plan.simulation.conditions_undeclared" in warnings, warnings
    assert not {c for c in problems if c == "plan.simulation.conditions_undeclared"}


def test_单条件的货箱不报这一条() -> None:
    _, warnings = _codes(_plan(research_type="simulation", simulation=SPEC),
                         _state(conditions={"baseline": "只有这一条"}))
    assert "plan.simulation.conditions_undeclared" not in warnings, warnings


def test_声明了要比较哪几条就不报() -> None:
    spec = SimulationSpec(mechanism="x", pattern="y", conditions_compared=["threshold_low"], stands_in_for_humans=False)
    _, warnings = _codes(_plan(research_type="simulation", simulation=spec), _state())
    assert "plan.simulation.conditions_undeclared" not in warnings, warnings


# ═══════════════════════ 六、入口漏洞：数据本身长得像仿真输出
#
# 上面四条都得先有一句声明或一份档案。**不声明、不入库、把 states.csv 当普通 CSV 直接跑**，
# 一条都不响——这是整个骨架唯一的入口。判据只认数据的列，不认任何声明。


def _sim_frame(**extra):
    import pandas as pd
    from oss.core import simbox
    cols = {k: [1, 2] for k in simbox.STATE_KEYS}
    return pd.DataFrame(cols | {"income": [1.0, 2.0], "treat": [0, 1]} | extra)


def test_四根钥匙列齐了却既没声明也没入库_拒跑() -> None:
    problems, _ = validate_plan(_plan(research_type="causal"), _profile(),
                                frame=_sim_frame(), sim_state=None)
    hit = [p for p in problems if code_of(p) == "plan.simulation.output_undeclared"]
    assert hit, problems
    assert "全部绕过去" in hit[0] and "oss sim import" in hit[0]


def test_少一根钥匙列就不认_宁可漏认也不冤枉() -> None:
    import pandas as pd
    from oss.core import simbox
    partial = pd.DataFrame({k: [1, 2] for k in simbox.STATE_KEYS[:-1]} | {"income": [1.0, 2.0]})
    problems, _ = validate_plan(_plan(research_type="causal"), _profile(),
                                frame=partial, sim_state=None)
    assert "plan.simulation.output_undeclared" not in {code_of(p) for p in problems}, problems


def test_已经声明为仿真的不重复报这一条() -> None:
    """声明了就走 ①②③ 那几条，这一条是给「什么都没声明」的情形兜底的。"""
    problems, _ = validate_plan(_plan(research_type="simulation", simulation=SPEC), _profile(),
                                frame=_sim_frame(), sim_state=_state())
    assert "plan.simulation.output_undeclared" not in {code_of(p) for p in problems}, problems


def test_入库过的不报这一条() -> None:
    problems, _ = validate_plan(_plan(research_type="simulation", simulation=SPEC), _profile(),
                                frame=_sim_frame(), sim_state=_state())
    assert "plan.simulation.output_undeclared" not in {code_of(p) for p in problems}, problems


def test_没有帧的时候不报() -> None:
    problems, _ = validate_plan(_plan(research_type="causal"), _profile(),
                                frame=None, sim_state=None)
    assert "plan.simulation.output_undeclared" not in {code_of(p) for p in problems}, problems


# ═══════════════════════ 七、合成数据的方差被压扁（设计稿 4.3 甲）
#
# 一百个小人回答同一道题，分布比真人窄得多（Bisbee 2024）。拿它当调查数据用，
# 标准误会假小、显著性会假强——而**验货只查结构，一个统计量都不看**。
#
# 门槛 0.95 是拿真实数据量出来、2026-09-19 用户拍板的：GSS 七万五千份问卷里
# 取值数 2–10 的量表题 4880 道，众数占比中位数 52.7%、95 分位 95.6%——
# 按 0.95 划线，真人题目里 5.6% 会被提醒。


def _frame(vals, col="income"):
    import pandas as pd
    return pd.DataFrame({col: vals, "treat": [0, 1] * (len(vals) // 2)})


def test_一列里九成五答同一个值_提醒() -> None:
    _, warnings = validate_plan(_plan(research_type="simulation", simulation=SPEC), _profile(),
                                frame=_frame([4] * 96 + [3, 5, 2, 1]), sim_state=_state())
    hit = [w for w in warnings if code_of(w) == "plan.simulation.degenerate_column"]
    assert hit, warnings
    assert "方差会被压扁" in hit[0] and "96%" in hit[0]
    # 处方要给出路，不能只描述病
    assert "real_benchmark" in hit[0] and "局限里写明" in hit[0]


def test_刚好压线以下不报_误报要可控() -> None:
    """真人问卷里本来就有大量合法的偏态题——误报多了人会整片忽略这条警告。"""
    _, warnings = validate_plan(_plan(research_type="simulation", simulation=SPEC), _profile(),
                                frame=_frame([4] * 90 + [3] * 10), sim_state=_state())
    assert not [w for w in warnings if code_of(w) == "plan.simulation.degenerate_column"], warnings


def test_没有货箱就不查这一条() -> None:
    """门控是第一原则：普通调查数据里的偏态题不该被这条打扰。"""
    _, warnings = validate_plan(_plan(research_type="causal"), _profile(),
                                frame=_frame([4] * 96 + [3, 5, 2, 1]), sim_state=None)
    assert not [w for w in warnings if code_of(w) == "plan.simulation.degenerate_column"], warnings


def test_整列一个值不在这里报_归画像管() -> None:
    """再报一遍是冗余——画像的 constant_columns 早就认出来了。"""
    _, warnings = validate_plan(_plan(research_type="simulation", simulation=SPEC), _profile(),
                                frame=_frame([4] * 100), sim_state=_state())
    assert not [w for w in warnings if code_of(w) == "plan.simulation.degenerate_column"], warnings


def test_行太少判不动_不报() -> None:
    _, warnings = validate_plan(_plan(research_type="simulation", simulation=SPEC), _profile(),
                                frame=_frame([4, 4, 4, 4, 4, 4, 4, 4, 3, 5][:8]), sim_state=_state())
    assert not [w for w in warnings if code_of(w) == "plan.simulation.degenerate_column"], warnings


def test_不看计划声明了什么_只看数据长什么样() -> None:
    """数据的形状是事实。仿真块没写也照查——测量那一批九个洞的同一条教训。"""
    _, warnings = validate_plan(_plan(research_type="simulation"), _profile(),
                                frame=_frame([4] * 96 + [3, 5, 2, 1]), sim_state=_state())
    assert [w for w in warnings if code_of(w) == "plan.simulation.degenerate_column"], warnings


def test_门槛的经验依据写在定数旁边() -> None:
    """**校准定数不许是魔法数字**：它是拿真实数据量出来、由用户拍板的，依据要写在旁边。"""
    from oss.core import sim_checks
    src = Path(sim_checks.__file__).read_text(encoding="utf-8")
    assert sim_checks.DEGENERATE_MODE_SHARE == 0.95
    assert "GSS" in src and "4880" in src and "5.6%" in src, "经验依据没写在定数旁边"


# ═══════════════════════ 八、跟真实调查对账（设计稿 4.3 乙）
#
# Argyle 等（2023）的「算法保真度」是拿合成样本跟真实调查比分布；Bisbee（2024）补了一刀：
# **只比分布相关不够，方差会被压扁**。这里两样都查——方差比、关系方向。
#
# 区间 [0.70, 1.43] 是拿真实数据量出来、2026-09-19 用户拍板的：GSS 2018 组与 2022 组
# （两批不同的真人）答同一套题，454 道可比的量表题里标准差之比中位数 0.99、
# 5–95 分位 0.88–1.09——**真人跟真人之间其实很稳**。按这个区间划线，两批真人里
# 只有 1.5% 会落在区间外。


def _bench_spec(pairs, stands=True):
    from oss.core.contracts import RealBenchmark
    return SimulationSpec(mechanism="x", pattern="y", stands_in_for_humans=stands,
                          real_benchmark=RealBenchmark(data="real.csv", pairs=pairs))


def _two(syn_vals, real_vals, syn2=None, real2=None):
    import pandas as pd
    syn = {"income": syn_vals, "treat": [0, 1] * (len(syn_vals) // 2)}
    real = {"a33": real_vals}
    if syn2 is not None:
        syn["edu_years"], real["a10"] = syn2, real2
    return pd.DataFrame(syn), pd.DataFrame(real)


def _warn(spec, frame, bench):
    _, warnings = validate_plan(_plan(research_type="simulation", simulation=spec), _profile(),
                                frame=frame, sim_state=_state(), benchmark=bench)
    return {code_of(w): w for w in warnings}


def test_方差被压扁_要报() -> None:
    import numpy as np
    rng = np.random.default_rng(0)
    syn, real = _two(list(rng.normal(0, 0.4, 200)), list(rng.normal(0, 1.0, 200)))
    hit = _warn(_bench_spec([("income", "a33")]), syn, real)
    assert "plan.simulation.variance_mismatch" in hit, hit
    w = hit["plan.simulation.variance_mismatch"]
    assert "压扁" in w and "0.99" in w, "要把真人之间的实际分布摆出来，作者才知道这个数有多不正常"


def test_方差被夸大_也报() -> None:
    """**两头都报**：压扁是 Bisbee 的发现，夸大同样说明这批小人不像真人。"""
    import numpy as np
    rng = np.random.default_rng(1)
    syn, real = _two(list(rng.normal(0, 3.0, 200)), list(rng.normal(0, 1.0, 200)))
    hit = _warn(_bench_spec([("income", "a33")]), syn, real)
    assert "夸大" in hit.get("plan.simulation.variance_mismatch", ""), hit


def test_方差差不多_不报() -> None:
    import numpy as np
    rng = np.random.default_rng(2)
    syn, real = _two(list(rng.normal(0, 1.0, 400)), list(rng.normal(0, 1.0, 400)))
    assert "plan.simulation.variance_mismatch" not in _warn(
        _bench_spec([("income", "a33")]), syn, real)


def test_关系方向反了_要报() -> None:
    """**分布再像也没用**——条件关系反了，说明这批小人的行为逻辑跟真人不是一回事。"""
    import numpy as np
    rng = np.random.default_rng(3)
    x = rng.normal(size=300)
    syn, real = _two(list(x), list(x), syn2=list(-x + rng.normal(0, .1, 300)),
                     real2=list(x + rng.normal(0, .1, 300)))
    hit = _warn(_bench_spec([("income", "a33"), ("edu_years", "a10")]), syn, real)
    assert "plan.simulation.relation_flipped" in hit, hit


def test_关系方向一致_不报() -> None:
    import numpy as np
    rng = np.random.default_rng(4)
    x = rng.normal(size=300)
    syn, real = _two(list(x), list(x), syn2=list(x + rng.normal(0, .1, 300)),
                     real2=list(x + rng.normal(0, .1, 300)))
    assert "plan.simulation.relation_flipped" not in _warn(
        _bench_spec([("income", "a33"), ("edu_years", "a10")]), syn, real)


def test_配对里有一边不在数据里_说清哪一边缺() -> None:
    """不猜、不跳过——「核不了」不等于「过了」。"""
    syn, real = _two([1.0] * 20 + [2.0] * 20, [1.0] * 20 + [2.0] * 20)
    hit = _warn(_bench_spec([("income", "nope")]), syn, real)
    assert "nope" in hit.get("plan.simulation.benchmark_column_missing", ""), hit


def test_没配对照列就不查_不主张代表真人的ABM不受打扰() -> None:
    """很多 ABM 本来就不主张小人等于真人——逼这类研究去找真人数据是错的。"""
    import numpy as np
    rng = np.random.default_rng(5)
    syn, real = _two(list(rng.normal(0, 0.1, 200)), list(rng.normal(0, 1.0, 200)))
    hit = _warn(SPEC, syn, real)
    assert not [c for c in hit if c.startswith("plan.simulation.variance")], hit


def test_配了对照列却把开关关着_照查() -> None:
    """自相矛盾就照查。**少填一栏就免检是零代价的**——这一批只堵得了这一半。"""
    import numpy as np
    rng = np.random.default_rng(6)
    syn, real = _two(list(rng.normal(0, 0.3, 200)), list(rng.normal(0, 1.0, 200)))
    hit = _warn(_bench_spec([("income", "a33")], stands=False), syn, real)
    assert "plan.simulation.variance_mismatch" in hit, hit


def test_对不上账要开must级处方() -> None:
    """处方这一步手上没有数据——判据在 oss run 那一步算完写进诊断，route 读它。"""
    from oss.core.route import _simulation_items
    items = _simulation_items({"合成数据保真度": "合成的「income」跟真实的「a33」方差压扁了"})
    assert len(items) == 1 and items[0].priority == "must"
    assert "stands_in_for_humans 改成 false" in items[0].advice, "撤回主张这条出路要写出来"


def test_对得上账不开处方_但诊断里要留一行() -> None:
    """「对过账、对得上」跟「没对过账」长得不一样，这一点要留在留痕里。"""
    from oss.core.route import _simulation_items
    assert _simulation_items({"合成数据保真度": "跟真实调查对过账：方差比与关系方向都对得上"}) == []
    assert _simulation_items({}) == []


def test_区间与级别的依据写在定数旁边() -> None:
    from oss.core import sim_checks
    src = Path(sim_checks.__file__).read_text(encoding="utf-8")
    assert sim_checks.VARIANCE_RATIO_BAND == (0.70, 1.43)
    assert sim_checks.BENCHMARK_LEVEL == "must"
    assert "0.99" in src and "1.5%" in src and "GSS" in src, "经验依据没写在定数旁边"


# ═══════════════════════ 九、接线：处方真的从 `_rules` 里出得来
#
# 上一轮合并踩过的坑：函数写好了、调用处没接上，自动合并照样成功。
# 这一条走真正的入口（`route._rules`），不直接叫那个函数。


def test_处方从真正的入口出得来_不是只有函数在(tmp_path: Path) -> None:
    from oss.core import route as route_mod
    from oss.core.contracts import AnalysisResults, Estimate, SpecSummary

    res = AnalysisResults(
        plan_id="p", n=100,
        estimates={"main": Estimate(term="x", coef=0.1, se=0.02, p=0.001)},
        spec=SpecSummary(estimator="describe"),
        diagnostics={"合成数据保真度": "合成的「income」跟真实的「a33」方差压扁了：标准差之比 0.31"})
    items = route_mod._rules(_plan(research_type="simulation", simulation=SPEC),
                             res, None, [])
    hit = [i for i in items if i.id == "simulation_fidelity"]
    assert hit, [i.id for i in items]
    assert hit[0].priority == "must"


def test_没有结果时也不崩(tmp_path: Path) -> None:
    from oss.core import route as route_mod
    assert not [i for i in route_mod._rules(_plan(research_type="simulation", simulation=SPEC),
                                            None, None, []) if i.id == "simulation_fidelity"]
