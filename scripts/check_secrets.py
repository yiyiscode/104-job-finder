"""掃描 git 裡有沒有祕密。git hook 的本體,也可以手動跑。

    python scripts/check_secrets.py --staged     # pre-commit:只看這次要 commit 的內容
    python scripts/check_secrets.py --history    # pre-push / 轉公開前:整個 commit 歷史

兩種訊號:

* **金鑰格式**:OpenRouter / Telegram bot token / GitHub / AWS / 私鑰檔頭。
* **`.env` 裡的實際值**:格式比對抓不到的(例如 chat id),拿本機 `.env` 的值逐字比。
  **輸出永遠遮罩**,只印變數名與位置 —— 掃描器自己把金鑰印進 log 就本末倒置了。

另外 `.env`、`*.db`、`local_data/` 這類檔案**光是路徑出現在歷史裡**就算失敗,
不管內容是什麼(db 裡是實際的求職紀錄)。

個資(手機、email)只列為 WARN 不擋 —— `profile.md` 本來就是履歷,是否公開由人決定。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "OpenRouter key": re.compile(r"sk-or-v1-[0-9a-f]{32,}"),
    "OpenAI/Anthropic 型 key": re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}"),
    "Telegram bot token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Modal token": re.compile(r"\ba[ks]-[A-Za-z0-9]{20,}\b"),
    "私鑰": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}

PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "手機號碼": re.compile(r"\b09\d{2}[- ]?\d{3}[- ]?\d{3}\b"),
    "email": re.compile(r"\b[\w.+-]+@(?!example\.)[\w-]+\.[\w.]+\b"),
}

#: 範本裡的佔位字串(`sk-or-v1-xxxxxxxx`、`<your-key>`)長得像金鑰,但不是
PLACEHOLDER = re.compile(r"x{6,}|X{6,}|<[^>]*>|\.\.\.|your", re.IGNORECASE)

#: 路徑本身就不該進版控。`.env.example` 是範本,不算。
FORBIDDEN_PATHS = re.compile(r"(^|/)\.env$|\.db$|\.sqlite3?$|(^|/)local_data/")

#: `.env` 裡這些值太短或太常見,逐字比會誤報(例如 JOBFINDER_SCRAPE_DISABLED=0)
MIN_LITERAL_LEN = 8


class Finding(NamedTuple):
    level: str  # "FAIL" | "WARN"
    kind: str
    where: str
    sample: str


def mask(value: str) -> str:
    return value[:4] + "***" if len(value) > 4 else "***"


def env_literals(env_path: Path) -> dict[str, str]:
    """`.env` 的實際值(只取夠長的)。沒有 `.env` 就回空的。"""
    if not env_path.exists():
        return {}
    out: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        value = value.strip().strip("\"'")
        if len(value) >= MIN_LITERAL_LEN:
            out[name.strip()] = value
    return out


def scan_text(text: str, where: str, literals: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    for kind, pattern in SECRET_PATTERNS.items():
        for m in pattern.finditer(text):
            if PLACEHOLDER.search(m.group(0)):
                continue
            findings.append(Finding("FAIL", kind, where, mask(m.group(0))))
    for name, value in literals.items():
        if value in text:
            findings.append(Finding("FAIL", f".env 的 {name}", where, mask(value)))
    for kind, pattern in PII_PATTERNS.items():
        for m in pattern.finditer(text):
            findings.append(Finding("WARN", kind, where, mask(m.group(0))))
    return findings


def scan_paths(paths: list[str], where: str) -> list[Finding]:
    return [
        Finding("FAIL", "不該進版控的檔案", where, p)
        for p in paths
        if FORBIDDEN_PATHS.search(p.replace("\\", "/"))
    ]


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def added_lines_by_commit(log_patch: str) -> list[tuple[str, str, str]]:
    """把 `git log -p` 切成 (commit, 檔名, 新增的行)。只看新增的行 —— 刪掉的也還在歷史裡,
    但它一定曾經以「新增」的身分出現過,所以不會漏。"""
    out: list[tuple[str, str, str]] = []
    commit, path = "?", "?"
    for line in log_patch.splitlines():
        if line.startswith("commit "):
            commit = line.split()[1][:8]
        elif line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("+") and not line.startswith("+++"):
            out.append((commit, path, line[1:]))
    return out


def scan_history(literals: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    names = git("log", "--all", "--name-only", "--format=commit %H")
    commit = "?"
    for line in names.splitlines():
        if line.startswith("commit "):
            commit = line.split()[1][:8]
        elif line.strip():
            findings += scan_paths([line.strip()], f"{commit}")
    patch = git("log", "--all", "-p", "--no-color", "--format=commit %H")
    for commit, path, text in added_lines_by_commit(patch):
        findings += scan_text(text, f"{commit} {path}", literals)
    return findings


def scan_staged(literals: dict[str, str]) -> list[Finding]:
    staged = git("diff", "--cached", "--name-only", "--diff-filter=ACMR").splitlines()
    findings = scan_paths(staged, "staged")
    patch = git("diff", "--cached", "--no-color")
    for _, path, text in added_lines_by_commit("commit staged\n" + patch):
        findings += scan_text(text, f"staged {path}", literals)
    return findings


def dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple[str, str, str, str]] = set()
    out = []
    for f in findings:
        key = (f.level, f.kind, f.where.split()[-1], f.sample)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="掃描 git 裡的金鑰與不該進版控的檔案")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="只掃這次要 commit 的內容")
    mode.add_argument("--history", action="store_true", help="掃整個 commit 歷史(所有分支)")
    parser.add_argument("--env", type=Path, default=PROJECT_ROOT / ".env", help="比對用的 .env")
    parser.add_argument("--quiet-warn", action="store_true", help="不印個資 WARN,只印 FAIL")
    args = parser.parse_args(argv)

    literals = env_literals(args.env)
    findings = dedupe(scan_staged(literals) if args.staged else scan_history(literals))
    fails = [f for f in findings if f.level == "FAIL"]
    warns = [f for f in findings if f.level == "WARN"]

    for f in fails + ([] if args.quiet_warn else warns):
        print(f"[{f.level}] {f.kind}  @ {f.where}  ({f.sample})")
    scope = "staged 內容" if args.staged else "整個歷史"
    print(
        f"check_secrets:{scope} —— FAIL {len(fails)}、WARN {len(warns)}"
        f"(比對了 .env 的 {len(literals)} 個實際值)"
    )
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
