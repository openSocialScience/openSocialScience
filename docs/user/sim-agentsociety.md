# AgentSociety 安装单

`sim: agentsociety` 这条插头要连得上真的 AgentSociety（v2）才能跑模拟。这份单子只管把
环境装起来、把电通上；跑单模板（怎么填九个 `{{}}` 空位——八个必填、一个可选
`{{CONCURRENCY}}`，不填缺省 8）是另一回事，在 `oss/sims/agentsociety/runner_template.py`。

**本插头未经真跑验收**：目前只做过合成样张集成（不调模型 API），装机步骤照上游
仓库与包元数据核实过，但整条链没烧过真钱——首跑按 §七 的铁律来。

---

## 一、建一个 Python 3.11 的 conda 环境

```bash
conda create -n agentsociety python=3.11 -y
conda activate agentsociety
```

`agentsociety2` 的 `pyproject.toml` 写死要 `>=3.11,<3.14`——3.11 最稳妥。跟 OASIS 的
环境**分开建**：两家依赖都重（见 §六 的 ray 那条），塞一个环境里迟早打架。

---

## 二、pip 装包 + clone 拿样本档案

```bash
pip install agentsociety2==2.8.4

git clone https://github.com/tsinghua-fib-lab/agentsociety.git ~/Documents/agentsociety
cd ~/Documents/agentsociety
git checkout agentsociety2-v2.8.4    # 让克隆里的样本档案与装的包同版本
```

三件事别弄混：

- **包名是 `agentsociety2`，不是 `agentsociety`**——同一个 GitHub 仓库里住着两代产品
  （§六 坑表第一行），pip 装 `agentsociety` 拿到的是 v1 遗留版，配置格式、CLI、落盘
  全不同，装错代=另一个产品。
- **包从 pip 装，clone 只为样本档案**——wheel 只打包 `agentsociety2/` 包目录，人群
  样本 `profiles.json` 留在仓库里（§四），所以两步都要做，但不用 `pip install -e` 装克隆。
- **克隆目录必须正好是 `~/Documents/agentsociety`**——`oss/sims/agentsociety/profile.json`
  的 `sample_base` 记的就是这个位置，换个目录名或换个位置，装得再对
  `oss sim doctor` 也会说样本数据不在。

---

## 三、中转站环境变量

跑单模板认的还是 `BASE` / `KEY` 这一对名字（与 OASIS 跑单同一套操作习惯），跑之前
自己 export（值通常跟项目根 `.env` 里的 `REVIEWER_*` 一样）：

```bash
export BASE=https://api.deepseek.com/v1
export KEY=sk-...
python raw/A_1.py     # 跑单模板填好空位另存的那份
```

跑单启动时会把它们映射成 AgentSociety 自己认的名字再喂给 CLI 子进程：

| 跑单设的变量 | 从哪来 | 干什么用 |
|---|---|---|
| `AGENTSOCIETY_LLM_API_BASE` | ← 你 export 的 `BASE` | 中转站地址（上游必需，缺了它模块导入就抛异常） |
| `AGENTSOCIETY_LLM_API_KEY` | ← 你 export 的 `KEY` | 中转站钥匙（同上，必需） |
| `AGENTSOCIETY_LLM_MODEL` | 模板 `MODEL_TYPE` 常量（手改，不是空位） | 用哪个模型 |
| `AGENTSOCIETY_LLM_RAY_CONCURRENCY` | 模板 `{{CONCURRENCY}}` 空位，不填缺省 **8** | 并发初值 |

**别自己预先 export `AGENTSOCIETY_LLM_*` 来代替 `BASE`/`KEY`**——跑单见不到
`BASE`/`KEY` 照样拒跑，起来了也会用自己的映射值覆盖你设的。

并发缺省 8 不是随便取的：上游默认 16 且带 AIMD 自适应（压垮了会自己降），但 AIMD
只保证「垮了能降」、不保证「垮之前不疼」——本仓中转站史（OASIS 用旧默认 32 两次
压垮中转站、改 8 实测稳，2026-08-31）主张保守起步。要调大，在整理稿层面明确写出
数字，并记得 `profile.json` 的 `defaults.concurrency` 与模板缺省要一起改。

---

## 四、样本数据在哪

`git clone` 已经把样本档案一起拉下来了，不用另外下载：

```
~/Documents/agentsociety/packages/agentsociety2/profiles.json
```

JSON 数组 100 人，每条记录带 `id/age/gender/education/occupation/consumption/home/work`。
`oss/sims/agentsociety/profile.json` 的 `sample_base`（`~/Documents/agentsociety`）拼上
`sample_data`（`packages/agentsociety2/profiles.json`）就是这条路径，`oss sim doctor`
按它去找——**各插头按自家 `sample_base` 解析**，OASIS 克隆在不在与这条无关。

另存跑单脚本时，把这份档案**复制**一份到脚本同目录、改名成 `profiles_full.json`——
`runner_template.py` 的 `load_profiles()` 就认这一个文件名：

```bash
cp ~/Documents/agentsociety/packages/agentsociety2/profiles.json \
   <货箱>/raw/profiles_full.json
```

复制而不是留着引用原路径，理由与 OASIS 安装单同一条：**箱子必须自含**——论文方法节
引用的是箱子里的文件，不是你机器上跑完可能就删掉、换机器也不在的临时克隆。

跑单工作流长这样（与 OASIS「一场跑一个 .db」不同）：每个 condition × rep 把模板
填好另存成 `raw/<条件>_<第几次>.py`（如 `raw/A_1.py`），跑起来各自建**并排的跑目录**
`raw/<条件>_<第几次>/`——`replay/` 分片、`artifacts/` 问卷、`run_summary.json`、
`meta_sidecar.json` 都落在里面；装箱时 `oss sim pack --simulator agentsociety` 读的
就是这些跑目录。

---

## 五、体检：`oss sim doctor --simulator agentsociety`

```bash
oss sim doctor --simulator agentsociety
```

**这条命令的退出码永远是 0**——不管三条体检过没过，都不会报错退出；doctor 只出体检报告，
不做通过检测。看每一行前面的 `✓`/`✗` 自己判断，别看命令没报错就以为都通过了。（唯一会让它
非 0 退出的情形是 `--simulator` 拼错成不认识的名字，那是另一种「输入错误」，不是体检没过。）

三条分别查什么、容易踩的坑：

- **包可导入且版本相符**：查的是**运行 `oss` 这条命令本身所在的 Python 环境**里有没有装
  `agentsociety2`==2.8.4——不是你在第①节新建的 `agentsociety` 这个 conda 环境！这两个
  环境常常是分开的（`oss` 装在 OSS 仓库自己的环境里，AgentSociety 装在专门的环境里），
  分开装的话这一条大概率显示 `✗`，**不代表 AgentSociety 没装好**，只是 doctor 在错的
  环境里找包（OASIS 安装单 §五 的同款坑）。想让这条也过，把 `oss` 也装进
  `agentsociety` 这个环境：`conda activate agentsociety && pip install -e <OSS 仓库路径>`。
- **样本数据在**：查上面④那条路径存在不存在。路径由本插头 `profile.json` 自持的
  `sample_base` 拼出来——从前 doctor 对所有插头都拼 `~/Documents/oasis` 前缀，
  AgentSociety 的样本检查会在 OASIS 克隆缺席时误败，现已解耦（一个插头的体检不依赖
  另一个插头的安装）。
- **中转站变量在**：按三档报——`BASE`/`KEY` 都已 export（`✓`，跑单直接能起）；只有
  坐标可抄（`AGENTSOCIETY_LLM_API_KEY`+`API_BASE` 都在、或 `REVIEWER_BASE_URL` 在、或
  哪一档 `.env` 找得到——当前目录往上、`~/.config/oss/.env`、本仓库开发态检出自己的
  `.env`，见 `oss/core/envfile.py`——`✓` 但备注会提醒你跑之前还得照它 export
  `BASE`/`KEY`，跑单只认这两个名）；全都没有（`✗`）。之所以查 `BASE`/`KEY` 而不是上游
  文档里的 `AGENTSOCIETY_LLM_*`，是因为跑单**运行时真正要的**是前者（§三）。与 OASIS
  那条一样，它**不检查值对不对、也不管那个 `.env` 是不是这个项目的**——显示 `✓`
  不代表中转站真的配对了，真跑之前自己再核对一遍。

---

## 六、已知坑

| 坑 | 是什么 | 应对 |
|---|---|---|
| v1/v2 两代同仓分叉 | pip 包 `agentsociety`（1.x，论文原型的官方遗留版）与 `agentsociety2`（2.x 现役版）住在同一个 GitHub 仓库里（`packages/agentsociety` 与 `packages/agentsociety2` 两个目录、两套 tag），配置格式、CLI、落盘结构互不兼容——装错代=装了另一个产品，报错还长得一脸像「配置写错」 | 只装 `agentsociety2==2.8.4`；克隆 checkout `agentsociety2-v2.8.4` 标签；论文引用时写明用的是 v2 |
| 无全局 seed、无温度旋钮 | 上游不提供随机种子与 temperature 配置，AIMD 并发自适应还会让请求执行序不定——**设计上不可复现**，不是你哪里没配对 | 靠重复次数（rep）撑推断；配料单 `temperature` 如实填 `provider_default`，复现声明写「设计上不可复现，靠重复次数」（设计稿已决） |
| 问卷不推进 step | `questionnaire` 步不消耗模拟步：波号（wave）= `steps.yaml` 里问卷步的出现序号（0 起），同一 step 可以有多波，别拿 step 数反推波数 | 波次↔真实轮次的对应表写在配料单 `interview_plan` 里，以它为准；`states.csv` 的 `wave` 列自己不记轮次号 |
| ray 依赖链偏重 | `ray>=2.0` 是硬依赖（还连带一串重包），装得动但装得慢、占地大 | 按 §一 单独建 conda 环境；第一次 `pip install` 给足耐心，别装进别的项目环境里 |
| 钱类信号有 403 quota 马甲 | 中转站额度耗尽不一定报 402/Insufficient：2026-09-01 OASIS 扩样实跑撞上的是 HTTP 403 PermissionDeniedError「token quota is not enough…」——不含 402/余额/欠费任何一个老词，当时检测用的词表没认出来，险些放行下一场 | 本插头跑单模板的钱类词表已把 403+quota、PermissionDenied、token quota is not enough 都算断粮；见到钱类横幅**立即全停**，中转站断粮重试无用，先核对余额/充值再决定重跑 |
| target_kind 词表是本插头自定义的 | `events.csv` 的 `target_kind` 列：`follow`→`agent`，`like`/`comment`/`repost`→`post`，`post`（发帖）→空——与 OASIS 插头的 `user` 不同。**这是设计不是事故**：货箱标准不规定词表，一个箱子只装一个模拟器，两套词表不会在同一箱里相撞 | 单箱分析照本表读；要跨箱比较两家模拟器的数据，先自己把词表对齐（如 `user`↔`agent`）再合并 |

---

## 七、费用参考（**未实测**——回填前不许出现具体元数）

本插头没跑过真钱，这一节**不编数字**，只给估算框架：

- **调用量公式**：模型调用次数 ≈ `N_AGENTS × ROUNDS × k`。AgentSociety 是 ReAct
  智能体，每 agent 每 step 约 **3-10 次**模型调用（感知/反思/行动各来几轮），
  k 取 3-10；OASIS 同规模每 agent 每轮约 1 次——所以**同规模成本约为 OASIS 的
  3-10 倍量级**。要估上下界，拿 OASIS 安装单（`docs/user/sim-oasis.md` §七）的
  实测元数乘 3-10。
- **首跑铁律**：必须先小规模试水（如 5 人 × 2 轮）+ 中转站预算确认，超预算立即
  中断（跑单脚本开跑会自己打印这条 cost hint）。试水与正式跑完之后，拿**中转站
  用量页的实测数字**回填此表——在那之前，这张表就该空着。
- **口径提醒**：中转站报余额用的是美元（2026-09-01 实案里剩余额度显示 `＄0.03`），
  按「元」做预算时留足汇率与计价口径的余量。

| 规模 | 单场时间 | 总费用 |
|---|---|---|
| （待首跑实测回填） | — | — |
