**简体中文** | [English](README.en.md)

# 🌱 星露谷物语攻略生成器

[![tests](https://github.com/Blake2HP/stardew-valley-planner/actions/workflows/tests.yml/badge.svg)](https://github.com/Blake2HP/stardew-valley-planner/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Code License: MIT](https://img.shields.io/badge/code%20license-MIT-green)](LICENSE)
[![Data License: CC BY-NC-SA 3.0](https://img.shields.io/badge/data%20license-CC%20BY--NC--SA%203.0-lightgrey)](data/NOTICE.md)
[![Stardew Valley 1.6](https://img.shields.io/badge/Stardew%20Valley-1.6-6BA539)](https://stardewvalleywiki.com/Version_History)
[![LLM: Claude](https://img.shields.io/badge/LLM-Claude%20Opus%205.5-D97757)](https://www.anthropic.com/claude)

根据你的**玩法风格**和**节奏偏好**，调用大模型为《星露谷物语》(Stardew Valley) 生成一整季的攻略：宏观策略、关键时间节点，以及第 1–28 天的每日目标。

和直接问大模型不同，这个项目让**数字由程序负责，规划由模型负责**：

- 作物售价、生长天数、加工倍率等数值都来自本地数据库。程序先算好收益，再交给模型作为【数据参考】。
- 模型给出的日程会经过代码校验：种植时间来不及成熟、在本季种了非本季作物、第 28 天没有清理农场，都会报警或自动修正。
- 模型输出的 JSON 格式有误时，先在本地修复，尽量不额外调用模型。
- 支持跨季连续规划（如 `--season spring,summer,fall`）：上一季结束时的资金和仍在地里的作物由程序算好，注入下一季的 Prompt。

> `data/` 中的作物和加工数值来自 [Stardew Valley Wiki](https://stardewvalleywiki.com/Crops)（游戏 1.6 版本），核对日期 2026-11-05；开局金币 500g 来自 [Gold](https://stardewvalleywiki.com/Gold) 页面，开局赠送的 15 颗防风草种子来自 [Getting Started](https://stardewvalleywiki.com/Getting_Started) 页面（新存档的第 1 年春季计入资金，草原农场除外）。

## 示例输出

<!-- 请在此处补充一张 CLI 运行截图或 Web 界面动图 (GIF) -->

> 🖼️ **截图位**：这里适合放一张终端运行 `python main.py --provider mock` 的截图，画面里最好能同时看到“⚠️ 换季校验”区块和“📅 每日核心目标”，让读者一眼看出“程序算数、模型规划”的效果。

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

## 目录

- [示例输出](#示例输出)
- [快速开始](#快速开始)
  - [命令行参数](#命令行参数)
  - [在代码中调用](#在代码中调用)
- [工作流程](#工作流程)
- [项目结构](#项目结构)
- [扩展游戏数据](#扩展游戏数据)
- [测试](#测试)
- [网页版 Demo](#网页版-demo)
- [模型稳定性评估](#模型稳定性评估)
- [Claude Code skill：`stardew-data-guard`](#claude-code-skillstardew-data-guard)
- [当前局限与未来路线图 (Roadmap)](#当前局限与未来路线图-roadmap)
- [数据来源与许可](#数据来源与许可)
- [声明](#声明)

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
| `ANTHROPIC_CACHE` | `5m` | Prompt 缓存：系统 Prompt 单独缓存（跨季规划的每一季共用），再加顶层自动缓存覆盖对话末尾，格式重试和自动修正时整份【数据参考】从缓存读。`1h` 改用 1 小时缓存（写入价格翻倍，只在两次请求常常隔 5 分钟以上时划算），`off` 关闭 |
| `ANTHROPIC_FALLBACKS` | `on` | 模型出于安全原因拒答时，由 API 在同一次调用里自动换模型重新生成（`fallbacks: "default"`），这次结果按换上的模型计费；`off` 关闭。最终仍被拒绝时给出明确提示，不会当成 JSON 错误重试 |

```bash
python main.py -p "种田为主，兼顾社区中心" -c "速通极限" -s summer -o guide.md
```

### 命令行参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| `--save` | 导入《星露谷物语》1.6 主存档（存档文件夹里和文件夹同名的文件，如 `Name_123456789`，不是 `SaveGameInfo`）：自动填入季节、当前日期、年份、现金、农场地图、耕种等级、职业、温室、路线、小桶 / 罐头瓶 / 蜂房数量、已有动物、地里已经种着的作物和成就进度（已出货的作物、每位村民的好感度、累计赚取的金币），并在终端打印“存档解析摘要”。命令行另外写了的参数以命令行为准；换了季节或年份、又没写 `--start-day` 时从第 1 天开始。`--save auto` 自动选最近玩的存档；只写 `--save` 时列出本机存档、输入编号选择 | 不导入 |
| `--list-saves` | 列出本机找到的存档（编号、农场名、第几年哪季第几天、金币、路径，最近玩的在前），然后退出 | - |
| `--saves-dir` | 存档根目录（可写多次），用于 `--list-saves` 和 `--save auto` | 按系统找默认位置：Windows `%APPDATA%\StardewValley\Saves`；macOS / Linux `~/.config/StardewValley/Saves`；Linux 另找 Flatpak 版 Steam 的 `~/.var/app/com.valvesoftware.Steam/.config/StardewValley/Saves` |
| `-p, --playstyle` | 玩法风格，如“钓鱼赚钱”“全收集”“种田为主” | `钓鱼赚钱，兼顾种田` |
| `--list-playstyles` | 列出预设的玩法风格和节奏偏好，以及各自的优劣势和适合人群（网页上点选预设时也会显示），然后退出 | - |
| `-c, --pacing` | 节奏偏好，如“休闲慢节奏”“速通极限” | `休闲慢节奏` |
| `-s, --season` | `spring/summer/fall/winter`，也可以写 `春季`、`夏` 等；用逗号分隔多个**连续**季节做跨季规划，如 `spring,summer`（冬季之后进入下一年春季） | `spring` |
| `-y, --year` | 游戏年份；第 1 年的收益表和校验不计入第 2 年起才出售种子的作物（大蒜、红叶卷心菜、未碾米、洋蓟） | `1` |
| `--provider` | `anthropic` / `mock` | 环境变量 `LLM_PROVIDER`，未设置时为 `anthropic` |
| `--model` | 覆盖默认模型名 | 环境变量 `ANTHROPIC_MODEL` |
| `-o, --output` | 同时把攻略保存为 Markdown 文件 | 不保存 |
| `--kegs` / `--jars` | 小桶、罐头瓶的数量。给出时按机器数量和加工时间模拟加工：低星、多赚多的先加工，机器排满后换另一种机器，加工净增计入资金；第 28 天还没做完的带到下一季 | `0`（不模拟） |
| `--bee-houses` | 室外蜂房数量。每 4 天一罐，按取蜜当天室外开着的最值钱的花算花蜜（没有花是野蜂蜜 100g），计入资金；冬季不产，温室里的花不算 | `0`（不模拟） |
| `--players` | 联机人数 1-4（P1 为房主）。多人时日程按玩家分开写、按树状列表渲染；体力安全线按人数放大；工具按玩家校验，每个人升级都单独付钱。共享钱包，开局资金和种子只算房主的一份 | `1` |
| `--achievements` | 想完成的 Steam 成就，逗号分隔，如 `"混合栽培,万人迷"`（也认“多作种植”）；全部 49 个成就都能选，用 `--list-achievements` 查看。赚钱类（新手……千万富翁）、友谊类（新朋友……万人迷）、混合栽培、单一栽培由程序算本季缺口，出货和友谊类还会校验种植和送礼；钓鱼、烹饪、博物馆等其他成就程序没有数据，只写进 Prompt 作为长期目标 | 不指定 |
| `--list-achievements` | 按类别列出全部成就，注明哪些由程序算缺口，然后退出 | - |
| `--achievement-progress` | 已有的成就进度，如 `"防风草=15,万人迷=12,千万富翁=50000"`：作物 = 已运送个数，友谊类成就名 = 已经达到该成就心数的人数（如 `万人迷=12`、`最好的朋友=1`），赚钱类成就名 = 已累计赚取的金币 | 从零算 |
| `--roles` | 联机分工，如 `"P1:种田, P2:下矿, P3:钓鱼"`；编号不能超过 `--players`。程序会检查有没有人从没做过自己的分工 | 不指定 |
| `--location` | 种植地点：`pelican_town`（鹈鹕镇农场）或 `ginger_island`（姜岛农场）。姜岛和温室一样不受季节限制、换季不枯死，收益表改为按“每 28 天稳态”计算，第 28 天不要求清理农场，耕地上限 777 格；需要已完成社区中心（或 Joja 路线）并修好威利的船 | `pelican_town` |
| `--route` | 游戏路线。`community_center`：社区中心献祭，收集包要用的作物（春 / 夏 / 秋季作物包、染料包的向日葵、厨师包的虞美人、饲料包的 10 个小麦等）先留下、不卖，按售价从收入里扣掉；今年最后能种某种作物的季节还没留下时报“⚠️ 献祭校验”并交给模型修正 | 不指定 |
| `--owned-animals` | 已经养着的成年动物，写法同 `--animals`；不计购买费，从第 1 天起就有产出（导入存档时自动填） | 无 |
| `--animals` | 本季第 1 天新买的动物，如 `"鸡:2,牛:1"`（鸡、鸭、兔子、牛、山羊、绵羊、猪，也可以写英文名）。按 wiki 的成熟天数和产出周期计算基础产品收入，扣除购买价和冬季干草，计入资金；跨季规划时动物带到下一季、不重复购买。养了动物时每天的目标都要包含“照料动物” | 不养 |
| `--greenhouse` | 温室已修好。可以在种植清单里写 `"place": "greenhouse"`：温室不受季节限制，可再生作物换季不会枯死，最多 120 格（超出、没修好时使用都会报问题） | 不开启 |
| `--farm-type` | 农场地图：`standard` / `riverland` / `forest` / `hilltop` / `wilderness` / `four_corners` / `beach` / `meadowlands`（也可以写中文名）。草原农场开局没有赠送的 15 颗防风草种子 | `standard` |
| `--revise-rounds` | 自动修正轮数：程序校验发现问题（来不及成熟、送错礼物、现金不足、日程和种植清单对不上等）时，把问题清单反馈给模型重写，问题变少才采用新版本；会多调用模型 | `1`（`0` = 关闭） |
| `--start-gold` | 期初资金 | 第 1 年春季为开局金币 500g（`data/farm.json`），其他季节为未知 |
| `--start-day` | 季中重新规划：今天是本季第几天（1-28），只规划这天到第 28 天；跨季规划时只有第一个季节从这天开始 | 1（第 1 年春季从第 2 天起不再默认开局金币和赠送的种子） |
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

规划参数（职业、肥料、机器、动物、成就、起始日……）的字段和说明都在 `stardew_planner/options.py` 的 `PlanOptions` 里，可以直接写关键字参数，也可以先组好一个 `PlanOptions` 再传：

```python
from stardew_planner import generate_plan
from stardew_planner.options import PlanOptions

opts = PlanOptions(start_day=15, start_gold=1000, kegs=10, achievements="混合栽培")
plan = generate_plan("种田为主", "速通极限", "spring,summer", provider="mock", options=opts)
```

命令行、网页和存档导入用的都是这份字段名；新加一个规划参数只要在 `PlanOptions` 里加字段，再给命令行和网页加同名的入口（`tests/test_options.py` 会检查有没有漏接）。

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
11. **工具升级**：模型可以在 `tool_upgrades` 里安排工具升级（工具、交送日、等级）。程序按 wiki 规则算出取回日（第 D + 2 天早上，取货日铁匠铺因节日关门时顺延），核对日程里有没有写“交送升级X”和“取回X”，以及工具不在手上的日子有没有安排要用它的工作（喷壶：浇水、播种；十字镐：下矿；斧头：砍树；锄头：开垦），有冲突就交给模型重写。升级费在交送日从资金里扣掉。
12. **体力**：开局最大体力 270 点，喷壶每浇一格消耗 2 − 0.1 × 耕种等级 点（wiki Energy、Skills）。程序按种植清单算出每天要手浇的格数（单次收获的作物浇到收获前一天，可再生作物整季都浇；不算洒水器和下雨天）。浇水超过 100 点的日子算压力大，连续压力大的日子里每 3 天至少要有一天安排补体力（采集大葱、摘鲑莓、去沙龙买沙拉等），否则报“⚠️ 体力预警”并交给模型修正。
13. **建筑工期**：模型可以在 `constructions` 里安排找罗宾盖农场建筑（建筑、下单日）。按 wiki 规则：周二商店不营业、节日不营业，这两种日子不能下单；下单第二天开工，节日不施工、工期顺延，工期 N 天 = 下单后第 N 天早上完工；同一时间只能建一座。程序算出完工日，下单日不合规、施工重叠或下单当天的目标没写建什么都会交给模型修正。建筑费在下单日从资金里扣掉，材料需要玩家自备。
14. **联机**：`--players 2-4` 时，系统 Prompt 追加联机规则（按玩家写日程、按分工安排、打开菜单和钓鱼时时间不暂停所以少排卡点、工具各自升级），模型的 `daily_goals` 每天写成 `{"P1": "…", "P2": "…"}`。程序把每天合成“P1：…；P2：…”交给原有的全农场校验，工具冲突按玩家查（P1 的喷壶在升级，P2 照样能浇水），体力安全线 = 单人阈值 × 人数，任何人补体力都算；超过一半的天数所有人干同一件事、或有人从没做过自己的分工，会报“⚠️ 联机校验”。连续几天每个玩家的目标都相同才合并，渲染成按玩家分行的列表。
15. **Steam 成就**：`--achievements "混合栽培,万人迷"` 时，程序按 `data/achievements.json` 和已有进度（`--achievement-progress`，不给时从零算）算出本季缺口，写进【数据参考】的【🏆 成就规划目标】：混合栽培是本季能在皮埃尔或复活节买到种子、还没运够的作物各要收获几个（沙漠绿洲、旅行货车的作物只列为可选），万人迷等友谊类是本季要增加多少好感度点数（还差的点数平均分到今年剩下的季节，每人只算到要求的心数），百万 / 千万富翁只提示还差多少钱。系统 Prompt 追加成就规则：`macro_strategy.achievement_plan` 写成就思路，`achievement_milestones` 列关键节点，日程里写明相关动作。程序按预计直接出货的个数核对混合栽培（多次收获的作物不用种 15 格；有小桶、罐头瓶时先留够要出货的再加工，留给收集包的不算出货），按日程逐天估算每位村民的好感度（写了“和X聊天”的日子 +20、送礼按喜好计分、生日 ×8、每周最多送 2 次、没聊天的日子 −2，可以结婚的村民送花束前最多 8 颗心），核对友谊类成就每周有没有送礼、本季增加的点数够不够，问题以“⚠️ 成就校验：”开头交给自动修正；跨季规划时预计收获、送礼和收入累计到下一季。
16. **季中重新规划**：`--start-day 15`（网页上是“当前日期（第几天）”）时，用户 Prompt 开头写明今天是第 15 天，系统 Prompt 要求日程、节点、种植、送礼、工具升级和建筑都不早于这天。收益表的最早种植日取 max(第 15 天, 种子开售日)，剩余天数里来不及成熟的当季作物不再列出、单独提示“不要再种”（跨季作物照常列出）；节日和生日只列还没过的。`daily_goals` 只需覆盖第 15-28 天（更早的直接丢掉），种植清单、工具升级、建筑里早于今天的条目不计入资金，报给自动修正；生日送礼只检查第 15 天起的生日；动物在第 15 天买入、蜂蜜和干草也从这天起算。成就的本季指标按剩下的天数比例缩小（本季算 14/28 季），来不及成熟的混合栽培作物留到以后。标题写成“第 1 年春季（第 15–28 天）”，跨季规划的下一季从第 1 天接上。
17. **导入存档**：`--save` 或网页上的“导入星露谷存档”读取 1.6 主存档（`save_parser.py`）。为了安全，带 DOCTYPE / ENTITY 声明的 XML 一律拒绝（星露谷的存档没有这些，这样不会解析外部实体，也不怕实体爆炸），装了 `defusedxml` 时再用它解析。作物的物品 ID、职业 ID（1 农耕人、4 工匠、5 农业学家）和农场编号（0-7，7 是草原农场）放在 `data/save_ids.json`，按 wiki 核对；小桶、罐头瓶按农场和农场建筑里的物品名统计，蜂房只算室外的；动物按类型名里的关键词归类（虚空鸡、金鸡的产品不同，不计入）。食品储藏室修好（`ccPantry` / `jojaPantry`）就算温室已修好；没加入 Joja、食品储藏室也还没修好时选社区中心路线。每一部分单独解析，缺字段、模组物品、看不懂的值只记进提示，不会让整个导入失败。存档里的日期是醒来的那天，所以直接作为季中重新规划的起始日；已有动物用 `owned_animals` 传入，不扣购买费。农场、温室、姜岛农场耕地上的作物按生长阶段算出成熟日（当前阶段还剩的天数加上之后各阶段的天数；可再生作物收过一次后按剩下的再生天数），用 `in_ground` 传入：不再买种子，收获计入资金，可再生作物之后按再生天数继续收，Prompt 里列出来并要求不要重复种植；枯死的、野生的作物不导入，换了季节或年份时也不导入。已经交过的收集包目前没有导入。找本机存档时（`--list-saves`、`--save auto`）先读每个存档文件夹里只有几 KB 的 `SaveGameInfo`（农夫、农场名、日期、金币），按主存档的修改时间排序；`SaveGameInfo` 缺失或损坏时只显示文件夹名。

## 项目结构

```
├── main.py                     命令行入口
├── scripts/eval_llm.py         真实 API 稳定性评估（含 token 用量与缓存命中率）
├── scripts/prompt_stats.py     测量 Prompt 长度（按场景、按章节）
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
│   ├── honey.py                蜂房模拟（按开着的花算花蜜）
│   ├── livestock.py            畜牧（动物产出、购买与干草成本）
│   ├── bundles.py              社区中心献祭路线（预留作物、献祭校验）
│   ├── save_parser.py          导入 1.6 主存档（安全解析、映射成生成器参数、存档解析摘要）
│   ├── tools.py                工具升级时间轴（取回日、冲突校验、升级费用）
│   ├── energy.py               前期体力（每天手浇格数、浇水体力、补体力校验）
│   ├── buildings.py            罗宾的建筑工期（下单日、完工日、同时只能建一座、建筑费用）
│   ├── achievements.py         Steam 成就（本季缺口、成就规划目标、成就校验、跨季进度）
│   ├── coop.py                 联机模式（分工、按玩家的日程、分工校验、联机规则）
│   ├── farm_state.py           跨季收获模拟、资金计算、季末农场状态
│   ├── options.py              规划参数 PlanOptions（字段说明、取值检查、跨季清理）
│   └── generator.py            generate_strategy() / generate_plan() 主流程
├── data/
│   ├── crops.json              作物：季节、种子价、售价、生长/再生天数、产量
│   ├── artisan.json            加工规则：倍率、加成、固定价格、耗时
│   ├── calendar.json           每季节日、采集季、换季日
│   ├── bonuses.json            职业、肥料与作物品质规则
│   ├── farm.json               开局金币、开局种子、农场地图、温室与姜岛农场
│   ├── npcs.json               NPC 生日与送礼喜好
│   ├── playstyles.json         预设玩法 / 节奏及其优劣势说明（给新手挑选用）
│   ├── animals.json            农场动物：价格、成熟天数、产出周期与产品
│   ├── bundles.json            社区中心收集包（标准版）
│   ├── tools.json              铁匠铺工具升级：耗时、费用与材料
│   ├── energy.json             体力上限与浇水消耗
│   ├── buildings.json          罗宾的农场建筑：价格、材料与工期
│   ├── achievements.json       全部 49 个 Steam 成就（条件、类别、混合栽培和单一栽培的作物、好感度规则）
│   ├── save_ids.json           导入存档用的作物物品 ID、职业 ID、农场编号
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

测试覆盖：种植识别（15 种写法）、跨季校验与边界情况、日程合并、收获次数与收益计算、JSON 容错解析（多逗号、漏括号、单引号等）、种子最早开售日对收益的影响、按年份过滤作物（第 2 年起出售的种子）、日历日期、收获掉落种子与副产物、NPC 生日注入与送礼校验（礼物识别、喜好与例外、生日当天送礼）、跨季连续规划（跨季收获模拟、资金与现金流、季末状态注入下一季 Prompt、季节连续性）、职业与加速肥料（价格取整、查表生长天数、职业冲突、肥料成本）、作物品质与品质肥料（概率对照 wiki 表格、四档售价、顶级肥料收益期望值、按机器容量分配的出售策略）、攻略切块与向量入库（MD5 去重、增量更新、换模型后重建）、语义检索与兜底、数据完整性、生成流程的重试与兜底、网页版接口（参数校验、限流、HTML 转义）、Steam 成就（缺口计算、Prompt 注入、成就校验与自动修正、跨季进度）、季中重新规划（收益重算、日程校验范围、资金模拟、跨季衔接）、导入存档（字段提取、异常 XML 容错、拒绝 DOCTYPE、命令行覆盖、网页上传接口）、本机存档查找（扫描与排序、SaveGameInfo 容错、自动选择和按编号选、本地接口开关、目录穿越防护），以及 `sv.py` 的输出与退出码。

## 网页版 Demo

`web/` 是一个最简单的网页版：左边最上面可以导入星露谷存档（只在内存里解析、不保存文件，默认 20MB 以内，`DEMO_MAX_SAVE_MB` 可以改），在本机运行时还会直接列出这台电脑上的存档（如“星露农场 - 第1年春季第15天 (3,200g)”），选好点“一键加载”即可；手动上传下面有“❓ 找不到存档文件？”，列出 Windows、macOS、Linux、Steam Deck 的默认位置和“复制路径”按钮。解析出的季节、日期、现金、职业、机器、已有动物和成就进度会回填到表单里（地里已经种着的作物在季节和年份没改时随请求一起发送），可以再改；也可以手动选玩法、节奏、季节（可以连续规划多季）、联机人数和每个玩家的分工、想完成的成就、职业、肥料和耕种等级，右边显示期末资金卡片和完整攻略，可复制或下载 Markdown。后端是 FastAPI，直接调用 `generate_strategy` / `generate_plan`，没有改业务代码。

<!-- 请在此处补充一张 CLI 运行截图或 Web 界面动图 (GIF) -->

> 🎬 **动图位**：这里适合放一段 10 秒左右的网页操作 GIF：在左侧选好玩法、节奏和季节（可以顺手勾上“温室已修好”、填几个小桶和蜂房），点“生成攻略”，右侧出现期末资金卡片和完整攻略。用 mock 模式录制即可，不消耗 API 额度。

```bash
pip install -r requirements.txt
python -m web                           # 打开 http://127.0.0.1:8000，默认用 mock，不消耗额度
```

用真实模型时设置环境变量 `DEMO_PROVIDER=anthropic`（同时需要 `ANTHROPIC_API_KEY`）。为了控制费用，真实模型默认每个 IP 每小时最多生成 10 次（`DEMO_RATE_LIMIT`），同时最多 2 个生成（`DEMO_MAX_CONCURRENT`），一次最多 4 个季节（`DEMO_MAX_SEASONS`）；单次模型调用超时 300 秒（`DEMO_REQUEST_TIMEOUT`），网页里不再让 SDK 自动重试。部署在反向代理后面时，用 `python -m web --forwarded-allow-ips <代理 IP>`（或环境变量 `DEMO_FORWARDED_ALLOW_IPS`）声明代理，限流才会按用户的真实 IP 计算；直接暴露在公网时不要设成 `*`，否则任何人都能伪造 IP 绕过限流。模型输出里的 HTML 会被转义，不会在页面上执行。读取本机存档的接口（`/api/saves/local`、`/api/saves/local/load`）会暴露服务器上的文件，所以只在 `python -m web` 监听本机地址（默认 127.0.0.1）时开启，并且只回应本机发来的请求；公网部署（如 `--host 0.0.0.0`）或直接用 uvicorn 启动时默认关闭，`DEMO_ALLOW_LOCAL_SAVES=1` / `0` 可以强制开关，`DEMO_SAVES_DIR` 指定存档目录。读取时只认扫描到的存档文件夹名，不接受路径，防止目录穿越。

## 模型稳定性评估

`scripts/eval_llm.py` 用真实 API 跑一组预设用例（春季极限赚钱、春季休闲钓鱼、夏季种玉米跨秋、秋季酿酒、冬季挖矿全收集、第 2 年春季社交送礼、秋季顶级肥料品质农业），统计成功率、一次生成成功率、`attempts`、JSON 解析方式、`season_warnings` 与送礼警告的触发频率和内容、`finance.end_gold`，结束后打印 Markdown 统计表。

```bash
python scripts/eval_llm.py --list                          # 查看用例
python scripts/eval_llm.py --runs 3 --json eval.json       # 每个用例跑 3 次，并保存原始结果
python scripts/eval_llm.py --only spring_rush fall_wine    # 只跑部分用例
python scripts/eval_llm.py --provider mock                 # 离线检查脚本本身，不消耗额度
```

用真实模型时报告里还有“token 用量与缓存”：输入 token 拆成未缓存、写缓存、读缓存三部分，算出缓存命中率。

`scripts/prompt_stats.py` 不调用模型，按几组典型参数（基础、换玩法、职业肥料、全功能、季中开始）统计系统 Prompt 和用户 Prompt 的字符数、各章节占比，以及和基础场景的共同前缀；加 `--count-tokens` 用 Anthropic 的 count_tokens 接口数 token（需要 API 凭证）。目前系统 Prompt 约 3,000 字，用户 Prompt 约 4,400-7,000 字，最大的三块是作物收益表、NPC 生日和成就目标。

真实 provider 每次运行至少调用一次模型，会产生 API 费用。用 `--cases my_cases.json` 可以换成自定义用例（字段同脚本里的 `DEFAULT_CASES`）。有运行失败时脚本退出码为 1。

## Claude Code skill：`stardew-data-guard`

在本项目中使用 [Claude Code](https://claude.com/claude-code) 时，涉及星露谷数值的请求会自动启用这个 skill（也可以用 `/stardew-data-guard` 手动调用）。它要求 Claude：

- **不凭记忆给数值**：数字只能来自 `data/` 或用户提供的数据。数据库没有的内容（如鱼、宝石逐项的送礼喜好，概率掉落），会请用户提供。
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

## 当前局限与未来路线图 (Roadmap)

### ✅ 已完成

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
- [x] 1-4 人联机（`--players` / `--roles`，网页上也能选：日程按玩家分开、分工校验、体力按人数、工具按玩家，联机提示统一以“⚠️ 联机校验：”开头）
- [x] 罗宾的建筑工期（模型输出 `constructions`；周二和节日不能下单，节日不施工顺延，完工前不能再下单，建筑费计入资金）
- [x] 前期体力（按种植清单算每天手浇的体力，压力大时每 3 天至少安排一次补体力，否则交给模型修正）
- [x] 导入 1.6 存档（`--save`，网页上可以上传：自动填好日期、现金、职业、机器、动物和成就进度）
- [x] Prompt 缓存（`ANTHROPIC_CACHE`：系统 Prompt 和整段对话前缀走缓存，格式重试、自动修正、跨季规划受益最大；`scripts/prompt_stats.py` 测量 Prompt 长度）
- [x] 季中重新规划（`--start-day`，网页上也能填：只规划今天到第 28 天，收益、资金和校验都从今天算起）
- [x] Steam 成就（`--achievements`，网页上也能选：全部 49 个；赚钱、友谊、混合栽培、单一栽培由程序算缺口，其他写进 Prompt 作为长期目标；本季缺口写进 Prompt，种植和送礼不达标时以“⚠️ 成就校验：”交给模型修正）
- [x] 姜岛农场（`--location ginger_island`：不受季节限制、换季不枯死、收益表按每 28 天稳态计算）
- [x] 工具升级时间轴（模型输出 `tool_upgrades`；升级 2 天期间不能安排要用这件工具的工作，节日关门顺延取回，升级费计入资金）
- [x] 社区中心献祭路线（`--route community_center`：收集包作物先留下、按售价扣钱，最后机会没留下时报献祭校验）
- [x] 畜牧基础（`--animals`：鸡、鸭、兔子、牛、山羊、绵羊、猪的产出、购买和冬季干草，每天照料动物校验）
- [x] 攻略文本检索从关键词匹配升级为向量检索（ChromaDB）
- [x] 换用多语言 Embedding 模型（paraphrase-multilingual-MiniLM-L12-v2），提升中文检索效果

### 🚧 当前局限

- [ ] 用真实 API 测试模型输出的稳定性（`scripts/eval_llm.py` 已就绪，还没有跑过真实结果）
- [ ] 送礼校验遇到“所有鱼”“所有宝石”这类类别时只保存了 wiki 原文，没有逐项展开，只能提示“不在记录的喜好中”
- [ ] 加工模拟按每台机器每天最多做 1 批计算（咖啡 2 小时就能再装，这里偏保守）
- [ ] 蜂房模拟假设每种花在蜂房旁边留 1 株不收；咖啡豆种子只能从旅行货车碰运气，按 2500g 计
- [ ] 产量不随耕种等级变化（未碾米 10 级起平均 2.11 个，目前固定按 1.61 计）

### 🔭 未来路线图

- [ ] **兼容本地大模型**：支持 Ollama / vLLM 等 OpenAI 兼容接口，实现完全离线的高级规划
- [ ] **畜牧业与温室进阶**：好感度带来的大号产品和品质、蛋黄酱 / 奶酪等加工链、建筑和工具成本；温室里的果树、更多室内作物
- [ ] **导出为日历文件 (.ics)**：把生成的 28 天日程一键导入手机或电脑日历，节日和生日当天自动提醒

## 数据来源与许可

| 内容 | 来源 | 许可 |
|---|---|---|
| 代码（`stardew_planner/`、`web/`、`scripts/`、`tests/`、`main.py` 等） | 本项目编写 | [MIT](LICENSE) |
| `data/crops.json`、`artisan.json`、`bonuses.json`、`farm.json`、`npcs.json`、`animals.json`、`bundles.json`、`tools.json`、`energy.json`、`buildings.json`、`achievements.json`、`save_ids.json` 中的数值 | 整理自 [Stardew Valley Wiki](https://stardewvalleywiki.com)（1.6 版本，核对日期 2026-11-05） | [CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/)，沿用原协议 |
| `data/calendar.json`、`data/playstyles.json`、`data/guides/` | 本项目自行编写（节日日期为游戏内固定日程） | [MIT](LICENSE) |

Stardew Valley Wiki 的内容按 [CC BY-NC-SA 3.0](https://stardewvalleywiki.com/Stardew_Valley_Wiki:Copyrights) 授权：使用时须署名，**不得用于商业用途**，改编后须以相同协议发布。因此，来自 wiki 的数据文件同样按 CC BY-NC-SA 3.0 提供。代码按 MIT 许可，可以自由使用（包括商用）；但如果连同上述数据一起用于商业用途，需要先取得 wiki 版权方的许可，或者换成自行核实的数据。详见 [data/NOTICE.md](data/NOTICE.md)。

## 声明

本项目是非官方的玩家自制工具，与 ConcernedApe 及《星露谷物语》官方无关。游戏名称和相关内容的权利归原作者所有。
