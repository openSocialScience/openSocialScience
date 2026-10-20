"""`oss doctor --host X` 只跑 X 该跑的那几条。

事故形状（轻，但每天发生）：dsh 用户跑 `oss doctor`，最后一行永远是
`✗ Claude Code CLI  未在 PATH 中找到 claude`。**这条红线他修不了，也不该修。**
长期挂着一条假红线，下一条真红线就没人看了——这跟阶段 1 那个「状态写着 exhausted、
阻塞信息却让你再审一轮」是同一类毛病：信号自相矛盾，于是信号失效。

每条检查是 `(profile) -> (通过?, 一句话)`。名字进档案的 `doctor_checks`；
档案里写了没实现的名字，`hosts.load()` 直接拒绝加载（拼错不静默）。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable

from oss.core import envfile
from oss.hosts import HostProfile


def _claude_cli(profile: HostProfile) -> tuple[bool, str]:
    p = shutil.which("claude")
    return (bool(p), p or "未在 PATH 中找到 claude")


def _dsh_home(profile: HostProfile) -> tuple[bool, str]:
    home = Path(os.getenv("DSH_HOME") or "~/.dsh").expanduser()
    return (home.exists(), str(home) if home.exists()
            else f"{home} 不在——dsh 没装，或者 DSH_HOME 指到了别处")


def _dotenv() -> Path | None:
    """oss 自己找 .env 的规则（见 oss/core/envfile.py）：cwd 项目本地 /
    用户级 `~/.config/oss/.env` / 源码检出，按这个优先级取第一份存在的。"""
    found = envfile.candidates()
    return found[0] if found else None


def _dsh_key_via_dotenv(profile: HostProfile) -> tuple[bool, str]:
    """dsh 的 bash 会剥掉名字里带 KEY/TOKEN/SECRET/PASSWORD 的环境变量。

    也就是说：在 dsh 外面 `export REVIEWER_API_KEY=...` 再让模型跑 `oss review`，
    **key 到不了这里**，而报错会长得像「审稿后端未配置」——一个指向错误方向的信号。
    oss 自己读 .env（cwd 项目本地 / 用户级 / 源码检出，见 oss/core/envfile.py），
    所以正确的做法是把 key 写进其中一份 .env。
    """
    if os.getenv("REVIEWER_API_KEY"):
        return True, "拿得到（环境变量或 .env）"
    env = _dotenv()
    if env:
        return False, (f"{env} 在，但里面没有 REVIEWER_API_KEY。"
                       "注意 dsh 的 bash 会剥掉名字带 KEY/TOKEN/SECRET/PASSWORD 的变量，"
                       "export 出来的传不进来——写进 .env")
    looked = "、".join(envfile.search_paths_for_message())
    return False, ("没有 .env，环境里也没有 REVIEWER_API_KEY。"
                   "dsh 的 bash 会剥掉名字带 KEY/TOKEN/SECRET/PASSWORD 的变量，"
                   "所以 export 不管用：建一份 .env 写进去，oss 自己读——"
                   f"找过这些地方：{looked}")


def _skills_dir_present(profile: HostProfile) -> tuple[bool, str]:
    user = profile.user_dir()
    proj = profile.project_dir()
    hit = [str(p) for p in (proj, user) if p and p.exists()]
    return (bool(hit), "、".join(hit) if hit
            else f"{proj} 与 {user} 都不在——跑 oss install-skills --host {profile.name}")


def _openclaw_workspace(profile: HostProfile) -> tuple[bool, str]:
    ws = Path("~/.openclaw/workspace").expanduser()
    return (ws.exists(), str(ws) if ws.exists() else f"{ws} 不在——OpenClaw 没装或没建过 workspace")


#: 检查名 → （给人看的标题, 函数）
CHECKS: dict[str, tuple[str, Callable[[HostProfile], tuple[bool, str]]]] = {
    "claude-cli-on-path": ("Claude Code CLI", _claude_cli),
    "dsh-home": ("dsh 配置目录", _dsh_home),
    "dsh-key-via-dotenv": ("审稿 key 到得了 dsh 的 bash", _dsh_key_via_dotenv),
    "agents-skills-dir": (".agents/skills 目录", _skills_dir_present),
    "openclaw-workspace": ("OpenClaw workspace", _openclaw_workspace),
    "workbuddy-skills-dir": (".workbuddy/skills 目录", _skills_dir_present),
}
