"""MCP 门面：只做协议，不做业务。

工具的名字与参数在 `tools.py`，传输在 `server.py`。
**逻辑一行都不在这里**——它全在 `oss/api/`，CLI 调的是同一批函数。
所以「MCP 跑一次」和「命令行跑一次」，盘上的 `results.json`、`trace.jsonl`、
`run_state.json` 一模一样。
"""
