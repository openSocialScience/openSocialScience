"""`oss sim init / check / pack / import / doctor`：仿真货箱的收货口。

设计稿 docs/design/2026-08-31-sim-intake.md 第三、五节的落地。
退出码在这一层写死，说明书按它分支：

    0  init/check/import：验货通过（可含警告——降级在 data）；pack：装箱完成；
       doctor：**总是 0**——体检是报告不是门，没过的条目看 data.checks
    1  init 撞已存在的箱子、货箱名撞到已存在的非目录、货箱名是空的；运行目录不存在；
       配料单不是合法 JSON；doctor 不认识的模拟器；
       pack 不认识的模拟器、装箱失败（PackError 原话透传）、档案读不进
    2  货箱目录或必需文件不存在——**不是拒收，是路径错了**
    7  验货拒收（must 级缺陷，problems 逐条点名）

`import` 只在验货通过后落 `sim_state.json` 并留痕；拒收的箱子一个字节都不进运行目录——
凭空造出来的通过是最坏的一种（`require_run` 的同一条教训）。
用户给的箱子路径一律先 expanduser + resolve：`sim_state.json` 是要跨会话交接的档案，
存相对路径等于把状态藏进 cwd（RunDir「状态全在文件里」的原则）。
"""
from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path

from oss.api.errors import ApiError, BadInput, Missing, Refused
from oss.api.result import result
from oss.api.rundir import require_run
from oss.core import envfile
from oss.core import simbox
from oss.core.contracts import TraceEvent
from oss.sims import get_adapter, KNOWN

#: init 生成的空白配料单：必填项齐全、值全空，人只管填不用记名单
_BLANK_META = {k: ({} if k == "conditions" else "") for k in simbox.META_REQUIRED}

#: 入库档案的文件名。V2 的说明书按这个名字读，别各写各的字面量
SIM_STATE = "sim_state.json"


def init(box: str) -> dict:
    """生成空白配料单与运行清单。箱子已存在就拒绝——不覆盖别人的货。"""
    if not str(box).strip():
        raise BadInput("要给货箱目录起个名字", code=1)
    p = Path(box).expanduser().resolve()
    if p.is_dir():
        raise BadInput(f"货箱已存在：{p}", code=1)
    if p.exists():
        raise BadInput(f"{p} 已存在且不是目录", code=1)
    p.mkdir(parents=True)
    # 2026-08-31 质量审：raw/ 顺手建好——跑单另存、样本档案 cp 进来都落这个目录，
    # 建箱这一步就把它建好，说明书的命令块才能直接复制粘贴，不用先手动 mkdir。
    (p / "raw").mkdir()
    (p / "sim.meta.json").write_text(
        json.dumps(_BLANK_META, ensure_ascii=False, indent=2), encoding="utf-8")
    (p / "runs.csv").write_text("file,condition,rep,complete\n", encoding="utf-8")
    return result(
        f"空白货箱已建：{p}——先填 sim.meta.json 的必填项；"
        f"跑完把跑脚本（根目录 runner.py 或 raw/ 下的 *.py，二选一）与 "
        f"states.csv/events.csv 放进来、runs.csv 逐跑登记，再验货",
        {"box": str(p), "meta_required": list(simbox.META_REQUIRED),
         "also_required": ["runner.py 或 raw/*.py（二选一）", "states.csv 或 events.csv",
                           "runs.csv 逐跑登记"]})


def _verdict(box: Path) -> simbox.BoxVerdict:
    try:
        return simbox.check_box(box)
    except FileNotFoundError as exc:
        raise Missing("simbox", str(exc), code=2) from exc
    except ValueError as exc:
        # 手填 JSON 的尾逗号、全角引号：报人话不报 traceback（eaf4960 的同一条规矩）
        raise BadInput(str(exc), code=1) from exc


def check(box: str) -> dict:
    """验货。拒收抛 Refused(7)，problems 逐条；通过时降级/单条件/配料单进 data。"""
    p = Path(box).expanduser().resolve()
    v = _verdict(p)
    if v.problems:
        raise Refused(f"验货拒收：{len(v.problems)} 处必须修", code=7, problems=v.problems)
    warn = f"，{len(v.warnings)} 条警告" if v.warnings else ""
    return result(f"验货通过{warn}", {
        "box": str(p), "warnings": v.warnings, "meta": v.meta,
        "downgrade": v.downgrade, "single_condition": v.single_condition,
    })


def import_box(box: str, *, run: str) -> dict:
    """入库：验货通过后写 `sim_state.json`、留痕。运行目录必须已存在（不存在退 1）。"""
    rundir = require_run(run)
    res = check(box)               # 拒收在这里就抛出去了
    d = res["data"]
    state = {"box": d["box"], "downgrade": d["downgrade"],
             "single_condition": d["single_condition"], "warnings": d["warnings"],
             "meta": d["meta"]}
    (rundir.file(SIM_STATE)).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step="sim", command="sim import",
        args={"box": d["box"]}, artifacts=[SIM_STATE]))
    return result(f"货箱已入库：{d['box']}", {"run": str(rundir.path)} | d)


def pack(simulator: str, *, box: str) -> dict:
    """走插头装箱。插头抛 PackError 一律翻成 code=1，原话透传。"""
    try:
        mod = get_adapter(simulator)
    except ValueError as exc:
        raise ApiError(str(exc), code=1) from exc
    p = Path(box).expanduser().resolve()
    if not p.is_dir():
        raise Missing("simbox", f"货箱目录不存在：{p}", code=2)
    try:
        report = mod.pack(p)
    except mod.PackError as exc:
        raise ApiError(f"装箱失败：{exc}", code=1) from exc
    return result(f"装箱完成：状态 {report['n_state_rows']} 行、事件 {report['n_event_rows']} 行",
                  {"box": str(p)} | report)


def _profile(simulator: str) -> dict:
    p = Path(__file__).resolve().parent.parent / "sims" / simulator / "profile.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as exc:
        # 档案随包分发，坏了是发布事故——但体检命令不该死在自己的数据文件上
        raise ApiError(f"{simulator} 的档案 profile.json 坏了：{exc}", code=1) from exc
    if not isinstance(data, dict):
        raise ApiError(
            f"{simulator} 的档案 profile.json 顶层必须是一个对象 {{…}}，不是列表或字符串",
            code=1)
    return data


def _relay_check(simulator: str) -> dict:
    """③网关变量在：查跑单**启动时**真正要的东西，不是上游包文档里的名字。

    两家跑单都只认 `BASE`/`KEY` 这一对环境变量名（AgentSociety 的跑单启动时
    把它们映射成 AGENTSOCIETY_LLM_API_BASE/API_KEY 再喂 CLI 子进程——预先
    export AGENTSOCIETY_* 并不能让跑单起来：它见不到 BASE/KEY 照样拒跑，
    起来了也会用自己的映射值覆盖）。所以 agentsociety 这条按三档如实报：
    BASE/KEY 已 export=直接能跑；只有坐标可抄（AGENTSOCIETY_LLM_*、
    REVIEWER_BASE_URL 或哪一档 `.env` 找得到，见 oss/core/envfile.py）=✓
    但注明还差一步 export；全无=✗。跟 OASIS 那条一样**不查值对不对**——
    ✓ 不等于中转站真配对了。OASIS 沿用老口径（.env 或 REVIEWER_BASE_URL），
    `.env` 的找法统一到 `envfile.candidates()` 的三档（cwd 向上 / 用户级
    `~/.config/oss/.env` / 源码检出）——sim-oasis.md §五、
    sim-agentsociety.md 对应描述已经跟着改。
    """
    name = "网关变量在"
    dotenv_near = bool(envfile.candidates())
    if simulator != "agentsociety":
        has_env = bool(os.getenv("REVIEWER_BASE_URL")) or dotenv_near
        return {"name": name, "ok": has_env,
                "note": "找到 .env 或环境变量" if has_env else "没找到 .env（cwd 向上 / 用户级 / 源码检出都没有）"}
    if os.getenv("BASE") and os.getenv("KEY"):
        return {"name": name, "ok": True,
                "note": "BASE/KEY 已 export——跑单启动时会映射成 "
                        "AGENTSOCIETY_LLM_API_BASE/API_KEY"}
    if (os.getenv("AGENTSOCIETY_LLM_API_KEY") and os.getenv("AGENTSOCIETY_LLM_API_BASE")) \
            or os.getenv("REVIEWER_BASE_URL") or dotenv_near:
        return {"name": name, "ok": True,
                "note": "坐标找得到（AGENTSOCIETY_LLM_*/REVIEWER_*/.env 之一）——"
                        "跑之前还得照它 export BASE/KEY，跑单只认这两个名"}
    return {"name": name, "ok": False,
            "note": "BASE/KEY 没 export，AGENTSOCIETY_LLM_*/REVIEWER_* 也没设，"
                    "也没找到 .env（cwd 向上 / 用户级 / 源码检出都没有）——网关坐标无处可抄"}


def doctor(simulator: str) -> dict:
    """体检：包版本对不对、样本数据在不在、网关变量找得到吗。只报事实，不修。"""
    if simulator not in KNOWN:
        raise ApiError(f"不认识的模拟器「{simulator}」，现有插头：{'、'.join(KNOWN)}", code=1)
    prof = _profile(simulator)
    checks = []
    try:
        ver = importlib.metadata.version(prof["pip_package"])
        ok = ver == prof["version_locked"]
        note = ver if ok else f"装的是 {ver}，档案锁定 {prof['version_locked']}——行为可能已变"
    except importlib.metadata.PackageNotFoundError:
        ok, note = False, f"{prof['pip_package']} 未装进当前 Python 环境"
    checks.append({"name": "包可导入且版本相符", "ok": ok, "note": note})
    # 样本按各插头自持的 sample_base 解析（停车场 5：从前基路径写死
    # ~/Documents/oasis，AgentSociety 靠相对上跳借道，OASIS 克隆一缺席它就
    # 误败——一个插头的体检不许依赖另一个插头的安装）。sample_base 随
    # profile.json 分发、~ 照展开；sample_data 若哪天写成绝对路径，
    # pathlib 的 / 会自然让它盖过基路径，不用另写分支。
    sample = Path(prof["sample_base"]).expanduser() / prof["sample_data"]
    checks.append({"name": "样本数据在", "ok": sample.exists(),
                   "note": str(sample) if sample.exists() else f"{sample} 不在——先照安装单下载"})
    checks.append(_relay_check(simulator))
    bad = [c for c in checks if not c["ok"]]
    return result(("体检通过" if not bad else f"体检 {len(bad)} 条没过"), {
        "simulator": simulator, "version_locked": prof["version_locked"], "checks": checks})
