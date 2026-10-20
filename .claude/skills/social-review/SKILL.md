---
name: social-review
description: 把写好的社科论文初稿交给别家模型审，产出可行动的修改清单。审的是「话」不是「数字」——数字已由占位符机制保证。被 social-research 主链的环节 8 调用，也可单独使用。
argument-hint: "[运行目录] [level: quick|normal|deep]"
allowed-tools: Bash(oss *), Read, Write, Edit, Glob, Grep
---

# 论文的跨模型审稿 · 路由器

原始输入：**$ARGUMENTS**

如果上面显示的是字面的 `$ARGUMENTS`，说明宿主不替换占位符；原始输入就是用户消息里 `/social-review` 之后的全部内容。
用 `---` 分段：第一段是运行目录，其后 `round:` 与可选的 `level:`。

**这份文件只负责路由。**按 [manifest.yaml](manifest.yaml) 做两件事：

1. `always_load`：[contract.md](contract.md)（四栏，唯一的验收标准）与 [reference/blind-discipline.md](reference/blind-discipline.md)（**互盲纪律一条都不能省**）
2. 按 `references.on_demand` 只读用得上的：

| 什么时候读 | 读哪一份 |
|---|---|
| 想知道这一步为什么存在 | [reference/why.md](reference/why.md) |
| `oss review` 非零退出 | [reference/exit-codes.md](reference/exit-codes.md) |
| 判词回来了、要决定下一轮 | [reference/after-verdict.md](reference/after-verdict.md) |

**判 PASS 也不要说「这篇可以投」。**一次跨模型审稿不等于同行评议——引用还没核过、识别策略还没审过、估计量也只有 OLS + 固定效应。
