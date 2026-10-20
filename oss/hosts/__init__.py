"""宿主档案：「哪个 agent 宿主在跑这套技能」是一个参数，不是一句假设。

**跟 `oss/host/` 不是一回事**（名字只差一个 s，看清楚）：
`oss/host/` 是运行期的适配层——运行目录 `RunDir`、跨模型审稿后端 `Reviewer`；
`oss/hosts/` 是**静态事实表**——各宿主的技能目录在哪、替不替换 `$ARGUMENTS`、
认不认 `allowed-tools`、自检该查什么、指令文件叫什么。

事实全部来自各宿主自己的文档，出处逐条记在档案的 `source` 字段里。
**没有出处的事实不许写进档案**：猜错一条，投影出来的安装树就是错的，而它看起来是对的。

档案是 JSON 不是 YAML：`oss` 的运行期依赖里没有 PyYAML（阶段 2 只把它放进 dev），
而 `install-skills` / `doctor` 是用户装完就要能跑的命令。**不为一张表加一个运行期依赖。**
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, fields
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: `${NAME:-缺省}`：宿主自己就是这么定位技能根的（dsh 的是 `$DSH_HOME/skills`，
#: `~/.dsh` 只是 `DSH_HOME` 的缺省值）。档案里写死 `~/.dsh/skills` 会在用户设过
#: `DSH_HOME` 时装到一个宿主根本不看的地方——**而且装完一切看起来都成功了**。
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}")


def expand_vars(s: str, env: dict | None = None) -> str:
    """把 `${NAME:-缺省}` 展开：环境变量非空就用它，否则用缺省。

    只认这一种写法（带缺省的），不认裸 `$NAME`：没有缺省的变量一旦没设，
    展开出来就是空串或字面量，两种都会静默装错地方。
    """
    e = os.environ if env is None else env
    return _VAR.sub(lambda m: e.get(m.group(1)) or m.group(2), s)


#: 认不出宿主时按它算。Claude Code 是本项目的开发宿主，它的投影等于原样复制，最保守。
DEFAULT_HOST = "claude-code"

#: 探测的优先级：**越专用的标记越靠前**。
#:
#: `.claude/` 几乎每个被 Claude Code 碰过的仓库都有（本仓库就有一个），
#: `.dsh/` 只有专门为 dsh 配过的项目才有——罕见的标记是更强的证据。
#: 环境变量扫描用同一张表：嵌套时（在 Claude Code 的终端里起 dsh）两个变量都在，
#: 而真正在跑技能的是内层的 dsh。
HOST_ORDER = ("dsh", "codex", "workbuddy", "openclaw", "claude-code")


class UnknownHost(ValueError):
    """认不出的宿主名，或档案本身写坏了。**拼错不静默**：静默会走缺省，而缺省是另一个宿主。"""


@dataclass(frozen=True)
class HostProfile:
    """一个宿主的全部静态事实。字段少而硬，加字段要同时加出处。"""

    #: 档案文件名（不带 .json），也是 `--host` 的取值
    name: str
    #: 给人看的名字，进 doctor 与文档
    label: str
    #: 用户级技能目录，可带 `~`，也可带 `${NAME:-缺省}`（环境变量非空就用它，
    #: 否则用缺省；见 expand_vars）——宿主的技能根本身就是变量的，档案得跟着变量走
    skills_dir: str
    #: 项目级技能目录（相对项目根）；没有就是空串
    project_skills_dir: str = ""
    #: 宿主会不会把 SKILL.md 里的 `$ARGUMENTS` 换成用户的参数
    substitutes_arguments: bool = True
    #: 宿主认不认 frontmatter 的 `allowed-tools`
    honors_allowed_tools: bool = True
    #: 怎么叫起来。四个宿主目前都是 `/name`
    invocation: str = "/name"
    #: 调用方式的细节差异（进文档与托管块）
    invocation_note: str = ""
    #: 宿主自己暴露的「当前模型」环境变量，按可信度排；多数宿主没有
    executor_model_env: tuple[str, ...] = ()
    #: 只有这个宿主该跑的自检项，名字来自 oss/hosts/checks.py 的 CHECKS
    doctor_checks: tuple[str, ...] = ()
    #: 宿主读的项目指令文件（放 OSS 托管块）；没有就是空串
    managed_doc: str = ""
    #: 宿主要不要一份技能索引文件；不要就是空串（四个宿主都不要，理由见档案 source）
    index_file: str = ""
    #: 探测用：这些环境变量任一非空就判定是这个宿主
    detect_env: tuple[str, ...] = ()
    #: 探测用：cwd 下这些目录任一存在就判定是这个宿主
    detect_markers: tuple[str, ...] = ()
    #: 一句话限制，原样进 docs/user/hosts.md（有测试盯着不许漂移）
    limits: str = ""
    #: 上面每条事实是从哪儿看来的
    source: str = ""

    def user_dir(self, env: dict | None = None) -> Path:
        """解析后的用户级技能目录。**要落盘或要比对的地方一律用它，不用 skills_dir。**"""
        return Path(expand_vars(self.skills_dir, env)).expanduser()

    def project_dir(self, cwd: Path | str | None = None) -> Path | None:
        if not self.project_skills_dir:
            return None
        return Path(cwd or Path.cwd()) / self.project_skills_dir


_TUPLE_FIELDS = ("executor_model_env", "doctor_checks", "detect_env", "detect_markers")


def available() -> list[str]:
    """有哪些宿主档案。加一个宿主 = 往这个目录放一份 <名字>.json，不用改代码。"""
    return sorted(p.stem for p in HERE.glob("*.json"))


def load(name: str) -> HostProfile:
    p = HERE / f"{name}.json"
    if not p.exists():
        raise UnknownHost(
            f"不认识的宿主 {name!r}；已知：{'、'.join(available())}。"
            f"要加一个：往 {HERE} 放一份 <名字>.json"
        )
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise UnknownHost(f"{p.name} 不是合法 JSON：{exc}") from exc
    known = {f.name for f in fields(HostProfile)}
    unknown = sorted(set(d) - known)
    if unknown:
        # 拼错一个键就静默走缺省，是配置文件最常见的坑。这里当错误报。
        raise UnknownHost(f"{p.name}：不认识的字段 {unknown}；可用字段：{sorted(known)}")
    prof = HostProfile(**{k: (tuple(v) if k in _TUPLE_FIELDS else v) for k, v in d.items()})
    if prof.name != name:
        raise UnknownHost(f"{p.name}：档案里的 name 是 {prof.name!r}，跟文件名对不上")
    from oss.hosts.checks import CHECKS      # 局部 import：checks 要用 HostProfile 做注解

    bad = [c for c in prof.doctor_checks if c not in CHECKS]
    if bad:
        raise UnknownHost(f"{p.name}：doctor_checks 里的 {bad} 没有实现；"
                          f"已实现：{sorted(CHECKS)}")
    return prof


def detect_reason(cwd: Path | str | None = None,
                  env: dict | None = None) -> tuple[str, str]:
    """探测宿主，并且**把依据一起给出来**。

    只报结论不报依据的探测最难查：用户看到 `dsh`，不知道是因为环境变量还是因为目录里有 `.dsh/`，
    也就不知道该改哪儿。三级优先：

    1. `OSS_HOST` —— 用户明说的
    2. 运行期环境变量 —— 来自**正在跑我们的那个宿主**，比目录可信
    3. 项目目录标记 —— 说的是「这个项目配过谁」，二义时按 HOST_ORDER 取，并把另一份证据说出来
    """
    env = os.environ if env is None else env
    cwd = Path(cwd) if cwd is not None else Path.cwd()

    if env.get("OSS_HOST"):
        return env["OSS_HOST"], f"环境变量 OSS_HOST={env['OSS_HOST']}"

    for name in HOST_ORDER:
        prof = load(name)
        hit = [v for v in prof.detect_env if env.get(v)]
        if hit:
            return name, f"环境变量 {hit[0]}"

    seen: list[tuple[str, str]] = []
    for name in HOST_ORDER:
        prof = load(name)
        for marker in prof.detect_markers:
            if (cwd / marker).exists():
                seen.append((name, marker))
                break
    if seen:
        name, marker = seen[0]
        extra = ""
        if len(seen) > 1:
            others = "、".join(m for _, m in seen[1:])
            extra = f"（同时看到 {others}，要别的宿主请加 --host）"
        return name, f"目录标记 {marker}{extra}"

    return DEFAULT_HOST, "没有任何标记，按缺省"


def detect(cwd: Path | str | None = None, env: dict | None = None) -> str:
    return detect_reason(cwd, env)[0]
