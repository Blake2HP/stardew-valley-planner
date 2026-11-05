# CLAUDE.md

## 提交规范（Git commit / PR）

- 提交信息格式：
  - 第一行是英文祈使句摘要，不超过 72 个字符，首字母大写，末尾不加句号，例如 `Add fault-tolerant JSON parsing`。
  - 需要说明时空一行，正文用 `- ` 列表写清改了什么、为什么改。
  - Claude 参与的提交，末尾空一行后加上共同作者署名：`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`（模型名按实际使用的模型填写）。
- 一个提交只做一件事；提交前运行 `python -m pytest`，确保全部通过。
- 只在用户要求时提交或推送；不要强制推送（`--force`），除非用户明确要求。
