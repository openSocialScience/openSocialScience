# 学科档案

**同一个 idea 在经济学是 top-5，在社会学可能不可发表。**

| 学科 | 额外启用的透镜 | 识别门 | 发表门槛 |
|---|---|---|---|
| `economics` | `new-data-window` | **硬**——纯描述基本判死；期望 RCT / IV / RD / DID | 对经济上有意义的结果给出可信因果 |
| `sociology` | `context-transfer` `temporal-shift` | **软**——现象新或模式理论上出人意料时，描述性可发 | 理论对话是门槛，机制 > 量级 |
| `political-science` | `context-transfer` `new-data-window` | 混合——美国政治偏硬，比较政治偏软 | 对某个具名理论辩论的贡献 |
| `psychology` | `untested-assumption` | **实验**——期望随机分配 | 构念效度 + 可复制性；预注册与功效近乎强制 |
| `communication` | `new-data-window` `temporal-shift` | 混合——数字痕迹设计常为准实验 | 测量效度是最常见的审稿攻击点 |

> **不要拿经济学的识别标准去要求社会学的 idea。那是学科错误，不是严谨。**

## 学科 × 研究类型的门槛

上表的「识别门」「发表门槛」是因果类的。按研究类型细分（2026-09-13 起；依据设计稿 docs/design/2026-09-13-research-types.md 附录 B）：

| 学科 \ 类型 | 因果 | 描述 | 测量 | 仿真 |
|---|---|---|---|---|
| economics | **硬**：期望 RCT / IV / RD / DID；观察数据加控制变量的因果主张要附敏感性分析 | **放行但门槛高**：新数据、新事实或新测量；按抽样设计报精度（Deaton） | 新指标要与已有指标或官方统计对账（Firebaugh 现实核查） | 校准到真实数据的矩，报参数敏感性 |
| sociology | **软**：观察数据可下因果主张，但要写明估计量与识别假设（Lundberg）；机制 > 量级 | **放行**：现象新，或模式理论上出人意料（Merton「先确立现象」） | 构念与理论对应；跨群体比较先验测量等价（Davidov） | ABM 按 ODD 写，面向模式验证（Grimm） |
| political-science | **混合**：美国政治偏硬、比较政治偏软；IV 报有效 F、OLS 与 2SLS 并排（Lal） | **放行**：对具名理论辩论有贡献的描述（Gerring） | 按类给效度证据（Adcock & Collier）；跨国比较测量等价 | LLM 合成被试要与真人数据对照（Argyle、Bisbee） |
| psychology | **实验**：期望随机分配；观察数据的因果主张门槛最高 | **放行**：事前写明描述目标（Scheel 2021「少做假设检验」） | **核心门槛**：构念效度，量表要给效度证据不只信度（Flake） | 合成被试不能替代真人被试，只作探索 |
| communication | **混合**：数字痕迹常为准实验；互惠动态要处理（Thomas） | **放行**：数字媒体的定量描述（Munger） | 测量效度是最常见攻击点；自报媒介使用按高误差代理（Parry） | 传播扩散的 ABM（Waldherr），按 ODD 写 |

出题、定审稿侧重时按这张表（陪审团只拿到 `ideas.json` 与固定提示词，看不到这张表）。功效灰区（`oss power --discipline`）按学科分；描述类不算功效、按精度判——区间半宽跟有意义的最小差别比，精度不足不停，门槛不分学科，见环节 4 的说明书 `social-research/stages/4-feasibility.md`「描述类题目按精度判」。
