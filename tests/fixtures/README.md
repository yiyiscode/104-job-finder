# tests/fixtures

## ✅ 這些是真實資料

2026-08-21 從 104 錄下的**真實 API 回應**,不是手工建的樣本。
`normalize.py` 的每一條欄位對應都是對著它們驗證出來的。

錄製方式:`curl` 帶 `Referer: https://www.104.com.tw/jobs/search/`,總共 5 個請求。
**之後就沒有再碰過 104。**

## 為什麼要有 fixture

SPEC.md 規則 6:**開發期絕不反覆打 104**。

有了 fixture,整個開發流程(normalize、去重、評分 prompt、Telegram 排版)
都能離線反覆迭代,不需要為了 debug 一個排版問題就再去爬一次。
這是保護使用者家用 IP 的主要手段,不只是「測試寫得漂亮」而已。

## 命名規則

詳細頁一律命名為 **`detail_{detail_id}.json`**,`detail_id` 是職缺連結的尾段。

⚠️ **不是 `job_no`。** 這兩個是不同的 ID:同一則職缺 `jobNo=15305872`,
但連結是 `/job/94234`,而詳細頁 API 只吃後者(拿 `jobNo` 打會回 404)。
fixture 的命名跟著真實 API 的 key 走,才不會養出「本機會過、上線就 404」的假象。

`--from-fixtures` 只做**嚴格對應**:找不到對應檔案就不給 detail,而不是隨便套一份別的。
套錯會讓「8年以上」的資深缺配到「經歷不拘」的詳細頁,預覽出來的分數完全是假的,
比沒有 detail 更糟。評分端本來就有「沒有詳細內容時保守評估」的路徑,走那條才誠實。

## 檔案

| 檔案 | 用途 |
|---|---|
| `search_page1.json` | 搜尋列表 API 回應(`AI工程師` / 台北 / `jobexp=1,3` / `isnew=3`)。32 筆,涵蓋月薪、年薪、面議三種薪資類型與 0/2/3 年的年資 |
| `search_exp10.json` | 同樣的搜尋但 `jobexp=10`(5~10 年)。用來證明 **`period` 是實際年資數字而非級距代碼** —— 這組回傳 6~9,前一組回傳 0/2/3 |
| `detail_94234.json` | 結構完整的詳細頁(`optionEdu=[4]` → `edu="大學"`,月薪) |
| `detail_94xn5.json` | `optionEdu=[3,4,5,6]` → `edu="專科以上"`,證明多碼的呈現慣例 |
| `detail_8yp7c.json` | `optionEdu=[5]` → `edu="碩士"`,釘死學歷代碼對照 |
| `detail_404_error.json` | 拿 `job_no` 打詳細頁的錯誤回應。驗證 normalize 記 drift 而不是 crash |

## 這些 fixture 抓出過的真 bug

- `data` 本身就是陣列,沒有 `data.list`
- `job_no` ≠ `detail_id`,詳細頁全部會 404
- 沒有 `salaryDesc`,而且 `s10` 是薪資類型 —— 不看它會把**年薪 567,000 顯示成「月薪 56 萬」**
- `description` 含換行,會打散批次粗篩 prompt 的「每筆四行」結構
- `tags` 的 `desc` 為空時退回 `param` 會撈出 `wf1`/`wf7` 這種內部代碼
