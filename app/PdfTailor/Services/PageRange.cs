namespace PdfTailor.Services;

/// <summary>Parses "1-3, 5, 8-" style page lists (1-based; an open end means the first/last page).</summary>
public static class PageRange
{
    private static readonly char[] Separators = { ',', '，', '、', ';', '；', ' ', '\t' };

    public static bool TryParse(string? text, int pageCount, out int[] pages, out string error)
    {
        pages = Array.Empty<int>();
        error = "";
        var parts = (text ?? "").Split(Separators, StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length == 0) { error = "請輸入頁碼範圍，例如 1-3, 5, 8-"; return false; }

        var set = new SortedSet<int>();
        foreach (var raw in parts)
        {
            var s = raw.Replace('－', '-').Replace('–', '-').Replace('—', '-').Replace('~', '-').Replace('～', '-');
            int a, b;
            int dash = s.IndexOf('-');
            if (dash < 0)
            {
                if (!int.TryParse(s, out a)) { error = $"看不懂「{raw}」，請用數字，例如 1-3, 5。"; return false; }
                b = a;
            }
            else
            {
                var l = s[..dash];
                var r = s[(dash + 1)..];
                a = 1;
                b = pageCount;
                if (l.Length > 0 && !int.TryParse(l, out a)) { error = $"看不懂「{raw}」。"; return false; }
                if (r.Length > 0 && !int.TryParse(r, out b)) { error = $"看不懂「{raw}」。"; return false; }
            }
            if (a < 1 || b < 1 || a > pageCount || b > pageCount)
            {
                error = $"「{raw}」超出範圍：這份 PDF 共 {pageCount} 頁。";
                return false;
            }
            if (a > b) { error = $"「{raw}」的起訖顛倒了。"; return false; }
            for (int i = a; i <= b; i++) set.Add(i);
        }
        pages = set.ToArray();
        return true;
    }

    /// <summary>"1-3, 5" for display.</summary>
    public static string Describe(IReadOnlyList<int> pages)
    {
        if (pages.Count == 0) return "";
        var sb = new System.Text.StringBuilder();
        int start = pages[0], prev = pages[0];
        void Flush() { if (sb.Length > 0) sb.Append(", "); sb.Append(start == prev ? $"{start}" : $"{start}-{prev}"); }
        for (int i = 1; i < pages.Count; i++)
        {
            if (pages[i] == prev + 1) { prev = pages[i]; continue; }
            Flush(); start = prev = pages[i];
        }
        Flush();
        return sb.ToString();
    }
}
