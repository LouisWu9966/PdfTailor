# vendor/ — 離線建置用的預先下載資源

這個資料夾放「建置時」需要下載、但公司防火牆可能擋掉的二進位資源。
放進來之後 commit 到 GitHub，公司電腦 clone/下載整個 repo 就能離線建置，
不需要再連到 Tesseract 官網、PyPI 等外部站點抓這些東西。

**注意**：這只解決「引擎（Python/Tesseract）建置」這一段的防火牆問題。
`.NET`（WPF 外殼）的 self-contained 發布第一次仍需要連 nuget.org 下載 runtime
pack（約數百 MB，只有編譯機器需要，執行時不需要）。如果公司電腦完全不能連
外網，建議直接用「README.md / HANDOFF.md」裡說的 Track A 做法：在有網路的
電腦上把 `release.cmd publish` 整個跑完，把產生的 `publish\` 資料夾壓縮後
上傳成 GitHub Release 附件，公司電腦只要能開 github.com 下載這個 zip、解壓
就能執行 —— 不需要 .NET SDK、不需要 Python、不需要 Tesseract 安裝檔，因為
`publish\` 裡的兩個 exe 都已經是自含執行檔。

## vendor/tesseract/

把 Tesseract-OCR 的**可攜式安裝結果**整份複製進來（而不是安裝程式本身）：

1. 在一台可以上網的電腦，安裝 UB Mannheim 版 Tesseract-OCR
   （https://github.com/UB-Mannheim/tesseract/wiki），安裝時務必勾選
   **Chinese - Traditional (chi_tra)**。
2. 把整個安裝資料夾（預設 `C:\Program Files\Tesseract-OCR`）複製到這裡，
   變成：

   ```
   vendor\tesseract\tesseract.exe
   vendor\tesseract\*.dll
   vendor\tesseract\tessdata\chi_tra.traineddata
   vendor\tesseract\tessdata\eng.traineddata
   vendor\tesseract\tessdata\*.traineddata  (其餘語言可刪，縮小體積)
   ```

3. `engine\build-engine.cmd` 會優先找 `vendor\tesseract\tesseract.exe`，
   找到就直接複製進 `publish\engine\tesseract\`，不會再去找系統安裝路徑、
   也不會連網路下載。

4. 體積：tesseract.exe + dll 約數 MB，chi_tra.traineddata 約 13MB、
   eng.traineddata 約 4MB，整包通常在 GitHub 單檔 100MB 限制內，
   不需要 Git LFS（除非你額外塞了更多語言包）。

這個資料夾**要**被 commit（不在 `.gitignore` 裡），因為目的就是把二進位檔
存進 GitHub 讓防火牆後的電腦下載。
