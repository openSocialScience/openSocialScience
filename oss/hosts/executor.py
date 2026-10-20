"""执行者是谁：问四处，都问不到就按「自审」记；开了 REQUIRE_CROSS_MODEL=1 才拒绝。

跨模型审稿的全部意义在于「审的和被审的不是同一家」。
**不知道被审的是谁，就没有跨家族可言**——所以这里宁可记成自审，不可猜（DECISIONS.md 2.3-2.5）。

四个来源按可信度排：

1. `EXECUTOR_MODEL` —— 用户明说的，最硬。写进项目根 .env 最稳（oss 自己读 .env，
   不靠宿主传；dsh 的 bash 还会剥掉带 KEY/TOKEN/SECRET/PASSWORD 的变量，
   EXECUTOR_MODEL 不在剥除之列，但 REVIEWER_API_KEY 在）
2. 宿主档案的 `executor_model_env` —— 宿主自己暴露的模型名。多数宿主没有：
   dsh 只注入 DSH_HOME / DSH_SHELL / DSH_SESSION_ID / DSH_SESSION_JSONL 四个变量，
   没有一个是模型名
3. `run_state.json` 的 `models.executor` —— 同一个运行目录里上一次审稿**真实记下来**的，
   不是猜的。压缩、换会话之后这一条最有用
4. 都没有 → 返回空串，交给 `check_family()`：缺省记成「自审」；严格档下拒绝，报错里带上宿主名和这三条怎么设
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from oss.core import state
from oss.hosts import HostProfile, UnknownHost, available, detect, load


@dataclass(frozen=True)
class Executor:
    #: 模型标识；问不到就是空串
    model: str
    #: 从哪儿问到的：EXECUTOR_MODEL / <宿主>:<变量名> / run_state.json / none / explicit
    source: str


def _profile(host: str | HostProfile | None, env: dict | None = None) -> HostProfile | None:
    """认不出就是 None，**不回落到缺省宿主**。

    回落看起来只是「宽容一点」，实际是**替一个不认识的宿主借了别家的环境变量**：
    `OSS_HOST=cursor` 时回落到 claude-code，于是用户在 Cursor 里设的
    `ANTHROPIC_MODEL` 被当成「执行者是谁」的答案报了出来——而那个变量说的
    根本不是正在跑这套技能的模型。宁可问不到，不可问错人。
    """
    if isinstance(host, HostProfile):
        return host
    try:
        return load(host or detect(env=env))
    except UnknownHost:
        return None


def resolve(host: str | HostProfile | None = None,
            rundir: Path | str | None = None,
            env: dict | None = None) -> Executor:
    env = os.environ if env is None else env
    if env.get("EXECUTOR_MODEL"):
        return Executor(env["EXECUTOR_MODEL"], "EXECUTOR_MODEL")
    prof = _profile(host, env)
    if prof:
        for var in prof.executor_model_env:
            if env.get(var):
                return Executor(env[var], f"{prof.name}:{var}")
    if rundir:
        p = Path(rundir)
        if (p / state.FILE).exists():
            m = str((state.load(p).models or {}).get("executor") or "")
            if m:
                return Executor(m, "run_state.json")
    return Executor("", "none")


def hint(host: str | HostProfile | None = None, env: dict | None = None) -> str:
    """拒绝时附在错误后面的那几行。**点名宿主**——「怎么设」在不同宿主下不一样。"""
    prof = _profile(host, env)
    if prof:
        label = f"{prof.label}（{prof.name}）"
    else:
        # **点名**：只说「认不出的宿主」，用户不知道是自己拼错了 --host 还是
        # OSS_HOST 设成了一个没有档案的名字。名字和已知清单一起给。
        name = host if isinstance(host, str) and host else detect(env=env)
        label = f"认不出的宿主 {name!r}（已知：{'、'.join(available())}）"
    exposed = "、".join(prof.executor_model_env) if prof and prof.executor_model_env \
        else "这个宿主没有暴露模型名的环境变量"
    lines = [
        f"当前宿主：{label}。执行者是谁，按这三处依次问，都问不到就拒绝：",
        "  1. EXECUTOR_MODEL —— 写进项目根 .env（oss 自己读它，不靠宿主传）",
        f"  2. 宿主自己暴露的变量：{exposed}",
        "  3. 同一运行目录里上一次审稿记下的 run_state.json → models.executor",
    ]
    if prof and prof.name == "dsh":
        lines.append(
            "  注意：dsh 的 bash 会剥掉名字带 KEY / TOKEN / SECRET / PASSWORD 的环境变量，"
            "REVIEWER_API_KEY 必须走 .env；EXECUTOR_MODEL 不在剥除之列，两种写法都行。")
    return "\n".join(lines)
