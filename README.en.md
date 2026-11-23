[简体中文](README.md) | **English**

# 🌱 Stardew Valley Strategy Generator

[![tests](https://github.com/Blake2HP/stardew-valley-planner/actions/workflows/tests.yml/badge.svg)](https://github.com/Blake2HP/stardew-valley-planner/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Code License: MIT](https://img.shields.io/badge/code%20license-MIT-green)](LICENSE)
[![Data License: CC BY-NC-SA 3.0](https://img.shields.io/badge/data%20license-CC%20BY--NC--SA%203.0-lightgrey)](data/NOTICE.md)
[![Stardew Valley 1.6](https://img.shields.io/badge/Stardew%20Valley-1.6-6BA539)](https://stardewvalleywiki.com/Version_History)
[![LLM: Claude](https://img.shields.io/badge/LLM-Claude%20Opus%205.5-D97757)](https://www.anthropic.com/claude)

Generate a full-season Stardew Valley guide tailored to your **playstyle** and **pacing**: a high-level strategy, key milestones, and a day-by-day plan for days 1–28.

Unlike asking an LLM directly, this project lets **code handle the numbers and the model handle the planning**:

- Crop prices, growth times, artisan multipliers and other values come from a local database. The program computes profits first and hands them to the model as a "data reference".
- The model's schedule is checked by code: crops that can't mature before the season ends, out-of-season crops, and a missing farm cleanup on day 28 are all flagged or fixed automatically.
- When the model returns malformed JSON, it is repaired locally so that no extra model call is needed.
- Multi-season planning (e.g. `--season spring,summer,fall`): the gold and the crops still in the ground at the end of a season are computed by code and injected into the next season's prompt.

> Crop and artisan values in `data/` come from the [Stardew Valley Wiki](https://stardewvalleywiki.com/Crops) (game version 1.6), checked on 2026-11-05; the 500g starting gold comes from the [Gold](https://stardewvalleywiki.com/Gold) page, and the 15 free Parsnip Seeds from the [Getting Started](https://stardewvalleywiki.com/Getting_Started) page (counted in a new game's first spring, except on the Meadowlands farm).
>
> The game data, prompts and generated guides are in **Chinese**. Playstyle and pacing can be written in any language, but the output guide is in Chinese.

## Example output

<!-- Add a CLI screenshot or a web demo GIF here -->

> 🖼️ **Screenshot slot**: a terminal screenshot of `python main.py --provider mock` fits here, ideally showing both the "⚠️ 换季校验" (season-change check) block and the "📅 每日核心目标" (daily goals), so readers see at a glance that code does the math and the model does the planning.

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

## Contents

- [Example output](#example-output)
- [Quick start](#quick-start)
  - [Command-line options](#command-line-options)
  - [Use from Python](#use-from-python)
- [How it works](#how-it-works)
- [Project structure](#project-structure)
- [Extending the game data](#extending-the-game-data)
- [Tests](#tests)
- [Web demo](#web-demo)
- [Model stability evaluation](#model-stability-evaluation)
- [Claude Code skill: `stardew-data-guard`](#claude-code-skill-stardew-data-guard)
- [Limitations and roadmap](#limitations-and-roadmap)
- [Data sources and licensing](#data-sources-and-licensing)
- [Disclaimer](#disclaimer)

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
| `--list-playstyles` | List the preset playstyles and pacings with their pros, cons and who they suit (the web page shows the same note when you pick a preset), then exit | - |
| `-c, --pacing` | Pacing, e.g. "relaxed", "speedrun" | `休闲慢节奏` |
| `-s, --season` | `spring/summer/fall/winter` (Chinese names like `春季` also work); a comma-separated list of **consecutive** seasons plans them in a row, e.g. `spring,summer` (winter is followed by spring of the next year) | `spring` |
| `-y, --year` | In-game year; in year 1 the profit table and checks leave out crops whose seeds are only sold from year 2 (Garlic, Red Cabbage, Unmilled Rice, Artichoke) | `1` |
| `--provider` | `anthropic` / `mock` | env var `LLM_PROVIDER`, else `anthropic` |
| `--model` | Override the default model name | env var `ANTHROPIC_MODEL` |
| `-o, --output` | Also save the guide to a Markdown file | not saved |
| `--kegs` / `--jars` | Number of kegs and preserves jars. When given, processing is simulated with machine counts and processing times: low-quality, high-gain crops go first, overflow moves to the other machine type, and the gain counts toward gold; goods still in a machine on day 28 carry into the next season | `0` (not simulated) |
| `--bee-houses` | Number of outdoor bee houses. One jar every 4 days, priced by the most valuable flower blooming outdoors on the collection day (wild honey, 100g, with no flower), counted toward gold; none in winter, and greenhouse flowers don't count | `0` (not simulated) |
| `--players` | Number of co-op players, 1-4 (P1 is the host). With more than one, the schedule is written per player and rendered as a nested list, the energy line scales with the player count, tools are checked per player and each upgrade is paid separately. Wallet shared; starting gold and seeds count the host only | `1` |
| `--achievements` | Steam achievements to work toward, comma-separated, e.g. `"混合栽培,万人迷"` (多作种植 also works); all 49 achievements are available, see `--list-achievements`. The earnings tiers (新手 … 千万富翁), the friendship ones (新朋友 … 万人迷), Polyculture and Monoculture get a computed gap for the season, and the shipping and friendship ones are also checked; the rest (fishing, cooking, museum, …) have no planner data and only go into the prompt as long-term goals | none |
| `--list-achievements` | List every achievement by category, marking which ones get a computed gap, then exit | - |
| `--achievement-progress` | Known progress, e.g. `"防风草=15,万人迷=12,千万富翁=50000"`: crop = already shipped, a friendship achievement = people already at its heart level (e.g. `万人迷=12`, `最好的朋友=1`), an earnings achievement = gold earned so far | zero |
| `--roles` | Co-op roles, e.g. `"P1:种田, P2:下矿, P3:钓鱼"`; ids can't exceed `--players`. The program checks that every player actually does their role at some point | none |
| `--location` | Where to farm: `pelican_town` (the valley farm) or `ginger_island` (the island farm). Like the greenhouse, the island has no season limits and crops survive the season change; the profit table switches to a steady-state per-28-days view, day 28 needs no cleanup, and the field holds up to 777 tiles. It requires a finished Community Center (or Joja route) and Willy's repaired boat | `pelican_town` |
| `--route` | Game route. `community_center`: the bundle route; crops needed for bundles (spring / summer / fall crops bundles, the Sunflower in the dye bundle, the Poppy in the chef's bundle, 10 Wheat for the fodder bundle, etc.) are kept instead of sold and their price is taken out of income; if the last season of the year to grow a crop ends without keeping it, a "⚠️ 献祭校验" (bundle check) problem is reported and sent back to the model | none |
| `--save` | Import a Stardew Valley 1.6 main save (the file named like its folder, e.g. `Name_123456789`, not `SaveGameInfo`): fills in season, current day, year, gold, farm map, farming level, professions, greenhouse, route, keg / jar / bee house counts, animals you already own, crops already in the ground and achievement progress (crops shipped, each villager's friendship points, total gold earned), and prints a short save summary. Options you also pass on the command line win; if you change the season or year without `--start-day`, planning starts on day 1. `--save auto` picks the most recently played save; a bare `--save` lists local saves and asks for a number | none |
| `--list-saves` | List the saves found on this computer (number, farm, year/season/day, gold, path; most recent first) and exit | - |
| `--saves-dir` | Save root folder (repeatable) for `--list-saves` and `--save auto` | OS default: Windows `%APPDATA%\StardewValley\Saves`; macOS / Linux `~/.config/StardewValley/Saves`; on Linux also Flatpak Steam's `~/.var/app/com.valvesoftware.Steam/.config/StardewValley/Saves` |
| `--owned-animals` | Grown animals you already own, same format as `--animals`; no purchase cost, producing from day 1 (filled in from a save) | none |
| `--animals` | Animals bought on day 1 of the season, e.g. `"鸡:2,牛:1"` (chicken, duck, rabbit, cow, goat, sheep, pig; English names work too). Base product income follows the wiki's maturity and production times, minus the purchase price and winter hay, and counts toward gold; in multi-season plans the animals carry over without being bought again. With animals, every day's goal must include "照料动物" (care for animals) | none |
| `--greenhouse` | The greenhouse is repaired. Plantings may set `"place": "greenhouse"`: no season limits, regrowing crops survive the season change, at most 120 tiles (exceeding it or using a locked greenhouse is reported as a problem) | off |
| `--farm-type` | Farm map: `standard` / `riverland` / `forest` / `hilltop` / `wilderness` / `four_corners` / `beach` / `meadowlands` (Chinese names also work). The Meadowlands farm starts without the 15 free Parsnip Seeds | `standard` |
| `--revise-rounds` | Self-correction rounds: when the checks find problems (late plantings, wrong gifts, cash shortfalls, goals that don't match the plantings list, etc.), the list goes back to the model for a rewrite, kept only if it has fewer problems; costs extra model calls | `1` (`0` = off) |
| `--start-gold` | Starting gold | 500g starting gold (`data/farm.json`) in year-1 spring, unknown otherwise |
| `--start-day` | Mid-season replanning: today's day of the season (1-28); only plans from that day to day 28. In multi-season plans only the first season starts there | 1 (from day 2 of year-1 spring the starting gold and free seeds are no longer assumed) |
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
4. **Tolerant parsing**: tries `strict` (standard parsing) → `cleaned` (fixes repeated and trailing commas) → `repaired` (hands off to [json_repair](https://github.com/mangiucugna/json_repair) for missing brackets, missing commas, single quotes, etc.). Syntax errors are fixed locally and don't trigger a retry; the model is asked again only when content is incomplete (for example, a missing day or no plantings list) or the output hit `max_tokens`. If it still fails, a fallback result is used and the guide warns clearly what is missing and that the gold estimate is unreliable. A model refusal is reported as such, not retried as a format error.
5. **Season-change checks**:
   - Harvest day = planting day + growth days, which must be ≤ 28 (unless the crop also grows next season).
   - An out-of-season crop dies immediately when planted.
   - Day 28's goal must include "clear the farm / stop planting" (`清理农场/停止种植`).
   - Negated phrases ("don't replant"), explanations in parentheses, and "only buy seeds" are not mistaken for planting.
   - **Self-correction**: problems the checks find (not the fixed reminders such as season-change notes) go back to the model as a list for one rewrite; the rewrite is kept only if it has fewer problems, otherwise the original stays. The guide notes the problem count before and after, and the eval script reports the change (`--revise-rounds 0` turns it off).
6. **Merging and rendering**: consecutive days with the same goal are merged into a range (e.g. "Days 3-5: fishing"), and ranges that include a festival are marked with 🎉.
7. **Crop quality and sale plan**: with a farming level or a quality fertilizer, each harvest's quality odds follow the wiki formula (gold chance g = 0.2 × level / 10 + 0.2 × fertilizer level × (level + 2) / 12 + 0.01, silver min(0.75, 2g), iridium g / 2 with Deluxe Fertilizer only), and price = ⌊base × quality multiplier⌋, then Tiller. Extra crops are always regular quality. Artisan goods don't inherit quality, so the gain from processing is largest for regular crops and smallest for iridium: with enough machines everything is processed; with fewer (`--processing-share`) the capacity goes to the biggest gains first, which means selling high-star crops and processing low-star ones.
8. **Plantings and gold**: the model must list every sowing (crop, day, number of tiles) in `plantings`; a missing list triggers a retry. The program simulates each harvest, across seasons where the crop allows it (corn planted in summer keeps producing until the end of fall), computes seed and fertilizer costs and crop income, and checks day by day that there is enough cash for the seeds. The model also estimates fishing, mining and other income in `other_income`; the guide shows it apart from the computed figures, labeled as a model estimate.
9. **Multi-season planning**: seasons are generated one at a time. After each season the farm state is extracted (end-of-season gold, multi-season crops still in the ground with their harvest days next season, cleared crops, leftover seeds, last season's planned goals) and written into a "carried-over state" section of the next season's user prompt; the system prompt stays the same apart from one rule about following on. Replanting a crop that is still in the ground triggers a warning, and the day-28 cleanup goal names the crops to keep (e.g. `清理农场/停止种植（保留玉米）`).
10. **Birthday gifts**: `data/npcs.json` holds birthdays and loved / liked / disliked / hated gifts for all 34 giftable villagers, plus the universal like, neutral, dislike and hate lists. When a season is generated, the villagers with a birthday that season and their preferences are injected into the data reference, and the system prompt requires a "送{NPC}{礼物}" (give NPC a gift) task on the birthday. The program then checks each gift: first the items listed on the villager's page, then the wiki's category notes for crops and artisan goods (e.g. Haley dislikes all vegetables, Sebastian hates artisan goods), then the universal lists (Poppy is a universal hate, except for Penny). Disliked, hated and neutral gifts are all reported. The 12 marriage candidates must get a gift on their birthday; other villagers are optional. A loved gift on a birthday is worth 80 × 8 = 640 friendship points.
11. **Tool upgrades**: the model can schedule upgrades in `tool_upgrades` (tool, drop-off day, level). Following the wiki, the program works out the pickup day (morning of day D + 2, pushed back when the Blacksmith is closed for a festival), checks that the schedule says "交送升级X" (drop off) and "取回X" (pick up), and that no work needing the tool is planned while it is away (watering can: watering and sowing; pickaxe: the mines; axe: chopping trees; hoe: tilling). Conflicts go back to the model for a rewrite. The upgrade cost is taken from gold on the drop-off day.
12. **Energy**: players start with 270 max energy, and each watering costs 2 − 0.1 × farming level (wiki Energy, Skills). From the planting list the program works out how many tiles must be hand-watered each day (single-harvest crops until the day before harvest, regrowing crops all season; sprinklers and rain are not counted). Days above 100 energy of watering are heavy; across a run of heavy days, at least one day in every 3 must plan food (foraging leeks, picking salmonberries, buying a salad at the Saloon, etc.), otherwise a "⚠️ 体力预警" (energy warning) goes back to the model.
13. **Construction timeline**: the model can order farm buildings from Robin in `constructions` (building, order day). Following the wiki: the shop is closed on Tuesdays and festivals, so no orders then; work starts the next day, festivals pause it, and an N-day build is ready on the morning of day D + N; only one building at a time. The program works out the finish day, and bad order days, overlapping builds or an order day whose goal doesn't name the building go back to the model. The cost is taken from gold on the order day; materials are up to the player.
14. **Co-op**: with `--players 2-4`, co-op rules are appended to the system prompt (per-player goals, follow the roles, time doesn't pause in menus or fishing so avoid tight timing, tools are upgraded per player), and `daily_goals` becomes `{"P1": "…", "P2": "…"}` per day. Each day is merged into "P1：…；P2：…" for the existing farm-wide checks; tool conflicts are checked per player (P1's can at the Blacksmith doesn't stop P2 watering), the energy line is the single-player threshold times the player count, and anyone's food counts. Everyone doing the same thing on more than half the days, or a role nobody follows, is reported as "⚠️ 联机校验". Days merge only when every player's goal matches, and the schedule renders one line per player.
15. **Steam achievements**: with `--achievements "混合栽培,万人迷"`, the program reads `data/achievements.json` and any known progress (`--achievement-progress`, zero if omitted), works out this season's gap and writes it into a 【🏆 成就规划目标】 block: for Polyculture, how many of each crop still to harvest among those whose seeds Pierre or the Egg Festival sell this year (desert and travelling-cart crops are optional); for Popular and the other friendship ones, how many friendship points to gain this season (the points still missing spread over the rest of the year, each person counted only up to the required hearts); for Millionaire / Legend, just the gold still missing. The system prompt asks for `macro_strategy.achievement_plan`, an `achievement_milestones` list and explicit daily actions. Polyculture is checked against expected direct shipments (regrowing crops don't need 15 tiles; with kegs or jars the shipping amount is kept back before processing, and crops kept for bundles don't count), friendship achievements against a day-by-day estimate of each villager's points (+20 on days the schedule says "和X聊天", gifts by taste, ×8 on birthdays, at most 2 gifts a week, −2 on days without talking, marriage candidates capped at 8 hearts before the bouquet), checking for a gift every week and enough points this season; misses start with "⚠️ 成就校验：" and go to the revise loop. In multi-season plans, harvests, gifts and income carry over to the next season.
16. **Mid-season replanning**: with `--start-day 15` ("当前日期（第几天）" on the web page), the user prompt says today is day 15 and the system prompt keeps every schedule day, milestone, planting, gift, tool upgrade and construction on or after it. The crop table plants on max(day 15, seed release day); season-only crops that can no longer mature are dropped and listed as "don't plant" (cross-season crops stay), and only festivals and birthdays still ahead are listed. `daily_goals` only needs days 15-28 (earlier days are dropped); plantings, tool upgrades and constructions before today aren't charged and go to the revise loop; only birthdays from day 15 on are checked; animals are bought on day 15 and honey and hay count from that day. Achievement targets for the season shrink in proportion to the days left (this season counts as 14/28), and Polyculture crops that can't mature are left for later. The title reads "第 1 年春季（第 15–28 天）", and the next season in a multi-season plan starts on day 1.
17. **Save import**: `--save`, or "导入星露谷存档" on the web page, reads a 1.6 main save (`save_parser.py`). For safety, XML with a DOCTYPE or ENTITY declaration is rejected outright (Stardew saves never have one, so no external entities and no entity bombs), and `defusedxml` is used when installed. Crop item IDs, profession IDs (1 Tiller, 4 Artisan, 5 Agriculturist) and farm type numbers (0-7, 7 = Meadowlands) live in `data/save_ids.json`, checked against the wiki; kegs and jars are counted by item name on the farm and inside farm buildings, bee houses only outdoors; animals are grouped by keywords in their type (Void and Golden chickens have different products and are skipped). A repaired Pantry (`ccPantry` / `jojaPantry`) means the greenhouse is fixed; with no Joja membership and no Pantry yet, the Community Center route is selected. Each part is parsed on its own, so missing fields, modded items or odd values only add a note instead of failing the import. The save's date is the morning you wake up on, so it becomes the mid-season start day; owned animals go in as `owned_animals` with no purchase cost. Crops on the farm, greenhouse and Ginger Island tilled soil get a ripening day from their growth phase (days left in the current phase plus the later phases; for regrowing crops already harvested once, the regrow days left) and go in as `in_ground`: no seed cost, harvests count toward gold, regrowing crops keep producing, and the prompt lists them so they aren't planted again. Dead and forage crops are skipped, and nothing is imported if you change the season or year. Bundles already donated aren't imported yet. When looking for local saves (`--list-saves`, `--save auto`), the small `SaveGameInfo` in each folder is read first (farmer, farm name, date, gold), sorted by the main save's modified time; if `SaveGameInfo` is missing or broken, only the folder name is shown.

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
│   ├── processing.py           processing simulation (machine counts, processing time, goods across seasons)
│   ├── honey.py                bee house simulation (honey priced by the flowers in bloom)
│   ├── livestock.py            livestock (animal output, purchase and hay costs)
│   ├── bundles.py              Community Center bundle route (kept crops, bundle checks)
│   ├── save_parser.py          1.6 save import (safe parsing, mapping to generator options, save summary)
│   ├── tools.py                tool upgrade timeline (pickup day, conflict checks, upgrade cost)
│   ├── energy.py               early-game energy (tiles watered per day, watering energy, food checks)
│   ├── buildings.py            Robin's construction timeline (order day, finish day, one at a time, cost)
│   ├── achievements.py         Steam achievements (season gaps, the prompt block, checks, progress across seasons)
│   ├── coop.py                 co-op mode (roles, per-player schedules, division checks, co-op rules)
│   ├── farm_state.py           cross-season harvest simulation, gold, end-of-season farm state
│   └── generator.py            generate_strategy() / generate_plan() pipeline
├── data/
│   ├── crops.json              crops: seasons, seed price, sell price, growth/regrow days, yield
│   ├── artisan.json            artisan rules: multiplier, bonus, fixed price, processing time
│   ├── calendar.json           festivals, foraging seasons and season changes
│   ├── bonuses.json            profession, fertilizer and crop quality rules
│   ├── farm.json               starting gold and seeds, farm maps, greenhouse and Ginger Island farm
│   ├── npcs.json               villager birthdays and gift preferences
│   ├── playstyles.json         preset playstyles / pacings with pros and cons for new players
│   ├── animals.json            farm animals: prices, maturity, production times and products
│   ├── bundles.json            Community Center bundles (standard)
│   ├── tools.json              Blacksmith tool upgrades: time, cost and materials
│   ├── energy.json             max energy and watering cost
│   ├── buildings.json          Robin's farm buildings: price, materials and build time
│   ├── achievements.json       all 49 Steam achievements (conditions, categories, Polyculture and Monoculture crops, friendship rules)
│   ├── save_ids.json           crop item IDs, profession IDs and farm type numbers for save import
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

The tests cover planting detection (15 phrasings), season-change checks and edge cases, schedule merging, harvest-count and profit math, tolerant JSON parsing (repeated commas, missing brackets, single quotes, etc.), how the seed sale day affects profit, filtering crops by year (seeds sold from year 2), calendar dates, seed drops and byproducts, birthday injection and gift checks (gift detection, preferences and exceptions, gifts on the birthday), multi-season planning (cross-season harvests, gold and cash flow, injecting the end-of-season state into the next prompt, season order), professions and speed fertilizers (price rounding, table-based growth days, profession conflicts, fertilizer cost), crop quality and quality fertilizers (odds checked against the wiki tables, quality prices, expected profit with Deluxe Fertilizer, capacity-based sell-or-process plans), guide chunking and vector indexing (MD5 dedup, incremental updates, rebuild on model change), semantic search and its fallback, data integrity, retries and fallbacks in the generation pipeline, the web API (validation, rate limiting, HTML escaping), Steam achievements (gap math, prompt injection, checks and revisions, progress across seasons), mid-season replanning (profit from today, schedule range, money simulation, hand-off to the next season), save import (field extraction, tolerance of broken XML, DOCTYPE rejection, command-line overrides, the web upload API), local save discovery (scanning and sorting, broken SaveGameInfo, auto and numbered selection, the local-only switch, path traversal), and `sv.py` output and exit codes.

## Web demo

`web/` is a minimal web version: at the top of the left panel you can import a Stardew save (parsed in memory only, never stored, up to 20MB by default, set `DEMO_MAX_SAVE_MB` to change it), when running locally it also lists the saves on this computer (e.g. "星露农场 - 第1年春季第15天 (3,200g)") with a one-click load button, and below the upload button a "❓ 找不到存档文件？" panel lists the default save folders for Windows, macOS, Linux and Steam Deck with copy buttons. The season, day, gold, professions, machines, owned animals and achievement progress it finds are filled into the form for you to adjust (crops already in the ground are sent along as long as the season and year stay the same); or pick a playstyle, pacing, seasons (several in a row are fine), co-op players and each player's role, achievements to work toward, professions, fertilizer and farming level on the left; the right side shows end-of-season gold cards and the full guide, which you can copy or download as Markdown. The backend is FastAPI calling `generate_strategy` / `generate_plan` directly, with no changes to the core code.

<!-- Add a CLI screenshot or a web demo GIF here -->

> 🎬 **GIF slot**: a ~10-second GIF of the web page fits here: pick a playstyle, pacing and season on the left (maybe tick "温室已修好" (greenhouse repaired) and enter a few kegs and bee houses), click "生成攻略" (generate), and the gold cards and full guide appear on the right. Recording in mock mode costs no API credit.

```bash
pip install -r requirements.txt
python -m web                           # open http://127.0.0.1:8000; uses mock by default, no API cost
```

Set `DEMO_PROVIDER=anthropic` (plus `ANTHROPIC_API_KEY`) to use the real model. To keep costs down, real providers default to 10 generations per IP per hour (`DEMO_RATE_LIMIT`), at most 2 at a time (`DEMO_MAX_CONCURRENT`), and at most 4 seasons per request (`DEMO_MAX_SEASONS`); each model call times out after 300 seconds (`DEMO_REQUEST_TIMEOUT`) and the web app turns off SDK retries. Behind a reverse proxy, declare the proxy with `python -m web --forwarded-allow-ips <proxy IP>` (or `DEMO_FORWARDED_ALLOW_IPS`) so rate limits use each user's real IP; don't set it to `*` on a server exposed directly to the internet, or anyone can fake an IP to get around the limit. HTML in model output is escaped, so it never runs in the page. The local-save endpoints (`/api/saves/local`, `/api/saves/local/load`) expose files on the server, so they are only on when `python -m web` listens on a loopback address (127.0.0.1 by default), and then only answer requests from the same machine; a public deployment (e.g. `--host 0.0.0.0`) or a plain uvicorn start keeps them off. `DEMO_ALLOW_LOCAL_SAVES=1` / `0` forces them on or off, and `DEMO_SAVES_DIR` sets the save folder. Loading only accepts a folder name found by the scan, never a path, which blocks path traversal.

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

- **Never give numbers from memory**: values must come from `data/` or from the user. For anything the database doesn't cover (such as item-by-item gift tastes for fish and gems, or drop chances), Claude asks the user for exact data.
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

### ✅ Done

- [x] Replace the mock data with real 1.6 values (source: Stardew Valley Wiki)
- [x] Profession bonuses (Tiller, Artisan, Agriculturist) and speed fertilizers (Speed-Gro, Deluxe, Hyper)
- [x] Crop quality (silver / gold / iridium) and quality fertilizers (Basic / Quality / Deluxe), with a sell-high-stars, process-low-stars plan
- [x] Birthday gifts (birthdays and loved / liked gifts for 6 villagers, prompt injection and gift checks)
- [x] Add the remaining villagers (34 in total) and the universal like / neutral / dislike / hate lists; gift checks tell the four levels apart
- [x] Simulate processing with machine counts and processing time (`--kegs` / `--jars`)
- [x] Greenhouse planning (`--greenhouse`: no season limits, 120-tile cap, crops carry across seasons)
- [x] 1-4 player co-op (`--players` / `--roles`, also in the web form: per-player schedules, role checks, energy scaled by players, per-player tools; co-op messages start with "⚠️ 联机校验：")
- [x] Robin's construction timeline (the model outputs `constructions`; no orders on Tuesdays or festivals, festivals push the finish back, one building at a time, cost counted toward gold)
- [x] Early-game energy (watering energy per day from the planting list; on heavy days food must be planned at least every 3 days, otherwise the model revises)
- [x] 1.6 save import (`--save`, or upload on the web page: fills in the date, gold, professions, machines, animals and achievement progress)
- [x] Mid-season replanning (`--start-day`, also in the web form: plans from today to day 28, with profit, money and checks all counted from today)
- [x] Steam achievements (`--achievements`, also in the web form: all 49; earnings, friendship, Polyculture and Monoculture get a computed gap, the rest go into the prompt as long-term goals; this season's gap goes into the prompt, and short plantings or gifts are sent back as "⚠️ 成就校验：")
- [x] Ginger Island farm (`--location ginger_island`: no season limits, crops survive the season change, steady-state per-28-days profit table)
- [x] Tool upgrade timeline (the model outputs `tool_upgrades`; no work needing the tool during the 2-day upgrade, pickup moves past festival closures, cost counted toward gold)
- [x] Community Center route (`--route community_center`: bundle crops are kept and their price deducted; a bundle check fires when the last chance passes)
- [x] Basic livestock (`--animals`: output, purchase and winter hay for chickens, ducks, rabbits, cows, goats, sheep and pigs, plus a daily animal-care check)
- [x] Add an "earliest planting day" field to crops (e.g. strawberry seeds are only sold at the Egg Festival on day 13) to fix the profit ranking
- [x] Add Garlic, Red Cabbage and Sunflower, and track seeds sold only from year 2 with `available_year`
- [x] Add Blue Jazz, Wheat and Poppy (Wheat → Beer via a crop-specific artisan rule)
- [x] Add Bok Choy, Artichoke, Fairy Rose, Summer Spangle and Unmilled Rice
- [x] Artisan rules with several inputs per product (5 Coffee Beans → 1 Coffee), and Coffee Bean itself (seeds only from the Traveling Cart, counted at 2,500g)
- [x] Honey rules for flowers (price = 100 + flower base price × 2, shown in crop notes and `sv.py artisan`)
- [x] Bee house simulation (`--bee-houses`: one jar every 4 days, priced by the flowers in bloom, counted toward gold)
- [x] Multi-season planning (carry gold and multi-season crops forward)
- [x] Artisan goods across seasons (goods still in a machine are credited when they finish next season)
- [x] Upgrade guide-text search from keyword matching to vector search (ChromaDB)
- [x] Switch to a multilingual embedding model (paraphrase-multilingual-MiniLM-L12-v2) for better Chinese retrieval

### 🚧 Current limitations

- [ ] Test model output stability against real APIs (`scripts/eval_llm.py` is ready, but no real results yet)
- [ ] Gift checks keep categories such as "all fish" or "all gems" as the wiki text only, without listing each item, so they can only say "not in the recorded tastes"
- [ ] The processing simulation runs at most one batch per machine per day (coffee could be reloaded every 2 hours, so this is conservative)
- [ ] The bee house simulation assumes one plant of each flower is left by the hives; Coffee Bean seeds only come from the Traveling Cart by luck and are counted at 2,500g
- [ ] Yield doesn't change with farming level (Unmilled Rice averages 2.11 from level 10, but is fixed at 1.61)

### 🔭 Future plans

- [ ] **Prompt caching**: the data tables and rules are resent on every call; caching them would noticeably cut API cost and time to first token, most of all for multi-season planning and self-correction
- [ ] **Local models**: support OpenAI-compatible endpoints such as Ollama / vLLM for fully offline planning
- [ ] **Advanced livestock and greenhouse**: large and quality products from friendship, the mayonnaise / cheese processing chain, building and tool costs; fruit trees and more indoor crops in the greenhouse
- [ ] **Export to calendar (.ics)**: import the 28-day schedule straight into a phone or desktop calendar, with reminders on festivals and birthdays

## Data sources and licensing

| Content | Source | License |
|---|---|---|
| Code (`stardew_planner/`, `web/`, `scripts/`, `tests/`, `main.py`, etc.) | Written for this project | [MIT](LICENSE) |
| Values in `data/crops.json`, `artisan.json`, `bonuses.json`, `farm.json`, `npcs.json`, `animals.json`, `bundles.json`, `tools.json`, `energy.json`, `buildings.json`, `achievements.json` | Compiled from the [Stardew Valley Wiki](https://stardewvalleywiki.com) (version 1.6, checked on 2026-11-05) | [CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/), same as the original |
| `data/calendar.json`, `data/playstyles.json`, `data/guides/` | Written for this project (festival dates are the game's fixed schedule) | [MIT](LICENSE) |

Stardew Valley Wiki content is licensed under [CC BY-NC-SA 3.0](https://stardewvalleywiki.com/Stardew_Valley_Wiki:Copyrights): it requires attribution, **does not allow commercial use**, and adaptations must be shared under the same license. The data files derived from the wiki are therefore also provided under CC BY-NC-SA 3.0. The code is MIT-licensed and free to use, including commercially; commercial use together with that data requires permission from the wiki's copyright holders, or replacing the data with independently verified values. See [data/NOTICE.md](data/NOTICE.md).

## Disclaimer

This is an unofficial fan-made tool and is not affiliated with ConcernedApe or Stardew Valley. All rights to the game's name and content belong to their owners.
