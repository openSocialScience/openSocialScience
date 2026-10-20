"""oss 到哪儿找 `.env`——全项目唯一定义这条规则的地方。

三处历史代码各自抄了一遍「cwd 往上找第一份 `.env`」：`oss/cli/main.py` 的
`_load_env`、`oss/hosts/checks.py` 的 `_dotenv`、`oss/api/sim.py` 的
`dotenv_near`。那条规则只在**仓库目录里跑**才成立——用户在
`~/Desktop/ossrun-d/r1` 这类自己的研究目录里跑，三处全部找不到 `.env`，
`oss doctor` 报「审稿 未配置」，`oss review` 拿不到 key。

按优先级给出候选（数字越小越先）：

1. cwd 向上找到的第一份 `.env`（项目本地——原来的行为，不变）
2. `~/.config/oss/.env`（用户级——pip 安装、不在仓库里跑时落这儿）
3. 源码检出自己的 `.env`（仅当确认是可编辑开发安装；装进 site-packages 的
   包永远不会命中——见 `_is_editable_checkout`）

真正读取时用 `os.environ.setdefault`：已经在环境里的值永远赢；候选之间
按上面的顺序，先出现的赢。
"""

from __future__ import annotations

import os
from pathlib import Path

import oss

#: 用户级配置——不在仓库目录里跑时的落点。
USER_ENV = Path.home() / ".config" / "oss" / ".env"


def _walk_up_env(start: Path) -> Path | None:
    """从 `start` 向上找到的第一份 `.env`；一路到根目录都没有就是 None。"""
    for parent in [start, *start.parents]:
        cand = parent / ".env"
        if cand.exists():
            return cand
    return None


def _source_checkout_root() -> Path:
    """`oss` 包上跳两层——仓库检出该长的样子。

    独立成函数是为了测试能把它整个换掉：真仓库正好满足
    `_is_editable_checkout` 的条件，不该被跟这条规则无关的测试意外读到。
    """
    return Path(oss.__file__).resolve().parents[1]


def _is_editable_checkout(root: Path) -> bool:
    """`root` 是不是本仓库的可编辑开发安装：`pyproject.toml` 里声明
    `name = "opensocialscience"`。`pip install opensocialscience` 装进
    site-packages 的那份没有 `pyproject.toml`，天然不会命中——不用另外判断
    是不是在 site-packages 里。
    """
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return False
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return False
    return 'name = "opensocialscience"' in text


def _source_checkout_env() -> Path | None:
    """源码检出自己的 `.env`；不是可编辑开发安装就是 None。"""
    root = _source_checkout_root()
    return (root / ".env") if _is_editable_checkout(root) else None


def search_paths() -> list[Path]:
    """按优先级列出**找过的地方**——只给路径，不给值。

    `oss doctor` 的「未配置」消息用它告诉用户该把 `.env` 建在哪；cwd 向上
    那一档只报 cwd 本身当起点（真正搜索会一路往上到根目录，消息里给出发点
    就够定位，不必把每一层父目录都列出来）。源码检出这一档只在确认是
    可编辑开发安装时才出现——不然会给用户一条 site-packages 里的误导路径。
    """
    out = [Path.cwd() / ".env", USER_ENV]
    src = _source_checkout_env()
    if src is not None:
        out.append(src)
    return out


def search_paths_for_message() -> list[str]:
    """`search_paths()` 的人话版——**只给消息用**。

    `search_paths()` 第一项字面是 `cwd/.env`，直接拼进消息会让人以为只查了
    cwd 自己那一份；实际上这一档会一路往上走到根目录（见 `_walk_up_env`）。
    这里把第一项换成一句话说清楚，其余几项照样给路径字符串——不给值。
    """
    paths = search_paths()
    out = [f"{paths[0].parent} 往上各级的 .env"]
    out.extend(str(p) for p in paths[1:])
    return out


def candidates() -> list[Path]:
    """按优先级排好、去重（按 resolve 后的真实路径）、只留**存在**的候选——
    `load_env()` 真正读取的顺序。"""
    found: list[Path] = []
    seen: set[Path] = set()

    def add(p: Path | None) -> None:
        if p is None or not p.exists():
            return
        rp = p.resolve()
        if rp in seen:
            return
        seen.add(rp)
        found.append(p)

    add(_walk_up_env(Path.cwd()))
    add(USER_ENV)
    add(_source_checkout_env())
    return found


def load_env() -> None:
    """把每一份候选 `.env` 按优先级读进环境。

    key 只在这里进 `os.environ`，不出现在任何输出里。用
    `os.environ.setdefault`：真正的环境变量、以及排在前面的候选文件里
    已经设过的键，都不会被后面的候选覆盖。
    """
    for env in candidates():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
