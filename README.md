**简体中文** | [English](README.en.md)

# 🌱 星露谷物语攻略生成器

根据你的**玩法风格**和**节奏偏好**，调用大模型为《星露谷物语》(Stardew Valley) 生成一整季的攻略：宏观策略、关键时间节点，以及第 1–28 天的每日目标。

和直接问大模型不同，这个项目让**数字由程序负责，规划由模型负责**：

- 作物售价、生长天数、加工倍率等数值都来自本地数据库。程序先算好收益，再交给模型作为【数据参考】。
- 模型给出的日程会经过代码校验：种植时间来不及成熟、在本季种了非本季作物、第 28 天没有清理农场，都会报警或自动修正。
- 模型输出的 JSON 格式有误时，先在本地修复，尽量不额外调用模型。

> `data/` 中的作物和加工数值来自 [Stardew Valley Wiki](https://stardewvalleywiki.com/Crops)（游戏 1.6 版本），核对日期 2026-11-05。

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

需要 Python 3.10 或更高版本。第一次运行时，ChromaDB 会下载约 80MB 的本地 Embedding 模型（all-MiniLM-L6-v2），之后离线可用。

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
LLM_PROVIDER=anthropic          # anthropic / openai / mock
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-opus-5-5
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1
```

```bash
python main.py -p "种田为主，兼顾社区中心" -c "速通极限" -s summer -o guide.md
```

### 命令行参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| `-p, --playstyle` | 玩法风格，如“钓鱼赚钱”“全收集”“种田为主” | `钓鱼赚钱，兼顾种田` |
| `-c, --pacing` | 节奏偏好，如“休闲慢节奏”“速通极限” | `休闲慢节奏` |
| `-s, --season` | `spring/summer/fall/winter`，也可以写 `春季`、`夏` 等 | `spring` |
| `-y, --year` | 游戏年份 | `1` |
| `--provider` | `anthropic` / `openai` / `mock` | 环境变量 `LLM_PROVIDER`，未设置时为 `anthropic` |
| `--model` | 覆盖默认模型名 | 环境变量 `ANTHROPIC_MODEL` / `OPENAI_MODEL` |
| `-o, --output` | 同时把攻略保存为 Markdown 文件 | 不保存 |

### 在代码中调用

```python
from stardew_planner import generate_strategy

result = generate_strategy("钓鱼赚钱", "休闲慢节奏", season="spring", provider="mock")
print(result.markdown)          # 完整攻略
print(result.schedule)          # 合并后的日程区间
print(result.season_warnings)   # 换季校验结果
print(result.attempts, result.json_parse_method)  # 调用模型次数、JSON 解析方式
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

1. **数据加载**：读取结构化数值（作物、加工规则、季节日历）和非结构化攻略文本。攻略文本按 `## 标题` 切块，以文本的 MD5 为 ID 写入 ChromaDB（`data/.chroma_db`，内容没变就不会重复写入，删掉的片段会同步删除），再用“季节，玩法，节奏”作为查询语义检索最相关的 3 个片段。ChromaDB 不可用时（未安装、模型下载失败等）自动退回关键词检索。
2. **确定性计算**：调用模型前，先算出每种作物整季的收获次数、单格净利和最划算的加工方式。种植日不早于种子最早开售日，实际可用生长天数 = 28 − 种植日 + 1（例如草莓种子第 13 天才开售，可用 16 天）。
3. **Prompt**：系统 Prompt 规定输出顺序（宏观策略 → 关键节点 → 1–28 天日程），并要求数值以【数据参考】为准。
4. **容错解析**：依次尝试 `strict`（标准解析）→ `cleaned`（修复多逗号、末尾逗号）→ `repaired`（交给 [json_repair](https://github.com/mangiucugna/json_repair) 处理漏括号、漏逗号、单引号等）。格式错误在本地修好，不会触发重试；只有内容不完整（比如缺了某一天）才会让模型重新输出。
5. **跨季校验**：
   - 成熟日 = 种植日 + 生长天数，必须 ≤ 28（下一季仍可种植的作物除外）。
   - 在本季种下非本季作物，作物会立即枯萎。
   - 第 28 天的目标必须包含“清理农场/停止种植”。
   - 能识别否定说法（“不再补种”）、括号里的解释和“只买种子不种”，不会把这些误判为种植。
6. **日程合并与渲染**：连续几天目标相同时合并为一个区间（如“第 3-5 天：钓鱼”），包含节日的区间会标出 🎉。

## 项目结构

```
├── main.py                     命令行入口
├── stardew_planner/
│   ├── data_loader.py          数据加载、攻略切块、ChromaDB 入库与语义检索
│   ├── analysis.py             收益计算，生成 Prompt 中的【数据参考】
│   ├── prompts.py              系统 Prompt / 用户 Prompt / 修正 Prompt 模板
│   ├── llm_client.py           Anthropic / OpenAI / Mock 客户端
│   ├── json_utils.py           大模型 JSON 容错解析
│   ├── season_rules.py         跨季规则校验
│   ├── schedule.py             日程合并
│   ├── renderer.py             Markdown 渲染
│   └── generator.py            generate_strategy() 主流程
├── data/
│   ├── crops.json              作物：季节、种子价、售价、生长/再生天数、产量
│   ├── artisan.json            加工规则：倍率、加成、固定价格、耗时
│   ├── calendar.json           每季节日、采集季、换季日
│   └── guides/*.md             攻略文本
├── tests/                      pytest 测试
└── .claude/skills/stardew-data-guard/   Claude Code 项目 skill
```

## 扩展游戏数据

**新增作物**：在 `data/crops.json` 的 `crops` 数组里加一条：

```json
{"name": "向日葵", "name_en": "Sunflower", "seasons": ["summer", "fall"],
 "seed_price": 200, "sell_price": 80, "grow_days": 8, "regrow_days": null,
 "category": "flower", "harvest_yield": 1.0, "earliest_available_day": 1,
 "seed_source": "皮埃尔杂货店"}
```

`category` 决定适用哪些加工规则（`fruit` / `vegetable` / `flower` / `special`），`regrow_days` 为 `null` 表示只能收获一次。`harvest_yield` 是单次收获的期望产量（基础产量 + p / (1 − p)，p 为额外产出几率）。`earliest_available_day` 是当季最早能买到种子的日期，普通作物为 1。

**新增攻略文本**：在 `data/guides/` 下放 `.md` 或 `.txt` 文件。每个 `## 标题` 是一个片段，片段下方可以加一行 `标签: 春季, 钓鱼, 新手`，用于检索时匹配玩法。

## 测试

```bash
python -m pytest
```

目前有 110 个测试，覆盖：种植识别（15 种写法）、跨季校验与边界情况、日程合并、收获次数与收益计算、JSON 容错解析（多逗号、漏括号、单引号等）、种子最早开售日对收益的影响、攻略切块与向量入库（MD5 去重、增量更新）、语义检索与兜底、数据完整性、生成流程的重试与兜底，以及 `sv.py` 的输出与退出码。

## Claude Code skill：`stardew-data-guard`

在本项目中使用 [Claude Code](https://claude.com/claude-code) 时，涉及星露谷数值的请求会自动启用这个 skill（也可以用 `/stardew-data-guard` 手动调用）。它要求 Claude：

- **不凭记忆给数值**：数字只能来自 `data/` 或用户提供的数据。数据库没有的内容（如 NPC 喜好），会请用户提供。
- **跨季日程必须校验**：用脚本检查日程，并附上“⚠️ 换季校验”。
- **先写公式再给结果**：例如 `果酒售价 = 基础售价 × 3 = 120 × 3 = 360g`。

附带的命令行工具也可以单独使用：

```bash
python .claude/skills/stardew-data-guard/scripts/sv.py crop 草莓
python .claude/skills/stardew-data-guard/scripts/sv.py artisan 草莓
python .claude/skills/stardew-data-guard/scripts/sv.py profit 草莓 --plant-day 14
python .claude/skills/stardew-data-guard/scripts/sv.py calendar summer
python .claude/skills/stardew-data-guard/scripts/sv.py check-schedule plan.json --season spring --fix
```

退出码：`0` 成功，`2` 数据库中没有该数据，`3` 校验发现问题。

## 当前局限与计划

- [x] 用游戏 1.6 版本的真实数值替换 mock 数据（来源：Stardew Valley Wiki）
- [ ] 补充 NPC 数据（喜好、生日、好感度）、品质与职业加成、肥料
- [x] 给作物加“最早可种植日”字段（例如草莓种子要到第 13 天复活节才能买到），修正收益排行
- [ ] 补充更多作物（大蒜、蓝爵、小麦、向日葵、红叶卷心菜等），并记录只在第二年起出售的种子
- [ ] 支持跨季、全年连续规划
- [x] 攻略文本检索从关键词匹配升级为向量检索（ChromaDB）
- [ ] 换用多语言 Embedding 模型，提升中文检索效果（默认模型以英文为主）
- [ ] 用真实 API 测试模型输出的稳定性

## 声明

本项目是非官方的玩家自制工具，与 ConcernedApe 及《星露谷物语》官方无关。游戏名称和相关内容的权利归原作者所有。
