#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一的外部程序调用层 —— 建筑软件接入层的地基。

为什么需要这一层
----------------
AutoCAD 的 accoreconsole.exe、3ds Max 的 3dsmaxbatch.exe 都是 Windows 控制台程序，
它们的回显编码**不是**当前 locale 就能解出来的：实测 accoreconsole 在重定向管道里
会吐出宽字符（UTF-16LE），直接按 GBK 解会得到一堆 \x00 与替换符。本模块只用标准库
做「试解 + 打分 + 选最优」，并把「超时杀进程树」「日志 UTF-8 落盘」一起收口。

对外只有两个东西
----------------
    run(cmd, timeout=120, cwd=None, out_log=None) -> dict
    decode_bytes(data) -> (text, report)

约定与纪律
----------
* cmd 必须是 list[str]。禁止传字符串拼 shell —— 中文路径在 cmd.exe 分词器里会被拆碎，
  这是本项目「路径含中文必须能跑」硬约束的直接原因。
* 超时默认 120s；超时用 taskkill /F /T 杀**整棵进程树**（accoreconsole 会派生子进程）。
* 日志默认落 <cwd>/out/logs/<name>-<timestamp>.log，UTF-8 无 BOM。
* 零第三方依赖（本机 Python 3.12 无 pip 权限）。

命令行
------
    python run_ext.py --selftest
    python run_ext.py -- <exe> [args...] [--timeout N] [--cwd DIR] [--json]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------- 常量

DEFAULT_TIMEOUT = 120.0
# 解释器解析顺序与 SKILL.md「运行环境」一致：DSH_PYTHON → DSH 自带运行时 → 当前解释器。
# 不要改成 shutil.which("python")：Windows 上它可能返回 Microsoft Store 占位符（退出码 9009）。
def _resolve_default_python() -> str:
    env = os.environ.get("DSH_PYTHON")
    if env and Path(env).is_file():
        return env
    home = os.environ.get("DSH_HOME")
    if home:
        found = sorted(Path(home).glob("dsh-runtimes/*/dependencies/python/python.exe"))
        if found:
            return str(found[0])
    return sys.executable or "python"


DEFAULT_PYTHON = _resolve_default_python()
# 候选编码的顺序是需求规定：UTF-8 → UTF-16LE → GBK
ENCODING_CANDIDATES = ("utf-8", "utf-16-le", "gbk")
# UTF-16LE 的守门条件：原始字节里 0x00 的占比。纯 ASCII 的偶数字节串
# 能"合法"解成 UTF-16LE，必须靠这个条件把它挡掉，否则 GBK 数据会被误判。
UTF16_NUL_RATIO_MIN = 0.05

BOMS = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)

SKILL_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------- 编码探测


def _nul_ratio(data: bytes) -> float:
    return data.count(0) / len(data) if data else 0.0


def _badness(text: str) -> tuple[int, int, int]:
    """返回 (NUL 个数, 替换符个数, 其它控制符/私用区字符个数)。

    解码正确时三者都应为 0。这是需求指定的判据：
    「解出的文本里是否出现 U+0000 或大量替换符」。
    """
    nul = text.count("\x00")
    repl = text.count("\ufffd")
    ctrl = 0
    for ch in text:
        o = ord(ch)
        if o < 32 and ch not in "\r\n\t":
            ctrl += 1
        elif 0xE000 <= o <= 0xF8FF:  # 私用区：GBK 被当成别的编码解常见产物
            ctrl += 1
    return nul, repl, ctrl


def decode_bytes(data: bytes) -> tuple[str, dict]:
    """把一段未知编码的输出字节解成 str，并返回打分报告。

    策略：按 UTF-8 → UTF-16LE → GBK 顺序试解，先通过「零坏字符」的胜出；
    全部都有坏字符时取坏字符最少的。UTF-16LE 额外要求原始字节里 NUL 占比够高。
    """
    report: dict = {
        "bytes": len(data),
        "bom": None,
        "chosen": None,
        "reason": "",
        "candidates": [],
    }
    if not data:
        report["chosen"] = "empty"
        report["reason"] = "零字节"
        return "", report

    for bom, enc in BOMS:
        if data.startswith(bom):
            text = data.decode(enc, errors="replace").replace("\r\n", "\n")
            report["bom"] = enc
            report["chosen"] = enc
            report["reason"] = "BOM 命中"
            report["candidates"].append({"encoding": enc, "bad": _badness(text)[0] + _badness(text)[1]})
            return text, report

    best_enc, best_bad, best_text = None, None, None
    for enc in ENCODING_CANDIDATES:
        item: dict = {"encoding": enc}
        if enc == "utf-16-le":
            ratio = _nul_ratio(data)
            item["nul_ratio"] = round(ratio, 4)
            if ratio < UTF16_NUL_RATIO_MIN:
                item["applicable"] = False
                item["note"] = "原始字节 NUL 占比 %.4f < %.2f，不可能是 UTF-16LE" % (
                    ratio,
                    UTF16_NUL_RATIO_MIN,
                )
                report["candidates"].append(item)
                continue
        item["applicable"] = True
        try:
            text = data.decode(enc, errors="replace").replace("\r\n", "\n")
        except LookupError:  # 理论上不会发生；GBK 别名缺失时的兜底
            item["applicable"] = False
            item["note"] = "当前 Python 无此编码"
            report["candidates"].append(item)
            continue
        nul, repl, ctrl = _badness(text)
        item.update({"nul": nul, "replacement": repl, "control": ctrl, "bad": nul + repl + ctrl})
        report["candidates"].append(item)
        # 严格小于才替换 => 同分时保持前面的候选（= 保持需求规定的优先级）
        if best_bad is None or item["bad"] < best_bad:
            best_enc, best_bad, best_text = enc, item["bad"], text
        if item["bad"] == 0:
            break

    if best_text is None:
        # 三个候选全被守门条件挡掉（例如超短的非 NUL 偶数字节串）
        best_enc = "utf-8"
        best_bad = -1
        best_text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
        report["reason"] = "全部候选被守门条件排除，退回 UTF-8 replace"
    else:
        report["reason"] = "坏字符 %d，按 UTF-8 → UTF-16LE → GBK 优先级选中 %s" % (best_bad, best_enc)

    report["chosen"] = best_enc
    return best_text, report


# ---------------------------------------------------------------- 进程树终止


def _kill_tree(proc: subprocess.Popen) -> dict:
    """终止子进程，并如实记录用了哪条路径。

    两级策略，缺一不可：
      1. taskkill /F /T —— 杀掉整棵进程树，能连带收掉 accoreconsole 派生的子进程。
         **但本机沙箱实测会返回"错误: 拒绝访问"(rc=1)**，所以不能只靠它。
      2. proc.kill() —— TerminateProcess 直接作用于我们持有的进程句柄，本机可用。
         只杀直接子进程，但对我们「直接调用 accoreconsole.exe / 3dsmaxbatch.exe」
         的用法足够；而且杀完还能从管道里把已缓冲的回显取回来。
    """
    rep: dict = {"taskkill": None, "direct_kill": False}
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        r = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=flags,
            timeout=30,
        )
        rep["taskkill"] = {
            "rc": r.returncode,
            "out": decode_bytes(r.stdout or b"")[0].strip(),
            "err": decode_bytes(r.stderr or b"")[0].strip(),
        }
    except Exception as exc:
        rep["taskkill"] = {"error": str(exc)}
    if proc.poll() is None:
        try:
            proc.kill()
            rep["direct_kill"] = True
        except Exception as exc:
            rep["direct_kill_error"] = str(exc)
    return rep


# ---------------------------------------------------------------- 日志


def _default_log_dir(cwd: Path | None) -> Path:
    env = os.environ.get("ARCH_LOG_DIR")
    if env:
        return Path(env)
    base = Path(cwd) if cwd else Path(os.getcwd())
    return base / "out" / "logs"


def _stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _write_log(path: Path, result: dict, cmd_text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    so = result.get("encoding", {}).get("stdout", {})
    se = result.get("encoding", {}).get("stderr", {})
    lines = [
        "# run_ext 调用日志",
        "# time:      " + result.get("started_at", ""),
        "# cmd:       " + cmd_text,
        "# cwd:       " + str(result.get("cwd", "")),
        "# timeout:   %.1fs" % result.get("timeout", 0),
        "# duration:  %dms" % result.get("duration_ms", 0),
        "# exit_code: %s   timed_out: %s" % (result.get("exit_code"), result.get("timed_out")),
        "# kill:      %s" % json.dumps(result.get("kill"), ensure_ascii=False),
        "# stdout:    %s bytes, 编码=%s (%s)" % (so.get("bytes", 0), so.get("chosen"), so.get("reason", "")),
        "# stderr:    %s bytes, 编码=%s (%s)" % (se.get("bytes", 0), se.get("chosen"), se.get("reason", "")),
        "",
        "===== STDOUT =====",
        result.get("stdout", ""),
        "",
        "===== STDERR =====",
        result.get("stderr", ""),
        "",
    ]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))


# ---------------------------------------------------------------- 主函数


def run(
    cmd,
    timeout: float = DEFAULT_TIMEOUT,
    cwd=None,
    out_log=None,
    *,
    name: str | None = None,
    log_dir=None,
    stdin_bytes: bytes | None = None,
    env: dict | None = None,
    keep_raw: bool = False,
) -> dict:
    """跑一个外部程序，返回 {exit_code, stdout, stderr, timed_out, ...}。

    参数
    ----
    cmd       : list[str]，argv 形式。传 str 会直接抛 TypeError（防 shell 注入/中文路径碎裂）。
    timeout   : 秒。超时即杀进程树，timed_out=True，exit_code 为 taskkill 后的实际值（通常非 0）。
    cwd       : 子进程工作目录。
    out_log   : 显式日志路径。缺省写 <log_dir>/<name>-<timestamp>.log。
    keep_raw  : 额外落盘原始字节 .stdout.bin/.stderr.bin，用于排查编码问题。
    """
    if isinstance(cmd, str):
        raise TypeError(
            "cmd 必须是 list[str]（argv 形式）。禁止传字符串：中文路径会被 shell 分词器拆碎。"
        )
    cmd = [str(c) for c in cmd]
    cwd_p = Path(cwd).resolve() if cwd else Path(os.getcwd()).resolve()
    t0 = time.time()
    started = _dt.datetime.now().isoformat(timespec="seconds")

    result: dict = {
        "cmd": cmd,
        "cmd_text": subprocess.list2cmdline(cmd),
        "cwd": str(cwd_p),
        "started_at": started,
        "timeout": float(timeout),
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "timed_out": False,
        "duration_ms": 0,
        "encoding": {},
        "log_path": None,
        "error": None,
    }

    exe = cmd[0]
    if os.path.isabs(exe):
        resolved = exe if os.path.exists(exe) else None
    else:
        resolved = shutil.which(exe)
    if not resolved:
        result["error"] = "可执行文件不存在: %s" % exe
        result["duration_ms"] = int((time.time() - t0) * 1000)
        result["ok"] = False
        return result

    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP

    popen_kw: dict = {
        "cwd": str(cwd_p) if cwd_p.exists() else None,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "creationflags": flags,
        "env": env,
    }
    if stdin_bytes is None:
        # 关键：绝不能继承父进程 stdin。accoreconsole 脚本跑完若等输入会永久挂住。
        popen_kw["stdin"] = subprocess.DEVNULL
    else:
        popen_kw["stdin"] = subprocess.PIPE

    try:
        proc = subprocess.Popen(cmd, **popen_kw)
    except OSError as exc:
        result["error"] = "启动失败: %s" % exc
        result["duration_ms"] = int((time.time() - t0) * 1000)
        result["ok"] = False
        return result

    out_b, err_b = b"", b""
    try:
        out_b, err_b = proc.communicate(input=stdin_bytes, timeout=timeout)
    except subprocess.TimeoutExpired:
        result["timed_out"] = True
        result["kill"] = _kill_tree(proc)
        try:
            # 进程已被 TerminateProcess 杀掉，这里只负责把管道里已缓冲的回显收回来
            out_b, err_b = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            out_b, err_b = b"", b""

    result["exit_code"] = proc.returncode
    result["duration_ms"] = int((time.time() - t0) * 1000)

    so_text, so_rep = decode_bytes(out_b or b"")
    se_text, se_rep = decode_bytes(err_b or b"")
    result["stdout"] = so_text
    result["stderr"] = se_text
    result["encoding"] = {"stdout": so_rep, "stderr": se_rep}

    # ---- 日志落盘
    if out_log:
        log_path = Path(out_log)
    else:
        nd = Path(log_dir) if log_dir else _default_log_dir(cwd_p)
        nm = name or Path(resolved).stem
        log_path = nd / ("%s-%s.log" % (nm, _stamp()))
    try:
        _write_log(log_path, result, result["cmd_text"])
        result["log_path"] = str(log_path)
        if keep_raw:
            base = log_path.with_suffix("")
            (Path(str(base) + ".stdout.bin")).write_bytes(out_b or b"")
            (Path(str(base) + ".stderr.bin")).write_bytes(err_b or b"")
    except OSError as exc:
        result["error"] = (result["error"] or "") + " 日志写入失败: %s" % exc

    result["ok"] = (result["exit_code"] == 0) and not result["timed_out"] and not result["error"]
    return result


def brief(result: dict) -> dict:
    """给命令行摘要用的精简视图。"""
    return {
        "cmd": result.get("cmd_text"),
        "exit_code": result.get("exit_code"),
        "timed_out": result.get("timed_out"),
        "duration_ms": result.get("duration_ms"),
        "stdout_encoding": result.get("encoding", {}).get("stdout", {}).get("chosen"),
        "stdout_bytes": result.get("encoding", {}).get("stdout", {}).get("bytes"),
        "stdout_head": (result.get("stdout") or "")[:400],
        "stderr_head": (result.get("stderr") or "")[:400],
        "log_path": result.get("log_path"),
        "error": result.get("error"),
    }


# ---------------------------------------------------------------- 自测


def selftest(out_root: Path | None = None) -> int:
    """五项自测：编码三态 + 退出码透传 + 超时杀树。全部通过返回 0。"""
    py = DEFAULT_PYTHON if os.path.exists(DEFAULT_PYTHON) else sys.executable
    out_root = Path(out_root) if out_root else REPO_ROOT / "out" / "verify" / "selftest"
    out_root.mkdir(parents=True, exist_ok=True)
    logs = out_root / "logs"
    logs.mkdir(parents=True, exist_ok=True)

    CJK = "\u4e2d\u6587\u6d4b\u8bd5"          # 中文测试
    EN = "Chinese-English-Mixed OK"

    cases = []

    # 1) 子进程按自己的默认编码打印中英文混杂
    code1 = (
        "import sys;"
        "sys.stdout.write('--- case1 console default ---\\n');"
        "sys.stdout.write('%s / %s\\n');"
        "sys.stdout.flush()" % (CJK, EN)
    )
    r1 = run([py, "-c", code1], timeout=60, log_dir=logs, name="st-01-default", keep_raw=True)
    ok1 = (CJK in r1["stdout"]) and (EN in r1["stdout"]) and r1["exit_code"] == 0
    cases.append(("1 中英文回显（子进程默认编码）", ok1, r1, [CJK, EN]))

    # 2) 强制 UTF-16LE 宽字符回显 —— 模拟 accoreconsole 的真实行为
    code2 = (
        "import sys;"
        "s='--- case2 utf-16le ---\\n%s WideChar UTF-16LE 123\\n';"
        "sys.stdout.buffer.write(s.encode('utf-16-le'));"
        "sys.stdout.buffer.flush()" % CJK
    )
    r2 = run([py, "-c", code2], timeout=60, log_dir=logs, name="st-02-utf16le", keep_raw=True)
    enc2 = r2["encoding"]["stdout"]["chosen"]
    ok2 = (enc2 == "utf-16-le") and (CJK in r2["stdout"]) and ("WideChar UTF-16LE 123" in r2["stdout"])
    cases.append(("2 宽字符回显（UTF-16LE 探测）", ok2, r2, [CJK, "WideChar UTF-16LE 123"]))

    # 3) 强制 GBK 回显 —— 中文 Windows 控制台最常见的情况
    code3 = (
        "import sys;"
        "s='--- case3 gbk ---\\n%s GBK-Line\\n';"
        "sys.stdout.buffer.write(s.encode('gbk'));"
        "sys.stdout.buffer.flush()" % CJK
    )
    r3 = run([py, "-c", code3], timeout=60, log_dir=logs, name="st-03-gbk", keep_raw=True)
    enc3 = r3["encoding"]["stdout"]["chosen"]
    ok3 = (enc3 == "gbk") and (CJK in r3["stdout"]) and ("GBK-Line" in r3["stdout"])
    cases.append(("3 中文回显（GBK 探测）", ok3, r3, [CJK, "GBK-Line"]))

    # 4) 退出码透传
    r4 = run([py, "-c", "import sys;sys.stdout.write('exit-code-passthrough\\n');sys.exit(3)"],
             timeout=60, log_dir=logs, name="st-04-exit")
    ok4 = r4["exit_code"] == 3 and not r4["timed_out"]
    cases.append(("4 退出码透传（期望 3）", ok4, r4, ["exit-code-passthrough"]))

    # 5) 超时 + 杀进程树
    r5 = run([py, "-c", "import time,sys;sys.stdout.write('sleep-start\\n');sys.stdout.flush();time.sleep(60)"],
             timeout=3, log_dir=logs, name="st-05-timeout")
    kill_rep = r5.get("kill") or {}
    ok5 = (
        r5["timed_out"]
        and r5["duration_ms"] < 25000
        and "sleep-start" in r5["stdout"]
        and (kill_rep.get("direct_kill") or kill_rep.get("taskkill", {}).get("rc") == 0)
    )
    cases.append(("5 超时杀进程树（timeout=3s，子进程睡 60s）", ok5, r5, ["sleep-start"]))

    # ---- 报告
    print("=" * 78)
    print("run_ext.py 自测  python=%s" % py)
    print("日志目录 %s" % logs)
    print("=" * 78)
    allok = True
    for title, ok, res, must in cases:
        allok = allok and ok
        print("")
        print("[%s] %s" % ("PASS" if ok else "FAIL", title))
        print("      cmd        : %s" % res["cmd_text"])
        print("      exit_code  : %s   timed_out: %s   duration: %dms"
              % (res["exit_code"], res["timed_out"], res["duration_ms"]))
        rep = res["encoding"]["stdout"]
        print("      stdout编码 : %s  (%s)" % (rep["chosen"], rep["reason"]))
        for cand in rep["candidates"]:
            if cand.get("applicable") is False:
                print("                   - %-10s 跳过: %s" % (cand["encoding"], cand.get("note")))
            else:
                print("                   - %-10s nul=%d repl=%d ctrl=%d bad=%d"
                      % (cand["encoding"], cand["nul"], cand["replacement"], cand["control"], cand["bad"]))
        for m in must:
            print("      含 %-26s : %s" % (repr(m)[1:-1], m in res["stdout"]))
        print("      回显原文   : %s" % repr(res["stdout"])[:300])
        if res.get("kill"):
            print("      终止方式   : taskkill rc=%s %s | direct_kill=%s"
                  % ((res["kill"].get("taskkill") or {}).get("rc"),
                     ((res["kill"].get("taskkill") or {}).get("err") or "")[:80],
                     res["kill"].get("direct_kill")))
        print("      日志       : %s" % res["log_path"])
    print("")
    print("=" * 78)
    print("自测结论: %s" % ("全部通过 (5/5)" if allok else "存在失败项"))
    print("=" * 78)
    return 0 if allok else 1


# ---------------------------------------------------------------- CLI


def main(argv) -> int:
    ap = argparse.ArgumentParser(
        prog="run_ext.py",
        description="统一外部程序调用层：编码探测 + 超时杀树 + UTF-8 日志",
    )
    ap.add_argument("--selftest", action="store_true", help="跑内置五项自测")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="超时秒数，默认 120")
    ap.add_argument("--cwd", default=None, help="子进程工作目录")
    ap.add_argument("--log", default=None, help="显式日志路径")
    ap.add_argument("--log-dir", default=None, help="日志目录，默认 <cwd>/out/logs")
    ap.add_argument("--name", default=None, help="日志文件名前缀")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--keep-raw", action="store_true", help="额外落盘原始字节，排查编码用")
    ap.add_argument("--stdin", default=None, help="向子进程 stdin 写入的文本")
    ap.add_argument("cmd", nargs=argparse.REMAINDER, help="-- 之后的程序与参数")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    cmd = [c for c in args.cmd if c != "--"]
    if not cmd:
        ap.print_help()
        return 2

    res = run(
        cmd,
        timeout=args.timeout,
        cwd=args.cwd,
        out_log=args.log,
        name=args.name,
        log_dir=args.log_dir,
        stdin_bytes=args.stdin.encode("utf-8") if args.stdin is not None else None,
        keep_raw=args.keep_raw,
    )
    if args.json:
        print(json.dumps(brief(res), ensure_ascii=False, indent=2))
    else:
        print("cmd        : %s" % res["cmd_text"])
        print("exit_code  : %s   timed_out: %s   duration: %dms"
              % (res["exit_code"], res["timed_out"], res["duration_ms"]))
        print("stdout编码 : %s (%s)" % (res["encoding"].get("stdout", {}).get("chosen"),
                                     res["encoding"].get("stdout", {}).get("reason")))
        print("log        : %s" % res["log_path"])
        if res["error"]:
            print("error      : %s" % res["error"])
        print("--- STDOUT ---")
        print(res["stdout"])
        if res["stderr"]:
            print("--- STDERR ---")
            print(res["stderr"])
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
