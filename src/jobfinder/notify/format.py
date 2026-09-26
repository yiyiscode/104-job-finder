"""Telegram 訊息排版。

一律用 **HTML** 而非 MarkdownV2:後者要跳脫 18 個字元,而職缺標題與薪資裡到處是
``(`` ``)`` ``-`` ``.``,漏一個就整則 400 Bad Request。HTML 只要跳脫 ``&`` ``<`` ``>``。
"""

from __future__ import annotations

import html
import re

from ..models import RunReport, ScoredJob
from ..scrape.urls import company_url

TG_LIMIT = 4096
#: 留餘裕給 HTML tag 與 emoji 的 UTF-16 計算差異
SAFE_LIMIT = 3800

_WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")


def esc(value: str | None) -> str:
    return html.escape(value or "", quote=False)


def strip_tags(text: str) -> str:
    """HTML 送失敗時的降級:拔掉標籤送純文字。寧可醜也不要整則消失。"""
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _score_icon(total: int, threshold: int) -> str:
    if total >= threshold + 15:
        return "🟢"
    if total >= threshold + 5:
        return "🟡"
    return "🟠"


def render_job_card(scored: ScoredJob, *, threshold: int = 70) -> tuple[str, dict | None]:
    """一則職缺 = 一則訊息。好讀、好轉傳、不會超字數。"""
    s = scored.summary
    lines = [
        f"{_score_icon(scored.total, threshold)} <b>{esc(s.job_name)}</b>"
        f"  <code>{scored.total}分</code>",
        f"🏢 {esc(s.cust_name)}",
        f"📍 {esc(s.area_desc or '地點未提供')}    💰 {esc(s.salary_desc or '待遇面議')}",
        f"🎓 {esc(s.edu_desc or '學歷不拘')}    ⏳ {esc(s.period_desc or '經歷不拘')}",
    ]

    if scored.one_liner:
        lines += ["", "<b>▎一句話</b>", esc(scored.one_liner)]

    if scored.highlights:
        lines += ["", "<b>▎為什麼適合你</b>"]
        lines += [f"• {esc(h)}" for h in scored.highlights[:3]]

    if scored.red_flags:
        lines += ["", "<b>▎注意</b>"]
        lines += [f"⚠️ {esc(r)}" for r in scored.red_flags[:2]]

    if scored.resume_tip:
        lines += ["", "<b>▎履歷建議</b>", f"💡 {esc(scored.resume_tip)}"]

    b = scored.breakdown
    lines += [
        "",
        f"<i>細項 技術{b.tech_fit}/35 · 年資{b.exp_fit}/25 · 領域{b.domain_fit}/15"
        f" · 成長{b.growth_fit}/15 · 實務{b.practical_fit}/10</i>",
    ]

    if s.matched_keywords:
        lines.append(f"<i>命中關鍵字:{esc('、'.join(s.matched_keywords))}</i>")

    buttons = [{"text": "📄 看原始職缺", "url": s.job_url}]
    if (company := company_url(s.cust_no)) is not None:
        buttons.append({"text": "🏢 公司其他職缺", "url": company})

    return "\n".join(lines), {"inline_keyboard": [buttons]}


def _digest_entry(job: ScoredJob, threshold: int, *, one_liner: bool) -> str:
    """digest 模式裡的一筆職缺:三行 + 可選的一句話。職稱本身就是 104 連結。"""
    s = job.summary
    url = html.escape(s.job_url or "", quote=True)
    lines = [
        f"{_score_icon(job.total, threshold)} <code>{job.total}</code> "
        f'<a href="{url}"><b>{esc(s.job_name)}</b></a>',
        f"🏢 {esc(s.cust_name)}",
        f"📍 {esc(s.area_desc or '地點未提供')} · 💰 {esc(s.salary_desc or '待遇面議')}"
        f" · ⏳ {esc(s.period_desc or '經歷不拘')}",
    ]
    if one_liner and job.one_liner:
        lines.append(f"💬 {esc(job.one_liner)}")
    return "\n".join(lines)


def render_summary(
    report: RunReport,
    *,
    threshold: int = 70,
    max_rejected: int = 10,
    mode: str = "threshold",
    top_n: int = 0,
    digest: bool = False,
) -> str:
    """總覽。``digest=True`` 時職缺直接寫在裡面,**保證只有一則訊息**。

    太長時依序犧牲:未達標清單 → 每筆的一句話 → 尾端的職缺(改成「另 N 則」)。
    不交給 :func:`split_message` 切段 —— 切成兩則就違背了「只發一則」。
    """
    if not digest:
        return _render_summary(report, threshold, max_rejected, mode, top_n, None)

    jobs_n = len(report.notified)
    attempts = [(max_rejected, True, jobs_n), (0, True, jobs_n), (0, False, jobs_n)]
    attempts += [(0, False, n) for n in range(jobs_n - 1, -1, -1)]
    text = ""
    for rejected_n, one_liner, shown in attempts:
        text = _render_summary(report, threshold, rejected_n, mode, top_n, (one_liner, shown))
        if len(text) <= SAFE_LIMIT:
            return text
    return text[:SAFE_LIMIT]  # 理論上走不到:0 筆職缺的摘要不可能超長


def _render_summary(
    report: RunReport,
    threshold: int,
    max_rejected: int,
    mode: str,
    top_n: int,
    digest: tuple[bool, int] | None,
) -> str:
    d = report.started_at
    header = f"📊 <b>104 職缺日報</b> · {d.year}/{d.month:02d}/{d.day:02d}"
    header += f"({_WEEKDAYS[d.weekday()]})"

    lines = [header, ""]

    if report.jobs_new == 0:
        lines.append("今天沒有新職缺。已檢查但沒有值得打擾你的東西。")
    else:
        # 漏斗一定要把「產業／規模」那一關畫出來:不然使用者只會看到
        # 「新職缺 52 → 粗篩留 3」,以為是模型太嚴,實際上是規則層先砍掉 44 筆。
        funnel = f"今日新職缺 <b>{report.jobs_new}</b> 則"
        if report.jobs_filtered_out:
            kept = report.jobs_new - report.jobs_filtered_out
            funnel += f" → 目標產業／規模留 <b>{kept}</b>"
        # top_n 模式下寫「達標(≥80)」是**假的** —— 那個模式根本不看絕對門檻,
        # 推出來的可能是 64 分。標籤要誠實反映實際用的選取規則。
        picked = f"達標(≥{threshold})" if mode == "threshold" else f"分數前 {top_n} 名"
        funnel += (
            f" → 粗篩留 <b>{report.jobs_screened_in}</b>"
            f" → 深評 <b>{report.jobs_deep_scored}</b>"
            f" → {picked} <b>{len(report.notified)}</b> 則"
        )
        lines.append(funnel)

    if report.notified and digest is not None:
        one_liner, shown = digest
        lines += ["", f"<b>▎今天推薦 {len(report.notified)} 則</b>(點職稱開 104)"]
        for job in report.notified[:shown]:
            lines += ["", _digest_entry(job, threshold, one_liner=one_liner)]
        if (rest := len(report.notified) - shown) > 0:
            lines += ["", f"…另 {rest} 則,在 Web UI 看"]
    elif report.notified:
        lines += ["", f"<b>▎接下來會逐則推送 {len(report.notified)} 則</b>"]
        for job in report.notified:
            icon = _score_icon(job.total, threshold)
            lines.append(
                f"{icon} <code>{job.total}</code> {esc(job.summary.job_name)}"
                f" · {esc(job.summary.cust_name)}"
            )

    if report.rejected and max_rejected > 0:
        shown = report.rejected[:max_rejected]
        lines += ["", f"<b>▎未達標(供參考,共 {len(report.rejected)} 則)</b>"]
        for job in shown:
            lines.append(
                f"<code>{job.total}</code> {esc(job.job_name)} · {esc(job.cust_name)}"
                f" — {esc(job.reason)}"
            )

    footer = [f"耗時 {report.duration_seconds:.0f} 秒"]
    if report.llm_cost_usd:
        footer.append(f"LLM 成本 ${report.llm_cost_usd:.3f}")
    footer.append(f"請求 {report.requests_used} 次")
    if report.schema_drift_count:
        footer.append(f"⚠️ schema drift {report.schema_drift_count} 筆")
    lines += ["", f"<i>{esc(' · '.join(footer))}</i>"]

    if report.warnings:
        lines += ["", "<b>▎警告</b>"]
        lines += [f"• {esc(w)}" for w in report.warnings[:5]]

    return "\n".join(lines)


def render_alert(title: str, lines: list[str]) -> str:
    """失敗告警。走 Telegram 不經過 104,所以被擋時一定送得到。"""
    out = [f"🚨 <b>{esc(title)}</b>", ""]
    out += [esc(line) for line in lines]
    return "\n".join(out)


def split_message(text: str, limit: int = SAFE_LIMIT) -> list[str]:
    """超長訊息切段。優先在空行切,其次換行,避免把 HTML tag 切開。"""
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    buf = ""
    for block in text.split("\n\n"):
        candidate = f"{buf}{block}\n\n"
        if len(candidate) > limit and buf:
            parts.append(buf.rstrip())
            buf = f"{block}\n\n"
        else:
            buf = candidate

    if buf.strip():
        parts.append(buf.rstrip())

    # 單一區塊本身就超長時,退回按行硬切
    out: list[str] = []
    for part in parts:
        while len(part) > limit:
            cut = part.rfind("\n", 0, limit)
            if cut <= 0:
                cut = limit
            out.append(part[:cut].rstrip())
            part = part[cut:].lstrip("\n")
        if part:
            out.append(part)
    return out
