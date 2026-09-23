"""評分層測試。

重點在**程式端的防幻覺機制**,不在 LLM 本身。SPEC.md §6.4:能用規則做的事就用規則做,
所以那些規則必須被測透 —— 尤其「專案豐富但年資 0–1 年」這個誤判組合。
"""

from __future__ import annotations

import json

import httpx
import pytest

from jobfinder.config import LLMCfg, ScoringCfg, load_config
from jobfinder.errors import LLMParseError
from jobfinder.models import JobDetail, JobSummary, ScoreBreakdown, ScoredJob
from jobfinder.scoring.fake import FakeScorer
from jobfinder.scoring.llm_client import CostTracker, OpenRouterClient, parse_structured
from jobfinder.scoring.prompts import (
    SYSTEM_DEEP,
    SYSTEM_SCREEN,
    build_deep_user,
    build_screen_user,
)
from jobfinder.scoring.resume import load_resume
from jobfinder.scoring.rules import (
    apply_guardrails,
    min_years_required,
    normalize_verdict,
    select_for_notification,
)
from jobfinder.scoring.schemas import DeepScore, ScreenResult, to_strict_schema
from jobfinder.scoring.stage1 import screen as run_screen

SCORING = ScoringCfg()


def make_job(**kw) -> JobSummary:
    base = {
        "job_no": "a1b2c",
        "detail_id": "94234",
        "job_name": "AI工程師",
        "cust_name": "示範公司",
        "job_url": "https://www.104.com.tw/job/a1b2c",
    }
    base.update(kw)
    return JobSummary(**base)


def make_deep(**kw) -> DeepScore:
    base = {
        "tech_fit": 30,
        "exp_fit": 20,
        "domain_fit": 12,
        "growth_fit": 12,
        "practical_fit": 8,
        "total_score": 82,
        "verdict": "apply",
        "one_liner": "一句話",
        "highlights": ["a"],
        "red_flags": [],
        "resume_tip": "建議",
    }
    base.update(kw)
    return DeepScore(**base)


# ═══ strict schema(最容易踩的雷)═══════════════════════════════════


@pytest.mark.parametrize("model", [ScreenResult, DeepScore])
def test_strict_schema_has_no_defs(model):
    """部分 provider 的 strict mode 不支援 $defs/$ref。"""
    text = json.dumps(to_strict_schema(model))
    assert "$defs" not in text
    assert "$ref" not in text


@pytest.mark.parametrize("model", [ScreenResult, DeepScore])
def test_strict_schema_locks_every_object_level(model):
    schema = to_strict_schema(model)

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)


def test_strict_schema_keeps_numeric_bounds():
    props = to_strict_schema(DeepScore)["properties"]
    assert props["tech_fit"]["maximum"] == 35
    assert props["exp_fit"]["maximum"] == 25


# ═══ 年資解析(壓制 LLM 誤判的關鍵)═════════════════════════════════


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("5年以上", 5),
        ("3 年以上工作經驗", 3),
        ("10年以上", 10),
        ("1~3年", 1),
        ("1-3 年", 1),
        ("不拘", 0),
        ("經歷不拘", 0),
        ("應屆畢業生歡迎", 0),
        ("無經驗可", 0),
        (None, None),
        ("", None),
        ("需具備相關背景", None),
    ],
)
def test_min_years_required(text, expected):
    assert min_years_required(text) == expected


def test_min_years_prefers_detail_over_summary():
    assert min_years_required("5年以上", "經歷不拘") == 5
    assert min_years_required(None, "3年以上") == 3


# ═══ 三道程式端校正(SPEC.md §6.4)═══════════════════════════════════


def test_senior_job_gets_exp_fit_clamped():
    """LLM 看到滿滿的專案會把新鮮人當資深 —— 這是最重要的一條護欄。"""
    detail = JobDetail(job_no="a1b2c", work_exp="8年以上")
    score = make_deep(exp_fit=24, total_score=86)
    breakdown, total, _, adjustments = apply_guardrails(score, make_job(), detail, SCORING)

    assert breakdown.exp_fit == SCORING.senior_exp_fit_cap
    assert total < 86
    assert any("hard rule" in a for a in adjustments)


def test_junior_friendly_job_is_left_alone():
    detail = JobDetail(job_no="a1b2c", work_exp="不拘")
    breakdown, total, _, adjustments = apply_guardrails(
        make_deep(exp_fit=23, total_score=85), make_job(), detail, SCORING
    )
    assert breakdown.exp_fit == 23
    assert total == 85
    assert adjustments == []


def test_total_is_recomputed_when_model_cannot_add_up():
    """便宜模型的算術常常錯,不信任它的加總。"""
    _, total, _, adjustments = apply_guardrails(
        make_deep(total_score=99), make_job(), None, SCORING
    )
    assert total == 82
    assert any("差距過大" in a for a in adjustments)


def test_small_arithmetic_drift_is_tolerated_silently():
    _, total, _, adjustments = apply_guardrails(
        make_deep(total_score=84), make_job(), None, SCORING
    )
    assert total == 82
    assert adjustments == []


def test_verdict_skip_downgrades_a_passing_score():
    """定性判斷比數值評分穩,矛盾時以 verdict 為準。"""
    _, total, verdict, adjustments = apply_guardrails(
        make_deep(verdict="skip"), make_job(), None, SCORING
    )
    assert total < SCORING.threshold
    assert verdict == "skip"
    assert any("定性判斷" in a for a in adjustments)


def test_unknown_verdict_means_no_signal_not_a_negative_one():
    """無法對應的 verdict 要回 None,不是 "maybe"。

    `maybe` 是負面訊號、會讓達標分數被降級。但模型把 verdict 寫成 `worth_a_shot`
    只代表它沒照格式寫,不代表它認為職缺不好 —— 拿它降級等於因為用字而懲罰職缺。
    (實測便宜模型 22 筆裡有 9 筆會這樣。)
    """
    assert normalize_verdict("worth_a_shot") is None
    assert normalize_verdict("definitely_apply") is None
    assert normalize_verdict("") is None
    # 大小寫與連字號的差異仍要吸收掉
    assert normalize_verdict("Strong-Apply") == "strong_apply"
    assert normalize_verdict(" apply ") == "apply"


def test_invalid_verdict_does_not_downgrade_a_passing_score():
    """這是上面那條規則實際會影響到的地方:85 分不該因為用字而掉到 69。"""
    score = make_deep(
        tech_fit=33,
        exp_fit=24,
        domain_fit=14,
        growth_fit=14,
        practical_fit=9,
        total_score=94,
    )
    # 繞過 Literal 驗證,模擬模型硬吐出非法值的情況
    object.__setattr__(score, "verdict", "worth_a_shot")

    _, total, verdict, adjustments = apply_guardrails(score, make_job(), None, SCORING)
    assert total == 94, "分數不該被無效的 verdict 拉下來"
    assert verdict == "maybe"  # 對外仍回一個合法值
    assert any("跳過 verdict 交叉驗證" in a for a in adjustments)


def test_verdict_schema_carries_an_enum_constraint():
    """沒有 enum 約束,便宜模型就會自己發明 verdict 值。"""
    props = to_strict_schema(DeepScore)["properties"]
    assert set(props["verdict"]["enum"]) == {"strong_apply", "apply", "maybe", "skip"}


# ═══ 推播挑選 ═══════════════════════════════════════════════════════


def scored_with(total: int, job_no: str) -> ScoredJob:
    return ScoredJob(
        summary=make_job(job_no=job_no),
        breakdown=ScoreBreakdown(
            tech_fit=10, exp_fit=10, domain_fit=5, growth_fit=5, practical_fit=5
        ),
        total=total,
        verdict="apply",
        one_liner="x",
    )


def test_threshold_mode_respects_daily_cap():
    jobs = [scored_with(90 - i, f"j{i}") for i in range(15)]
    cfg = ScoringCfg(threshold=70, max_per_day=10)
    passing, rejected = select_for_notification(jobs, cfg)

    assert len(passing) == 10
    assert passing[0].total == 90
    # 超過上限的沒有消失,退到未達標清單讓使用者仍看得到標題
    assert len(rejected) == 5


def test_threshold_mode_filters_low_scores():
    jobs = [scored_with(85, "a"), scored_with(40, "b")]
    passing, rejected = select_for_notification(jobs, ScoringCfg(threshold=70))
    assert [p.job_no for p in passing] == ["a"]
    assert [r.job_no for r in rejected] == ["b"]


def test_top_n_mode_ignores_absolute_threshold():
    """風險 3 的備案:分數校準不了時改用相對排序。"""
    jobs = [scored_with(t, f"j{t}") for t in (50, 45, 40, 30)]
    passing, _ = select_for_notification(jobs, ScoringCfg(mode="top_n", top_n=2))
    assert [p.total for p in passing] == [50, 45]


# ═══ LLM 輸出解析(三層保險的第 2 層)═══════════════════════════════


def test_parses_clean_json():
    assert parse_structured('{"results": []}', ScreenResult).results == []


def test_parses_json_wrapped_in_markdown_fence():
    text = '好的,以下是結果:\n```json\n{"results": []}\n```\n希望有幫助!'
    assert parse_structured(text, ScreenResult).results == []


def test_parses_json_with_surrounding_prose():
    text = '這是我的判斷 {"results": [{"job_no":"a","keep":true,"rough":80,"reason":"合"}]} 以上'
    result = parse_structured(text, ScreenResult)
    assert result.results[0].job_no == "a"


def test_unparseable_output_raises_with_the_raw_text():
    with pytest.raises(LLMParseError) as exc:
        parse_structured("我拒絕輸出 JSON", ScreenResult)
    assert "我拒絕輸出" in exc.value.raw


def test_out_of_range_score_is_rejected_by_schema():
    with pytest.raises(LLMParseError):
        parse_structured(json.dumps({**make_deep().model_dump(), "tech_fit": 99}), DeepScore)


# ═══ OpenRouter 客戶端 ══════════════════════════════════════════════


def make_client(handler, cfg: LLMCfg | None = None) -> OpenRouterClient:
    cfg = cfg or load_config("config/config.yaml").llm
    return OpenRouterClient(
        "key", cfg, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


def reply(content: str, cost: float = 0.001) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}}],
            "usage": {"cost": cost},
        },
    )


async def test_request_requires_structured_output_capable_provider():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return reply('{"results": []}')

    await make_client(handler).structured(
        model="m",
        system="s",
        user="u",
        schema_model=ScreenResult,
        schema_name="screen_result",
    )
    body = seen[0]
    assert body["provider"]["require_parameters"] is True
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["usage"]["include"] is True


async def test_malformed_output_triggers_a_repair_round():
    """第 3 層保險:把壞掉的輸出丟回去要求重輸出。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return reply("抱歉,我不太確定要輸出什麼格式")
        return reply('{"results": []}')

    result, cost = await make_client(handler).structured(
        model="m",
        system="s",
        user="u",
        schema_model=ScreenResult,
        schema_name="screen_result",
    )
    assert calls["n"] == 2
    assert result.results == []
    assert cost == pytest.approx(0.002)


async def test_repair_round_also_failing_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return reply("永遠不給 JSON")

    with pytest.raises(LLMParseError):
        await make_client(handler).structured(
            model="m",
            system="s",
            user="u",
            schema_model=ScreenResult,
            schema_name="screen_result",
        )


def test_cost_tracker_caps_spending():
    tracker = CostTracker(0.5)
    tracker.add(0.3)
    assert not tracker.exceeded
    tracker.add(0.3)
    assert tracker.exceeded


# ═══ 粗篩批次 ═══════════════════════════════════════════════════════


@pytest.fixture
def resume():
    return load_resume("profile.md")


async def test_screen_batches_and_keeps_only_passing_jobs(resume):
    cfg = load_config("config/config.yaml")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        user = body["messages"][1]["content"]
        results = [
            {
                "job_no": jn,
                "keep": jn != "bad",
                "rough": 20 if jn == "bad" else 80,
                "reason": "測試",
            }
            for jn in ("good", "bad")
            if f"[job_no] {jn}" in user
        ]
        return reply(json.dumps({"results": results}))

    jobs = [make_job(job_no="good"), make_job(job_no="bad")]
    outcome = await run_screen(make_client(handler), cfg.llm, cfg.scoring, resume, jobs)

    assert [j.job_no for j in outcome.keep] == ["good"]
    assert [r.job_no for r in outcome.dropped] == ["bad"]


async def test_screen_keeps_jobs_the_model_forgot_to_answer(resume):
    """便宜模型批次時會漏回 job_no。靜默弄丟一個好缺比多花一點深評成本糟糕得多。"""
    cfg = load_config("config/config.yaml")

    def handler(request: httpx.Request) -> httpx.Response:
        return reply(json.dumps({"results": []}))

    outcome = await run_screen(
        make_client(handler), cfg.llm, cfg.scoring, resume, [make_job(job_no="ghost")]
    )
    assert [j.job_no for j in outcome.keep] == ["ghost"]
    assert any("未回應" in w for w in outcome.warnings)


async def test_screen_survives_a_failing_batch(resume):
    cfg = load_config("config/config.yaml")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "upstream"})

    outcome = await run_screen(make_client(handler), cfg.llm, cfg.scoring, resume, [make_job()])
    assert outcome.warnings
    assert len(outcome.keep) == 1  # 失敗時保守保留


async def test_screen_of_empty_list_is_a_noop(resume):
    cfg = load_config("config/config.yaml")

    def handler(request):  # pragma: no cover - 不該被呼叫
        raise AssertionError("空清單不該打 API")

    outcome = await run_screen(make_client(handler), cfg.llm, cfg.scoring, resume, [])
    assert outcome.keep == [] and outcome.cost_usd == 0.0


# ═══ 履歷與 prompt ══════════════════════════════════════════════════


def test_resume_brief_is_much_shorter_than_full(resume):
    assert len(resume.brief) < len(resume.full)
    assert "林芳義" in resume.brief


def test_deep_prompt_hammers_the_zero_years_framing():
    """這句是壓制『把新鮮人當資深』誤判的核心,不該被無意改掉。"""
    assert "正式工作年資約 0-1 年" in SYSTEM_DEEP
    assert "不等於正職年資" in SYSTEM_DEEP


def test_prompts_score_through_a_data_engineering_lens():
    """2026-09-23:主敘事從 AI 換成資料工程(profile-de.md)。

    只換 config 的關鍵字是不夠的 —— prompt 還用 AI 的尺在量,
    資料工程職缺就會因為「沒提到 LLM」而被低估。這幾句是那次改版的錨點。
    """
    assert "資料工程" in SYSTEM_SCREEN
    # tech_fit 的高權重必須是 DE 的技術棧,不是 LLM/RAG
    assert "ETL" in SYSTEM_DEEP
    assert "SQL" in SYSTEM_DEEP
    # 沒實作過的大數據工具要扣分但不歸零 —— 他有等價的自建管線
    assert "Airflow" in SYSTEM_DEEP
    assert "不要歸零" in SYSTEM_DEEP


def test_deep_prompt_names_the_two_target_industries():
    """半導體與金融是 targeting 層硬篩出來的產業,domain_fit 要有對應的實績依據,
    否則模型只會看產業名稱猜,而他在這兩個產業都有真實專案。"""
    assert "半導體" in SYSTEM_DEEP
    assert "國泰人壽" in SYSTEM_DEEP


def test_deep_prompt_location_matches_search_areas():
    """搜尋參數已含台中(config 的 areas),prompt 若還寫「雙北桃竹」,
    台中的職缺會在 practical_fit 被莫名扣分。"""
    assert "台中" in SYSTEM_DEEP


def test_deep_prompt_falls_back_when_detail_is_missing(resume):
    text = build_deep_user(resume.full, make_job(desc_snippet=None), None)
    assert "未取得詳細工作內容" in text


def test_screen_prompt_lists_every_job_no(resume):
    jobs = [make_job(job_no=f"j{i}") for i in range(5)]
    text = build_screen_user(resume.brief, jobs)
    assert all(f"[job_no] j{i}" in text for i in range(5))


# ═══ 假評分器 ═══════════════════════════════════════════════════════


async def test_fake_scorer_drops_non_technical_jobs():
    outcome = await FakeScorer().screen(
        [make_job(job_no="a", job_name="AI業務代表"), make_job(job_no="b")]
    )
    assert [j.job_no for j in outcome.keep] == ["b"]


async def test_fake_scorer_penalises_senior_requirements():
    scorer = FakeScorer()
    junior, _ = await scorer.deep(make_job(), JobDetail(job_no="a1b2c", work_exp="不拘"))
    senior, _ = await scorer.deep(make_job(), JobDetail(job_no="a1b2c", work_exp="8年以上"))
    assert junior.total > senior.total
