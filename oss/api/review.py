"""`oss review`：叫一个别家的模型来审。

契约：只传路径不传摘要、每次开新线程、executor 与 reviewer **优先不同家族**。
同家族或判不出家族时照审，判词记为「自审」、论文必须写进局限；
`.env` 设 `REQUIRE_CROSS_MODEL=1` 才拒绝执行（正式验收跑用）。

**`reviewer=` 是整个 api 层唯一的网络接缝。**不给就现造一个
`oss.host.reviewer.Reviewer()`（它自己读环境变量与 `.env`）；测试给自己的假货，
MCP 给自己那一个。这样一来：

* `oss/api/` 的测试**一次网都不连**——不连网不是靠 monkeypatch 猜对了哪个名字，
  而是因为这一层根本没有别的地方会去建连接；
* **审稿 key 永远不作为参数传递**（提案 §十一 的悬案，这里定成这样）——
  它只从服务器进程的环境里来，不经过任何一次工具调用的参数。

`data["log"]` 是**边走边打的那四行**（`[stream, text]` 二元组）：
「综合第 N 轮的 M 份报告」「⚠ 缺 …，这几项审稿人核不了」「第 N 轮复审：带上…」
「⚠ 网关超时…降档重试」。它们原先是 `typer.echo` 现打的——被后面某一步拒绝时，
它们那时候已经在屏幕上了。攒进 `problems` 一起打会把顺序改掉，所以走 log
（跟 `figure` / `paper export` 同一套办法）：门面先按序打 log，再打判词块。

只读命令的规矩这里也照办：走 `require_run`，打错路径要拒绝，不许顺手建一个
空目录出来——凭空造出来的运行目录会让轮次门在一张白纸上说「当前决定：continue」。
"""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

from oss.api.errors import BadInput, Missing, Refused, Unavailable
from oss.api.paper import results_digest
from oss.api.result import result
from oss.api.rundir import read_json_file, require_run
from oss.core import causal_wording
from oss.core import loop as loop_mod
from oss.core import state as state_mod
from oss.core.contracts import ARTIFACTS, TraceEvent
from oss.gates import research_type as rt_gate
from oss.host.reviewer import FOLLOWUP_KINDS, ReviewerUnavailable, SameFamilyRefused

_LEVELS = ("quick", "normal", "deep")


def _alive(pid: int) -> bool:
    """这个进程还在吗。信号 0 只做权限与存在性检查，不打扰对方。

    **`PermissionError` 要判「在」，不是「不在」**——它的意思是进程确实存在、
    只是属于别的用户，我们没权限给它发信号。判成「不在」的话，锁会当着一个
    活着的审稿的面放行第二个，而这道锁存在的全部理由就是拦住那一个。
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@contextmanager
def _inflight(out: Path, log: list):
    """同一份判词不许同时跑两个。**这道锁是给钱包的，不是给文件的。**

    验收跑 r20260907_edufert_zh 的实录：一次 `--level deep` 的审稿被放进后台，
    执行者分不清它是死了还是还在跑，于是每隔一两分钟 `ls` + `pgrep` 探一次，
    探不准就再起一个——**12 分钟里同一份审稿起了 4 次，每一次都真调 API、真花钱**，
    而只有最后一次的结果落了盘。改成前台跑之后，后面几轮一次都没重复。

    锁记 PID：进程没了就是陈旧的锁（上一次被 Ctrl-C 或被杀），直接接管，
    不让一把死锁把人卡住——**卡住比重复更糟，重复只是费钱**。
    """
    lock = out.with_suffix(".lock")
    if lock.exists():
        try:
            info = json.loads(lock.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            info = {}
        pid = int(info.get("pid") or 0)
        if pid and pid != os.getpid() and _alive(pid):
            age = int(time.time() - float(info.get("started") or time.time()))
            raise Refused(
                f"已经有一个同样的审稿在跑（PID {pid}，已跑 {age} 秒）——没落盘不等于死了。",
                code=3,
                problems=[
                    f"等它写出 {out.name} 再往下；**别再起一个**，重复起只是重复花钱",
                    "长档审稿要几分钟到十几分钟，**不要放后台**——分不清死活正是这个坑的来源",
                ],
                data={"log": log, "pid": pid, "age_seconds": age, "out": out.name})
        log.append(["out", f"  接管一把陈旧的锁（PID {pid or '?'} 已不在）"])
    lock.write_text(json.dumps({"pid": os.getpid(), "started": time.time()}),
                    encoding="utf-8")
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _round_of(d: dict) -> int:
    """报告里记的轮次。**读不出来就算第 1 轮。**

    `round` 是写报告时落进 JSON 的，手工改过的运行目录里见过 "2"、null、甚至一句话。
    `int()` 直接抛会让整条复审链因为一个坏字段崩掉，而它想问的只是「是不是上一轮的」。
    """
    try:
        return int(d.get("round") or 1)
    except (TypeError, ValueError):
        return 1


def with_downgrade(rev, files, level: str, kind: str, emphasis: str,
                   round_: int = 1, previous: list[str] | None = None,
                   log: list | None = None):
    """网关超时（HTTP 524 / 5xx）就**自动降一档重试一次**，并说出来。

    验收跑的 12 个失败点里 9 个是 524。执行者每次都要手动降档重跑——
    三位互盲审稿人就要手动重跑三次。这是整条链最常撞的墙，不该靠人绕。

    只降一档、只重试一次：降到 quick 还超时就是后端真的不可用，
    那时照旧 fail closed——**降档重试不是把「审不了」变成「通过」的通道**。

    `log` 是唯一的新东西：原先这里直接 `typer.echo` 那句降档提示，现在把它塞进
    日志列表，由门面去打。**位置在判词之前，顺序跟原来一样。**签名的其余部分
    （五个位置参数、`round_` / `previous`、返回判词）一个字都不许换：
    `tests/test_whole_output.py` 直接调 `oss.cli.main._review_with_downgrade`。
    """
    # 第 1 轮不带这两个参数。`followup_brief()` 在第 1 轮返回空串，带上它们对提示词
    # 一点影响都没有，却会让只实现了老签名的审稿后端当场 TypeError。
    # **老用法连调用签名都不变**——跟「第 1 轮的提示词一个字都没变」是同一条规矩。
    extra = ({"round_": round_, "previous_findings": previous}
             if (round_ >= 2 and kind in FOLLOWUP_KINDS) else {})
    try:
        return rev.review(files, level=level, kind=kind, emphasis=emphasis, **extra)
    except ReviewerUnavailable as exc:
        msg = str(exc)
        gateway = "HTTP 524" in msg or "HTTP 502" in msg or "HTTP 503" in msg or "HTTP 504" in msg
        if not gateway or level not in _LEVELS or level == "quick":
            raise
        lower = _LEVELS[_LEVELS.index(level) - 1]
        if log is not None:
            log.append(["err", f"  ⚠ 网关超时（{msg[:12]}），自动从 {level} 降到 {lower} 重试一次"])
        return rev.review(files, level=lower, kind=kind, emphasis=emphasis, **extra)


def _default_reviewer(rundir=None):
    """没注入就现造一个。**这是这一层唯一会连网的地方**，所以它只有一行、只在这里。

    运行目录要传下去：执行者身份的第三顺位是这个目录里 run_state.json 记下的。
    MCP 的 oss_review 走的正是这条路——不传的话它永远问不到第三顺位。
    """
    from oss.host.reviewer import Reviewer
    return Reviewer(rundir=rundir)


def review(run: str, *, files: list[str] | None = None, level: str = "normal",
           kind: str = "generic", emphasis: str = "", round_: int = 1,
           identification: bool = False, paper: bool = False, desk: bool = False,
           alternatives: bool = False, editor: bool = False,
           extra_round: bool = False, why: str = "", reviewer=None) -> dict:
    """叫一个别家的模型来审。

    **审论文要用 `paper=True`。**只把 `paper.md` 交出去审不了要紧的问题：
    「稳健性是不是事后补的」得对着 `plan.json` 里事前锁定的清单看，
    「局限写全没有」得对着 `power.json` 与 `literature.json` 的 notes 看。
    多文件是机械拼接原文，**不做摘要**——一总结就带上了调用方的口径。
    """
    rundir = require_run(run)
    log: list[list[str]] = []
    # 轮次门。事故：状态 exhausted，模型仍审到第 5 轮；另一条绕法是重跑 --round 2 覆盖报告。
    # **两者都由程序拒绝**：超过上限的轮号，或循环已耗尽还要再审论文。
    # 桌审 / 编辑综合 / 因果审计不算审稿轮次（loop.py 的 _NOT_PAPER_REVIEW），它们的 --round 只是选择器，不拦。
    if not (desk or editor or identification):
        # 注意：loop_status() 会顺手重写 loop_state.json（拒绝时也会）。内容是确定性重算的，无害。
        loop_now = loop_mod.loop_status(rundir.path)
        # 「耗尽」是「不许再开一轮」，不是「本轮不许再审」——最后一轮的第 2、3 位
        # 互盲审稿人还没回来时状态已经是 exhausted。**审全了才算没事可做。**
        rs = loop_now.rounds
        covered_now = bool(rs) and rs[-1].n_reviews >= max(r.n_reviews for r in rs)
        exhausted_now = (loop_now.decision["decision"] == loop_mod.DECISION_EXPORT_OPEN
                         and covered_now)
        if round_ > loop_mod.MAX_ROUNDS or exhausted_now:
            if not (extra_round and why.strip()):
                reason = (f"第 {round_} 轮超过上限 {loop_mod.MAX_ROUNDS}" if round_ > loop_mod.MAX_ROUNDS
                          else f"循环已耗尽（{loop_now.round}/{loop_now.max_rounds}）")
                rundir.append_trace(TraceEvent(
                    seq=rundir.next_seq(), step="review", command=f"oss review --round {round_}",
                    outcome="refused", args={"round": round_, "max_rounds": loop_mod.MAX_ROUNDS,
                                             "decision": loop_now.decision["decision"]},
                    note=reason))
                raise Refused(
                    f"拒绝：{reason}。轮次耗尽后不再复审——"
                    f"当前决定：{loop_now.decision['decision']}｜{loop_now.decision['reason']}。"
                    f"确需再审：--extra-round --why \"<理由>\"",
                    code=4, data={"log": log, "round": round_,
                                  "max_rounds": loop_mod.MAX_ROUNDS,
                                  "decision": loop_now.decision})
            rundir.append_trace(TraceEvent(
                seq=rundir.next_seq(), step="review",
                command=f"oss review --round {round_} --extra-round",
                args={"round": round_, "why": why}, note=f"超上限加审：{why}"))
    files = list(files or [])
    rendered = rundir.file("paper_rendered.md")
    if desk:
        kind = "desk"
        if level == "normal":
            level = "quick"                      # 桌审就该快
        if not rendered.exists():
            raise Missing("paper_rendered", "缺 paper_rendered.md——先跑 oss paper render",
                          code=1, data={"log": log})
        files = [str(rendered)] + files
    if alternatives:
        # 替代解释审稿人是三位互盲审稿人之一：材料跟 --paper 一样，提示词不一样
        paper = True
        if not emphasis:
            emphasis = "替代解释：主要结果还能被什么解释，每条附一个手头数据能做的检查"
    if editor:
        kind = "editor"
        if not rendered.exists():
            raise Missing("paper_rendered", "缺 paper_rendered.md——先跑 oss paper render",
                          code=1, data={"log": log})
        reports = sorted(
            p for p in rundir.path.glob("review_*.json")
            if loop_mod.paper_review_round(p) == round_
        )
        if not reports:
            raise Missing("reviews", f"第 {round_} 轮没有任何审稿报告可综合——先让审稿人审",
                          code=1, data={"log": log})
        files = [str(rendered), *map(str, reports)] + files
        log.append(["out", f"  综合第 {round_} 轮的 {len(reports)} 份报告："
                           f"{'、'.join(p.name for p in reports)}"])
    if identification:
        kind = "identification"
        wanted = [rundir.file(ARTIFACTS["plan"]), rundir.file(ARTIFACTS["results"])]
        missing = [w.name for w in wanted if not w.exists()]
        if missing:
            raise Missing("plan/results", f"缺 {'、'.join(missing)}——先跑 oss run",
                          code=1, data={"log": log})
        files = [str(w) for w in wanted] + files
    if paper:
        kind = "alternatives" if alternatives else "paper"
        # 语料原文有几十万字节，而审论文只用得到它的诊断字段。
        # **整个发出去会网关超时，而超时看起来像后端坏了。**
        # 这里做的是**机械投影**（挑字段），不是摘要——不引入任何判断。
        lit = rundir.file(ARTIFACTS["literature"])
        digest = rundir.file("literature_digest.json")
        if lit.exists():
            raw = json.loads(lit.read_text(encoding="utf-8"))
            digest.write_text(json.dumps(
                {k: v for k, v in raw.items() if k != "records"}
                | {"n_records": len(raw.get("records", []))},
                ensure_ascii=False, indent=2), encoding="utf-8")
        # results.json 同理：15 项稳健性 × 逐期系数把它撑到 59 KB，整包 100 KB，
        # 审稿后端一半的调用网关超时或返回空正文。**机械投影**：每项稳健性只留
        # main、n、error、诊断——审稿人核的是「话对不对」，用不到每项的逐期系数。
        res_digest = rundir.file("results_digest.json")
        res_path = rundir.file(ARTIFACTS["results"])
        if res_path.exists():
            res_digest.write_text(json.dumps(
                results_digest(json.loads(res_path.read_text(encoding="utf-8"))),
                ensure_ascii=False, indent=1), encoding="utf-8")
        wanted = [
            rundir.file("paper_rendered.md"),
            rundir.file(ARTIFACTS["plan"]),
            res_digest if res_digest.exists() else res_path,
            rundir.file(ARTIFACTS["power"]),
            digest,
        ]
        # 因果审计的报告也给审稿人：FAIL 不再拦导出，改成**必须写进局限**——
        # 「写进去没有」就得有人对着审计报告核，那是「局限写全没有」这位审稿人的活
        audit = rundir.file("review_plan.json")
        if audit.exists():
            wanted.append(audit)
        # 非因果类型的论文：措辞清单在审稿这一刻按当前 paper.md 现扫现建，不读盘上
        # 可能过期的那份——类型换回因果之后，或者 `oss paper check` 之后稿子又改过，
        # 盘上那份 causal_flags.json 都不再作数。判据只有一处（`wording_scope`）。
        # **三位都拿同一份**——只给识别审稿人就破了「同一份不可变材料 + 自己的侧重」
        # 的互盲规矩（blind-discipline.md）
        wtype, _ = rt_gate.wording_scope(rundir.path)
        paper_md = rundir.file(ARTIFACTS["paper"])
        if wtype and paper_md.exists():
            hits = causal_wording.scan(paper_md.read_text(encoding="utf-8"), wtype)
            wanted.append(causal_wording.write_flags(rundir.path, hits, wtype))
        missing = [w.name for w in wanted if not w.exists()]
        if rundir.file("paper_rendered.md") in wanted and missing and \
                "paper_rendered.md" in missing:
            raise Missing("paper_rendered", "缺 paper_rendered.md——先跑 oss paper render",
                          code=1, data={"log": log})
        if missing:
            # 缺件不拦，但**必须说出来**——审稿人看不到的东西也就核不了
            log.append(["err", f"  ⚠ 缺 {'、'.join(missing)}，这几项审稿人核不了"])
        files = [str(w) for w in wanted if w.exists()] + files
    if not files:
        raise BadInput("没给要审的文件（或用 --paper）", code=2, data={"log": log})

    # 第 2 轮起：把上一轮的逐条意见交给审稿人，任务从「再审一遍」变成「判处置了没有」。
    # **上一轮标了 [新发现] 的不带进去**——它们当时就声明了不构成义务。
    previous: list[str] = []
    if round_ >= 2 and kind in FOLLOWUP_KINDS:
        prev_round = round_ - 1
        letters: list[dict] = []
        reports: list[dict] = []
        for p_ in sorted(rundir.path.glob("review_*.json")):
            d = read_json_file(p_) or {}
            if p_.name.startswith("review_editor"):
                # 编辑综合不算「一位审稿人」，所以 paper_review_round() 认不出它的轮次
                if _round_of(d) == prev_round:
                    letters.append(d)
            # 「算不算上一轮的论文审稿」只有 loop.paper_review_round() 一个出处：
            # 它已经把选题 / 功效 / 引用 / 桌审 / 计划审计都排除在论文轮次之外，
            # 而且轮次以文件里的 round 为准、不看文件名。
            elif loop_mod.paper_review_round(p_) == prev_round:
                reports.append(d)
        # **决定信优先，但空的决定信不算数。**编辑综合按构造就是几位审稿人意见去重、
        # 排序、裁分歧之后的版本，所以有它就只用它——把原始报告并进来等于把编辑
        # 已经裁掉的分歧搬回来，同一件事还会以两种措辞各出现一次。
        # 可是一封 findings 为空的决定信（后端半路挂了、编辑判 PASS 什么都没列）
        # 会把三位审稿人的意见全吃掉，复审于是拿着一张白纸去判「处置了没有」。
        # 所以回落的判据是**这一档到底供出了意见没有**，不是决定信这个文件在不在。
        seen_keys: set[str] = set()
        from_letter = False
        for source in (letters, reports):
            for d in source:
                for raw in d.get("findings") or []:
                    f_ = str(raw)
                    # 上一轮标了 [新发现] 的不带进去——它们当时就声明了不构成义务
                    if loop_mod.classify_finding(f_) == loop_mod.KIND_NEW:
                        continue
                    # 三位互盲审稿人提同一件事是常态（那正是它要紧的证据）。
                    # 带三遍只会让复审把一条意见判三次，所以按归一化后的正文去重、保序。
                    key = " ".join(f_.split())
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    previous.append(f_)
            if previous:
                from_letter = source is letters
                break
        log.append(["out", f"  第 {round_} 轮复审：带上第 {prev_round} 轮的 {len(previous)} 条意见"
                           + ("（取自编辑综合的决定信）" if from_letter else "")
                           + f"，**只判处置了没有**"])

    label = "、".join(Path(f).name for f in files)
    rev = reviewer if reviewer is not None else _default_reviewer(rundir.path)

    # 判词的文件名**在花钱之前**就算出来，锁才有意义（见 _inflight）

    # 判词的文件名**在花钱之前**就要算出来——锁认的是它（见 _inflight）
    stem = Path(files[0]).stem
    if desk:
        stem = "desk"                           # review_desk*.json：不算审稿轮次里的一位
    elif editor:
        stem = "editor"                         # review_editor*.json：同上
    elif emphasis:
        # 互盲的多轮不能互相覆盖——每个侧重一份独立报告
        stem += "_" + re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", emphasis)[:12]
    if round_ > 1:
        stem += f"_r{round_}"          # 改稿重审不覆盖上一轮，每轮都留着
    out = rundir.file(f"review_{stem}.json")

    with _inflight(out, log):
        try:
            v = with_downgrade(rev, [Path(f) for f in files], level, kind, emphasis,
                               round_=round_, previous=previous, log=log)
        except SameFamilyRefused as exc:
            rundir.append_trace(TraceEvent(
                seq=rundir.next_seq(), step="review", command=f"oss review {label}",
                outcome="refused", note=str(exc)))
            raise Refused(str(exc), code=2, data={"log": log}) from exc
        except ReviewerUnavailable as exc:
            # 审稿后端用不了 ≠ 审稿通过。这是最危险的失败模式，必须显式区分。
            rundir.append_trace(TraceEvent(
                seq=rundir.next_seq(), step="review", command=f"oss review {label}",
                outcome="error", note=str(exc)))
            raise Unavailable(
                f"审稿后端不可用：{exc}", code=1,
                problems=["**这不是「通过」**——本次没有得到任何裁决"],
                data={"log": log}) from exc

    kinds = {loop_mod.KIND_DESIGN: 0, loop_mod.KIND_DATA: 0,
             loop_mod.KIND_TEXT: 0, loop_mod.KIND_NEW: 0}
    for f_ in v.findings:
        kinds[loop_mod.classify_finding(f_)] = kinds.get(loop_mod.classify_finding(f_), 0) + 1
    out.write_text(json.dumps(v.to_dict() | {"round": round_, "kinds": kinds},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step="review", command=f"oss review {label}",
        outcome="ok", artifacts=[out.name],
        # 判词与意见分类进 args，不只进 note：note 是给人看的散文，
        # 机械核这条留痕（第几轮、判了什么、几条设计级）得有结构化字段可取。
        args={"round": round_, "verdict": v.verdict, "kinds": kinds},
        note=f"{v.verdict} by {v.reviewer_model}（{v.reviewer_family} × {v.executor_family}）"
             f"；第 {round_} 轮；设计 {kinds[loop_mod.KIND_DESIGN]} / 数据 {kinds[loop_mod.KIND_DATA]}"
             f" / 文字 {kinds[loop_mod.KIND_TEXT]} / 新发现 {kinds[loop_mod.KIND_NEW]}"))

    # 状态落盘放在回显之后：状态写出岔子也不该吞掉审稿人已经给出的判词。
    # 门面拿到返回值才打印，所以这里的「之后」是**返回之前的最后一步**，位置没变。
    # 模型身份每种审稿都记——谁审的、跟执行者是不是一家，是 assurance 契约的凭据。
    # family_relation 按家族名当场比：cross_family 在能走到这里的路径上恒为真，记它等于没记。
    state_mod.update(rundir.path, models={
        "executor": v.executor_model, "reviewer": v.reviewer_model,
        "family_relation": "different" if v.reviewer_family != v.executor_family else "same"})
    # 只有论文类审稿（桌审 / 三位 / 综合）才把阶段推到 8；审计与选题陪审团发生在别的环节。
    if paper or desk or editor:
        # 轮次要重算而不是用 --round：**第几轮不能靠模型记**，压缩后它会把第 3 轮记成第 2 轮。
        st_loop = loop_mod.loop_status(rundir.path)
        state_mod.set_stage(rundir.path, "8-review")
        state_mod.update(rundir.path, review={
            "round": st_loop.round, "max_rounds": st_loop.max_rounds,
            "last_decision": st_loop.decision.get("decision")})

    return result(f"{v.verdict}  {v.reason}", {
        "run": str(rundir.path), "verdict": v.verdict, "reason": v.reason,
        "round": round_, "kind": kind, "level": level, "emphasis": emphasis,
        "kinds": kinds,
        "reviewer_model": v.reviewer_model, "reviewer_family": v.reviewer_family,
        "executor_model": v.executor_model, "executor_family": v.executor_family,
        "findings": list(v.findings), "previous": previous,
        "files": [Path(f).name for f in files],
        "path": str(out), "name": out.name, "log": log,
    })
