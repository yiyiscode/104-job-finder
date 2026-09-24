"""週一選家用的本機 Web UI(Streamlit)。

邊界(``tests/test_webui_boundary.py`` 會驗):

* **不碰 104**:不 import ``scrape`` / ``http_source`` / ``pipeline`` / ``httpx``。
  抓取的請求預算、節流、熔斷只在 pipeline 裡,UI 按鈕會繞過它們。
* **不寫 jobs.db**:也不 import ``storage``。pipeline 會把整個 jobs.db 複製出去、
  跑完再 ``os.replace`` 蓋回來,寫在裡面的東西會被覆蓋。jobs.db 只讀快照,
  使用者的標記寫在獨立的 ``decisions.db``(見 docs/adr/0001)。
* 除了 ``app.py``,這裡的模組都不 import streamlit / pandas —— 邏輯要能在
  沒裝 ``[ui]`` 的環境裡離線測試。
"""
