"""Claude Code 的 PreToolUse hook:在 AI 執行指令之前擋下三類危險操作。

1. `modal deploy` —— 雲端 IP 會被 104 的 Cloudflare 擋,cron 每天失敗會把熔斷器燒到要人工解除(見 CLAUDE.md)
2. 會真的連線 104 的 `jobfinder run` —— 沒帶 `--limit` 就擋;`--from-fixtures` / `--replay` 是離線的,放行
3. 讀取 `.env` —— 金鑰由本人手動維護,AI 不需要看實際值(`.env.example` 不受影響)

stdin 收 hook 的 JSON,擋下時輸出 permissionDecision=deny,理由會回給 AI。
放在 .claude/hooks/ 而不是 scripts/:這不是給人跑的 CLI,tests/test_scripts.py 的 argparse 檢查不適用。
"""

import json
import re
import sys

# `.env` 當成獨立路徑出現才算:前面是開頭/空白/斜線/引號/等號,後面是結尾/空白/引號/分號/管線。
# 反斜線只在 Windows 路徑裡算(前面是路徑字元),`grep '\.env'` 這種正規表達式裡的跳脫不算
ENV_PATH = re.compile(r"""(?:^|[\s/"'=]|(?<=[\w\-])\\)\.env(?=$|[\s"';|&)])""")
READERS = re.compile(
    r"\b(cat|head|tail|less|more|grep|rg|sed|awk|type|source|Get-Content|gc|Select-String|strings|xxd|od)\b"
)
QUOTED_ENV_PATH = re.compile(r"^(?:.*[\w\-. ~:][\\/]|/)?\.env$")  # '\.env' 是正規表達式,不算
JOBFINDER_RUN =re.compile(r"jobfinder(?:\.cli)?\s+run\b")
OFFLINE_FLAGS = ("--from-fixtures", "--replay", "--help", "-h")


def check_bash(cmd: str) -> str | None:
    # 引號裡是文字不是指令(例如 commit message 寫到「modal deploy」),比對前先拿掉。
    # 例外:整串引號內容就是一個以 .env 結尾的路徑(帶空白的路徑必須加引號),換成 .env 保留下來
    def unquote(m: re.Match) -> str:
        return " .env " if QUOTED_ENV_PATH.match(m.group(0)[1:-1]) else '""'

    bare = re.sub(r""""[^"]*"|'[^']*'""", unquote, cmd)
    if re.search(r"\bmodal\s+deploy\b", bare):
        return "禁止 `modal deploy`:雲端 IP 會被 104 的 Cloudflare 擋下,排程每天失敗會燒熔斷器(見 CLAUDE.md「為什麼跑在本機」)。"
    if JOBFINDER_RUN.search(bare) and not any(f in bare for f in OFFLINE_FLAGS):
        if "--limit" not in bare:
            return "禁止不帶 `--limit` 的 `jobfinder run`:會實際連線 104。開發請用 `--from-fixtures` / `--replay`,真要連線必須 `--limit`(CLAUDE.md 絕對禁止第 6 條)。"
    if ENV_PATH.search(bare) and READERS.search(bare):
        return "禁止讀取 `.env`:金鑰由本人手動維護,AI 不需要實際值。要看變數名稱請讀 `.env.example`。"
    return None


def check_path(path: str) -> str | None:
    name = re.split(r"[\\/]", path.rstrip("\\/"))[-1]
    if name == ".env":
        return "禁止讀取 `.env`:金鑰由本人手動維護,AI 不需要實際值。要看變數名稱請讀 `.env.example`。"
    return None


def main() -> int:
    # Windows 的預設編碼是 cp950,不指定的話中文理由會變亂碼
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    payload = json.load(sys.stdin)
    tool = payload.get("tool_name", "")
    args = payload.get("tool_input", {}) or {}

    if tool in ("Bash", "PowerShell"):
        reason = check_bash(args.get("command", ""))
    elif tool in ("Read", "Grep", "Edit", "Write"):
        reason = check_path(args.get("file_path") or args.get("path") or "")
    else:
        reason = None

    if reason:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            },
            sys.stdout,
            ensure_ascii=False,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
