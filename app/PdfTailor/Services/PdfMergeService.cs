using System.ComponentModel;
using System.IO;
using PdfSharpCore.Pdf;
using PdfSharpCore.Pdf.IO;

namespace PdfTailor.Services;

/// <summary>One PDF in the merge list. <see cref="Order"/> is the 1-based position shown on the row.</summary>
public sealed class MergeItem : INotifyPropertyChanged
{
    private int _order;

    public MergeItem(string path, int pages)
    {
        FullPath = path;
        Name = System.IO.Path.GetFileName(path);
        Pages = pages;
        var info = new FileInfo(path);
        SizeLabel = FormatSize(info.Exists ? info.Length : 0);
    }

    public string FullPath { get; }
    public string Name { get; }
    public int Pages { get; }
    public string PagesLabel => $"{Pages} 頁";
    public string SizeLabel { get; }

    public int Order
    {
        get => _order;
        set
        {
            if (_order == value) return;
            _order = value;
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(Order)));
        }
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    private static string FormatSize(long bytes)
    {
        string[] units = ["B", "KB", "MB", "GB"];
        double size = bytes;
        var unit = 0;
        while (size >= 1024 && unit < units.Length - 1) { size /= 1024; unit++; }
        return $"{size:0.#} {units[unit]}";
    }
}

/// <summary>Merges PDFs in list order (ported from DocumentTailor's PdfMergeService).</summary>
public sealed class PdfMergeService
{
    /// <summary>Page count of a PDF. Throws if the file is unreadable or password-protected.</summary>
    public static int CountPages(string path)
    {
        using var doc = PdfReader.Open(path, PdfDocumentOpenMode.InformationOnly);
        return doc.PageCount;
    }

    public Task MergeAsync(IReadOnlyList<string> inputPaths, string outputPath,
        IProgress<string>? progress = null, CancellationToken cancellationToken = default)
        => Task.Run(() =>
        {
            if (inputPaths.Count < 2)
                throw new InvalidOperationException("請至少加入 2 份 PDF。");
            if (inputPaths.Any(p => string.Equals(System.IO.Path.GetFullPath(p), System.IO.Path.GetFullPath(outputPath),
                    StringComparison.OrdinalIgnoreCase)))
                throw new InvalidOperationException("輸出檔不能與清單中的來源檔同一個，請換一個檔名。");

            // Write to a temp file next to the target and swap it in, so a failed or cancelled
            // merge never leaves a half-written (or overwritten) PDF behind.
            var tmp = outputPath + ".merging.tmp";
            try
            {
                using (var output = new PdfDocument())
                {
                    for (var index = 0; index < inputPaths.Count; index++)
                    {
                        cancellationToken.ThrowIfCancellationRequested();
                        var inputPath = inputPaths[index];
                        progress?.Report($"正在合併 {index + 1} / {inputPaths.Count}：{System.IO.Path.GetFileName(inputPath)}（Esc 取消）");
                        using var input = PdfReader.Open(inputPath, PdfDocumentOpenMode.Import);
                        for (var page = 0; page < input.PageCount; page++)
                            output.AddPage(input.Pages[page]);
                    }
                    cancellationToken.ThrowIfCancellationRequested();
                    output.Save(tmp);
                }
                File.Move(tmp, outputPath, overwrite: true);
            }
            finally
            {
                try { if (File.Exists(tmp)) File.Delete(tmp); } catch { /* best effort */ }
            }
        }, cancellationToken);
}
