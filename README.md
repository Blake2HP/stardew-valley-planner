**简体中文** | [English](README.en.md)

# 🌱 星露谷物语攻略生成器

[![tests](https://github.com/Blake2HP/stardew-valley-planner/actions/workflows/tests.yml/badge.svg)](https://github.com/Blake2HP/stardew-valley-planner/actions/workflows/tests.yml)

根据你的**玩法风格**和**节奏偏好**，调用大模型为《星露谷物语》(Stardew Valley) 生成一整季的攻略：宏观策略、关键时间节点，以及第 1–28 天的每日目标。

和直接问大模型不同，这个项目让**数字由程序负责，规划由模型负责**：

- 作物售价、生长天数、加工倍率等数值都来自本地数据库。程序先算好收益，再交给模型作为【数据参考】。
- 模型给出的日程会经过代码校验：种植时间来不及成熟、在本季种了非本季作物、第 28 天没有清理农场，都会报警或自动修正。
- 模型输出的 JSON 格式有误时，先在本地修复，尽量不额外调用模型。
- 支持跨季连续规划（如 `--season spring,summer,fall`）：上一季结束时的资金和仍在地里的作物由程序算好，注入下一季的 Prompt。

> `data/` 中的作物和加工数值来自 [Stardew Valley Wiki](https://stardewvalleywiki.com/Crops)（游戏 1.6 版本），核对日期 2026-11-05；开局金币 500g 来自 [Gold](https://stardewvalleywiki.com/Gold) 页面，开局赠送的 15 颗防风草种子来自 [Getting Started](https://stardewvalleywiki.com/Getting_Started) 页面（新存档的第 1 年春季计入资金，草原农场除外）。

## 示例输出

```bash
python main.py -p "钓鱼赚钱，兼顾种田" -c "休闲慢节奏" -s spring --provider mock
```

```markdown
# 🌸 星露谷物语 · 第 1 年春季攻略

## 🧭 宏观策略指导
**核心思路**：前半季靠钓鱼和防风草滚雪球，复活节后转向草莓与花椰菜……

## 📌 关键时间节点
| 日期 | 事件 | 准备事项 | 最晚准备日 |
| 第 13 天 | **复活节** | 预留 2000g 以上购买草莓种子 | 第 12 天 |

## ⚠️ 换季校验
> ⚠️ 第 28 天是换季前一天：防风草、土豆、花椰菜……将在进入夏季时枯死

## 📅 每日核心目标
- **第 1 天**：种下防风草并清理农场
- **第 3-4 天**：海滩钓鱼攒钱
- **第 13 天**：复活节购买草莓种子　🎉 *复活节(13日)*
- **第 15-18 天**：采集鲑莓，钓鱼　🎉 *鲑莓采集季开始(15日)*
```

完整示例见 [sample_output.md](sample_output.md)。

## 快速开始

需要 Python 3.10 或更高版本。依赖里包含 sentence-transformers 和 PyTorch（CPU 版），安装需要几分钟。第一次运行时会从 Hugging Face 下载约 460MB 的多语言 Embedding 模型（paraphrase-multilingual-MiniLM-L12-v2），之后离线可用。

```bash
git clone https://github.com/Blake2HP/stardew-valley-planner.git
cd stardew-valley-planner
pip install -r requirements.txt
```

**离线试用**（mock 模式，不需要 API Key）：

```bash
python main.py --provider mock
```

**调用真实大模型**：把 `.env.example` 复制为 `.env`，填入 Key：

```ini
LLM_PROVIDER=anthropic          # anthropic / mock
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-opus-5-5
```

Claude 的调用参数（都可以在 `.env` 里改）：

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `ANTHROPIC_EFFORT` | `medium` | 思考力度（`low` / `medium` / `high` / `xhigh` / `max`）。Claude Opus 5.5 的思考无法关闭，只能调力度 |
| `ANTHROPIC_MAX_TOKENS` | `16000` | 输出上限，思考 token 也算在内；被截断时会让模型重写 |
| `ANTHROPIC_TIMEOUT` | `300` | 单次请求超时（秒） |
| `ANTHROPIC_MAX_RETRIES` | `1` | SDK 对网络错误、429、5xx 的自动重试次数 |
| `ANTHROPIC_FALLBACKS` | `on` | 模型出于安全原因拒答时，由 API 在同一次调用里自动换模型重新生成（`fallbacks: "default"`），这次结果按换上的模型计费；`off` 关闭。最终仍被拒绝时给出明确提示，不会当成 JSON 错误重试 |

```bash
python main.py -p "种田为主，兼顾社区中心" -c "速通极限" -s summer -o guide.md
```

### 命令行参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| `-p, --playstyle` | 玩法风格，如“钓鱼赚钱”“全收集”“种田为主” | `钓鱼赚钱，兼顾种田` |
| `-c, --pacing` | 节奏偏好，如“休闲慢节奏”“速通极限” | `休闲慢节奏` |
| `-s, --season` | `spring/summer/fall/winter`，也可以写 `春季`、`夏` 等；用逗号分隔多个**连续**季节做跨季规划，如 `spring,summer`（冬季之后进入下一年春季） | `spring` |
| `-y, --year` | 游戏年份；第 1 年的收益表和校验不计入第 2 年起才出售种子的作物（大蒜、红叶卷心菜、未碾米、洋蓟） | `1` |
| `--provider` | `anthropic` / `mock` | 环境变量 `LLM_PROVIDER`，未设置时为 `anthropic` |
| `--model` | 覆盖默认模型名 | 环境变量 `ANTHROPIC_MODEL` |
| `-o, --output` | 同时把攻略保存为 Markdown 文件 | 不保存 |
| `--kegs` / `--jars` | 小桶、罐头瓶的数量。给出时按机器数量和加工时间模拟加工：低星、多赚多的先加工，机器排满后换另一种机器，加工净增计入资金；第 28 天还没做完的带到下一季 | `0`（不模拟） |
| `--bee-houses` | 室外蜂房数量。每 4 天一罐，按取蜜当天室外开着的最值钱的花算花蜜（没有花是野蜂蜜 100g），计入资金；冬季不产，温室里的花不算 | `0`（不模拟） |
| `--greenhouse` | 温室已修好。可以在种植清单里写 `"place": "greenhouse"`：温室不受季节限制，可再生作物换季不会枯死，最多 120 格（超出、没修好时使用都会报问题） | 不开启 |
| `--farm-type` | 农场地图：`standard` / `riverland` / `forest` / `hilltop` / `wilderness` / `four_corners` / `beach` / `meadowlands`（也可以写中文名）。草原农场开局没有赠送的 15 颗防风草种子 | `standard` |
| `--revise-rounds` | 自动修正轮数：程序校验发现问题（来不及成熟、送错礼物、现金不足、日程和种植清单对不上等）时，把问题清单反馈给模型重写，问题变少才采用新版本；会多调用模型 | `1`（`0` = 关闭） |
| `--start-gold` | 期初资金 | 第 1 年春季为开局金币 500g（`data/farm.json`），其他季节为未知 |
| `--professions` | 职业，逗号分隔：`tiller`（农耕人，作物 +10%）、`artisan`（工匠，加工品 +40%）、`agriculturist`（农业学家，生长 +10%）。也可以写中文名；选工匠或农业学家会自动加入农耕人，两者只能二选一 | 无 |
| `--fertilizer` | 肥料（每格只能施一种）。加速：`speed_gro`（生长激素 10%）、`deluxe_speed_gro`（高级 25%）、`hyper_speed_gro`（顶级 33%）；品质：`basic_fertilizer`（普通肥料）、`quality_fertilizer`（高级肥料）、`deluxe_fertilizer`（顶级肥料，唯一能出铱星） | 无 |
| `--farming-level` | 耕种等级 0-14；给出时（或用了品质肥料时）按品质概率计算收益期望值，没给就取所选职业要求的最低等级 | 不计品质 |
| `--processing-share` | 机器能加工的收获比例 0-1；不足 1 时自动算出“高星直接卖、低星加工”的分界 | `1`（机器充足） |

### 在代码中调用

```python
from stardew_planner import generate_strategy

result = generate_strategy("钓鱼赚钱", "休闲慢节奏", season="spring", provider="mock",
                           professions=["artisan"], fertilizer="speed_gro")
print(result.markdown)          # 完整攻略
print(result.schedule)          # 合并后的日程区间
print(result.season_warnings)   # 换季校验结果
print(result.attempts, result.json_parse_method)  # 调用模型次数、JSON 解析方式
print(result.bonuses.describe())  # 职业：农耕人、工匠；肥料：生长激素
print(result.finance.end_gold)    # 期末资金（只算种植收支，程序计算）
result = generate_strategy("种田为主", "速通极限", "spring", provider="mock",
                           fertilizer="deluxe_fertilizer", farming_level=10, processing_share=0.5)
```

跨季连续规划：

```python
from stardew_planner import generate_plan

plan = generate_plan("种田为主", "速通极限", "spring,summer,fall", provider="mock")
print(plan.markdown)                   # 总览表 + 各季攻略
for state in plan.states:              # 每季结束时的农场状态
    print(state.next_season, state.gold, state.gold_estimated, state.carried_names)
```

## 工作流程

```
data/*.json ──► analysis.py ──┐
  作物/加工/日历     计算收益排行    │
                             ├──► prompts.py ──► 大模型 ──► json_utils.py ──► 结构校验
data/guides/*.md ──► 向量检索   ─┘   系统 + 用户 Prompt          容错解析        （不合格时重试 1 次）
                                                                                  │
            Markdown 攻略 ◄── renderer.py ◄── schedule.py ◄── season_rules.py ◄──┘
                                              合并连续日程      跨季校验与修正
```

1. **数据加载**：读取结构化数值（作物、加工规则、季节日历）和非结构化攻略文本。攻略文本按 `## 标题` 切块，以文本的 MD5 为 ID 写入 ChromaDB（`data/.chroma_db`，内容没变就不会重复写入，删掉的片段会同步删除），再用“季节，玩法，节奏”作为查询语义检索最相关的 3 个片段。向量库记录了建库所用的 Embedding 模型，换模型后会自动删除旧集合并重建。ChromaDB 不可用时（未安装、模型下载失败等）自动退回关键词检索。
2. **确定性计算**：调用模型前，先算出每种作物整季的收获次数、单格净利和最划算的加工方式。种植日不早于种子最早开售日，实际可用生长天数 = 28 − 种植日 + 1（例如草莓种子第 13 天才开售，可用 16 天）。可跨季的作物按完整存活期计算（夏季种的玉米算到秋季末），收益表按平均每季净利排序，并始终列出跨季作物。
3. **Prompt**：系统 Prompt 规定输出顺序（宏观策略 → 关键节点 → 1–28 天日程），并要求数值以【数据参考】为准。
4. **容错解析**：依次尝试 `strict`（标准解析）→ `cleaned`（修复多逗号、末尾逗号）→ `repaired`（交给 [json_repair](https://github.com/mangiucugna/json_repair) 处理漏括号、漏逗号、单引号等）。格式错误在本地修好，不会触发重试；内容不完整（比如缺了某一天、没有种植清单）或输出达到 `max_tokens` 被截断时，才会让模型重新输出。重试后仍不合格时用兜底结果，并在攻略里明确警告缺了哪些内容、资金预期不可靠。模型拒答时给出明确提示，不当成格式错误重试。
5. **跨季校验**：
   - 成熟日 = 种植日 + 生长天数，必须 ≤ 28（下一季仍可种植的作物除外）。
   - 在本季种下非本季作物，作物会立即枯萎。
   - 第 28 天的目标必须包含“清理农场/停止种植”。
   - 能识别否定说法（“不再补种”）、括号里的解释和“只买种子不种”，不会把这些误判为种植。
   - **自动修正**：校验发现的问题（不含换季提醒等固定提示）会作为清单反馈给模型，让它重写一次；修正版的问题更少才采用，否则保留原版本。攻略里会注明修正前后的问题条数，评估脚本也会统计修正前后的变化（`--revise-rounds 0` 关闭）。
6. **日程合并与渲染**：连续几天目标相同时合并为一个区间（如“第 3-5 天：钓鱼”），包含节日的区间会标出 🎉。
7. **作物品质与出售策略**：给出耕种等级或使用品质肥料时，按 wiki 公式计算每次收获的品质分布（金星几率 g = 0.2 × 等级 / 10 + 0.2 × 肥料等级 × (等级 + 2) / 12 + 0.01，银星 min(0.75, 2g)，铱星 g / 2 且只有顶级肥料才有），售价 = ⌊基础 × 品质倍率⌋ 再计农耕人；额外产出一律普通品质。加工品不继承品质，所以“加工多赚的钱”在普通品质最大、铱星最小：机器充足时全部加工；机器不够时（`--processing-share`）按这个差额从大到小分配，得到“高星直接卖、低星加工”。
8. **种植清单与资金**：模型必须在 `plantings` 里列出每次播种的作物、日期和格数（漏写会重试）。程序据此模拟每次收获（可跨季，例如夏季种的玉米会一直收获到秋季末），算出种子、肥料支出和作物收入，并逐日检查现金是否够买种子。模型还会在 `other_income` 里估算钓鱼、挖矿等收入，攻略中与程序计算的部分分开显示，标为“模型估算，仅供参考”。
9. **跨季连续规划**：逐季生成。每季结束后提取农场状态（期末资金、仍在地里的跨季作物及其在下一季的收获日、清空的作物、手上剩余的种子、上季计划目标），写进下一季用户 Prompt【数据参考】的“上季延续状态”一节；系统 Prompt 保持不变，只多一条衔接规则。下一季再种仍在地里的作物会报警，第 28 天的清理目标会注明保留跨季作物（如“清理农场/停止种植（保留玉米）”）。
10. **生日送礼**：`data/npcs.json` 记录了全部 34 位可送礼村民的生日和最爱 / 喜欢 / 不喜欢 / 讨厌物品，以及通用喜欢、中立、不喜欢、讨厌列表。生成某季攻略时，当季过生日的 NPC 及其喜好会注入【数据参考】，系统 Prompt 要求在生日当天写“送{NPC}{礼物}”。程序随后核对每件礼物：先查 NPC 页面逐项列出的物品，再按 wiki 的类别说明判断作物和加工品（如海莉不喜欢所有蔬菜、塞巴斯蒂安讨厌工匠物品），最后查通用列表（虞美人是通用讨厌，潘妮例外）；不喜欢、讨厌、中立的礼物都会报出来。12 位可以结婚的村民生日当天必须有送礼任务，其他村民可选。生日送最爱 = 80 × 8 = 640 好感度。

## 项目结构

```
├── main.py                     命令行入口
├── scripts/eval_llm.py         真实 API 稳定性评估
├── web/                        网页版 Demo（FastAPI + 单页面）
├── stardew_planner/
│   ├── data_loader.py          数据加载、攻略切块、ChromaDB 入库与语义检索
│   ├── analysis.py             收益计算，生成 Prompt 中的【数据参考】
│   ├── prompts.py              系统 Prompt / 用户 Prompt / 修正 Prompt 模板
│   ├── llm_client.py           Anthropic（Claude）/ Mock 客户端
│   ├── json_utils.py           大模型 JSON 容错解析
│   ├── season_rules.py         跨季规则校验
│   ├── schedule.py             日程合并
│   ├── renderer.py             Markdown 渲染
│   ├── bonuses.py              职业与加速肥料加成
│   ├── processing.py           加工模拟（机器数量、加工时间、跨季加工品）
│   ├── farm_state.py           跨季收获模拟、资金计算、季末农场状态
│   └── generator.py            generate_strategy() / generate_plan() 主流程
├── data/
│   ├── crops.json              作物：季节、种子价、售价、生长/再生天数、产量
│   ├── artisan.json            加工规则：倍率、加成、固定价格、耗时
│   ├── calendar.json           每季节日、采集季、换季日
│   ├── bonuses.json            职业、肥料与作物品质规则
│   ├── farm.json               开局金币
│   ├── npcs.json               NPC 生日与送礼喜好
│   ├── NOTICE.md               数据来源与许可
│   └── guides/*.md             攻略文本
├── tests/                      pytest 测试
└── .claude/skills/stardew-data-guard/   Claude Code 项目 skill
```

## 扩展游戏数据

**新增作物**：在 `data/crops.json` 的 `crops` 数组里加一条：

```json
{"name": "大蒜", "name_en": "Garlic", "seasons": ["spring"],
 "seed_price": 40, "sell_price": 60, "grow_days": 4, "regrow_days": null,
 "category": "vegetable", "harvest_yield": 1.0, "earliest_available_day": 1,
 "available_year": 2, "seed_source": "皮埃尔杂货店（第 2 年起）",
 "speed_grow_days": {"10": 3, "20": 3, "25": 3, "33": 2, "35": 2, "43": 2}}
```

`category` 决定适用哪些加工规则（`fruit` / `vegetable` / `flower` / `special`；个别作物在 `artisan.json` 里有专属规则 `input_crops`，例如小麦放进小桶得到啤酒而不是果汁），`regrow_days` 为 `null` 表示只能收获一次。`harvest_yield` 是单次收获的期望产量（基础产量 + p / (1 − p)，p 为额外产出几率）。`earliest_available_day` 是当季最早能买到种子的日期，普通作物为 1。`available_year` 是种子开始出售的年份（默认 1）：传入 `year=1` 时，收益排行和 Prompt 会过滤掉 `available_year > 1` 的作物，日程里种了这类作物也会给出警告。可选字段 `aliases` 是同一作物的其他叫法（如官方名“西红柿”“虞美人花”），各处识别作物名时都会认；`seed_drop` 是每次收获平均掉落的种子数（向日葵 = 1），补种时先用掉落的种子；`byproducts` 是收获附带的副产物（如小麦 40% 几率掉干草，干草不能出售，不计入收入）。`speed_grow_days` 是加速后的生长天数，键为总加速百分比（肥料 10 / 25 / 33，加上农业学家 10），数据取自各作物 wiki 页面的日历：游戏按阶段扣减天数且有浮点误差，不能按百分比直接换算（例如山药 10 天在 20% 加速下是 7 天，不是 8 天）。

**新增攻略文本**：在 `data/guides/` 下放 `.md` 或 `.txt` 文件。每个 `## 标题` 是一个片段，片段下方可以加一行 `标签: 春季, 钓鱼, 新手`，用于检索时匹配玩法。

## 测试

```bash
python -m pytest
```

测试覆盖：种植识别（15 种写法）、跨季校验与边界情况、日程合并、收获次数与收益计算、JSON 容错解析（多逗号、漏括号、单引号等）、种子最早开售日对收益的影响、按年份过滤作物（第 2 年起出售的种子）、日历日期、收获掉落种子与副产物、NPC 生日注入与送礼校验（礼物识别、喜好与例外、生日当天送礼）、跨季连续规划（跨季收获模拟、资金与现金流、季末状态注入下一季 Prompt、季节连续性）、职业与加速肥料（价格取整、查表生长天数、职业冲突、肥料成本）、作物品质与品质肥料（概率对照 wiki 表格、四档售价、顶级肥料收益期望值、按机器容量分配的出售策略）、攻略切块与向量入库（MD5 去重、增量更新、换模型后重建）、语义检索与兜底、数据完整性、生成流程的重试与兜底、网页版接口（参数校验、限流、HTML 转义），以及 `sv.py` 的输出与退出码。

## 网页版 Demo

`web/` 是一个最简单的网页版：左边选玩法、节奏、季节（可以连续规划多季）、职业、肥料和耕种等级，右边显示期末资金卡片和完整攻略，可复制或下载 Markdown。后端是 FastAPI，直接调用 `generate_strategy` / `generate_plan`，没有改业务代码。

```bash
pip install -r requirements.txt
python -m web                           # 打开 http://127.0.0.1:8000，默认用 mock，不消耗额度
```

用真实模型时设置环境变量 `DEMO_PROVIDER=anthropic`（同时需要 `ANTHROPIC_API_KEY`）。为了控制费用，真实模型默认每个 IP 每小时最多生成 10 次（`DEMO_RATE_LIMIT`），同时最多 2 个生成（`DEMO_MAX_CONCURRENT`），一次最多 4 个季节（`DEMO_MAX_SEASONS`）；单次模型调用超时 300 秒（`DEMO_REQUEST_TIMEOUT`），网页里不再让 SDK 自动重试。部署在反向代理后面时，用 `python -m web --forwarded-allow-ips <代理 IP>`（或环境变量 `DEMO_FORWARDED_ALLOW_IPS`）声明代理，限流才会按用户的真实 IP 计算；直接暴露在公网时不要设成 `*`，否则任何人都能伪造 IP 绕过限流。模型输出里的 HTML 会被转义，不会在页面上执行。

## 模型稳定性评估

`scripts/eval_llm.py` 用真实 API 跑一组预设用例（春季极限赚钱、春季休闲钓鱼、夏季种玉米跨秋、秋季酿酒、冬季挖矿全收集、第 2 年春季社交送礼、秋季顶级肥料品质农业），统计成功率、一次生成成功率、`attempts`、JSON 解析方式、`season_warnings` 与送礼警告的触发频率和内容、`finance.end_gold`，结束后打印 Markdown 统计表。

```bash
python scripts/eval_llm.py --list                          # 查看用例
python scripts/eval_llm.py --runs 3 --json eval.json       # 每个用例跑 3 次，并保存原始结果
python scripts/eval_llm.py --only spring_rush fall_wine    # 只跑部分用例
python scripts/eval_llm.py --provider mock                 # 离线检查脚本本身，不消耗额度
```

真实 provider 每次运行至少调用一次模型，会产生 API 费用。用 `--cases my_cases.json` 可以换成自定义用例（字段同脚本里的 `DEFAULT_CASES`）。有运行失败时脚本退出码为 1。

## Claude Code skill：`stardew-data-guard`

在本项目中使用 [Claude Code](https://claude.com/claude-code) 时，涉及星露谷数值的请求会自动启用这个 skill（也可以用 `/stardew-data-guard` 手动调用）。它要求 Claude：

- **不凭记忆给数值**：数字只能来自 `data/` 或用户提供的数据。数据库没有的内容（如未收录的 NPC、概率掉落），会请用户提供。
- **跨季日程必须校验**：用脚本检查日程，并附上“⚠️ 换季校验”。
- **先写公式再给结果**：例如 `果酒售价 = 基础售价 × 3 = 120 × 3 = 360g`。

附带的命令行工具也可以单独使用：

```bash
python .claude/skills/stardew-data-guard/scripts/sv.py crop 草莓
python .claude/skills/stardew-data-guard/scripts/sv.py artisan 草莓
python .claude/skills/stardew-data-guard/scripts/sv.py profit 草莓 --plant-day 14
python .claude/skills/stardew-data-guard/scripts/sv.py simulate 玉米 --season summer --tiles 8
python .claude/skills/stardew-data-guard/scripts/sv.py profit 土豆 --professions agriculturist --fertilizer speed_gro
python .claude/skills/stardew-data-guard/scripts/sv.py quality 花椰菜 --fertilizer deluxe_fertilizer --farming-level 10 --processing-share 0.5
python .claude/skills/stardew-data-guard/scripts/sv.py npc 海莉                    # NPC 生日与喜好
python .claude/skills/stardew-data-guard/scripts/sv.py check-schedule plan.json --season spring --gifts  # 同时核对送礼
python .claude/skills/stardew-data-guard/scripts/sv.py calendar summer
python .claude/skills/stardew-data-guard/scripts/sv.py check-schedule plan.json --season spring --fix
```

退出码：`0` 成功，`2` 数据库中没有该数据，`3` 校验发现问题。

## 当前局限与计划

- [x] 用游戏 1.6 版本的真实数值替换 mock 数据（来源：Stardew Valley Wiki）
- [x] 职业加成（农耕人、工匠、农业学家）与加速肥料（生长激素、高级、顶级）
- [x] 作物品质（银星 / 金星 / 铱星）与品质肥料（普通 / 高级 / 顶级），以及“高星直接卖、低星加工”的出售策略
- [x] NPC 生日送礼（6 位 NPC 的生日与最爱 / 喜欢，Prompt 注入与送礼校验）
- [x] 补充其余 NPC（共 34 位），以及通用喜欢 / 中立 / 不喜欢 / 讨厌列表；送礼校验区分四档
- [x] 加工模拟计入机器数量与加工时间（`--kegs` / `--jars`）
- [x] 给作物加“最早可种植日”字段（例如草莓种子要到第 13 天复活节才能买到），修正收益排行
- [x] 补充大蒜、红叶卷心菜、向日葵，并用 `available_year` 记录只在第二年起出售的种子
- [x] 补充蓝爵、小麦、虞美人（小麦酿啤酒用作物专属加工规则）
- [x] 补充小白菜、洋蓟、玫瑰仙子、夏季亮片、未碾米
- [x] 加工规则支持“多个原料做一件”（5 颗咖啡豆 → 1 杯咖啡），补充咖啡豆（种子只能从旅行货车买到，按 2500g 计）
- [x] 花的蜂蜜规则（售价 = 100 + 花的基础售价 × 2，写进作物备注和 `sv.py artisan`）
- [x] 蜂房模拟（`--bee-houses`：每 4 天一罐，按开着的花算花蜜，计入资金）
- [x] 支持跨季连续规划（资金与跨季作物衔接）
- [x] 跨季的加工品（季末还在机器里的货品下季完成时入账）
- [x] 温室规划（`--greenhouse`：不受季节限制、120 格上限、作物跨季延续）
- [x] 攻略文本检索从关键词匹配升级为向量检索（ChromaDB）
- [x] 换用多语言 Embedding 模型（paraphrase-multilingual-MiniLM-L12-v2），提升中文检索效果
- [ ] 用真实 API 测试模型输出的稳定性

## 数据来源与许可

| 内容 | 来源 | 许可 |
|---|---|---|
| 代码（`stardew_planner/`、`web/`、`scripts/`、`tests/`、`main.py` 等） | 本项目编写 | [MIT](LICENSE) |
| `data/crops.json`、`artisan.json`、`bonuses.json`、`farm.json`、`npcs.json` 中的数值 | 整理自 [Stardew Valley Wiki](https://stardewvalleywiki.com)（1.6 版本，核对日期 2026-11-05） | [CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/)，沿用原协议 |
| `data/calendar.json`、`data/guides/` | 本项目自行编写（节日日期为游戏内固定日程） | [MIT](LICENSE) |

Stardew Valley Wiki 的内容按 [CC BY-NC-SA 3.0](https://stardewvalleywiki.com/Stardew_Valley_Wiki:Copyrights) 授权：使用时须署名，**不得用于商业用途**，改编后须以相同协议发布。因此，来自 wiki 的数据文件同样按 CC BY-NC-SA 3.0 提供。代码按 MIT 许可，可以自由使用（包括商用）；但如果连同上述数据一起用于商业用途，需要先取得 wiki 版权方的许可，或者换成自行核实的数据。详见 [data/NOTICE.md](data/NOTICE.md)。

## 声明

本项目是非官方的玩家自制工具，与 ConcernedApe 及《星露谷物语》官方无关。游戏名称和相关内容的权利归原作者所有。
