"""通知層測試。

排版壞掉的症狀是「整則訊息 400 消失」,使用者只會看到日報少了一則卻不知道為什麼。
所以跳脫與降級路徑要測透。
"""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from jobfinder.config import NotifyCfg, ScoringCfg
from jobfinder.errors import NotifyFailed
from jobfinder.models import (
    JobSummary,
    RejectedJob,
    RunReport,
    ScoreBreakdown,
    ScoredJob,
)
from jobfinder.notify.format import (
    SAFE_LIMIT,
    render_job_card,
    render_summary,
    split_message,
    strip_tags,
)
from jobfinder.notify.telegram import TelegramNotifier

NOW = datetime(2026, 8, 21, 8, 0, 0)


def make_scored(**kw) -> ScoredJob:
    summary = JobSummary(
        job_no="a1b2c",
        detail_id="94234",
        job_name=kw.pop("job_name", "AI工程師(LLM應用)"),
        cust_name=kw.pop("cust_name", "示範金融科技"),
        cust_no="cust001",
        job_url="https://www.104.com.tw/job/a1b2c",
        area_desc="台北市南港區",
        salary_desc="月薪 60,000~90,000 元",
        edu_desc="碩士以上",
        period_desc="經歷不拘",
        matched_keywords=["AI工程師", "LLM工程師"],
    )
    defaults = {
        "summary": summary,
        "breakdown": ScoreBreakdown(
            tech_fit=32, exp_fit=22, domain_fit=14, growth_fit=12, practical_fit=7
        ),
        "total": 87,
        "verdict": "strong_apply",
        "one_liner": "金融業 LLM 落地職缺,跟你的 RAG 專案幾乎完全對上。",
        "highlights": ["JD 點名 LangChain 與向量資料庫", "金融資安背景對應國泰實習"],
        "red_flags": ["需與業務單位溝通需求"],
        "resume_tip": "把 RAG 的拒答機制寫成法遵語言",
    }
    defaults.update(kw)
    return ScoredJob(**defaults)


# ─── 跳脫 ───────────────────────────────────────────────────────────


def test_html_special_chars_are_escaped():
    """職缺標題偶爾有 < 或 &,漏跳脫就整則 400。"""
    card, _ = render_job_card(make_scored(job_name="AI<工程師> & 研究員"))
    assert "&lt;工程師&gt;" in card
    assert "&amp;" in card
    assert "<b>" in card, "自己的標籤不該被跳脫掉"


def test_strip_tags_recovers_plain_text():
    card, _ = render_job_card(make_scored())
    plain = strip_tags(card)
    assert "<b>" not in plain
    assert "AI工程師" in plain


# ─── 卡片 ───────────────────────────────────────────────────────────


def test_card_includes_score_breakdown_and_link_button():
    card, keyboard = render_job_card(make_scored())
    assert "87分" in card
    assert "技術32/35" in card
    urls = [b["url"] for b in keyboard["inline_keyboard"][0]]
    assert "https://www.104.com.tw/job/a1b2c" in urls
    assert any("company/cust001" in u for u in urls)


def test_card_omits_empty_sections():
    card, _ = render_job_card(make_scored(highlights=[], red_flags=[], resume_tip="", one_liner=""))
    assert "為什麼適合你" not in card
    assert "注意" not in card
    assert "履歷建議" not in card
    assert "\n\n\n" not in card, "不該留下空洞的區塊"


def test_card_without_cust_no_still_has_one_button():
    scored = make_scored()
    scored.summary.cust_no = None
    _, keyboard = render_job_card(scored)
    assert len(keyboard["inline_keyboard"][0]) == 1


def test_card_fits_in_one_telegram_message():
    scored = make_scored(
        highlights=["很長的理由 " * 20] * 3,
        red_flags=["需要注意的地方 " * 15] * 2,
    )
    card, _ = render_job_card(scored)
    assert len(split_message(card)) == 1


# ─── 摘要 ───────────────────────────────────────────────────────────


def make_report(**kw) -> RunReport:
    defaults = {
        "started_at": NOW,
        "finished_at": datetime(2026, 8, 21, 8, 6, 12),
        "jobs_fetched": 60,
        "jobs_new": 47,
        "jobs_screened_in": 16,
        "jobs_deep_scored": 16,
        "requests_used": 12,
        "llm_cost_usd": 0.021,
    }
    defaults.update(kw)
    return RunReport(**defaults)


def test_summary_shows_funnel_and_costs():
    report = make_report(notified=[make_scored()])
    text = render_summary(report)
    assert "47" in text and "16" in text
    assert "$0.021" in text
    assert "請求 12 次" in text


def test_summary_lists_rejected_jobs_for_reference():
    report = make_report(
        rejected=[
            RejectedJob(
                job_no="x",
                job_name="資料科學家",
                cust_name="某電信",
                total=68,
                reason="要求 3 年 BI 實戰",
            )
        ]
    )
    text = render_summary(report)
    assert "未達標" in text
    assert "要求 3 年 BI 實戰" in text


def test_summary_when_no_new_jobs_is_not_silent():
    """0 則也要發:靜默會讓使用者以為系統壞了(SPEC.md F-7)。"""
    text = render_summary(make_report(jobs_new=0, jobs_screened_in=0, jobs_deep_scored=0))
    assert "今天沒有新職缺" in text


def test_summary_surfaces_schema_drift_warning():
    text = render_summary(make_report(schema_drift_count=9))
    assert "schema drift" in text


# ─── digest(只發一則)─────────────────────────────────────────────


def test_digest_puts_jobs_inline_with_title_links():
    job = make_scored(
        summary=make_scored().summary.model_copy(
            update={"job_url": "https://www.104.com.tw/job/a1b2c?a=1&b=2"}
        )
    )
    text = render_summary(make_report(notified=[job]), digest=True)
    assert '<a href="https://www.104.com.tw/job/a1b2c?a=1&amp;b=2">' in text
    assert "示範金融科技" in text and "月薪 60,000~90,000 元" in text
    assert "💬" in text
    assert "逐則推送" not in text, "digest 不會再逐則推,不能這樣寫"


def test_cards_summary_is_unchanged():
    text = render_summary(make_report(notified=[make_scored()]))
    assert "逐則推送" in text and "<a href" not in text


def test_digest_always_fits_in_one_message():
    """塞不下時依序犧牲:未達標清單 → 一句話 → 尾端職缺。絕不切成兩則。"""
    long = "很長的一句話" * 40
    jobs = [make_scored(job_name=f"職缺{i}" + "X" * 60, one_liner=long) for i in range(20)]
    rejected = [
        RejectedJob(
            job_no=f"r{i}", job_name="被刷掉" * 10, cust_name="某公司", total=50, reason="理由" * 30
        )
        for i in range(10)
    ]
    text = render_summary(
        make_report(notified=jobs, rejected=rejected), digest=True, max_rejected=10
    )
    assert len(text) <= SAFE_LIMIT
    assert len(split_message(text)) == 1
    assert "未達標" not in text, "未達標清單最先被犧牲"
    assert "另 " in text and "Web UI" in text, "砍掉的職缺要說一聲,不能無聲消失"


def test_digest_keeps_rejected_list_when_it_fits():
    rejected = [
        RejectedJob(
            job_no="x",
            job_name="資料科學家",
            cust_name="某電信",
            total=68,
            reason="要求 3 年 BI 實戰",
        )
    ]
    text = render_summary(make_report(notified=[make_scored()], rejected=rejected), digest=True)
    assert "要求 3 年 BI 實戰" in text


# ─── 切段 ───────────────────────────────────────────────────────────


def test_short_message_is_not_split():
    assert split_message("hi") == ["hi"]


def test_long_message_splits_under_the_limit():
    text = "\n\n".join(f"<b>區塊 {i}</b>\n{'內容 ' * 100}" for i in range(30))
    parts = split_message(text)
    assert len(parts) > 1
    assert all(len(p) <= SAFE_LIMIT for p in parts)


def test_split_does_not_cut_inside_a_tag():
    text = "\n\n".join(f"<b>標題{i}</b> {'字' * 300}" for i in range(40))
    for part in split_message(text):
        assert part.count("<b>") == part.count("</b>")


def test_single_oversized_block_is_still_split():
    parts = split_message("\n".join("字" * 100 for _ in range(200)))
    assert all(len(p) <= SAFE_LIMIT for p in parts)


# ─── Telegram HTTP 行為 ─────────────────────────────────────────────


class NoSleep:
    def __init__(self):
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def make_notifier(handler, sleeper=None):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TelegramNotifier(
        "token",
        "chat",
        notify=NotifyCfg(),
        scoring=ScoringCfg(),
        client=client,
        sleeper=sleeper or NoSleep(),
    )


async def test_send_posts_html_with_preview_disabled():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    await make_notifier(handler).send_job(make_scored())
    assert seen[0]["parse_mode"] == "HTML"
    assert seen[0]["link_preview_options"] == {"is_disabled": True}
    assert "reply_markup" in seen[0]


async def test_400_downgrades_to_plain_text_instead_of_losing_the_message():
    """寧可醜也不要整則消失。"""
    import json

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if "parse_mode" in body:
            return httpx.Response(400, json={"description": "can't parse entities"})
        return httpx.Response(200, json={"ok": True})

    await make_notifier(handler).send_job(make_scored())
    assert len(seen) == 2
    assert "parse_mode" not in seen[1]
    assert "<b>" not in seen[1]["text"]


async def test_429_respects_retry_after():
    import json

    calls = {"n": 0}
    sleeper = NoSleep()

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"parameters": {"retry_after": 7}})
        return httpx.Response(200, json={"ok": True})

    await make_notifier(handler, sleeper).send("hi")
    assert calls["n"] == 2
    assert 8 in sleeper.calls, f"應依 retry_after 等待,實際 {sleeper.calls}"
    assert json  # keep import used


async def test_persistent_failure_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"description": "Unauthorized"})

    with pytest.raises(NotifyFailed):
        await make_notifier(handler).send("hi")


async def test_messages_are_throttled_between_sends():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    sleeper = NoSleep()
    notifier = make_notifier(handler, sleeper)
    await notifier.send("one")
    await notifier.send("two")
    assert sleeper.calls, "單一聊天室建議 < 1 msg/sec,必須節流"


def test_summary_does_not_claim_a_threshold_in_top_n_mode():
    """top_n 模式根本不看絕對門檻,推出來的可能是 64 分 ——
    摘要寫「達標(≥80)」就是在說謊,而那是使用者唯一會看的那行字。"""
    from jobfinder.models import RunReport

    report = RunReport(
        started_at=datetime(2026, 9, 23, 8, 0),
        finished_at=datetime(2026, 9, 23, 8, 1),
        jobs_new=94,
        jobs_filtered_out=87,
        jobs_screened_in=4,
        jobs_deep_scored=4,
    )
    text = render_summary(report, threshold=80, mode="top_n", top_n=5)
    assert "達標" not in text
    assert "分數前 5 名" in text

    # threshold 模式照舊
    text = render_summary(report, threshold=80, mode="threshold")
    assert "達標(≥80)" in text


def test_summary_shows_the_targeting_step():
    """漏斗少了這一關,使用者只會看到「新職缺 94 → 粗篩留 4」,
    以為是模型太嚴,實際上是規則層先砍掉 87 筆。"""
    from jobfinder.models import RunReport

    report = RunReport(
        started_at=datetime(2026, 9, 23, 8, 0),
        finished_at=datetime(2026, 9, 23, 8, 1),
        jobs_new=94,
        jobs_filtered_out=87,
        jobs_screened_in=4,
        jobs_deep_scored=4,
    )
    assert "目標產業／規模留 <b>7</b>" in render_summary(report, threshold=80)
