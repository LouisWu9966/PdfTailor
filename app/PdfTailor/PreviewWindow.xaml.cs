using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace PdfTailor;

/// <summary>Shows the pages exactly as the engine will write them (the preview is a real
/// export to a temp file, rendered) next to the originals. Export happens only after "確定輸出".</summary>
public partial class PreviewWindow : Window
{
    private readonly List<PreviewPage> _pages;
    private string _mode = "side";

    public PreviewWindow(List<PreviewPage> pages, List<string> notes, int modified)
    {
        InitializeComponent();
        _pages = pages;
        Summary.Text = $"共修改 {modified} 行，影響 {pages.Count} 頁。未修改的部分與原檔完全相同。";
        NotesText.Text = notes.Count > 0 ? string.Join("\n", notes) : "";
        Build();
    }

    private void Build()
    {
        PagesPanel.Children.Clear();
        foreach (var p in _pages)
        {
            PagesPanel.Children.Add(new TextBlock { Text = $"第 {p.Page} 頁", FontSize = 14, FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 6, 0, 8) });
            var row = new Grid { Margin = new Thickness(0, 0, 0, 26) };
            if (_mode == "side")
            {
                row.ColumnDefinitions.Add(new ColumnDefinition());
                row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(18) });
                row.ColumnDefinitions.Add(new ColumnDefinition());
                var a = Card("原始", p.OriginalPng, false);
                var b = Card("修改後", p.EditedPng, true);
                Grid.SetColumn(b, 2);
                row.Children.Add(a);
                row.Children.Add(b);
            }
            else
            {
                row.Children.Add(Card(_mode == "after" ? "修改後" : "原始", _mode == "after" ? p.EditedPng : p.OriginalPng, _mode == "after"));
            }
            PagesPanel.Children.Add(row);
        }
    }

    private static FrameworkElement Card(string label, string png, bool accent)
    {
        var sp = new StackPanel();
        sp.Children.Add(new TextBlock
        {
            Text = label, FontSize = 12, Margin = new Thickness(0, 0, 0, 6),
            Foreground = new SolidColorBrush(accent ? Color.FromRgb(0x28, 0xD7, 0xFF) : Color.FromRgb(0x91, 0xA0, 0xBE)),
        });
        var bmp = new BitmapImage();
        bmp.BeginInit();
        bmp.CacheOption = BitmapCacheOption.OnLoad;
        bmp.CreateOptions = BitmapCreateOptions.IgnoreImageCache;
        bmp.UriSource = new Uri(png);
        bmp.EndInit();
        bmp.Freeze();
        var border = new Border
        {
            BorderBrush = new SolidColorBrush(accent ? Color.FromRgb(0x28, 0xD7, 0xFF) : Color.FromRgb(0x25, 0x34, 0x4E)),
            BorderThickness = new Thickness(accent ? 2 : 1),
            Background = Brushes.White,
            Child = new Image { Source = bmp, Stretch = Stretch.Uniform, StretchDirection = StretchDirection.DownOnly },
        };
        RenderOptions.SetBitmapScalingMode(border.Child, BitmapScalingMode.HighQuality);
        sp.Children.Add(border);
        return sp;
    }

    private void OnModeChanged(object sender, SelectionChangedEventArgs e)
    {
        if (sender is ComboBox box && box.SelectedItem is ComboBoxItem i && i.Tag is string t)
        {
            _mode = t;
            if (_pages != null) Build();
        }
    }

    private void OnConfirm(object sender, RoutedEventArgs e) => DialogResult = true;
    private void OnBack(object sender, RoutedEventArgs e) => DialogResult = false;

    private void OnKey(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Escape) { DialogResult = false; e.Handled = true; }
    }
}
