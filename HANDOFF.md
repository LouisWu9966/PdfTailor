# PdfTailor 交接文件（HANDOFF）

> 最後更新：2026-11-04（台北時間下午，WPF 已實機編譯成功）
> 狀態：**Python 引擎完成（81 個測試全過）；WPF 外殼已在 Windows 編譯並 publish 成功**，
> 尚未用真實 PDF（含掃描頁）實測。
> 新討論串請先讀這份檔案，再依「0. 接手第一步」開始。

---

## 0. 接手第一步

1. **實測 `publish\PdfTailor.exe`**：`release.cmd publish` 已經成功過一次（2026-11-04），
   `D:\AI\Project\PdfTailor\publish\PdfTailor.exe` 已存在。下一步是請使用者雙擊執行，
   拖一份真實 PDF 進去，走一遍 開檔 → 分析 → 編輯 → 預覽 → 輸出，回報任何 crash / 畫面異常。
   目前本機還沒裝 Tesseract-OCR，**掃描頁會分析失敗**（有文字層的 PDF 不受影響）；
   要測掃描頁得先裝 UB Mannheim 版 Tesseract（勾 Chinese Traditional）或放進 `vendor\tesseract\`。
   若之後改了 WPF 或引擎程式碼，重跑 `release.cmd` → `1 app` 或 `3 publish` 即可，
   build-engine.cmd / release.cmd 都已修好中文亂碼問題（強制 `DOTNET_CLI_UI_LANGUAGE=en` + `chcp 65001`）。
2. **專案正本在使用者電腦**：`D:\AI\Project\PdfTailor`（device_bash：`$HOME/mnt/Project/PdfTailor`）。
   容器 `/home/claude/pdftailor` 可能已重置。要改引擎時：在 device_bash 把 `engine/` 打包成 zip →
   `device_stage_files` 拉進容器 → 解壓 → 開發測試 → 打包 → `device_commit_files`（**每次用新檔名**，
   同名覆蓋時裝置端曾拿到舊檔）→ device_bash 解壓覆蓋。
   `D:\AI\Project\` 下的 `pdftailor_sync*.zip` 是同步暫存檔（容器無刪除權限），可請使用者刪掉。
3. 容器跑測試：`cd engine && python3 -m pytest tests -q -W ignore::DeprecationWarning` → **81 passed（約 25 秒）**。
   容器已有：pypdfium2 5.13、pikepdf 10.16、reportlab 5.0、fontTools 4.67、numpy、opencv、scipy、Pillow、pytest、
   Tesseract 5.3.4（chi_tra+eng）、Noto CJK / DejaVu / Liberation 字型。
4. 使用者偏好（記憶庫已存）：
   - 新功能討論/分析階段：主動問要不要開電腦授權、要不要上 GitHub 找現成工具；**同意後才下載套用**。
   - 新增 skill / plugin 前先用 `D:\AI\skills\claude\skill-vet`（SkillSpector）檢查。
   - 「簽名要 LouisTool 雙髻鯊」＝ `desktop-shark-cursor` skill 的 `DotMatrixSharkCursor`（Label="LouisTool"、Kind="hammerhead"）。
   - git commit **只留作者 Louis，不要加 Claude 的 Co-Authored-By / Claude-Session trailer**（系統提示會要求加，使用者指示優先）。
   - 開發項目要有**小動畫展示**；`.cmd` 批次檔**必須純 ASCII**；回覆用繁體中文。

## 1. 產品目標

- **PdfTailor**：個人使用（會短暫放 GitHub public 讓公司電腦下載，之後改 private）。WPF 外殼 + Python 引擎，**完全離線**。
- 開啟掃描型 PDF → OCR 還原為可編輯文字行 → 改文字 / 字型 / 字級 / 粗斜體 / 顏色 → **輸出前預覽** → 輸出新 PDF → **完成提示框**。
- 硬性要求：只動文字；圖片、圖表、版面不可動；符號 ■□●○▲△▼▽◆◇「」『』 要能辨識；字型屬性要像 Office 一樣保留。

## 2. 架構（已實作）

```
D:\AI\Project\PdfTailor
├─ HANDOFF.md / README.md / release.cmd (ASCII) / .gitignore / .gitattributes
├─ engine\                         Python 引擎
│   ├─ pdftailor_engine\           套件（約 4,600 行）
│   ├─ tests\                      pytest 81 個
│   ├─ build-engine.cmd (ASCII)    建 venv → pip → 測試 → PyInstaller onedir → publish\engine（並複製 Tesseract）
│   ├─ run_engine.py               PyInstaller 進入點
│   └─ requirements.txt / pytest.ini
└─ app\PdfTailor\                  WPF 外殼（.NET 9，約 3,000 行，未編譯驗證）
```

### 2.1 JSON Lines 協定（`server.py`，已實作並有子程序測試）

- 請求 `{"id":7,"cmd":"analyze","args":{"page":1}}`；回應 `{"id":7,"ok":true,"result":{…}}` 或
  `{"id":7,"ok":false,"error":{"code":"user|cancelled|bad-request|internal","message":"…"}}`。
- 事件：啟動時 `{"event":"ready","version":"0.1.0","tesseract":true}`；進度 `{"event":"progress","id":7,"stage":"ocr","progress":0.4}`。
- `{"cmd":"cancel","args":{"target":7}}` 由讀取執行緒立即處理，目標請求在下一個進度點以 `cancelled` 結束。
- 指令：`ping`、`version`、`open{path}`、`render{page,dpi,path?}`、`analyze{page,ocr?,ocrLines?,forceOcr?}`、
  `preview{edits,pages?,dpi?}`、`export{edits,out,verify?}`、`fonts`、`close`、`shutdown`。
- stdout 只放協定（`sys.stdout` 被導到 stderr），log 走 stderr。stdin 關閉 → 引擎自行結束（外殼當掉也不會留孤兒程序）。
- 影像以暫存 PNG 路徑交換（Session 的 temp 資料夾，`shutdown` 時刪除）。

### 2.2 引擎模組

| 模組 | 內容 |
|---|---|
| `geometry.py` | 顯示座標（pt、左上原點、已套 Rotate/CropBox）↔ user space |
| `models.py` | `TextStyle`、`Run`、`Block`、`Edit`（含 `Edit.from_block(block, **changes)`） |
| `pdfio.py` | `PdfDoc`：頁框繼承、render |
| `textlayer.py` | 讀文字層 → Block（ink 框、字型名/粗細/斜角/顏色、不可見 OCR 層判斷） |
| `contentstream.py` / `redact.py` | 位元組級 tokenizer；清空區域內的文字字串；Form XObject copy-on-write；`flate_stream()` |
| `analysis/` | 影像墨跡、符號幾何分類、行掃描（em/基線）、粗斜體與字級估計 |
| `ocr/` | `OcrEngine`（含 `dpi` 屬性）、`TesseractOcr`（傳真實 DPI；同目錄 tessdata 優先）、`ExternalOcr` |
| `scanpage.py` | 整頁 OCR 管線（見 3.2） |
| **`fonts.py`** | 字型索引（name table 所有語言別名、weight、italic、width、CFF、可嵌入；JSON 快取在 `%LOCALAPPDATA%\PdfTailor\fontindex.json`）；**Office 式字型分流**（CJK 與「寬度隨語系」符號 ■□「」※ 走東亞字型，其餘走西文字型，缺字走符號備援鏈）；`Embedder`（子集化 → CFF 轉 TrueType → 註冊 reportlab；`glyph_bounds` 取墨跡框） |
| **`fontmatch.py`** | 掃描行**字型辨識**：用 OCR 文字在每個候選字型渲染，與掃描墨跡比對（模糊遮罩相關係數 − 寬高比誤差），並依該字型的墨跡高度**反推字級** |
| **`writer.py`** | 寫回 PDF（見 3.3） |
| **`service.py`** | `Session`：open / render / analyze（含字型辨識 + 字級貼齊 Office 常用級數）/ preview / export / `verify_output` |
| **`server.py`** | JSON Lines 伺服器 |

## 3. 關鍵設計與實測結果（合成資料）

### 3.1 字型

- 無家族名的掃描行：`fontmatch` 在已安裝候選（Windows：Calibri、Arial、Times New Roman、Cambria、Verdana、Tahoma、Segoe UI、Georgia…；
  CJK：微軟正黑體、新細明體、標楷體…）中挑最像的；短行沿用同頁同語系多數決的字型。**實測 48/48 正確，字級中位誤差 1.3%**。
- 字級再「貼齊」Office 常用級數（誤差 ≤3.5% 才貼）。
- 粗/斜體優先用真的 Bold/Italic 字型檔，沒有才合成（描邊 3.5% 字級、剪切 12°）；缺字型/缺字元會在報告中提示。
- 嵌入：每次輸出把用到的字元子集化；Noto/思源等 CFF 字型會轉成 TrueType 曲線（reportlab 只吃 TrueType）。

### 3.2 OCR 管線強化（本次新增）

- **孤兒墨跡回收**：Tesseract 版面分析常漏掉孤立短詞（表頭「品名」「數量」）。把沒被任何辨識行涵蓋、像文字的墨跡
  （以 union-find 依「同列並排」或「同欄上下疊」分組，所以 品 的三個口會合在一起）逐塊裁切重新辨識（信心 ≥55 才收）。
- **弱行不佔位**：信心 <70 且 ≤3 字的行不阻擋孤兒搜尋；若孤兒結果覆蓋它就被取代（曾把「品名」的一半讀成「於」）。
- **低信心行重讀**：信心 <80 或 ≤4 字的整行，以單行模式（psm 7）、正規化到約 48px em 重讀，信心更高才採用；超大字（em>80px）會先縮小。
- **重疊行去重**、**傳給 Tesseract 真實 DPI**（原本寫死 240）。
- 結果：同一份 200dpi 掃描，以 200 / 240 / 300 / 400 DPI 分析，文字、表頭、粗體全部正確。

### 3.3 樣式量測（本次改進）

- 筆畫寬度改在**半對比度門檻**量測（模糊邊緣在半對比處正好是原邊緣），不再用固定像素補償 → 不受掃描模糊或放大倍率影響。
- 粗體基準改為**依字數加權的中位數**（避免粗體標題/表頭在短頁拉高基準）；門檻 `BOLD_RATIO=1.24`（一般行最高 1.17、粗體最低 1.31）。
- 實測：粗體 36/36、0 誤判；斜體 18/18、0 誤判；字級中位誤差 1.4%。

### 3.4 寫回 PDF（`writer.py`）

1. **移除舊字**：文字層 → 清空 content stream 中該區的字串；掃描頁 → 不碰原圖，改貼一塊**修補圖**：渲染 300dpi，
   遮罩舊字墨跡（含掃描模糊的淡邊，只取「貼著筆畫」的部分），**表格線等長細線不納入遮罩**，再用 OpenCV Telea inpaint
   以周圍紙張補回（保留紙紋與雜訊，不是白框）。同區的隱藏 OCR 文字層一併清除。
2. **畫新字**：reportlab overlay，**以墨跡對位**——新字的墨跡框落在舊字墨跡框的位置（用實際字型的字形邊界算基線；
   實測位置誤差 ≤0.6pt）。旋轉 0/90/180/270、CropBox 位移皆正確（測試涵蓋）。
3. **較長的新字**：先看右邊到下一欄/頁邊的空間，放得下就自然延伸（像 Word）；放不下才水平壓縮（最多到 85%），仍不夠就在報告提示。
4. **合併**：overlay 轉 Form XObject，以平移矩陣貼上（**不用 pikepdf add_overlay**，它會再補償一次 /Rotate 而轉錯）；
   Resources 若是繼承或共用，先做私有淺複本。
5. **存檔**：`compress_streams=False` + `stream_decode_level=none`（`compress_streams=True` 會讓 qpdf 去掉 ASCII85、重壓未修改的串流）；
   新串流自行 Flate 壓縮。→ **掃描原圖與未修改頁面逐位元組相同**（測試涵蓋）。
6. **驗證**：`export` 後重新渲染比對，修改範圍外有像素變化就回報（外殼完成框會顯示黃色警告）。

## 4. WPF 外殼（`app\PdfTailor`，程式碼已寫、未編譯）

| 檔案 | 內容 |
|---|---|
| `PdfTailor.csproj` | net9.0-windows10.0.19041.0、單檔自含 win-x64、無 NuGet 套件 |
| `App.xaml` | 直接複製 DocumentTailor 的深色主題（含看得清楚的下拉選單），加上 StyleToggle、ChipButton、FieldBox、ProgressBar、ToolTip |
| `Engine/EngineClient.cs` | 啟動引擎（順序：`PDFTAILOR_ENGINE` → `engine\pdftailor-engine.exe` → 開發時往上找 `engine\pdftailor_engine`，用 `.venv-build\Scripts\python.exe` 或 `py -3 -m pdftailor_engine`）、JSON Lines、id 配對、進度事件、CancellationToken → cancel、stderr 尾端附在錯誤訊息 |
| `Engine/BlockVm.cs` | 一行文字的 VM（原值 + 修改值），`ToEditJson()` 產生引擎要的 edit |
| `MainWindow.*` | 頁面檢視（PNG + 文字框疊層：藍=可編輯、黃虛線=低信心、綠=已修改、青=選取；已修改行會即時以近似字型顯示新字）、右側屬性面板（文字、原文、符號列 ■□●○▲△▼▽◆◇※「」『』☑☐✓→、字型、字級 ± 依 Office 級數、B/I、顏色色票+HEX、對齊、舊字背景：自動修補/純色/不覆蓋、提示）、縮放、換頁、重新辨識此頁、拖放開檔、Esc 取消、Tab 換行、Ctrl+B/I、分析時頁面上的掃描光束動畫、未輸出修改的關閉提醒 |
| `PreviewWindow.*` | **輸出前預覽**：原始/修改後左右並排（或只看其一），列出字型替代/缺字/超寬提示；「確定輸出」才存檔 |
| `DoneDialog.*` | **輸出完成**提示框（開啟檔案 / 開啟資料夾 / 完成；驗證失敗顯示黃色警告） |
| `Controls/ScanDemo.cs` | 空狀態英雄動畫：掃描光束掃過文件、每行變成可編輯框、標題被「改寫」；關閉系統動畫時靜態顯示；隱藏時暫停 |
| `Controls/DotMatrixSharkCursor.cs` | 從 `D:\AI\skills\dsh\desktop-shark-cursor\src` 複製，只改 namespace 為 `PdfTailor.Controls`；XAML 最後一個子元素、RowSpan=3、Loaded 時 `Attach(this)` |

## 5. 已知問題 / 下一步（建議順序）

1. **編譯並修正 WPF**（見 0.1）。然後實機走一遍：開檔 → 分析 → 編輯 → 預覽 → 輸出 → 完成框。
2. **真實文件驗證**（最大風險）：目前全部是合成掃描。至少試 5 份：乾淨掃描、手機拍攝、傳真品質、含表格、含圖表；
   特別觀察 OCR 錯字率、字型辨識在 Windows 大量候選字型下的表現（Arial vs Calibri vs Segoe UI 較難分）、修補痕跡。
3. **Windows OCR 整合**：外殼用 `Windows.Media.Ocr` 取行，經 `analyze{ocrLines:[{text,x,y,w,h,confidence}]}`（顯示座標 pt）
   傳給引擎（`ExternalOcr`，不能重裁切）。DocumentTailor 已有 Windows OCR 程式可參考。之後做 benchmark 決定預設引擎。
4. Tesseract 繁中錯字仍多（「個資法」→「個次法」等）→ 易混字表 `CONFUSABLE`、雙引擎投票、UI 標示低信心（已做）。
5. 掃描頁**傾斜校正**、多欄/表格版面、直書文字尚未處理。
6. PyInstaller 建置尚未在 Windows 實測：可能需要補 `--hidden-import` 或 `--collect-*`（看 `engine\build-engine-log.txt`）。
7. 可選：輸出時為掃描頁加整頁不可見文字層（可搜尋）。
8. 外殼目前一次只支援單行（Block 為一行）；多行段落重排尚未支援。
9. **公司電腦部署**：使用者要把這套工具放到有防火牆限制的公司電腦上。`publish\`
   資料夾本身執行不需網路（兩個 exe 都自含執行環境），只有「建置」需要連
   nuget.org / PyPI / Tesseract 官網。已加好 `vendor\tesseract\`（build-engine.cmd
   會優先用這裡的離線 Tesseract，見 `vendor/README.md`）。推薦做法仍是：在有網路
   的電腦建置完 `publish\`，壓縮上傳成 GitHub Release，公司電腦只下載解壓即可跑，
   完全不需要重新建置。細節見 `README.md` 的「部署到有防火牆限制的電腦」一節。

## 6. 踩過的坑（別再踩）

- 座標一律經 `PageGeometry`；reportlab `setPageRotation` 會連 MediaBox 一起換（測試用 pikepdf 設 Rotate）。
- `getbbox` 是 advance box 不是墨跡；對位要用字形邊界（fontTools BoundsPen）或實際渲染。
- pikepdf `add_overlay` 會補償 /Rotate → 自己貼 Form XObject。
- `pdf.save(compress_streams=True)` 會改動未修改的串流位元組。
- `redact.page_content_bytes` 不可 `contents_coalesce()`（會改寫頁面）；陣列內容要自己串接。
- Tesseract：`--dpi` 要傳真實值；CJK 逐字框不可靠（所以在符號處切行重讀）；版面分析會漏孤立短詞（孤兒回收）。
- 粗體判斷不能用固定像素模糊補償、不能用未加權中位數。
- 字型：Noto CJK 是 CFF（要轉 TrueType）；`.ttc` 要帶 face index；fsType 限制嵌入的字型要跳過。
- `device_commit_files` 同名覆蓋可能拿到舊檔 → 用新檔名。
- cmd.exe 批次檔必須 ASCII；git 在使用者碟上要使用者自己操作（容器無法刪 `.lock`）。

## 7. 授權

- 不用 PyMuPDF（AGPL-3.0）。依賴：pypdfium2（Apache/BSD）、pikepdf（MPL-2.0）、reportlab（BSD）、fontTools（MIT）、
  OpenCV（Apache-2.0）、Tesseract（Apache-2.0）。字型一律讀使用者本機，不隨程式散布。

## 8. 相關專案

- DocumentTailor：`D:\Codex\projects\DocumentTailor`（C#，已有 PDF 編輯 P1–P3）。⚠ 使用者仍需自己執行
  `git push --force-with-lease origin main`（先前 amend 移除了 Claude trailer）。
- Skills：`D:\AI\skills\dsh\`（ascii-sharks、desktop-shark-cursor）、`D:\AI\skills\claude\skill-vet`。

## 9. 驗收清單

- [x] 引擎測試全過（81）
- [x] 未修改區域像素差 0、掃描原圖位元組不變、未修改頁面位元組不變（測試）
- [x] 預覽與輸出同一流程
- [x] 符號辨識（■□●○▲△▼▽◆◇「」『』）與輸出字型分流
- [x] 粗/斜體/字級/顏色/字型保留（合成資料）
- [x] 輸出完成提示框、鯊魚游標、掃描動畫（程式碼）
- [x] **WPF 在 Windows 編譯通過**（2026-11-04 `release.cmd 1 app` 實測，先前兩個缺 `using System.IO;` 的錯誤已修正）
- [x] `release.cmd publish` 產出可執行的 `publish\PdfTailor.exe`（2026-11-04 實測成功，`OK: publish`）
- [ ] 實際執行 `publish\PdfTailor.exe`，開啟一份真實 PDF 跑一遍開檔→分析→編輯→預覽→輸出
- [ ] 真實掃描 PDF 驗證（乾淨掃描／手機拍／傳真品質／含表格／含圖表）
- [ ] 安裝或 vendor 好 Tesseract-OCR（含 chi_tra），目前本機還沒有，掃描頁無法 OCR
- [ ] 斷網實測完全離線
- [ ] git 初始化並推上 GitHub（作者僅 Louis）

---

## 追加（2026-11-04 下午）：側欄 / PDF 合併 / 鯊魚游標按鈕

> **這批 WPF 修改尚未編譯驗證**（寫碼環境沒有 dotnet / NuGet）。下一步：`release.cmd` → `1 app`，有編譯錯誤貼 `release-log.txt`。

- 左側功能欄 `NavRail`（編輯 PDF / PDF 合併），☰ 或 Ctrl+M 收合；狀態存 `%LOCALAPPDATA%\PdfTailor\ui.txt`。
- PDF 合併：`Services/PdfMergeService.cs`（PdfSharpCore 1.3.67，自 DocumentTailor 移植；新增 NuGet 相依）＋ `MainWindow.Merge.cs` ＋ MainWindow.xaml 的 `MergeView`。
  拖放加入 / 拖曳排序 / 上下移 / 先寫暫存檔再取代，輸出完成用 `DoneDialog(path, detail)` 新建構式。
- 狀態列右下角 `SharkToggle`：按下 → `SharkCursor.Detach()` 恢復一般游標，再按重新啟用。`DotMatrixSharkCursor` 新增 `IsAttached`，Detach 會清掉殘留畫格。
- 圖示：使用者選了 A「針線縫補」。`app/PdfTailor/Assets/PdfTailor.ico`（16–256px 共 7 尺寸；48px 以下用去掉「PDF」字樣的加粗版）、PdfTailor.png（512）、PdfTailor.svg（原稿）。
  csproj 已設 `ApplicationIcon` 並以 `Resource` 內嵌，MainWindow 的 `Icon` 也指向它。

## 追加（2026-11-04 晚）：PDF 轉 Word（側欄「W  PDF 轉 Word」）

- **做法**：結構判斷與 .docx 寫入都在 Python 引擎（`python-docx`，MIT，離線），WPF 只負責頁面範圍、存檔、進度。
  為什麼不用 C# OpenXML SDK：本機無法下載 NuGet、容器也不能編譯 C#，引擎端可以完整測試並用 LibreOffice 轉回 PDF 目視比對。
- 引擎新增：`tables.py`（框線表格：形態學抓線→格線→缺線=合併儲存格→底色格也算邊）、`docstruct.py`（行→段落、標題、清單、
  無框線對齊表格、對齊/縮排/間距）、`docxout.py`（python-docx 寫檔）、`Session.export_docx`、server 指令 `docx`
  （參數 `out`、`pages`[可省略=全部]、`edits`、`ocr`；有進度與取消）。
- 規則重點：折行以「上一行是否填滿到右緣（下一個字放得進去就不算滿）」判斷；首行縮排、凸排清單、中文不插空白（會濾掉引擎的字框空白）；
  標題依字級排名＋粗體＋編號深度對應 Heading 1–4；清單保留原符號並加 TAB（不是 Word 自動編號）；
  多欄文字行（頁首的「左 … 右日期」）用右對齊定位點；每個 PDF 頁一個 Word 區段，沿用頁面大小/邊界。
- WPF：`MainWindow.Word.cs`（新）、`Services/PageRange.cs`（新，解析 `1-3, 5, 8-`）、`MainWindow.xaml` 加 `NavWord`/`WordView`、
  `MainWindow.Merge.cs` 的 `Mode.Word`、`DoneDialog(path, detail, heading)`。**尚未在 Windows 編譯**。
- 打包：`pdftailor-engine.spec` 與 `build-engine.cmd` 已加入 `docx`（含預設範本資料檔）與 `lxml`。**引擎需重建**（`release.cmd` → 2 或 3）。
- 測試：`tests/test_docx.py`（20 項，含掃描頁 OCR、底色標題列）、`tests/docfix.py`（造 PDF 用，內嵌 TrueType 字型：
  reportlab 內建 Helvetica 不嵌入，pdfium 會把數字拆成「1 2」）。引擎測試共 101 項全過。
- 已知限制：不轉圖片、不轉頁首頁尾；只有橫線沒有直線的表格（三線表）會當成無框線表格；跨頁段落不會接起來；
  兩欄排版的文章會變成兩欄表格；Word 以自己的字型重新排字，換行位置可能與 PDF 不同。
