# 环节 3 · 找空白、想 idea、把题目要的数据拿到

## 先分岔：输入里有没有 `idea:`

- **没有** → 照下面走：`/social-idea` 出十个透镜的候选、陪审团砍题、选一个。
- **有** → 走**整理模式**：不出题，把用户那段原文整理成 `ideas.json` 里**唯一**的一条
  `idea_001`（`source: "user"`、`lens: "user-idea"`），十个透镜只用来补 `theory_hook`
  与 `value_if_null` 两栏；陪审团**只记录不淘汰**；整理稿回给用户确认一次
  （checkpoint `idea`，带 `idea:` 时缺省就停这一次，`checkpoint: none` 才不停）。
  四步的细则在 `.claude/skills/social-idea/reference/tidy-mode.md`，调用时把原文一起给它：

  ```
  /social-idea <运行目录> --- discipline: <学科> --- idea: <原文，一个字不改>
  ```

  **模型可以检视，不能替换。**用户的想法哪怕理论钩子弱、空结果没价值、缺数据，
  都原样留在文件里并写进论文局限——**淘汰它就是换题，而换题是用户的决定**。

## 输入
- `<运行目录>/literature.json`、`reading_cards.json`、`profile.json`
- `run_state.stage == "3-ideas"`

## 产出
- `<运行目录>/ideas.json`（由 `social-idea` 产出）与 `review_ideas.json`（陪审团）
- `ideas.json` 顶层两栏由这一环写满：`generated_n`（发散段产出了多少条）与
  `unreachable`（清单乙：更值得做但这份数据做不了的编号）
- 每条候选的 `prescreen` 栏（经验预检的证据）与 `prescreen_<idea_id>.json`
- 题目需要的补充数据文件与重新画像后的 `profile.json`

## 完成条件（全部满足才进环节 4）
- 调过 `/social-idea <运行目录> --- discipline: <学科>`（自带想法时再带 `--- idea: <原文>`），`ideas.json` 在盘上
- 每个候选都写了 `research_type`（按「想下什么结论」定：想说 X 影响 Y 就是 `causal`；输入带 `--- type:` 的一律照它）
- `research_type` 是 `descriptive` 的候选，要说清**新在哪**：新数据、新事实、新测量，或模式出人意料、跟现有理论对不上。
  **经济学的纯描述门槛最高**：要有新数据、新事实或新测量（`.claude/skills/social-idea/reference/discipline-profiles.md` 的「学科 × 研究类型的门槛」）。
  陪审团会问这一句，说不出新在哪的描述题砍掉——描述本身可以是贡献，但把一个均值再算一遍不是
- 逐个确认三件事并记下数目：`theory_hook` 为空的几个（**强负面信号**）、`value_if_null` 答不上的几个（p-hacking 的根源）、`needs_extra_data` 非空的几个
- `needs_extra_data` 里的每一条都**先搜过列再说没有**（`oss data columns <包> --search "..."`），搜得到的就拿、拼好、重新画像
- **陪审团判完之后停一次，把选题摆给用户看**（缺省 checkpoint `ideas`，细则见下）
- **对存活候选跑经验预检**（最多前 8 个），据此**重排**：构念绑不绑得上、最小可测效应多大、
  焦点变量像不像样。细则在 `.claude/skills/social-idea/reference/prescreen.md`。
  **这一段是环节 3 从原环节 4 吃下来的**——以前是选定题目之后才验证，验证结果改变不了选择
- 预检发现做不了的，**不扣分**：编号进 `unreachable`，`needs_extra_data` 写清缺什么、去哪拿
- `generated_n` 与文件里的条数**必须相等**——多出来是拿变量清单回头造题，少下去是把砍掉的候选删了
- 拿不到的如实留在 `needs_extra_data` 里，题目留在清单里不砍
- **自带想法时另加三条**：`ideas.json` 里**只有一条**且 `idea_id` 是 `idea_001`、`source` 是 `user`；`ideas.json` 的 `direction` 与 `idea:` 原文**逐字相同**；`review_ideas.json` 在盘上（判词已写回那一条的 `jury_verdict` / `jury_reason`）
- **自带想法时还要停一次**：整理稿的九栏（哪九栏见 `social-idea/reference/tidy-mode.md` 第 3 步）+ 研究类型 + 陪审团三条意见 + `needs_extra_data` 一次性回给用户确认（checkpoint `idea`）；X 或 Y 留空的必须补齐才继续
- `run_state.stage` 推进为 `4-feasibility`

## 禁止
- 在生成阶段按质量淘汰候选——淘汰是陪审团的事
- 让"更值得做但这份数据做不了"的题被"能跑的那个"顶替；它要**单独交付**
- 按国家名拼补充数据（按 ISO3 代码拼；只有名字就走 `oss merge` 的对照表流程）
- 把搜到的指标当成答案："这个指标是不是我要的构念"是你的判断
- 拿别的数据顶替要申请的数据集
- **自带想法时**：改写 `direction`（那是用户的原话）、把 `idea_001` 换成别的编号、在它旁边再加一条候选、因为陪审团判词不好看就把它删掉或改题——这四样都是换题：还没跑过 `oss run`（锚点没冻结）的，把要换的内容和理由告诉用户，拿到一句同意再改 `ideas.json`；已经跑过分析的，只能 `oss anchor switch --to <idea_id> --why ... --authorized-by user`

## 命令
```
/social-idea <运行目录> --- discipline: <学科>
```
**这一环交给专门的 skill 做**——方法论很厚（十个分析透镜、学科档案、强制门），塞进主链会让主链没法读。

```bash
oss data needs --run <运行目录>          # 候选缺的数据，合并同类项、标可信度
oss data search "internet users share"
oss data columns vdem --search "censor, internet, disinformation"
oss data fetch owid share-of-individuals-using-the-internet --run <运行目录>
oss data get <名> --columns ... --run <运行目录>
oss merge <主数据> <运行目录>/owid_xxx.csv --left-on country,year --right-on iso3,year --run <运行目录>
```

## 停一次，把选题摆给用户看（缺省 checkpoint `ideas`）

陪审团判完之后停下来。**这不是走过场**：技术上站不站得住由陪审团判，
**值不值得做由用户判**——他关不关心这个问题、知不知道这方向已经做烂了，
只有他答得上；而这一步选错，后面七十分钟全白做。

一屏之内给他这些，然后**等他回话**：

**摆的是两份清单，不是一份**：甲是这份数据能做的（排过序、带预检证据），
乙是更值得做但做不了的（带缺什么、去哪拿）。只交甲那份，等于替他把研究降级成
「这份数据恰好允许的那些题」，而且降得看起来很客观。

1. **留下的候选**，每个一行：题目 + 陪审团那句判词的**原话**（一两句，不转述）+ 预检的数（`mde` 或区间半宽）
2. **被砍掉的也列出来，带理由**——万一陪审团判错了、或者用户恰好知道内情，
   他能捞回来。**只列不劝**
3. 每个候选**缺什么数据**：跑 `oss data needs --run <运行目录>`，把那张表原样贴上
4. 每个候选的两栏：**接的是哪个理论争论**（`theory_hook`）、
   **结果是零它还成不成立**（`value_if_null`）。第二栏答不上的要点出来——
   价值绑在结果方向上的题，是硬凑结论的温床
5. 每个候选的**研究类型**（`research_type`：因果 / 描述 / 测量 / 仿真）——想下因果结论就要过识别审计，
   只描述就不许写因果的话。**他可以改**，改了就照改

**问他一句话就够**：

> 这几个题，你要哪个？（也可以改措辞，或者都不满意让我重出一批）

**不要问他「哪个设计更严谨」**——那是陪审团已经做完的事，也是他答不上的问题。

他的三种回法：

- **挑一个** → 往下走
- **改措辞** → 改完那条就算他的题了：`source` 记 `user`，走自带想法那条路
- **都不要** → 按他新给的说明重出一批

**记下来**：他选过或改过之后，`ideas.json` 里那条的 `source` 写 `user`——
**「这个研究问题是人定的」对论文是有意义的事实**，不是流程细节。

`checkpoint: none` 时不停（验收跑测流程用），但收尾说明里仍要把上面五样列全。

## 卡住时读
[3-ideas.notes.md](3-ideas.notes.md)——三件事各自的含义、三个数据源的脾气、"那个模块就在 vdem 包里没人去搜"的评审第四条。
