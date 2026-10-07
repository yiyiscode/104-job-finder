"""自評 app(方案 B:逐筆卡片)。跟 Job Finder UI 畫面分開、資料共用 decisions.db。

一次排一批約 30 張卡片;每評一張就寫進 db,中斷不會掉資料,下次自動跳過評完的。
啟動:``python scripts/selfeval_ui.py``(只綁 127.0.0.1)。

跟 app.py 一樣:jobs.db 只讀快照(docs/adr/0001),不連 104。
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from jobfinder.hitrate.compute import resume_hash
from jobfinder.hitrate.store import HitRateStore
from jobfinder.shortlist.store import SHORTLIST_DB, ShortlistStore
from jobfinder.webui import candidates as cand
from jobfinder.webui import selfeval as se
from jobfinder.webui.decisions import DESIRE_LABELS, DecisionStore
from jobfinder.webui.gates import gate3_label
from jobfinder.webui.rows import JobRow
from jobfinder.webui.skill_match import resume_skills
from jobfinder.webui.snapshot import connect_readonly, take_snapshot


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="jobfinder-selfeval")
    parser.add_argument("--data-dir", default="local_data")
    parser.add_argument("--resume", default="profile-de.md", help="技能自評要寫回的履歷檔")
    args, _ = parser.parse_known_args(sys.argv[1:])
    return args


ARGS = _args()
DATA_DIR = Path(ARGS.data_dir)
RESUME = Path(ARGS.resume)
SNAPSHOT_DIR = Path(tempfile.gettempdir()) / "jobfinder-selfeval"
SOURCE_LABELS = {"sample": "🎲 低分抽樣", "notified": "📨 推播過", "scored": "⭐ 有深評"}


@st.cache_data(show_spinner="讀取 jobs.db 快照…")
def _load(snapshot: str, hit_version: int, resume_version: str) -> list[JobRow]:
    hit_db = DATA_DIR / "hitrate.db"
    hits = HitRateStore(hit_db).for_resume(resume_version) if hit_version else {}
    shortlist_db = DATA_DIR / SHORTLIST_DB
    shortlist = ShortlistStore(shortlist_db) if shortlist_db.exists() else None
    conn = connect_readonly(Path(snapshot))
    try:
        # have_skills=None:不算程式版命中率(佔載入時間 85%,這裡用不到)
        return cand.load_rows(conn, hits, None, shortlist=shortlist)
    finally:
        conn.close()


def load_rows() -> list[JobRow]:
    hit_db = DATA_DIR / "hitrate.db"
    text = RESUME.read_text(encoding="utf-8") if RESUME.exists() else ""
    return _load(
        str(take_snapshot(DATA_DIR / "jobs.db", SNAPSHOT_DIR)),
        hit_db.stat().st_mtime_ns if hit_db.exists() else 0,
        resume_hash(text) if text else "no-resume",
    )


@st.cache_resource
def store() -> DecisionStore:
    return DecisionStore(DATA_DIR / "decisions.db")


def now() -> datetime:
    return datetime.now(cand.TAIPEI)


# ── 職缺卡片 ──────────────────────────────────────────────────────
def _jd_panel(job: JobRow) -> None:
    if job.has_detail:
        st.markdown("#### 📄 JD 全文")
        if job.jd_description:
            st.markdown(job.jd_description.replace("\n", "  \n"))
        for title, text in job.jd_conditions:
            st.markdown(f"**{title}**  \n" + text.replace("\n", "  \n"))
    else:
        st.markdown("#### 📄 JD 摘要")
        st.caption("這筆沒有抓全文,下面是 104 列表上的摘要 —— 完整內容請開 104")
        st.markdown((job.jd or "(沒有摘要)").replace("\n", "  \n"))


def _llm_panel(job: JobRow) -> None:
    st.markdown("#### 🤖 LLM 評分")
    verdict = se.VERDICTS.get(job.verdict, job.verdict or "—")
    st.markdown(f"### {job.deep_score} 分 · {verdict}")
    st.caption(f"{se.SCORE_BANDS} · 模型:{job.score_model or '—'}")
    parts = {name: (got, full) for name, got, full in job.score_parts}
    for _, label, full, rule in se.DIMENSIONS:
        got = parts.get(label, (None, full))[0]
        if got is None:
            continue
        st.progress(got / full, text=f"**{label}** {got} / {full}")
        st.caption(rule)
    if job.one_liner:
        st.info(job.one_liner)
    if job.highlights:
        st.markdown("**為什麼適合(LLM 的理由)**")
        st.markdown("\n".join(f"- {h}" for h in job.highlights))
    if job.red_flags:
        st.markdown("**注意**")
        st.markdown("\n".join(f"- ⚠️ {r}" for r in job.red_flags))
    if job.resume_tip:
        st.markdown(f"**履歷建議** 💡 {job.resume_tip}")
    if job.adjustments:
        st.caption("程式端校正:" + ";".join(job.adjustments))


def _rating_panel(job: JobRow, n: int, desires: dict, feedback: dict) -> None:
    with st.container(border=True):
        st.markdown("#### ✍️ 你的評分")
        stars = st.feedback(
            "stars",
            key=f"st_{job.job_no}",
            default=desires[job.job_no] - 1 if job.job_no in desires else None,
        )
        st.caption("想去程度:" + " · ".join(f"{k} {v}" for k, v in DESIRE_LABELS.items()))
        fb = st.segmented_control(
            f"以你的想去程度來看,{job.deep_score} 分…",
            list(se.FEEDBACK),
            format_func=se.FEEDBACK.get,
            default=feedback.get(job.job_no),
            key=f"fb_{job.job_no}",
        )
        a, b, c = st.columns(3)
        if a.button("⬅️ 上一張", width="stretch", disabled=st.session_state.pos == 0):
            st.session_state.pos -= 1
            st.rerun()
        if b.button("跳過", width="stretch"):
            st.session_state.pos = min(st.session_state.pos + 1, n)
            st.rerun()
        if c.button(
            "儲存 → 下一張", type="primary", width="stretch", disabled=stars is None and fb is None
        ):
            t = now()
            if stars is not None and desires.get(job.job_no) != stars + 1:
                store().rate(job.job_no, stars + 1, t)
            if fb and feedback.get(job.job_no) != fb:
                store().give_feedback(job.job_no, fb, job.deep_score, job.score_model, t)
            st.session_state.pos = min(st.session_state.pos + 1, n)
            st.rerun()


def jobs_mode(rows: list[JobRow]) -> None:
    desires, feedback = store().latest_desires(), store().latest_feedback()
    if "queue" not in st.session_state:
        # 一批排好就固定:評完的卡片不會從佇列消失,上一張/下一張的位置才不會跳
        cards = se.build_queue(rows, desires, feedback, cand.week_start(now().date()))
        st.session_state.queue = [c.job.job_no for c in cards]
        st.session_state.sources = {c.job.job_no: c.source for c in cards}
        st.session_state.pos = 0
    by_no = {r.job_no: r for r in rows}
    queue = [j for j in st.session_state.queue if j in by_no]
    pos = st.session_state.pos
    done = sum(j in desires and j in feedback for j in queue)
    st.progress(
        min(pos / max(len(queue), 1), 1.0), text=f"本批 {pos} / {len(queue)} · 已完成 {done}"
    )
    if pos >= len(queue):
        st.success(f"這一批看完了 🎉(完成 {done} 張)")
        if st.button("🔄 再排一批"):
            for k in ("queue", "sources", "pos"):
                st.session_state.pop(k, None)
            st.rerun()
        return
    job = by_no[queue[pos]]
    source = SOURCE_LABELS[st.session_state.sources.get(job.job_no, "scored")]
    head, link = st.columns([5, 1], vertical_alignment="bottom")
    head.markdown(f"## {job.company}\n**{job.title}**")
    link.link_button("🔗 在 104 開啟", job.url, type="primary", width="stretch")
    meta = [
        source,
        job.area or "地點未提供",
        job.salary_text or "待遇面議",
        job.experience,
        job.education or "學歷不拘",
        f"{job.employees:,} 人" if job.employees else "人數未提供",
        job.industry_desc,
        f"首次出現 {job.first_seen:%m/%d}",
        f"③命中率 {gate3_label(job)}",
    ]
    st.caption(" · ".join(m for m in meta if m))
    left, right = st.columns([3, 2], gap="large")
    with right:
        _rating_panel(job, len(queue), desires, feedback)
        _llm_panel(job)
    with left:
        _jd_panel(job)


# ── 技能自評 ──────────────────────────────────────────────────────
def skills_mode(rows: list[JobRow]) -> None:
    text = RESUME.read_text(encoding="utf-8") if RESUME.exists() else ""
    have, missing, demand = se.skill_lists(rows, resume_skills(text))
    queue = have + missing
    rated = store().latest_skills()
    st.session_state.setdefault("skill_pos", 0)
    pos = st.session_state.skill_pos
    st.caption("刻度:" + " · ".join(f"{k} {v}" for k, v in se.SCALE.items()))
    st.progress(min(pos / len(queue), 1.0), text=f"{pos} / {len(queue)} · 已評 {len(rated)}")
    if pos < len(queue):
        name = queue[pos]
        with st.container(border=True):
            st.markdown(f"## {name}")
            st.caption(
                ("履歷上有" if name in have else "履歷沒寫")
                + f" · 出現在 {demand[name]} 筆有深評的職缺摘要"
                + (f" · 目前自評 {rated[name]}" if name in rated else "")
            )
            for level, label in se.SCALE.items():
                if st.button(
                    f"{level} · {label}",
                    key=f"sk_{name}_{level}",
                    width="stretch",
                    type="primary" if rated.get(name) == level else "secondary",
                ):
                    store().rate_skill(name, level, now())
                    st.session_state.skill_pos += 1
                    st.rerun()
            a, b = st.columns(2)
            if a.button("⬅️ 上一個", width="stretch", disabled=pos == 0):
                st.session_state.skill_pos -= 1
                st.rerun()
            if b.button("沒碰過 / 跳過", width="stretch"):
                if name in rated:
                    store().rate_skill(name, None, now())
                st.session_state.skill_pos += 1
                st.rerun()
    else:
        st.success("技能都看過一輪了。")
        if st.button("從頭再看一次"):
            st.session_state.skill_pos = 0
            st.rerun()

    st.divider()
    st.markdown("#### 📝 寫進履歷")
    updated = se.apply_skills_block(text, rated)
    if updated == text:
        st.caption("履歷裡的自評區塊已經是最新的。")
        return
    st.warning(
        f"寫入 {RESUME} 後履歷內容改變 → **命中率會全部重算**(每日排程每次補 30 筆),"
        "深評只影響之後的新職缺。評完一批再寫,不要評一個寫一次。"
    )
    with st.expander("預覽要寫入的區塊"):
        st.code(se.skills_block(rated) if rated else "(清空自評區塊)", language="markdown")
    if st.button(f"✍️ 寫入履歷({len(rated)} 項)", type="primary"):
        RESUME.write_text(updated, encoding="utf-8")
        st.success("已寫入。")
        st.rerun()


# ── 報表 ──────────────────────────────────────────────────────────
def report_mode(rows: list[JobRow]) -> None:
    desires, feedback = store().latest_desires(), store().latest_feedback()
    m = se.metrics(rows, desires, feedback)
    st.caption("「準」= 深評分數貼近你的想去程度。N 少於 10 只看方向不看數字。")
    a, b, c, d = st.columns(4)
    a.metric("可用標記", f"{len(m.labeled)} / {se.TARGET_LABELS}", help="有深評分數又評了想去程度")
    b.metric("正例(想去 ≥4)", m.positives)
    c.metric("反例(想去 ≤2)", m.negatives)
    d.metric(
        "排序一致度",
        "—" if m.rho is None else f"{m.rho:+.2f}",
        help="Spearman:+1 = 分數高低完全照你的想去程度排;0 = 沒關係",
    )
    st.progress(min(len(m.labeled) / se.TARGET_LABELS, 1.0), text="校正模型進度")
    st.markdown(
        "**分數準不準**:"
        + " · ".join(f"{label} {m.feedback.get(k, 0)}" for k, label in se.FEEDBACK.items())
    )
    if m.labeled:
        st.scatter_chart(pd.DataFrame(m.labeled, columns=["深評", "想去"]), x="深評", y="想去")
    st.markdown("**每日推播前 5 名,你評幾則 ≥4 星**")
    st.dataframe(pd.DataFrame(se.daily_precision(rows, desires)), hide_index=True, width="stretch")


def main() -> None:
    st.set_page_config(page_title="自評", page_icon="✍️", layout="wide")
    rows = load_rows()
    st.title("✍️ 自評")
    mode = st.segmented_control(
        "模式",
        ["jobs", "skills", "report"],
        default="jobs",
        required=True,
        format_func={"jobs": "📏 職缺", "skills": "🎯 技能", "report": "📊 報表"}.get,
        label_visibility="collapsed",
    )
    {"jobs": jobs_mode, "skills": skills_mode, "report": report_mode}[mode](rows)
    st.sidebar.caption(
        "資料跟 Job Finder 共用 decisions.db(想去程度兩邊互通);jobs.db 只讀快照,不連 104。"
    )


main()
