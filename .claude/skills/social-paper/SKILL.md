---
name: social-paper
description: 把分析结果写成一篇社科定量论文的初稿。产出 paper.md，正文只用占位符引用统计结果，不手打数字。被 social-research 主链的环节 7 调用，也可单独使用。
argument-hint: "[运行目录] [discipline: economics|sociology|political-science|psychology|communication]"
allowed-tools: Bash(oss *), Read, Write, Edit, Glob, Grep
---

# 写论文初稿 · 路由器

原始输入：**$ARGUMENTS**

如果上面显示的是字面的 `$ARGUMENTS`，说明宿主不替换占位符；原始输入就是用户消息里 `/social-paper` 之后的全部内容。
用 `---` 分段：第一段是运行目录，其后 `discipline:` 与可选的 `style:`、`lang:`（`zh` = 写中文论文）。

**这份文件只负责路由。**动笔前按 [manifest.yaml](manifest.yaml) 做两件事：

1. 读 [contract.md](contract.md)——输入、产出、完成条件、禁止四栏，**这是唯一的验收标准**
2. 按 `references.on_demand` 只读用得上的那几份 `reference/`，不要一次全读

| 什么时候读 | 读哪一份 |
|---|---|
| 起稿、带了 `--- style: <刊名>` | [reference/english-and-style.md](reference/english-and-style.md) |
| 带了 `--- lang: zh`（写中文论文） | [reference/chinese.md](reference/chinese.md) |
| 要引结果、排表、嵌图、加引用；校验拦下来了 | [reference/placeholders.md](reference/placeholders.md) |
| 校验说正文有研究过程的痕迹 | [reference/no-process-language.md](reference/no-process-language.md) |
| 动笔前 | [reference/read-first.md](reference/read-first.md) |
| 写具体某一节 | [reference/sections.md](reference/sections.md) |

**不要声称这篇稿子可以投。**它是初稿，没有经过跨模型审稿，也没有经过引用核验。
