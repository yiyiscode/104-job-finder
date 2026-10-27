"""投遞清單:詳細頁轉接、shortlist.db、標記的鍵合併、CSV 匯入、補抓的護欄。

全部離線。補抓走真的 ``HttpJobSource`` + ``RequestBudget``,只把傳輸層換成 MockTransport ——
要證明的是「這條新路沒有繞過任何一道護欄」,所以不能用假的 source 跳過它們。
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from webui_helpers import row

from jobfinder import cli
from jobfinder.config import load_config
from jobfinder.errors import ChallengeBlocked
from jobfinder.http_source import HttpJobSource
from jobfinder.normalize import detail_as_summary, work_exp_to_period
from jobfinder.scoring.fake import FakeScorer
from jobfinder.scrape import blocking
from jobfinder.scrape.budget import RequestBudget
from jobfinder.shortlist.fetch import fetch_entries
from jobfinder.shortlist.importer import import_csv
from jobfinder.shortlist.score import job_from_detail, score_entries
from jobfinder.shortlist.store import Entry, Fetch, ShortlistStore
from jobfinder.storage import Database, JobRepo
from jobfinder.webui.candidates import (
    SHORTLIST_STATUS,
    detail_aliases,
    merge_shortlist,
    shortlist_row,
)
from jobfinder.webui.decisions import DecisionStore

FIXTURES = Path(__file__).parent / "fixtures"
TZ = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 9, 28, 10, 0, tzinfo=TZ)
MONDAY = date(2026, 9, 28)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


DETAIL = _fixture("detail_94234.json")  # 列表裡 jobNo=15305872 的同一筆
NOT_FOUND = _fixture("detail_404_error.json")
CHALLENGE_403 = httpx.Response(
    403, text="<html><head><title>Just a moment...</title></head><body>cf</body></html>"
)


# ═══ 詳細頁 → 列表形狀 ════════════════════════════════════════════
def test_detail_as_summary_matches_the_real_list_entry_of_the_same_job():
    """錨點:同一筆職缺,從詳細頁轉出來的欄位要跟 104 列表給的一模一樣。"""
    listed = next(j for j in _fixture("search_page1.json")["data"] if j["jobNo"] == "15305872")
    derived = detail_as_summary(DETAIL)
    for key in ("coIndustry", "employeeCount", "s10", "salaryLow", "salaryHigh", "period"):
        assert derived[key] == listed[key], key
    assert derived["jobName"] == listed["jobName"]
    assert derived["link"]["cust"] == listed["link"]["cust"]


def test_detail_as_summary_unprovided_employees_and_english():
    derived = detail_as_summary(_fixture("detail_94xn5.json"))
    assert derived["employeeCount"] == 0  # 「暫不提供」→ 跟列表一樣是 0
    assert derived["languageRequirements"] == [{"language": 1}]  # 英文


@pytest.mark.parametrize(
    ("text", "period"),
    [("不拘", 0), ("1年以上", 2), ("2年以上", 3), ("3年以上", 4), ("", None), ("面議", None)],
)
def test_work_exp_to_period_is_inverse_of_period_to_years(text, period):
    assert work_exp_to_period(text) == period


# ═══ UI 列 ═══════════════════════════════════════════════════════
def _entry(detail_id: str = "94234", company: str = "清單寫的公司") -> Entry:
    return Entry(detail_id, company, "清單寫的職稱", NOW.isoformat())


def _fetched(detail_id: str = "94234", payload: dict = DETAIL) -> Fetch:
    return Fetch(detail_id, NOW.isoformat(), json.dumps(payload, ensure_ascii=False), None, False)


def test_shortlist_row_uses_detail_and_runs_the_same_gates():
    r = shortlist_row(_entry(), _fetched())
    assert r.job_no == "94234" and r.pipeline_status == SHORTLIST_STATUS and r.in_shortlist
    assert r.has_detail and r.fetch_note == ""
    assert r.company == "財團法人祐生研究基金會"  # 詳細頁優先於清單上手寫的
    assert r.employees == 24 and r.education == "大學"
    assert r.gates.stuck_at == 1  # 24 人 < 30:閘門照常算,沒有因為來源不同被跳過
    assert r.url == "https://www.104.com.tw/job/94234"


def test_unfetched_shortlist_row_still_shows_with_list_info():
    r = shortlist_row(_entry(), None)
    assert r.company == "清單寫的公司" and r.title == "清單寫的職稱"
    assert not r.has_detail and r.fetch_note == "尚未抓取"


def test_url_only_entry_gets_placeholder_names_until_fetched():
    """只有網址匯入的職缺:抓到詳細頁之前公司/職稱是空的,UI 曾因此顯示 None。"""
    bare = Entry("6xqff", "", "", NOW.isoformat())
    r = shortlist_row(bare, None)
    assert r.company == "(尚未抓取)6xqff" and r.title == "(等排程抓全文)"
    assert shortlist_row(bare, _fetched("6xqff")).company == "財團法人祐生研究基金會"


def test_score_status_says_why_there_is_no_deep_score():
    assert shortlist_row(_entry(), None).score_status == "待抓全文"
    assert shortlist_row(_entry(), _fetched()).score_status == "待深評"
    scored = shortlist_row(_entry(), _fetched(), score={"total_score": 70})
    assert scored.score_status == ""

    r = shortlist_row(_entry(), _fetched())
    r.in_shortlist = False
    for status, label in [
        ("filtered_out", "規則層淘汰"),
        ("screened_out", "粗篩淘汰"),
        ("new", "未深評"),
    ]:
        r.pipeline_status = status
        assert r.score_status == label


def test_gone_job_shows_104_error():
    gone = Fetch("94234", NOW.isoformat(), None, "11201 職務不存在", True)
    assert shortlist_row(_entry(), gone).fetch_note == "11201 職務不存在"


def test_merge_prefers_jobs_db_row_and_does_not_duplicate():
    pipeline_row = row(job_no="15305872")
    pipeline_row.url = "https://www.104.com.tw/job/94234"
    merged = merge_shortlist([pipeline_row], [_entry("94234"), _entry("7j5sn")], {}, {}, None)
    assert [r.job_no for r in merged] == ["15305872", "7j5sn"]
    assert all(r.in_shortlist for r in merged)
    assert detail_aliases(merged) == {"94234": "15305872"}  # 只收 jobs.db 的列


# ═══ 標記:detail_id 與 job_no 合併 ═══════════════════════════════
def test_decisions_under_detail_id_fold_into_job_no_and_newest_wins(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    store.append("94234", "pending", None, "清單匯入", NOW)
    assert store.latest({"94234": "15305872"})["15305872"].status == "pending"

    store.append("15305872", "apply", None, "", NOW)  # pipeline 抓到後在 UI 改判
    latest = store.latest({"94234": "15305872"})
    assert set(latest) == {"15305872"} and latest["15305872"].status == "apply"
    assert [d.status for d in store.history("15305872", "94234")] == ["pending", "apply"]


def test_plan_and_sent_latest_wins_and_none_cancels(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    store.plan("J1", MONDAY, NOW)
    store.plan("J2", MONDAY, NOW)
    store.plan("J2", None, NOW)
    store.mark_sent("J1", date(2026, 9, 27), NOW)
    store.mark_sent("J3", date(2026, 9, 27), NOW)
    store.mark_sent("J3", None, NOW)
    assert store.latest_plans() == {"J1": MONDAY}
    assert store.latest_sent() == {"J1": date(2026, 9, 27)}


def test_plan_must_be_a_monday(tmp_path):
    with pytest.raises(ValueError, match="週一"):
        DecisionStore(tmp_path / "d.db").plan("J1", date(2026, 9, 30), NOW)


def test_sent_date_cannot_be_in_the_future(tmp_path):
    with pytest.raises(ValueError, match="未來"):
        DecisionStore(tmp_path / "d.db").mark_sent("J1", date(2026, 9, 29), NOW)


# ═══ shortlist.db ════════════════════════════════════════════════
def test_to_fetch_skips_known_fetched_and_gone_but_retries_transient(tmp_path):
    store = ShortlistStore(tmp_path / "shortlist.db")
    for detail_id in ("aaaa1", "bbbb2", "cccc3", "dddd4", "eeee5"):
        store.add(detail_id, "公司", "職稱", NOW)
    store.record_fetch("bbbb2", NOW, raw_detail="{}")
    store.record_fetch("cccc3", NOW, error="11201 職務不存在", gone=True)
    store.record_fetch("dddd4", NOW, error="HTTP 500")
    assert [e.detail_id for e in store.to_fetch(known={"aaaa1"})] == ["dddd4", "eeee5"]


def test_later_failure_does_not_hide_earlier_success(tmp_path):
    store = ShortlistStore(tmp_path / "shortlist.db")
    store.add("aaaa1", "", "", NOW)
    store.record_fetch("aaaa1", NOW, raw_detail='{"ok": 1}')
    store.record_fetch("aaaa1", NOW, error="HTTP 500")
    assert store.latest_fetches()["aaaa1"].raw_detail == '{"ok": 1}'


@pytest.mark.parametrize("bad", ["https://www.104.com.tw/job/7j5sn", "15305872x9", "7J5SN", ""])
def test_add_rejects_things_that_are_not_detail_ids(tmp_path, bad):
    with pytest.raises(ValueError):
        ShortlistStore(tmp_path / "shortlist.db").add(bad, "", "", NOW)


# ═══ CSV 匯入 ════════════════════════════════════════════════════
CSV_HEAD = "detail_id,company,title,decision,week,note\n"


def _csv(tmp_path, body: str) -> Path:
    p = tmp_path / "list.csv"
    p.write_text(CSV_HEAD + body, encoding="utf-8")
    return p


def test_import_seeds_entries_decisions_and_weeks(tmp_path):
    sl, dec = ShortlistStore(tmp_path / "s.db"), DecisionStore(tmp_path / "d.db")
    path = _csv(
        tmp_path,
        "7j5sn,台積電,IT Data Engineer,apply,2026-09-28,僅接受官網投遞\n"
        "93ofp,蝦皮,Data Engineer,pending,,候補\n"
        "8y1m0,台中商銀,數據工程,,,\n",
    )
    report = import_csv(path, sl, dec, NOW)
    assert (report.added, report.decided, report.planned, report.errors) == (3, 2, 1, [])
    assert {k: d.status for k, d in dec.latest().items()} == {"7j5sn": "apply", "93ofp": "pending"}
    assert dec.latest()["7j5sn"].note == "僅接受官網投遞"
    assert dec.latest_plans() == {"7j5sn": MONDAY}


def test_reimport_never_overrides_what_you_changed_in_the_ui(tmp_path):
    sl, dec = ShortlistStore(tmp_path / "s.db"), DecisionStore(tmp_path / "d.db")
    path = _csv(tmp_path, "7j5sn,台積電,IT Data Engineer,apply,2026-09-28,\n")
    import_csv(path, sl, dec, NOW)
    dec.append("7j5sn", "skip", "命中率不足", "", NOW)  # 之後在 UI 改判
    dec.plan("7j5sn", date(2026, 10, 5), NOW)

    report = import_csv(path, sl, dec, NOW)
    assert (report.added, report.updated, report.decided, report.planned) == (0, 1, 0, 0)
    assert dec.latest()["7j5sn"].status == "skip"
    assert dec.latest_plans()["7j5sn"] == date(2026, 10, 5)


def test_import_respects_marks_made_on_the_jobs_db_key(tmp_path):
    """jobs.db 已有的職缺,你可能早就用 job_no 標過 —— 匯入不能再疊一筆蓋掉它。"""
    sl, dec = ShortlistStore(tmp_path / "s.db"), DecisionStore(tmp_path / "d.db")
    dec.append("14589892", "skip", "其他", "", NOW)
    path = _csv(tmp_path, "8opms,國泰產險,數據架構工程師,apply,2026-10-19,\n")
    import_csv(path, sl, dec, NOW, aliases={"8opms": "14589892"})
    assert dec.latest({"8opms": "14589892"})["14589892"].status == "skip"


def test_import_reports_bad_rows_and_keeps_the_good_ones(tmp_path):
    sl, dec = ShortlistStore(tmp_path / "s.db"), DecisionStore(tmp_path / "d.db")
    path = _csv(
        tmp_path,
        "7j5sn,台積電,IT,skip,,\n"  # 不投要原因,只能在 UI 標
        "4oosb,日月光,SD,apply,2026-09-30,\n"  # 不是週一
        "https://x,壞,壞,,,\n"
        "6oz7y,力積電,大數據,apply,,\n",
    )
    report = import_csv(path, sl, dec, NOW)
    assert len(report.errors) == 3 and report.added == 1
    assert [e.detail_id for e in sl.entries()] == ["6oz7y"]


def test_import_rejects_csv_without_required_columns(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("id,company\n7j5sn,台積電\n", encoding="utf-8")
    report = import_csv(p, ShortlistStore(tmp_path / "s.db"), DecisionStore(tmp_path / "d.db"), NOW)
    assert report.errors and "detail_id" in report.errors[0]


# ═══ 補抓:走真的 HttpJobSource + RequestBudget ═══════════════════
class NoSleep:
    def __init__(self):
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _source(handler):
    cfg = load_config("config/config.yaml")
    budget = RequestBudget(cfg.scrape, sleeper=NoSleep())
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpJobSource(cfg, budget, client=client), budget


def _responder(by_id: dict[str, httpx.Response], seen: list[httpx.Request] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return by_id[request.url.path.rsplit("/", 1)[-1]]

    return handler


def _store_with(tmp_path, *ids: str) -> ShortlistStore:
    store = ShortlistStore(tmp_path / "shortlist.db")
    for detail_id in ids:
        store.add(detail_id, "公司", "職稱", NOW)
    return store


async def test_fetch_saves_success_marks_gone_and_keeps_transient_for_retry(tmp_path):
    store = _store_with(tmp_path, "94234", "7j5sn", "4oosb")
    seen: list[httpx.Request] = []
    source, budget = _source(
        _responder(
            {
                "94234": httpx.Response(200, json=DETAIL),
                "7j5sn": httpx.Response(200, json=NOT_FOUND),
                "4oosb": httpx.Response(500, text="oops"),
            },
            seen,
        )
    )
    report = await fetch_entries(store.entries(), source, store, lambda: NOW)

    assert report.fetched == ["94234"] and report.gone == ["7j5sn"]
    assert [d for d, _ in report.failed] == ["4oosb"]
    assert budget.details_used == 3  # 每個請求都過了預算
    # Referer 是該職缺自己的頁面(詳細頁 API 的 origin 規則)
    assert [r.headers["Referer"] for r in seen] == [
        f"https://www.104.com.tw/job/{d}" for d in ("94234", "7j5sn", "4oosb")
    ]
    assert [e.detail_id for e in store.to_fetch(known=set())] == ["4oosb"]


async def test_block_stops_immediately_but_keeps_what_was_already_fetched(tmp_path):
    store = _store_with(tmp_path, "94234", "7j5sn", "4oosb")
    seen: list[httpx.Request] = []
    source, _ = _source(
        _responder({"94234": httpx.Response(200, json=DETAIL), "7j5sn": CHALLENGE_403}, seen)
    )
    with pytest.raises(ChallengeBlocked):
        await fetch_entries(store.entries(), source, store, lambda: NOW)
    assert len(seen) == 2  # 被擋之後一個請求都沒再發(沒有重試、沒有下一筆)
    assert store.latest_fetches()["94234"].raw_detail is not None


# ═══ CLI 護欄:全部在發第一個請求之前 ═════════════════════════════
@pytest.fixture
def no_network(monkeypatch):
    """任何走到 http_source 的路徑都直接失敗 —— 證明護欄擋在連線之前。"""

    def boom(*_a, **_k):
        raise AssertionError("不該連線")

    monkeypatch.setattr("jobfinder.http_source.http_source", boom)


def _run_cli(tmp_path, *args: str) -> int:
    return cli.main(["--data-dir", str(tmp_path), "shortlist", *args])


def _seed(tmp_path, *ids: str) -> None:
    _store_with(tmp_path, *ids)


@pytest.mark.parametrize("limit", ["0", "6", "18"])
def test_fetch_limit_hard_cap(tmp_path, no_network, limit):
    _seed(tmp_path, "94234")
    with pytest.raises(SystemExit):
        _run_cli(tmp_path, "fetch", "--limit", limit)


def test_fetch_requires_explicit_limit(tmp_path, no_network):
    with pytest.raises(SystemExit):
        _run_cli(tmp_path, "fetch")


def test_fetch_respects_kill_switch(tmp_path, no_network, monkeypatch):
    _seed(tmp_path, "94234")
    monkeypatch.setenv("JOBFINDER_SCRAPE_DISABLED", "1")
    assert _run_cli(tmp_path, "fetch", "--limit", "1") == 1


@pytest.mark.parametrize("cooled_down", [False, True])
def test_fetch_refuses_unless_circuit_is_closed(tmp_path, no_network, cooled_down):
    """冷卻期過了(half_open)也不抓:試探留給每日排程,手動指令更保守。"""
    _seed(tmp_path, "94234")
    tripped_at = datetime(2026, 1, 1, tzinfo=TZ) if cooled_down else datetime.now(TZ)
    with Database(tmp_path, "jobs.db", local_dir=tmp_path / "work") as db:
        repo = JobRepo(db.conn)
        repo.save_circuit(blocking.trip(repo.get_circuit(), "test", tripped_at))
    assert _run_cli(tmp_path, "fetch", "--limit", "1") == 1


def test_fetch_dry_run_does_not_connect(tmp_path, no_network, capsys):
    _seed(tmp_path, "94234", "7j5sn")
    assert _run_cli(tmp_path, "fetch", "--limit", "1", "--dry-run") == 0
    assert "待抓 2 筆,本次 1 筆" in capsys.readouterr().out


def test_fetch_block_trips_the_circuit_in_jobs_db(tmp_path, monkeypatch):
    _seed(tmp_path, "94234", "7j5sn")
    source, _ = _source(
        _responder({"94234": httpx.Response(200, json=DETAIL), "7j5sn": CHALLENGE_403})
    )

    @asynccontextmanager
    async def fake_http_source(cfg, **_):
        yield source

    monkeypatch.setattr("jobfinder.http_source.http_source", fake_http_source)
    assert _run_cli(tmp_path, "fetch", "--limit", "2") == 1

    with Database(tmp_path, "jobs.db", local_dir=tmp_path / "work") as db:
        circuit = JobRepo(db.conn).get_circuit()
        runs = db.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    assert circuit.state == "open" and "shortlist fetch" in circuit.reason
    assert runs == 0  # 不寫 runs:否則 has_run_today 會擋掉當天 08:00 的日報
    assert ShortlistStore(tmp_path / "shortlist.db").latest_fetches()["94234"].raw_detail


# ═══ 深評:不連 104,跟 pipeline 同一個評分器 ══════════════════════
async def _score_with_fake(store: ShortlistStore, *, known: set[str] | None = None):
    return await score_entries(store.to_score(known or set()), store, FakeScorer(), lambda: NOW)


async def test_score_saves_deep_score_only_for_fetched_entries(tmp_path):
    store = _store_with(tmp_path, "94234", "7j5sn")
    store.record_fetch("94234", NOW, raw_detail=json.dumps(DETAIL, ensure_ascii=False))
    assert [e.detail_id for e in store.to_score(set())] == ["94234"]  # 7j5sn 沒全文

    report = await _score_with_fake(store)

    assert [d for d, _ in report.scored] == ["94234"] and not report.failed
    saved = store.latest_scores()["94234"]
    assert saved["total_score"] == report.scored[0][1]
    assert json.loads(saved["highlights"]) is not None  # JSON 陣列,跟 jobs.db 同格式
    assert store.to_score(set()) == []  # 評過就不再評
    assert [e.detail_id for e in store.to_score(set(), rescore=True)] == ["94234"]


async def test_score_skips_jobs_already_in_jobs_db(tmp_path):
    """jobs.db 有的由 pipeline 評,這裡不重複花 LLM。"""
    store = _store_with(tmp_path, "94234")
    store.record_fetch("94234", NOW, raw_detail=json.dumps(DETAIL, ensure_ascii=False))
    assert store.to_score({"94234"}) == []


def test_job_from_detail_keys_by_detail_id():
    summary, detail = job_from_detail("94234", json.dumps(DETAIL, ensure_ascii=False))
    assert summary.job_no == "94234" == summary.detail_id == detail.job_no
    assert summary.job_url == "https://www.104.com.tw/job/94234"
    assert detail.job_description


def test_shortlist_row_shows_its_deep_score():
    score = {
        "total_score": 76, "tech_fit": 28, "exp_fit": 22, "domain_fit": 9,
        "growth_fit": 11, "practical_fit": 6, "verdict": "apply", "one_liner": "一句話",
        "highlights": '["亮點"]', "red_flags": "[]", "resume_tip": "建議",
        "adjustments": "[]", "score_model": "claude-cli:sonnet",
    }  # fmt: skip
    r = shortlist_row(_entry(), _fetched(), score=score)
    assert r.deep_score == 76 and r.verdict == "apply" and r.highlights == ["亮點"]
    assert r.score_model == "claude-cli:sonnet"
    assert shortlist_row(_entry(), _fetched()).deep_score is None


def test_merge_keeps_jobs_db_score_over_shortlist_score():
    pipeline_row = row(job_no="15305872")
    pipeline_row.url = "https://www.104.com.tw/job/94234"
    before = pipeline_row.deep_score
    merged = merge_shortlist(
        [pipeline_row], [_entry("94234"), _entry("7j5sn")], {"7j5sn": _fetched("7j5sn")}, {},
        None, {"94234": {"total_score": 1}, "7j5sn": {"total_score": 66}},
    )  # fmt: skip
    assert merged[0].deep_score == before  # jobs.db 那筆不被清單的分數蓋掉
    assert merged[1].deep_score == 66


def test_score_cli_dry_run_never_calls_llm(tmp_path, no_network, monkeypatch, capsys):
    store = _store_with(tmp_path, "94234")
    store.record_fetch("94234", NOW, raw_detail=json.dumps(DETAIL, ensure_ascii=False))

    def boom(*_a, **_k):
        raise AssertionError("dry-run 不該建評分器")

    monkeypatch.setattr(cli, "_build_scorer", boom)
    assert _run_cli(tmp_path, "score", "--dry-run") == 0
    assert "待評 1 筆,本次 1 筆" in capsys.readouterr().out


def test_score_cli_with_fake_llm_does_not_touch_104(tmp_path, no_network):
    store = _store_with(tmp_path, "94234")
    store.record_fetch("94234", NOW, raw_detail=json.dumps(DETAIL, ensure_ascii=False))
    assert _run_cli(tmp_path, "score", "--fake-llm") == 0
    assert "94234" in ShortlistStore(tmp_path / "shortlist.db").latest_scores()
    assert not (tmp_path / "jobs.db").exists()  # 不寫 jobs.db


def test_score_cli_can_target_specific_detail_ids(tmp_path, no_network, capsys):
    store = _store_with(tmp_path, "94234", "7j5sn")
    for d in ("94234", "7j5sn"):
        store.record_fetch(d, NOW, raw_detail=json.dumps(DETAIL, ensure_ascii=False))
    assert _run_cli(tmp_path, "score", "7j5sn", "nope1", "--dry-run") == 0
    out = capsys.readouterr()
    assert "待評 1 筆,本次 1 筆" in out.out and "7j5sn" in out.out and "94234" not in out.out
    assert "nope1" in out.err
