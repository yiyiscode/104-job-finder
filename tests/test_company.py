"""公司評價:名稱正規化、清洗、companies.db、挑公司、逐家查詢的停損。全部離線。"""

from __future__ import annotations

import contextlib
import dataclasses
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from test_claude_cli import fake_cli  # noqa: F401 —— pytest fixture
from webui_helpers import row

from jobfinder.company.review import DENIED_TOOLS, WEB_TOOLS, run_reviews, web_client
from jobfinder.company.schema import CompanyReview, Facet, Source, company_key, sanitize
from jobfinder.company.store import RUNNING_STALE, CompanyStore
from jobfinder.company.targets import interested_companies, pick_targets
from jobfinder.scoring.claude_cli import ClaudeQuotaExhausted
from jobfinder.webui.decisions import Decision

NOW = datetime(2026, 10, 27, 9, 0, tzinfo=ZoneInfo("Asia/Taipei"))


def review(**kw) -> CompanyReview:
    facet = Facet(tone="mixed", summary="依部門而定")
    data = dict(
        overall="整體中等",
        tone="mixed",
        culture=facet,
        pay=facet,
        interview=facet,
        data_team=facet,
        career_url="https://careers.example.com",
        career_note="官網要另建履歷",
        sources=[Source(title="面試趣", url="https://interview.tw/c/x", date="2026-07")],
    )
    return CompanyReview(**{**data, **kw})


# ── 名稱 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("光寶科技", "光寶科技股份有限公司"),  # 清單匯入的簡稱 vs jobs.db 的正式名
        ("玉山銀行_玉山商業銀行股份有限公司", "玉山商業銀行"),
        ("台達電子工業股份有限公司 _DELTA ELECTRONICS INC.", "台達電子工業"),
        ("台灣積體電路製造股份有限公司(台積電)", "台灣積體電路製造"),
        ("合作金庫人壽保險股份有限公司(總公司)", "合作金庫人壽保險"),
    ],
)
def test_company_key_merges_spellings_of_the_same_company(a, b):
    assert company_key(a) == company_key(b)


def test_company_key_keeps_different_companies_apart():
    assert company_key("國泰世華商業銀行") != company_key("國泰金融控股")


# ── 清洗 ──────────────────────────────────────────────────────────


def test_sanitize_drops_104_sources():
    r = sanitize(
        review(
            sources=[
                Source(title="104", url="https://www.104.com.tw/company/abc", date="不明"),
                Source(title="PTT", url="https://www.ptt.cc/bbs/Tech_Job/x.html", date="2026-01"),
            ]
        )
    )
    assert [s.title for s in r.sources] == ["PTT"]


@pytest.mark.parametrize(
    "url", ["https://www.104.com.tw/company/abc", "https://www.cake.me/companies/x", "不是網址"]
)
def test_sanitize_rejects_job_boards_as_career_site(url):
    r = sanitize(review(career_url=url))
    assert r.career_url == ""
    assert "沒有公司自己的徵才官網" in r.career_note


def test_sanitize_keeps_official_career_site():
    assert sanitize(review()).career_url == "https://careers.example.com"


# ── companies.db ─────────────────────────────────────────────────


def test_requery_and_failure_keep_the_previous_review(tmp_path):
    store = CompanyStore(tmp_path / "companies.db")
    store.save_ok("光寶科技", review(), "sonnet", NOW)
    store.mark_running("光寶科技股份有限公司", NOW + timedelta(days=1))
    s = store.all()[company_key("光寶科技")]
    assert s.status == "running" and s.review is not None

    store.save_error("光寶科技", "逾時", NOW + timedelta(days=1))
    s = store.all()[company_key("光寶科技")]
    assert (s.status, s.error, s.review.overall) == ("error", "逾時", "整體中等")
    assert s.reviewed_at == NOW  # 失敗不更新查詢日


def test_running_and_fresh_windows(tmp_path):
    store = CompanyStore(tmp_path / "companies.db")
    store.mark_running("A公司", NOW)
    s = store.all()[company_key("A公司")]
    assert s.running(NOW + timedelta(minutes=5))
    assert not s.running(NOW + RUNNING_STALE)  # 排程被砍掉的「查詢中」要能解除

    store.save_ok("A公司", review(), "sonnet", NOW)
    s = store.all()[company_key("A公司")]
    assert s.fresh(NOW + timedelta(days=89), 90)
    assert not s.fresh(NOW + timedelta(days=90), 90)


# ── 挑公司 ────────────────────────────────────────────────────────


def decision(job_no: str, status: str) -> Decision:
    return Decision(id=0, job_no=job_no, status=status, reason=None, note="", decided_at="")


def test_interested_companies_are_applied_plus_shortlist_backups():
    rows = [
        row(job_no="1", company="光寶科技股份有限公司", score=77),
        dataclasses.replace(row(job_no="2", company="光寶科技"), in_shortlist=True),
        dataclasses.replace(row(job_no="3", company="和泰聯網股份有限公司"), in_shortlist=True),
        row(job_no="4", company="不投的公司"),
        row(job_no="5", company="待確認但不在清單"),
        dataclasses.replace(row(job_no="6", company="(等排程抓全文)"), in_shortlist=True),
    ]
    latest = {
        "1": decision("1", "apply"),
        "2": decision("2", "pending"),
        "3": decision("3", "pending"),
        "4": decision("4", "skip"),
        "5": decision("5", "pending"),
        "6": decision("6", "pending"),
    }
    got = interested_companies(rows, latest)
    assert [(c.name, len(c.jobs), c.applied) for c in got] == [
        ("光寶科技股份有限公司", 2, 1),  # 有標投的排前面;名稱取正式名
        ("和泰聯網股份有限公司", 1, 0),
    ]


def test_pick_targets_skips_fresh_and_running(tmp_path):
    store = CompanyStore(tmp_path / "companies.db")
    rows = [row(job_no=str(i), company=f"公司{i}") for i in range(4)]
    companies = interested_companies(rows, {str(i): decision(str(i), "apply") for i in range(4)})
    store.save_ok("公司0", review(), "sonnet", NOW)  # 新鮮 → 跳過
    store.save_ok("公司1", review(), "sonnet", NOW - timedelta(days=100))  # 過期 → 重查
    store.mark_running("公司2", NOW)  # 查詢中 → 跳過
    picked, pending = pick_targets(companies, store.all(), NOW, 90, limit=5)
    assert sorted(c.name for c in picked) == ["公司1", "公司3"]
    assert pending == 2


# ── 逐家查詢 ──────────────────────────────────────────────────────


class FakeClient:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.users: list[str] = []

    async def structured(self, *, user, **_):
        self.users.append(user)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out, 0.0


async def run(store, client, names):
    return await run_reviews(
        [(n, ["資料工程師"]) for n in names],
        store=store,
        client=client,
        model="sonnet",
        now_fn=lambda: NOW,
    )


async def test_review_is_sanitized_before_saving(tmp_path):
    store = CompanyStore(tmp_path / "companies.db")
    client = FakeClient(review(career_url="https://www.104.com.tw/company/x"))
    report = await run(store, client, ["光寶科技"])
    assert report.done == ["光寶科技"]
    assert store.all()[company_key("光寶科技")].review.career_url == ""
    assert "資料工程師" in client.users[0]  # 部門線索有傳進去


async def test_quota_exhaustion_stops_immediately(tmp_path):
    store = CompanyStore(tmp_path / "companies.db")
    client = FakeClient(ClaudeQuotaExhausted("usage limit"), review())
    report = await run(store, client, ["A", "B"])
    assert report.stopped_reason == "Claude 額度用完"
    assert len(client.users) == 1  # 不重試、不換下一家撞同一面牆
    assert store.all()[company_key("A")].status == "error"


async def test_consecutive_failures_stop_the_batch(tmp_path):
    store = CompanyStore(tmp_path / "companies.db")
    client = FakeClient(RuntimeError("x"), RuntimeError("y"), review())
    report = await run(store, client, ["A", "B", "C"])
    assert report.stopped_reason.startswith("連續失敗")
    assert len(client.users) == 2


# ── claude 權限:開網搜、封鎖 104 ────────────────────────────────


async def test_web_client_enables_search_and_blocks_104_at_permission_layer(fake_cli):  # noqa: F811
    seen, _ = fake_cli
    client = web_client(600)
    client.executable = "claude"
    with contextlib.suppress(Exception):  # 假回應不是 Facet 的形狀;這裡只看 argv
        await client.structured(
            model="sonnet", system="s", user="u", schema_model=Facet, schema_name="f"
        )
    argv = seen[0]["argv"]
    assert argv[argv.index("--tools") + 1] == ",".join(WEB_TOOLS)
    allowed = argv[argv.index("--allowedTools") + 1 : argv.index("--disallowedTools")]
    assert allowed == list(WEB_TOOLS)
    denied = argv[argv.index("--disallowedTools") + 1 :]
    assert all(d in denied for d in DENIED_TOOLS)
    assert "104.com.tw" in json.dumps(DENIED_TOOLS)
