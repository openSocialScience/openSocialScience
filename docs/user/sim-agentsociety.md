# AgentSociety 安装单

`sim: agentsociety` 要连上真实的 AgentSociety（v2）才能跑模拟。这份单子讲怎么装环境、配变量、
放样本数据和体检。跑单模板有九个 `{{}}` 空位，八个必填，可选的 `{{CONCURRENCY}}` 不填缺省 8，
填法见 `oss/sims/agentsociety/runner_template.py` 开头的说明。

这条插头还没有完整真跑过，第一次跑先按第七节小规模试水。

---

## 一、建一个 Python 3.11 的 conda 环境

```bash
conda create -n agentsociety python=3.11 -y
conda activate agentsociety
```

`agentsociety2` 要求 Python `>=3.11,<3.14`，用 3.11。跟 OASIS 的环境分开建，两家依赖都重
（见第六节 ray 那条），装在一起容易冲突。

---

## 二、pip 装包 + clone 拿样本档案

```bash
pip install agentsociety2==2.8.4

git clone https://github.com/tsinghua-fib-lab/agentsociety.git ~/Documents/agentsociety
cd ~/Documents/agentsociety
git checkout agentsociety2-v2.8.4    # 让克隆里的样本档案与装的包同版本
```

三点要注意：

- **包名是 `agentsociety2`**。同一个 GitHub 仓库里有两代产品（见第六节第一行），
  `pip install agentsociety` 装到的是 v1，配置格式、CLI 和落盘结构都不一样。
- **包从 pip 装，clone 只为拿样本档案**。wheel 里只有 `agentsociety2/` 包目录，人群样本
  `profiles.json` 在仓库里（见第四节），所以两步都要做，克隆不用 `pip install -e`。
- **克隆目录必须是 `~/Documents/agentsociety`**。`oss sim doctor` 按这个位置找样本数据，
  换了位置会报样本数据不在。

---

## 三、API 网关的环境变量

跑单模板读 `BASE` 和 `KEY` 两个环境变量（跟 OASIS 一样），跑之前自己 export，
值通常跟项目根 `.env` 里的 `REVIEWER_*` 一样：

```bash
export BASE=https://api.deepseek.com/v1
export KEY=sk-...
python raw/A_1.py     # 跑单模板填好空位另存的那份
```

跑单启动时把它们映射成 AgentSociety 自己的变量名，再传给 CLI 子进程：

| 跑单设的变量 | 从哪来 | 用途 |
|---|---|---|
| `AGENTSOCIETY_LLM_API_BASE` | 你 export 的 `BASE` | 网关地址，必需，缺了模块导入就报错 |
| `AGENTSOCIETY_LLM_API_KEY` | 你 export 的 `KEY` | 网关 key，必需 |
| `AGENTSOCIETY_LLM_MODEL` | 模板里的 `MODEL_TYPE` 常量（手改，不是空位） | 用哪个模型 |
| `AGENTSOCIETY_LLM_RAY_CONCURRENCY` | 模板的 `{{CONCURRENCY}}` 空位，不填缺省 **8** | 并发初值 |

**不要自己预先 export `AGENTSOCIETY_LLM_*` 来代替 `BASE` / `KEY`**：跑单没看到 `BASE` / `KEY`
就不启动，启动后也会用自己的映射值覆盖你设的。

上游默认并发 16，带 AIMD 自适应，压垮网关后会自己降下来。缺省取 8 是为了一开始就不压垮。
要调大，在整理稿里写明数字，并把 `profile.json` 的 `defaults.concurrency` 和模板缺省一起改。

---

## 四、样本数据在哪

clone 时样本档案已经一起下来了：

```
~/Documents/agentsociety/packages/agentsociety2/profiles.json
```

这是一个 100 人的 JSON 数组，每条记录有 `id/age/gender/education/occupation/consumption/home/work`。

另存跑单脚本时，把这份档案复制到脚本同目录，改名为 `profiles_full.json`，跑单模板只认这个文件名：

```bash
cp ~/Documents/agentsociety/packages/agentsociety2/profiles.json \
   <货箱>/raw/profiles_full.json
```

货箱必须自带全部文件，论文方法节引用的是箱子里的这一份。

每个 condition × rep 把模板填好另存成 `raw/<条件>_<第几次>.py`（如 `raw/A_1.py`），跑起来各自建一个
同名的跑目录 `raw/<条件>_<第几次>/`，里面有 `replay/` 分片、`artifacts/` 问卷、`run_summary.json` 和
`meta_sidecar.json`。装箱时 `oss sim pack --simulator agentsociety` 读的就是这些跑目录。

---

## 五、体检：`oss sim doctor --simulator agentsociety`

```bash
oss sim doctor --simulator agentsociety
```

这条命令只出报告，三条过没过退出码都是 0（只有 `--simulator` 写错时非 0）。看每行前面的 `✓` / `✗`。

- **包可导入且版本相符**：查的是运行 `oss` 的那个 Python 环境里有没有 `agentsociety2==2.8.4`。
  `oss` 和 AgentSociety 装在两个环境里时这一条显示 `✗`，AgentSociety 本身照样能用。想让它也过，
  把 `oss` 装进 `agentsociety` 环境：`conda activate agentsociety && pip install -e <OSS 仓库路径>`。
- **样本数据在**：查第四节那条路径。
- **网关变量在**：分三档。`BASE` / `KEY` 都已 export，显示 `✓`，可以直接跑；只找到
  `AGENTSOCIETY_LLM_API_KEY` 加 `AGENTSOCIETY_LLM_API_BASE`、`REVIEWER_BASE_URL` 或某个 `.env`
  （当前目录逐级往上、`~/.config/oss/.env`、OSS 仓库根目录），显示 `✓`，备注会提醒跑之前还要
  export `BASE` / `KEY`；都没有，显示 `✗`。这一条不查值对不对，真跑前自己核一遍。

---

## 六、已知坑

| 坑 | 是什么 | 应对 |
|---|---|---|
| 同一仓库里有两代产品 | pip 包 `agentsociety`（1.x）和 `agentsociety2`（2.x）在同一个 GitHub 仓库里（`packages/agentsociety` 与 `packages/agentsociety2`，两套 tag），配置格式、CLI、落盘结构互不兼容，装错了报错看起来像配置写错 | 只装 `agentsociety2==2.8.4`；克隆 checkout `agentsociety2-v2.8.4`；论文里写明用的是 v2 |
| 没有全局 seed 和温度参数 | 上游不提供随机种子和 temperature 配置，AIMD 并发自适应还会打乱请求顺序，结果本身不可复现 | 靠重复次数（rep）支撑推断；配料单的 `temperature` 填 `provider_default`，复现声明写「不可复现，靠重复次数」 |
| 问卷不推进 step | `questionnaire` 步不消耗模拟步：波号（wave）是 `steps.yaml` 里问卷步的出现序号（从 0 起），同一个 step 可以有多波 | 波次和真实轮次的对应写在配料单的 `interview_plan` 里；`states.csv` 的 `wave` 列不记轮次 |
| ray 依赖重 | `ray>=2.0` 是硬依赖，还连带一串大包，装得慢、占地大 | 按第一节单独建环境，第一次 `pip install` 多等一会 |
| 余额耗尽可能报 403 | 网关额度用完不一定报 402 / Insufficient，也可能是 HTTP 403 PermissionDeniedError「token quota is not enough…」 | 跑单模板把 403 + quota、PermissionDenied、token quota is not enough 都当成余额耗尽；看到这类横幅**立即全停**，重试没用，先核对余额再决定重跑 |
| `target_kind` 词表跟 OASIS 不同 | `events.csv` 的 `target_kind`：`follow` 记 `agent`，`like` / `comment` / `repost` 记 `post`，发帖记空；OASIS 用 `user`。一个箱子只装一个模拟器，两套词表不会混在同一个箱里 | 单箱分析照本表读；跨箱比较两家模拟器的数据，先把词表对齐（如 `user` 对 `agent`）再合并 |

---

## 七、费用参考

还没有实测数字，下面是估算办法：

- **调用量**：模型调用次数约为 `N_AGENTS × ROUNDS × k`。AgentSociety 是 ReAct 智能体，
  每个 agent 每步要调 3 到 10 次模型，k 取 3 到 10；OASIS 每个 agent 每轮约 1 次，
  所以同样规模的成本约为 OASIS 的 3 到 10 倍。拿 [OASIS 安装单](sim-oasis.md) 第七节的实测费用乘 3 到 10，
  就是上下界。
- **第一次跑**：先小规模试水（如 5 人 × 2 轮），确认网关预算，超预算立即中断（跑单脚本开跑时会打印费用提示）。
  跑完拿网关用量页上的实际数字填进下表。
- **币种**：网关的余额可能以美元显示，按人民币做预算时留出汇率余量。

| 规模 | 单场时间 | 总费用 |
|---|---|---|
| （首跑后填） | | |
