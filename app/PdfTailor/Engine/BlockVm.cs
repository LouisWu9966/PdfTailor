using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Text.Json.Nodes;
using System.Windows.Media;

namespace PdfTailor.Engine;

/// <summary>One editable text line, as the engine reported it plus the user's changes.
/// Coordinates are display points (top-left origin), like the engine's.</summary>
public sealed class BlockVm : INotifyPropertyChanged
{
    private readonly JsonObject _raw;

    public BlockVm(JsonObject raw)
    {
        _raw = raw;
        Id = raw["id"]!.GetValue<int>();
        Page = raw["page"]!.GetValue<int>();
        OriginalText = raw["text"]?.GetValue<string>() ?? "";
        _text = OriginalText;
        var b = raw["bounds"]!.AsArray();
        X = b[0]!.GetValue<double>(); Y = b[1]!.GetValue<double>();
        W = b[2]!.GetValue<double>(); H = b[3]!.GetValue<double>();
        Source = raw["source"]?.GetValue<string>() ?? "ocr";
        var st = raw["style"]!.AsObject();
        OriginalFamily = st["family"]?.GetValue<string>();
        OriginalSize = st["size"]?.GetValue<double>() ?? 10.5;
        OriginalBold = st["bold"]?.GetValue<bool>() ?? false;
        OriginalItalic = st["italic"]?.GetValue<bool>() ?? false;
        OriginalColor = ReadColor(st["color"], Color.FromRgb(17, 17, 17));
        BoldConfidence = st["boldConfidence"]?.GetValue<double>() ?? 1;
        ItalicConfidence = st["italicConfidence"]?.GetValue<double>() ?? 1;
        _family = OriginalFamily; _size = OriginalSize; _bold = OriginalBold; _italic = OriginalItalic; _color = OriginalColor;
        Background = ReadColor(raw["background"], Colors.White);
        BackgroundReliable = raw["backgroundReliable"]?.GetValue<bool>() ?? true;
        var conf = raw["confidence"];
        Confidence = conf == null ? null : conf.GetValue<double>();
        Notes = raw["notes"]?.AsArray().Select(n => n?.GetValue<string>() ?? "").Where(n => n.Length > 0).ToList() ?? new();
        DetectedSymbolCount = raw["detectedSymbols"]?.AsArray().Count ?? 0;
    }

    private static Color ReadColor(JsonNode? n, Color fallback)
    {
        if (n is not JsonArray a || a.Count < 3) return fallback;
        return Color.FromRgb((byte)a[0]!.GetValue<int>(), (byte)a[1]!.GetValue<int>(), (byte)a[2]!.GetValue<int>());
    }

    // ---- as analysed ----------------------------------------------------------------------
    public int Id { get; }
    public int Page { get; }
    public string OriginalText { get; }
    public double X { get; }
    public double Y { get; }
    public double W { get; }
    public double H { get; }
    public string Source { get; }
    public string? OriginalFamily { get; }
    public double OriginalSize { get; }
    public bool OriginalBold { get; }
    public bool OriginalItalic { get; }
    public Color OriginalColor { get; }
    public double BoldConfidence { get; }
    public double ItalicConfidence { get; }
    public Color Background { get; }
    public bool BackgroundReliable { get; }
    public double? Confidence { get; }
    public List<string> Notes { get; }
    public int DetectedSymbolCount { get; }

    /// <summary>OCR was unsure: the UI marks the line so the user checks it.</summary>
    public bool LowConfidence => Source == "ocr" && Confidence is < 0.75;

    // ---- edits ----------------------------------------------------------------------------
    private string _text;
    public string Text { get => _text; set { if (Set(ref _text, value)) Changed(); } }

    private string? _family;
    public string? Family { get => _family; set { if (Set(ref _family, value)) Changed(); } }

    private double _size;
    public double Size { get => _size; set { if (Set(ref _size, Math.Clamp(value, 2, 200))) Changed(); } }

    private bool _bold;
    public bool Bold { get => _bold; set { if (Set(ref _bold, value)) Changed(); } }

    private bool _italic;
    public bool Italic { get => _italic; set { if (Set(ref _italic, value)) Changed(); } }

    private Color _color;
    public Color Color { get => _color; set { if (Set(ref _color, value)) Changed(); } }

    private string _align = "left";
    public string Align { get => _align; set { if (Set(ref _align, value)) Changed(); } }

    /// <summary>"auto" (in-paint on scans), "solid" (fill with <see cref="FillColor"/>) or "none".</summary>
    private string _fillMode = "auto";
    public string FillMode { get => _fillMode; set { if (Set(ref _fillMode, value)) Changed(); } }

    public bool StyleDirty =>
        _family != OriginalFamily || Math.Abs(_size - OriginalSize) > 0.01 || _bold != OriginalBold ||
        _italic != OriginalItalic || _color != OriginalColor || _align != "left" || _fillMode != "auto";

    public bool Modified => _text != OriginalText || StyleDirty;

    public void Revert()
    {
        Text = OriginalText; Family = OriginalFamily; Size = OriginalSize; Bold = OriginalBold;
        Italic = OriginalItalic; Color = OriginalColor; Align = "left"; FillMode = "auto";
    }

    private void Changed()
    {
        OnPropertyChanged(nameof(Modified));
        OnPropertyChanged(nameof(StyleDirty));
    }

    /// <summary>The block in the shape the engine's preview/export expects.</summary>
    public JsonObject ToEditJson()
    {
        var e = (JsonObject)_raw.DeepClone();
        var bounds = new JsonArray(X, Y, W, H);
        e["text"] = _text;
        e["originalText"] = OriginalText;
        e["bounds"] = bounds;
        e["originalBounds"] = bounds.DeepClone();
        e["style"] = new JsonObject
        {
            ["family"] = _family,
            ["size"] = _size,
            ["bold"] = _bold,
            ["italic"] = _italic,
            ["color"] = new JsonArray((int)_color.R, (int)_color.G, (int)_color.B),
            ["source"] = StyleDirty ? "user" : "measured",
        };
        e["styleDirty"] = StyleDirty;
        e["fillMode"] = _fillMode;
        e["fillColor"] = new JsonArray((int)Background.R, (int)Background.G, (int)Background.B);
        e["align"] = _align;
        return e;
    }

    public override string ToString() => Text;

    public event PropertyChangedEventHandler? PropertyChanged;
    private void OnPropertyChanged([CallerMemberName] string? name = null) => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));

    private bool Set<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return false;
        field = value;
        OnPropertyChanged(name);
        return true;
    }
}
