# 断点 2026-08-15 FactSet→LSEG

- **FactSet《Earnings Insight》**：`earnings_insight.csv` 中 `source=FactSet`，最后一期 **2026-08-07**。
- **LSEG I/B/E/S《This Week in Earnings》**：自 **2026-08-15** 起 `source=LSEG`；周日晨间只采 LSEG。
- **不可直接对比**：EPS Surprise、超预期比例、季度/年度增速等在断点前后口径不同；看板两段分开画，断口不是真实跳变。
- **政策**：继续用 LSEG；禁止改回 FactSet；禁止重写断点前历史行或旧笔记。

相关字段：`pipeline/us/insight.py` 的 `BREAK`；看板 `insight.break_date` / `insight.note`。
