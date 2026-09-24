# 使用者標記存在獨立的 decisions.db，Web UI 只讀 jobs.db 快照

**Status**：accepted（2026-09-24）

Web UI 的「投／不投／待確認」標記存在 `local_data/decisions.db`，不放在 `jobs.db` 裡。
Web UI 讀取 `jobs.db` 時，一律先複製成快照（`webui/snapshot.py`），再唯讀開啟快照。
原因是 pipeline 的 `Database`（`storage/db.py`）會把整個 `jobs.db` 複製到暫存區操作，結束時用 `os.replace` 整檔蓋回去。
所以：

1. **寫在 `jobs.db` 裡的標記會消失。** pipeline 執行期間 UI 寫入的內容，會在 pipeline 蓋回檔案時被覆蓋。
2. **UI 開著 `jobs.db`，會讓 pipeline 丟資料。** 在 Windows 上，只要 UI 開著這個檔案，`os.replace` 就會失敗。checkpoint 失敗只會被記進 log，那次抓到的資料和熔斷器狀態都會遺失，而且沒有任何其他跡象。
3. **定期清理會連帶刪掉標記。** `prune()` 刪掉舊職缺時，會透過 `ON DELETE CASCADE` 把有外鍵關聯的資料一起刪除。

## Considered Options

- **在 `jobs.db` 新增一張表，搭配短交易。** 這是規格原本的寫法。它解決不了上面三點：短交易擋不住整檔覆蓋。
- **在 `jobs.db` 新增一張表，pipeline 執行時禁止 UI 寫入。** 可以避開第 1 點，但第 2、3 點仍在，而且要另外做一套偵測「pipeline 是否正在執行」的機制。

## Consequences

- 標記用 `job_no` 對應職缺，但沒有外鍵，因為兩邊在不同檔案。`jobs.db` 清掉舊職缺後，標記仍會保留（可以拿來做長期的閘門檢討），只是 UI 上找不到對應的職缺。
- 快照只在 `jobs.db` 的 mtime 或大小改變時重新複製，每次只佔用原檔幾毫秒。偵測到 `jobs.db.tmp` 時代表 pipeline 正在寫回，這時沿用上一份快照，不去碰原檔。剩下的風險只有：pipeline checkpoint 剛好落在 UI 複製檔案的那幾毫秒內。
- `webui/` 不得 import `storage`（`tests/test_webui_boundary.py` 會擋）。`Database` 結束時會自動 checkpoint，UI 一旦用它，就會把快照寫回 `jobs.db`。
