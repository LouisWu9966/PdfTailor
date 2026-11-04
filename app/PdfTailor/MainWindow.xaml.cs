using System.ComponentModel;
using System.Globalization;
using System.IO;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Imaging;
using Microsoft.Win32;
using PdfTailor.Engine;
using Shapes = System.Windows.Shapes;

namespace PdfTailor;

public partial class MainWindow : Window
{
    private static readonly string[] Symbols = { "■", "□", "●", "○", "▲", "△", "▼", "▽", "◆", "◇", "※", "「", "」", "『』", "☑", "☐", "✓", "→" };
    private static readonly Color[] Palette =
    {
        Color.FromRgb(0x11, 0x11, 0x11), Color.FromRgb(0x59, 0x59, 0x59), Color.FromRgb(0xC0, 0x00, 0x00),
        Color.FromRgb(0x1F, 0x4E, 0x79), Color.FromRgb(0x00, 0x70, 0xC0), Color.FromRgb(0x38, 0x76, 0x1D),
    };

    private readonly EngineClient _engine = new();
    private string? _path;
    private int _page = 1, _pageCount;
    private readonly Dictionary<int, (double W, double H)> _pageSize = new();
    private readonly Dictionary<int, List<BlockVm>> _blocks = new();
    private readonly Dictionary<int, string> _pageNotes = new();
    private BlockVm? _sel;
    private double _zoom = 1.25;
    private bool _busy, _syncing;
    private CancellationTokenSource? _cts;
    private Storyboard? _beam;
    private List<string> _families = new();

    public MainWindow()
    {
        InitializeComponent();
        InitNavAndMerge();
        InitWord();
        foreach (var s in Symbols)
        {
            var b = new Button { Content = s, Style = (Style)FindResource("ChipButton"), ToolTip = "插入 " + s };
            b.Click += (_, _) => InsertSymbol(s);
            SymbolPanel.Children.Add(b);
        }
        foreach (var c in Palette)
        {
            var b = new Button
            {
                Width = 24, Height = 24, Margin = new Thickness(0, 0, 6, 0), Cursor = Cursors.Hand,
                Background = new SolidColorBrush(c), BorderBrush = new SolidColorBrush(Color.FromRgb(0x3A, 0x52, 0x78)),
                ToolTip = Hex(c),
            };
            b.Template = RoundSwatch();
            b.Click += (_, _) => ApplyColor(c);
            ColorPanel.Children.Add(b);
        }
        _engine.Progress += (id, stage, f) => Dispatcher.BeginInvoke(() => ShowProgress(stage, f));
        _engine.Exited += () => Dispatcher.BeginInvoke(() =>
        {
            if (!_closing) SetStatus("引擎已結束；下次操作時會自動重新啟動。");
        });
    }

    private static ControlTemplate RoundSwatch()
    {
        var t = new ControlTemplate(typeof(Button));
        var border = new FrameworkElementFactory(typeof(Border));
        border.SetValue(Border.CornerRadiusProperty, new CornerRadius(12));
        border.SetValue(Border.BorderThicknessProperty, new Thickness(1));
        border.SetBinding(Border.BackgroundProperty, new System.Windows.Data.Binding("Background") { RelativeSource = System.Windows.Data.RelativeSource.TemplatedParent });
        border.SetBinding(Border.BorderBrushProperty, new System.Windows.Data.Binding("BorderBrush") { RelativeSource = System.Windows.Data.RelativeSource.TemplatedParent });
        t.VisualTree = border;
        return t;
    }

    // ================================================================ lifecycle
    private bool _closing;

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        SharkCursor.Attach(this);
        UpdateSharkToggle();
        try
        {
            SetStatus("正在啟動引擎…");
            await _engine.StartAsync();
            SetStatus(_engine.TesseractAvailable
                ? $"引擎 {_engine.Version} 已就緒。"
                : $"引擎 {_engine.Version} 已就緒，但找不到 Tesseract OCR：有文字層的 PDF 可編輯，掃描頁無法辨識。");
            var fonts = await _engine.CallAsync("fonts");
            _families = fonts?["families"]?.AsArray().Select(n => n!.GetValue<string>()).ToList() ?? new();
            var args = Environment.GetCommandLineArgs();
            if (args.Length > 1 && File.Exists(args[1])) await OpenPdf(args[1]);
        }
        catch (EngineException ex)
        {
            SetStatus("引擎無法啟動。");
            MessageBox.Show(this, ex.Message, "PdfTailor", MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }

    private async void OnClosing(object? sender, CancelEventArgs e)
    {
        if (_blocks.Values.Any(l => l.Any(b => b.Modified)) && !_exported)
        {
            var r = MessageBox.Show(this, "有尚未輸出的修改，確定要關閉嗎？", "PdfTailor", MessageBoxButton.YesNo, MessageBoxImage.Question);
            if (r != MessageBoxResult.Yes) { e.Cancel = true; return; }
        }
        _closing = true;
        _cts?.Cancel();
        await _engine.DisposeAsync();
    }

    // ================================================================ opening
    private async void OnOpen(object sender, RoutedEventArgs e)
    {
        if (_busy) return;
        var dlg = new OpenFileDialog { Filter = "PDF 檔案 (*.pdf)|*.pdf", Title = "開啟 PDF" };
        if (dlg.ShowDialog(this) == true) await OpenPdf(dlg.FileName);
    }

    private void OnDragOver(object sender, DragEventArgs e)
    {
        e.Effects = e.Data.GetDataPresent(DataFormats.FileDrop) ? DragDropEffects.Copy : DragDropEffects.None;
        e.Handled = true;
    }

    private async void OnDrop(object sender, DragEventArgs e)
    {
        if (_busy) return;
        if (_mode == Mode.Merge)
        {
            if (e.Data.GetData(DataFormats.FileDrop) is string[] dropped) AddMergeFiles(dropped);
            return;
        }
        if (e.Data.GetData(DataFormats.FileDrop) is string[] files)
        {
            var pdf = files.FirstOrDefault(f => f.EndsWith(".pdf", StringComparison.OrdinalIgnoreCase));
            if (pdf != null) await OpenPdf(pdf);
        }
    }

    private bool _exported;

    private async Task OpenPdf(string path)
    {
        if (_blocks.Values.Any(l => l.Any(b => b.Modified)) && !_exported)
        {
            var r = MessageBox.Show(this, "目前檔案有尚未輸出的修改，要放棄並開啟新檔嗎？", "PdfTailor", MessageBoxButton.YesNo, MessageBoxImage.Question);
            if (r != MessageBoxResult.Yes) return;
        }
        await Run("開啟中…", async ct =>
        {
            var info = (await _engine.CallAsync("open", new JsonObject { ["path"] = path }, ct))!.AsObject();
            _path = info["path"]!.GetValue<string>();
            _pageCount = info["pageCount"]!.GetValue<int>();
            _pageSize.Clear(); _blocks.Clear(); _pageNotes.Clear(); _exported = false;
            foreach (var p in info["pages"]!.AsArray())
                _pageSize[p!["page"]!.GetValue<int>()] = (p["width"]!.GetValue<double>(), p["height"]!.GetValue<double>());
            Title = $"PdfTailor — {Path.GetFileName(_path)}";
            EmptyState.Visibility = Visibility.Collapsed;
            PageScroll.Visibility = Visibility.Visible;
            Select(null);
        });
        if (_path != null) await GoTo(1);
        UpdateWordView();
    }

    // ================================================================ pages
    private double DipPerPt => _zoom * 96.0 / 72.0;

    private async Task GoTo(int page, bool forceOcr = false)
    {
        if (_path == null) return;
        _page = Math.Clamp(page, 1, Math.Max(1, _pageCount));
        PageLabel.Text = $"{_page} / {_pageCount}";
        PrevPage.IsEnabled = _page > 1;
        NextPage.IsEnabled = _page < _pageCount;
        Select(null);
        await RenderPage();
        if (forceOcr || !_blocks.ContainsKey(_page)) await Analyze(_page, forceOcr);
        DrawOverlay();
        UpdateCounters();
    }

    private async Task RenderPage()
    {
        var (w, h) = _pageSize[_page];
        var scale = VisualTreeHelper.GetDpi(this).DpiScaleX;
        var dpi = Math.Min(300, Math.Round(96 * _zoom * scale));
        await Run("顯示頁面…", async ct =>
        {
            var r = (await _engine.CallAsync("render", new JsonObject { ["page"] = _page, ["dpi"] = dpi }, ct))!;
            PageImage.Source = LoadBitmap(r["path"]!.GetValue<string>());
        }, showBeam: false);
        PageHost.Width = w * DipPerPt;
        PageHost.Height = h * DipPerPt;
    }

    private static BitmapImage LoadBitmap(string path)
    {
        var bmp = new BitmapImage();
        bmp.BeginInit();
        bmp.CacheOption = BitmapCacheOption.OnLoad; // do not keep the temp file locked
        bmp.CreateOptions = BitmapCreateOptions.IgnoreImageCache;
        bmp.UriSource = new Uri(path);
        bmp.EndInit();
        bmp.Freeze();
        return bmp;
    }

    private async Task Analyze(int page, bool forceOcr)
    {
        await Run(forceOcr ? "重新辨識中…" : "分析頁面中…", async ct =>
        {
            var r = (await _engine.CallAsync("analyze", new JsonObject { ["page"] = page, ["forceOcr"] = forceOcr }, ct))!.AsObject();
            var list = r["blocks"]!.AsArray().Select(b => new BlockVm(b!.AsObject())).ToList();
            foreach (var b in list) b.PropertyChanged += OnBlockChanged;
            _blocks[page] = list;
            var notes = r["notes"]?.AsArray().Select(n => n!.GetValue<string>()).ToList() ?? new();
            var mode = r["mode"]?.GetValue<string>() == "ocr" ? "影像辨識" : "文字層";
            var secs = r["seconds"]?.GetValue<double>() ?? 0;
            _pageNotes[page] = $"第 {page} 頁：{mode}，{list.Count} 行，{secs:0.0} 秒。" + (notes.Count > 0 ? " " + string.Join(" ", notes) : "");
        });
        if (_pageNotes.TryGetValue(page, out var note)) SetStatus(note);
    }

    private async void OnPrevPage(object sender, RoutedEventArgs e) { if (!_busy) await GoTo(_page - 1); }
    private async void OnNextPage(object sender, RoutedEventArgs e) { if (!_busy) await GoTo(_page + 1); }

    private async void OnReOcr(object sender, RoutedEventArgs e)
    {
        if (_busy || _path == null) return;
        if (_blocks.TryGetValue(_page, out var l) && l.Any(b => b.Modified))
        {
            var r = MessageBox.Show(this, "重新辨識會清除這一頁的修改，要繼續嗎？", "PdfTailor", MessageBoxButton.YesNo, MessageBoxImage.Question);
            if (r != MessageBoxResult.Yes) return;
        }
        await GoTo(_page, forceOcr: true);
    }

    private async void OnZoomChanged(object sender, SelectionChangedEventArgs e)
    {
        if (sender is ComboBox box && box.SelectedItem is ComboBoxItem item && double.TryParse((string)item.Tag, NumberStyles.Float, CultureInfo.InvariantCulture, out var z))
        {
            _zoom = z;
            if (_path != null && !_busy && IsLoaded)
            {
                var keep = _sel;
                await RenderPage();
                DrawOverlay();
                if (keep != null) Select(keep);
            }
        }
    }

    // ================================================================ overlay
    private readonly Dictionary<BlockVm, FrameworkElement> _visuals = new();

    private void DrawOverlay()
    {
        Overlay.Children.Clear();
        _visuals.Clear();
        if (!_blocks.TryGetValue(_page, out var list)) return;
        foreach (var b in list)
        {
            var v = MakeVisual(b);
            _visuals[b] = v;
            Overlay.Children.Add(v);
        }
        UpdateCounters();
    }

    private FrameworkElement MakeVisual(BlockVm b)
    {
        double k = DipPerPt, pad = 2;
        var host = new Grid { Width = b.W * k + 2 * pad, Height = b.H * k + 2 * pad, Cursor = Cursors.IBeam, Background = Brushes.Transparent, Tag = b };
        Canvas.SetLeft(host, b.X * k - pad);
        Canvas.SetTop(host, b.Y * k - pad);

        if (b.Modified)
        {
            // WYSIWYG-ish: cover the old line with its paper colour and show the new text.
            // A Canvas does not clip, so a longer line runs on past its box as it will in
            // the PDF. The exact result comes from the engine's preview.
            host.Children.Add(new Border { Background = new SolidColorBrush(b.Background) });
            var tb = new TextBlock
            {
                Text = b.Text,
                FontFamily = new FontFamily(PreviewFamily(b)),
                FontSize = Math.Max(4, b.Size * 96.0 / 72.0 * _zoom),
                FontWeight = b.Bold ? FontWeights.Bold : FontWeights.Normal,
                FontStyle = b.Italic ? FontStyles.Italic : FontStyles.Normal,
                Foreground = new SolidColorBrush(b.Color),
                TextWrapping = TextWrapping.NoWrap,
            };
            tb.Measure(new Size(double.PositiveInfinity, double.PositiveInfinity));
            double tw = tb.DesiredSize.Width, boxW = b.W * k;
            double left = b.Align switch { "center" => pad + (boxW - tw) / 2, "right" => pad + boxW - tw, _ => pad };
            var canvas = new Canvas { ClipToBounds = false };
            Canvas.SetLeft(tb, left);
            Canvas.SetTop(tb, pad + b.H * k - tb.DesiredSize.Height * 0.92);
            canvas.Children.Add(tb);
            host.Children.Add(canvas);
        }

        var frame = new Shapes.Rectangle { RadiusX = 3, RadiusY = 3, StrokeThickness = 1.2, Fill = Brushes.Transparent };
        StyleFrame(frame, b);
        host.Children.Add(frame);

        host.ToolTip = b.LowConfidence ? $"辨識信心 {b.Confidence:P0}：請確認文字" : null;
        host.MouseLeftButtonDown += (_, e) => { Select(b); e.Handled = true; };
        return host;
    }

    private static string PreviewFamily(BlockVm b)
    {
        if (!string.IsNullOrEmpty(b.Family)) return b.Family + ", Microsoft JhengHei, Segoe UI Symbol";
        bool cjk = b.Text.Any(ch => ch >= 0x2E80 && ch <= 0x9FFF);
        return cjk ? "Microsoft JhengHei, Segoe UI Symbol" : "Calibri, Arial";
    }

    private void StyleFrame(Shapes.Rectangle r, BlockVm b)
    {
        bool sel = ReferenceEquals(b, _sel);
        Color c = sel ? Color.FromRgb(0x28, 0xD7, 0xFF)
            : b.Modified ? Color.FromRgb(0x65, 0xE6, 0xAD)
            : b.LowConfidence ? Color.FromRgb(0xF5, 0xB9, 0x41)
            : Color.FromRgb(0x5D, 0x7B, 0xA3);
        r.Stroke = new SolidColorBrush(c);
        r.StrokeThickness = sel ? 2 : (b.Modified || b.LowConfidence ? 1.5 : 1);
        r.Opacity = sel || b.Modified || b.LowConfidence ? 1 : 0.55;
        r.StrokeDashArray = b.LowConfidence && !sel && !b.Modified ? new DoubleCollection { 3, 2 } : null;
        r.Fill = new SolidColorBrush(Color.FromArgb(sel ? (byte)28 : (byte)0, 0x28, 0xD7, 0xFF));
    }

    private void RefreshVisual(BlockVm b)
    {
        if (!_visuals.TryGetValue(b, out var old)) return;
        var idx = Overlay.Children.IndexOf(old);
        var v = MakeVisual(b);
        _visuals[b] = v;
        if (idx >= 0) { Overlay.Children.RemoveAt(idx); Overlay.Children.Insert(idx, v); }
    }

    private void OnOverlayEmptyClick(object sender, MouseButtonEventArgs e) => Select(null);

    // ================================================================ selection / inspector
    private void Select(BlockVm? b)
    {
        var prev = _sel;
        _sel = b;
        if (prev != null) RefreshVisual(prev);
        if (b != null) RefreshVisual(b);

        _syncing = true;
        try
        {
            Inspector.IsEnabled = b != null;
            if (b == null)
            {
                BlockInfo.Text = _path == null ? "點選頁面上的文字框開始編輯。" : "點選頁面上的文字框開始編輯（Tab 跳下一行）。";
                EditText.Text = ""; OriginalText.Text = ""; NotesBox.Visibility = Visibility.Collapsed;
                return;
            }
            var src = b.Source == "ocr" ? $"影像辨識{(b.Confidence is double c ? $"，信心 {c:P0}" : "")}" : "PDF 文字層";
            var sty = $"{b.OriginalFamily ?? "未知字型"} {b.OriginalSize:0.#}pt{(b.OriginalBold ? " 粗體" : "")}{(b.OriginalItalic ? " 斜體" : "")}";
            BlockInfo.Text = $"第 {b.Page} 頁 · {src} · 偵測：{sty}";
            EditText.Text = b.Text;
            OriginalText.Text = b.OriginalText;

            var fams = new List<string>(_families);
            foreach (var f in new[] { b.Family, b.OriginalFamily })
                if (!string.IsNullOrEmpty(f) && !fams.Contains(f)) fams.Insert(0, f);
            fams.Insert(0, "（自動）");
            FamilyBox.ItemsSource = fams;
            FamilyBox.SelectedItem = string.IsNullOrEmpty(b.Family) ? "（自動）" : b.Family;

            SizeBox.Text = b.Size.ToString("0.#", CultureInfo.InvariantCulture);
            BoldToggle.IsChecked = b.Bold;
            ItalicToggle.IsChecked = b.Italic;
            ColorHex.Text = Hex(b.Color);
            SelectTag(AlignBox, b.Align);
            SelectTag(FillBox, b.FillMode);

            var notes = new List<string>(b.Notes);
            if (b.LowConfidence) notes.Insert(0, "辨識信心偏低，請對照原文確認每個字。");
            if (b.DetectedSymbolCount > 0) notes.Add($"其中 {b.DetectedSymbolCount} 個符號是依形狀辨識（■□「」等）。");
            if (b.Source == "ocr" && !b.BackgroundReliable) notes.Add("此行背景不是單一顏色（底紋或漸層），自動修補可能略有痕跡。");
            NotesText.Text = string.Join("\n", notes);
            NotesBox.Visibility = notes.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        }
        finally { _syncing = false; }

        if (_visuals.TryGetValue(b, out var v)) v.BringIntoView();
        EditText.Focus();
        EditText.CaretIndex = EditText.Text.Length;
    }

    private static void SelectTag(ComboBox box, string tag)
    {
        foreach (var o in box.Items)
            if (o is ComboBoxItem i && (string)i.Tag == tag) { box.SelectedItem = i; return; }
    }

    private void OnBlockChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (sender is BlockVm b && e.PropertyName == nameof(BlockVm.Modified)) UpdateCounters();
    }

    private void UpdateCounters()
    {
        int n = _blocks.Values.Sum(l => l.Count(b => b.Modified));
        ModifiedLabel.Text = n > 0 ? $"已修改 {n} 行" : "";
        ExportButton.IsEnabled = n > 0 && !_busy;
        ReOcrButton.IsEnabled = _path != null && !_busy;
    }

    private void Edit(Action<BlockVm> change)
    {
        if (_syncing || _sel == null) return;
        change(_sel);
        RefreshVisual(_sel);
        UpdateCounters();
    }

    private void OnTextChanged(object sender, TextChangedEventArgs e) => Edit(b => b.Text = EditText.Text);

    private void InsertSymbol(string s)
    {
        if (_sel == null) return;
        int at = EditText.CaretIndex;
        string ins = s == "『』" ? "『』" : s;
        EditText.Text = EditText.Text.Insert(at, ins);
        EditText.CaretIndex = at + (s == "『』" ? 1 : ins.Length);
        EditText.Focus();
    }

    private void OnFamilyChanged(object sender, SelectionChangedEventArgs e) =>
        Edit(b => b.Family = FamilyBox.SelectedItem is string f && f != "（自動）" ? f : null);

    private void OnSizeKey(object sender, KeyEventArgs e) { if (e.Key == Key.Enter) OnSizeCommit(sender, e); }

    private void OnSizeCommit(object sender, RoutedEventArgs e)
    {
        if (double.TryParse(SizeBox.Text, NumberStyles.Float, CultureInfo.InvariantCulture, out var v) && v >= 2 && v <= 200)
            Edit(b => b.Size = v);
        else if (_sel != null) SizeBox.Text = _sel.Size.ToString("0.#", CultureInfo.InvariantCulture);
    }

    private static readonly double[] OfficeSizes = { 6, 7, 8, 9, 10, 10.5, 11, 12, 14, 16, 18, 20, 22, 24, 26, 28, 36, 48, 72 };

    private void OnSizeUp(object sender, RoutedEventArgs e) => StepSize(+1);
    private void OnSizeDown(object sender, RoutedEventArgs e) => StepSize(-1);

    private void StepSize(int dir)
    {
        if (_sel == null) return;
        double cur = _sel.Size;
        double next = dir > 0 ? OfficeSizes.FirstOrDefault(s => s > cur + 0.01, cur + 2) : OfficeSizes.LastOrDefault(s => s < cur - 0.01, Math.Max(2, cur - 1));
        Edit(b => b.Size = next);
        _syncing = true;
        SizeBox.Text = next.ToString("0.#", CultureInfo.InvariantCulture);
        _syncing = false;
    }

    private void OnBoldClick(object sender, RoutedEventArgs e) => Edit(b => b.Bold = BoldToggle.IsChecked == true);
    private void OnItalicClick(object sender, RoutedEventArgs e) => Edit(b => b.Italic = ItalicToggle.IsChecked == true);

    private void ApplyColor(Color c)
    {
        Edit(b => b.Color = c);
        _syncing = true;
        ColorHex.Text = Hex(c);
        _syncing = false;
    }

    private void OnColorHexKey(object sender, KeyEventArgs e) { if (e.Key == Key.Enter) OnColorHexCommit(sender, e); }

    private void OnColorHexCommit(object sender, RoutedEventArgs e)
    {
        if (_sel == null) return;
        var t = ColorHex.Text.Trim().TrimStart('#');
        if (t.Length == 6 && int.TryParse(t, NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var v))
            ApplyColor(Color.FromRgb((byte)(v >> 16), (byte)(v >> 8), (byte)v));
        else
            ColorHex.Text = Hex(_sel.Color);
    }

    private static string Hex(Color c) => $"#{c.R:X2}{c.G:X2}{c.B:X2}";

    private void OnAlignChanged(object sender, SelectionChangedEventArgs e) =>
        Edit(b => b.Align = (AlignBox.SelectedItem as ComboBoxItem)?.Tag as string ?? "left");

    private void OnFillChanged(object sender, SelectionChangedEventArgs e) =>
        Edit(b => b.FillMode = (FillBox.SelectedItem as ComboBoxItem)?.Tag as string ?? "auto");

    private void OnRevert(object sender, RoutedEventArgs e)
    {
        if (_sel == null) return;
        var b = _sel;
        b.Revert();
        Select(b);
        UpdateCounters();
    }

    private void OnPrevBlock(object sender, RoutedEventArgs e) => StepBlock(-1);
    private void OnNextBlock(object sender, RoutedEventArgs e) => StepBlock(+1);

    private void StepBlock(int dir)
    {
        if (!_blocks.TryGetValue(_page, out var list) || list.Count == 0) return;
        int i = _sel == null ? (dir > 0 ? -1 : list.Count) : list.IndexOf(_sel);
        int j = Math.Clamp(i + dir, 0, list.Count - 1);
        Select(list[j]);
    }

    private async void OnKeyDown(object sender, KeyEventArgs e)
    {
        bool ctrl = (Keyboard.Modifiers & ModifierKeys.Control) != 0;
        if (ctrl && e.Key == Key.M) { e.Handled = true; SetNavOpen(!_navOpen); return; }
        if (_mode == Mode.Word)
        {
            if (ctrl && e.Key == Key.O) { e.Handled = true; OnOpen(sender, e); }
            else if (e.Key == Key.Escape && _busy) { _cts?.Cancel(); e.Handled = true; }
            return;
        }
        if (_mode == Mode.Merge)
        {
            if (ctrl && e.Key == Key.O) { e.Handled = true; PickMergeFiles(); }
            else if (e.Key == Key.Escape && _busy) { _cts?.Cancel(); e.Handled = true; }
            return;
        }
        if (ctrl && e.Key == Key.O) { e.Handled = true; OnOpen(sender, e); return; }
        if (e.Key == Key.Tab && _sel != null)
        {
            e.Handled = true;
            StepBlock((Keyboard.Modifiers & ModifierKeys.Shift) != 0 ? -1 : 1);
            return;
        }
        if (ctrl && e.Key == Key.B && _sel != null) { e.Handled = true; BoldToggle.IsChecked = !(BoldToggle.IsChecked == true); OnBoldClick(sender, e); return; }
        if (ctrl && e.Key == Key.I && _sel != null) { e.Handled = true; ItalicToggle.IsChecked = !(ItalicToggle.IsChecked == true); OnItalicClick(sender, e); return; }
        if (e.Key == Key.Escape && _busy) { _cts?.Cancel(); e.Handled = true; return; }
        if (e.Key == Key.Escape && _sel != null) { Select(null); e.Handled = true; return; }
        if (_busy || EditText.IsKeyboardFocusWithin) return;
        if (e.Key == Key.PageDown) { e.Handled = true; await GoTo(_page + 1); }
        if (e.Key == Key.PageUp) { e.Handled = true; await GoTo(_page - 1); }
    }

    // ================================================================ preview & export
    private JsonArray CollectEdits(out int modified)
    {
        var edits = new JsonArray();
        modified = 0;
        foreach (var (page, list) in _blocks)
        {
            if (!list.Any(b => b.Modified)) continue;
            // send the whole page: the writer uses unmodified neighbours to know how far a
            // longer line may run before it hits the next column
            foreach (var b in list)
            {
                edits.Add(b.ToEditJson());
                if (b.Modified) modified++;
            }
        }
        return edits;
    }

    private async void OnPreviewExport(object sender, RoutedEventArgs e)
    {
        if (_busy || _path == null) return;
        var edits = CollectEdits(out int n);
        if (n == 0) { MessageBox.Show(this, "還沒有任何修改。", "PdfTailor"); return; }

        List<PreviewPage>? pages = null;
        List<string> notes = new();
        await Run("產生預覽中…", async ct =>
        {
            var pv = (await _engine.CallAsync("preview", new JsonObject { ["edits"] = edits.DeepClone(), ["dpi"] = 120 }, ct))!.AsObject();
            pages = new List<PreviewPage>();
            foreach (var img in pv["images"]!.AsArray())
            {
                int p = img!["page"]!.GetValue<int>();
                var orig = (await _engine.CallAsync("render", new JsonObject { ["page"] = p, ["dpi"] = 120 }, ct))!;
                pages.Add(new PreviewPage(p, orig["path"]!.GetValue<string>(), img["path"]!.GetValue<string>()));
            }
            notes = DescribeNotes(pv["report"]?["notes"]?.AsArray());
        });
        if (pages == null || pages.Count == 0) return;

        var preview = new PreviewWindow(pages, notes, n) { Owner = this };
        if (preview.ShowDialog() != true) { SetStatus("已返回編輯。"); return; }

        var save = new SaveFileDialog
        {
            Filter = "PDF 檔案 (*.pdf)|*.pdf",
            FileName = Path.GetFileNameWithoutExtension(_path) + "_已編輯.pdf",
            InitialDirectory = Path.GetDirectoryName(_path),
            Title = "輸出 PDF",
        };
        if (save.ShowDialog(this) != true) return;
        var outPath = save.FileName;

        JsonObject? result = null;
        await Run("輸出中…", async ct =>
        {
            result = (await _engine.CallAsync("export", new JsonObject { ["edits"] = edits.DeepClone(), ["out"] = outPath, ["verify"] = true }, ct))!.AsObject();
        });
        if (result == null) return;
        _exported = true;
        bool ok = result["verify"]?["ok"]?.GetValue<bool>() ?? true;
        SetStatus($"已輸出：{outPath}");
        new DoneDialog(outPath, n, ok) { Owner = this }.ShowDialog();
    }

    private static List<string> DescribeNotes(JsonArray? notes)
    {
        var list = new List<string>();
        if (notes == null) return list;
        foreach (var n in notes)
        {
            var page = n?["page"]?.GetValue<int>() ?? 0;
            var note = n?["note"]?.GetValue<string>() ?? "";
            string text = note switch
            {
                var s when s.StartsWith("font-missing:") => $"這台電腦沒有字型「{s[13..]}」，已改用最接近的字型。",
                var s when s.StartsWith("chars-missing:") => $"字型缺少這些字元，可能顯示為方框：{s[14..]}",
                "text-overflow" => "有一行新文字比原本長很多，已略為壓縮仍超出範圍。",
                _ => note,
            };
            var line = $"第 {page} 頁：{text}";
            if (!list.Contains(line)) list.Add(line);
        }
        return list;
    }

    // ================================================================ busy / status
    private async Task Run(string status, Func<CancellationToken, Task> work, bool showBeam = true)
    {
        if (_busy) return;
        _busy = true;
        _cts = new CancellationTokenSource();
        OpenButton.IsEnabled = false; ExportButton.IsEnabled = false; ReOcrButton.IsEnabled = false;
        PrevPage.IsEnabled = false; NextPage.IsEnabled = false;
        SetStatus(status + "（Esc 取消）");
        Progress.Visibility = Visibility.Visible;
        Progress.IsIndeterminate = true;
        if (showBeam) StartBeam();
        try
        {
            await work(_cts.Token);
        }
        catch (EngineException ex) when (ex.Code == "cancelled")
        {
            SetStatus("已取消。");
        }
        catch (EngineException ex)
        {
            SetStatus("發生錯誤。");
            MessageBox.Show(this, ex.Message, "PdfTailor", MessageBoxButton.OK, ex.Code == "user" ? MessageBoxImage.Warning : MessageBoxImage.Error);
        }
        finally
        {
            StopBeam();
            Progress.Visibility = Visibility.Hidden;
            _busy = false;
            OpenButton.IsEnabled = true;
            PrevPage.IsEnabled = _path != null && _page > 1;
            NextPage.IsEnabled = _path != null && _page < _pageCount;
            UpdateCounters();
            _cts.Dispose();
            _cts = null;
        }
    }

    private static readonly Dictionary<string, string> Stages = new()
    {
        ["text-layer"] = "讀取文字層", ["render"] = "轉成影像", ["ocr"] = "辨識文字", ["fonts"] = "比對字型", ["done"] = "完成",
    };

    private void ShowProgress(string stage, double f)
    {
        if (!_busy) return;
        Progress.IsIndeterminate = false;
        Progress.Value = f;
        if (Stages.TryGetValue(stage, out var s)) SetStatus($"{s}…（Esc 取消）");
        else if (stage.StartsWith("頁 ")) SetStatus($"轉換第 {stage[2..]} 頁…（Esc 取消）");
        else if (stage == "寫入 Word") SetStatus("寫入 Word 檔…");
    }

    private void SetStatus(string s) => StatusText.Text = s;

    /// <summary>A scan beam sweeping over the page while it is being analysed.</summary>
    private void StartBeam()
    {
        if (!SystemParameters.ClientAreaAnimation || PageScroll.Visibility != Visibility.Visible) return;
        BeamLayer.Children.Clear();
        var cyan = Color.FromRgb(0x28, 0xD7, 0xFF);
        double h = Math.Max(200, PageHost.ActualHeight);
        var beam = new Shapes.Rectangle
        {
            Width = Math.Max(100, PageHost.ActualWidth), Height = 60,
            Fill = new LinearGradientBrush(new GradientStopCollection
            {
                new GradientStop(Color.FromArgb(0, cyan.R, cyan.G, cyan.B), 0),
                new GradientStop(Color.FromArgb(55, cyan.R, cyan.G, cyan.B), 0.9),
                new GradientStop(Color.FromArgb(200, cyan.R, cyan.G, cyan.B), 1),
            }, new Point(0, 0), new Point(0, 1)),
        };
        BeamLayer.Children.Add(beam);
        var a = new DoubleAnimation(-60, h, TimeSpan.FromSeconds(1.8)) { RepeatBehavior = RepeatBehavior.Forever, EasingFunction = new SineEase { EasingMode = EasingMode.EaseInOut } };
        Storyboard.SetTarget(a, beam);
        Storyboard.SetTargetProperty(a, new PropertyPath(Canvas.TopProperty));
        _beam = new Storyboard();
        _beam.Children.Add(a);
        _beam.Begin(this, true);
    }

    private void StopBeam()
    {
        _beam?.Stop(this);
        _beam = null;
        BeamLayer.Children.Clear();
    }
}

public sealed record PreviewPage(int Page, string OriginalPng, string EditedPng);
