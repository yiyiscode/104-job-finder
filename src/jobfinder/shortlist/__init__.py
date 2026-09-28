"""投遞清單:使用者手動挑的職缺(例如 ``apply-shortlist-2026-09-23.md``)。

很多職缺不在 pipeline 的關鍵字裡(台積電 IT Data Engineer、日月光 SD…),
jobs.db 沒有它們。這個套件記「清單裡有哪些」,並用一次性指令補抓詳細頁。

* ``store``:只碰 ``shortlist.db``。UI 與命中率可以 import
* ``fetch``:會連 104。**只有 CLI 可以 import**(``tests/test_webui_boundary.py`` 會擋)
"""
