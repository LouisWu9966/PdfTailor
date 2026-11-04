using System.IO;
using System.Text.Json.Nodes;
using System.Windows;
using Microsoft.Win32;
using PdfTailor.Services;

namespace PdfTailor;

// "PDF 轉 Word" page: exports a page range of the open PDF as a flowing .docx (the engine does the work).
public partial class MainWindow
{
    private void InitWord()
    {
        WordAll.IsChecked = true;
        WordUseEdits.IsChecked = true;
        UpdateWordView();
    }

    private void OnWordOptionChanged(object sender, RoutedEventArgs e)
    {
        if (WordRangeBox == null) return;   // fired while the XAML is still loading
        UpdateWordView();
        if (WordRange.IsChecked == true && IsLoaded) WordRangeBox.Focus();
    }

    private void UpdateWordView()
    {
        if (WordRangeBox == null) return;
        bool has = _path != null;
        WordFileName.Text = has ? Path.GetFileName(_path) : "尚未開啟 PDF";
        WordFileInfo.Text = has ? $"共 {_pageCount} 頁" : "把 PDF 拖進來，或按「開啟 PDF」。";
        WordCurrent.Content = has ? $"目前頁（第 {_page} 頁）" : "目前頁";
        WordRangeBox.IsEnabled = WordRange.IsChecked == true;
        WordButton.IsEnabled = has && !_busy;
        WordRangeHint.Text = has ? $"例：1-3, 5, 8-（共 {_pageCount} 頁；8- 代表第 8 頁到最後一頁）" : "";
    }

    private void OnWordOpen(object sender, RoutedEventArgs e) => OnOpen(sender, e);

    private async void OnWordConvert(object sender, RoutedEventArgs e)
    {
        if (_busy || _path == null) return;

        int[]? pages = null;   // null = every page
        if (WordCurrent.IsChecked == true) pages = new[] { _page };
        else if (WordRange.IsChecked == true)
        {
            if (!PageRange.TryParse(WordRangeBox.Text, _pageCount, out var list, out var error))
            {
                MessageBox.Show(this, error, "PdfTailor", MessageBoxButton.OK, MessageBoxImage.Warning);
                WordRangeBox.Focus();
                return;
            }
            pages = list;
        }

        var edits = new JsonArray();
        if (WordUseEdits.IsChecked == true) edits = CollectEdits(out _);

        string suffix = pages == null ? "" : pages.Length == 1 ? $"_p{pages[0]}" : $"_p{PageRange.Describe(pages).Replace(", ", "_")}";
        var save = new SaveFileDialog
        {
            Title = "輸出 Word",
            Filter = "Word 文件 (*.docx)|*.docx",
            FileName = Path.GetFileNameWithoutExtension(_path) + suffix + ".docx",
            InitialDirectory = Path.GetDirectoryName(_path),
            AddExtension = true,
            DefaultExt = ".docx",
        };
        if (save.ShowDialog(this) != true) return;
        var outPath = save.FileName;

        JsonObject? result = null;
        await Run("轉成 Word 中…", async ct =>
        {
            var args = new JsonObject { ["out"] = outPath, ["edits"] = edits.DeepClone() };
            if (pages != null)
            {
                var arr = new JsonArray();
                foreach (var p in pages) arr.Add(p);
                args["pages"] = arr;
            }
            result = (await _engine.CallAsync("docx", args, ct))!.AsObject();
        }, showBeam: false);
        UpdateWordView();
        if (result == null) return;

        int n = result["pages"]?.AsArray().Count ?? 0;
        int paras = result["paragraphs"]?.GetValue<int>() ?? 0;
        int heads = result["headings"]?.GetValue<int>() ?? 0;
        int lists = result["lists"]?.GetValue<int>() ?? 0;
        int tables = result["tables"]?.GetValue<int>() ?? 0;
        SetStatus($"已輸出：{outPath}");
        new DoneDialog(outPath,
            $"已轉出 {n} 頁：{paras} 個段落（其中 {heads} 個標題、{lists} 個清單項目）、{tables} 個表格。\n" +
            "圖片與頁首頁尾不會轉出；請在 Word 開啟後檢查表格與版面。", "轉換完成") { Owner = this }.ShowDialog();
    }
}
