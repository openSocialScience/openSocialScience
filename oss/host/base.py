"""宿主适配层的运行期部分。

**这一层是迁移时唯一要换掉的东西**（DECISIONS.md 1.4）。
`core/` 绝不 import 这里；skill 也绝不直接碰宿主，只调 `oss xxx` 命令。

这里只剩两样真东西：运行目录 `RunDir`（本文件）与跨模型审稿后端 `reviewer.Reviewer`。
**各宿主的静态差异在 `oss/hosts/`**（技能目录、$ARGUMENTS、allowed-tools、自检项）。

阶段 4 删掉了 `Host` 协议与 `ClaudeCodeHost`：前者没有任何实现者，后者的 `review()`
返回 `FAKE_阶段 0 不调用真实审稿模型`，而真实审稿早在阶段 3 就走 `reviewer.Reviewer` 了。
**留着的假路比没有更糟**——它看起来是一条可选实现，实际上谁调谁拿到假判词。
"""

from __future__ import annotations

import os
from pathlib import Path

from oss.core.contracts import TraceEvent


class RunDir:
    """一次运行的工作目录。产出物落盘的唯一入口。

    状态全在文件里，不在对话里——这样 agent 中断后能从盘上恢复，
    换一个 agent 接手也能继续。
    """

    def __init__(self, root: Path | str | None = None, run_id: str | None = None) -> None:
        base = Path(root) if root else Path(os.getcwd()) / "runs"
        self.run_id = run_id or _new_run_id()
        self.path = base / self.run_id
        self.path.mkdir(parents=True, exist_ok=True)
        self._seq: int | None = None

    def file(self, name: str) -> Path:
        return self.path / name

    def next_seq(self) -> int:
        """留痕的流水号。**基数从盘上的 trace.jsonl 数，不从 0 数。**

        这里原先是个进程内计数器。而每一个 `oss xxx` 都是独立进程——
        一次运行有几十个命令，于是**每一条留痕的 seq 都是 1**，
        留痕文件没法自己排序。真跑一次 40 多步，40 多个 1。

        留痕的用处就是事后复原「谁先谁后」。全是 1 等于这个字段不存在，
        而它看起来又像是存在的——**比没有更糟**。
        """
        if self._seq is None:
            trace = self.file("trace.jsonl")
            try:
                with trace.open(encoding="utf-8") as f:
                    self._seq = sum(1 for line in f if line.strip())
            except OSError:
                self._seq = 0
        self._seq += 1
        return self._seq

    def append_trace(self, event: TraceEvent) -> None:
        with self.file("trace.jsonl").open("a", encoding="utf-8") as f:
            f.write(event.to_line() + "\n")


def _new_run_id() -> str:
    import uuid

    return f"run_{uuid.uuid4().hex[:8]}"
