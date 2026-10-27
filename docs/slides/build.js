// 104 職缺日報 —— 專案介紹簡報產生器(pptxgenjs,結構化:theme + layouts + sections)
const path = require("path");
const fs = require("fs");
const pptxgen = require("pptxgenjs");
const React = require("react");
const RDS = require("react-dom/server");
const sharp = require("sharp");
const fa = require("react-icons/fa6");
const JSZip = require("jszip");
const { applyTheme } = require("./apply_theme.js");

const OUT = process.argv[2] || "job-finder-intro.pptx";
const SHOT = "D:/side_projects/Job Finder/docs/images/webui-candidates.png";
const FONT = "Microsoft JhengHei";

const THEME = {
  name: "Ink and Signal",
  headFontFace: FONT,
  bodyFontFace: FONT,
  colors: {
    dk1: "14212B", lt1: "FFFFFF", dk2: "1F3A4A", lt2: "EEF3F4",
    accent1: "1E7F7A", accent2: "E89B2D", accent3: "C8473F",
    accent4: "6C8EA0", accent5: "CFE6E3", accent6: "3D5A6C",
    hlink: "1E7F7A", folHlink: "6C8EA0",
  },
};
const HEX = THEME.colors;
const INK_BG = HEX.dk1;

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5
pres.author = "林芳義 Fang-Yi Lin";
pres.title = "104 職缺日報:自動化求職資料管線";
pres.theme = { headFontFace: FONT, bodyFontFace: FONT };
const C = pres.SchemeColor;

// ---------- layouts ----------
pres.defineSlideMaster({
  title: "TITLE_DARK",
  background: { color: INK_BG },
  objects: [
    { placeholder: { options: { name: "title", type: "title", align: "left", x: 0.8, y: 1.55, w: 11.7, h: 1.6, fontSize: 40, bold: true, color: C.background1, valign: "bottom", margin: 0 }, text: "" } },
    { placeholder: { options: { name: "body", type: "body", align: "left", x: 0.8, y: 3.3, w: 11.7, h: 0.8, fontSize: 20, color: C.accent5, valign: "top", margin: 0 }, text: "" } },
  ],
});
pres.defineSlideMaster({
  title: "SECTION_DARK",
  background: { color: INK_BG },
  objects: [
    { placeholder: { options: { name: "title", type: "title", align: "left", x: 0.8, y: 2.75, w: 11.7, h: 1.1, fontSize: 40, bold: true, color: C.background1, valign: "middle", margin: 0 }, text: "" } },
    { placeholder: { options: { name: "body", type: "body", align: "left", x: 0.8, y: 3.95, w: 11.7, h: 0.9, fontSize: 20, color: C.accent5, valign: "top", margin: 0 }, text: "" } },
  ],
  slideNumber: { x: 12.3, y: 6.95, w: 0.6, h: 0.3, fontSize: 10, color: "8FA3AE", align: "right" },
});
pres.defineSlideMaster({
  title: "CONTENT",
  background: { color: HEX.lt1 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", align: "left", x: 0.6, y: 0.4, w: 12.1, h: 0.85, fontSize: 30, bold: true, color: C.text1, valign: "middle", margin: 0 }, text: "" } },
    { text: { text: "104 職缺日報 · 林芳義", options: { x: 0.6, y: 6.95, w: 6, h: 0.3, fontSize: 10, color: C.accent4, margin: 0 } } },
  ],
  slideNumber: { x: 12.1, y: 6.95, w: 0.6, h: 0.3, fontSize: 10, color: HEX.accent4, align: "right" },
});

// ---------- helpers ----------
// 中文語境的半形標點轉全形(數字、程式碼、URL 裡的不動)
const HAN = /[㐀-鿿]/;
function zh(t) {
  if (typeof t !== "string") return t;
  return t
    .replace(/\(([^()\n]*)\)/g, (m, inner, off, str) => (HAN.test(inner) || HAN.test(str[off - 1] || "") ? "（" + inner + "）" : m))
    .replace(/(?<=[㐀-鿿」）%])[,:;]|[,:;](?=[㐀-鿿「（])/g, c => ({ ",": "，", ":": "：", ";": "；" })[c])
    .replace(/\|/g, " · ");
}
function zhRuns(x) {
  if (typeof x === "string") return zh(x);
  return x.map(r => (typeof r === "string" ? zh(r) : { ...r, text: zh(r.text) }));
}
function zhRows(rows) {
  return rows.map(r => r.map(c => (typeof c === "string" ? zh(c) : { ...c, text: zh(c.text) })));
}
const iconCache = {};
async function iconData(name, color = "#FFFFFF") {
  const key = name + color;
  if (!iconCache[key]) {
    const svg = RDS.renderToStaticMarkup(React.createElement(fa[name], { color, size: 256 }));
    const buf = await sharp(Buffer.from(svg)).resize(256, 256, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } }).png().toBuffer();
    iconCache[key] = "image/png;base64," + buf.toString("base64");
  }
  return iconCache[key];
}
async function iconCircle(s, name, x, y, d, fill = C.accent1, id = name) {
  s.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: fill }, line: { type: "none" }, objectName: "icon-bg-" + id });
  const k = d * 0.5;
  s.addImage({ data: await iconData(name), x: x + (d - k) / 2, y: y + (d - k) / 2, w: k, h: k, altText: id, objectName: "icon-" + id });
}
function T(s, text, o = {}) {
  s.addText(zhRuns(text), { isTextBox: true, margin: 0, fontSize: 16, lang: "zh-TW", color: C.text1, valign: "top", ...o });
}
function card(s, x, y, w, h, fill = C.background2, name = "card") {
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.08, fill: { color: fill }, line: { type: "none" }, objectName: name });
}
function bullets(items, o = {}) {
  return items.map((t, i) => {
    const base = { bullet: { indent: 16 }, paraSpaceAfter: 6, breakLine: i < items.length - 1 };
    if (typeof t === "string") return { text: t, options: base };
    return { text: t.text, options: { ...base, ...t.options } };
  });
}
function content(title, section) {
  const s = pres.addSlide({ masterName: "CONTENT", sectionTitle: section });
  s.addText(zh(title), { placeholder: "title" });
  return s;
}
function divider(num, title, sub, section) {
  pres.addSection({ title: section });
  const s = pres.addSlide({ masterName: "SECTION_DARK", sectionTitle: section });
  T(s, num, { x: 0.8, y: 2.2, w: 3, h: 0.5, fontSize: 22, bold: true, color: C.accent2 });
  s.addText(zh(title), { placeholder: "title" });
  s.addText(zh(sub), { placeholder: "body" });
  return s;
}
function N(s, text) {
  s.addNotes(zh(text));
}
function caption(s, text, y = 6.45) {
  T(s, text, { x: 0.6, y, w: 12.1, h: 0.35, fontSize: 11, color: C.accent6, italic: true });
}

(async () => {
  // =============== 開場 ===============
  let SEC = "開場";
  pres.addSection({ title: SEC });

  // 1. 封面
  let s = pres.addSlide({ masterName: "TITLE_DARK", sectionTitle: SEC });
  s.addText(zh("把求職變成一條每天自動跑的資料管線"), { placeholder: "title" });
  s.addText(zh("104 職缺日報|抓取 → 清洗 → 過濾 → LLM 評分 → 推播"), { placeholder: "body" });
  {
    const nodes = ["FaClock", "FaSpider", "FaDatabase", "FaFilter", "FaRobot", "FaPaperPlane"];
    const x0 = 0.8, y0 = 4.65, d = 0.62, gap = 1.05;
    s.addShape(pres.shapes.LINE, { x: x0 + d / 2, y: y0 + d / 2, w: gap * (nodes.length - 1), h: 0, line: { color: HEX.accent6, width: 2 }, objectName: "pipeline-line" });
    for (let i = 0; i < nodes.length; i++) await iconCircle(s, nodes[i], x0 + i * gap, y0, d, i === nodes.length - 1 ? C.accent2 : C.accent1, "cover-" + i);
  }
  T(s, "林芳義 Fang-Yi Lin|資料工程・流程自動化|github.com/yiyiscode", { x: 0.8, y: 6.3, w: 11.7, h: 0.4, fontSize: 14, color: "B7C6CE" });
  N(s, `【講稿】
大家好,我是林芳義。今天想用一個我自己每天都在用的系統,說明我怎麼做資料工程:它每天早上 8 點自動到 104 抓職缺、清洗、過濾,用 LLM 對照我的履歷評分,最後只把值得投的推到我的手機。

【對 HR 的一句話】
這是我為了自己求職做的工具,2026-08-22 上線,到 10-25 已有 46 次實際連線執行(橫跨 45 天),不是課堂作業。

【對技術主管的一句話】
重點不只是「會串 LLM」,而是:對外部資料源負責的護欄、未公開 API 的資料契約,以及「先量測、再下結論」的工程判斷。

【這頁的概念】
資料管線(data pipeline):資料從來源出發,依序經過擷取(extract)→ 轉換(transform)→ 載入/輸出(load)的固定流程,每一段的輸入輸出都定義清楚,可以單獨測試、單獨重跑。`);

  // 2. 一頁總結
  s = content("一句話:每天 08:00,自動幫我挑出值得投的職缺", SEC);
  {
    const stats = [
      ["46 次", "實際連線 104 執行", "2026-08-22 ~ 10-25"],
      ["2,623 筆", "累計不重複職缺", "來自 1,490 家公司"],
      ["4.7%", "最後推播的比例", "09-23 改版後 24 次執行"],
      ["US$1.28", "評分的 LLM 實付費用", "命中率另計;10-06 起改用 Claude"],
    ];
    const w = 2.8, gap = 0.3, x0 = 0.6, y = 1.75, h = 2.6;
    stats.forEach(([big, label, sub], i) => {
      const x = x0 + i * (w + gap);
      card(s, x, y, w, h, C.background2, "stat-" + i);
      T(s, big, { x: x + 0.25, y: y + 0.35, w: w - 0.5, h: 0.95, fontSize: 40, bold: true, color: i === 2 ? C.accent2 : C.accent1, valign: "middle", fit: "shrink" });
      T(s, label, { x: x + 0.25, y: y + 1.4, w: w - 0.5, h: 0.5, fontSize: 16, bold: true });
      T(s, sub, { x: x + 0.25, y: y + 1.9, w: w - 0.5, h: 0.45, fontSize: 12, color: C.accent6 });
    });
    await iconCircle(s, "FaCircleCheck", 0.6, 4.85, 0.6, C.accent1, "real");
    T(s, [
      { text: "真實上線運作,不是 demo。", options: { bold: true, breakLine: true } },
      { text: "Windows 排程每天 08:00 觸發、錯過開機補跑;674 個離線測試守住每一道護欄。", options: { color: C.accent6 } },
    ], { x: 1.45, y: 4.82, w: 11.2, h: 0.9, fontSize: 16 });
    caption(s, "資料:local_data/jobs.db 的 runs 表,只計 requests_used > 0 的實際連線執行(截至 2026-10-25 15:39 那一次)");
  }
  N(s, `【講稿】
先給結論:這個系統跑了 46 次真的連線 104 的執行,累計看過 2,623 筆不重複職缺。改版後,新職缺只有 4.7% 會被推到我手機上;評分用的 LLM 實付費用 1.28 美元。

【數字口徑(被問到時要能答)】
- 46 次:runs 表裡 status 為 success/partial 且 requests_used > 0。離線重播與測試不算。
- 2,623 筆 / 1,490 家:jobs 表 count(*) 與 count(DISTINCT cust_no)。
- 4.7%:09-23 加上產業/規模規則層之後的 24 次執行,新職缺 1,481 → 推播 70。全期(含改版前)是 210/2,688 ≈ 7.8%。
- US$1.28:runs.llm_cost_usd 加總,是粗篩 + 深評的 OpenRouter 實付。10-06 起評分改用 Claude 訂閱,不另計費。
- 不含命中率(hitrate):它仍走 OpenRouter,成本另存 hitrate.db,目前留存的 719 筆合計約 US$1.12(改 prompt 時清過舊結果,所以這是下限)。

【概念】
「只計實際連線」是刻意的:離線重播(--replay)會重跑評分但不連 104,混進來會讓請求數、耗時都失真。`);

  // 3. 問題
  s = content("問題:職缺一直冒出來,大部分跟我的方向無關", SEC);
  {
    const y = 1.6, h = 3.5, w = 5.45;
    card(s, 0.6, y, w, h, C.background2, "before");
    await iconCircle(s, "FaHourglassHalf", 0.9, y + 0.3, 0.7, C.accent6, "before");
    T(s, "以前:人工", { x: 1.8, y: y + 0.38, w: 4, h: 0.55, fontSize: 22, bold: true, valign: "middle" });
    T(s, bullets([
      "每次搜回約 350 筆,逐筆看列表與 JD",
      "每天約 1 小時(自己估計的基準)",
      "關鍵字是全文比對,撈回大量不相關職缺",
      "好職缺混在雜訊裡,容易漏看",
    ]), { x: 0.95, y: y + 1.3, w: w - 0.7, h: 2.0, fontSize: 16 });

    s.addShape(pres.shapes.RIGHT_ARROW, { x: 6.27, y: y + 1.45, w: 0.75, h: 0.6, fill: { color: C.accent2 }, line: { type: "none" }, objectName: "arrow" });

    const x2 = 7.25;
    card(s, x2, y, w, h, C.accent5, "after");
    await iconCircle(s, "FaBolt", x2 + 0.3, y + 0.3, 0.7, C.accent1, "after");
    T(s, "現在:自動", { x: x2 + 1.2, y: y + 0.38, w: 4, h: 0.55, fontSize: 22, bold: true, valign: "middle" });
    T(s, bullets([
      "08:00 自動執行,錯過開機後補跑",
      "全流程中位數約 4 分鐘(含刻意的節流等待)",
      "產業代碼 + 員工數先過濾,LLM 只看值得看的",
      "手機只收到當天前 5 名,附評分理由",
    ]), { x: x2 + 0.35, y: y + 1.3, w: w - 0.7, h: 2.0, fontSize: 16 });
  }
  caption(s, "350 筆:每次執行跨關鍵字去重後的中位數(10-09 前 35 次,docs/resume-metrics.md);4 分鐘:runs 表 started_at → finished_at 中位數 243 秒");
  N(s, `【講稿】
每次搜尋會撈回約 350 筆職缺,其中第一次出現的中位數是 43 筆(大日子可到 238 筆),但我的方向很窄:資料工程、半導體或金融的大公司。人工逐筆看,我估計一天要花一小時。現在整個流程中位數約 4 分鐘,而且不用我在場。

【誠實註記】
「每天 1 小時」是我自評的手動基準,不是量測值。面試被問就照實說。
「4 分鐘」大半是刻意的等待:每個請求之間至少間隔 3 秒,這是為了不給 104 添負擔,不是效能不足。

【概念:為什麼關鍵字撈回不相關的】
104 的關鍵字搜尋是全文模糊比對,JD 裡只要出現「資料工程」四個字就會被撈回來,所以搜「資料工程師」也會撈回 AI 研發工程師。這是後面「工程判斷 1」的伏筆。`);

  // =============== 01 系統 ===============
  SEC = "01 系統怎麼運作";
  s = divider("01", "系統怎麼運作", "一條單向的資料管線,加上一個做決策用的本機介面", SEC);
  N(s, "【轉場】先看整條管線長什麼樣,再看每一段怎麼把職缺量從上千筆收斂到個位數。");

  // 5. 架構
  s = content("架構:護欄排在所有請求之前,每一段都可以單獨換掉", SEC);
  {
    const steps = [
      ["FaClock", "排程", "每天 08:00\n錯過開機補跑"],
      ["FaShieldHalved", "三道護欄", "緊急開關\n熔斷器・每日一次"],
      ["FaSpider", "抓取", "逐筆送出\n節流 + 預算上限"],
      ["FaDatabase", "正規化・去重", "SQLite\n只留今天新的"],
      ["FaFilter", "規則層", "產業代碼\n+ 員工數 ≥500"],
      ["FaRobot", "LLM 評分", "粗篩 15 筆一批\n深評逐筆讀 JD"],
      ["FaPaperPlane", "推播", "Telegram\n當天前 5 名"],
    ];
    const d = 0.85, x0 = 0.75, gap = 1.75, y = 1.75;
    s.addShape(pres.shapes.LINE, { x: x0 + d / 2, y: y + d / 2, w: gap * (steps.length - 1), h: 0, line: { color: HEX.accent4, width: 2, endArrowType: "triangle" }, objectName: "flow-line" });
    for (let i = 0; i < steps.length; i++) {
      const [ic, name, desc] = steps[i];
      const cx = x0 + i * gap;
      await iconCircle(s, ic, cx, y, d, i === 1 ? C.accent3 : i === 6 ? C.accent2 : C.accent1, "flow-" + i);
      T(s, name, { x: cx + d / 2 - 0.8, y: y + d + 0.15, w: 1.6, h: 0.4, fontSize: 15, bold: true, align: "center" });
      T(s, desc, { x: cx + d / 2 - 0.8, y: y + d + 0.55, w: 1.6, h: 0.75, fontSize: 12, color: C.accent6, align: "center" });
    }
    // 下方兩張重點卡
    const cy = 4.35, ch = 1.9, cw = 5.9;
    card(s, 0.6, cy, cw, ch, C.background2, "point-io");
    T(s, [
      { text: "對外 IO 全靠注入", options: { bold: true, fontSize: 17, breakLine: true } },
      { text: "抓 104、呼叫 LLM、推播都靠注入,流程只讀寫本機資料庫。抓取方式曾從「真實瀏覽器」整個換成「純 HTTP」,流程、儲存、評分、推播一行都沒改。", options: { fontSize: 14, color: C.accent6 } },
    ], { x: 0.85, y: cy + 0.2, w: cw - 0.5, h: ch - 0.35, paraSpaceAfter: 6 });
    card(s, 6.8, cy, cw, ch, C.background2, "point-ui");
    T(s, [
      { text: "Web UI 只讀資料庫的快照", options: { bold: true, fontSize: 17, breakLine: true } },
      { text: "每週一在本機挑這週要投哪幾家。只綁 127.0.0.1、不會連 104;標記另存獨立資料庫,不跟每日排程搶檔案。", options: { fontSize: 14, color: C.accent6 } },
    ], { x: 7.05, y: cy + 0.2, w: cw - 0.5, h: ch - 0.35, paraSpaceAfter: 6 });
  }
  N(s, `【講稿】
由左到右:排程觸發後,先過三道護欄才會發出任何請求;抓回來的資料正規化、去重,只留今天新出現的;接著規則層用產業代碼和員工數先砍掉大半;剩下的才交給 LLM,先批次粗篩、再逐筆深評;最後推播到 Telegram。

【技術細節(給技術主管)】
- ports.py 定義 JobSource / Scorer / Notifier 三個 Protocol;pipeline.py 是唯一的流程編排;對外 IO(104、LLM、Telegram)全靠注入,本機資料庫則由流程直接讀寫。這是「依賴反轉」:高層流程依賴抽象介面,不依賴具體實作。
- normalize.py 是唯一認識 104 JSON 結構的地方。對方改版時只要改一個檔案,而且 drift 會被偵測、記錄,不會悄悄算錯。
- 三道護欄:緊急開關(kill switch)、熔斷器(circuit breaker)、每日一次(同一天不會重複推播,所以錯過開機補跑是安全的)。

【概念:為什麼 UI 不能直接開 jobs.db】
pipeline 結束時用 os.replace 把整個 jobs.db 換掉。Windows 上檔案被其他程式開著時,替換會失敗,當天資料和熔斷器狀態就會靜靜遺失。所以 UI 一律先複製快照再讀(docs/adr/0001)。`);

  // 6. 漏斗
  s = content("漏斗:規則層先砍 70%,LLM 只看值得看的", SEC);
  {
    s.addChart(pres.charts.BAR, [{ name: "職缺數", labels: ["新職缺", "通過規則層", "LLM 深評", "推播到手機"], values: [1481, 442, 186, 70] }], {
      x: 0.6, y: 1.5, w: 7.2, h: 4.75, barDir: "bar", barGapWidthPct: 45,
      catAxisOrientation: "maxMin", catAxisLabelColor: HEX.dk1, catAxisLabelFontSize: 14, catAxisLabelFontFace: "+mn-lt",
      catAxisLineShow: false, valAxisHidden: true, valGridLine: { style: "none" }, catGridLine: { style: "none" },
      chartColors: [HEX.accent1], showValue: true, dataLabelPosition: "outEnd", dataLabelColor: HEX.dk1,
      dataLabelFontSize: 14, dataLabelFontFace: "+mn-lt", dataLabelFormatCode: "#,##0", dataLabelFontBold: true,
      showLegend: false, showTitle: true, title: "09-23 改版後 24 次執行(職缺數)", titleFontSize: 14, titleColor: HEX.dk2, titleFontFace: "+mn-lt",
    });
    const items = [
      ["FaFilter", "規則層:零成本", "產業代碼前綴 + 員工數,在 LLM 之前執行。不符合的不花 token,也不佔每天的詳細頁名額。"],
      ["FaLayerGroup", "粗篩:15 筆一批", "履歷是每次呼叫的固定成本,批次送攤下來每筆約 60 token(設計估算)。"],
      ["FaFileLines", "深評:逐筆讀完整 JD", "五個維度評分,再由程式端校正(下一頁)。"],
    ];
    let y = 1.65;
    for (const [ic, h, b] of items) {
      await iconCircle(s, ic, 8.2, y, 0.6, C.accent1, "funnel-" + ic);
      T(s, [
        { text: h, options: { bold: true, fontSize: 17, breakLine: true } },
        { text: b, options: { fontSize: 14, color: C.accent6 } },
      ], { x: 9.0, y: y - 0.05, w: 3.7, h: 1.4, paraSpaceAfter: 4 });
      y += 1.55;
    }
  }
  caption(s, "資料:jobs.db runs 表 started_at ≥ 2026-09-23 的 24 次實際執行;通過規則層 = 新職缺 1,481 − 規則層濾掉 1,039");
  N(s, `【講稿】
這是 9 月 23 日加上產業/規模規則層之後的 24 次執行。1,481 筆新職缺,規則層就先擋掉 1,039 筆(70%),剩 442 筆;LLM 深評 186 筆,最後推播 70 筆,平均一天不到 3 則。

【技術細節】
- 規則層放在 LLM 之前,是成本結構決定的:規則判斷是免費的,LLM 要錢、詳細頁要對 104 發請求(每次最多 18 個詳細頁)。
- 粗篩批次:履歷約 800 token,是每次呼叫的固定成本。15 筆一起送,攤到每筆只剩約 60 token。這是設計時的估算,不是量測值(scoring/stage1.py 開頭註解)。代價是模型偶爾漏回幾筆 —— 程式會比對、缺的補跑;補跑仍漏的保留進深評,不會悄悄丟掉。
- 粗分低於 45 不進深評(config.yaml rough_threshold)。

【概念:fail-open vs fail-closed】
如果 104 哪天不給員工數欄位,「缺資料就擋掉」(fail-closed)會讓日報靜靜變成 0 則,跟「今天沒新職缺」長得一樣。所以某欄位在整批缺超過一半時,停用該條件並大聲告警(targeting.py MISSING_RATIO_LIMIT = 0.5)。`);

  // 7. 評分
  s = content("評分:模型只做判斷,數字和取捨由程式決定", SEC);
  {
    T(s, "深評五個維度(滿分 100)", { x: 0.6, y: 1.5, w: 6, h: 0.45, fontSize: 18, bold: true });
    const dims = [["技術契合", 35, "SQL / Python / ETL / 排程 / 爬蟲 權重最高"], ["年資可行性", 25, "最關鍵:經歷不拘、1–3 年給高分"], ["領域", 15, "金融(有實習)> 半導體(只有專案)"], ["成長", 15, "有沒有 mentor、是不是真的做資料工程"], ["實務", 10, "地點、雇用型態;「待遇面議」給中間值"]];
    let y = 2.1;
    const barX = 2.4, full = 3.6;
    for (const [n, v, note] of dims) {
      T(s, n, { x: 0.6, y, w: 1.7, h: 0.4, fontSize: 15, bold: true, valign: "middle" });
      s.addShape(pres.shapes.RECTANGLE, { x: barX, y: y + 0.05, w: full * v / 35, h: 0.3, fill: { color: C.accent1 }, line: { type: "none" }, objectName: "dim-" + n });
      T(s, String(v), { x: barX + full * v / 35 + 0.1, y, w: 0.6, h: 0.4, fontSize: 15, bold: true, color: C.accent1, valign: "middle" });
      T(s, note, { x: 0.6, y: y + 0.42, w: 6.0, h: 0.35, fontSize: 12, color: C.accent6 });
      y += 0.86;
    }
    const x2 = 7.05, w2 = 5.65;
    card(s, x2, 1.5, w2, 4.8, C.background2, "rules");
    T(s, "程式端三道校正", { x: x2 + 0.3, y: 1.7, w: w2 - 0.6, h: 0.45, fontSize: 18, bold: true });
    const rules = [
      ["1", "年資 hard rule", "JD 要求 ≥5 年 → 年資維度強制壓到 8 分。專案多、正職年資少,模型最容易把我當資深工程師。"],
      ["2", "總分由程式重算", "五個維度相加,不採用模型自己加的總分。"],
      ["3", "定性交叉驗證", "分數達標但模型判 skip / maybe → 降到 59(門檻 − 1),排序往後。不合法的 verdict 跳過,不當負面訊號"],
    ];
    let ry = 2.35;
    for (const [n, h, b] of rules) {
      s.addShape(pres.shapes.OVAL, { x: x2 + 0.3, y: ry, w: 0.45, h: 0.45, fill: { color: C.accent2 }, line: { type: "none" }, objectName: "rule-num-" + n });
      T(s, n, { x: x2 + 0.3, y: ry, w: 0.45, h: 0.45, fontSize: 15, bold: true, color: C.background1, align: "center", valign: "middle" });
      T(s, [
        { text: h, options: { bold: true, fontSize: 16, breakLine: true } },
        { text: b, options: { fontSize: 13, color: C.accent6 } },
      ], { x: x2 + 0.95, y: ry - 0.03, w: w2 - 1.25, h: 1.2, paraSpaceAfter: 3 });
      ry += 1.3;
    }
  }
  N(s, `【講稿】
LLM 給五個維度的分數和一個定性判斷,但「數字」和「最後的取捨」都在程式端。原則是:模型擅長讀文字下判斷,不擅長算數、也不穩定,所以不能讓它決定結果。

【技術細節】
- 五維配分寫在 scoring/prompts.py;三道校正在 scoring/rules.py 的 apply_rules()。
- 定性交叉驗證:總分 ≥ threshold(60)但 verdict 是 skip/maybe → 降到 59。現在是 top_n 模式(下限 50),所以它影響的是排序,不是直接擋下。
- 年資:senior_years_cutoff = 5、senior_exp_fit_cap = 8(config.yaml)。年資優先讀 104 列表的 period 欄位換算(period 是「年資 + 1」,0 = 不拘),比對中文跑正規表達式可靠得多。
- verdict 用 JSON schema 的 enum 限定(strong_apply / apply / maybe / skip)。沒有限制時,便宜模型會自己發明 worth_a_shot 之類的值,實測 22 筆有 9 筆。
- 不合法的 verdict 回 None 而不是 maybe:maybe 是負面訊號會觸發降級,但「模型沒照格式寫」不代表它覺得職缺不好。曾因此讓一個 75 分的職缺被誤降到消失。
- 「待遇面議」給中間值:台灣多數職缺不揭露薪資,拿它扣分等於懲罰大部分職缺。

【概念:為什麼不信任模型的數字】
LLM 生成的是「看起來合理的文字」,加總、比大小這種確定性運算交給程式,結果才可重現、可測試。`);

  // 8. Web UI
  s = content("Web UI:把分數變成每週一的投遞決策", SEC);
  {
    const img = await sharp(SHOT).extract({ left: 373, top: 100, width: 1160, height: 780 }).png().toBuffer();
    const iw = 7.0, ih = iw * 780 / 1160;
    s.addImage({ data: "image/png;base64," + img.toString("base64"), x: 0.6, y: 1.55, w: iw, h: ih, altText: "Web UI 候選清單:三道閘門燈號、深評分數與公司職稱", objectName: "webui-shot", shadow: { type: "outer", color: "000000", opacity: 0.18, blur: 8, offset: 2, angle: 90 } });
    T(s, "候選清單:每筆職缺都標出卡在第幾道閘門", { x: 0.6, y: 1.55 + ih + 0.12, w: iw, h: 0.35, fontSize: 11, color: C.accent6, italic: true });
    const items = [
      ["FaListCheck", "三道閘門", "硬門檻 → 職缺訊號(SI、打雜等紅燈)→ 必備命中率,依序判定"],
      ["FaUserCheck", "標記「不投」必填原因", "累積的原因用來回頭檢討閘門設得對不對"],
      ["FaLock", "唯讀快照 + 獨立資料庫", "不碰每日排程的主資料庫,也不會觸發任何抓取"],
    ];
    let y = 1.65;
    for (const [ic, h, b] of items) {
      await iconCircle(s, ic, 7.95, y, 0.6, C.accent1, "ui-" + ic);
      T(s, [
        { text: h, options: { bold: true, fontSize: 17, breakLine: true } },
        { text: b, options: { fontSize: 14, color: C.accent6 } },
      ], { x: 8.75, y: y - 0.05, w: 3.95, h: 1.35, paraSpaceAfter: 4 });
      y += 1.55;
    }
  }
  N(s, `【講稿】
推播只是提醒,真正的決策在每週一。這個本機介面把每筆職缺放進三道閘門,我標「投 / 不投 / 待確認」;選「不投」一定要寫原因,這些原因之後拿來檢討閘門規則對不對。

【技術細節】
- Streamlit 只綁 127.0.0.1(cli.py 啟動參數),因為資料庫裡是真實求職資料。
- 邊界由測試守住:webui/ 不得 import 抓取、pipeline、storage、httpx(tests/test_webui_boundary.py)。UI 有按鈕能觸發抓取 = 繞過預算、節流、熔斷三道護欄。
- 「閘門」和「規則層」是兩回事:規則層(≥500 人 + 產業)決定哪些值得花 LLM;閘門(≥30 人、不限產業)是我挑職缺的條件,在 UI 端對全部職缺計算。合併的話會改變每天的 LLM 成本。
- 第 3 道「必備命中率」是獨立的 LLM 呼叫,結果存 hitrate.db,以 (職缺, 履歷雜湊) 為鍵 —— 改履歷就自動失效重算。

【概念:ADR】
Architecture Decision Record,一份短文件記錄「當時為什麼這樣決定、考慮過哪些替代方案」。這個專案的 ADR 0001 記錄標記為什麼存在獨立資料庫。`);

  // =============== 02 工程判斷 ===============
  SEC = "02 四個工程判斷";
  s = divider("02", "四個工程判斷", "這個專案花最多時間的地方不在寫程式,而在「先量測、再下結論」", SEC);
  N(s, "【轉場】接下來四個例子,共同點是:直覺的做法都是錯的,是實際量測之後才找到真正的問題。");

  // 10. 判斷 1
  s = content("判斷 1:換關鍵字沒用,真正的問題在資料本身", SEC);
  {
    const cols = [
      ["FaMagnifyingGlass", "現象", "關鍵字已全部換成「資料工程師」這一類,推播出來的仍然大多是 AI 工程師職缺。"],
      ["FaVial", "查證", "104 是全文模糊比對,JD 提到「資料工程」就會被撈回。改用「職務類別」過濾也試過 —— 雇主常掛錯類別,硬篩反而刪掉最好的職缺。"],
      ["FaRoute", "做法", "改用產業代碼前綴 + 員工數過濾,放在我方程式端:請求數完全一樣,但不必對 104 多做測試請求,還能離線測試。"],
    ];
    const w = 3.8, gap = 0.35, y = 1.55, h = 2.75;
    for (let i = 0; i < cols.length; i++) {
      const [ic, h1, b] = cols[i];
      const x = 0.6 + i * (w + gap);
      card(s, x, y, w, h, C.background2, "j1-" + i);
      await iconCircle(s, ic, x + 0.3, y + 0.3, 0.6, C.accent1, "j1-" + i);
      T(s, h1, { x: x + 1.05, y: y + 0.35, w: 2.5, h: 0.5, fontSize: 20, bold: true, valign: "middle" });
      T(s, b, { x: x + 0.3, y: y + 1.1, w: w - 0.6, h: h - 1.25, fontSize: 15, color: C.text2 });
    }
    card(s, 0.6, 4.65, 12.1, 1.3, C.text1, "j1-result");
    T(s, "8 / 10", { x: 0.95, y: 4.75, w: 2.4, h: 1.1, fontSize: 44, bold: true, color: C.accent2, valign: "middle" });
    T(s, [
      { text: "某天原本推播的 10 則裡,有 8 則是軟體/服務業小公司的 AI 職缺", options: { bold: true, breakLine: true } },
      { text: "改用產業/規模過濾後,這 8 則全部被擋下(2026-09-23 replay,run 27)", options: { color: "B7C6CE", fontSize: 14 } },
    ], { x: 3.45, y: 4.82, w: 9.0, h: 1.0, fontSize: 17, color: C.background1, valign: "middle" });
  }
  N(s, `【講稿】
直覺做法是「換關鍵字」,我換了,沒用。查證後發現 104 的關鍵字是全文比對,不是職稱比對。我也試過 104 自己的職務類別代碼,看起來是完美解法,但驗證後發現雇主亂掛:最好的玉山 Data & AI Platform 被掛成「雲端工程師」,反而 AI 研發工程師被掛成「資料工程師」。硬篩會殺掉最好的、留下不要的,剛好做反。
最後改用產業代碼:公司屬於哪個產業,雇主沒有理由亂填。

【技術細節】
- coIndustry 是階層式代碼,例如 1001006 半導體業、1004 金融投顧及保險。比對代碼前綴、不比對中文名稱 —— 104 改名稱時代碼不會變。
- config.py 拒絕在 industry_prefixes 填中文:填錯不會報錯、只會默默把職缺濾光,所以直接拒絕啟動。
- 員工數 0 代表「暫不提供」不是 0 人(鴻海、華碩都填 0),產業對就放行。這是 10-10 修正的,之前誤擋了 26 筆。

【為什麼過濾不放在 104 的搜尋參數】
搜尋請求數 = 關鍵字 × 頁數,跟有沒有帶產業參數無關。在 104 端過濾省不到任何請求,卻要為一個沒驗證過的參數多打 104。`);

  // 11. 判斷 2
  s = content("判斷 2\uFF1ALLM 分數有 ±8 雜訊,固定門檻等於擲骰子", SEC);
  {
    const ax0 = 0.9, aw = 6.6, ay = 4.0;
    const X = v => ax0 + (v - 40) / 60 * aw;
    T(s, "同一份資料、同一個 prompt 跑兩次,當天最好的職缺:", { x: 0.6, y: 1.55, w: 7.2, h: 0.4, fontSize: 16, bold: true });
    s.addShape(pres.shapes.RECTANGLE, { x: X(72), y: ay - 1.05, w: X(88) - X(72), h: 1.05, fill: { color: C.accent2, transparency: 75 }, line: { type: "none" }, objectName: "noise-band" });
    T(s, "雜訊帶 ±8", { x: X(72), y: ay - 1.0, w: X(88) - X(72), h: 0.35, fontSize: 12, bold: true, color: C.text2, align: "center" });
    s.addShape(pres.shapes.LINE, { x: ax0, y: ay, w: aw, h: 0, line: { color: HEX.accent6, width: 1.5 }, objectName: "axis" });
    for (const v of [40, 50, 60, 70, 80, 90, 100]) {
      s.addShape(pres.shapes.LINE, { x: X(v), y: ay, w: 0, h: 0.1, line: { color: HEX.accent6, width: 1 }, objectName: "tick-" + v });
      T(s, String(v), { x: X(v) - 0.3, y: ay + 0.13, w: 0.6, h: 0.3, fontSize: 12, color: C.accent6, align: "center" });
    }
    s.addShape(pres.shapes.LINE, { x: X(80), y: ay - 1.45, w: 0, h: 1.45, line: { color: HEX.accent3, width: 2.5, dashType: "dash" }, objectName: "threshold" });
    T(s, "當時的門檻 80", { x: X(80) - 0.9, y: ay - 1.85, w: 1.8, h: 0.35, fontSize: 13, bold: true, color: C.accent3, align: "center" });
    for (const [v, lab] of [[80, "第 1 次 80"], [88, "第 2 次 88"]]) {
      s.addShape(pres.shapes.OVAL, { x: X(v) - 0.13, y: ay - 0.43, w: 0.26, h: 0.26, fill: { color: C.text1 }, line: { color: HEX.lt1, width: 1.5 }, objectName: "dot-" + v });
    }
    T(s, "第 1 次 80 分", { x: X(80) - 1.95, y: ay - 0.48, w: 1.7, h: 0.35, fontSize: 12, align: "right" });
    T(s, "第 2 次 88 分", { x: X(88) + 0.22, y: ay - 0.48, w: 1.7, h: 0.35, fontSize: 12 });
    T(s, [
      { text: "門檻剛好落在雜訊帶正中央:", options: { bold: true, breakLine: true } },
      { text: "最好的職缺會隨機出現或消失,而外面看起來就只是「今天沒有好缺」—— 沒有任何跡象可以察覺。", options: { color: C.accent6 } },
    ], { x: 0.6, y: 4.85, w: 7.0, h: 1.3, fontSize: 16, paraSpaceAfter: 4 });

    const x2 = 8.0, w2 = 4.7;
    card(s, x2, 1.55, w2, 4.7, C.accent5, "j2-fix");
    await iconCircle(s, "FaScaleBalanced", x2 + 0.3, 1.8, 0.65, C.accent1, "j2");
    T(s, "改成相對排序", { x: x2 + 1.1, y: 1.85, w: 3.4, h: 0.55, fontSize: 20, bold: true, valign: "middle" });
    T(s, bullets([
      { text: "推播當天分數前 5 名,雜訊只影響排序,不再決定有沒有", options: {} },
      { text: "另設一個遠低於雜訊帶的下限,只擋明顯不合適的", options: {} },
      { text: "換成 Claude 評分後依新口徑重校:下限 50", options: {} },
    ]), { x: x2 + 0.35, y: 2.7, w: w2 - 0.7, h: 3.3, fontSize: 16 });
  }
  caption(s, "雜訊實測:Gemini 時期同一批資料 replay 兩次;Claude 評分後分數整體低 18.3 分(405 筆配對),門檻 80→60、下限 60→50");
  N(s, `【講稿】
用 LLM 分數當門檻之前,我先量了雜訊:同一份資料、同一個 prompt 跑兩次,當天最好的職缺一次 80、一次 88。而我原本的門檻正好是 80。這代表最好的職缺會隨機消失,而且我不會發現。
所以改成「當天前 5 名」:雜訊只影響排序。另外設一個遠低於雜訊帶的下限,只用來擋職缺荒日子的垃圾。

【技術細節】
- config.yaml:scoring.mode = top_n、top_n = 5、top_n_floor = 50。threshold(60)仍保留,給卡片的分數燈號用。
- 10-06 起改用 Claude 評分。405 筆配對後,Claude 平均比 Gemini 低 18.3 分,所以門檻依 Claude 口徑重校(80→60、下限 60→50)。這提醒我:分數是「某個模型的口徑」,換模型就要重校,不能沿用舊的分數區間推論。
- Claude 自身的重跑差異,10 筆兩次平均差 4.5 分;但量法跟 Gemini 的 ±8 不同,我不把兩者寫成數字對比。

【概念:measurement noise】
任何量測都有雜訊。決策門檻如果落在雜訊帶內,決策結果就是隨機的。正確做法是先量雜訊大小,再決定用絕對門檻、相對排序,或拉開門檻與雜訊的距離。`);

  // 12. 判斷 3
  s = content("判斷 3:平均數會稀釋致命缺口,核心條件一票否決", SEC);
  {
    const y = 1.6;
    card(s, 0.6, y, 3.6, 2.6, C.background2, "j3-llm");
    T(s, "88%", { x: 0.6, y: y + 0.3, w: 3.6, h: 1.1, fontSize: 60, bold: true, color: C.accent4, align: "center", valign: "middle" });
    T(s, "LLM 算出的平均命中率", { x: 0.6, y: y + 1.55, w: 3.6, h: 0.4, fontSize: 15, align: "center" });
    T(s, "某半導體/電子大廠資料職缺", { x: 0.6, y: y + 1.95, w: 3.6, h: 0.4, fontSize: 12, color: C.accent6, align: "center" });
    T(s, "vs", { x: 4.2, y: y + 0.75, w: 0.9, h: 0.6, fontSize: 22, bold: true, color: C.accent6, align: "center", valign: "middle" });
    card(s, 5.1, y, 3.6, 2.6, C.text1, "j3-me");
    T(s, "61%", { x: 5.1, y: y + 0.3, w: 3.6, h: 1.1, fontSize: 60, bold: true, color: C.accent2, align: "center", valign: "middle" });
    T(s, "我自己的人工判斷", { x: 5.1, y: y + 1.55, w: 3.6, h: 0.4, fontSize: 15, color: C.background1, align: "center" });
    T(s, "差距在核心條件:AWS 資料服務", { x: 5.25, y: y + 1.95, w: 3.3, h: 0.45, fontSize: 12, color: "B7C6CE", align: "center" });

    T(s, "改成:LLM 只判定,百分比由程式算", { x: 9.1, y: y, w: 3.6, h: 0.5, fontSize: 17, bold: true });
    T(s, bullets([
      "LLM 逐條判定「符合 / 部分 / 不符」,附履歷證據",
      "程式算:符合 1、部分 0.5、不符 0",
      "每筆最多標 2 條核心條件,任一不是「符合」→ 直接不通過",
    ]), { x: 9.1, y: y + 0.6, w: 3.6, h: 3.0, fontSize: 15 });

    card(s, 0.6, 4.6, 8.1, 1.6, C.accent5, "j3-lesson");
    await iconCircle(s, "FaLightbulb", 0.9, 4.95, 0.75, C.accent1, "j3");
    T(s, [
      { text: "判準由我定義,模型負責執行", options: { bold: true, breakLine: true } },
      { text: "我拿自己的人工判斷校正模型:差距大的地方,就是規則該改的地方。", options: { color: C.text2, fontSize: 15 } },
    ], { x: 1.9, y: 4.8, w: 6.6, h: 1.25, fontSize: 17, valign: "middle" });
  }
  N(s, `【講稿】
「履歷命中幾成必備條件」這件事,一開始讓 LLM 給平均。有一筆算出 88%,我自己判斷只有約 61%,差距就在一條核心條件:它要求的 AWS 資料服務,我沒有實作經驗(我用過的是 SageMaker、Bedrock 這類,不是資料服務)。平均數把這個缺口稀釋掉了。
所以改成:LLM 只逐條判定並附證據,百分比由程式算;每筆最多標 2 條核心條件,只要有一條不是「完全符合」就不通過。

【技術細節】
- 實作在 hitrate/(獨立指令 jobfinder hitrate),結果存 hitrate.db。
- 部分符合給 0.5;加分條件不進分母。
- 改 prompt 會讓數字位移(同一筆 83% → 75%),所以改完要重跑驗證、考慮清掉舊結果。
- 那筆實例是華碩 8vyka(README 與 CLAUDE.md 有記錄)。簡報上刻意不寫公司名。

【概念:aggregation hides outliers】
平均、加總這類聚合會抹平極端值。如果某個條件是「缺了就不行」(硬性條件),就不能跟其他條件一起平均,要單獨當成否決條件處理。`);

  // 13. 判斷 4
  s = content("判斷 4:雲端被擋的原因是 IP 信譽,不是抓取手法", SEC);
  {
    const rows = [
      [{ text: "連線來源", options: { bold: true, color: HEX.lt1, fill: { color: HEX.dk2 } } }, { text: "做法", options: { bold: true, color: HEX.lt1, fill: { color: HEX.dk2 } } }, { text: "結果", options: { bold: true, color: HEX.lt1, fill: { color: HEX.dk2 } } }],
      ["家用台灣住宅 IP", "純 HTTP,帶 Referer", { text: "✓ 200,連續 19 個請求全過", options: { color: HEX.accent1, bold: true } }],
      ["雲端資料中心 IP(Modal)", "純 HTTP,帶 Referer", { text: "✗ 403,Cloudflare 驗證頁", options: { color: HEX.accent3, bold: true } }],
      ["雲端資料中心 IP(Modal)", "真實瀏覽器", { text: "✗ 更糟:升級成要人工操作的驗證", options: { color: HEX.accent3, bold: true } }],
    ];
    s.addTable(zhRows(rows), { x: 0.6, y: 1.6, w: 7.6, colW: [2.6, 2.2, 2.8], fontSize: 14, color: HEX.dk1, lang: "zh-TW", fontFace: FONT, border: { type: "solid", pt: 0.75, color: "D5DEE2" }, rowH: 0.8, valign: "middle", margin: [0, 0.12, 0, 0.12], fill: { color: HEX.lt1 } });
    T(s, [
      { text: "結論:", options: { bold: true } },
      { text: "擋下來的是 IP 信譽 —— 換做法不會過,換成瀏覽器反而更糟。", options: {} },
    ], { x: 0.6, y: 5.0, w: 7.6, h: 0.8, fontSize: 17 });

    const x2 = 8.6, w2 = 4.1;
    card(s, x2, 1.6, w2, 4.6, C.background2, "j4-decision");
    await iconCircle(s, "FaHouse", x2 + 0.3, 1.85, 0.65, C.accent1, "j4");
    T(s, "我的決定", { x: x2 + 1.1, y: 1.9, w: 2.8, h: 0.55, fontSize: 20, bold: true, valign: "middle" });
    T(s, bullets([
      "遇到驗證不去繞過(瀏覽器測試只為診斷原因)",
      "改在本機排程執行",
      "雲端版程式碼完整保留,等有合規的住宅連線再切回去",
    ]), { x: x2 + 0.35, y: 2.75, w: w2 - 0.7, h: 3.2, fontSize: 16 });
  }
  caption(s, "實測日期 2026-08-22;診斷指令 modal run modal_app.py::diagnose 從雲端發一個請求,印出 egress IP、cf-mitigated、server 與 body");
  N(s, `【講稿】
原本計畫部署到雲端。實測:家裡的 IP 完全暢通;雲端資料中心的 IP 被 Cloudflare 擋下要求驗證;改用真實瀏覽器反而被升級成要人工操作的驗證。所以原因是 IP 信譽,不是技術手法。瀏覽器那次測試是為了診斷原因;確認是 IP 信譽後,我沒有去繞過它,而是改成本機排程。

【技術細節:三種回應的分辨(CLAUDE.md)】
- 403 + body 空 → 104 origin 的 Referer 白名單:我方設定錯。
- 403 + cf-mitigated: challenge → Cloudflare 依 IP 信譽出的挑戰。
- 429 / 503 → 速率限制。
只能靠看回應本身分辨,所以寫了一個只發「一個請求」的診斷指令,把完整回應印出來。

【這個專案錯判過兩次,方向還相反】
1. 最初:「API 有 Cloudflare,必須用真實瀏覽器」—— 測試管道設不了 Referer 又從資料中心出去,兩個因素混在一起。
2. 更正:「104 沒有 Cloudflare,只有 Referer 白名單」—— 只在家用 IP 驗證過就推論成通則。
正確理解:兩件事同時為真。信譽夠的 IP 會遇到 Referer 白名單;信譽不夠的 IP 在那之前就被 Cloudflare 攔下。

【概念:控制變因】
一次只改一個變因。第一次錯判就是兩個變因(Referer、IP 來源)同時不同,卻歸因給其中一個。`);

  // =============== 03 可靠性 ===============
  SEC = "03 可靠性與合規";
  s = divider("03", "可靠性與合規", "寧可少抓幾筆職缺,也不給資料來源添麻煩", SEC);
  N(s, "【轉場】這一段是我最在意的部分:一個會對外發請求的自動化系統,先要確保它不會出事。");

  // 15. 保護順位
  s = content("衝突時的犧牲順序:帳號 > 家中 IP > 雲端 IP > 功能", SEC);
  {
    const lv = [["1", "104 求職帳號", "被停權 = 失去真實求職身分,無可替代", C.accent3], ["2", "家中住宅 IP", "被封就整個系統停擺", C.accent2], ["3", "雲端 IP", "被擋可以換,不牽連帳號與家中 IP", C.accent4], ["4", "功能完整度", "少抓幾筆職缺,明天還有", C.accent5]];
    let y = 1.6;
    for (let i = 0; i < lv.length; i++) {
      const [n, h, b, col] = lv[i];
      const w = 5.4 - i * 0.45;
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 0.6, y, w, h: 1.0, rectRadius: 0.08, fill: { color: col }, line: { type: "none" }, objectName: "prio-" + n });
      const dark = i >= 2;
      T(s, n, { x: 0.8, y, w: 0.5, h: 1.0, fontSize: 28, bold: true, color: dark ? C.text1 : C.background1, valign: "middle" });
      T(s, [{ text: h, options: { bold: true, fontSize: 17, breakLine: true } }, { text: b, options: { fontSize: 12 } }], { x: 1.35, y: y + 0.1, w: w - 0.9, h: 0.8, color: dark ? C.text1 : C.background1, valign: "middle" });
      y += 1.15;
    }
    const rules = [
      ["FaBan", "不登入", "目標資料對訪客完全公開;設定檔出現 password 欄位就拒絕啟動"],
      ["FaShieldHalved", "單一守門員", "每個請求都要先過 RequestBudget.acquire():黑名單、預算、節流、計數"],
      ["FaGaugeHigh", "上限只能往保守調", "頁數、詳細頁、總請求數有寫死的硬上限,超過就拒絕啟動"],
      ["FaPlugCircleXmark", "遇封鎖立刻停、不重試", "429 / 503 / 驗證頁 → 中止並熔斷"],
      ["FaStopwatch", "全程序列、不併發", "請求間隔至少 3 秒;單次執行最多 40 個請求"],
    ];
    let ry = 1.55;
    for (const [ic, h, b] of rules) {
      await iconCircle(s, ic, 6.6, ry, 0.55, C.accent1, "g-" + ic);
      T(s, [{ text: h, options: { bold: true, fontSize: 16, breakLine: true } }, { text: b, options: { fontSize: 13, color: C.accent6 } }], { x: 7.35, y: ry - 0.05, w: 5.35, h: 0.95, paraSpaceAfter: 2 });
      ry += 1.0;
    }
  }
  N(s, `【講稿】
這個系統的規則有一個明確的優先順序:104 帳號 > 家裡的 IP > 雲端 IP > 功能完整度。衝突時一律犧牲後面的。寧可少抓職缺,也不冒險。右邊是落實這個順序的五條護欄。

【技術細節(都有程式強制,不靠自律)】
- 不登入:config.py 的禁用欄位清單含 password;設定了就拒絕啟動。
- RequestBudget.acquire()(scrape/budget.py):順序是「黑名單 → 預算 → 強制節流 → 計數」,而且先計數再放行 —— 就算請求接著拋例外,封包已經送出,也必須算進預算。
- 硬上限(config.py 開頭常數):每關鍵字 ≤3 頁、詳細頁 ≤25、總導覽 ≤40、間隔下限 3 秒。目前設定比上限更保守(2 頁、18 個詳細頁)。
- 路徑黑名單(scrape/urls.py):應徵、收藏、追蹤公司這類「登入使用者才會做的動作」一律擋;robots.txt 禁用的查詢參數也擋。
- user_agent 必須誠實標示,含 Mozilla 字樣(偽裝成瀏覽器)會拒絕啟動。

【概念:為什麼「不重試」】
被暫時挑戰時重試,是把「暫時被挑戰」變成「永久封 IP」的頭號原因。對外部系統,正確的反應是停下來、等、讓人來判斷。`);

  // 16. 兩種 403
  s = content("同樣是 403,處置剛好相反", SEC);
  {
    const cols = [
      ["FaKey", "403 + 回應是空的", "我方設定錯了(Referer 掉了)", ["丟 SourceMisconfigured", "告警,請人檢查設定", "不熔斷 —— 冷卻 24 小時對設定錯誤毫無幫助"], C.accent2],
      ["FaTriangleExclamation", "403 + 驗證頁 / 429 / 503", "對方真的上防護了", ["丟 ChallengeBlocked,立刻中止", "熔斷:第 1 次冷卻 24h、第 2 次 72h", "第 3 次起要人工解除(reset-circuit)"], C.accent3],
    ];
    const w = 5.9, y = 1.6, h = 3.35;
    for (let i = 0; i < 2; i++) {
      const [ic, h1, sub, pts, col] = cols[i];
      const x = 0.6 + i * (w + 0.3);
      card(s, x, y, w, h, C.background2, "403-" + i);
      await iconCircle(s, ic, x + 0.35, y + 0.35, 0.8, col, "403-" + i);
      T(s, [{ text: h1, options: { bold: true, fontSize: 21, breakLine: true } }, { text: sub, options: { fontSize: 15, color: C.accent6 } }], { x: x + 1.35, y: y + 0.32, w: w - 1.6, h: 0.95, valign: "middle" });
      T(s, bullets(pts), { x: x + 0.4, y: y + 1.55, w: w - 0.8, h: h - 1.7, fontSize: 16 });
    }
    card(s, 0.6, 5.2, 12.1, 1.05, C.text1, "403-how");
    T(s, [{ text: "怎麼分辨:", options: { bold: true, color: C.accent2 } }, { text: "只能看回應本身。診斷指令只發一個請求,把狀態碼、cf-mitigated 標頭和 body 全部印出來。", options: {} }], { x: 0.95, y: 5.2, w: 11.5, h: 1.05, fontSize: 16, color: C.background1, valign: "middle" });
  }
  caption(s, "瀏覽器路徑讀不到回應內容時,一律當成被擋(fail-closed);只有純 HTTP 路徑能做這個分流(scrape/blocking.py)");
  N(s, `【講稿】
同樣是 HTTP 403,意義可能完全相反。回應是空的,代表我方的 Referer 設定掉了,這時候冷卻一天毫無幫助,應該告警請人修;出現驗證頁才是真的被擋,要立刻停並熔斷。如果不分,不是誤熔斷浪費一天,就是被擋了還繼續打。

【技術細節】
- 熔斷冷卻:第 1 次 24 小時、第 2 次 72 小時、第 3 次起無限期,要人工 jobfinder reset-circuit(scrape/blocking.py)。
- 冷卻時間有下限 24 小時(config.py HARD_MIN_COOLDOWN_HOURS),只能往長調。
- 瀏覽器模式讀 body 可能失敗,所以 403 在那條路徑上一律 fail-closed(當成被擋)—— 不確定的時候選安全的那邊。
- 網路斷線(DNS 還沒好)另外處理:NetworkUnavailable,不熔斷、記 failed、不佔當天額度(10-13 修正)。

【概念:circuit breaker】
熔斷器源自電路保險絲:偵測到異常就「跳開」,一段時間內拒絕所有新請求,避免持續對已經出問題的下游施壓。`);

  // 17. 品質
  s = content("品質靠測試和 hook 守住,不靠記憶", SEC);
  {
    const stats = [["674", "個自動化測試", "全部離線,跑錄下來的真實 104 回應"], ["11,286", "行 Python 程式", "另有 6,428 行測試(含註解)"], ["2 層", "git hook 祕密掃描", "commit 前、push 前各掃一次"]];
    const w = 3.83, y = 1.6, h = 2.3;
    stats.forEach(([big, lab, sub], i) => {
      const x = 0.6 + i * (w + 0.3);
      card(s, x, y, w, h, C.background2, "q-" + i);
      T(s, big, { x: x + 0.3, y: y + 0.25, w: w - 0.6, h: 0.9, fontSize: 44, bold: true, color: C.accent1, valign: "middle" });
      T(s, lab, { x: x + 0.3, y: y + 1.2, w: w - 0.6, h: 0.4, fontSize: 16, bold: true });
      T(s, sub, { x: x + 0.3, y: y + 1.6, w: w - 0.6, h: 0.55, fontSize: 13, color: C.accent6 });
    });
    card(s, 0.6, 4.2, 12.1, 2.0, C.text1, "q-story");
    await iconCircle(s, "FaVial", 0.95, 4.55, 0.75, C.accent2, "q-story");
    T(s, [
      { text: "規則來自真實事故,例如:", options: { bold: true, fontSize: 18, breakLine: true } },
      { text: "有個腳本沒寫 argparse,測試跑 --help 時整支被執行,真的送出了 6 則 Telegram 訊息。現在 tests/test_scripts.py 會檢查每支腳本的 --help 印得出 usage。", options: { fontSize: 15, color: "B7C6CE" } },
    ], { x: 2.0, y: 4.35, w: 10.4, h: 1.7, color: C.background1, valign: "middle", paraSpaceAfter: 6 });
  }
  caption(s, "測試數:pytest --collect-only(2026-10-25);行數:src/ 與 tests/ 下 .py 檔的 wc -l");
  N(s, `【講稿】
護欄沒有測試就等於沒有護欄。674 個測試全部離線,用的是 8 月錄下來的真實 104 回應,所以開發時完全不用連 104。另外 commit 和 push 前各有一層祕密掃描,避免金鑰外流。

【技術細節】
- fixture:真實 API 回應錄成 JSON 檔;開發用 --from-fixtures 或 --replay,完全不碰 104。
- 護欄的測試在 tests/test_guardrails.py、tests/test_http_source.py。
- pre-commit(scripts/check_secrets.py --staged):金鑰格式、.env 裡的實際值、.env / *.db 路徑。
- pre-push(--history):掃整個 commit 歷史 —— 因為 commit 階段可以用 --no-verify 跳過,但推上遠端就收不回來了。

【概念:regression test】
每修一個 bug,就寫一個會在 bug 重現時失敗的測試。這樣同一個錯不會第二次發生,而且規則的「理由」會跟著測試留在 repo 裡。`);

  // =============== 04 AI 協作 ===============
  SEC = "04 我怎麼跟 AI 協作";
  s = divider("04", "我怎麼跟 AI 協作", "程式大部分是 Claude Code 寫的;我的工作是讓它寫對", SEC);
  N(s, "【轉場】最後談工作方式。這個專案的程式碼大部分由 AI 撰寫,所以我想說明我在其中負責什麼。");

  // 19. 分工
  s = content("AI 寫程式,我負責定義什麼算對", SEC);
  {
    card(s, 0.6, 1.6, 3.4, 4.0, C.text1, "ai-stat");
    T(s, "> 90%", { x: 0.6, y: 2.1, w: 3.4, h: 1.0, fontSize: 40, bold: true, color: C.accent2, align: "center", valign: "middle" });
    T(s, "的 commit 標註\nClaude Code 共同作者", { x: 0.75, y: 3.2, w: 3.1, h: 0.9, fontSize: 15, color: C.background1, align: "center" });
    T(s, "git log(100 多個 commit,2026-10-25)", { x: 0.75, y: 5.05, w: 3.1, h: 0.35, fontSize: 11, color: "B7C6CE", align: "center" });
    const cols = [
      ["FaFileLines", "規則寫進 repo", ["SPEC.md:驗收標準", "CLAUDE.md:禁止事項與 API 實測", "ADR:決策與理由"]],
      ["FaGears", "重要規則用程式再擋一次", ["寫成測試", "git hook 掃祕密", "Claude Code hook 擋下危險指令"]],
      ["FaUserShield", "我不交給 AI 的事", ["風險的優先順序", "評分的判準與核心條件", "金鑰與憑證"]],
    ];
    const w = 2.75, gap = 0.2;
    for (let i = 0; i < cols.length; i++) {
      const [ic, h, pts] = cols[i];
      const x = 4.3 + i * (w + gap);
      card(s, x, 1.6, w, 4.0, C.background2, "ai-" + i);
      await iconCircle(s, ic, x + 0.25, 1.85, 0.6, i === 2 ? C.accent2 : C.accent1, "ai-" + i);
      T(s, h, { x: x + 0.25, y: 2.6, w: w - 0.45, h: 0.75, fontSize: 17, bold: true });
      T(s, bullets(pts), { x: x + 0.25, y: 3.4, w: w - 0.45, h: 2.1, fontSize: 14 });
    }
  }
  N(s, `【講稿】
100 多個 commit 裡,超過九成標了 Claude Code 共同作者,這點我不避諱。AI 每開一個新 session 對專案的記憶都是零,所以「什麼算對」必須寫進 repo:規格、禁止事項、踩過的坑。寫在文件裡的規則 AI 可能漏看,所以重要的會再用程式擋一次。而有三件事我不交給 AI:風險順序、評分判準、金鑰。

【技術細節】
- .claude/hooks/guard.py:AI 每次要執行指令或讀檔前先過這支腳本,三類操作直接擋下並回傳理由:modal deploy、沒帶 --limit 的真實連線、讀 .env。
- 金鑰只存在本機 .env(在 .gitignore),repo 裡只有列變數名稱的 .env.example。

【可能被問】
Q:那你自己會寫程式嗎?
A:(依你的實際狀況回答)建議重點放在:我讀得懂、審得了每一段改動,也能指出它錯在哪 —— 例如 period 欄位是「年資 + 1」這個錯,是我在 UI 上發現卡片寫「2年以上」、104 頁面寫「1年以上」對不上,再用 369 筆詳細頁驗證出來的。`);

  // 20. 工作方式
  s = content("修正過的做法:從寫細計畫,改成小步驗證", SEC);
  {
    card(s, 0.6, 1.6, 5.6, 4.0, C.background2, "old");
    T(s, "一開始", { x: 0.9, y: 1.8, w: 5, h: 0.5, fontSize: 20, bold: true });
    T(s, "AI 追問需求 → 寫細規格與計畫 → 照計畫實作", { x: 0.9, y: 2.35, w: 5.0, h: 0.7, fontSize: 15, color: C.accent6 });
    T(s, bullets([
      "慢:時間花在把計畫寫細,很晚才看到真實結果",
      "計畫越細,沒驗證過的推測越多,寫進去就被當成事實",
      { text: "例:「104 有沒有 Cloudflare」錯判兩次;年資欄位多算 1 年,錯了一個月", options: { color: HEX.accent3 } },
    ]), { x: 0.9, y: 3.15, w: 5.0, h: 2.3, fontSize: 15 });

    s.addShape(pres.shapes.RIGHT_ARROW, { x: 6.35, y: 3.3, w: 0.6, h: 0.55, fill: { color: C.accent2 }, line: { type: "none" }, objectName: "arrow2" });

    card(s, 7.1, 1.6, 5.6, 4.0, C.accent5, "new");
    T(s, "現在", { x: 7.4, y: 1.8, w: 5, h: 0.5, fontSize: 20, bold: true });
    T(s, "每一輪只做一小段能跑的 → 看真實結果 → 再決定下一步", { x: 7.4, y: 2.35, w: 5.0, h: 0.7, fontSize: 15, color: C.text2 });
    T(s, bullets([
      "計畫細到什麼程度,取決於已經確認了多少",
      "下判斷前先看真實回應,不讓 AI 用推論的",
      "拿掉的是步驟規定,驗收標準留著",
    ]), { x: 7.4, y: 3.15, w: 5.0, h: 2.3, fontSize: 16 });
  }
  N(s, `【講稿】
我一開始的流程很「重」:先讓 AI 追問我需求、寫完規格和實作計畫,才開始寫程式。用了一陣子發現兩個問題:一是慢,二是計畫越細,裡面沒驗證過的推測越多,而推測一旦寫進計畫和測試就被當成事實。
後來借用 Scrum 的精神,但不照搬角色和會議:每一輪只做一小段能跑的,看到真實結果再決定下一步。

【兩個真實例子】
1. Cloudflare 錯判兩次(見判斷 4)。第一次讓整套瀏覽器抓取程式被寫了出來,第二次讓我以為可以直接上雲端。
2. 104 列表的 period 欄位被當成「實際年數」寫進文件,實際上是「年資 + 1」。錯了一個月,日報上每筆職缺都多算一年。是我在 UI 上發現跟 104 頁面對不上,再拿 369 筆詳細頁的 workExp 驗證才確定。

【三個原則】
- 驗證過的事實可以寫死,還沒實測的先保持彈性。
- 遇到「是不是被擋了」這類問題,先發一個請求把完整回應印出來看。
- 不要矯枉過正:CLAUDE.md 的絕對禁止事項就是驗收標準,是最不能退讓的部分。`);

  // 21. 結語
  SEC = "結語";
  pres.addSection({ title: SEC });
  s = pres.addSlide({ masterName: "TITLE_DARK", sectionTitle: SEC });
  s.addText(zh("這個專案代表我能帶進團隊的東西"), { placeholder: "title" });
  {
    const items = [
      ["FaDatabase", "資料工程", "把未公開文件的 API 轉成穩定的資料模型;去重、可離線重跑"],
      ["FaShieldHalved", "可靠性", "護欄、熔斷、缺資料時降級並告警,不悄悄出錯"],
      ["FaRobot", "LLM 應用", "批次控成本、程式端校正、換模型就重校"],
      ["FaChartColumn", "工作方式", "先量測再下結論;用規則和測試讓 AI 寫對"],
    ];
    const w = 2.8, gap = 0.3, y = 3.55;
    for (let i = 0; i < items.length; i++) {
      const [ic, h, b] = items[i];
      const x = 0.8 + i * (w + gap);
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h: 2.45, rectRadius: 0.08, fill: { color: C.text2 }, line: { type: "none" }, objectName: "close-" + i });
      await iconCircle(s, ic, x + 0.25, y + 0.25, 0.6, i === 3 ? C.accent2 : C.accent1, "close-" + i);
      T(s, h, { x: x + 0.25, y: y + 0.95, w: w - 0.5, h: 0.45, fontSize: 18, bold: true, color: C.background1 });
      T(s, b, { x: x + 0.25, y: y + 1.42, w: w - 0.5, h: 0.95, fontSize: 13, color: "C9D6DC" });
    }
  }
  T(s, "林芳義 Fang-Yi Lin|az045317@gmail.com|github.com/yiyiscode", { x: 0.8, y: 6.4, w: 11.7, h: 0.4, fontSize: 14, color: "B7C6CE" });
  N(s, `【講稿】
最後總結我能帶進團隊的四件事:資料工程的基本功、對可靠性的重視、實際落地過的 LLM 應用,以及「先量測再下結論」的工作方式。謝謝,歡迎提問。

【依對象調整】
- HR:強調「真實每天在用」「自己發現問題、自己量測、自己修正」「對外部系統負責的態度」。
- 技術主管:可以直接跳到附錄,聊 104 API 的資料契約、RequestBudget 的設計、或模組邊界。`);

  // =============== 附錄 ===============
  SEC = "附錄:技術細節";
  s = divider("附錄", "技術細節", "被追問時用:資料契約、守門員、模組邊界、模型遷移、數字口徑", SEC);
  N(s, "附錄不一定要講,給技術主管追問時翻出來,也給自己複習用。");

  // A1 API 欄位
  s = content("104 API 實測欄位:別憑印象寫", SEC);
  {
    const hd = o => ({ text: o, options: { bold: true, color: HEX.lt1, fill: { color: HEX.dk2 } } });
    const rows = [
      [hd("欄位"), hd("直覺會以為"), hd("實際上"), hd("不處理的後果")],
      ["jobNo vs 連結尾段", "同一個 ID", "兩個不同 ID;詳細頁 API 只吃連結尾段", "拿 jobNo 打詳細頁回 404「職務不存在」"],
      ["s10", "看不懂的欄位", "薪資類型:10 面議、50 月薪、60 年薪", "把年薪 567,000 寫成「月薪 56 萬」"],
      ["period", "實際年資", "年資 + 1(0 = 不拘、2 = 1 年以上)", "每筆職缺多算 1 年(錯了一個月)"],
      ["employeeCount = 0", "0 人的小公司", "「暫不提供」;鴻海、華碩都填 0", "大公司被規則層誤擋(26 筆)"],
      ["coIndustry", "用中文名稱比對", "階層式代碼,比對前綴(1001006 半導體)", "104 改名稱時過濾默默失效"],
    ];
    s.addTable(zhRows(rows), { x: 0.6, y: 1.55, w: 12.1, colW: [2.3, 2.1, 4.0, 3.7], fontSize: 13, color: HEX.dk1, lang: "zh-TW", fontFace: FONT, border: { type: "solid", pt: 0.75, color: "D5DEE2" }, rowH: 0.62, valign: "middle", margin: [0, 0.1, 0, 0.1], fill: { color: HEX.lt1 } });
    caption(s, "fixture 為 2026-08-21 錄下的真實回應;normalize.py 是唯一認識 104 JSON 結構的模組", 5.95);
  }
  N(s, `【講稿】
這頁是「資料契約」:對方沒有公開文件的 API,每個欄位的意義都要自己驗證。這幾個都是踩過才知道的。

【技術細節】
- 領域模型把 jobNo(去重主鍵)和 detail_id(打 API 用)分成兩個欄位。
- period 用 normalize.period_to_years() 換算。驗證方法:369 筆詳細頁的 workExp 文字逐筆對照:0→不拘 266 筆、2→1 年以上 59 筆、3→2 年以上 41 筆。
- 搜尋回應的 data 本身就是陣列(沒有 data.list);description 不是完整 JD(中位數 123 字),所以詳細頁還是得抓。
- 打錯 ID 時 API 回 {"error": {"code": 11201, ...}},記成 drift 而不是讓程式 crash。

【概念:schema drift】
外部資料源的格式會在不通知你的情況下改變。對策是把「認識外部格式」集中在一個模組,並在格式不符時記錄、告警,而不是默默產生錯誤的資料。`);

  // A2 守門員
  s = content("RequestBudget.acquire():所有請求的唯一守門員", SEC);
  {
    const steps = [["FaBan", "黑名單", "應徵/收藏路徑\nrobots.txt 禁用參數"], ["FaCoins", "預算檢查", "總導覽、詳細頁\n每關鍵字頁數"], ["FaStopwatch", "強制節流", "隨機等待\n下限 3 秒"], ["FaListCheck", "先計數", "再放行\n例外也算一次"], ["FaArrowRight", "放行", "呼叫端才\n實際發請求"]];
    const d = 0.9, x0 = 0.9, gap = 2.6, y = 1.7;
    s.addShape(pres.shapes.LINE, { x: x0 + d / 2, y: y + d / 2, w: gap * 4, h: 0, line: { color: HEX.accent4, width: 2, endArrowType: "triangle" }, objectName: "acq-line" });
    for (let i = 0; i < steps.length; i++) {
      const [ic, h, b] = steps[i];
      const cx = x0 + i * gap;
      await iconCircle(s, ic, cx, y, d, i === 4 ? C.accent2 : C.accent1, "acq-" + i);
      T(s, h, { x: cx + d / 2 - 1.1, y: y + d + 0.15, w: 2.2, h: 0.4, fontSize: 16, bold: true, align: "center" });
      T(s, b, { x: cx + d / 2 - 1.1, y: y + d + 0.55, w: 2.2, h: 0.7, fontSize: 13, color: C.accent6, align: "center" });
    }
    const hd = o => ({ text: o, options: { bold: true, color: HEX.lt1, fill: { color: HEX.dk2 } } });
    const rows = [[hd("項目"), hd("硬上限(程式寫死)"), hd("目前設定")], ["每個關鍵字的頁數", "3", "2"], ["每次執行的詳細頁", "25", "18"], ["每次執行的總請求數", "40", "40"], ["請求間隔下限", "3 秒", "3–9 秒(依類型)"]];
    s.addTable(zhRows(rows), { x: 0.6, y: 4.3, w: 8.0, colW: [3.2, 2.4, 2.4], fontSize: 14, color: HEX.dk1, lang: "zh-TW", fontFace: FONT, border: { type: "solid", pt: 0.75, color: "D5DEE2" }, rowH: 0.4, valign: "middle", margin: [0, 0.1, 0, 0.1], fill: { color: HEX.lt1 } });
    T(s, [{ text: "實測:", options: { bold: true } }, { text: "每次執行平均 28.3 個請求,最高 40。上限確實有擋住。", options: {} }], { x: 9.0, y: 4.35, w: 3.7, h: 1.6, fontSize: 15 });
  }
  caption(s, "src/jobfinder/scrape/budget.py:acquire();src/jobfinder/config.py 開頭的 HARD_* 常數;設定值超過硬上限就拒絕啟動");
  N(s, `【講稿】
HTTP 模式和瀏覽器模式共用同一個守門員,所以多一條抓取路徑也不會繞過護欄。

【技術細節】
- 順序:assert_path_allowed(url) → 預算檢查(不夠丟 BudgetExhausted,呼叫端停止抓取但正常收尾)→ _throttle(delay) → 計數。
- 先計數再放行:即使接下來的請求拋例外,封包已經送出,必須算進預算;少算會讓實際請求量超出上限。
- 節流分三種:分頁之間 3–5 秒、關鍵字之間 5–9 秒、詳細頁之間 4–8 秒(config.yaml)。
- 每天剩下沒用完的詳細頁名額,會依序給「投遞清單」和「補深評佇列」—— 不多發任何請求。

【概念:單一入口(choke point)】
把所有對外請求收斂到一個函式,護欄只要寫一次、測一次;新增功能時只要確認它有呼叫這個入口。`);

  // A3 模組邊界
  s = content("模組邊界:對外 IO 全靠注入,外部格式只有一個模組認識", SEC);
  {
    const boxes = [
      [0.6, 1.7, "pipeline.py", "唯一的流程編排\n對外 IO 全靠注入", C.text1, C.background1],
      [4.85, 1.7, "ports.py", "JobSource / Scorer / Notifier\n三個 Protocol", C.accent1, C.background1],
      [9.1, 1.7, "normalize.py", "唯一認識 104 JSON 的地方\nraw JSON → 領域模型", C.accent2, C.text1],
    ];
    for (const [x, y, h, b, fill, tc] of boxes) {
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w: 3.6, h: 1.55, rectRadius: 0.08, fill: { color: fill }, line: { type: "none" }, objectName: "mod-" + h });
      T(s, [{ text: h, options: { bold: true, fontSize: 18, breakLine: true } }, { text: b, options: { fontSize: 13 } }], { x: x + 0.25, y: y + 0.15, w: 3.1, h: 1.25, color: tc, valign: "middle", paraSpaceAfter: 4 });
    }
    s.addShape(pres.shapes.RIGHT_ARROW, { x: 4.3, y: 2.25, w: 0.45, h: 0.45, fill: { color: C.accent4 }, line: { type: "none" }, objectName: "mod-arrow1" });
    s.addShape(pres.shapes.LEFT_ARROW, { x: 8.55, y: 2.25, w: 0.45, h: 0.45, fill: { color: C.accent4 }, line: { type: "none" }, objectName: "mod-arrow2" });
    const impl = [["JobSource 的實作", "HttpJobSource(預設)\nPlaywrightJobSource(瀏覽器備援)\nFixtureSource(離線開發)"], ["Scorer 的實作", "Claude 訂閱(優先)\n失敗黏著退回 OpenRouter\nFake(測試用)"], ["Notifier 的實作", "Telegram(HTML parse mode,\n400 時降級純文字重送)"]];
    impl.forEach(([h, b], i) => {
      const x = 0.6 + i * 4.25;
      card(s, x, 3.65, 3.85, 1.95, C.background2, "impl-" + i);
      T(s, [{ text: h, options: { bold: true, fontSize: 16, breakLine: true } }, { text: b, options: { fontSize: 14, color: C.accent6 } }], { x: x + 0.25, y: 3.85, w: 3.35, h: 1.6, paraSpaceAfter: 6 });
    });
  }
  N(s, `【講稿】
這個切法讓「抓取方式整個換掉」變成換一個實作的事:從瀏覽器改成純 HTTP 時,pipeline、storage、scoring、notify 一行都沒改。

【技術細節】
- Protocol 是 Python 的結構化型別(structural typing):實作不必繼承,只要方法簽名對得上就符合介面。測試時注入 Fake,完全離線。
- 離線開發的兩個入口:--from-fixtures(錄好的真實回應)、--replay <RUN_ID>(拿舊 raw JSON 重跑評分,調 prompt 用)。
- Telegram 用 HTML 不用 MarkdownV2:後者要跳脫 18 個字元,職缺標題滿是括號與連字號,漏一個整則 400。

【概念:ports and adapters(六角架構)】
核心邏輯只依賴「port」(介面),外部世界(104、LLM、Telegram)透過「adapter」(實作)接進來。好處:核心可測、外部可換。`);

  // A4 模型遷移
  s = content("評分模型遷移:Claude 優先,失敗整次退回 OpenRouter", SEC);
  {
    const stats = [["484", "筆職缺已有 Claude 深評", C.accent1], ["−18.3", "分:Claude 比 Gemini 平均低(405 筆配對)", C.accent3], ["80 → 60", "門檻依 Claude 口徑重校;下限 60 → 50", C.accent2]];
    stats.forEach(([big, lab, col], i) => {
      const x = 0.6 + i * 4.13;
      card(s, x, 1.6, 3.83, 2.1, C.background2, "m-" + i);
      T(s, big, { x: x + 0.3, y: 1.8, w: 3.3, h: 0.85, fontSize: 38, bold: true, color: col, valign: "middle" });
      T(s, lab, { x: x + 0.3, y: 2.7, w: 3.3, h: 0.85, fontSize: 14 });
    });
    T(s, "遷移步驟", { x: 0.6, y: 4.0, w: 6, h: 0.45, fontSize: 18, bold: true });
    T(s, bullets([
      "先用 10 筆實測額度、耗時與穩定度,再決定要不要全面切換",
      "只重評有完整 JD 的職缺,分 3 天跑完;舊的 Gemini 分數保留做配對比較",
      "依「想去程度」標記重校門檻,不沿用 Gemini 時代的分數區間",
      "Claude 失敗或額度用完 → 該次執行剩下的全部改走 OpenRouter(黏著退回,不來回切換)",
    ]), { x: 0.6, y: 4.5, w: 12.1, h: 1.9, fontSize: 15 });
  }
  caption(s, "10-06 起 llm.provider: claude_cli;Claude 回報的成本是等價值,實付為 0,每日成本上限只攔 OpenRouter 的實付費用");
  N(s, `【講稿】
10 月初把評分從 Gemini 換成 Claude 訂閱。重點不是換模型本身,而是換模型時要做的事:先小規模實測、保留舊分數做配對比較、依新口徑重校門檻。

【技術細節】
- claude -p 必須清掉 ANTHROPIC_API_KEY(否則走 API 計費)、在空目錄執行、--tools "" 與 --strict-mcp-config(否則專案設定會塞進每次評分)。
- 不開 --effort low:structured output 會被反覆打回重交,實測一筆失敗、成本沒省。
- scores.model 記錄真正用到的模型(例如 claude-cli:sonnet),這樣才分得出哪些分數是哪個口徑。
- 10-09 校準時發現:Claude 分數分不出 3/4/5 星的想去程度(現況請自行確認) —— 下一步是提升評分的鑑別力,而不是再調門檻。

【概念:sticky fallback】
失敗後在同一次執行裡不再嘗試主要供應商。避免每一筆都先失敗一次再退回,浪費時間,也避免同一批結果混雜兩種口徑。`);

  // A5 數字口徑
  s = content("數字的出處與口徑", SEC);
  {
    const hd = o => ({ text: o, options: { bold: true, color: HEX.lt1, fill: { color: HEX.dk2 } } });
    const rows = [
      [hd("數字"), hd("出處"), hd("要知道的事")],
      ["46 次實際執行", "runs 表,requests_used > 0", "排除離線重播與測試;2026-08-22 ~ 10-25"],
      ["2,623 筆 / 1,490 家", "jobs 表 count、distinct cust_no", "不重複職缺;其中 493 筆有完整 JD"],
      ["4.7%(70 / 1,481)", "runs 表,09-23 之後 24 次", "全期含改版前是 210 / 2,688 ≈ 7.8%"],
      ["約 4 分鐘", "runs 的 started_at → finished_at", "中位數 243 秒;多半是刻意的節流等待"],
      ["每天約 1 小時", "我自己的估計", "人工基準,不是量測值"],
      ["±8 分雜訊", "Gemini 時期 replay 兩次", "整批最好那則 80 vs 88;跟 Claude 的量法不同,不做數字對比"],
      ["US$1.28", "runs.llm_cost_usd 加總", "粗篩 + 深評的 OpenRouter 實付;命中率另計(hitrate.db 留存約 US$1.12)"],
    ];
    s.addTable(zhRows(rows), { x: 0.6, y: 1.5, w: 12.1, colW: [2.6, 3.9, 5.6], fontSize: 13, color: HEX.dk1, lang: "zh-TW", fontFace: FONT, border: { type: "solid", pt: 0.75, color: "D5DEE2" }, rowH: 0.56, valign: "middle", margin: [0, 0.1, 0, 0.1], fill: { color: HEX.lt1 } });
  }
  N(s, `這頁是給自己的備忘:每個數字被追問時,要能說出它從哪裡來、口徑是什麼、有什麼限制。
重算方式見 docs/resume-metrics.md 的「重算方式」段落(以唯讀模式開 jobs.db)。
資料庫最後更新:local_data/jobs.db 2026-10-25 15:43。之後要更新簡報數字,重跑那段查詢即可。`);

  await pres.writeFile({ fileName: OUT });
  await applyTheme(OUT, THEME);

  // 主題字型的東亞字(ea)也指定為微軟正黑體,否則中文會落到預設的新細明體
  const zip = await JSZip.loadAsync(fs.readFileSync(OUT));
  for (const f of Object.keys(zip.files).filter(n => /^ppt\/theme\/theme\d+\.xml$/.test(n))) {
    let xml = await zip.file(f).async("string");
    xml = xml.replace(/(<a:(?:majorFont|minorFont)>[\s\S]*?)<a:ea typeface="[^"]*"\s*\/>/g, `$1<a:ea typeface="${FONT}"/>`);
    xml = xml.replace(/<a:font script="Hant" typeface="[^"]*"\s*\/>/g, `<a:font script="Hant" typeface="${FONT}"/>`);
    zip.file(f, xml);
  }
  fs.writeFileSync(OUT, await zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE" }));
  console.log("wrote", OUT);
})().catch(e => { console.error(e); process.exit(1); });
