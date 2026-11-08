#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""arch-env-design 阶段自检器。

用法：
    python checklist.py <design.json> --stage 概念|方案|深化 [--json]
    python checklist.py --list-rules
    python checklist.py --help

判定来源分两类：
  1) 指标类（FAR / DENSITY / GREEN / PARK）只认 core 引擎的输出，本脚本不自算：
     "$PY" -m scripts.core.design_io check <design.json 绝对路径> --json   （cwd = skill 根目录）
  2) 设计意图类字段（竖向路径、剖面、出入口、材料、配色等）直接读 design.json。
core 不可用时，依赖指标的规则输出 UNVERIFIED 并给出修复提示，其余规则照常判定。

退出码：存在 FAIL 返回 1；否则返回 0；输入不可读或参数错误返回 2。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

STAGES = ("概念", "方案", "深化")
STATUSES = ("PASS", "WARN", "FAIL", "UNVERIFIED")
SKILL_ROOT = Path(__file__).resolve().parents[2]

FAR_ALIASES = ("far", "far_actual", "far_used", "far_value", "plot_ratio",
               "floor_area_ratio", "far_design", "容积率")
FAR_LIMIT_ALIASES = ("far_limit", "far_max", "far_cap", "far_allowed",
                     "plot_ratio_limit", "far_upper", "far_limit_value", "容积率上限")
DENS_ALIASES = ("density", "density_pct", "building_density", "building_coverage",
                "coverage", "density_value", "site_coverage", "建筑密度")
DENS_LIMIT_ALIASES = ("density_limit", "density_max", "density_cap", "coverage_limit",
                      "building_density_limit", "density_upper", "建筑密度上限")
GREEN_ALIASES = ("green", "greenratio", "green_ratio", "green_ratio_pct", "green_rate",
                 "greenery_ratio", "green_area_ratio", "绿地率")
GREEN_MIN_ALIASES = ("green_min", "green_ratio_min", "green_ratio_min_pct", "green_rate_min",
                     "required_green_ratio", "green_ratio_lower", "绿地率下限")
PARK_ALIASES = ("parking", "parking_provided", "parking_count", "parking_total",
                "parking_spaces", "car_spaces", "停车位", "机动车位")
PARK_REQ_ALIASES = ("parking_required", "parking_req", "parking_demand", "required_parking",
                    "parking_standard", "配建停车", "停车配建")

ACTUAL_SUBKEYS = ("actual", "value", "design", "current", "used", "computed", "provided", "total")
PARK_SUBKEYS = ("car", "cars", "motor", "automobile", "car_spaces", "total", "value", "actual",
                "provided", "count", "spaces")
LIMIT_SUBKEYS = ("limit", "max", "cap", "upper", "allowed", "min", "lower", "floor",
                 "required", "requirement")

METRIC_LABEL = {
    "far": "FAR",
    "far_limit": "FAR 上限",
    "density": "DENSITY",
    "density_limit": "DENSITY 上限",
    "green": "GREEN",
    "green_min": "GREEN 下限",
    "parking": "PARK",
    "parking_required": "PARK 配建要求",
}

CRITICAL_METRICS = (("far", "FAR"), ("density", "DENSITY"), ("green", "GREEN"), ("parking", "PARK"))

RULE_TITLES = {
    "PLAN-METRIC-01": "指标预估表四项齐备",
    "PLAN-FAR-01": "容积率不超限值且留余量",
    "PLAN-DENSITY-01": "建筑密度不超限值且留余量",
    "PLAN-GREEN-01": "绿地率不低于下限",
    "PLAN-PARK-01": "停车配建满足要求",
    "PLAN-SITE-01": "场地限制条件逐项有处置",
    "PLAN-PROG-01": "决策悬空登记（已定/未定/假设）",
    "PLAN-CASE-01": "先例引用合规（不超过 3 个且写明借哪一层）",
    "PLAN-XLAY-01": "交汇叠图与 1:500 建筑-景观接口图",
    "PLAN-DRAIN-01": "竖向闭合（排水路径至出口、无反坡）",
    "PLAN-SECT-01": "剖面解释平面",
    "PLAN-FIRE-01": "消防车道闭合与登高面净空",
    "PLAN-ACCESS-01": "建筑出入口连续无障碍路径",
    "PLAN-BASE-01": "地下室轮廓不越红线与退线",
    "PLAN-TREE-01": "乔木等级与顶板覆土厚度匹配",
    "DET-WIN-01": "同一立面窗宽呈整数倍（窗洞模数）",
    "DET-WIN-02": "立面窗洞与平面窗洞、结构梁位一致",
    "DET-NODE-01": "外露材料均有节点做法与缝宽",
    "DET-CLEAR-01": "关键空间净高与余量",
    "AEST-MOD-01": "基准模数整除主要尺寸",
    "AEST-MAT-01": "立面主材不超过 3 种且有主次",
    "AEST-LIGHT-01": "9 组光影校核齐备",
    "AEST-COLOR-01": "色相数与主色占比、明度跨度",
}


# ------------------------------------------------------------------ 小工具

def emit(text):
    sys.stdout.write(text + "\n")


def ensure_utf8():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            continue


def as_number(node, subkeys=()):
    """把任意标量/对象尽力转成 float，转不了返回 None。"""
    if isinstance(node, bool) or node is None:
        return None
    if isinstance(node, (int, float)):
        return float(node)
    if isinstance(node, str):
        raw = node.strip().replace(",", "")
        match = re.search(r"-?\d+(?:\.\d+)?", raw)
        if match is None:
            return None
        try:
            return float(match.group(0))
        except ValueError:
            return None
    if isinstance(node, dict):
        for key in subkeys:
            if key in node:
                value = as_number(node[key], subkeys)
                if value is not None:
                    return value
        return None
    return None


def find_key(node, aliases, depth=0):
    """递归按键名找值，返回原始值（可能是对象）。键名大小写不敏感。"""
    if depth > 4 or not isinstance(node, dict):
        return None
    lowered = {}
    for key in node.keys():
        lowered[str(key).strip().lower()] = key
    for alias in aliases:
        real = lowered.get(alias.lower())
        if real is None:
            continue
        return node[real]
    for key in node.keys():
        value = node[key]
        if isinstance(value, dict):
            found = find_key(value, aliases, depth + 1)
            if found is not None:
                return found
    return None


def unwrap_metrics(payload):
    if not isinstance(payload, dict):
        return {}
    for key in ("metrics", "indicators", "kpi", "KPI", "指标"):
        if isinstance(payload.get(key), dict):
            return payload[key]
    return payload


def extract_metrics(payload):
    """把 core（或 design.json 自带）的指标对象归一成 CANON -> float。"""
    root = unwrap_metrics(payload)
    metrics = {}
    pairs = (
        ("far", FAR_ALIASES, FAR_LIMIT_ALIASES, "far_limit"),
        ("density", DENS_ALIASES, DENS_LIMIT_ALIASES, "density_limit"),
        ("green", GREEN_ALIASES, GREEN_MIN_ALIASES, "green_min"),
        ("parking", PARK_ALIASES, PARK_REQ_ALIASES, "parking_required"),
    )
    for canon, aliases, limit_aliases, limit_key in pairs:
        raw = find_key(root, aliases)
        if isinstance(raw, dict):
            subkeys = PARK_SUBKEYS if canon == "parking" else ACTUAL_SUBKEYS
            value = as_number(raw, subkeys)
            limit = as_number(raw, LIMIT_SUBKEYS)
        else:
            subkeys = PARK_SUBKEYS if canon == "parking" else ACTUAL_SUBKEYS
            value = as_number(raw, subkeys)
            limit = None
        if value is not None:
            metrics[canon] = value
        if limit is None:
            limit = as_number(find_key(root, limit_aliases), LIMIT_SUBKEYS)
        if limit is not None:
            metrics[limit_key] = limit
    for key in ("density", "density_limit", "green", "green_min"):
        value = metrics.get(key)
        if value is not None and 0 < value <= 1.0:
            metrics[key] = round(value * 100.0, 4)
    return metrics


def parse_json_payload(text):
    if not text:
        return None
    cleaned = text.lstrip("\ufeff").strip()
    try:
        return json.loads(cleaned)
    except ValueError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except ValueError:
                return None
    return None


def make(rule, status, actual, limit, advice):
    if status not in STATUSES:
        raise ValueError("未知状态: " + str(status))
    return {
        "rule": rule,
        "title": RULE_TITLES.get(rule, ""),
        "status": status,
        "actual": actual,
        "limit": limit,
        "advice": advice,
    }


def unverified(rule, missing, how):
    return make(rule, "UNVERIFIED", "缺字段 " + missing, how,
                "在 design.json 补齐 " + missing + " 后重跑；字段名见 references/checklist.md 契约表")


# ------------------------------------------------------------------ core 调用

def core_check(design_path):
    """返回 (metrics|None, 失败原因, 命令行回显)。metrics 可能是部分字段。"""
    py = os.environ.get("PY") or sys.executable
    command = [py, "-m", "scripts.core.design_io", "check", str(design_path), "--json"]
    shown = " ".join(command)
    try:
        proc = subprocess.run(
            command,
            cwd=str(SKILL_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except FileNotFoundError as exc:
        return None, "无法启动 Python：" + str(exc), shown
    except subprocess.TimeoutExpired:
        return None, "core 调用超时（180s）", shown
    except OSError as exc:
        return None, "core 调用失败：" + str(exc), shown
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        tail = (err or out).splitlines()
        last = tail[-1][:220] if tail else ""
        return None, "core 退出码 " + str(proc.returncode) + "：" + last, shown
    payload = parse_json_payload(out)
    if payload is None:
        return None, "core --json 输出无法解析：" + (out[:180] or "(空)"), shown
    metrics = extract_metrics(payload)
    if not metrics:
        return None, "core 输出中没有可识别的指标字段（期望 metrics.far 等）", shown
    return metrics, "", shown


def metrics_from_design(design):
    for key in ("metrics", "indicators", "指标"):
        block = design.get(key)
        if isinstance(block, dict):
            return extract_metrics(block)
    return {}


LIMIT_BLOCKS = ("limits", "control", "regulatory", "requirements", "规划条件", "控制指标")


def design_limits(design):
    """从 design.json 的 limits/规划条件块取**上限**，补成 far_limit / density_limit / green_min / parking_required。"""
    block = None
    for key in LIMIT_BLOCKS:
        node = design.get(key)
        if isinstance(node, dict):
            block = node
            break
    if not block:
        return {}
    out = {}
    spec = (
        ("far_limit", ("far", "far_limit", "floor_area_ratio", "容积率")),
        ("density_limit", ("density", "density_limit", "coverage", "建筑密度")),
        ("green_min", ("green", "green_min", "greenratio", "绿地率")),
        ("parking_required", ("parking", "parking_required", "cars", "配建停车")),
    )
    for target, aliases in spec:
        raw = find_key(block, aliases)
        subkeys = PARK_SUBKEYS if target == "parking_required" else ACTUAL_SUBKEYS
        value = as_number(raw, subkeys)
        if value is None:
            continue
        if target in ("density_limit", "green_min") and 0 < value <= 1.0:
            value = round(value * 100.0, 4)
        out[target] = value
    return out


def merge_metrics(actual, limits):
    """实际值来自 core/design.metrics，上限值来自 design.limits；两边互不覆盖对方已有的键。"""
    merged = dict(limits or {})
    merged.update({k: v for k, v in (actual or {}).items() if v is not None})
    return merged


# ------------------------------------------------------------------ 规则实现

def rule_metric_table(ctx):
    metrics = ctx["metrics"] or {}
    source = "core"
    if not metrics:
        metrics = metrics_from_design(ctx["design"])
        source = "design.json"
    if not metrics:
        return [make("PLAN-METRIC-01", "UNVERIFIED",
                     "未取到指标表（core 不可用且 design.json 无 metrics 块）",
                     "概念阶段须有 FAR/DENSITY/GREEN/PARK 四项",
                     "先补 design.json 的 metrics 块或完成 core 引擎")]
    missing = [label for key, label in CRITICAL_METRICS if metrics.get(key) is None]
    if missing:
        return [make("PLAN-METRIC-01", "FAIL",
                     "缺 " + "/".join(missing) + "（来源 " + source + "）",
                     "概念阶段须有 FAR/DENSITY/GREEN/PARK 四项",
                     "补齐缺失项后再体量：指标不成立的形式不是方案")]
    return [make("PLAN-METRIC-01", "PASS",
                 "四项齐备（来源 " + source + "）",
                 "概念阶段须有 FAR/DENSITY/GREEN/PARK 四项",
                 "进入方案阶段每轮复算一次，保留 3%–5% 余量")]


def rule_far(ctx):
    rule = "PLAN-FAR-01"
    metrics = ctx["metrics"]
    if not metrics:
        return [make(rule, "UNVERIFIED", "core 未提供 metrics",
                     "FAR ≤ 上限，且余量 ≥3%", "先完成 scripts/core/design_io.py")]
    far, limit = metrics.get("far"), metrics.get("far_limit")
    if far is None or limit is None:
        return [unverified(rule, "metrics.far / metrics.far_limit", "FAR 与上限双字段")]
    if limit <= 0:
        return [make(rule, "UNVERIFIED", "上限 " + str(limit), "上限须为正数", "核对控规给定的 FAR 上限")]
    ratio = far / limit
    actual = "FAR " + ("%.3f" % far) + "，占上限 " + ("%.1f" % (ratio * 100)) + "%"
    limit_text = "≤ " + ("%.3f" % limit) + "（余量 ≥3%）"
    if far > limit + 1e-9:
        return [make(rule, "FAIL", actual, limit_text,
                     "砍 " + ("%.2f" % (far - limit)) + " 计容面积或调整体量：超容不可报建")]
    if ratio > 0.97:
        return [make(rule, "WARN", actual, limit_text,
                     "余量不足 3%，面积计算口径变动即超容；预留 3%–5%")]
    if ratio < 0.80:
        return [make(rule, "WARN", actual, limit_text,
                     "未用足 80% 限值，确认让量是主动决策并写入决策记录")]
    return [make(rule, "PASS", actual, limit_text, "保持余量，面积口径变更时复算")]


def rule_density(ctx):
    rule = "PLAN-DENSITY-01"
    metrics = ctx["metrics"]
    if not metrics:
        return [make(rule, "UNVERIFIED", "core 未提供 metrics",
                     "密度 ≤ 上限，且余量 ≥3%", "先完成 scripts/core/design_io.py")]
    value, limit = metrics.get("density"), metrics.get("density_limit")
    if value is None or limit is None:
        return [unverified(rule, "metrics.density / metrics.density_limit", "密度与上限双字段")]
    ratio = value / limit if limit else 0
    actual = "密度 " + ("%.2f" % value) + "%，占上限 " + ("%.1f" % (ratio * 100)) + "%"
    limit_text = "≤ " + ("%.2f" % limit) + "%（余量 ≥3%）"
    if value > limit + 1e-9:
        return [make(rule, "FAIL", actual, limit_text,
                     "减小基底或拆分体量；密度超标即总图重做")]
    if ratio > 0.97:
        return [make(rule, "WARN", actual, limit_text,
                     "余量不足 3%；出入口、门卫、连廊的计入规则一变就超")]
    return [make(rule, "PASS", actual, limit_text, "保持余量，明确有顶盖/连廊的计入口径")]


def rule_green(ctx):
    rule = "PLAN-GREEN-01"
    metrics = ctx["metrics"]
    if not metrics:
        return [make(rule, "UNVERIFIED", "core 未提供 metrics",
                     "绿地率 ≥ 下限 +3%", "先完成 scripts/core/design_io.py")]
    value, low = metrics.get("green"), metrics.get("green_min")
    if value is None or low is None:
        return [unverified(rule, "metrics.green / metrics.green_min", "绿地率与下限双字段")]
    actual = "绿地率 " + ("%.2f" % value) + "%"
    limit_text = "≥ " + ("%.2f" % low) + "%（含 +3% 余量目标）"
    if value < low - 1e-9:
        return [make(rule, "FAIL", actual, limit_text,
                     "补绿地或调整硬质范围；水面与屋顶绿地计入口径按地方规则")]
    if value < low * 1.03:
        return [make(rule, "WARN", actual, limit_text,
                     "仅压线通过；绿地边界随管线与消防车道调整即不达标")]
    return [make(rule, "PASS", actual, limit_text, "保留计算书，分列地面/覆土/屋顶三项口径")]


def rule_parking(ctx):
    rule = "PLAN-PARK-01"
    metrics = ctx["metrics"]
    if not metrics:
        return [make(rule, "UNVERIFIED", "core 未提供 metrics",
                     "配建车位 ≥ 要求值", "先完成 scripts/core/design_io.py")]
    value, need = metrics.get("parking"), metrics.get("parking_required")
    if value is None or need is None:
        return [unverified(rule, "metrics.parking / metrics.parking_required",
                           "车位数与配建要求双字段")]
    actual = "车位 " + ("%d" % round(value)) + " 个"
    limit_text = "≥ " + ("%d" % round(need)) + " 个"
    if value < need - 0.5:
        return [make(rule, "FAIL", actual, limit_text,
                     "补 " + str(int(round(need - value))) + " 个车位；优先核柱网是否能排整排车位")]
    if value > need * 1.2:
        return [make(rule, "WARN", actual, limit_text,
                     "超配 20% 以上，多为柱网与车位排布不匹配导致的浪费")]
    return [make(rule, "PASS", actual, limit_text, "补齐充电桩比例、无障碍与访客车位清单")]


def _constraint_items(design):
    site = design.get("site")
    if isinstance(site, dict):
        for key in ("constraints", "restrictions", "限制条件"):
            if isinstance(site.get(key), list):
                return site[key]
    for key in ("constraints", "site_constraints", "限制条件"):
        if isinstance(design.get(key), list):
            return design[key]
    return None


def rule_constraints(ctx):
    rule = "PLAN-SITE-01"
    items = _constraint_items(ctx["design"])
    if items is None:
        return [make(rule, "UNVERIFIED", "缺 site.constraints", "限制条件须清单化并逐项处置",
                     "把红线/退线/限高/蓝线/文保/古树/管线走廊逐条写进 site.constraints")]
    if not items:
        return [make(rule, "FAIL", "site.constraints 为空", "每条限制须有对应处置",
                     "先出限制条件图：图上每条限制都要有处置动作")]
    pending = []
    for item in items:
        if not isinstance(item, dict):
            pending.append(str(item))
            continue
        name = str(item.get("type") or item.get("name") or item.get("id") or "未命名")
        handled = item.get("handled")
        if handled is None:
            handled = item.get("status")
        if handled is None:
            handled = bool(item.get("response") or item.get("measure") or item.get("处置"))
        if isinstance(handled, str):
            done = handled.strip().lower() not in ("", "none", "no", "false", "todo", "未处置", "待定")
        else:
            done = bool(handled)
        if not done:
            pending.append(name)
    if pending:
        return [make(rule, "FAIL", str(len(pending)) + " 条未处置：" + "、".join(pending[:5]),
                     "每条限制须有对应处置", "逐条补处置动作与图纸印证，再进方案")]
    if len(items) < 5:
        return [make(rule, "WARN", "已列 " + str(len(items)) + " 条，全部有处置",
                     "限制条件通常不少于 5 条", "对照 workflow.md 概念输入清单逐项打勾，防漏项")]
    return [make(rule, "PASS", "已列 " + str(len(items)) + " 条，全部有处置",
                 "每条限制须有对应处置", "每阶段更新一次清单")]


def _decisions(design):
    block = design.get("decisions")
    if isinstance(block, dict):
        return block
    for key in ("decision_log", "决策记录"):
        if isinstance(design.get(key), dict):
            return design[key]
        if isinstance(design.get(key), list):
            return {"items": design[key]}
    return None


def rule_decisions(ctx):
    rule = "PLAN-PROG-01"
    block = _decisions(ctx["design"])
    if block is None:
        return [make(rule, "UNVERIFIED", "缺 decisions 块", "已定/未定/假设三栏须随阶段更新",
                     "补 decisions.decided / pending / assumed 三栏")]
    assumed = block.get("assumed")
    if isinstance(assumed, dict):
        assumed = [assumed]
    if assumed is None:
        assumed = []
    if not isinstance(assumed, list):
        assumed = [assumed]
    unconfirmed = []
    for item in assumed:
        if isinstance(item, dict):
            if not bool(item.get("confirmed")):
                unconfirmed.append(str(item.get("question") or item.get("id") or "假设项"))
        else:
            unconfirmed.append(str(item))
    pending = block.get("pending")
    if isinstance(pending, dict):
        pending = [pending]
    if pending is None:
        pending = []
    if not isinstance(pending, list):
        pending = [pending]
    if unconfirmed:
        return [make(rule, "FAIL", str(len(unconfirmed)) + " 条假设未书面确认：" + "、".join(unconfirmed[:5]),
                     "假设项必须书面确认", "把假设项发给业主确认，或改为未定项并停止依赖它的设计")]
    if pending:
        return [make(rule, "WARN", "未定 " + str(len(pending)) + " 条，假设均已确认",
                     "未定项应在阶段汇报前列出", "阶段汇报会上逐条拍板，未拍板不得进入下一阶段")]
    return [make(rule, "PASS", "无未定项，假设项均已确认", "三栏随阶段更新", "保持登记节奏")]


LAYER_CATEGORIES = ("结构构造", "空间组织", "场地关系", "材料细节", "运营时间")


def rule_cases(ctx):
    rule = "PLAN-CASE-01"
    refs = ctx["design"].get("references")
    if refs is None:
        refs = ctx["design"].get("precedents")
    if refs is None:
        return [make(rule, "UNVERIFIED", "缺 references", "先例 ≤3 个且写明借哪一层、改写了什么",
                     "补 references: [{case_id, layer, why, rewritten}]")]
    if not isinstance(refs, list) or not refs:
        return [make(rule, "FAIL", "references 为空", "至少 1 个先例须写明借哪一层",
                     "按 cases.md 的引用格式补全")]
    if len(refs) > 3:
        return [make(rule, "FAIL", str(len(refs)) + " 个先例，超过 3 个上限",
                     "≤3 个且来自不同类目", "保留最贴合的 3 个，其余移入备选")]
    bad = []
    layers = []
    for item in refs:
        if not isinstance(item, dict):
            bad.append(str(item))
            continue
        cid = str(item.get("case_id") or item.get("id") or "?")
        layer = str(item.get("layer") or item.get("borrowed_layer") or "").strip()
        rewritten = str(item.get("rewritten") or item.get("adaptation") or "").strip()
        if layer not in LAYER_CATEGORIES:
            bad.append(cid + "(借哪一层缺失或不合法)")
        else:
            layers.append(layer)
        if not rewritten:
            bad.append(cid + "(缺改写了什么)")
    if bad:
        return [make(rule, "FAIL", "、".join(bad[:5]),
                     "每条须有 layer∈" + "/".join(LAYER_CATEGORIES) + " 且 written 改写说明",
                     "补 layer 与 rewritten 两项；缺改写说明视为抄袭风险")]
    if len(set(layers)) < len(layers):
        return [make(rule, "WARN", "借的层重复：" + "/".join(layers),
                     "3 个先例宜分属不同类目", "换 1 个不同类目的先例，避免单一谱系")]
    return [make(rule, "PASS", "、".join(layers), "≤3 个且来自不同类目", "汇报时逐条说明改了哪一处")]


def _sheets(design):
    for key in ("sheets", "drawings", "图纸"):
        if isinstance(design.get(key), list):
            return design[key]
    return []


def _scale_text(value):
    text = str(value).strip().lower().replace(" ", "")
    return text.replace("1-", "1:").replace("1：", "1:")


def rule_interface_map(ctx):
    rule = "PLAN-XLAY-01"
    sheets = _sheets(ctx["design"])
    iface = ctx["design"].get("interface_map")
    if not isinstance(iface, dict):
        iface = {}
    found = None
    for sheet in sheets:
        if not isinstance(sheet, dict):
            continue
        text = " ".join(str(sheet.get(k) or "") for k in ("code", "title", "name", "id"))
        scale = _scale_text(sheet.get("scale") or sheet.get("ratio") or "")
        if ("接口" in text or "交汇" in text) and scale in ("1:500", "500", "1:500(局部)"):
            found = text.strip() or "接口图"
            break
    if found is None and _scale_text(iface.get("scale") or "") in ("1:500", "500"):
        found = str(iface.get("title") or "interface_map")
    if found is None:
        return [make(rule, "FAIL", "sheets 中没有 1:500 的接口图/交汇图",
                     "总图阶段须出 1 张 1:500 建筑-景观接口图",
                     "补 X-10 接口图：建筑轮廓+地下室轮廓+覆土分区+乔木骨架+车道+管线走廊+出入口")]
    overlays = iface.get("overlays") or ctx["design"].get("overlays")
    if not isinstance(overlays, list):
        return [make(rule, "WARN", "接口图存在（" + found + "），但未声明叠合底图",
                     "须叠合 乔木定位图 × 综合管线图 × 地下室顶板图",
                     "在 interface_map.overlays 中登记三张底图")]
    joined = " ".join(str(x) for x in overlays)
    need = (("乔木", "tree", "plant"), ("管线", "pipe", "utility"), ("顶板", "roof", "basement", "地下室"))
    absent = [group[0] for group in need if not any(token in joined.lower() for token in group)]
    if absent:
        return [make(rule, "WARN", "接口图存在，叠合缺：" + "、".join(absent),
                     "乔木定位图 × 综合管线图 × 地下室顶板图，三张都要叠",
                     "补叠缺失底图；树压管、树压顶板都在这一步暴露")]
    return [make(rule, "PASS", "1:500 接口图 + 三底图已叠合", "三张底图必须叠合",
                 "接口图作为两专业唯一仲裁底图，改动即重跑本规则")]


def _drainage(design):
    for key in ("drainage", "drainage_paths", "排水路径", "stormwater"):
        if isinstance(design.get(key), list):
            return design[key]
    return None


def rule_drainage(ctx):
    rule = "PLAN-DRAIN-01"
    paths = _drainage(ctx["design"])
    if paths is None:
        return [make(rule, "UNVERIFIED", "缺 drainage 路径表", "每条雨水路径须闭合到雨水口或市政接口",
                     "补 drainage: [{id, slopes_pct, outfall, reverse_slope}]")]
    if not paths:
        return [make(rule, "FAIL", "drainage 为空", "场地须有明确排水路径",
                     "在 1:500 总图上画坡度箭头直到出口")]
    reverse = []
    no_out = []
    for item in paths:
        if not isinstance(item, dict):
            no_out.append(str(item))
            continue
        pid = str(item.get("id") or "?")
        if bool(item.get("reverse_slope")):
            reverse.append(pid)
        slopes = item.get("slopes_pct")
        if isinstance(slopes, list):
            if any((as_number(s) is not None and as_number(s) < 0) for s in slopes):
                reverse.append(pid)
        outfall = item.get("outfall")
        if outfall is None:
            outfall = item.get("outlet") or item.get("出口")
        text = str(outfall).strip().lower() if outfall is not None else ""
        if not text or text in ("none", "no", "无", "null", "待定"):
            no_out.append(pid)
    if reverse:
        return [make(rule, "FAIL", "反坡路径：" + "、".join(reverse[:5]) + "（反坡即否决）",
                     "任一点沿坡向必须能走到出口，不得反坡",
                     "改标高或加雨水口，把反坡段消除后再出图")]
    if no_out:
        return [make(rule, "FAIL", str(len(no_out)) + " 条路径无出口：" + "、".join(no_out[:5]),
                     "每条路径须闭合到雨水口或市政接口",
                     "补雨水口或接市政接口，箭头必须闭合")]
    return [make(rule, "PASS", str(len(paths)) + " 条路径全部闭合且无反坡", "每条路径须闭合到出口",
                 "与景观方互签后冻结标高，任何 ±0.00 改动重跑")]


PUBLIC_TOKENS = ("public", "公共", "门厅", "中庭", "楼梯", "架空", "下沉", "庭院", "大厅", "大堂")


def _is_public(space):
    if not isinstance(space, dict):
        return True
    for key in ("public", "is_public", "公共"):
        if key in space:
            return bool(space.get(key))
    text = str(space.get("type") or space.get("kind") or space.get("name") or "")
    return any(token in text.lower() for token in PUBLIC_TOKENS)


def rule_sections(ctx):
    rule = "PLAN-SECT-01"
    spaces = ctx["design"].get("spaces")
    sections = ctx["design"].get("sections")
    if not isinstance(spaces, list) or not spaces:
        return [make(rule, "UNVERIFIED", "缺 spaces", "主要公共空间须至少被一条剖面穿过",
                     "补 spaces: [{id, name, public}]")]
    if not isinstance(sections, list) or not sections:
        return [make(rule, "UNVERIFIED", "缺 sections", "主要公共空间须至少被一条剖面穿过",
                     "补 sections: [{id, cuts:[space_id]}]")]
    public = [s for s in spaces if isinstance(s, dict) and _is_public(s)]
    if not public:
        public = [s for s in spaces if isinstance(s, dict)]
    covered = set()
    for section in sections:
        if not isinstance(section, dict):
            continue
        cuts = section.get("cuts") or section.get("spaces") or section.get("穿过空间")
        if isinstance(cuts, list):
            for cid in cuts:
                covered.add(str(cid))
    missing = [str(s.get("id") or s.get("name")) for s in public if str(s.get("id") or s.get("name")) not in covered]
    if missing:
        return [make(rule, "FAIL", "未被剖面穿过：" + "、".join(missing[:5]),
                     "每个主要公共空间至少 1 条剖面",
                     "补 1 条穿过该空间的剖面，并在剖面上标层高、梁高、净高与采光口")]
    return [make(rule, "PASS", str(len(public)) + " 个公共空间全部有剖面",
                 "每个主要公共空间至少 1 条剖面", "剖面上必须能看到层高与梁高的真实关系")]


def rule_fire(ctx):
    rule = "PLAN-FIRE-01"
    fire = ctx["design"].get("fire")
    if not isinstance(fire, dict):
        return [make(rule, "UNVERIFIED", "缺 fire 块", "消防车道闭合、登高面无遮挡",
                     "补 fire: {loop_closed, turnaround, high_side_clear, lane_width_m, slope_pct}")]
    loop = bool(fire.get("loop_closed"))
    turn = fire.get("turnaround")
    has_turn = bool(turn) if turn is not None else False
    if not loop and not has_turn:
        return [make(rule, "FAIL", "车道不闭合且无回车场", "环形车道或尽头式车道设回车场",
                     "补环形车道或回车场，并在 1:500 总图上量转弯半径")]
    if fire.get("high_side_clear") is False:
        return [make(rule, "FAIL", "登高面一侧有遮挡", "登高面一侧不得有雨棚、乔木、架空层",
                     "移走登高面一侧的构筑物与乔木；隐形车道不得种树")]
    width = as_number(fire.get("lane_width_m"))
    slope = as_number(fire.get("slope_pct"))
    warns = []
    if width is not None and width < 4.0:
        warns.append("车道净宽 " + ("%.1f" % width) + "m < 4m（工程惯例建议值）")
    if slope is not None and slope > 3.0:
        warns.append("登高面坡度 " + ("%.1f" % slope) + "% > 3%（工程惯例建议值）")
    if warns:
        return [make(rule, "WARN", "；".join(warns), "净宽 ≥4m、登高面坡度 ≤3%（以现行规范核对）",
                     "按当地消防审查口径复核后调整；数值为惯例值不是规范值")]
    return [make(rule, "PASS", ("闭合" if loop else "回车场") + "，登高面无遮挡", "车道闭合且登高面净空",
                 "与景观铺装做法互签：隐形车道承载与转弯半径")]


def rule_access(ctx):
    rule = "PLAN-ACCESS-01"
    entries = ctx["design"].get("entrances")
    if not isinstance(entries, list) or not entries:
        return [make(rule, "UNVERIFIED", "缺 entrances", "每个出入口须有连续无障碍路径",
                     "补 entrances: [{id, accessible, step_mm}]")]
    bad = []
    warns = []
    for item in entries:
        if not isinstance(item, dict):
            bad.append(str(item))
            continue
        eid = str(item.get("id") or item.get("name") or "?")
        if item.get("accessible") is False:
            bad.append(eid + "(无无障碍路径)")
            continue
        step = as_number(item.get("step_mm") if item.get("step_mm") is not None else item.get("step"))
        has_ramp = bool(item.get("ramp_slope") or item.get("ramp"))
        if step is not None and step > 15 and not has_ramp:
            bad.append(eid + "(高差 " + ("%.0f" % step) + "mm 未坡化)")
        elif step is not None and 15 < step <= 30:
            warns.append(eid + "(高差 " + ("%.0f" % step) + "mm，宜做过渡坡)")
    if bad:
        return [make(rule, "FAIL", "、".join(bad[:5]), "每个出入口须有连续无障碍路径（坡度≤1:12）",
                     "补坡道或改标高；门口加台阶是最常见返工")]
    if warns:
        return [make(rule, "WARN", "、".join(warns[:5]), "高差 >15mm 须坡化（工程惯例建议值）",
                     "把 15–30mm 的高差做成 1:12 过渡坡，或降室内外高差")]
    return [make(rule, "PASS", str(len(entries)) + " 个出入口无障碍路径连续", "每个出入口须连续可达",
                 "盲道与坡道不得被车位或花池占用")]


DEFAULT_SOIL_MM = {"大乔木": 1500, "小乔木": 600, "灌木": 450, "地被": 300}


def rule_trees(ctx):
    rule = "PLAN-TREE-01"
    trees = ctx["design"].get("trees")
    if not isinstance(trees, list) or not trees:
        return [make(rule, "UNVERIFIED", "缺 trees", "乔木等级须与覆土厚度匹配",
                     "补 trees: [{id, class, soil_depth_mm, on_roof}]")]
    site = ctx["design"].get("site") if isinstance(ctx["design"].get("site"), dict) else {}
    table = site.get("soil_min_mm") if isinstance(site.get("soil_min_mm"), dict) else {}
    bad = []
    warns = []
    for item in trees:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("id") or item.get("name") or "?")
        cls = str(item.get("class") or item.get("size") or item.get("等级") or "")
        on_roof = bool(item.get("on_roof") or item.get("roof"))
        depth = as_number(item.get("soil_depth_mm") if item.get("soil_depth_mm") is not None else item.get("soil_depth"))
        need = as_number(table.get(cls)) if table else None
        if need is None:
            for key, value in DEFAULT_SOIL_MM.items():
                if key in cls:
                    need = float(value)
                    break
        if need is None:
            warns.append(tid + "(未识别乔木等级)")
            continue
        if depth is None:
            warns.append(tid + "(未填覆土厚度)")
            continue
        if depth < need - 1:
            flag = "顶板上" if on_roof else "地面上"
            bad.append(tid + "(" + cls + " " + flag + " 覆土 " + ("%.0f" % depth) + "mm < " + ("%.0f" % need) + "mm)")
    if bad:
        return [make(rule, "FAIL", "、".join(bad[:5]),
                     "覆土厚度须满足该等级乔木最小要求（默认大乔木 1500mm / 小乔木 600mm，待核：按地方标准）",
                     "降乔木等级、加厚覆土或把大树移出顶板范围")]
    if warns:
        return [make(rule, "WARN", "、".join(warns[:5]), "每株须有等级与覆土厚度",
                     "补等级与覆土厚度；顶板上种大树是最常见不可实施项")]
    return [make(rule, "PASS", str(len(trees)) + " 株乔木覆土均达标", "覆土厚度须满足该等级乔木要求",
                 "叠管线图核：树压管同样否决")]


def rule_basement(ctx):
    rule = "PLAN-BASE-01"
    base = ctx["design"].get("basement")
    if not isinstance(base, dict):
        return [make(rule, "UNVERIFIED", "缺 basement 块", "地下室轮廓不得越红线与退线",
                     "补 basement: {outline_inside_redline, min_offset_mm, required_offset_mm}")]
    if base.get("outline_inside_redline") is False:
        return [make(rule, "FAIL", "地下室轮廓越红线/退线", "轮廓须在红线与退线内",
                     "退地下室边线；外墙退线常被忽略，须单独标注")]
    offset = as_number(base.get("min_offset_mm"))
    need = as_number(base.get("required_offset_mm"))
    if offset is not None and need is not None and offset < need - 1:
        return [make(rule, "FAIL", "最小退距 " + ("%.0f" % offset) + "mm < 要求 " + ("%.0f" % need) + "mm",
                     "地下室外墙退距须满足退线要求",
                     "内退地下室或调整挡土做法；退距不足影响放坡与管线")]
    return [make(rule, "PASS", "轮廓在红线与退线内" + ("，最小退距 " + ("%.0f" % offset) + "mm" if offset is not None else ""),
                 "轮廓须在红线与退线内", "与景观覆土分区一并出图")]


def _facade(design):
    facade = design.get("facade")
    return facade if isinstance(facade, dict) else {}


def _window_widths(design):
    facade = _facade(design)
    for key in ("windows_mm", "window_widths_mm", "openings_mm", "窗宽"):
        widths = facade.get(key)
        if isinstance(widths, list):
            values = [as_number(w) for w in widths]
            return [v for v in values if v is not None]
    return None


def rule_window_module(ctx):
    rule = "DET-WIN-01"
    widths = _window_widths(ctx["design"])
    if widths is None:
        return [make(rule, "UNVERIFIED", "缺 facade.windows_mm", "同一立面任取 3 个窗宽须呈整数倍",
                     "补 facade.windows_mm 列表")]
    if not widths:
        return [make(rule, "FAIL", "窗宽列表为空", "同一立面任取 3 个窗宽须呈整数倍",
                     "把立面窗宽按 600/900/1200/1500 系列重排")]
    distinct = sorted(set(round(w, 3) for w in widths))
    module = as_number(_facade(ctx["design"]).get("module_mm")) or 300.0
    offenders = [w for w in distinct if abs(w % module) > 0.5 and abs(w % module - module) > 0.5]
    if offenders:
        return [make(rule, "FAIL", "不落在模数 " + ("%.0f" % module) + "mm 上：" + "、".join("%.0f" % w for w in offenders[:5]),
                     "窗宽须为模数整数倍（工程惯例建议值 300/600mm）",
                     "按模数重排窗宽；立面改模数等于全图重画")]
    if len(distinct) < 3:
        return [make(rule, "WARN", "窗宽仅 " + str(len(distinct)) + " 种，无法做整数倍抽检",
                     "同一立面任取 3 个窗宽须呈整数倍",
                     "补足窗宽系列，或声明单一窗宽并说明理由")]
    bad_triples = []
    size = len(distinct)
    for i in range(size):
        for j in range(i + 1, size):
            for k in range(j + 1, size):
                triple = (distinct[i], distinct[j], distinct[k])
                base = triple[0]
                if base <= 0:
                    continue
                if abs(triple[1] / base - round(triple[1] / base)) > 1e-6 or abs(triple[2] / base - round(triple[2] / base)) > 1e-6:
                    bad_triples.append("/".join("%.0f" % w for w in triple))
    if bad_triples:
        return [make(rule, "FAIL", "非整数倍组合：" + "；".join(bad_triples[:3]),
                     "任取 3 个窗宽，最小者须是其余两者的整数分之一",
                     "收敛窗宽系列到 3 种以内，且互为整数倍")]
    return [make(rule, "PASS", "窗宽 " + "、".join("%.0f" % w for w in distinct) + " 互为整数倍，模数 " + ("%.0f" % module) + "mm",
                 "任取 3 个窗宽须呈整数倍", "与平面窗洞、结构梁位再对一次")]


def rule_window_consistency(ctx):
    rule = "DET-WIN-02"
    design = ctx["design"]
    facade = _facade(design)
    widths = _window_widths(design)
    plan = design.get("openings") if isinstance(design.get("openings"), list) else None
    if widths is None and plan is None:
        return [make(rule, "UNVERIFIED", "缺 facade.windows_mm 与 openings", "立面窗洞须与平面窗洞一致",
                     "补 facade.windows_mm 与 openings 两处窗宽")]
    problems = []
    if widths and plan:
        plan_widths = []
        for item in plan:
            if isinstance(item, dict):
                value = as_number(item.get("width_mm") if item.get("width_mm") is not None else item.get("width"))
                if value is not None:
                    plan_widths.append(round(value, 3))
            else:
                value = as_number(item)
                if value is not None:
                    plan_widths.append(round(value, 3))
        if plan_widths:
            only_facade = sorted(set(round(w, 3) for w in widths) - set(plan_widths))
            only_plan = sorted(set(plan_widths) - set(round(w, 3) for w in widths))
            if only_facade:
                problems.append("仅立面有：" + "、".join("%.0f" % w for w in only_facade[:5]))
            if only_plan:
                problems.append("仅平面有：" + "、".join("%.0f" % w for w in only_plan[:5]))
    aligned = facade.get("beam_aligned")
    if aligned is False:
        problems.append("幕墙分格未对结构梁位")
    if problems:
        return [make(rule, "FAIL", "；".join(problems), "立面/平面/梁位三者一致",
                     "以平面窗洞为准改立面，或同步改两侧；两套逻辑必然在门窗表爆炸")]
    if aligned is None:
        return [make(rule, "WARN", "窗洞已对齐，但未声明与结构梁位的对应",
                     "须声明 beam_aligned", "核对梁位后把 beam_aligned 置 true")]
    if not plan:
        return [make(rule, "WARN", "只有立面窗宽，未提供平面窗洞 openings",
                     "立面与平面须能逐一对位", "补 openings 后重跑")]
    return [make(rule, "PASS", "立面窗口与平面窗洞一致，且已对梁位", "立面/平面/梁位三者一致",
                 "出图前再核一次开启扇与通风面积")]


NODE_KEYS = ("node_ref", "node", "joint_mm", "seam_mm", "exposed")


def _node_items(design):
    """Collect node-level material entries from materials (list or dict) and node_materials."""
    out = []

    def take(seq):
        for item in seq:
            if isinstance(item, dict):
                out.append(item)

    def take_map(mapping):
        for name, val in mapping.items():
            if not isinstance(val, dict):
                continue
            if not any(k in val for k in NODE_KEYS):
                continue
            item = dict(val)
            if not item.get("name"):
                item["name"] = name
            out.append(item)

    mats = design.get("materials")
    if isinstance(mats, list):
        take(mats)
    elif isinstance(mats, dict):
        take_map(mats)
    nodes = design.get("node_materials")
    if isinstance(nodes, list):
        take(nodes)
    elif isinstance(nodes, dict):
        take_map(nodes)
    return out


def rule_node_materials(ctx):
    rule = "DET-NODE-01"
    items = _node_items(ctx["design"])
    if not items:
        return [make(rule, "UNVERIFIED",
                     "materials 只声明颜色/粗糙度，全库无任何节点字段（node_ref / joint_mm）",
                     "每个外露材料须有节点做法与缝宽",
                     "补 node_materials: [{name, node_ref, joint_mm, exposed}]，"
                     "node_ref 指向真实节点详图编号；无节点详图时本条无法判定")]
    bad = []
    warns = []
    for item in items:
        name = str(item.get("name") or item.get("材料") or "?")
        exposed = item.get("exposed")
        if exposed is False:
            continue
        if not str(item.get("node_ref") or item.get("node") or "").strip():
            bad.append(name + "(无节点)")
            continue
        joint = as_number(item.get("joint_mm") if item.get("joint_mm") is not None else item.get("seam_mm"))
        if joint is not None and joint < 8:
            warns.append(name + "(缝宽 " + ("%.0f" % joint) + "mm < 8mm)")
    if bad:
        return [make(rule, "FAIL", "、".join(bad[:5]), "每个外露材料须在节点图上有厚度、连接与收口",
                     "补节点详图引用；未落在图上的材料等于没定")]
    if warns:
        return [make(rule, "WARN", "、".join(warns[:5]), "缝宽 ≥8mm 且规律（工程惯例建议值）",
                     "放大缝宽到 8mm 以上或改干挂做法；窄缝无法施工")]
    return [make(rule, "PASS", str(len(items)) + " 种材料均有节点引用", "每个外露材料须有节点做法",
                 "节点图比例 1:20/1:10，收边宽度须反映板厚")]


DEFAULT_CLEARANCE_MM = {"车库": 2200, "走廊": 2400, "走道": 2400, "设备房": 2500, "设备": 2500}


def rule_clearance(ctx):
    rule = "DET-CLEAR-01"
    items = ctx["design"].get("clearances")
    if not isinstance(items, list) or not items:
        return [make(rule, "UNVERIFIED", "缺 clearances", "关键空间净高须与下限比对",
                     "补 clearances: [{name, clear_mm, min_mm}]")]
    limits = ctx["design"].get("clearance_limits")
    limits = limits if isinstance(limits, dict) else {}
    bad = []
    warns = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("space") or "?")
        clear = as_number(item.get("clear_mm") if item.get("clear_mm") is not None else item.get("clear"))
        need = as_number(item.get("min_mm") if item.get("min_mm") is not None else item.get("min"))
        if need is None:
            need = as_number(limits.get(name))
        if need is None:
            for token, value in DEFAULT_CLEARANCE_MM.items():
                if token in name:
                    need = float(value)
                    break
        if clear is None:
            warns.append(name + "(未填净高)")
            continue
        if need is None:
            warns.append(name + "(无下限值)")
            continue
        if clear < need - 1:
            bad.append(name + " " + ("%.0f" % clear) + "mm < " + ("%.0f" % need) + "mm")
        elif clear - need < 100:
            warns.append(name + " 余量仅 " + ("%.0f" % (clear - need)) + "mm")
    if bad:
        return [make(rule, "FAIL", "、".join(bad[:5]),
                     "净高须 ≥ 下限（车库 2200 / 走廊 2400mm，工程惯例建议值）",
                     "加高该空间或挪管线；做完只剩 2.1m 的走廊无法过验收")]
    if warns:
        return [make(rule, "WARN", "、".join(warns[:5]), "净高余量宜 ≥100mm",
                     "做管线综合剖面复核，余量不足 100mm 时留检修空间")]
    return [make(rule, "PASS", str(len(items)) + " 个空间净高均达标", "净高须 ≥ 下限且留余量",
                 "附走廊与车库的净高剖面，标梁高与管线底")]


def rule_module(ctx):
    rule = "AEST-MOD-01"
    design = ctx["design"]
    dims = design.get("dims_mm") if isinstance(design.get("dims_mm"), list) else None
    if dims is None:
        for key in ("principal_dims_mm", "主尺寸"):
            if isinstance(design.get(key), list):
                dims = design[key]
    module = as_number(design.get("module_mm")) or as_number(_facade(design).get("module_mm"))
    if dims is None:
        return [make(rule, "UNVERIFIED", "缺 dims_mm", "抽 10 个主要尺寸，≥8 个能被基准模数整除",
                     "补 dims_mm（主要开间/进深/层高/分格）")]
    if module is None:
        return [make(rule, "UNVERIFIED", "缺 module_mm", "须声明项目基准模数",
                     "补 module_mm（工程惯例建议值：建筑 300mm，铺装 300 或 600mm）")]
    values = [as_number(d) for d in dims]
    values = [v for v in values if v is not None and v > 0]
    if not values:
        return [make(rule, "FAIL", "dims_mm 无可解析数值", "须能被基准模数整除",
                     "填真实尺寸，不要写区间")]
    hit = sum(1 for v in values if abs(v % module) <= 0.5 or abs(v % module - module) <= 0.5)
    ratio = hit / float(len(values))
    actual = str(hit) + "/" + str(len(values)) + " 落在 " + ("%.0f" % module) + "mm 模数上（" + ("%.0f" % (ratio * 100)) + "%）"
    limit_text = "≥80% 整除（工程惯例建议值）"
    if ratio < 0.7:
        return [make(rule, "FAIL", actual, limit_text,
                     "统一模数：柱网/开间/分格三套模数是模数漂移的典型症状")]
    if ratio < 0.8:
        return [make(rule, "WARN", actual, limit_text, "把剩余尺寸向模数取整，或声明例外并给理由")]
    if len(values) < 10:
        return [make(rule, "WARN", actual + "，样本不足 10 个", limit_text + "，样本 ≥10 个",
                     "补主尺寸样本后重算；样本太少结论不可靠")]
    return [make(rule, "PASS", actual, limit_text, "保持一套基准模数贯穿建筑与铺装")]


def rule_material_share(ctx):
    rule = "AEST-MAT-01"
    facade = _facade(ctx["design"])
    items = facade.get("materials") if isinstance(facade.get("materials"), list) else None
    if items is None:
        items = ctx["design"].get("facade_materials") if isinstance(ctx["design"].get("facade_materials"), list) else None
    if items is None:
        return [make(rule, "UNVERIFIED", "缺 facade.materials", "立面色块主材 ≤3 种，前三种占比 ≥85%",
                     "补 facade.materials: [{name, share}]")]
    entries = []
    for item in items:
        if isinstance(item, dict):
            name = str(item.get("name") or "?")
            share = as_number(item.get("share") if item.get("share") is not None else item.get("ratio"))
            entries.append((name, share))
        else:
            entries.append((str(item), None))
    exposed = [e for e in entries if not any(token in e[0] for token in ("玻璃", "金属收边", "收边", "glass"))]
    total_known = sum(share for _, share in entries if share is not None)
    if total_known > 1.5:
        entries = [(name, None if share is None else share / 100.0) for name, share in entries]
    if len(exposed) > 4:
        return [make(rule, "FAIL", str(len(exposed)) + " 种主材（不含玻璃与收边）", "主材 ≤3 种",
                     "砍到 3 种以内；没有主次的材料堆叠在施工时会被换成最便宜的一种")]
    top3 = sorted([e for e in entries if e[1] is not None], key=lambda x: -x[1])[:3]
    top_share = sum(share for _, share in top3)
    actual = str(len(exposed)) + " 种主材"
    if top3:
        actual += "，前 3 占比 " + ("%.0f" % (top_share * 100)) + "%"
    if len(exposed) == 4:
        return [make(rule, "WARN", actual, "主材 ≤3 种", "合并 1 种材料或把其中一种降为收边")]
    if top3 and top_share < 0.85:
        return [make(rule, "WARN", actual, "前 3 种占比 ≥85%（工程惯例建议值）",
                     "拉开面积差：主材要明显大于辅材，否则等于没有主次")]
    return [make(rule, "PASS", actual, "主材 ≤3 种且前 3 占比 ≥85%", "把材质分区与板缝一起定，不要先定颜色")]


def _light_checks(design):
    block = design.get("light_checks")
    if isinstance(block, list):
        return block
    for key in ("light_study", "光影校核"):
        if isinstance(design.get(key), list):
            return design[key]
    return None


def rule_light(ctx):
    rule = "AEST-LIGHT-01"
    checks = _light_checks(ctx["design"])
    if checks is None:
        return [make(rule, "UNVERIFIED", "缺 light_checks", "须做 3 日期 × 3 时刻共 9 组校核",
                     "补 light_checks（date/time/sun_altitude_deg）")]
    combos = set()
    low = 0
    for item in checks:
        if not isinstance(item, dict):
            continue
        date = str(item.get("date") or item.get("节气") or "").strip()
        clock = str(item.get("time") or item.get("时刻") or "").strip()
        alt = as_number(item.get("sun_altitude_deg") if item.get("sun_altitude_deg") is not None else item.get("altitude"))
        if alt is not None and alt < 35:
            low += 1
        if date and clock:
            combos.add((date, clock))
    actual = str(len(combos)) + "/9 组"
    if len(combos) < 9:
        return [make(rule, "FAIL", actual + "（缺一即未校核）",
                     "冬至/夏至/春秋分 × 9:00/12:00/15:00 共 9 组",
                     "补齐 9 组；只有正午效果图不算校核")]
    if low == 0:
        return [make(rule, "WARN", actual + "，但无低太阳高度角记录", "至少 1 组太阳高度角 <35°",
                     "补冬季上午/下午的阴影图；正午是最不具信息量的时刻")]
    return [make(rule, "PASS", actual + "，含 " + str(low) + " 组低角度", "9 组齐备且含低角度",
                 "把阴影当空间工具：檐下、廊下、架空层主动制造阴影")]


def rule_color(ctx):
    rule = "AEST-COLOR-01"
    design = ctx["design"]
    palette = design.get("palette") if isinstance(design.get("palette"), dict) else None
    if palette is None:
        return [make(rule, "UNVERIFIED", "缺 palette", "色相 ≤5、主色面积 ≥50%、明度跨度受控",
                     "补 palette: {hues, main_share, high_sat_share, lightness_range}")]
    hues = as_number(palette.get("hues") if palette.get("hues") is not None else palette.get("hue_count"))
    main = as_number(palette.get("main_share") if palette.get("main_share") is not None else palette.get("primary_share"))
    high = as_number(palette.get("high_sat_share") if palette.get("high_sat_share") is not None else palette.get("saturated_share"))
    span = as_number(palette.get("lightness_range") if palette.get("lightness_range") is not None else palette.get("l_range"))
    if main is not None and main > 1.0:
        main = main / 100.0
    if high is not None and high > 1.0:
        high = high / 100.0
    parts = []
    if hues is not None:
        parts.append("色相 " + ("%.0f" % hues))
    if main is not None:
        parts.append("主色 " + ("%.0f" % (main * 100)) + "%")
    if high is not None:
        parts.append("高饱和 " + ("%.0f" % (high * 100)) + "%")
    if span is not None:
        parts.append("明度极差 " + ("%.0f" % span))
    actual = "、".join(parts) if parts else "palette 无可解析数值"
    limit_text = "色相 ≤5、主色 ≥50%、高饱和 ≤10%、明度极差 ≤25（工程惯例建议值）"
    if hues is not None and hues > 5:
        return [make(rule, "FAIL", actual, limit_text, "砍到 5 个色相以内；一图一主题色")]
    if main is not None and main < 0.5:
        return [make(rule, "FAIL", actual, limit_text, "拉开主色面积占比到 50% 以上")]
    if high is not None and high > 0.10:
        return [make(rule, "WARN", actual, limit_text, "降低饱和面积；荧光感来自高饱和大面积")]
    if span is not None and span > 25:
        return [make(rule, "WARN", actual, limit_text, "压缩明度跨度，或用材质本色代替涂料")]
    return [make(rule, "PASS", actual, limit_text, "从场地取色：土壤、岩石、植被、既有建筑")]


RULES = (
    ("PLAN-METRIC-01", ("概念", "方案", "深化"), rule_metric_table),
    ("PLAN-FAR-01", ("概念", "方案", "深化"), rule_far),
    ("PLAN-DENSITY-01", ("概念", "方案", "深化"), rule_density),
    ("PLAN-GREEN-01", ("概念", "方案", "深化"), rule_green),
    ("PLAN-PARK-01", ("概念", "方案", "深化"), rule_parking),
    ("PLAN-SITE-01", ("概念", "方案", "深化"), rule_constraints),
    ("PLAN-PROG-01", ("概念", "方案", "深化"), rule_decisions),
    ("PLAN-CASE-01", ("概念", "方案", "深化"), rule_cases),
    ("PLAN-XLAY-01", ("方案", "深化"), rule_interface_map),
    ("PLAN-DRAIN-01", ("方案", "深化"), rule_drainage),
    ("PLAN-SECT-01", ("方案", "深化"), rule_sections),
    ("PLAN-FIRE-01", ("方案", "深化"), rule_fire),
    ("PLAN-ACCESS-01", ("方案", "深化"), rule_access),
    ("PLAN-BASE-01", ("方案", "深化"), rule_basement),
    ("PLAN-TREE-01", ("方案", "深化"), rule_trees),
    ("DET-WIN-01", ("深化",), rule_window_module),
    ("DET-WIN-02", ("深化",), rule_window_consistency),
    ("DET-NODE-01", ("深化",), rule_node_materials),
    ("DET-CLEAR-01", ("深化",), rule_clearance),
    ("AEST-MOD-01", ("方案", "深化"), rule_module),
    ("AEST-MAT-01", ("方案", "深化"), rule_material_share),
    ("AEST-LIGHT-01", ("方案", "深化"), rule_light),
    ("AEST-COLOR-01", ("方案", "深化"), rule_color),
)


# ------------------------------------------------------------------ 主流程

def load_design(path):
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return None, "读不到 design.json：" + str(exc)
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return None, "design.json 不是合法 JSON：" + str(exc)
    if not isinstance(data, dict):
        return None, "design.json 顶层须为对象"
    return data, ""


def build_hint(core_ok, core_reason, metrics, missing):
    if missing:
        names = "/".join(label for _, label in missing)
        if not core_ok:
            return ("core 引擎未就绪：无法校核 FAR/DENSITY/GREEN，请先完成 scripts/core/design_io.py"
                    "（" + names + " 同受影响；原因：" + (core_reason or "未知") + "）")
        return "core 输出缺指标：" + names + "，请在其 check 的 metrics 里补齐（键名见 references/checklist.md）"
    return ""


def render_text(report):
    lines = []
    lines.append("arch-env-design 自检 · stage=" + report["stage"])
    lines.append("design : " + report["design"])
    if report["core"]["ok"]:
        lines.append("core   : OK（metrics " + str(report["core"]["metric_count"]) + " 项）")
    else:
        lines.append("core   : 不可用（" + report["core"]["reason"] + "）")
    lines.append("rules  : " + str(report["summary"]["total"]) + " 条适用")
    lines.append("-" * 78)
    for item in report["results"]:
        lines.append("[" + item["status"] + "] " + item["rule"] + " " + item["title"]
                     + " | 实测 " + item["actual"]
                     + " | 限值 " + item["limit"]
                     + " | 建议 " + item["advice"])
    lines.append("-" * 78)
    summary = report["summary"]
    lines.append("汇总: PASS " + str(summary["PASS"]) + " · WARN " + str(summary["WARN"])
                 + " · FAIL " + str(summary["FAIL"]) + " · UNVERIFIED " + str(summary["UNVERIFIED"])
                 + " · 共 " + str(summary["total"]))
    if report["hint"]:
        lines.append("! " + report["hint"])
    lines.append("退出码: " + str(report["exit_code"]) + ("（存在 FAIL）" if summary["FAIL"] else ""))
    return "\n".join(lines)


def run(stage, design_path):
    data, error = load_design(design_path)
    if data is None:
        return None, error
    metrics, core_reason, core_cmd = core_check(design_path)
    if not metrics:
        metrics = metrics_from_design(data)
    metrics = merge_metrics(metrics, design_limits(data))
    ctx = {
        "design": data,
        "metrics": metrics or {},
        "stage": stage,
        "path": str(design_path),
        "core_reason": core_reason,
        "core_cmd": core_cmd,
    }
    results = []
    for rule_id, stages, func in RULES:
        if stage not in stages:
            continue
        produced = func(ctx)
        if not produced:
            produced = [make(rule_id, "UNVERIFIED", "规则未产出结果", "应有至少 1 条判定",
                             "检查规则实现")]
        results.extend(produced)
    summary = {status: 0 for status in STATUSES}
    for item in results:
        summary[item["status"]] += 1
    summary["total"] = len(results)
    missing = [(key, label) for key, label in CRITICAL_METRICS if (metrics or {}).get(key) is None]
    hint = build_hint(bool(metrics), core_reason, metrics or {}, missing)
    report = {
        "stage": stage,
        "design": str(design_path),
        "core": {
            "ok": bool(metrics),
            "cmd": core_cmd,
            "reason": core_reason,
            "metric_count": len(metrics or {}),
            "metrics": metrics or {},
        },
        "results": results,
        "summary": summary,
        "hint": hint,
        "exit_code": 1 if summary["FAIL"] else 0,
    }
    return report, ""


def list_rules():
    width = max(len(r[0]) for r in RULES)
    lines = ["已实现规则（rule id / 适用阶段 / 标题）："]
    for rule_id, stages, _ in RULES:
        lines.append("  " + rule_id.ljust(width) + "  " + "、".join(stages).ljust(9) + "  " + RULE_TITLES.get(rule_id, ""))
    lines.append("合计 " + str(len(RULES)) + " 条。规则文本见 references/checklist.md，两处 rule id 必须一致。")
    return "\n".join(lines)


def main(argv=None):
    ensure_utf8()
    parser = argparse.ArgumentParser(
        prog="checklist.py",
        description="arch-env-design 阶段自检器：按 概念/方案/深化 校核指标、交汇、规范与审美规则。",
        epilog="示例：python checklist.py ..\\examples\\demo-campus\\design.json --stage 方案 --json",
    )
    parser.add_argument("design", nargs="?", help="design.json 路径（配合 --list-rules 时可省略）")
    parser.add_argument("--stage", choices=list(STAGES), help="校核阶段：概念 / 方案 / 深化")
    parser.add_argument("--json", action="store_true", help="输出结构化 JSON（含 summary 计数）")
    parser.add_argument("--list-rules", action="store_true", help="列出全部规则 id 与适用阶段后退出")
    args = parser.parse_args(argv)

    if args.list_rules:
        emit(list_rules())
        return 0
    if not args.design:
        parser.error("缺少 design.json 路径（或用 --list-rules 查看规则）")
    if not args.stage:
        parser.error("缺少 --stage（概念 / 方案 / 深化）")

    design_path = Path(args.design)
    if not design_path.is_absolute():
        design_path = Path.cwd() / design_path
    design_path = design_path.resolve()
    if not design_path.is_file():
        emit("错误：design.json 不存在：" + str(design_path))
        return 2

    report, error = run(args.stage, design_path)
    if report is None:
        emit("错误：" + error)
        return 2
    if args.json:
        emit(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        emit(render_text(report))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
