"""`oss run`：按 plan.json 跑分析。

**跑之前先校验计划里每个列名都在数据里存在。**列名写错不会报错，
回归照样跑，只是量错了东西——这是最贵的静默失败。

再往前还有一道：`plan.json` 若带 `design_locked`（用户用 `--- design:` 锁死的五个键的副本），
逐键跟计划本体比，不等就拒绝，**退出码 6**（跟 `oss route` 的「must 级处方没处置」同一个数：
两者都是「盘上有一条明摆着的、必须由人处置的冲突」）。这一条排在列名校验**前面**：
连计划自己都跟用户锁的对不上时，后面校验列名、跑回归、冻锚点全都是在为一个
**已经不是用户那个设计**的规格服务。

再往前还有一道：`plan.json` 读不进（pydantic 校验没过）时**报人话，不甩 traceback**。
说明书三处点名的那个最常见的错——把 `design_locked` 写成用户那套 `{"fe": …, "cluster": …}`——
从前会打一整屏英文 traceback：`--json` 那一侧根本不出 JSON，MCP 的 summary 是整段英文。
模型读不懂自己错在哪，只会再猜一次。现在逐条列出「哪个字段、什么毛病」，退出码 2（参数不对）。

走 `require_run` 而不是 `open_run`：`run` 是写的命令，但它要的是一个
**已经画过像、有计划**的目录。打错路径时把目录建出来，报的是
「缺 plan.json」——那句话把「目录错了」说成「这一步还没做」。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from pydantic import ValidationError

from oss.api.errors import BadInput, Missing, Refused
from oss.api.result import result
from oss.api.rundir import emit, read_json_file, require_run
from oss.api.sim import SIM_STATE
from oss.core import analysis as analysis_mod
from oss.core import datapacks as packs_mod
from oss.core import loop as loop_mod
from oss.core import plan_checks
from oss.core import route as route_mod
from oss.core import state as state_mod
from oss.core.contracts import ARTIFACTS, AnalysisPlan, BindingReport, DataProfile, TraceEvent
from oss.core.findings import code_of
from oss.core.loaders import load_any
from oss.gates import explore as explore_gate
from oss.gates import research_type as rt_gate


def _benchmark_frame(plan: AnalysisPlan, rundir) -> tuple[object | None, str]:
    """仿真类的真实对照数据：`(那份数据, 读不到时说的一句话)`。两样都没有就 `(None, "")`。

    `simulation.real_benchmark.data` 两种写法都认——**数据包名**（`oss data` 那套）与
    **运行目录里的文件名**，跟环节 4 的 `_benchmark_missing` 同一套解析。

    读不到要说一声：不说的话，方差比与关系方向那两条**静默不查**，
    而「核不了」不等于「过了」（这一批反复出现的同一条）。
    """
    spec = getattr(plan, "simulation", None)
    bench = getattr(spec, "real_benchmark", None) if spec is not None else None
    if bench is None or not bench.data.strip() or not bench.pairs:
        return None, ""
    name = bench.data.strip()
    path = packs_mod.source_file(name) if name in packs_mod.PACKS else rundir.file(name)
    try:
        return load_any(path).frame, ""
    except Exception as exc:                 # noqa: BLE001 —— 对照数据读不了不该挡住 oss run
        return None, (f"真实对照数据「{name}」读不进来（{type(exc).__name__}）——"
                      f"方差比与关系方向这两条**没查**。这不是「过了」，是「没核」。"
                      f"数据包先 `oss data get {name}`，或者把 real_benchmark.data 指到"
                      f"运行目录里那份文件")


#: 跟真实调查对账对不上的那两条编号——诊断那一行按它们拼（编号只写这一处）。
_FIDELITY_CODES = ("plan.simulation.variance_mismatch", "plan.simulation.relation_flipped")


def _fidelity_line(warnings: list[str], benchmark) -> str:
    """合成数据保真度写进 `results.diagnostics` 的那一行；没对过账就是空串。

    **对得上也要写一行**：诊断里空着跟「没对过账」长得一模一样，而这两件事差得远
    （这一批反复出现的同一条：「核不了」不等于「过了」）。
    """
    if benchmark is None:
        return ""
    bad = [w for w in warnings if code_of(w) in _FIDELITY_CODES]
    return "；".join(bad) if bad else "跟真实调查对过账：方差比与关系方向都对得上"


def _estimates(res) -> list[dict]:
    """系数表：散文与 JSON 两侧打的是同一份，顺序就是 `res.estimates` 的顺序。"""
    return [{"key": k, "term": e.term, "coef": e.coef, "se": e.se, "p": e.p}
            for k, e in res.estimates.items()]


def emit_explore(rundir, res, table, data: str, for_: str, probe_path: Path,
                 why: str, changed: list[str]) -> tuple[Path, int]:
    """探索结果落盘 + 留痕。**跟 `emit` 分开，因为它落的不是 ARTIFACTS 里的产出。**

    探索的每一份结果都打上 `plan_id = explore:<意见>#<第几次>`：
    有人把它拷成 results.json 时，这个戳跟着文件走，`paper check` 一眼认得出来。

    **不回显**——回显归门面（`_emit_explore` 原先在 CLI 里连回显一起做）。
    """
    n = explore_gate.used_count(rundir.path, for_) + 1
    out_dir = rundir.file("explore")
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{re.sub(r'[^0-9a-zA-Z_-]+', '_', for_)}_{n}.results.json"
    if out.exists():
        # 编号从留痕数出来，留痕被截断/删过就会撞号——**宁可拒绝也不能覆盖**：
        # 覆盖掉的是上一次探索的证据，而覆盖这件事本身不会留下任何痕迹
        raise Refused(f"探索结果已存在（trace 被截断？）：{out.name}", code=1)
    res.plan_id = f"explore:{for_}#{n}"
    res.save(out)
    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step="explore", command=f"oss run --explore --for {for_}",
        artifacts=[f"explore/{out.name}"],
        args={"for": for_, "n": n, "plan_sha": loop_mod.plan_sha(probe_path),
              "data": data, "data_sha256": table.sha256,
              "estimator": res.spec.estimator if res.spec else "",
              "changed": changed, "why": why},
        note=f"探索 {n}/{explore_gate.MAX_PER_TARGET}：n={res.n}，{len(res.estimates)} 个系数——不进论文"))
    return out, n


#: `design_locked` 的五个键 → `AnalysisPlan` 本体的同名字段。**键名一样**（这正是
#: `DesignLock` 用 AnalysisPlan 的字段名而不是用户那套 `fe` / `cluster` 的原因），
#: 但值的形状不一样：`fixed_effects` 是列表，其余四个是字符串。
DESIGN_KEYS = ("outcome", "treatment", "fixed_effects", "cluster_level", "estimator")


def _norm_lock(v) -> str | tuple[str, ...]:
    """比对前归一化：字符串去首尾空白；列表去空项、去每项首尾空白、**去重、排序**。

    固定效应是个集合——`firm+year` 与 `year+firm` 是同一个设计，`firm+firm+year`
    也是。拿一次无关紧要的差别去拦用户，只会教人把这道门关掉。**去重不能省**：
    `DesignLock.fixed_effects` 的文档串说的是「集合」，而重复项参与比对时
    `["unit","unit","year"]` 会跟 `["unit","year"]` 判成两个不同的设计——
    文档说一套、门判另一套，是最难查的那种不一致。
    """
    if isinstance(v, list):
        return tuple(sorted({str(x).strip() for x in v if str(x).strip()}))
    return str(v or "").strip()


def _shown(v) -> str:
    """归一化后的值怎么给人看。**空的一律是「—」**：原先空元组渲染成空串，
    报错里就成了「plan.json 写的是「」」——看不出是空着还是这行字丢了。
    """
    s = "+".join(v) if isinstance(v, (tuple, list)) else str(v or "")
    return s or "—"


def design_lock_problems(plan: AnalysisPlan) -> list[str]:
    """`plan.design_locked` 与 `plan` 本体逐键比对，返回逐条差异。没锁就是空列表。

    **空值 = 没锁**（空串、空列表）：`--- design:` 五个键都可缺省，缺省的由模型定。
    **一次报全五条**，不报一条就返回：改完一个再被拦一次，是把一次修改拆成五次往返。
    """
    lock = plan.design_locked
    if lock is None:
        return []
    out: list[str] = []
    for k in DESIGN_KEYS:
        want = _norm_lock(getattr(lock, k))
        if not want:
            continue                      # 这个键用户没锁，模型爱怎么定怎么定
        got = _norm_lock(getattr(plan, k))
        if got != want:
            out.append(f"{k}：plan.json 写的是「{_shown(got)}」，用户锁的是「{_shown(want)}」")
    return out


#: `design_locked` 抄错键名时补的那一句。**这是这条路上最常见的错**（说明书三处点名）：
#: 用户输入里写的是 `fe=` / `cluster=`，抄进 `plan.json` 得改成 `fixed_effects` /
#: `cluster_level`。只报「extra_forbidden」等于把映射表藏起来，让模型自己去猜哪个键名对。
LOCK_KEY_HINT = ("`design_locked` 只认 outcome / treatment / fixed_effects / cluster_level "
                 "/ estimator，`fe`/`cluster` 要按 `rules/input-and-rundir.md` 的映射表改名")


def plan_load_problems(exc: ValidationError) -> list[str]:
    """pydantic 的校验报错翻成逐条中文：`<字段路径> —— <类型>`。

    **只用 `loc` 与 `type`，不抄 `msg`**：`msg` 是英文散文（"Extra inputs are not
    permitted"），而这一行是要给模型照着改的——字段路径说改哪里，类型说什么毛病，
    两样都是机器写的、稳定的。整份文件读不出来（JSON 都不合法）时 `loc` 是空的，
    那就明说「（整份文件）」，不要留一个空的字段路径。

    **枚举字段要把合法取值带上**（`ctx.expected`，也是机器写的）。只说
    「description.kind —— literal_error」，看的人不知道能填什么，只能猜：
    2026-09-23 DiscoveryBench 一片 12 道题里有 3 道就死在这儿——猜成
    `proportions`、改三次、三次都不合法，一道题整个作废。
    """
    errs = exc.errors()
    out = []
    for e in errs:
        where = ".".join(str(x) for x in (e.get("loc") or ())) or "（整份文件）"
        line = f"{where} —— {e.get('type') or 'unknown'}"
        if expected := (e.get("ctx") or {}).get("expected"):
            line += f"（只能填 {expected}）"
        out.append(line)
    if any(e.get("type") == "extra_forbidden"
           and tuple(e.get("loc") or ())[:1] == ("design_locked",) for e in errs):
        out.append(LOCK_KEY_HINT)
    return out


def _cmd_line(explore: bool, for_id: str) -> str:
    """留痕里的 `command`——**人照着这一行就能重跑**。

    `--for` 在探索门之前可能还是空的，拼进去会留下尾随空格，所以逐段过滤后再拼。
    只定义一次：读不进计划、锁被改掉，两条拒绝路径打的是同一行。
    """
    return " ".join(filter(None, ("oss run", "--explore" if explore else "",
                                  f"--for {for_id}" if for_id else "")))


def run(run: str, *, data: str = "", explore: bool = False, for_id: str = "",
        explore_plan: str = "", why: str = "") -> dict:
    """跑一次分析。`explore=True` 时结果进 `explore/`，**不进 results.json**。"""
    rundir = require_run(run)
    plan_path = rundir.file(ARTIFACTS["plan"])
    if not plan_path.exists():
        raise Missing("plan", f"缺 {ARTIFACTS['plan']}——分析计划是 skill 写的，先让它产出",
                      code=1)
    try:
        plan = AnalysisPlan.load(plan_path)
    except ValidationError as exc:
        # 计划读不进是**参数不对**（退出码 2），不是门拒绝：盘上那份文件不合规格，
        # 没有任何判断可做。整屏 pydantic traceback 在这里等于没报错——
        # `--json` 那一侧不出 JSON，MCP 的 summary 是一整段英文
        problems = plan_load_problems(exc)
        summary = f"{ARTIFACTS['plan']} 读不进：" + "；".join(problems)
        rundir.append_trace(TraceEvent(
            seq=rundir.next_seq(), step="run", command=_cmd_line(explore, for_id),
            outcome="refused",
            args={"fields": [".".join(str(x) for x in (e.get("loc") or ()))
                             for e in exc.errors()]},
            note=summary))
        raise BadInput(summary, problems=problems) from exc
    # --plan 只在探索下有效。**默默忽略它比报错糟得多**：忘了 --explore 不是「少跑一次探索」，
    # 而是拿 plan.json 跑出一份正式结果、冻上锚点、推进阶段——而模型以为它看到的是探针的数字。
    if (explore_plan or for_id or why) and not explore:
        raise BadInput("--plan / --for / --why 只在 --explore 下有效；正式跑用 plan.json"
                       "（要改计划走 oss route --apply）", code=1)
    if explore and not explore_plan:
        raise BadInput("--explore 要配 --plan <探索计划文件>", code=1)

    # 用户锁的设计不许被模型静默改掉。**参数错先报、计划错后报**：上面两条说的是
    # 「这次调用本身写错了」，这一条说的是「盘上那份计划不对」，两件事不该混在一句话里。
    # **门比的是 `plan.json` 本体，从不比探针**：探索门的自由字段跟锁定的键有交集
    # （`fixed_effects`、`cluster_level`），探针把它们改掉这道门不拦。不拦是对的——
    # 探索产物只落 `explore/`，进不了 `results.json` 与论文；要把哪一次探索转正，
    # 必须先改 `plan.json`，那时这道门会拦。探索路径上仍跑这一条，是因为本体已经
    # 不是用户那个设计时，探索也只是在为一份错规格找证据。
    locked = design_lock_problems(plan)
    if locked:
        rundir.append_trace(TraceEvent(
            seq=rundir.next_seq(), step="design_lock", command=_cmd_line(explore, for_id),
            outcome="refused", args={"keys": [x.split("：")[0] for x in locked]},
            note="；".join(locked)))
        raise Refused("plan.json 改掉了用户锁定的设计，拒绝执行：", code=6, problems=[
            *locked,
            "用户锁的键只能由用户改（改输入里的 `--- design:` 再重来）；"
            "模型要换设计只能在没锁的键上找出路，找不到就按现有规则"
            "「穷尽 → 保留设计、降级为关联、逐条写进局限」"])

    # 研究类型门：类型随锚点冻结，只许在识别站不住时从因果降为描述（oss/gates/research_type.py）。
    # 跟设计锁同一族（退出码 6）：盘上那份计划改了不许模型自己改的东西
    rt_problems, rt_down = rt_gate.check(rundir.path, plan.research_type)
    if rt_problems:
        rundir.append_trace(TraceEvent(
            seq=rundir.next_seq(), step="research_type", command=_cmd_line(explore, for_id),
            outcome="refused", args={"plan": plan.research_type}, note="；".join(rt_problems)))
        raise Refused("plan.json 改了研究类型，拒绝执行：", code=6, problems=rt_problems)
    if rt_down and not rt_gate.already_recorded(rundir.path):
        rundir.append_trace(TraceEvent(
            seq=rundir.next_seq(), step=rt_gate.DOWNGRADE_STEP, command=_cmd_line(explore, for_id),
            args={"from": "causal", "to": "descriptive"}, note=rt_down))
    # 防作弊：标成非因果、研究问题本身却在问因果——计划阶段就拒（合规降级的除外）
    if plan.research_type and plan.research_type != "causal" and not rt_down:
        claims = rt_gate.causal_question(rundir.path, plan.idea_id)
        if claims:
            rundir.append_trace(TraceEvent(
                seq=rundir.next_seq(), step="research_type", command=_cmd_line(explore, for_id),
                outcome="refused", args={"plan": plan.research_type}, note="；".join(claims)))
            raise Refused("研究类型跟研究问题对不上，拒绝执行：", code=1, problems=claims)

    prof = DataProfile.load(rundir.file(ARTIFACTS["profile"]))
    base_plan = plan
    if explore:
        probe = AnalysisPlan.load(Path(explore_plan))
        gate_problems = explore_gate.validate(rundir.path, plan, probe, for_id, why)
        # 换规格是探索，换数据是另一件事：探针跑在别的文件上，两次的数字根本不可比，
        # 而留痕只会说「探索了一次」——这正是「用自己造的证据否证自己」的形状
        if data and Path(data).resolve() != Path(prof.file.path).resolve():
            gate_problems.append("探索只能换规格，不能换数据：--data 与 profile.json 记录的数据不同")
        if gate_problems:
            rundir.append_trace(TraceEvent(
                seq=rundir.next_seq(), step="explore", command="oss run --explore",
                outcome="refused", args={"for": for_id}, note="；".join(gate_problems)))
            raise Refused("探索检测未通过：", code=1, problems=gate_problems)
        plan = probe

    # 数据先读进来：计划的门里有一条（缺失值代码）判的是「码还在不在」，
    # 只有**分析真正要跑的这一份**说得准——profile.json 画的是 prep.py 之前的原始数据。
    # 读不进来就先不查那一条：数据读不了该报什么、在哪一步报，跟这次改动之前一字不差
    if not data:
        data = prof.file.path
    try:
        table = load_any(data)
    except Exception:                    # noqa: BLE001 — 原样留到下面那一步再抛
        table = None

    # 绑定给测量类的 M5 用：「这一列按代理处理了没有」只有 binding.json 说得出
    # （`status="proxy"` 与代理差距三栏）。没有、或老版本读不出来，就当没绑——那一条照判，
    # 只是没有东西能推翻「没按代理处理」
    bpath = rundir.file(ARTIFACTS["binding"])
    try:
        binding = BindingReport.load(bpath) if bpath.exists() else None
    except Exception:                    # noqa: BLE001 —— 读不出来的绑定不该挡住 oss run
        binding = None

    # 入库档案给仿真类的接轴门用：**盘上有没有货箱是事实**，而「这是不是仿真研究」只是一句声明。
    # 读不出来的档案当没有——论文期的 sim.state_broken 会单报那件事，这里不替它报
    spath = rundir.file(SIM_STATE)
    sim_state = read_json_file(spath) if spath.exists() else None
    if not isinstance(sim_state, dict):
        sim_state = None
    benchmark, bench_note = _benchmark_frame(plan, rundir)
    problems, warnings = analysis_mod.validate_plan(
        plan, prof, table.frame if table else None, table.value_labels if table else None,
        binding=binding, sim_state=sim_state, benchmark=benchmark,
        prelocked=loop_mod.prelocked_robustness(rundir.path))
    if bench_note:
        warnings.append(bench_note)
    if problems:
        rundir.append_trace(TraceEvent(
            seq=rundir.next_seq(),
            step="explore" if explore else "results",
            command=f"oss run --explore --for {for_id}" if explore else "oss run",
            outcome="refused", args={"for": for_id} if explore else {},
            note="；".join(problems)))
        raise Refused("分析计划过不了校验，拒绝执行：", code=1, problems=problems, data={
            "warnings": warnings,
            # 真跑时踩过：列明明加进数据了，报「不在数据里」——
            # 因为校验对着的是 profile.json，而那是**加列之前**画的。
            # 不提示这一层，人会对着数据文件反复核对列名，越核越糊涂。
            "stale_profile_hint": any("不在数据里" in x for x in problems)})

    # 计划体检（docs/design/2026-09-23-plan-check.md）：数据里现成、计划漏用的东西开 must 级处方，
    # 处置完才正式跑。**放在跑之前**：先定分析、再看结果，跟稳健性事前锁定同一个道理。
    # 探针不查——它只落 explore/，进不了结果与论文
    if not explore and table is not None:
        plan_checks.check(plan, table.frame, prof).save(rundir.file(plan_checks.ARTIFACT))
        held = [it for it in route_mod.build(rundir.path, save=False).pending_must()
                if it.id.startswith(plan_checks.PREFIX)]
        if held:
            lines = [f"{it.id}：{it.evidence}——{it.advice}" for it in held]
            rundir.append_trace(TraceEvent(
                seq=rundir.next_seq(), step="plan_check", command="oss run", outcome="refused",
                args={"items": [it.id for it in held]}, note="；".join(lines)[:500]))
            raise Refused("计划体检：数据里现成的东西计划没用上，处置完再跑：", code=6,
                          problems=lines, data={"warnings": warnings})

    try:
        if table is None:
            table = load_any(data)       # 上面吞掉的那个错，在这里照原样抛
        res = analysis_mod.run_plan(table.frame, plan)
        # 合成数据的保真度写进诊断：**处方那一步手上没有数据**（route 只拿计划、结果、
        # 画像、绑定），方差比与关系方向是这一步才算得出来的。走诊断这条路，
        # 跟「分批实施」「Bacon 分解」同一个办法
        if (fid := _fidelity_line(warnings, benchmark)):
            res.diagnostics["合成数据保真度"] = fid
    except analysis_mod.PlanRejected as exc:
        # 警告跟着异常走：散文那一侧它们本来就打在 ✗ 之前
        raise Refused(str(exc), code=1, data={"warnings": warnings}) from exc
    except (KeyError, ValueError) as exc:
        raise Refused(f"回归跑不起来：{type(exc).__name__}: {exc}", code=1,
                      data={"warnings": warnings}) from exc

    if explore:
        try:
            out, n = emit_explore(rundir, res, table, str(data), for_id, Path(explore_plan), why,
                                  explore_gate.diff(base_plan, plan))
        except Refused as exc:
            # 撞名那条拒绝也要带上警告：散文里 ⚠ 本来就打在 ✗ 之前，
            # 不带上就会在这一条路径上凭空少几行
            exc.data.setdefault("warnings", warnings)
            raise
        return result(f"探索 {n}/{explore_gate.MAX_PER_TARGET}：n={res.n}，"
                      f"{len(res.estimates)} 个系数——不进论文", {
            "run": str(rundir.path), "explore": True, "for": for_id, "n_used": n,
            "max_per_target": explore_gate.MAX_PER_TARGET, "path": str(out),
            "n": res.n, "estimates": _estimates(res), "warnings": warnings,
        })

    # 构造变量的脚本跟结果一起留痕：哪一版 prep.py 算出的这份数据。
    # 它是链上唯一没人核的计算，至少要能回答「当时是哪个文件」
    prep = rundir.file("prep.py")
    prep_sha = hashlib.sha256(prep.read_bytes()).hexdigest()[:12] if prep.exists() else ""
    note = f"{plan.plan_id or plan.idea_id}：n={res.n}，{len(res.estimates)} 个系数"
    # **稳健性项的名字要留痕。**「哪些是事前锁定的、哪些是看过结果才加的」原先
    # 分不出来——两者可信度完全不同（事前锁定的是承诺，事后加的是回应），
    # 而判据只能挂在作者的声明上，那正是这个项目反复堵的洞。
    # 记下每一次成功跑时清单里有哪些项，「事前锁定」就成了**算得出来的事实**：
    # 第一次成功跑分析时就在清单里的，才是事前锁定的。
    path = emit(rundir, "results", "oss run", "results", res, note=note, args={
        "plan_sha": loop_mod.plan_sha(plan_path), "plan_id": plan.plan_id, "data": str(data),
        "data_sha256": table.sha256, "estimator": plan.estimator, "prep_sha": prep_sha,
        "robustness": [r.label for r in plan.robustness]})

    # 状态落盘：跑出第一份结果就把原始问题冻住。**换题只能走 oss anchor switch。**
    # 上下文一压缩，模型就不记得「当初要研究什么」，于是顺着数据改题——盘上有锚点才拦得住。
    # 放在最末：回归跑完、结果都算出来了才算走到第 6 环，中途被拒的不该留下阶段指针。
    cur_anchor = state_mod.current_anchor(rundir.path)
    state_mod.freeze_anchor(rundir.path, **cur_anchor)
    # 换过题的运行：switch 当时新计划还没写，指纹留了空（先换题的话研究类型也空着），
    # 现在新计划真跑出结果了，补回去；同一道题第一次声明类型的那次跑也在这里锁定（用户
    # 2026-09-14 决定 2A：oss/core/state.py::refill_plan_sha 的「首次声明即锁」）。
    # `late` 是不是「看过结果才补」由 refill_plan_sha 自己算（判据只写那一处）——
    # 这里不再自己比对补格前后的锚点：两处各算一遍会漂，复审就是这么抓出
    # 「idea_id 对不上、什么都没补，却照样写了一行锁定」的（2026-09-14 二次复审）
    _, late = state_mod.refill_plan_sha(rundir.path, idea_id=cur_anchor["idea_id"],
                                        plan_sha=cur_anchor["plan_sha"],
                                        research_type=cur_anchor["research_type"])
    if late:
        rundir.append_trace(TraceEvent(
            seq=rundir.next_seq(), step=rt_gate.DECLARED_LATE_STEP, command="oss run",
            args={"research_type": cur_anchor["research_type"]},
            note="锚点冻结时计划没声明研究类型，这一次才写明——类型是在看过结果之后定的；从这一次起锁定"))
    state_mod.set_stage(rundir.path, "6-run")

    return result(note, {
        "run": str(rundir.path), "explore": False,
        "artifact": ARTIFACTS["results"], "path": str(path),
        "n": res.n, "estimates": _estimates(res),
        "diagnostics": dict(res.diagnostics),
        "event_study": [{"k": p.k, "coef": p.coef, "se": p.se} for p in (res.event_study or [])],
        "robustness": [
            {"label": label, "error": rb.error, "n": rb.n,
             "main": (None if rb.estimates.get("main") is None else
                      {"coef": rb.estimates["main"].coef, "se": rb.estimates["main"].se,
                       "p": rb.estimates["main"].p})}
            for label, rb in (res.robustness or {}).items()],
        "warnings": warnings,
    })
