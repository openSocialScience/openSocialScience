"""`oss loop status`：循环走到哪了。**判断归审稿人，计数与放行归这里。**"""

from __future__ import annotations

from oss.api.result import result
from oss.api.rundir import require_run
from oss.core import loop as loop_mod
from oss.core import state as state_mod
from oss.gates import anchor as anchor_gate

#: 退出码就是答案。**说明书按这三个数分支，不许改。**
EXIT_BY_STATUS = {"exhausted": 4, "in_progress": 3}


def status(run: str) -> dict:
    """第几轮、收敛没有、因果审计门、锚点漂没漂。

    `decision` 是这条命令的**主要产出**（`loop.decide()` 给的四选一），
    所以它上到顶层，不埋在 data 里——模型只该照着它做，不该从散文里猜。

    只读，所以走 `require_run`：目录不存在就抛 `Missing`，**不建**。
    """
    rundir = require_run(run)
    st = loop_mod.loop_status(rundir.path)
    rs = state_mod.load(rundir.path)
    code = EXIT_BY_STATUS.get(st.status, 0)
    data = {
        "run": str(rundir.path),
        "round": st.round,
        "max_rounds": st.max_rounds,
        "status": st.status,
        "converged": st.converged,
        "exhausted": st.exhausted,
        "identification_gate": st.identification_gate,
        "identification_note": st.identification_note,
        "rounds": [
            {"round": r.round, "n_reviews": r.n_reviews, "verdicts": list(r.verdicts),
             "design_open": r.design_open, "data_open": r.data_open,
             "text_open": r.text_open, "new_open": r.new_open, "resolved": r.resolved,
             "design_items": list(r.design_items)}
            for r in st.rounds
        ],
        "anchor": dict(st.anchor),
        "state": {"stage": rs.stage, "stage_status": rs.stage_status,
                  "frozen_idea_id": rs.anchor.get("idea_id", "")},
        "anchor_drift": anchor_gate.check(rundir.path),
        "blockers": list(st.blockers),
        "loop_state_path": str(rundir.file("loop_state.json")),
    }
    return result(f"{st.decision['decision']}｜{st.decision['reason']}", data,
                  decision=st.decision, exit_code=code)
