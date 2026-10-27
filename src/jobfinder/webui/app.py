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

from jobfinder.company.schema import FACETS, TONE_ICONS, company_key
from jobfinder.company.store import COMPANIES_DB, CompanyStore, StoredCompany
from jobfinder.company.targets import interested_companies
from jobfinder.hitrate.compute import resume_hash
from jobfinder.hitrate.store import HitRateStore
from jobfinder.shortlist.store import SHORTLIST_DB, ShortlistStore
from jobfinder.webui import candidates as cand
from jobfinder.webui import planning, trends
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
SHORTLIST_PATH = DATA_DIR / SHORTLIST_DB
COMPANIES_PATH = DATA_DIR / COMPANIES_DB
PROJECT_ROOT = Path(__file__).resolve().parents[3]
RESUME = Path(ARGS.resume)
SNAPSHOT_DIR = Path(tempfile.gettempdir()) / "jobfinder-webui"


@st.cache_data(show_spinner="讀取 jobs.db 快照…")
def _load(
    snapshot: str, hitrate_version: int, resume_version: str, shortlist_version: int
) -> list[JobRow]:
    hits = HitRateStore(HITRATE_DB).for_resume(resume_version) if hitrate_version else {}
    have = resume_skills(RESUME.read_text(encoding="utf-8")) if RESUME.exists() else None
    shortlist = ShortlistStore(SHORTLIST_PATH) if shortlist_version else None
    conn = connect_readonly(Path(snapshot))
    try:
        return cand.load_rows(conn, hits, have, shortlist=shortlist)
    finally:
        conn.close()


def load_rows() -> list[JobRow]:
    # 快取鍵:快照檔名(含 mtime+size)、hitrate.db 與 shortlist.db 的 mtime、履歷版本
    # —— 任一變了就重讀
    hitrate_version = HITRATE_DB.stat().st_mtime_ns if HITRATE_DB.exists() else 0
    shortlist_version = SHORTLIST_PATH.stat().st_mtime_ns if SHORTLIST_PATH.exists() else 0
    resume_version = (
        resume_hash(RESUME.read_text(encoding="utf-8")) if RESUME.exists() else "no-resume"
    )
    return _load(
        str(take_snapshot(JOBS_DB, SNAPSHOT_DIR)),
        hitrate_version,
        resume_version,
        shortlist_version,
    )


def marks(rows: list[JobRow]):
    """(aliases, 最新標記, 想去程度)。投遞清單時期用 detail_id 存的,併到 jobs.db 的 job_no。"""
    aliases = cand.detail_aliases(rows)
    return aliases, store().latest(aliases), store().latest_desires(aliases)


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


TITLE_ALL = "全部"


def _title_group_choice(disabled: bool) -> list[str]:
    """職稱類別單選(2026-10-08 由多選改單選);「全部」= 不限。"""
    options = [TITLE_ALL, *TITLE_GROUP_NAMES]
    choice = st.selectbox(
        "職稱類別",
        options,
        index=options.index(DEFAULT_TITLE_GROUPS[0]),
        help="依職稱關鍵字歸類,由上往下第一個命中為準"
        "(資料工程優先,所以「AI 數據開發工程師」算資料工程)",
        disabled=disabled,
    )
    return list(TITLE_GROUP_NAMES) if choice == TITLE_ALL else [choice]


# ── 頁 1:候選清單 ──────────────────────────────────────────────────
def candidates_page() -> None:
    rows = load_rows()
    _, latest, desires = marks(rows)
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
            # 候選清單的產業預設全選(2026-10-08);技能趨勢頁仍預設單一產業,方便聚焦
            industries=st.multiselect("產業", INDUSTRY_NAMES, default=INDUSTRY_NAMES, disabled=off),
            title_groups=_title_group_choice(off),
            title_query=st.text_input(
                "職稱關鍵字",
                placeholder="例:資料工程|ETL",
                help="不分大小寫;用 | 分隔表示「或」",
                disabled=off,
            ),
            gate=st.radio(
                "閘門", cand.GATE_FILTERS, index=1, help="規則見頁面上方的說明", disabled=off
            ),
            deep_only=st.checkbox(
                "只看有深評的",
                value=True,
                help="進過 LLM 深評、有深評分數的職缺。沒深評的多半在規則層或粗篩就被刷掉了",
                disabled=off,
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
                "score_status": r.score_status,
                "industry": r.industry_desc or r.industry,
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
            "industry": st.column_config.TextColumn(
                "產業", width="medium", help="104 的公司產業;側欄「產業」篩的是它歸成的大類"
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
            "score_status": st.column_config.TextColumn(
                "未評原因",
                width="small",
                help="沒有深評分數的原因。待抓全文/待深評 = 排程會自己補上;"
                "粗篩淘汰/規則層淘汰 = 當初被刷掉,不會再評(除非加進投遞清單)",
            ),
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
        _decision_panel(view[selected[0]], desires)


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


def _analysis_summary(job: JobRow) -> None:
    if job.deep_score is not None:
        st.info(f"深評 {job.deep_score} 分:{job.one_liner}")
        _deep_score_details(job)
    else:
        st.caption(f"深評:{job.score_status}")
    if job.gates.fails:
        st.error("第 1 道排除:" + " · ".join(job.gates.fails))
    if job.gates.flags:
        st.warning(" · ".join(job.gates.flags))


def _analysis_columns(job: JobRow) -> None:
    # 命中率與 104 原文並排:逐條判定要對著原文看才判斷得了對不對
    hit_col, jd_col = st.columns(2)
    with hit_col:
        _hit_rate_breakdown(job)
        _skill_match_breakdown(job)
    with jd_col:
        _jd_original(job)


def _decision_panel(job: JobRow, desires: dict[str, int]) -> None:
    st.divider()
    st.subheader(f"{job.company} — {job.title}")
    left, right = st.columns([3, 2])
    with left:
        _analysis_summary(job)
    with right:
        _decision_form(job, desires)
    _analysis_columns(job)


@st.dialog("職缺分析", width="large")
def _analysis_dialog(job: JobRow) -> None:
    """已標投遞頁看分析用:跟候選清單點一列看到的一樣,只是不放標記表單。"""
    st.subheader(f"{job.company} — {job.title}")
    tab_job, tab_company = st.tabs(["📊 職缺分析", "🏢 公司"])
    with tab_job:
        _analysis_summary(job)
        _analysis_columns(job)
    with tab_company:
        _company_panel(job.company, CompanyStore(COMPANIES_PATH).all(), f"dlg_{job.job_no}")


# ── 公司評價 ──────────────────────────────────────────────────────
#: 按下查詢到 CLI 把狀態寫成 running 之間有幾秒空窗,這段時間也當作查詢中,免得連按兩次
LAUNCH_GRACE_SECONDS = 90
#: UI 不載入 config;跟 config.yaml 的 company.ttl_days 預設值一致,只影響「過期」標示
UI_TTL_DAYS = 90


def _launch_company_review(name: str) -> None:
    """另開背景程序跑 ``jobfinder company review``。

    UI 不自己呼叫 claude:一家要 1–3 分鐘,Streamlit 的一次 rerun 撐不住,
    而且關掉 UI 也不該把查到一半的結果丟掉。這條路只打 claude、不碰 104 與 jobs.db。
    """
    import os
    import subprocess

    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    log = (DATA_DIR / "company_review.log").open("a", encoding="utf-8")
    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "jobfinder.cli",
            "--config",
            str(PROJECT_ROOT / "config" / "config.yaml"),
            "--data-dir",
            str(DATA_DIR.resolve()),
            "company",
            "review",
            name,
        ],  # fmt: skip
        cwd=PROJECT_ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        creationflags=flags,
        start_new_session=sys.platform != "win32",
    )
    st.session_state.setdefault("company_launched", {})[company_key(name)] = datetime.now(
        cand.TAIPEI
    )


def _company_running(key: str, stored: StoredCompany | None, now: datetime) -> bool:
    launched = st.session_state.get("company_launched", {}).get(key)
    if launched and (now - launched).total_seconds() < LAUNCH_GRACE_SECONDS:
        return True
    return stored is not None and stored.running(now)


def _company_panel(name: str, all_stored: dict[str, StoredCompany], widget_key: str) -> None:
    key = company_key(name)
    s = all_stored.get(key)
    now = datetime.now(cand.TAIPEI)
    running = _company_running(key, s, now)

    if running:
        st.info("🔎 查詢中(claude 網搜,約 1–3 分鐘)。可以先做別的,回來按重新整理。")
        if st.button("🔄 重新整理", key=f"co_refresh_{widget_key}"):
            st.rerun()
    elif s is not None and s.status == "error":
        st.warning(f"上次查詢失敗:{s.error}")

    review = s.review if s else None
    if review is None:
        if not running:
            st.caption("這家還沒有評價資料。排程每天會補查幾家;急的話直接查。")
            if st.button("🔎 查詢公司評價", type="primary", key=f"co_go_{widget_key}"):
                _launch_company_review(name)
                st.rerun()
        return

    st.info(f"{TONE_ICONS[review.tone]} {review.overall}")
    st.markdown("**🔗 徵才官網**")
    if review.career_url:
        st.link_button("開啟徵才官網", review.career_url)
    else:
        st.caption("❌ 沒找到公司自己的徵才官網")
    if review.career_note:
        st.caption(review.career_note)
    for field_name, label in FACETS:
        facet = getattr(review, field_name)
        st.markdown(f"**{TONE_ICONS[facet.tone]} {label}**")
        st.markdown(facet.summary)
    if review.sources:
        st.markdown("**來源**")
        st.markdown("\n".join(f"- [{src.title}]({src.url})({src.date})" for src in review.sources))
    foot = st.columns([4, 1], vertical_alignment="center")
    foot[0].caption(
        f"查詢日 {s.reviewed_at:%Y-%m-%d} · {s.model} · "
        "claude 網搜整理,可能誤讀 —— 重要的點進來源核對"
    )
    if not running and foot[1].button("🔄 重查", key=f"co_again_{widget_key}"):
        _launch_company_review(name)
        st.rerun()


def companies_page() -> None:
    rows = load_rows()
    _, latest, _ = marks(rows)
    companies = interested_companies(rows, latest)
    all_stored = CompanyStore(COMPANIES_PATH).all()
    now = datetime.now(cand.TAIPEI)

    st.title("公司評價")
    done = sum(1 for c in companies if (s := all_stored.get(c.key)) and s.review)
    st.caption(
        f"範圍:標「投」+ 清單候補的公司 {len(companies)} 家 · 已查 {done} 家 · "
        "排程每天補查 3 家,沒資料的可以點進去直接查"
    )
    if not companies:
        st.info("還沒有標「投」或清單候補的職缺。")
        return

    def career(s: StoredCompany | None) -> str:
        if s is None or s.review is None:
            return ""
        return "✅" if s.review.career_url else "❌"

    def state(c) -> str:
        s = all_stored.get(c.key)
        if _company_running(c.key, s, now):
            return "查詢中"
        if s is None:
            return "未查"
        if s.status == "error" and s.review is None:
            return "失敗"
        return "" if s.fresh(now, UI_TTL_DAYS) else "過期"

    table = pd.DataFrame(
        [
            {
                "company": c.name,
                "jobs": f"{len(c.jobs)}(投 {c.applied})",
                "score": c.best_score or None,
                "tone": TONE_ICONS[s.review.tone]
                if (s := all_stored.get(c.key)) and s.review
                else "",
                "career": career(all_stored.get(c.key)),
                "reviewed": s.reviewed_at.date() if s and s.reviewed_at else None,
                "state": state(c),
            }
            for c in companies
        ]
    )
    left, right = st.columns([1, 1])
    with left:
        event = st.dataframe(
            table,
            hide_index=True,
            width="stretch",
            height=560,
            on_select="rerun",
            selection_mode="single-row",
            key="companies_table",
            column_config={
                "company": st.column_config.TextColumn("公司", width="medium"),
                "jobs": st.column_config.TextColumn("職缺", width="small"),
                "score": st.column_config.NumberColumn("最高深評", format="%d", width="small"),
                "tone": st.column_config.TextColumn("整體", width="small"),
                "career": st.column_config.TextColumn("徵才官網", width="small"),
                "reviewed": st.column_config.DateColumn("查詢日"),
                "state": st.column_config.TextColumn("狀態", width="small"),
            },
        )
    with right:
        picked = event.selection.rows if event else []
        if not picked:
            st.info("點左邊一家看評價摘要與徵才官網。")
            return
        c = companies[picked[0]]
        st.subheader(c.name)
        st.caption("職缺:" + "、".join(j.title for j in c.jobs[:6]))
        _company_panel(c.name, all_stored, f"page_{c.key}")


def _decision_form(job: JobRow, desires: dict[str, int]) -> None:
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
    current_desire = desires.get(key)
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
    # 投遞清單匯入的標記是用 detail_id 存的;pipeline 抓到之後這筆的鍵變成 job_no
    detail_id = cand.detail_id_of(job.url)
    history = store().history(key, *([detail_id] if detail_id != key else []))
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
WEEKDAY = "一二三四五六日"


def applied_page() -> None:
    rows = load_rows()
    aliases, latest, desires = marks(rows)
    plans = store().latest_plans(aliases)
    sent = store().latest_sent(aliases)
    closed = store().latest_closed(aliases)
    this_week = cand.week_start(today())
    board = planning.build_board(rows, latest, plans, sent, desires, this_week, closed)
    if "applied_gen" not in st.session_state:
        # 動作完成後換表格的 key 來清掉勾選 —— 列會換區,留著舊的勾選索引會指到別筆
        st.session_state.applied_gen = 0

    st.title("已標投遞")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("本週待投", len(board.due), help="排在本週或更早、還沒送出的")
    c2.metric("之後幾週", len(board.later))
    c3.metric("未排週次", len(board.unplanned))
    c4.metric("已送出", len(board.done), help="只記送出日期;回覆／面試記在 Notion")
    if not (board.pool or board.backups or board.closed):
        st.info("還沒有標「投」的職缺。到「候選清單」點一列、標記「✅ 投」後就會出現在這裡。")
        return

    def table(rs: list[JobRow], name: str, mode: str = "multi-row") -> list[JobRow]:
        return _applied_table(rs, name, mode, latest, desires, plans, sent, this_week)

    tab_progress, tab_tailor = st.tabs(["📨 投遞進度", "✍️ 履歷修改建議"])

    with tab_progress:
        st.caption(
            "勾選表格左側的方塊可以一次選多筆、也可以跨區選 → 最下方一次「確認投遞」或排週次"
        )
        st.subheader(f"📅 本週待投 · {this_week:%m/%d} 那週")
        chosen = table(board.due, "due")
        # 全部預設收合:只留「本週待投」攤開,看資訊時不被其他區塊擠下去
        with st.expander(f"之後幾週({len(board.later)})"):
            chosen += table(board.later, "later")
        with st.expander(f"未排週次({len(board.unplanned)})—— 標了「投」但還沒決定哪週"):
            chosen += table(board.unplanned, "unplanned")
        with st.expander(
            f"清單候補({len(board.backups)})—— 投遞清單裡標「待確認」的:逐筆點投/不投,一次儲存"
        ):
            _backup_triage(board.backups)
            backup_chosen = []
            if board.backups and st.toggle("只排週次(不改標記)", key="backup_plan_mode"):
                backup_chosen = table(board.backups, "backups")
        # 評分區放在「已送出」之前:還沒送的才需要評,評完再決定要不要投
        _rate_unrated([r for r in board.pool + board.backups if r.job_no not in desires])
        with st.expander(f"✅ 已送出({len(board.done)})"):
            undo_chosen = table(board.done, "done")
        with st.expander(f"🚫 職缺已關閉({len(board.closed)})—— 標了投、還沒送,104 上已下架"):
            reopen_chosen = table(board.closed, "closed")
        st.divider()
        _bulk_actions(chosen, chosen + backup_chosen, undo_chosen, plans, this_week, reopen_chosen)

        applied = board.pool
        a, b = st.columns(2)
        a.download_button("下載 Markdown", to_markdown(applied, latest), file_name="applied.md")
        b.download_button(
            "下載 CSV", to_csv(applied, latest), file_name="applied.csv", mime="text/csv"
        )

    with tab_tailor:
        options = planning.week_options(board, plans)
        option = st.selectbox(
            "週次",
            options,
            index=planning.default_week_option(options),
            format_func=lambda o: planning.option_label(o, board, plans, this_week),
            key="tailor_week",
        )
        pool = planning.filter_pool(board, option, plans)
        st.caption("點一列看這家的履歷修改建議與 104 原文 · 排序:本週 → 之後 → 未排 → 已送出")
        picked = table(pool, f"tailor_{option}", mode="single-row")
        if not picked:
            st.info("點上面表格的一列,下方就會顯示履歷修改建議與 104 原文。")
        else:
            job = picked[0]
            st.subheader(f"{job.company} — {job.title}")
            left, right = st.columns(2)
            with left:
                _tailoring_panel(job)
            with right:
                _jd_original(job)


def _applied_table(rs, name, mode, latest, desires, plans, sent, this_week) -> list[JobRow]:
    """已標投遞頁共用的表格。回傳勾選的列。"""
    if not rs:
        st.caption("(沒有)")
        return []
    table = pd.DataFrame(
        [
            {
                "week": planning.week_label(plans.get(r.job_no), this_week),
                "sent": sent.get(r.job_no),
                "desire": desire_stars(desires.get(r.job_no)),
                "company": r.company,
                "title": r.title,
                "gates": LIGHT_ICONS[r.gates.gate1] + LIGHT_ICONS[r.gates.gate2],
                "gate3": gate3_label(r),
                "score": r.deep_score,
                "score_status": r.score_status,
                "area": r.area,
                "salary": r.salary_text,
                "note": latest[r.job_no].note if r.job_no in latest else "",
                "data": r.fetch_note or ("清單" if r.in_shortlist else ""),
                "url": r.url,
            }
            for r in rs
        ]
    )
    event = st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode=mode,
        key=f"applied_{name}_{st.session_state.applied_gen}",
        column_config={
            "week": st.column_config.TextColumn("週次", width="small"),
            "sent": st.column_config.DateColumn("送出日"),
            "desire": st.column_config.TextColumn(
                "想去", width="small", help="想去程度 1–5;空白 = 還沒評,到候選清單點該職缺評分"
            ),
            "company": "公司",
            "title": st.column_config.TextColumn("職稱", width="medium"),
            "gates": st.column_config.TextColumn("①②", width="small"),
            "gate3": st.column_config.TextColumn("③命中率(LLM)", width="medium"),
            "score": st.column_config.NumberColumn("深評", format="%d"),
            "score_status": st.column_config.TextColumn(
                "未評原因",
                width="small",
                help="沒有深評分數的原因。待抓全文/待深評 = 排程會自己補上;"
                "粗篩淘汰/規則層淘汰 = 當初被刷掉,不會再評(除非加進投遞清單)",
            ),
            "area": "地點",
            "salary": "薪資",
            "note": st.column_config.TextColumn("備註", width="large"),
            "data": st.column_config.TextColumn(
                "資料",
                width="small",
                help="清單 = 投遞清單匯入的;其他文字 = 詳細頁還沒抓到(`jobfinder shortlist fetch`)",
            ),
            "url": st.column_config.LinkColumn("104", display_text="開啟"),
        },
    )
    return [rs[i] for i in (event.selection.rows if event else [])]


def _bulk_actions(
    to_send: list[JobRow],
    to_plan: list[JobRow],
    to_undo: list[JobRow],
    plans: dict,
    this_week,
    to_reopen: list[JobRow] = (),
) -> None:
    gen = st.session_state.applied_gen
    if not (to_plan or to_undo or to_reopen):
        st.info("還沒勾選。")
    picked = [*to_plan, *to_undo, *to_reopen]
    if len(picked) == 1 and st.button("🔍 看這筆的分析(深評 + 命中率 + 104 原文)"):
        _analysis_dialog(picked[0])
    if to_send:
        st.markdown(f"#### 📨 已選 {len(to_send)} 筆:" + "、".join(j.company for j in to_send))
        d1, d2 = st.columns([1, 2], vertical_alignment="bottom")
        day = d1.date_input("日期", value=today(), max_value=today(), key=f"sd_{gen}")
        # 兩顆按鈕緊靠:水平容器 + 小間距,不用 columns(columns 會平均撐開欄寬)
        buttons = d2.container(horizontal=True, gap="xsmall", vertical_alignment="bottom")
        if buttons.button(f"✅ 確認投遞 {len(to_send)} 筆", type="primary"):
            _confirm_sent(to_send, day)
        if buttons.button(
            f"🚫 職缺已關閉 {len(to_send)} 筆",
            help="104 上已下架或額滿、沒投成。移到「職缺已關閉」,不算投遞",
        ):
            _confirm_closed(to_send, day)
    if to_plan:
        weeks = planning.plan_week_choices(this_week)
        w1, w2 = st.columns([1, 2], vertical_alignment="bottom")
        week = w1.selectbox(
            f"把已選的 {len(to_plan)} 筆排到",
            weeks,
            index=planning.default_plan_week(to_plan, plans, weeks),
            format_func=lambda w: (
                "不排(取消週次)" if w is None else planning.week_label(w, this_week)
            ),
            # key 帶上勾選內容:勾選一變就重新套用預設值,不沿用上一組勾選時選的週
            key=f"wk_{gen}_{'-'.join(sorted(j.job_no for j in to_plan))}",
        )
        if w2.button(f"📅 排入週次({len(to_plan)} 筆)"):
            now = datetime.now(cand.TAIPEI)
            for j in to_plan:
                store().plan(j.job_no, week, now)
            _after_bulk_action()
    if to_undo and st.button(f"↩️ 撤銷已送出({len(to_undo)} 筆,勾錯了)"):
        now = datetime.now(cand.TAIPEI)
        for j in to_undo:
            store().mark_sent(j.job_no, None, now)
        _after_bulk_action()
    if to_reopen and st.button(f"↩️ 撤銷已關閉({len(to_reopen)} 筆,標錯了)"):
        now = datetime.now(cand.TAIPEI)
        for j in to_reopen:
            store().mark_closed(j.job_no, None, now)
        _after_bulk_action()


@st.dialog("確認投遞")
def _confirm_sent(jobs: list[JobRow], day) -> None:
    st.markdown(
        f"以下 **{len(jobs)} 筆**要記為 **{day:%Y-%m-%d}(週{WEEKDAY[day.weekday()]})已送出**:"
    )
    for j in jobs:
        st.markdown(f"- {j.company} — {j.title}")
    st.caption("記完會移到「已送出」;勾錯了可以在「已送出」勾選後撤銷。回覆／面試照舊記在 Notion。")
    a, b = st.columns(2)
    if a.button("✅ 確認", type="primary", width="stretch"):
        now = datetime.now(cand.TAIPEI)
        for j in jobs:
            store().mark_sent(j.job_no, day, now)
        _after_bulk_action()
    if b.button("取消", width="stretch"):
        st.rerun()


def _rate_unrated(jobs: list[JobRow]) -> None:
    """還沒評「想去」的職缺,在這裡直接填星數。

    獨立的小表(``st.data_editor``)而不是讓上面的表格可編輯 —— data_editor 在
    Streamlit 1.64 不支援勾選列,而確認投遞、排週次全靠勾選。
    """
    if not jobs:
        return
    with st.expander(f"⭐ 還沒評想去程度({len(jobs)})—— 直接在表格裡選星數,再按儲存"):
        gen = st.session_state.applied_gen
        edited = st.data_editor(
            pd.DataFrame([{"company": j.company, "title": j.title, "desire": None} for j in jobs]),
            hide_index=True,
            width="stretch",
            disabled=["company", "title"],
            placeholder="未評",
            key=f"rate_{gen}",
            column_config={
                "company": "公司",
                "title": st.column_config.TextColumn("職稱", width="large"),
                "desire": st.column_config.SelectboxColumn(
                    "想去",
                    options=list(DESIRE_LABELS),
                    width="small",
                    help="1 不太想 … 5 很想去;留空 = 先不評",
                ),
            },
        )
        picked = [(j, int(v)) for j, v in zip(jobs, edited["desire"], strict=True) if pd.notna(v)]
        if st.button(f"⭐ 儲存 {len(picked)} 筆想去程度", disabled=not picked):
            now = datetime.now(cand.TAIPEI)
            for j, v in picked:
                store().rate(j.job_no, v, now)
            _after_bulk_action()


@st.dialog("職缺已關閉")
def _confirm_closed(jobs: list[JobRow], day) -> None:
    st.markdown(f"以下 **{len(jobs)} 筆**要記為 **{day:%Y-%m-%d} 發現已關閉**(沒投成):")
    for j in jobs:
        st.markdown(f"- {j.company} — {j.title}")
    st.caption("記完會移到「職缺已關閉」,不算投遞;標錯了可以在那一區勾選後撤銷。")
    a, b = st.columns(2)
    if a.button("🚫 確認", type="primary", width="stretch"):
        now = datetime.now(cand.TAIPEI)
        for j in jobs:
            store().mark_closed(j.job_no, day, now)
        _after_bulk_action()
    if b.button("取消", width="stretch"):
        st.rerun()


TRIAGE_LABELS = {
    planning.TRIAGE_APPLY: "✅ 投",
    planning.TRIAGE_SKIP: "❌ 不投",
    planning.TRIAGE_HOLD: "⏸",
}


def _backup_triage(rs: list[JobRow]) -> None:
    """清單候補逐列分流(design-demos/2026-10-27 方案 C):點投/不投,不投展開原因,一次存。"""
    if not rs:
        st.caption("(沒有)")
        return
    gen = st.session_state.applied_gen
    st.caption("⏸ = 這次先不決定(不寫入)。不投一定要選原因,原因是之後檢討閘門的資料。")
    picks: dict[str, tuple[str | None, str | None]] = {}
    for r in rs:
        left, right, view = st.columns([1.4, 5, 0.7], vertical_alignment="center")
        # 對話框不動底下的勾選:看完關掉,剛點的投/不投都還在
        if view.button("🔍 分析", key=f"tri_view_{r.job_no}", help="深評 + 命中率 + 104 原文"):
            _analysis_dialog(r)
        choice = left.segmented_control(
            "決定",
            list(TRIAGE_LABELS),
            format_func=TRIAGE_LABELS.get,
            key=f"tri_{gen}_{r.job_no}",
            label_visibility="collapsed",
        )
        score = r.deep_score if r.deep_score is not None else r.score_status
        right.markdown(
            f"**{r.company}** — {r.title}  \n"
            f"{LIGHT_ICONS[r.gates.gate1]}{LIGHT_ICONS[r.gates.gate2]} · "
            f"③ {gate3_label(r)} · 深評 {score} · [104 原文]({r.url})"
        )
        reason = None
        if choice == planning.TRIAGE_SKIP:
            reason = right.pills(
                "不投原因",
                SKIP_REASONS,
                key=f"trr_{gen}_{r.job_no}",
                label_visibility="collapsed",
            )
        picks[r.job_no] = (choice, reason)

    writes, missing = planning.triage_writes(picks)
    if st.button(f"💾 儲存 {len(writes) + len(missing)} 筆", type="primary", key=f"tri_save_{gen}"):
        if missing:
            st.error(f"有 {len(missing)} 筆標了不投但沒選原因,整批還沒存。")
            return
        now = datetime.now(cand.TAIPEI)
        for job_no, status, reason in writes:
            store().append(job_no, status, reason, "", now)
        _after_bulk_action()


def _after_bulk_action() -> None:
    st.session_state.applied_gen += 1
    st.rerun()


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
            st.Page(companies_page, title="公司評價", icon="🏢"),
            st.Page(trends_page, title="技能趨勢", icon="📈"),
        ]
    ).run()


main()
