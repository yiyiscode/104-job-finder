"""錄下 104 的真實 API 回應存成 fixture。

**這個腳本一輩子只該跑 1–2 次。** 錄完之後,normalize、去重、評分 prompt、
Telegram 排版全部可以離線反覆迭代,不需要再碰 104 一次(SPEC.md 規則 6)。

它是從**你家的網路**打出去的,所以刻意設得比 Modal 上更保守:
最多 3 次導覽、有頭瀏覽器讓你親眼看著、已有 fixture 就拒絕執行。

    python scripts/probe_api.py                 # 第一次
    python scripts/probe_api.py --force         # 明知故犯地覆寫

⚠️ 過程中**不要登入你的 104 帳號**。腳本會在抓取前檢查,發現登入態會直接中止。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobfinder.config import ScrapeCfg, load_config  # noqa: E402
from jobfinder.errors import JobFinderError  # noqa: E402
from jobfinder.logging_conf import setup_logging, use_utf8_console  # noqa: E402
from jobfinder.normalize import (  # noqa: E402
    normalize_detail_response,
    normalize_search_response,
)
from jobfinder.scrape.browser import browser_context, check_page_not_blocked  # noqa: E402
from jobfinder.scrape.budget import RequestBudget  # noqa: E402
from jobfinder.scrape.interceptor import ResponseInterceptor  # noqa: E402
from jobfinder.scrape.urls import build_job_url, build_search_page_url  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

EXPECTED_SUMMARY_FIELDS = [
    "jobNo",
    "jobName",
    "custName",
    "custNo",
    "jobAddrNoDesc",
    "salaryDesc",
    "salaryLow",
    "salaryHigh",
    "periodDesc",
    "optionEdu",
    "appearDate",
    "link",
    "descSnippet",
    "applyCnt",
    "tags",
]
EXPECTED_DETAIL_PATHS = [
    "data.jobDetail.jobDescription",
    "data.jobDetail.salary",
    "data.jobDetail.jobCategory",
    "data.jobDetail.addressRegion",
    "data.condition.workExp",
    "data.condition.edu",
    "data.condition.major",
    "data.condition.specialty",
    "data.condition.other",
    "data.welfare.tag",
    "data.industry",
]


def _has(payload, dotted: str) -> bool:
    node = payload
    for key in dotted.split("."):
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    return True


def _report(title: str, present: list[str], missing: list[str]) -> None:
    print(f"\n── {title} ──")
    for name in present:
        print(f"  ✅ {name}")
    for name in missing:
        print(f"  ❌ {name}  ← normalize.py 預期有這個欄位,實際沒有")


async def probe(keyword: str, job_no: str | None) -> int:
    cfg = load_config("config/config.yaml")

    # 比正式執行更嚴的預算:這是從使用者家用 IP 打出去的
    tight = ScrapeCfg(
        **{
            **cfg.scrape.model_dump(),
            "max_navigations_per_run": 3,
            "max_pages_per_keyword": 1,
            "max_details_per_run": 1,
            "headless": False,
        }
    )
    cfg = cfg.model_copy(update={"scrape": tight})
    budget = RequestBudget(tight)

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    async with browser_context(cfg, budget, data_dir="local_data", headless=False) as context:
        page = await context.new_page()
        interceptor = ResponseInterceptor(
            page,
            {
                "search": tight.search_api_pattern,
                "detail": tight.detail_api_pattern,
            },
        ).attach()

        # ── 搜尋列表 ────────────────────────────────────────────
        url = build_search_page_url(cfg.search, keyword, 1)
        print(f"\n開啟搜尋頁:{url}")
        response = await budget.navigate(page, url, delay="none", kind="search")
        await check_page_not_blocked(page, response)

        payload = await interceptor.wait_for("search", tight.api_wait_seconds)
        if payload is None:
            print("\n❌ 沒攔截到搜尋 API 回應。該頁實際觀察到的 XHR:")
            for observed in dict.fromkeys(interceptor.observed_urls):
                print(f"   {observed}")
            print(
                f"\n若上面有看起來像職缺列表的路徑,請把 config.yaml 的"
                f" scrape.search_api_pattern 從 {tight.search_api_pattern!r} 改成它。"
            )
            return 1

        (FIXTURE_DIR / "search_page1.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"✅ 已存 {FIXTURE_DIR / 'search_page1.json'}")

        rows = (payload.get("data") or {}).get("list") or []
        print(
            f"   totalCount={payload.get('data', {}).get('totalCount')}"
            f" totalPage={payload.get('data', {}).get('totalPage')}"
            f" 本頁 {len(rows)} 筆"
        )

        if rows:
            present = [f for f in EXPECTED_SUMMARY_FIELDS if f in rows[0]]
            missing = [f for f in EXPECTED_SUMMARY_FIELDS if f not in rows[0]]
            _report("搜尋列表欄位對照", present, missing)
            extra = sorted(set(rows[0]) - set(EXPECTED_SUMMARY_FIELDS))
            if extra:
                print(f"  ℹ️  額外欄位(不影響):{', '.join(extra[:15])}")

        parsed = normalize_search_response(payload, keyword)
        print(f"\nnormalize 解析出 {len(parsed.jobs)} 筆職缺")
        for note in parsed.drift:
            print(f"  ⚠️  {note}")

        # ⚠️ 用 detail_id 不是 job_no —— 詳細頁 API 拿 job_no 打會回 404
        target = job_no or (parsed.jobs[0].detail_id if parsed.jobs else None)
        if not target:
            print("\n沒有可用的 job_no,略過詳細頁。")
            return 0

        # ── 職缺詳細 ────────────────────────────────────────────
        interceptor.clear("detail")
        detail_url = build_job_url(target)
        print(f"\n開啟職缺頁:{detail_url}")
        response = await budget.navigate(page, detail_url, delay="detail", kind="detail")
        await check_page_not_blocked(page, response)

        detail_payload = await interceptor.wait_for("detail", tight.api_wait_seconds)
        if detail_payload is None:
            print("\n❌ 沒攔截到詳細頁 API 回應。實際觀察到的 XHR:")
            for observed in dict.fromkeys(interceptor.observed_urls):
                print(f"   {observed}")
            return 1

        out = FIXTURE_DIR / f"detail_{target}.json"
        out.write_text(json.dumps(detail_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ 已存 {out}")

        present = [p for p in EXPECTED_DETAIL_PATHS if _has(detail_payload, p)]
        missing = [p for p in EXPECTED_DETAIL_PATHS if not _has(detail_payload, p)]
        _report("詳細頁欄位對照", present, missing)

        drift: list[str] = []
        detail = normalize_detail_response(detail_payload, target, drift)
        print(
            f"\nnormalize 解析結果:年資={detail.work_exp!r} 學歷={detail.edu!r}"
            f" 技能={detail.specialty[:5]}"
        )
        for note in drift:
            print(f"  ⚠️  {note}")

        interceptor.detach()

    print(f"\n完成。共 {budget.navigations_used} 次導覽。")
    print("接下來請改用 `jobfinder run --from-fixtures`,不要再跑這個腳本。")
    if missing:
        print("\n⚠️ 有欄位對不上,請依上面的實際結構調整 src/jobfinder/normalize.py。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keyword", default="AI工程師")
    parser.add_argument(
        "--job-no",
        dest="detail_id",
        help="指定要抓詳細頁的職缺代碼(連結尾段,如 94234;不是 jobNo)",
    )
    parser.add_argument("--force", action="store_true", help="已有 fixture 時仍覆寫")
    args = parser.parse_args()

    use_utf8_console()
    setup_logging("INFO")

    existing = sorted(FIXTURE_DIR.glob("search_page*.json"))
    if existing and not args.force:
        print(
            f"已經有錄好的 fixture 了({', '.join(p.name for p in existing)})。\n"
            "SPEC.md 規則 6:開發期絕不反覆打 104 —— 請改用"
            " `jobfinder run --from-fixtures`。\n"
            "真的要重錄請加 --force。"
        )
        return 0

    print(
        "\n⚠️  接下來會開啟瀏覽器連到 104(從你家的網路)。\n"
        "    請【不要登入】你的 104 帳號,也不要點應徵或收藏。\n"
        "    腳本會在抓取前自動檢查,偵測到登入態會直接中止。\n"
    )

    try:
        return asyncio.run(probe(args.keyword, args.detail_id))
    except JobFinderError as exc:
        print(f"\n❌ {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
