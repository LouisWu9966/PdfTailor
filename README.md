# PdfTailor

離線的掃描 PDF 文字編輯器：辨識文字（含 ■□●「」『』 等符號）、保留字型 / 字級 / 粗斜體 / 顏色，
修改後先預覽，確認才輸出新 PDF。圖片、圖表與版面一律不動；未修改的頁面與影像串流逐位元組相同。

- **引擎**：Python（pypdfium2、pikepdf、reportlab、fontTools、OpenCV、Tesseract），以 JSON Lines 經 stdin/stdout 溝通。
- **外殼**：WPF（.NET 9），雙髻鯊簽名游標（LouisTool）、掃描示意動畫。
- 授權堆疊皆為寬鬆授權（未使用 PyMuPDF / AGPL）。

## 建置與執行（Windows）

需要：.NET SDK 9、Python 3.11 以上（勾選 Add to PATH）、Tesseract-OCR（UB Mannheim 版，安裝時勾選 Chinese Traditional）。

```
release.cmd            ← 互動選單
release.cmd publish    ← 引擎 + 單檔 PdfTailor.exe → publish\
release.cmd run        ← 從原始碼啟動（開發用）
release.cmd test       ← 跑引擎測試
```

輸出結構：

```
publish\PdfTailor.exe
publish\engine\pdftailor-engine.exe   (+ _internal\)
publish\engine\tesseract\             (若本機已安裝 Tesseract，會自動複製)
```

## 使用

1. 開啟或拖入 PDF。有文字層的頁面直接讀字；掃描頁自動 OCR（Esc 可取消）。
2. 點文字框編輯；右側可改字型、字級、粗體 / 斜體、顏色、對齊、舊字背景處理方式；符號列可插入 ■□「」等。
   Tab / Shift+Tab 切換上下行，Ctrl+B / Ctrl+I 粗斜體，PageUp / PageDown 換頁。
3. 「預覽並輸出」→ 預覽視窗（原始 / 修改後並排，與實際輸出同一流程產生）→「確定輸出」→ 存檔 → 完成提示。

## 部署到有防火牆限制的電腦（例如公司電腦）

`publish\` 資料夾本身**不需要網路就能執行**：`PdfTailor.exe` 是 .NET
self-contained 單檔，`pdftailor-engine.exe` 是 PyInstaller 打包檔，都已經
包含執行所需的執行環境，公司電腦不需要裝 .NET SDK、不需要裝 Python。唯一
的前提是 `publish\engine\tesseract\` 裡要有完整的 Tesseract（含
`chi_tra.traineddata`）—— 這是**建置時**才需要網路去抓的東西，執行時不需要。

會用到網路、可能被防火牆擋下來的，都只發生在「建置」這一步：
- `dotnet publish`（self-contained）第一次要連 nuget.org 下載 .NET runtime pack。
- 引擎建置要連 PyPI 裝 `requirements.txt` 套件 + PyInstaller。
- Tesseract-OCR 安裝檔要連 UB Mannheim / GitHub 下載。

建議作法（最省事，完全避開公司電腦的防火牆）：

1. 在一台能上網的電腦（自己的筆電即可）跑完 `release.cmd publish`，產生完整的
   `publish\` 資料夾。
2. 把 `publish\` 整個壓縮成 zip，上傳到 GitHub（放成 Release 附件最乾淨，
   不會讓 repo 肥大；或直接 commit 進 repo 裡的一個資料夾也行）。
3. 公司電腦只要能開 `github.com` 下載這個 zip、解壓，就能直接執行
   `PdfTailor.exe`，不需要再下載任何東西。

如果你希望公司電腦也能自己重新建置引擎（而不是只執行現成的 exe），可以把
Tesseract 的可攜版預先放進 `vendor\tesseract\`（連同原始碼一起 commit 到
GitHub），`build-engine.cmd` 會優先用這份而不去網路抓 —— 作法見
`vendor/README.md`。但 `.NET` 那邊的 NuGet runtime pack 沒有對應的簡單
離線方案，所以公司電腦要重新建置整個外殼仍需要能連 nuget.org；若連不到，
還是用上面「整包 publish\ 丟 GitHub」的做法最實際。

## 引擎測試（開發）

```
cd engine
python -m pytest tests -q
```

開發狀態與完整設計說明：見 `HANDOFF.md`。
