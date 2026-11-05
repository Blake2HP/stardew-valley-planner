[简体中文](README.md) | **English**

# 🌱 Stardew Valley Strategy Generator

Generate a full-season Stardew Valley guide tailored to your **playstyle** and **pacing**: a high-level strategy, key milestones, and a day-by-day plan for days 1–28.

Unlike asking an LLM directly, this project lets **code handle the numbers and the model handle the planning**:

- Crop prices, growth times, artisan multipliers and other values come from a local database. The program computes profits first and hands them to the model as a "data reference".
- The model's schedule is checked by code: crops that can't mature before the season ends, out-of-season crops, and a missing farm cleanup on day 28 are all flagged or fixed automatically.
- When the model returns malformed JSON, it is repaired locally so that no extra model call is needed.

> Crop and artisan values in `data/` come from the [Stardew Valley Wiki](https://stardewvalleywiki.com/Crops) (game version 1.6), checked on 2026-11-05.
>
> The game data, prompts and generated guides are in **Chinese**. Playstyle and pacing can be written in any language, but the output guide is in Chinese.

## Example output

```bash
python main.py -p "钓鱼赚钱，兼顾种田" -c "休闲慢节奏" -s spring --provider mock
```

(Playstyle: "make money fishing, with some farming"; pacing: "relaxed".)

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

The guide has a high-level strategy, a milestone table, a season-change check, and the daily goals with consecutive identical days merged (e.g. "Days 3-4: fish at the beach"). See [sample_output.md](sample_output.md) for the full example.

## Quick start

Requires Python 3.10 or later. The dependencies include sentence-transformers and PyTorch (CPU build), which take a few minutes to install. On first run, a multilingual embedding model of about 460 MB (paraphrase-multilingual-MiniLM-L12-v2) is downloaded from Hugging Face; after that it works offline.

```bash
git clone https://github.com/Blake2HP/stardew-valley-planner.git
cd stardew-valley-planner
pip install -r requirements.txt
```

**Try it offline** (mock mode, no API key needed):

```bash
python main.py --provider mock
```

**Use a real LLM**: copy `.env.example` to `.env` and fill in your key:

```ini
LLM_PROVIDER=anthropic          # anthropic / openai / mock
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-opus-5-5
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1
```

```bash
python main.py -p "Farming first, finish the Community Center" -c "Speedrun" -s summer -o guide.md
```

### Command-line options

| Option | Description | Default |
|---|---|---|
| `-p, --playstyle` | Playstyle, e.g. "fishing for money", "completionist", "farming first" | `钓鱼赚钱，兼顾种田` |
| `-c, --pacing` | Pacing, e.g. "relaxed", "speedrun" | `休闲慢节奏` |
| `-s, --season` | `spring/summer/fall/winter` (Chinese names like `春季` also work) | `spring` |
| `-y, --year` | In-game year | `1` |
| `--provider` | `anthropic` / `openai` / `mock` | env var `LLM_PROVIDER`, else `anthropic` |
| `--model` | Override the default model name | env var `ANTHROPIC_MODEL` / `OPENAI_MODEL` |
| `-o, --output` | Also save the guide to a Markdown file | not saved |

### Use from Python

```python
from stardew_planner import generate_strategy

result = generate_strategy("钓鱼赚钱", "休闲慢节奏", season="spring", provider="mock")
print(result.markdown)          # full guide
print(result.schedule)          # merged schedule blocks
print(result.season_warnings)   # season-change check results
print(result.attempts, result.json_parse_method)  # number of model calls, JSON parse method
```

## How it works

```
data/*.json ──► analysis.py ──┐
  crops/artisan/calendar  profit ranking │
                             ├──► prompts.py ──► LLM ──► json_utils.py ──► schema check
data/guides/*.md ──► vector search  ─┘   system + user prompt     tolerant parsing   (one retry if invalid)
                                                                                  │
         Markdown guide ◄── renderer.py ◄── schedule.py ◄── season_rules.py ◄────┘
                                            merge schedule    season-change checks
```

1. **Data loading**: reads structured values (crops, artisan rules, seasonal calendar) and unstructured guide texts. Guide texts are split by `## heading` and stored in ChromaDB (`data/.chroma_db`) with the MD5 of the text as the ID, so unchanged passages are never written twice and deleted ones are removed. A "season, playstyle, pacing" query then retrieves the 3 most relevant passages by semantic search. The vector store records which embedding model built it and is rebuilt automatically when the model changes. If ChromaDB is unavailable (not installed, model download failed, etc.), it falls back to keyword search.
2. **Deterministic calculation**: before calling the model, the program computes each crop's harvest count for the season, net profit per tile, and best artisan product. Planting never starts before the seeds go on sale, and available growing days = 28 − planting day + 1 (e.g. strawberry seeds go on sale on day 13, leaving 16 days).
3. **Prompt**: the system prompt fixes the output order (high-level strategy → milestones → days 1–28) and requires all numbers to come from the data reference.
4. **Tolerant parsing**: tries `strict` (standard parsing) → `cleaned` (fixes repeated and trailing commas) → `repaired` (hands off to [json_repair](https://github.com/mangiucugna/json_repair) for missing brackets, missing commas, single quotes, etc.). Syntax errors are fixed locally and don't trigger a retry; the model is only asked again when content is incomplete (for example, a missing day).
5. **Season-change checks**:
   - Harvest day = planting day + growth days, which must be ≤ 28 (unless the crop also grows next season).
   - An out-of-season crop dies immediately when planted.
   - Day 28's goal must include "clear the farm / stop planting" (`清理农场/停止种植`).
   - Negated phrases ("don't replant"), explanations in parentheses, and "only buy seeds" are not mistaken for planting.
6. **Merging and rendering**: consecutive days with the same goal are merged into a range (e.g. "Days 3-5: fishing"), and ranges that include a festival are marked with 🎉.

## Project structure

```
├── main.py                     CLI entry point
├── stardew_planner/
│   ├── data_loader.py          data loading, guide chunking, ChromaDB indexing and semantic search
│   ├── analysis.py             profit calculation, builds the prompt's data reference
│   ├── prompts.py              system / user / repair prompt templates
│   ├── llm_client.py           Anthropic / OpenAI / mock clients
│   ├── json_utils.py           tolerant parsing of LLM JSON output
│   ├── season_rules.py         season-change checks
│   ├── schedule.py             schedule merging
│   ├── renderer.py             Markdown rendering
│   └── generator.py            generate_strategy() pipeline
├── data/
│   ├── crops.json              crops: seasons, seed price, sell price, growth/regrow days, yield
│   ├── artisan.json            artisan rules: multiplier, bonus, fixed price, processing time
│   ├── calendar.json           festivals, foraging seasons and season changes
│   └── guides/*.md             guide texts
├── tests/                      pytest tests
└── .claude/skills/stardew-data-guard/   Claude Code project skill
```

## Extending the game data

**Add a crop**: append an entry to the `crops` array in `data/crops.json`:

```json
{"name": "向日葵", "name_en": "Sunflower", "seasons": ["summer", "fall"],
 "seed_price": 200, "sell_price": 80, "grow_days": 8, "regrow_days": null,
 "category": "flower", "harvest_yield": 1.0, "earliest_available_day": 1,
 "seed_source": "皮埃尔杂货店"}
```

`category` decides which artisan rules apply (`fruit` / `vegetable` / `flower` / `special`). `regrow_days: null` means the crop is harvested only once. `harvest_yield` is the expected yield per harvest (base yield + p / (1 − p), where p is the chance of extra crops). `earliest_available_day` is the first day of the season the seeds can be bought; it is 1 for regular crops. `name` is the Chinese name used in schedules; `name_en` is also accepted by the `sv.py` lookup tool.

**Add guide text**: put `.md` or `.txt` files in `data/guides/`. Each `## heading` becomes one passage. A line like `标签: 春季, 钓鱼, 新手` (tags: spring, fishing, beginner) under the heading is used to match playstyles during search.

## Tests

```bash
python -m pytest
```

There are currently 117 tests, covering planting detection (15 phrasings), season-change checks and edge cases, schedule merging, harvest-count and profit math, tolerant JSON parsing (repeated commas, missing brackets, single quotes, etc.), how the seed sale day affects profit, guide chunking and vector indexing (MD5 dedup, incremental updates, rebuild on model change), semantic search and its fallback, data integrity, retries and fallbacks in the generation pipeline, and `sv.py` output and exit codes.

## Claude Code skill: `stardew-data-guard`

When you use [Claude Code](https://claude.com/claude-code) in this project, requests involving Stardew Valley numbers automatically use this skill (you can also invoke it with `/stardew-data-guard`). It requires Claude to:

- **Never give numbers from memory**: values must come from `data/` or from the user. For anything the database doesn't cover (such as NPC gift preferences), Claude asks the user for exact data.
- **Always check cross-season schedules**: run the schedule through the checker script and include a "⚠️ season-change check" block.
- **Show the formula before the result**: e.g. `wine price = base price × 3 = 120 × 3 = 360g`.

The bundled CLI tool can also be used on its own:

```bash
python .claude/skills/stardew-data-guard/scripts/sv.py crop Strawberry
python .claude/skills/stardew-data-guard/scripts/sv.py artisan Strawberry
python .claude/skills/stardew-data-guard/scripts/sv.py profit Strawberry --plant-day 14
python .claude/skills/stardew-data-guard/scripts/sv.py calendar summer
python .claude/skills/stardew-data-guard/scripts/sv.py check-schedule plan.json --season spring --fix
```

Exit codes: `0` success, `2` data not in the database, `3` the check found problems.

## Limitations and roadmap

- [x] Replace the mock data with real 1.6 values (source: Stardew Valley Wiki)
- [ ] Add NPC data (gift preferences, birthdays, friendship), quality and profession bonuses, fertilizer
- [x] Add an "earliest planting day" field to crops (e.g. strawberry seeds are only sold at the Egg Festival on day 13) to fix the profit ranking
- [ ] Add more crops (Garlic, Blue Jazz, Wheat, Sunflower, Red Cabbage, etc.) and track seeds that are only sold from year 2
- [ ] Support multi-season and full-year planning
- [x] Upgrade guide-text search from keyword matching to vector search (ChromaDB)
- [x] Switch to a multilingual embedding model (paraphrase-multilingual-MiniLM-L12-v2) for better Chinese retrieval
- [ ] Test model output stability against real APIs

## Disclaimer

This is an unofficial fan-made tool and is not affiliated with ConcernedApe or Stardew Valley. All rights to the game's name and content belong to their owners.
