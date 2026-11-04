using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Animation;
using Shapes = System.Windows.Shapes;

namespace PdfTailor.Controls;

/// <summary>
/// The empty-state animation: a scanned page, a beam sweeping down it, and each line of
/// "ink" turning into an editable text box as the beam passes, one of them then retyped.
/// Static when Windows animations are off (Settings &gt; Accessibility &gt; Visual effects).
/// Pauses while hidden so it costs nothing behind a loaded document.
/// </summary>
public sealed class ScanDemo : Canvas
{
    private const double PageW = 230, PageH = 300, Cycle = 4.2, Sweep = 2.6;
    private static readonly Color Cyan = Color.FromRgb(0x28, 0xD7, 0xFF);
    private readonly Storyboard _story = new() { RepeatBehavior = RepeatBehavior.Forever };
    private bool _built;

    public ScanDemo()
    {
        Width = PageW + 40;
        Height = PageH + 30;
        IsHitTestVisible = false;
        Loaded += (_, _) => Build();
        IsVisibleChanged += (_, e) =>
        {
            if (!_built) return;
            if ((bool)e.NewValue) _story.Resume(this); else _story.Pause(this);
        };
    }

    private void Build()
    {
        if (_built) return;
        _built = true;
        bool animate = SystemParameters.ClientAreaAnimation;

        // page with a little shadow
        var shadow = new Shapes.Rectangle { Width = PageW, Height = PageH, RadiusX = 10, RadiusY = 10, Fill = new SolidColorBrush(Color.FromArgb(70, 0, 0, 0)) };
        SetLeft(shadow, 26); SetTop(shadow, 14);
        Children.Add(shadow);
        var page = new Shapes.Rectangle
        {
            Width = PageW, Height = PageH, RadiusX = 10, RadiusY = 10,
            Fill = new SolidColorBrush(Color.FromRgb(0xF4, 0xF3, 0xEE)),
        };
        SetLeft(page, 20); SetTop(page, 8);
        Children.Add(page);

        // a "chart" that the scan must leave alone
        double cx = 40, cy = 205;
        var frame = new Shapes.Rectangle { Width = 190, Height = 78, Stroke = new SolidColorBrush(Color.FromRgb(0x9A, 0xA6, 0xB8)), StrokeThickness = 1.2 };
        SetLeft(frame, cx); SetTop(frame, cy);
        Children.Add(frame);
        var bars = new[] { (28.0, 0x28D7FFu), (48.0, 0x8B5CF6u), (20.0, 0x65E6ADu), (38.0, 0xE8892Cu) };
        for (int i = 0; i < bars.Length; i++)
        {
            var (h, rgb) = bars[i];
            var bar = new Shapes.Rectangle { Width = 26, Height = h, Fill = new SolidColorBrush(Color.FromRgb((byte)(rgb >> 16), (byte)(rgb >> 8), (byte)rgb)) };
            SetLeft(bar, cx + 16 + i * 44); SetTop(bar, cy + 72 - h);
            Children.Add(bar);
        }

        // text lines: ink bars, their recognised box, and (for one) the retyped text
        var lines = new[] { (40.0, 30.0, 120.0, 13.0), (40.0, 62.0, 170.0, 7.0), (40.0, 80.0, 150.0, 7.0), (40.0, 98.0, 165.0, 7.0), (40.0, 128.0, 90.0, 7.0), (40.0, 146.0, 140.0, 7.0), (40.0, 172.0, 110.0, 7.0) };
        for (int i = 0; i < lines.Length; i++)
        {
            var (x, y, w, h) = lines[i];
            var ink = new Shapes.Rectangle { Width = w, Height = h, RadiusX = 2, RadiusY = 2, Fill = new SolidColorBrush(Color.FromRgb(0x3A, 0x40, 0x4C)) };
            SetLeft(ink, x); SetTop(ink, y);
            Children.Add(ink);
            var box = new Shapes.Rectangle
            {
                Width = w + 8, Height = h + 8, RadiusX = 4, RadiusY = 4,
                Stroke = new SolidColorBrush(Cyan), StrokeThickness = 1.4,
                Fill = new SolidColorBrush(Color.FromArgb(40, Cyan.R, Cyan.G, Cyan.B)),
                Opacity = animate ? 0 : 1,
            };
            SetLeft(box, x - 4); SetTop(box, y - 4);
            Children.Add(box);
            if (!animate) continue;

            double pass = Sweep * (y - 8) / PageH;
            var a = new DoubleAnimationUsingKeyFrames();
            a.KeyFrames.Add(new DiscreteDoubleKeyFrame(0, KeyTime.FromTimeSpan(TimeSpan.Zero)));
            a.KeyFrames.Add(new DiscreteDoubleKeyFrame(0, KeyTime.FromTimeSpan(TimeSpan.FromSeconds(pass))));
            a.KeyFrames.Add(new EasingDoubleKeyFrame(1, KeyTime.FromTimeSpan(TimeSpan.FromSeconds(pass + 0.18))));
            a.KeyFrames.Add(new LinearDoubleKeyFrame(1, KeyTime.FromTimeSpan(TimeSpan.FromSeconds(Cycle - 0.35))));
            a.KeyFrames.Add(new LinearDoubleKeyFrame(0, KeyTime.FromTimeSpan(TimeSpan.FromSeconds(Cycle))));
            Storyboard.SetTarget(a, box);
            Storyboard.SetTargetProperty(a, new PropertyPath(OpacityProperty));
            _story.Children.Add(a);

            if (i == 0)
            {
                // the title gets "edited": its ink fades and a new, longer bar types in
                var fade = new DoubleAnimationUsingKeyFrames();
                fade.KeyFrames.Add(new DiscreteDoubleKeyFrame(1, KeyTime.FromTimeSpan(TimeSpan.Zero)));
                fade.KeyFrames.Add(new LinearDoubleKeyFrame(1, KeyTime.FromTimeSpan(TimeSpan.FromSeconds(Sweep + 0.2))));
                fade.KeyFrames.Add(new LinearDoubleKeyFrame(0, KeyTime.FromTimeSpan(TimeSpan.FromSeconds(Sweep + 0.45))));
                fade.KeyFrames.Add(new LinearDoubleKeyFrame(0, KeyTime.FromTimeSpan(TimeSpan.FromSeconds(Cycle - 0.35))));
                fade.KeyFrames.Add(new LinearDoubleKeyFrame(1, KeyTime.FromTimeSpan(TimeSpan.FromSeconds(Cycle))));
                Storyboard.SetTarget(fade, ink);
                Storyboard.SetTargetProperty(fade, new PropertyPath(OpacityProperty));
                _story.Children.Add(fade);

                var typed = new Shapes.Rectangle { Width = 0, Height = h, RadiusX = 2, RadiusY = 2, Fill = new SolidColorBrush(Color.FromRgb(0x0E, 0x5A, 0x8A)) };
                SetLeft(typed, x); SetTop(typed, y);
                Children.Add(typed);
                var grow = new DoubleAnimationUsingKeyFrames();
                grow.KeyFrames.Add(new DiscreteDoubleKeyFrame(0, KeyTime.FromTimeSpan(TimeSpan.Zero)));
                grow.KeyFrames.Add(new LinearDoubleKeyFrame(0, KeyTime.FromTimeSpan(TimeSpan.FromSeconds(Sweep + 0.4))));
                grow.KeyFrames.Add(new LinearDoubleKeyFrame(w + 26, KeyTime.FromTimeSpan(TimeSpan.FromSeconds(Sweep + 1.1))));
                grow.KeyFrames.Add(new LinearDoubleKeyFrame(w + 26, KeyTime.FromTimeSpan(TimeSpan.FromSeconds(Cycle - 0.35))));
                grow.KeyFrames.Add(new LinearDoubleKeyFrame(0, KeyTime.FromTimeSpan(TimeSpan.FromSeconds(Cycle))));
                Storyboard.SetTarget(grow, typed);
                Storyboard.SetTargetProperty(grow, new PropertyPath(WidthProperty));
                _story.Children.Add(grow);
            }
        }

        if (!animate) return;

        // the scanning beam: a soft cyan band with a bright edge
        var beam = new Shapes.Rectangle
        {
            Width = PageW, Height = 26,
            Fill = new LinearGradientBrush(
                new GradientStopCollection
                {
                    new GradientStop(Color.FromArgb(0, Cyan.R, Cyan.G, Cyan.B), 0),
                    new GradientStop(Color.FromArgb(70, Cyan.R, Cyan.G, Cyan.B), 0.85),
                    new GradientStop(Color.FromArgb(230, Cyan.R, Cyan.G, Cyan.B), 1),
                },
                new Point(0, 0), new Point(0, 1)),
            Opacity = 0,
        };
        SetLeft(beam, 20);
        Children.Add(beam);
        var move = new DoubleAnimation(8 - 26, 8 + PageH - 26, TimeSpan.FromSeconds(Sweep)) { EasingFunction = new SineEase { EasingMode = EasingMode.EaseInOut } };
        Storyboard.SetTarget(move, beam);
        Storyboard.SetTargetProperty(move, new PropertyPath(TopProperty));
        _story.Children.Add(move);
        var show = new DoubleAnimationUsingKeyFrames();
        show.KeyFrames.Add(new LinearDoubleKeyFrame(1, KeyTime.FromTimeSpan(TimeSpan.FromSeconds(0.15))));
        show.KeyFrames.Add(new LinearDoubleKeyFrame(1, KeyTime.FromTimeSpan(TimeSpan.FromSeconds(Sweep - 0.2))));
        show.KeyFrames.Add(new LinearDoubleKeyFrame(0, KeyTime.FromTimeSpan(TimeSpan.FromSeconds(Sweep))));
        Storyboard.SetTarget(show, beam);
        Storyboard.SetTargetProperty(show, new PropertyPath(OpacityProperty));
        _story.Children.Add(show);

        _story.Duration = new Duration(TimeSpan.FromSeconds(Cycle));
        _story.Begin(this, true);
        if (!IsVisible) _story.Pause(this);
    }
}
