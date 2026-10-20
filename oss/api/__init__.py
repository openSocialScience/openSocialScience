"""api 层：一份逻辑，三个门面。

    cli/  散文        解析参数 → 调这里 → 打印
    cli/  --json      解析参数 → 调这里 → 打一行 JSON
    mcp/  工具        协议解包 → 调这里 → 包成 MCP 返回

**这一层不打印、不退出、不 import typer。**它吃路径与标量，
吐一个 JSON 可序列化的字典，或者抛一个自己带退出码的异常。

分层（补 `oss/__init__.py` 的那三行）：

    core/   确定性计算   —— 宿主无关
    gates/  程序化的门   —— 各返回 JSON 决定
    host/   宿主适配     —— 审稿后端、运行目录
    api/    可调用的核   —— core + gates + host 的编排；**落盘与留痕在这一层**
    cli/    命令行门面
    mcp/    MCP 门面

**api/ 可以 import host/，core/ 仍然不许。**落盘与留痕放在 api 而不是 cli，
是这一阶段最要紧的一条：MCP 调一次 `oss_run` 和命令行跑一次 `oss run`，
盘上（`results.json`、`trace.jsonl`、`run_state.json`）必须一模一样——
否则留痕就答不出「这个数字是谁在什么时候算的」，而那正是留痕存在的全部理由。
"""

from oss.api.errors import ApiError, BadInput, Missing, Refused, Unavailable
from oss.api.result import result

__all__ = ["ApiError", "BadInput", "Missing", "Refused", "Unavailable", "result"]
