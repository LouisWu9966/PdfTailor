using System.Collections.ObjectModel;
using System.Collections.Specialized;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using Microsoft.Win32;
using PdfTailor.Services;

namespace PdfTailor;

// Left navigation rail (collapsible), the PDF merge page, and the shark-cursor toggle.
public partial class MainWindow
{
    private enum Mode { Edit, Merge, Word }

    private const double NavWidth = 196;

    private Mode _mode = Mode.Edit;
    private bool _navOpen = true;
    private readonly ObservableCollection<MergeItem> _mergeFiles = new();
    private readonly PdfMergeService _mergeService = new();
    private MergeItem? _dragItem;
    private Point _dragStart;

    private static string UiStatePath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PdfTailor", "ui.txt");

    private void InitNavAndMerge()
    {
        try { if (File.Exists(UiStatePath)) _navOpen = File.ReadAllText(UiStatePath).Trim() != "nav=0"; }
        catch { /* default: open */ }
        NavRail.Width = _navOpen ? NavWidth : 0;
        NavRail.Visibility = _navOpen ? Visibility.Visible : Visibility.Collapsed;

        MergeList.ItemsSource = _mergeFiles;
        _mergeFiles.CollectionChanged += OnMergeFilesChanged;
        UpdateMergeSummary();
        SetMode(Mode.Edit);
    }

    // ================================================================ collapsible left rail
    private void OnToggleNav(object sender, RoutedEventArgs e) => SetNavOpen(!_navOpen);

    private void SetNavOpen(bool open)
    {
        _navOpen = open;
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(UiStatePath)!);
            File.WriteAllText(UiStatePath, open ? "nav=1" : "nav=0");
        }
        catch { /* not worth bothering the user */ }

        NavToggle.ToolTip = open ? "隱藏功能欄 (Ctrl+M)" : "顯示功能欄 (Ctrl+M)";
        if (!SystemParameters.ClientAreaAnimation)
        {
            NavRail.BeginAnimation(WidthProperty, null);
            NavRail.Width = open ? NavWidth : 0;
            NavRail.Visibility = open ? Visibility.Visible : Visibility.Collapsed;
            return;
        }

        var anim = new DoubleAnimation(open ? NavWidth : 0, TimeSpan.FromMilliseconds(180))
        {
            EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut },
            FillBehavior = FillBehavior.Stop,
        };
        if (open) NavRail.Visibility = Visibility.Visible;
        anim.Completed += (_, _) =>
        {
            // FillBehavior.Stop hands the property back to its local value: pin it to the end state.
            if (_navOpen != open) return;
            NavRail.Width = open ? NavWidth : 0;
            NavRail.Visibility = open ? Visibility.Visible : Visibility.Collapsed;
        };
        NavRail.BeginAnimation(WidthProperty, anim);
    }

    private void OnNavEdit(object sender, RoutedEventArgs e) => SetMode(Mode.Edit);
    private void OnNavMerge(object sender, RoutedEventArgs e) => SetMode(Mode.Merge);
    private void OnNavWord(object sender, RoutedEventArgs e) => SetMode(Mode.Word);

    private void SetMode(Mode mode)
    {
        if (_busy && mode != _mode) return;
        _mode = mode;
        bool edit = mode == Mode.Edit;
        EditView.Visibility = edit ? Visibility.Visible : Visibility.Collapsed;
        MergeView.Visibility = mode == Mode.Merge ? Visibility.Visible : Visibility.Collapsed;
        WordView.Visibility = mode == Mode.Word ? Visibility.Visible : Visibility.Collapsed;
        EditToolbar.Visibility = edit ? Visibility.Visible : Visibility.Collapsed;
        EditRight.Visibility = edit ? Visibility.Visible : Visibility.Collapsed;
        NavEdit.Tag = edit ? "active" : null;
        NavMerge.Tag = mode == Mode.Merge ? "active" : null;
        NavWord.Tag = mode == Mode.Word ? "active" : null;
        SubTitle.Text = mode switch { Mode.Merge => "PDF 合併", Mode.Word => "PDF 轉 Word", _ => "掃描 PDF 文字編輯" };
        if (mode == Mode.Merge) SetStatus(_mergeFiles.Count == 0 ? "把要合併的 PDF 拖進來，或按「加入 PDF」。" : $"清單共 {_mergeFiles.Count} 份 PDF。");
        if (mode == Mode.Word)
        {
            UpdateWordView();
            SetStatus(_path == null ? "先開啟要轉換的 PDF。" : "選擇頁面範圍後按「轉成 Word」。");
        }
    }

    // ================================================================ merge list
    private void OnMergeFilesChanged(object? sender, NotifyCollectionChangedEventArgs e) => UpdateMergeSummary();

    private void UpdateMergeSummary()
    {
        for (int i = 0; i < _mergeFiles.Count; i++) _mergeFiles[i].Order = i + 1;
        int pages = _mergeFiles.Sum(x => x.Pages);
        MergeSummary.Text = _mergeFiles.Count == 0 ? "尚未加入檔案" : $"{_mergeFiles.Count} 份 PDF · 共 {pages} 頁";
        MergeEmptyHint.Visibility = _mergeFiles.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        MergeButton.IsEnabled = _mergeFiles.Count >= 2 && !_busy;
    }

    private void OnMergeAdd(object sender, RoutedEventArgs e) => PickMergeFiles();

    private void PickMergeFiles()
    {
        if (_busy) return;
        var dlg = new OpenFileDialog { Multiselect = true, Title = "選擇要合併的 PDF", Filter = "PDF 檔案 (*.pdf)|*.pdf" };
        if (dlg.ShowDialog(this) == true) AddMergeFiles(dlg.FileNames);
    }

    private void AddMergeFiles(IEnumerable<string> paths)
    {
        if (_busy) return;
        int added = 0, skipped = 0;
        var bad = new List<string>();
        foreach (var path in paths.Where(File.Exists))
        {
            if (!path.EndsWith(".pdf", StringComparison.OrdinalIgnoreCase)) { skipped++; continue; }
            if (_mergeFiles.Any(x => string.Equals(x.FullPath, path, StringComparison.OrdinalIgnoreCase))) continue;
            try
            {
                _mergeFiles.Add(new MergeItem(path, PdfMergeService.CountPages(path)));
                added++;
            }
            catch (Exception)
            {
                bad.Add(Path.GetFileName(path));
            }
        }
        var msg = $"已加入 {added} 份，清單共 {_mergeFiles.Count} 份。";
        if (skipped > 0) msg += $" 略過 {skipped} 個非 PDF 檔。";
        if (bad.Count > 0) msg += $" 無法讀取（損毀或有密碼保護）：{string.Join("、", bad)}。";
        SetStatus(msg);
    }

    private void OnMergeAction(object sender, RoutedEventArgs e)
    {
        if (_busy || sender is not Button { Tag: string action }) return;
        int i = MergeList.SelectedIndex;
        switch (action)
        {
            case "clear": _mergeFiles.Clear(); break;
            case "remove" when i >= 0:
                _mergeFiles.RemoveAt(i);
                if (_mergeFiles.Count > 0) MergeList.SelectedIndex = Math.Min(i, _mergeFiles.Count - 1);
                break;
            case "up" when i > 0:
                _mergeFiles.Move(i, i - 1); MergeList.SelectedIndex = i - 1; break;
            case "down" when i >= 0 && i < _mergeFiles.Count - 1:
                _mergeFiles.Move(i, i + 1); MergeList.SelectedIndex = i + 1; break;
        }
    }

    // ---- drag to reorder (internal) and drop files from Explorer (external)
    private static T? FindParent<T>(DependencyObject? child) where T : DependencyObject
    {
        while (child != null)
        {
            if (child is T t) return t;
            child = VisualTreeHelper.GetParent(child);
        }
        return null;
    }

    private void OnMergeListMouseDown(object sender, MouseButtonEventArgs e)
    {
        _dragStart = e.GetPosition(MergeList);
        _dragItem = FindParent<ListViewItem>(e.OriginalSource as DependencyObject)?.DataContext as MergeItem;
    }

    private void OnMergeListMouseMove(object sender, MouseEventArgs e)
    {
        if (_busy || _dragItem == null || e.LeftButton != MouseButtonState.Pressed) return;
        var p = e.GetPosition(MergeList);
        if (Math.Abs(p.X - _dragStart.X) < SystemParameters.MinimumHorizontalDragDistance &&
            Math.Abs(p.Y - _dragStart.Y) < SystemParameters.MinimumVerticalDragDistance) return;
        var item = _dragItem;
        _dragItem = null;
        DragDrop.DoDragDrop(MergeList, item, DragDropEffects.Move);
    }

    private void OnMergeListDragOver(object sender, DragEventArgs e)
    {
        if (_busy) e.Effects = DragDropEffects.None;
        else if (e.Data.GetDataPresent(typeof(MergeItem))) e.Effects = DragDropEffects.Move;
        else if (e.Data.GetDataPresent(DataFormats.FileDrop)) e.Effects = DragDropEffects.Copy;
        else e.Effects = DragDropEffects.None;
        e.Handled = true;
    }

    private void OnMergeListDrop(object sender, DragEventArgs e)
    {
        e.Handled = true;
        if (_busy) return;
        if (e.Data.GetData(DataFormats.FileDrop) is string[] files) { AddMergeFiles(files); return; }
        if (e.Data.GetData(typeof(MergeItem)) is not MergeItem source) return;

        int from = _mergeFiles.IndexOf(source);
        if (from < 0) return;
        var row = FindParent<ListViewItem>(e.OriginalSource as DependencyObject);
        int to = row?.DataContext is MergeItem target ? _mergeFiles.IndexOf(target) : _mergeFiles.Count;
        if (row != null && e.GetPosition(row).Y > row.ActualHeight / 2) to++;
        if (from < to) to--;
        to = Math.Clamp(to, 0, _mergeFiles.Count - 1);
        if (from != to) _mergeFiles.Move(from, to);
        MergeList.SelectedItem = source;
        SetStatus($"已調整順序：{source.Name} 排在第 {to + 1} 位。");
    }

    // ================================================================ merge & save
    private async void OnMerge(object sender, RoutedEventArgs e)
    {
        if (_busy) return;
        if (_mergeFiles.Count < 2) { SetStatus("PDF 合併至少需要 2 份檔案。"); return; }
        var dlg = new SaveFileDialog
        {
            Title = "儲存合併後的 PDF",
            Filter = "PDF 檔案 (*.pdf)|*.pdf",
            FileName = $"合併文件_{DateTime.Now:yyyyMMdd_HHmm}.pdf",
            AddExtension = true,
            DefaultExt = ".pdf",
            InitialDirectory = Path.GetDirectoryName(_mergeFiles[0].FullPath),
        };
        if (dlg.ShowDialog(this) != true) return;

        var inputs = _mergeFiles.Select(x => x.FullPath).ToList();
        int pages = _mergeFiles.Sum(x => x.Pages);
        var output = dlg.FileName;
        bool done = false;
        IProgress<string> progress = new System.Progress<string>(SetStatus);

        await Run("正在合併 PDF…", async ct =>
        {
            try
            {
                await _mergeService.MergeAsync(inputs, output, progress, ct);
                done = true;
            }
            catch (OperationCanceledException) { SetStatus("已取消合併。"); }
            catch (Exception ex)
            {
                SetStatus("合併失敗。");
                MessageBox.Show(this, ex.Message, "PdfTailor", MessageBoxButton.OK, MessageBoxImage.Warning);
            }
        }, showBeam: false);

        UpdateMergeSummary();
        if (!done) return;
        SetStatus($"PDF 合併完成：{Path.GetFileName(output)}");
        new DoneDialog(output, $"已合併 {inputs.Count} 份 PDF，共 {pages} 頁。") { Owner = this }.ShowDialog();
    }

    // ================================================================ shark cursor toggle
    private void OnSharkToggle(object sender, RoutedEventArgs e)
    {
        if (SharkCursor.IsAttached) SharkCursor.Detach();   // restores the normal Windows cursor
        else SharkCursor.Attach(this);
        UpdateSharkToggle();
    }

    private void UpdateSharkToggle()
    {
        bool on = SharkCursor.IsAttached;
        SharkToggle.Content = on ? "🦈  恢復一般游標" : "🦈  鯊魚游標";
        SharkToggle.ToolTip = on ? "按下後恢復成正常滑鼠游標" : "重新啟用鯊魚游標";
        SharkToggle.Foreground = new SolidColorBrush(on ? Color.FromRgb(0x66, 0xE5, 0xFF) : Color.FromRgb(0x7A, 0x8C, 0xA8));
    }
}
