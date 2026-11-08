# 更新记录

## 0.1.1 — 2026-11-08

修两处会让技能「看起来坏了」的真实缺陷。

### 运行环境可移植

- `SKILL.md` 新增「运行环境（开工前 30 秒，必做）」：四级探测（`DSH_PYTHON` → DSH 自带运行时 → `py -3`/`python3` → 裸 `python` 且退出码为 0），并说明 **9009 是解释器没启动，不是脚本没输出**。
- `references/workflow.md`、`references/checklist.md`、`references/interoperability.md`、`references/report-deck.md` 的示例命令改为占位符 `<python>`，不再写死某台机器的绝对路径。
- `scripts/interop/run_ext.py`：`DEFAULT_PYTHON` 由硬编码路径改为运行时解析（`DSH_PYTHON` → `DSH_HOME` 下运行时 → `sys.executable`），删掉写死的用户目录。
- `SKILL.md` §7 从「本机环境事实（不要重新探测）」改为「撰写机的环境事实（示例，不是你的机器）」，避免把出图机器的软件清单当成通用前提。

### GLB 不再写绝对路径

- `scripts/core/gltf_export.py`：调色板（palette）引用写相对路径，导出的 `model.glb` 里不再出现本机绝对路径；换目录、换工作目录重导 SHA256 一致。
- `examples/demo-campus/expected/model.glb` 重新生成，与上述修改对齐。

### 验收

`verify_skill.py` 18/18 PASS；三张 A1 图逐张目视复核（图框、标题栏、指北针、比例尺、图例、尺寸链、门窗表、房间面积表在位，中文无豆腐块）。

## 0.1.0 — 2026-10-23

首个版本：`design.json` 单一真相源 → 指标真算 → A1 图纸（SVG/PDF/PNG）→ DXF R12 + glTF 2.0 GLB → GB·CJJ 与 IBC·LEED 双体系校核 → 28 案例 / 112 标签的手法检索。
