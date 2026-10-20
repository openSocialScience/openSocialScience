"""OASIS 跑单模板——一次 condition × rep 的模拟怎么跑，写死在这份文件里。

**此文件是数据不是代码**：八个 `{{...}}` 花括号空位（见下，七个必填、第八个
SEMAPHORE 可选）不是合法的运行时值，由 V2 的说明书（`/social-sim`）照整理稿
把它们填好、另存成货箱 `raw/` 下的一份具体脚本（如 `raw/A_1.py`）才能跑。
**别直接 import 这份模板**——import 会在模块顶层就执行 `int("{{ROUNDS}}")`
这类语句，字面量转不了整数，当场炸；就算哪天恰好不炸，跑出来的也是空话占位符
而不是真实设置，比炸更危险。

运行契约（跑单不管怎么改都不能破的两条，2026-08-31 设计稿第三节钉死）：

1. **开跑前先访谈一轮**——第 0 波是 DID 的处理前基线，没有它处理前后无从比较
   （V0 发现 8：条件当组间处理变量在现有工具箱里做不了机制分析，正确形状是
   前后对照/DID，第 0 波就是"处理前"）。
2. **每跑完一步（不管是行动轮还是访谈波）都要记一次"跑到第几步了"**：
   连 db 执行 `SELECT MAX(rowid) FROM trace`，把
   `{"kind": "round"|"interview", "round"|"wave": n, "max_rowid": m}`
   追加进 `<db 名>.steps.jsonl`（插头 `oss/sims/oasis/adapter.py` 的
   `_steps()` 就认这个格式；少一步、字段名对不上，装箱直接拒收，不会瞎猜）。

本模板另内置两项兜底（2026-08-31 真跑验证的教训，F2/F5，见
`docs/design/plans/2026-08-31-sim-intake-v2.md` Task 2；不是「运行契约」——
不改变数据形状，是跑单自己的免受一个异常打断整场的保护，实现见下方
「内置兜底与重试」「模型错误计数」两段代码）：

- 访谈遇到异常不再杀全场：连接类异常重试后仍不行、或压根不是连接类异常
  （如中转站断粮的 402），该小人这一波记为缺失（`[interview-miss]`），
  访谈继续跑，不牵连其他小人；
- 跑完在 db 旁落一份 `<db 名>.run_summary.json`，记模型错误数与访谈缺失数，
  插头 `adapter.pack` 见到会把两个数汇总进配料单 `packed`（这份文件可选，
  老箱子没有也不报错）。

用法（另存并把七个必填空位换成真实值之后；第八个 SEMAPHORE 空位可选，
不填有缺省）：

    BASE=<中转站 URL> KEY=<中转站 key> python raw/A_1.py

八个空位（七必填一可选）：CONDITION、REP、RECSYS、ROUNDS、N_AGENTS、
INTERVIEW_Q、INTERVIEW_WAVES 七个必填，SEMAPHORE 第八个可选——逐条说明见
下面的赋值处（此处刻意不写花括号包裹的原字面量：另存时的盲替换按每个名字
两侧包一对花括号的写法逐个找目标，写在这里会被替换值把这一行填坏）。
"""
import asyncio
import csv
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from camel.models import ModelFactory
from camel.types import ModelPlatformType

import oasis
from oasis import (ActionType, LLMAction, ManualAction,
                    generate_twitter_agent_graph)
from oasis.social_platform.channel import Channel
from oasis.social_platform.platform import Platform

# ── 内置兜底与重试（F2）─────────────────────────────────────────────────
# OASIS 0.2.5 的 perform_interview 对空回复/网络异常不设防：一个小人的异常经
# asyncio.gather 放大成全场访谈波中止（2026-08-31 真跑验证实测过三次，
# 当时用的是实例侧 monkeypatch 顶住的，改进账写明「应进模板本体」——这里就是
# 那笔账的落地，见 oasis_v1run/box/raw/A_1.py 的 "interview-miss" 标记）。
# 动态取到那个类（不写死类名，camel-oasis 内部把 perform_interview 挂在哪个
# 具体类上属于实现细节，靠 hasattr 找更稳）包一层：
#   · 连接类异常（按类名匹配 APIConnectionError / APITimeoutError，
#     不直接 import openai 以免跑单模板多背一个依赖）——重试最多 3 次，
#     指数退避 1s/2s/4s 再判定失败；
#   · 其余异常（如中转站断粮时常见的 402 状态错）——钱的问题重试无用，
#     不重试，第一次失败就算数；
# 最终失败打印 `[interview-miss] agent <id>: <类名>: <异常文字前200字>`，
# 返回哨兵字典——异常文字一起打出来是 2026-08-31 质量审补的：光有类名
# 分不出「哪条中转站错误」，402/欠费类消息常常连类名都通用（如
# APIStatusError），不带文字这行打印就是个不能用的传感器。
# 该小人此波在 trace 无行（camel-oasis 0.2.5 的落库发生在模型调用**之后**，
# 一次 miss 压根没有行可写，不是「空答案那一行」）；波内缺人由海关的人数
# 覆盖警告负责（见 T2b，另任务实现，此处不重复造轮子），这一波访谈继续跑，
# 不殃及其他小人。
from oasis.social_agent import agent as _agent_mod
_cls = next(v for k, v in vars(_agent_mod).items()
            if isinstance(v, type) and hasattr(v, "perform_interview"))
_orig_interview = _cls.perform_interview
_RETRYABLE_EXC_NAMES = {"APIConnectionError", "APITimeoutError"}
_RETRY_BACKOFF = (1, 2, 4)   # 秒，重试前依次等待——第 3 次仍败即放弃
_interview_miss_count = 0


async def _safe_interview(self, prompt):
    global _interview_miss_count
    exc = None
    for attempt in range(1 + len(_RETRY_BACKOFF)):
        try:
            return await _orig_interview(self, prompt)
        except Exception as e:
            exc = e
            if (type(e).__name__ not in _RETRYABLE_EXC_NAMES
                    or attempt == len(_RETRY_BACKOFF)):
                break   # 非连接类异常，或连接类异常也已经重试到头
            await asyncio.sleep(_RETRY_BACKOFF[attempt])
    _interview_miss_count += 1   # 计数先加，不等打印是否成功
    try:
        # stdout 断管（BrokenPipeError）经 gather 会复杀整波——2026-08-31
        # 质量审实测过；这个 print 只是留痕，不该成为第二个杀全场的异常源。
        print(f"[interview-miss] agent {getattr(self, 'social_agent_id', '?')}: "
              f"{type(exc).__name__}: {str(exc)[:200]}", flush=True)
    except Exception:
        pass
    return {"interview_missed": True}


_cls.perform_interview = _safe_interview

# ── 模型错误计数（F5）───────────────────────────────────────────────────
# 中转站断粮（402）时，行动轮的异常不像访谈那样有 [interview-miss] 这行字
# 留痕——LLMAction 失败多半悄悄变成 do_nothing 之类的沉默行为，跑痕看起来
# 一切正常，实则整段行动轮都是噪音（V2 发现 F5）。这里挂一个
# logging.Handler，数 camel、social.agent 两棵 logger 树上 ERROR 及以上的
# 记录数——两棵树都用默认的 propagate，子 logger 的记录会冒泡到这里，不用
# 逐个模块找齐全部 logger 名。跑完连同 interview-miss 一起写进
# run_summary.json（见 main() 结尾）。
class _ErrorCounter(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.count = 0

    def emit(self, record):
        self.count += 1


_error_counter = _ErrorCounter()
for _logger_name in ("camel", "social.agent"):
    logging.getLogger(_logger_name).addHandler(_error_counter)

HERE = Path(__file__).resolve().parent

# ── 七个待填空位（必填）──────────────────────────────────────────────
COND = "{{CONDITION}}"
# 条件名，如 A / B。只作文件名与落库记录用，不改变模拟行为——
# 真正让条件产生差异的是下面的 RECSYS（以及研究者在整理稿里定的其他设置）。
REP = "{{REP}}"
# 第几次重复，如 1 / 2 / 3。同一条件跑多次重复，每次必须换一个 REP，
# 否则库文件同名互相覆盖（详见货箱标准 docs/user/sim-box.md 的 runs.csv 一节）。
RECSYS = "{{RECSYS}}"
# 推荐算法：reddit / random / twhin-bert 三选一（twitter 档已知坏，见安装单）。
ROUNDS_RAW = "{{ROUNDS}}"
# 行动轮数（不含访谈波）。
N_AGENTS_RAW = "{{N_AGENTS}}"
# 从样本档案里取前多少人——按 CSV **记录数**取，不按物理行数（V0 发现 3：
# 简介字段里常有换行，`head -N` 之类按物理行取人会漏人或取重）。
INTERVIEW_Q = "{{INTERVIEW_Q}}"
# 访谈问题原文，逐字进 trace 也逐字进论文方法节；只认回答是否解析成 1-7
# 单个数字，解析不了记为缺失，不猜（插头 adapter.py 的老规矩）。
INTERVIEW_WAVES_RAW = "{{INTERVIEW_WAVES}}"
# 除第 0 波（开跑前基线，见上面的运行契约 1）之外，额外访谈的轮次号，
# 逗号分隔，如 "5,10" 表示第 5 轮和第 10 轮结束后各访谈一次。
# 第 0 波是固定动作，不必也不能写进这里。
# wave 按访谈发生的先后顺序编号 0,1,2……不是轮次号本身：填 "5,10" 得到
# wave 0=基线（第 0 轮）、wave 1=第 5 轮后、wave 2=第 10 轮后；wave 与真实
# 轮次的对应关系要写进配料单 interview_plan，states.csv 的 wave 列自己不记轮次号。

# ── 第八个空位（可选，F3）────────────────────────────────────────────
SEMAPHORE_RAW = "{{SEMAPHORE}}"
# 并发信号量：同时在飞的模型请求上限。可选——不填（仍是花括号占位符）时
# 缺省 8。缺省值不是随便取的：2026-08-31 真跑验证用 32（旧默认）在长上下文
# 的末波两次把中转站压垮，改成 8 才稳住，故把 8 定为本模板的缺省而不是 32；
# 要调大就在整理稿层面明确写出这个数字，另存时把 {{SEMAPHORE}} 换成它。

_UNFILLED = [name for name, val in (
    ("CONDITION", COND), ("REP", REP), ("RECSYS", RECSYS),
    ("ROUNDS", ROUNDS_RAW), ("N_AGENTS", N_AGENTS_RAW),
    ("INTERVIEW_Q", INTERVIEW_Q), ("INTERVIEW_WAVES", INTERVIEW_WAVES_RAW),
) if val.startswith("{{") and val.endswith("}}")]
# SEMAPHORE 不在上面这份必填清单里——它是第八个可选空位，没填不算「跑单没填好」，
# 缺省逻辑在下面单独处理（不走这道 SystemExit 拒绝）。
if _UNFILLED:
    raise SystemExit(
        f"{'、'.join(_UNFILLED)} 还是花括号占位符，没有被另存替换——"
        f"这份文件本身是数据不是代码，见顶部 docstring")

ROUNDS = int(ROUNDS_RAW)
N_AGENTS = int(N_AGENTS_RAW)
INTERVIEW_WAVES = [int(x) for x in INTERVIEW_WAVES_RAW.split(",") if x.strip()]
SEMAPHORE = (8 if SEMAPHORE_RAW.startswith("{{") and SEMAPHORE_RAW.endswith("}}")
             else int(SEMAPHORE_RAW))

DB = HERE / f"{COND}_{REP}.db"
# 插头按 <db 名去掉扩展名>.steps.jsonl 找行号日志（adapter.py 的
# db.with_name(db.stem + ".steps.jsonl")），这里用同一个算法算文件名，
# 保证跑单这边写的文件名与插头那边找的文件名永远一致。
STEPLOG = DB.with_name(DB.stem + ".steps.jsonl")
RUN_SUMMARY = DB.with_name(DB.stem + ".run_summary.json")
# 同一算法算出的第三个旁路文件（F5）：跑完写 model_errors/interview_miss/
# advice 三个字段。插头 adapter.pack 认这个文件名，见到就把前两个数汇总进
# 配料单 packed；这个文件不存在不报错——老箱子（V1 模板跑出来的）没有它。
PROFILE_SRC = HERE / "profiles_full.csv"
# 未裁剪的样本档案，另存脚本前放到这个位置（同名同目录）——货箱必须自含，
# 不引用箱子外的绝对路径。默认可用 profile.json 里 sample_data 指向的那份
# OASIS 自带样本（列名 user_id/name/username/following_agentid_list/
# previous_tweets/user_char/description），换成自己的人群档案也可以，
# 只要列名一致、generate_twitter_agent_graph 认得。
PROFILE_TRIMMED = HERE / f"profiles_{COND}_{REP}.csv"

SEED_CLAIM = ("Remote work will completely replace office work "
              "within five years.")
# V0 沿用的刺激帖：第 1 轮由 0 号小人发出，作为扩散的种子。条件差异走 RECSYS
# （以及研究者在整理稿里另定的设置），不走这条帖子的内容——八个空位（七必填
# 一可选）里没有「刺激内容」这一项，是本模板 V1 的有意简化；如果研究设计
# 需要按条件换刺激内容，这条常量要连着模板一起手改，不能只填空位。
MODEL_TYPE = "deepseek-v4-flash"

print(f"[cost hint] 实测参考：约 1.5 元/场（46 人×10 轮×flash 经中转站——"
      f"V0 那句『约 3 元』是两场合计，不是单场）；时长与并发挂钩：并发 32 时"
      f"约 5 分钟/场（V0），缺省并发 8 时约 13-14 分钟/场（2026-09-01 验收轮"
      f"50 人×10 轮实测）。本次 {N_AGENTS} 人×{ROUNDS} 轮，费用按人数×轮数"
      f"折算（不随并发变）；超预算就中断。",
      flush=True)


def log_step(kind: str, n: int) -> None:
    """每次 env.step 之后立刻调用一次：记下当前 trace 表跑到第几行。

    kind 只能是 "round"（行动轮）或 "interview"（访谈波）；round 记 "round" 键，
    interview 记 "wave" 键——两种记法插头 adapter._locate() 分开认，别混着写。
    """
    con = sqlite3.connect(DB)
    max_rowid = con.execute("SELECT MAX(rowid) FROM trace").fetchone()[0]
    con.close()
    record = {"kind": kind, ("round" if kind == "round" else "wave"): n,
              "max_rowid": int(max_rowid) if max_rowid is not None else 0}
    with open(STEPLOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {record}", flush=True)


def trim_profiles() -> None:
    """按 CSV 记录数取前 N_AGENTS 条，写到 PROFILE_TRIMMED（V0 发现 3 的落地）。

    用 csv.DictReader 逐记录数，不用 `for line in file` 按物理行数——简介字段
    (`description`) 里常带换行，一条记录能跨好几物理行，按行取人会取少或取乱。
    """
    try:
        fin = PROFILE_SRC.open(encoding="utf-8", newline="")
    except FileNotFoundError:
        raise SystemExit(
            f"{PROFILE_SRC.name} 不在——按安装单 docs/user/sim-oasis.md 第四节，"
            f"把样本档案复制到这份跑单脚本同目录、改名成 {PROFILE_SRC.name} 再跑")
    with fin:
        reader = csv.DictReader(fin)
        fieldnames = reader.fieldnames
        rows = []
        for row in reader:
            rows.append(row)
            if len(rows) >= N_AGENTS:
                break
    if len(rows) < N_AGENTS:
        raise SystemExit(
            f"{PROFILE_SRC.name} 只有 {len(rows)} 条记录，不够 {N_AGENTS} 人")
    with PROFILE_TRIMMED.open("w", encoding="utf-8", newline="") as fout:
        writer = csv.DictWriter(fout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


async def main() -> None:
    if DB.exists():
        DB.unlink()
    if STEPLOG.exists():
        STEPLOG.unlink()
    if RUN_SUMMARY.exists():
        RUN_SUMMARY.unlink()
    if PROFILE_TRIMMED.exists():
        PROFILE_TRIMMED.unlink()

    trim_profiles()

    base = os.environ.get("BASE")
    key = os.environ.get("KEY")
    if not base or not key:
        raise SystemExit(
            "环境变量 BASE 和/或 KEY 没设——中转站的 URL 和 key 得先设好才能跑，"
            "见顶部 docstring 的用法：BASE=<中转站 URL> KEY=<中转站 key> python raw/A_1.py")

    model = ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI_COMPATIBLE_MODEL,
        model_type=MODEL_TYPE,
        url=base,
        api_key=key,
    )

    available_actions = [
        ActionType.CREATE_POST,
        ActionType.CREATE_COMMENT,
        ActionType.LIKE_POST,
        ActionType.REPOST,
        ActionType.QUOTE_POST,
        ActionType.FOLLOW,
        ActionType.DO_NOTHING,
    ]

    agent_graph = await generate_twitter_agent_graph(
        profile_path=str(PROFILE_TRIMMED),
        model=model,
        available_actions=available_actions,
    )

    channel = Channel()
    platform = Platform(
        db_path=str(DB),
        channel=channel,
        recsys_type=RECSYS,
        refresh_rec_post_count=2,
        max_rec_post_len=2,
        following_post_count=3,
    )
    env = oasis.make(agent_graph=agent_graph, platform=platform,
                      database_path=str(DB), semaphore=SEMAPHORE)

    await env.reset()

    wave = 0

    async def interview_all(tag: str) -> None:
        nonlocal wave
        actions = {agent: ManualAction(
            action_type=ActionType.INTERVIEW,
            action_args={"prompt": INTERVIEW_Q})
            for _, agent in env.agent_graph.get_agents()}
        await env.step(actions)
        log_step("interview", wave)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] interview done: {tag}",
              flush=True)
        wave += 1

    await interview_all("baseline")  # 第 0 波：运行契约 1，DID 的处理前观测

    for r in range(1, ROUNDS + 1):
        if r == 1:
            actions = {env.agent_graph.get_agent(0): ManualAction(
                action_type=ActionType.CREATE_POST,
                action_args={"content": SEED_CLAIM})}
        else:
            actions = {agent: LLMAction()
                       for _, agent in env.agent_graph.get_agents()}
        await env.step(actions)
        log_step("round", r)
        if r in INTERVIEW_WAVES:
            await interview_all(f"round{r}")

    await env.close()

    print(f"[done] condition={COND} rep={REP} recsys={RECSYS} rounds={ROUNDS} "
          f"agents={N_AGENTS} db={DB.name} steplog={STEPLOG.name}", flush=True)
    # camel-oasis 0.2.5 的 ChatAgent 不在实例上留存跨轮累计 token 数
    # （单次请求的 usage_dict 用完即弃，generate_twitter_agent_graph 也不接
    # on_request_usage 回调把它导出来）；这里尽力探一下常见位置，探不到就
    # 如实说探不到，精确账单去中转站网关自己的用量页核对——不编数字。
    usage = getattr(model, "token_usage", None) or getattr(model, "usage", None)
    if usage:
        print(f"[token usage] {usage}", flush=True)
    else:
        print("[token usage] 这版 camel-oasis 没有留存跨轮累计 token 数的公开接口；"
              "费用量级见开头的 cost hint，精确数字去中转站网关的用量页查。",
              flush=True)

    # ── run_summary.json（F5）───────────────────────────────────────
    # advice 不用「错误占比」框架：分子（错误日志条数）含重试——一次访谈
    # 失败重试 3 次能留下 4 条 ERROR 记录；分母（agents×rounds）又数不全
    # 请求（访谈请求本就不进 camel/social.agent 这两棵 logger 树），两者
    # 硬凑出的百分比在 2026-08-31 真跑数据上报过 89.1%，而当时真实失败率
    # 只有约 9%——诚实叫法是「N 条错误日志（含重试，非失败请求数）」，
    # 阈值仍用 agents×rounds×5% 这条经验线，但明说它是启发式指标、不是
    # 失败率，别让人拿它当比例读。
    threshold = max(1, N_AGENTS * ROUNDS) * 0.05
    if _error_counter.count > threshold:
        advice = (f"{_error_counter.count} 条错误日志（含重试，非失败请求数）"
                  f"超过经验线 agents×rounds×5%={threshold:.1f}"
                  f"（启发式指标，非失败率）——建议在 runs.csv 把这场标 complete=no")
    else:
        advice = (f"{_error_counter.count} 条错误日志（含重试，非失败请求数）"
                  f"未超过经验线 agents×rounds×5%={threshold:.1f}"
                  f"（启发式指标，非失败率），暂不建议标 complete=no")
    run_summary = {"model_errors": _error_counter.count,
                   "interview_miss": _interview_miss_count,
                   "advice": advice}
    RUN_SUMMARY.write_text(json.dumps(run_summary, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"[run summary] {run_summary}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
