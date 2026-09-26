"""`scripts/check_secrets.py`:git hook 的本體。護欄沒有測試等於沒有護欄。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_secrets.py"
_spec = importlib.util.spec_from_file_location("check_secrets", _PATH)
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)

FAKE_OPENROUTER = "sk-or-v1-" + "0123456789abcdef" * 4
FAKE_TELEGRAM = "1234567890:" + "A" * 20 + "b" * 15


def kinds(findings, level="FAIL"):
    return {f.kind for f in findings if f.level == level}


def test_detects_real_looking_keys():
    text = f"OPENROUTER_API_KEY={FAKE_OPENROUTER}\nTOKEN={FAKE_TELEGRAM}"
    assert {"OpenRouter key", "Telegram bot token"} <= kinds(cs.scan_text(text, "x", {}))


def test_placeholders_are_not_findings():
    text = "OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    assert kinds(cs.scan_text(text, ".env.example", {})) == set()


def test_env_literal_match_is_masked():
    """格式比對抓不到的值(chat id)靠 .env 逐字比;輸出不能洩漏原值。"""
    findings = cs.scan_text("chat=987654321012", "x", {"TELEGRAM_CHAT_ID": "987654321012"})
    hit = [f for f in findings if f.kind == ".env 的 TELEGRAM_CHAT_ID"]
    assert hit and "987654321012" not in hit[0].sample


def test_env_literals_skip_short_values(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# c\nJOBFINDER_SCRAPE_DISABLED=0\nKEY='abcdefghij'\n", encoding="utf-8")
    assert cs.env_literals(env) == {"KEY": "abcdefghij"}


def test_forbidden_paths():
    bad = [".env", "local_data/jobs.db", "sub/decisions.db", "local_data/daily.log"]
    ok = [".env.example", "src/jobfinder/storage/db.py", "docs/images/ui.png"]
    assert {f.sample for f in cs.scan_paths(bad + ok, "x")} == set(bad)


def test_pii_is_warn_not_fail():
    findings = cs.scan_text("mail me: someone@gmail.com", "profile.md", {})
    assert kinds(findings) == set() and "email" in kinds(findings, "WARN")


def test_added_lines_only():
    patch = "commit abcdef123\n+++ b/a.txt\n-removed\n+added\n"
    assert cs.added_lines_by_commit(patch) == [("abcdef12", "a.txt", "added")]
