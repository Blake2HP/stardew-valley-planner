[简体中文](README.md) | **English**

# 🌱 Stardew Valley Strategy Generator

[![tests](https://github.com/Blake2HP/stardew-valley-planner/actions/workflows/tests.yml/badge.svg)](https://github.com/Blake2HP/stardew-valley-planner/actions/workflows/tests.yml)

Generate a full-season Stardew Valley guide tailored to your **playstyle** and **pacing**: a high-level strategy, key milestones, and a day-by-day plan for days 1–28.

Unlike asking an LLM directly, this project lets **code handle the numbers and the model handle the planning**:

- Crop prices, growth times, artisan multipliers and other values come from a local database. The program computes profits first and hands them to the model as a "data reference".
- The model's schedule is checked by code: crops that can't mature before the season ends, out-of-season crops, and a missing farm cleanup on day 28 are all flagged or fixed automatically.
- When the model returns malformed JSON, it is repaired locally so that no extra model call is needed.
- Multi-season planning (e.g. `--season spring,summer,fall`): the gold and the crops still in the ground at the end of a season are computed by code and injected into the next season's prompt.

> Crop and artisan values in `data/` come from the [Stardew Valley Wiki](https://stardewvalleywiki.com/Crops) (game version 1.6), checked on 2026-11-05; the 500g starting gold comes from the [Gold](https://stardewvalleywiki.com/Gold) page.
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
LLM_PROVIDER=anthropic          # anthropic / mock
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-opus-5-5
```

Claude request settings (all can go in `.env`):

| Env var | Default | Notes |
|---|---|---|
| `ANTHROPIC_EFFORT` | `medium` | Thinking effort (`low` / `medium` / `high` / `xhigh` / `max`). Claude Opus 5.5 always thinks; effort is the only control |
| `ANTHROPIC_MAX_TOKENS` | `16000` | Output cap, thinking tokens included; a truncated reply is retried |
| `ANTHROPIC_TIMEOUT` | `300` | Per-request timeout in seconds |
| `ANTHROPIC_MAX_RETRIES` | `1` | SDK retries for network errors, 429 and 5xx |
| `ANTHROPIC_FALLBACKS` | `on` | When the model declines for safety reasons, the API reruns the request on another model in the same call (`fallbacks: "default"`), billed at that model's rates; `off` disables it. A final refusal is reported clearly instead of being retried as a JSON error |

```bash
python main.py -p "Farming first, finish the Community Center" -c "Speedrun" -s summer -o guide.md
```

### Command-line options

| Option | Description | Default |
|---|---|---|
| `-p, --playstyle` | Playstyle, e.g. "fishing for money", "completionist", "farming first" | `钓鱼赚钱，兼顾种田` |
| `-c, --pacing` | Pacing, e.g. "relaxed", "speedrun" | `休闲慢节奏` |
| `-s, --season` | `spring/summer/fall/winter` (Chinese names like `春季` also work); a comma-separated list of **consecutive** seasons plans them in a row, e.g. `spring,summer` (winter is followed by spring of the next year) | `spring` |
| `-y, --year` | In-game year; in year 1 the profit table and checks leave out crops whose seeds are only sold from year 2 (Garlic, Red Cabbage) | `1` |
| `--provider` | `anthropic` / `mock` | env var `LLM_PROVIDER`, else `anthropic` |
| `--model` | Override the default model name | env var `ANTHROPIC_MODEL` |
| `-o, --output` | Also save the guide to a Markdown file | not saved |
| `--start-gold` | Starting gold | 500g starting gold (`data/farm.json`) in year-1 spring, unknown otherwise |
| `--professions` | Comma-separated professions: `tiller` (crops +10%), `artisan` (artisan goods +40%), `agriculturist` (growth +10%). Chinese names also work; choosing Artisan or Agriculturist adds Tiller automatically, and the two exclude each other | none |
| `--fertilizer` | Fertilizer (one per tile). Speed: `speed_gro` (10%), `deluxe_speed_gro` (25%), `hyper_speed_gro` (33%); quality: `basic_fertilizer`, `quality_fertilizer`, `deluxe_fertilizer` (the only way to get iridium) | none |
| `--farming-level` | Farming level 0–14; when given (or with a quality fertilizer), profits are expected values over crop quality. Defaults to the lowest level the chosen professions require | quality not counted |
| `--processing-share` | Share of each harvest your machines can process, 0–1; below 1 the program works out which qualities to sell directly and which to process | `1` (enough machines) |

### Use from Python

```python
from stardew_planner import generate_strategy

result = generate_strategy("钓鱼赚钱", "休闲慢节奏", season="spring", provider="mock",
                           professions=["artisan"], fertilizer="speed_gro")
print(result.markdown)          # full guide
print(result.schedule)          # merged schedule blocks
print(result.season_warnings)   # season-change check results
print(result.attempts, result.json_parse_method)  # number of model calls, JSON parse method
print(result.bonuses.describe())  # applied professions and fertilizer
print(result.finance.end_gold)    # end-of-season gold (farming only, computed by code)
result = generate_strategy("种田为主", "速通极限", "spring", provider="mock",
                           fertilizer="deluxe_fertilizer", farming_level=10, processing_share=0.5)
```

Multi-season planning:

```python
from stardew_planner import generate_plan

plan = generate_plan("种田为主", "速通极限", "spring,summer,fall", provider="mock")
print(plan.markdown)                   # overview table + one guide per season
for state in plan.states:              # farm state at the end of each season
    print(state.next_season, state.gold, state.gold_estimated, state.carried_names)
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
2. **Deterministic calculation**: before calling the model, the program computes each crop's harvest count for the season, net profit per tile, and best artisan product. Planting never starts before the seeds go on sale, and available growing days = 28 − planting day + 1 (e.g. strawberry seeds go on sale on day 13, leaving 16 days). Multi-season crops are valued over their full life (corn planted in summer counts through the end of fall), the table is sorted by average profit per season, and multi-season crops are always listed.
3. **Prompt**: the system prompt fixes the output order (high-level strategy → milestones → days 1–28) and requires all numbers to come from the data reference.
4. **Tolerant parsing**: tries `strict` (standard parsing) → `cleaned` (fixes repeated and trailing commas) → `repaired` (hands off to [json_repair](https://github.com/mangiucugna/json_repair) for missing brackets, missing commas, single quotes, etc.). Syntax errors are fixed locally and don't trigger a retry; the model is only asked again when content is incomplete (for example, a missing day).
5. **Season-change checks**:
   - Harvest day = planting day + growth days, which must be ≤ 28 (unless the crop also grows next season).
   - An out-of-season crop dies immediately when planted.
   - Day 28's goal must include "clear the farm / stop planting" (`清理农场/停止种植`).
   - Negated phrases ("don't replant"), explanations in parentheses, and "only buy seeds" are not mistaken for planting.
6. **Merging and rendering**: consecutive days with the same goal are merged into a range (e.g. "Days 3-5: fishing"), and ranges that include a festival are marked with 🎉.
7. **Crop quality and sale plan**: with a farming level or a quality fertilizer, each harvest's quality odds follow the wiki formula (gold chance g = 0.2 × level / 10 + 0.2 × fertilizer level × (level + 2) / 12 + 0.01, silver min(0.75, 2g), iridium g / 2 with Deluxe Fertilizer only), and price = ⌊base × quality multiplier⌋, then Tiller. Extra crops are always regular quality. Artisan goods don't inherit quality, so the gain from processing is largest for regular crops and smallest for iridium: with enough machines everything is processed; with fewer (`--processing-share`) the capacity goes to the biggest gains first, which means selling high-star crops and processing low-star ones.
8. **Plantings and gold**: the model must list every sowing (crop, day, number of tiles) in `plantings`; a missing list triggers a retry. The program simulates each harvest, across seasons where the crop allows it (corn planted in summer keeps producing until the end of fall), computes seed and fertilizer costs and crop income, and checks day by day that there is enough cash for the seeds. The model also estimates fishing, mining and other income in `other_income`; the guide shows it apart from the computed figures, labeled as a model estimate.
9. **Multi-season planning**: seasons are generated one at a time. After each season the farm state is extracted (end-of-season gold, multi-season crops still in the ground with their harvest days next season, cleared crops, last season's planned goals) and written into a "carried-over state" section of the next season's user prompt; the system prompt stays the same apart from one rule about following on. Replanting a crop that is still in the ground triggers a warning, and the day-28 cleanup goal names the crops to keep (e.g. `清理农场/停止种植（保留玉米）`).
10. **Birthday gifts**: `data/npcs.json` holds birthdays and loved / liked gifts for 6 villagers (Haley, Shane, Penny, Abigail, Sebastian, Leah). When a season is generated, the villagers with a birthday that season and their preferences are injected into the data reference, and the system prompt requires a "送{NPC}{礼物}" (give NPC a gift) task on the birthday. The program then checks that each gift is among that villager's loved or liked items (including universal loves and their exceptions, e.g. Haley hates Prismatic Shard) and that every birthday has a gift task. A loved gift on a birthday is worth 80 × 8 = 640 friendship points.

## Project structure

```
├── main.py                     CLI entry point
├── scripts/eval_llm.py         stability evaluation against the real API
├── web/                        web demo (FastAPI + single page)
├── stardew_planner/
│   ├── data_loader.py          data loading, guide chunking, ChromaDB indexing and semantic search
│   ├── analysis.py             profit calculation, builds the prompt's data reference
│   ├── prompts.py              system / user / repair prompt templates
│   ├── llm_client.py           Anthropic (Claude) / mock clients
│   ├── json_utils.py           tolerant parsing of LLM JSON output
│   ├── season_rules.py         season-change checks
│   ├── schedule.py             schedule merging
│   ├── renderer.py             Markdown rendering
│   ├── bonuses.py              profession and speed-fertilizer bonuses
│   ├── farm_state.py           cross-season harvest simulation, gold, end-of-season farm state
│   └── generator.py            generate_strategy() / generate_plan() pipeline
├── data/
│   ├── crops.json              crops: seasons, seed price, sell price, growth/regrow days, yield
│   ├── artisan.json            artisan rules: multiplier, bonus, fixed price, processing time
│   ├── calendar.json           festivals, foraging seasons and season changes
│   ├── bonuses.json            profession, fertilizer and crop quality rules
│   ├── farm.json               starting gold
│   ├── npcs.json               villager birthdays and gift preferences
│   ├── NOTICE.md               data sources and licensing
│   └── guides/*.md             guide texts
├── tests/                      pytest tests
└── .claude/skills/stardew-data-guard/   Claude Code project skill
```

## Extending the game data

**Add a crop**: append an entry to the `crops` array in `data/crops.json`:

```json
{"name": "大蒜", "name_en": "Garlic", "seasons": ["spring"],
 "seed_price": 40, "sell_price": 60, "grow_days": 4, "regrow_days": null,
 "category": "vegetable", "harvest_yield": 1.0, "earliest_available_day": 1,
 "available_year": 2, "seed_source": "皮埃尔杂货店（第 2 年起）",
 "speed_grow_days": {"10": 3, "20": 3, "25": 3, "33": 2, "35": 2, "43": 2}}
```

`category` decides which artisan rules apply (`fruit` / `vegetable` / `flower` / `special`); a few crops have crop-specific rules in `artisan.json` (`input_crops`), e.g. Wheat in a Keg makes Beer instead of Juice. `regrow_days: null` means the crop is harvested only once. `harvest_yield` is the expected yield per harvest (base yield + p / (1 − p), where p is the chance of extra crops). `earliest_available_day` is the first day of the season the seeds can be bought; it is 1 for regular crops. `available_year` is the year the seeds go on sale (default 1): with `year=1`, the profit ranking and the prompt leave out crops with `available_year > 1`, and planting one in a schedule triggers a warning. The optional `aliases` lists other names for a crop (such as the official Chinese names 西红柿 and 虞美人花), accepted wherever crop names are matched; the optional `seed_drop` is the average number of seeds dropped per harvest (1 for Sunflower), used before buying seeds for replanting; `byproducts` lists extra drops at harvest (e.g. a 40% chance of Hay from Wheat; Hay can't be sold, so it adds no income). `speed_grow_days` holds growth days under speed bonuses, keyed by total speed percent (fertilizer 10 / 25 / 33, plus 10 for Agriculturist). The values come from the calendars on each crop's wiki page: the game removes days per growth stage and has floating-point quirks, so they can't be derived from the percentage (e.g. Yam takes 7 days at 20%, not 8). `name` is the Chinese name used in schedules; `name_en` is also accepted by the `sv.py` lookup tool.

**Add guide text**: put `.md` or `.txt` files in `data/guides/`. Each `## heading` becomes one passage. A line like `标签: 春季, 钓鱼, 新手` (tags: spring, fishing, beginner) under the heading is used to match playstyles during search.

## Tests

```bash
python -m pytest
```

There are currently 484 tests, covering planting detection (15 phrasings), season-change checks and edge cases, schedule merging, harvest-count and profit math, tolerant JSON parsing (repeated commas, missing brackets, single quotes, etc.), how the seed sale day affects profit, filtering crops by year (seeds sold from year 2), calendar dates, seed drops and byproducts, birthday injection and gift checks (gift detection, preferences and exceptions, gifts on the birthday), multi-season planning (cross-season harvests, gold and cash flow, injecting the end-of-season state into the next prompt, season order), professions and speed fertilizers (price rounding, table-based growth days, profession conflicts, fertilizer cost), crop quality and quality fertilizers (odds checked against the wiki tables, quality prices, expected profit with Deluxe Fertilizer, capacity-based sell-or-process plans), guide chunking and vector indexing (MD5 dedup, incremental updates, rebuild on model change), semantic search and its fallback, data integrity, retries and fallbacks in the generation pipeline, the web API (validation, rate limiting, HTML escaping), and `sv.py` output and exit codes.

## Web demo

`web/` is a minimal web version: pick a playstyle, pacing, seasons (several in a row are fine), professions, fertilizer and farming level on the left; the right side shows end-of-season gold cards and the full guide, which you can copy or download as Markdown. The backend is FastAPI calling `generate_strategy` / `generate_plan` directly, with no changes to the core code.

```bash
pip install -r requirements.txt
python -m web                           # open http://127.0.0.1:8000; uses mock by default, no API cost
```

Set `DEMO_PROVIDER=anthropic` (plus `ANTHROPIC_API_KEY`) to use the real model. To keep costs down, real providers default to 10 generations per IP per hour (`DEMO_RATE_LIMIT`), at most 2 at a time (`DEMO_MAX_CONCURRENT`), and at most 4 seasons per request (`DEMO_MAX_SEASONS`); each model call times out after 300 seconds (`DEMO_REQUEST_TIMEOUT`) and the web app turns off SDK retries. Behind a reverse proxy, declare the proxy with `python -m web --forwarded-allow-ips <proxy IP>` (or `DEMO_FORWARDED_ALLOW_IPS`) so rate limits use each user's real IP; don't set it to `*` on a server exposed directly to the internet, or anyone can fake an IP to get around the limit. HTML in model output is escaped, so it never runs in the page.

## Model stability evaluation

`scripts/eval_llm.py` runs a set of preset cases against the real API (spring speedrun profit, relaxed spring fishing, summer corn into fall, fall winemaking, winter mining and collecting, year-2 spring socializing with birthday gifts, fall Deluxe Fertilizer quality farming). It reports the success rate, first-try success rate, `attempts`, JSON parse methods, how often `season_warnings` and gift warnings fire and what they say, and `finance.end_gold`, then prints Markdown tables.

```bash
python scripts/eval_llm.py --list                          # list the cases
python scripts/eval_llm.py --runs 3 --json eval.json       # run each case 3 times and save raw results
python scripts/eval_llm.py --only spring_rush fall_wine    # run only some cases
python scripts/eval_llm.py --provider mock                 # check the script offline, no API cost
```

Real providers call the model at least once per run, which costs API credit. `--cases my_cases.json` swaps in your own cases (same fields as `DEFAULT_CASES` in the script). The script exits with code 1 if any run fails.

## Claude Code skill: `stardew-data-guard`

When you use [Claude Code](https://claude.com/claude-code) in this project, requests involving Stardew Valley numbers automatically use this skill (you can also invoke it with `/stardew-data-guard`). It requires Claude to:

- **Never give numbers from memory**: values must come from `data/` or from the user. For anything the database doesn't cover (such as villagers not yet recorded, or drop chances), Claude asks the user for exact data.
- **Always check cross-season schedules**: run the schedule through the checker script and include a "⚠️ season-change check" block.
- **Show the formula before the result**: e.g. `wine price = base price × 3 = 120 × 3 = 360g`.

The bundled CLI tool can also be used on its own:

```bash
python .claude/skills/stardew-data-guard/scripts/sv.py crop Strawberry
python .claude/skills/stardew-data-guard/scripts/sv.py artisan Strawberry
python .claude/skills/stardew-data-guard/scripts/sv.py profit Strawberry --plant-day 14
python .claude/skills/stardew-data-guard/scripts/sv.py simulate Corn --season summer --tiles 8
python .claude/skills/stardew-data-guard/scripts/sv.py profit Potato --professions agriculturist --fertilizer speed_gro
python .claude/skills/stardew-data-guard/scripts/sv.py quality Cauliflower --fertilizer deluxe_fertilizer --farming-level 10 --processing-share 0.5
python .claude/skills/stardew-data-guard/scripts/sv.py npc Haley                     # birthday and gift preferences
python .claude/skills/stardew-data-guard/scripts/sv.py check-schedule plan.json --season spring --gifts  # also check gifts
python .claude/skills/stardew-data-guard/scripts/sv.py calendar summer
python .claude/skills/stardew-data-guard/scripts/sv.py check-schedule plan.json --season spring --fix
```

Exit codes: `0` success, `2` data not in the database, `3` the check found problems.

## Limitations and roadmap

- [x] Replace the mock data with real 1.6 values (source: Stardew Valley Wiki)
- [x] Profession bonuses (Tiller, Artisan, Agriculturist) and speed fertilizers (Speed-Gro, Deluxe, Hyper)
- [x] Crop quality (silver / gold / iridium) and quality fertilizers (Basic / Quality / Deluxe), with a sell-high-stars, process-low-stars plan
- [x] Birthday gifts (birthdays and loved / liked gifts for 6 villagers, prompt injection and gift checks)
- [ ] Add the remaining villagers and full universal like / dislike item lists
- [ ] Model machine counts and processing time in the sale plan (currently approximated by a processing share)
- [x] Add an "earliest planting day" field to crops (e.g. strawberry seeds are only sold at the Egg Festival on day 13) to fix the profit ranking
- [x] Add Garlic, Red Cabbage and Sunflower, and track seeds sold only from year 2 with `available_year`
- [x] Add Blue Jazz, Wheat and Poppy (Wheat → Beer via a crop-specific artisan rule)
- [ ] Add more crops (Bok Choy, Coffee Bean, etc.) and honey rules for flowers
- [x] Multi-season planning (carry gold and multi-season crops forward)
- [ ] Artisan goods across seasons (started at the end of one season, ready the next)
- [x] Upgrade guide-text search from keyword matching to vector search (ChromaDB)
- [x] Switch to a multilingual embedding model (paraphrase-multilingual-MiniLM-L12-v2) for better Chinese retrieval
- [ ] Test model output stability against real APIs

## Data sources and licensing

| Content | Source | License |
|---|---|---|
| Values in `data/crops.json`, `artisan.json`, `bonuses.json`, `farm.json`, `npcs.json` | Compiled from the [Stardew Valley Wiki](https://stardewvalleywiki.com) (version 1.6, checked on 2026-11-05) | [CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/), same as the original |
| `data/calendar.json`, `data/guides/` | Written for this project (festival dates are the game's fixed schedule) | Same as the project code |

Stardew Valley Wiki content is licensed under [CC BY-NC-SA 3.0](https://stardewvalleywiki.com/Stardew_Valley_Wiki:Copyrights): it requires attribution, **does not allow commercial use**, and adaptations must be shared under the same license. The data files derived from the wiki are therefore also provided under CC BY-NC-SA 3.0. Commercial use of this project requires permission from the wiki's copyright holders, or replacing that data with independently verified values. See [data/NOTICE.md](data/NOTICE.md).

## Disclaimer

This is an unofficial fan-made tool and is not affiliated with ConcernedApe or Stardew Valley. All rights to the game's name and content belong to their owners.
