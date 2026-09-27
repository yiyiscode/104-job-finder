"""Streamlit 入口。只負責畫面;規則與統計都在旁邊的純模組裡。

啟動:``jobfinder ui``(會強制綁 127.0.0.1)。直接 ``streamlit run`` 的話要自己帶
``--server.address 127.0.0.1``,否則 Streamlit 預設對整個區網公開。
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from jobfinder.hitrate.compute import resume_hash
from jobfinder.hitrate.store import HitRateStore
from jobfinder.webui import candidates as cand
from jobfinder.webui import trends
from jobfinder.webui.decisions import (
    DESIRE_LABELS,
    SKIP_REASONS,
    STATUS_LABELS,
    STATUSES,
    DecisionStore,
)
from jobfinder.webui.export import to_csv, to_markdown
from jobfinder.webui.gates import (
    GATE3_LEGEND,
    GATE_HELP_MD,
    Light,
    Training,
    gate3_label,
)
from jobfinder.webui.groups import (
    DEFAULT_INDUSTRIES,
    DEFAULT_TITLE_GROUPS,
    INDUSTRY_NAMES,
    TITLE_GROUP_NAMES,
)
from jobfinder.webui.rows import JobRow
from jobfinder.webui.skill_match import resume_skills
from jobfinder.webui.snapshot import connect_readonly, take_snapshot
from jobfinder.webui.tailoring import build_tailoring

LIGHT_ICONS = {Light.PASS: "✅", Light.WARN: "⚠️", Light.FAIL: "❌", Light.UNKNOWN: "❔"}
TRAINING_TEXT = {
    Training.FOUND: "培訓✅",
    Training.NOT_FOUND: "培訓未提",
    Training.UNKNOWN: "未判定",
}
DESIRE_UNRATED = "未評"


def gate2_label(job: JobRow) -> str:
    """🔴 紅燈原因 / ✅ 或 ❔ + 培訓訊號(培訓不是紅燈,只當附帶資訊)。"""
    if job.gates.signals:
        return "🔴 " + "·".join(job.gates.signals)
    return f"{LIGHT_ICONS[job.gates.gate2]} {TRAINING_TEXT[job.gates.training]}"


def desire_stars(value: int | None) -> str:
    return "★" * value if value else ""


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="jobfinder-ui")
    parser.add_argument("--data-dir", default="local_data")
    parser.add_argument("--resume", default="profile-de.md", help="命中率綁定的履歷檔")
    args, _ = parser.parse_known_args(sys.argv[1:])
    return args


ARGS = _args()
DATA_DIR = Path(ARGS.data_dir)
JOBS_DB = DATA_DIR / "jobs.db"
DECISIONS_DB = DATA_DIR / "decisions.db"
HITRATE_DB = DATA_DIR / "hitrate.db"
RESUME = Path(ARGS.resume)
SNAPSHOT_DIR = Path(tempfile.gettempdir()) / "jobfinder-webui"


@st.cache_data(show_spinner="讀取 jobs.db 快照…")
def _load(snapshot: str, hitrate_version: int, resume_version: str) -> list[JobRow]:
    hits = HitRateStore(HITRATE_DB).for_resume(resume_version) if hitrate_version else {}
    have = resume_skills(RESUME.read_text(encoding="utf-8")) if RESUME.exists() else None
    conn = connect_readonly(Path(snapshot))
    try:
        return cand.load_rows(conn, hits, have)
    finally:
        conn.close()


def load_rows() -> list[JobRow]:
    # 快取鍵:快照檔名(含 mtime+size)、hitrate.db 的 mtime、履歷版本 —— 任一變了就重讀
    hitrate_version = HITRATE_DB.stat().st_mtime_ns if HITRATE_DB.exists() else 0
    resume_version = (
        resume_hash(RESUME.read_text(encoding="utf-8")) if RESUME.exists() else "no-resume"
    )
    return _load(str(take_snapshot(JOBS_DB, SNAPSHOT_DIR)), hitrate_version, resume_version)


@st.cache_resource
def store() -> DecisionStore:
    return DecisionStore(DECISIONS_DB)


def today():
    return datetime.now(cand.TAIPEI).date()


def date_range(label: str, default, lo, hi, key: str):
    # 值只放 session_state、不傳給 date_input —— 兩邊都給的話,按鈕改值時 Streamlit 會警告
    if key not in st.session_state:
        st.session_state[key] = default

    def _select_all() -> None:
        st.session_state[key] = (lo, hi)

    rng = st.date_input(label, min_value=lo, max_value=hi, key=key)
    st.button("全部期間", key=f"{key}_all", on_click=_select_all)
    if isinstance(rng, tuple) and len(rng) == 2:
        return rng
    return default  # 使用者只點了起始日,範圍還沒選完


# ── 頁 1:候選清單 ──────────────────────────────────────────────────
def candidates_page() -> None:
    rows = load_rows()
    latest = store().latest()
    desires = store().latest_desires()
    first_day = min((r.first_seen for r in rows), default=today())
    statuses = sorted({r.pipeline_status for r in rows})

    with st.sidebar:
        st.header("篩選")
        start, end = date_range(
            "首次出現日期", (cand.week_start(today()), today()), first_day, today(), "c_date"
        )
        notified_only = st.toggle(
            "📨 只看推播過的",
            help="對照 Telegram 日報用。打開後只套用日期,下面的產業/職稱/閘門等條件全部不看",
        )
        off = notified_only  # 其他條件在這個模式下不生效,灰掉免得誤會
        f = cand.CandidateFilter(
            start=start,
            end=end,
            notified_only=notified_only,
            industries=st.multiselect(
                "產業", INDUSTRY_NAMES, default=DEFAULT_INDUSTRIES, disabled=off
            ),
            title_groups=st.multiselect(
                "職稱類別",
                TITLE_GROUP_NAMES,
                default=DEFAULT_TITLE_GROUPS,
                help="依職稱關鍵字歸類,由上往下第一個命中為準"
                "(資料工程優先,所以「AI 數據開發工程師」算資料工程)",
                disabled=off,
            ),
            title_query=st.text_input(
                "職稱關鍵字",
                placeholder="例:資料工程|ETL",
                help="不分大小寫;用 | 分隔表示「或」",
                disabled=off,
            ),
            gate=st.radio(
                "閘門", cand.GATE_FILTERS, index=1, help="規則見頁面上方的說明", disabled=off
            ),
            no_english_only=st.checkbox("只看不要求英文", disabled=off),
            pipeline_statuses=st.multiselect(
                "pipeline 狀態",
                statuses,
                default=statuses,
                help=cand.PIPELINE_STATUS_LEGEND,
                disabled=off,
            ),
            hide_skipped=st.checkbox("隱藏已標「不投」", value=True, disabled=off),
        )
        sort_key = st.selectbox("排序", list(cand.SORT_KEYS))

    view = cand.sort_rows(cand.apply_filter(rows, f, latest), sort_key)
    period = cand.in_range(rows, start, end)

    st.title("候選清單")
    with st.expander("📖 閘門規則說明:硬門檻 ／ 職缺訊號 ／ 命中率"):
        st.markdown(GATE_HELP_MD)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("期間新職缺", len(period))
    c2.metric("通過第 1 道", sum(r.gates.passes_through(1) for r in period))
    c3.metric("有深評分數", sum(r.deep_score is not None for r in period))
    c4.metric("已標記", sum(r.job_no in latest for r in period))

    st.caption(f"顯示 {len(view)} 筆 · 點一列可標記 · 欄位標題也可以點擊排序")
    st.caption(GATE3_LEGEND)
    table = pd.DataFrame(
        [
            {
                "decision": STATUS_LABELS[latest[r.job_no].status] if r.job_no in latest else "",
                "desire": desire_stars(desires.get(r.job_no)),
                "gate1": LIGHT_ICONS[r.gates.gate1],
                "gate2": gate2_label(r),
                "gate3": gate3_label(r),
                "score": r.deep_score,
                "company": r.company,
                "title": r.title,
                "title_group": r.title_group,
                "area": r.area,
                "employees": r.employees,
                "experience": r.experience,
                "education": r.education,
                "salary": r.salary_text,
                "english": r.english,
                "prog_rate": None if r.prog_rate is None else round(r.prog_rate * 100),
                "url": r.url,
                "fails": " · ".join(r.gates.fails),
                "flags": " · ".join(r.gates.flags),
                "pipeline": r.pipeline_status,
                "first_seen": r.first_seen,
            }
            for r in view
        ]
    )
    event = st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        height=460,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "decision": st.column_config.TextColumn("標記", width="small"),
            "desire": st.column_config.TextColumn(
                "想去", width="small", help="想去程度 1–5(你自己評的,選填)"
            ),
            "gate1": st.column_config.TextColumn(
                "①硬門檻",
                width="small",
                help="✅ 全過 · ⚠️ 有黃/紅標但不排除 · ❌ 排除(原因見「排除原因」欄)",
            ),
            "gate2": st.column_config.TextColumn(
                "②職缺訊號",
                width="small",
                help="🔴 SI產業 / 職類含行銷行政業務 → 卡在第 2 道。"
                "培訓✅/培訓未提 只是附帶資訊(沒寫 ≠ 沒有),不影響通過",
            ),
            "gate3": st.column_config.TextColumn(
                "③命中率(LLM)",
                width="medium",
                help=GATE3_LEGEND + "。LLM 逐條判定、程式算分",
            ),
            "company": "公司",
            "title": st.column_config.TextColumn("職稱", width="medium"),
            "title_group": st.column_config.TextColumn("職稱類別", width="small"),
            "area": "地點",
            "employees": st.column_config.NumberColumn("員工數", format="%d"),
            "experience": "經歷",
            "education": "學歷",
            "salary": "薪資",
            "english": st.column_config.CheckboxColumn("英文"),
            "score": st.column_config.NumberColumn("深評", format="%d"),
            "prog_rate": st.column_config.NumberColumn(
                "命中率(程式)",
                format="%d%%",
                help="技能詞典比對,每筆都有。只供參考、不參與閘門:分不出核心、沒有部分符合,"
                "沒全文的只看得到列表摘要",
            ),
            "url": st.column_config.LinkColumn("104", display_text="開啟"),
            "fails": "排除原因",
            "flags": "警示",
            "pipeline": st.column_config.TextColumn("pipeline", help=cand.PIPELINE_STATUS_LEGEND),
            "first_seen": st.column_config.DateColumn("首次出現"),
        },
    )

    selected = event.selection.rows if event else []
    if selected:
        _decision_panel(view[selected[0]])

    st.divider()
    chosen = cand.picks(rows, latest, start, end)
    st.subheader(f"匯出:期間內標「投」的 {len(chosen)} 筆")
    if chosen:
        md = to_markdown(chosen, latest)
        a, b = st.columns(2)
        a.download_button("下載 Markdown", md, file_name=f"picks-{start}.md", mime="text/markdown")
        b.download_button(
            "下載 CSV", to_csv(chosen, latest), file_name=f"picks-{start}.csv", mime="text/csv"
        )
        st.markdown(md)
    else:
        st.caption("還沒有標「投」的職缺。")


def _skill_match_breakdown(job: JobRow) -> None:
    m = job.skill_match
    if m is None:
        return
    head = "無可辨識技能" if m.rate is None else f"{m.rate:.0%}"
    with st.expander(f"命中率(程式){head} · 依{m.source}比對技能詞典"):
        st.markdown(f"**✅ 履歷有:** {'、'.join(m.matched) or '—'}")
        st.markdown(f"**❌ 履歷沒有(必備):** {'、'.join(m.missing) or '—'}")
        if m.preferred_missing:
            st.markdown(f"**加分但沒有:** {'、'.join(m.preferred_missing)}")
        st.caption(
            "只供參考,不參與閘門。詞典比對不懂等價經驗(自建排程 ≠ Airflow)、沒有部分符合;"
            + ("沒有全文,只比對了約 123 字的列表摘要。" if m.source == "摘要" else "")
        )


def _jd_original(job: JobRow) -> None:
    if not job.has_detail:
        with st.expander("JD(僅列表摘要 —— 這筆沒有抓全文,完整內容請開 104 連結)"):
            st.text(job.jd)
        # 放在 expander 外面(JD 下方):不用展開就點得到
        st.link_button("🔗 在 104 開啟完整 JD", job.url)
        return
    with st.expander("📄 104 原文:工作內容 + 條件要求", expanded=True):
        st.markdown(f"[🔗 在 104 開啟]({job.url})")
        st.markdown("##### 工作內容")
        st.text(job.jd_description or "(未提供)")
        st.markdown("##### 條件要求")
        for title, text in job.jd_conditions:
            if "\n" in text:
                st.markdown(f"**{title}**")
                st.text(text)
            else:
                st.markdown(f"**{title}**:{text}")


MATCH_LABELS = {"yes": "✅ 符合", "partial": "🟡 部分", "no": "❌ 不符"}


def _hit_rate_breakdown(job: JobRow) -> None:
    if not job.hit_requirements:
        st.caption(f"③ 命中率:{job.hit_rate_status}")
        return
    required = [r for r in job.hit_requirements if r.kind == "required"]
    head = "無明列必備" if job.hit_rate is None else f"{job.hit_rate:.0%}"
    with st.expander(f"③ 命中率 {head}(必備 {len(required)} 條)· 逐條判定", expanded=True):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "條件": ("⭐ " if r.core and r.kind == "required" else "") + r.item,
                        "類型": "必備" if r.kind == "required" else "加分",
                        "判定": MATCH_LABELS[r.match],
                        "履歷證據": r.evidence,
                    }
                    for r in sorted(job.hit_requirements, key=lambda r: r.kind != "required")
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        if job.hit_core_missed:
            st.error("核心條件不符,第 3 道不過:" + "、".join(job.hit_core_missed))
        st.caption(
            "命中率 =(符合 + 0.5 × 部分)÷ 必備條數;加分條件不進分母。⭐ = 核心條件。"
            f"判定:{job.hit_model}(claude-code = 手動補算,不經 API);數字由程式算。"
        )


def _deep_score_details(job: JobRow) -> None:
    """原本 Telegram 卡片上的內容。日報改成單則之後,完整版只在這裡看得到。"""
    if job.highlights:
        st.markdown("**為什麼適合你**")
        st.markdown("\n".join(f"- {h}" for h in job.highlights))
    if job.red_flags:
        st.markdown("**注意**")
        st.markdown("\n".join(f"- ⚠️ {r}" for r in job.red_flags))
    if job.resume_tip:
        st.markdown(f"**履歷建議** 💡 {job.resume_tip}")
    if job.score_parts:
        st.caption(
            "細項 " + " · ".join(f"{name} {got}/{full}" for name, got, full in job.score_parts)
        )
    if job.company_url:
        st.markdown(f"[🏢 這家公司的其他職缺]({job.company_url})")


def _decision_panel(job: JobRow) -> None:
    st.divider()
    st.subheader(f"{job.company} — {job.title}")
    left, right = st.columns([3, 2])
    with left:
        if job.deep_score is not None:
            st.info(f"深評 {job.deep_score} 分:{job.one_liner}")
            _deep_score_details(job)
        if job.gates.fails:
            st.error("第 1 道排除:" + " · ".join(job.gates.fails))
        if job.gates.flags:
            st.warning(" · ".join(job.gates.flags))
    with right:
        _decision_form(job)

    # 命中率與 104 原文並排:逐條判定要對著原文看才判斷得了對不對
    hit_col, jd_col = st.columns(2)
    with hit_col:
        _hit_rate_breakdown(job)
        _skill_match_breakdown(job)
    with jd_col:
        _jd_original(job)


def _decision_form(job: JobRow) -> None:
    key = job.job_no
    # 不預選:預設選「投」的話,只想評想去程度的人會順手存進一筆假的「投」
    status = st.radio(
        "標記",
        STATUSES,
        format_func=STATUS_LABELS.get,
        horizontal=True,
        index=None,
        key=f"st_{key}",
    )
    current_desire = store().latest_desires().get(key)
    desire = st.select_slider(
        "想去程度(選填,跟投/不投分開)",
        options=[DESIRE_UNRATED, *DESIRE_LABELS],
        value=current_desire or DESIRE_UNRATED,
        format_func=lambda v: DESIRE_LABELS.get(v, v),
        help="你主觀有多想去這家。之後拿來分析偏好,例如「很想去卻不投」通常代表卡在命中率",
        key=f"ds_{key}",
    )
    reason = None
    if status == "skip":
        reason = st.selectbox(
            "不投原因(必填)",
            SKIP_REASONS,
            index=None,
            placeholder="選一個原因",
            key=f"rs_{key}",
        )
    note = st.text_input("備註", key=f"nt_{key}")
    if st.button("儲存標記", type="primary", key=f"sv_{key}"):
        rated = desire != DESIRE_UNRATED and desire != current_desire
        if status is None and not rated:
            st.error("沒有要存的東西:選一個標記,或拉一下想去程度")
            return
        now = datetime.now(cand.TAIPEI)
        try:
            if status is not None:
                store().append(key, status, reason, note, now)
            if rated:
                store().rate(key, desire, now)
        except ValueError as exc:
            st.error(f"{exc} —— 不投原因是之後檢討閘門的唯一資料")
        else:
            st.rerun()
    history = store().history(key)
    if history:
        st.caption("標記歷史(只追加)")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "時間": d.decided_at,
                        "標記": STATUS_LABELS[d.status],
                        "原因": d.reason or "",
                        "備註": d.note,
                    }
                    for d in history
                ]
            ),
            hide_index=True,
        )


# ── 頁:已標投遞 ──────────────────────────────────────────────────
def applied_page() -> None:
    rows = load_rows()
    latest = store().latest()
    desires = store().latest_desires()
    applied = [r for r in rows if (d := latest.get(r.job_no)) is not None and d.status == "apply"]
    applied.sort(key=lambda r: latest[r.job_no].decided_at, reverse=True)

    st.title("已標投遞")
    st.caption(f"最新標記為「投」的職缺 {len(applied)} 筆(不限日期)· 點一列看履歷修改建議")
    if not applied:
        st.info("還沒有標「投」的職缺。到「候選清單」點一列、標記「✅ 投」後就會出現在這裡。")
        return

    table = pd.DataFrame(
        [
            {
                "decided_at": latest[r.job_no].decided_at[:16].replace("T", " "),
                "desire": desire_stars(desires.get(r.job_no)),
                "company": r.company,
                "title": r.title,
                "gate3": gate3_label(r),
                "score": r.deep_score,
                "area": r.area,
                "salary": r.salary_text,
                "note": latest[r.job_no].note,
                "url": r.url,
            }
            for r in applied
        ]
    )
    event = st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "decided_at": "標記時間",
            "desire": st.column_config.TextColumn(
                "想去", width="small", help="想去程度 1–5;空白 = 還沒評,到候選清單點該職缺評分"
            ),
            "company": "公司",
            "title": st.column_config.TextColumn("職稱", width="medium"),
            "gate3": st.column_config.TextColumn("③命中率(LLM)", width="medium"),
            "score": st.column_config.NumberColumn("深評", format="%d"),
            "area": "地點",
            "salary": "薪資",
            "note": "備註",
            "url": st.column_config.LinkColumn("104", display_text="開啟"),
        },
    )
    a, b = st.columns(2)
    a.download_button("下載 Markdown", to_markdown(applied, latest), file_name="applied.md")
    b.download_button("下載 CSV", to_csv(applied, latest), file_name="applied.csv", mime="text/csv")

    selected = event.selection.rows if event else []
    if selected:
        job = applied[selected[0]]
        st.divider()
        st.subheader(f"{job.company} — {job.title}")
        left, right = st.columns(2)
        with left:
            _tailoring_panel(job)
        with right:
            _jd_original(job)


def _tailoring_panel(job: JobRow) -> None:
    t = build_tailoring(job)
    st.markdown("#### ✍️ 履歷修改建議")
    if t.empty:
        st.caption("這筆沒有命中率逐條判定也沒有深評,只能對照右邊的 104 原文自己比對。")
    if t.resume_tip:
        st.info(f"**深評建議:** {t.resume_tip}")
    if t.actions:
        st.markdown("**要補強的(必備條件中部分符合／不符)**")
        for text in t.actions:
            st.markdown(f"- {text}")
    if t.emphasize:
        st.markdown("**要強調的(已符合,自傳裡點名這些證據)**")
        for item, evidence in t.emphasize:
            st.markdown(f"- **{item}** —— {evidence}")
    if t.highlights:
        st.markdown("**深評看到的亮點**")
        for h in t.highlights:
            st.markdown(f"- {h}")
    if t.red_flags:
        st.markdown("**面試要準備說明的(深評紅旗)**")
        for f in t.red_flags:
            st.markdown(f"- {f}")
    if t.preferred_gaps:
        st.caption("加分條件沒有的:" + "、".join(t.preferred_gaps))
    if t.missing_skills:
        st.caption("JD 提到、履歷技能表沒寫的(程式比對,供參考):" + "、".join(t.missing_skills))


# ── 頁 2:技能趨勢 ──────────────────────────────────────────────────
def _bar(data: pd.DataFrame, color: str) -> alt.Chart:
    return (
        alt.Chart(data)
        .mark_bar(color=color)
        .encode(
            x=alt.X("rate:Q", title="出現率", axis=alt.Axis(format="%")),
            y=alt.Y("skill:N", sort="-x", title=None),
            tooltip=[
                alt.Tooltip("skill:N", title="技能"),
                alt.Tooltip("rate:Q", title="出現率", format=".1%"),
                alt.Tooltip("count:Q", title="筆數"),
            ],
        )
        .properties(height=420)
    )


def _rates_frame(rows: list[JobRow], layer: str) -> pd.DataFrame:
    return pd.DataFrame(
        [vars(s) for s in trends.skill_rates(rows, layer)], columns=["skill", "count", "rate"]
    )


def trends_page() -> None:
    everything = load_rows()
    lo = min((r.first_seen for r in everything), default=today())
    hi = max((r.first_seen for r in everything), default=today())
    with st.sidebar:
        st.header("分析範圍")
        start, end = date_range("首次出現日期", (lo, hi), lo, hi, "t_date")
        industries = st.multiselect("產業", INDUSTRY_NAMES, default=DEFAULT_INDUSTRIES, key="t_ind")
        groups = st.multiselect(
            "職稱類別", TITLE_GROUP_NAMES, default=DEFAULT_TITLE_GROUPS, key="t_grp"
        )

    rows = [
        r
        for r in cand.in_range(everything, start, end)
        if r.industry in industries and r.title_group in groups
    ]
    st.title("技能趨勢")
    if not rows:
        st.warning("這個範圍沒有職缺。")
        return
    n = len(rows)
    detail_n = len(trends.layer_rows(rows, trends.DETAIL_LAYER))
    st.caption(
        f"範圍:{start} → {end} · {'、'.join(industries)} × {'、'.join(groups)} · "
        f"職缺 {n:,}/{len(everything):,} 筆 · 全文 {detail_n:,} 筆({detail_n / n:.0%})"
    )
    if detail_n < trends.SMALL_SAMPLE:
        st.warning(
            f"全文層只有 {detail_n} 筆,每 1 筆就差 {1 / max(detail_n, 1):.0%},只看方向不要看數字。"
        )

    _group_heatmap(rows)

    st.subheader("技能出現率 —— 兩層分開報,不合併")
    a, b = st.columns(2)
    for col, layer, color, label in (
        (a, trends.SUMMARY_LAYER, "#4C78A8", f"N={n:,}(母體估計)"),
        (b, trends.DETAIL_LAYER, "#F58518", f"N={detail_n:,}(細節)"),
    ):
        with col:
            st.markdown(f"**{layer}** · {label}")
            st.caption(trends.BIAS_NOTES[layer])
            data = _rates_frame(rows, layer)
            if data.empty:
                st.caption("這一層沒有資料。")
            else:
                st.altair_chart(_bar(data, color), width="stretch")

    st.subheader("隨週變化(摘要層)")
    top = [s.skill for s in trends.skill_rates(rows, trends.SUMMARY_LAYER, 8)]
    weekly = pd.DataFrame(trends.weekly_rates(rows, top))
    if not weekly.empty:
        weekly["week"] = weekly["week"].astype(str)
        n_by_week = weekly.groupby("week")["n"].first()
        st.caption(
            "每週 N:"
            + " · ".join(f"{w} N={c}" for w, c in n_by_week.items())
            + "。"
            + trends.BIAS_NOTES["weekly"]
        )
        st.altair_chart(
            alt.Chart(weekly)
            .mark_line(point=True)
            .encode(
                x=alt.X("week:N", title="週(週一起算)"),
                y=alt.Y("rate:Q", title="出現率", axis=alt.Axis(format="%")),
                color=alt.Color("skill:N", title="技能"),
                tooltip=["week", "skill", alt.Tooltip("rate:Q", format=".1%"), "n"],
            )
            .properties(height=320),
            width="stretch",
        )

    a, b = st.columns(2)
    with a:
        st.subheader("薪資分佈(月薪換算)")
        s = trends.salary_stats(rows)
        st.caption(
            f"揭露率 {s.disclosure_rate:.0%}(N={len(s.lows):,}/{s.n:,})。"
            f"上限只算有明確上限的 N={len(s.highs):,};「以上」型 {s.open_ended} 筆不列入上限。"
            + trends.BIAS_NOTES["salary"]
        )
        sal = pd.DataFrame(
            [{"月薪": v, "類型": "下限"} for v in s.lows]
            + [{"月薪": v, "類型": "上限"} for v in s.highs]
        )
        if not sal.empty:
            sal = sal[sal["月薪"] <= 250_000]
            st.altair_chart(
                alt.Chart(sal)
                .mark_bar(opacity=0.6)
                .encode(
                    x=alt.X("月薪:Q", bin=alt.Bin(step=10_000), axis=alt.Axis(format=",.0f")),
                    y=alt.Y("count():Q", stack=None, title="筆數"),
                    color=alt.Color("類型:N", scale=alt.Scale(range=["#4C78A8", "#F58518"])),
                )
                .properties(height=300),
                width="stretch",
            )
    with b:
        st.subheader("公司規模分佈")
        dist = pd.DataFrame(trends.size_distribution(rows), columns=["規模", "筆數"])
        unknown = int(dist.loc[dist["規模"] == trends.SIZE_UNKNOWN, "筆數"].iloc[0])
        st.caption(f"N={n:,} · 未提供 {unknown / n:.0%}。" + trends.BIAS_NOTES["size"])
        st.altair_chart(
            alt.Chart(dist)
            .mark_bar(color="#54A24B")
            .encode(
                x=alt.X("規模:N", sort=list(dist["規模"]), title=None),
                y="筆數:Q",
                tooltip=["規模", "筆數"],
            )
            .properties(height=300),
            width="stretch",
        )


def _group_heatmap(rows: list[JobRow]) -> None:
    st.subheader("分群比較 —— 同一個技能在不同群裡的出現率")
    a, b = st.columns([1, 3])
    with a:
        by = st.radio("分群依據", ["職稱類別", "產業"], key="t_by")
        layer = st.radio(
            "資料層",
            trends.LAYERS,
            key="t_layer",
            help="全文層只有粗篩通過的職缺才有,會偏向履歷已有的技能",
        )
    key = (lambda r: r.title_group) if by == "職稱類別" else (lambda r: r.industry)
    skills, cells = trends.group_matrix(rows, key, layer)
    with b:
        if not cells:
            st.caption("這一層沒有資料。")
            return
        data = pd.DataFrame(cells)
        data["label"] = data["group"] + " (N=" + data["n"].astype(str) + ")"
        base = alt.Chart(data).encode(
            x=alt.X("label:N", title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("skill:N", sort=skills, title=None),
        )
        heat = base.mark_rect().encode(
            color=alt.Color(
                "rate:Q",
                title="出現率",
                scale=alt.Scale(scheme="blues"),
                legend=alt.Legend(format="%"),
            ),
            tooltip=["group", "skill", alt.Tooltip("rate:Q", format=".1%"), "n"],
        )
        text = base.mark_text(fontSize=11).encode(
            text=alt.Text("rate:Q", format=".0%"),
            color=alt.condition("datum.rate > 0.4", alt.value("white"), alt.value("black")),
        )
        st.altair_chart(heat + text, width="stretch")
        st.caption(
            f"{layer} · 每欄標了該群 N。**N < {trends.SMALL_SAMPLE} 的群出現率不穩定。**"
            + trends.BIAS_NOTES[layer]
        )


def main() -> None:
    st.set_page_config(page_title="Job Finder", page_icon="📋", layout="wide")
    if not JOBS_DB.exists():
        st.error(f"找不到 {JOBS_DB} —— pipeline 還沒跑過,或 --data-dir 指錯了。")
        st.stop()
    st.sidebar.caption("資料是 jobs.db 的唯讀快照;標記寫在 decisions.db。這個介面不會連線 104。")
    st.navigation(
        [
            st.Page(candidates_page, title="候選清單", icon="📋", default=True),
            st.Page(applied_page, title="已標投遞", icon="✅"),
            st.Page(trends_page, title="技能趨勢", icon="📈"),
        ]
    ).run()


main()
