---
name: social-idea
description: 从文献语料和数据形状里找出真正的研究空白，生成候选社科定量研究 idea。用十个分析透镜找角度（六个默认开、四个按学科开），机械去重，客观可行性筛选，产出 ideas.json。被 social-research 主链的环节 3 调用，也可单独使用。
argument-hint: "[运行目录] [discipline: economics|sociology|political-science|psychology|communication]"
allowed-tools: Bash(oss *), Read, Write, Glob, Grep, Agent
---

# 找空白，生成 idea · 路由器

原始输入：**$ARGUMENTS**

如果上面显示的是字面的 `$ARGUMENTS`，说明宿主不替换占位符；原始输入就是用户消息里 `/social-idea` 之后的全部内容。
用 `---` 分段：第一段是运行目录，其后 `discipline:`、可选的 `idea:`（用户自带的想法原文）。

**移植自 ARIS 的 `idea-creator-social-science`**，改掉了宿主调用，对齐了本项目的产出契约。

**这份文件只负责路由。**按 [manifest.yaml](manifest.yaml) 做两件事：

1. `always_load`：[contract.md](contract.md)（四栏，唯一的验收标准）与 [reference/iron-laws.md](reference/iron-laws.md)
2. 按 `references.on_demand` 只读用得上的：

| 什么时候读 | 读哪一份 |
|---|---|
| **输入带 `idea:`**——不出题，整理用户那一条 | [reference/tidy-mode.md](reference/tidy-mode.md) |
| 开工前、或方向像是质性研究 | [reference/why-social-science.md](reference/why-social-science.md) |
| 拿到 `discipline` 之后 | [reference/discipline-profiles.md](reference/discipline-profiles.md) |
| 步骤 1–5：建地图、过透镜、去重、筛选、写文件（**带 `idea:` 时整套不跑**） | [reference/lenses.md](reference/lenses.md) |
| 步骤 5：对存活候选跑经验预检、据此重排 | [reference/prescreen.md](reference/prescreen.md) |
| 步骤 6–7：定向查重定位、交跨模型评议 | [reference/jury.md](reference/jury.md) |
