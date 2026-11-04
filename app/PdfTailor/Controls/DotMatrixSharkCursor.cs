#nullable enable
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace PdfTailor.Controls;

/// <summary>
/// 點陣鯊魚游標（ascii-sharks 風格的 WPF 移植）。
///
/// 與舊版 <c>SharkCursorOverlay</c> 的差別：
/// 1. 造型不再手繪，直接移植 ascii-sharks renderer.ts 的程序化剪影
///    （halfWidth 函式 + 胸鰭／腹鰭／尾鰭三角形 + 雙髻鯊頭翼），
///    每格 5 點超取樣做邊緣抗鋸齒，跟原版看起來一模一樣。
/// 2. 不旋轉整張圖：字元格固定在螢幕上，鯊魚「游過」點陣，字形永遠正立。
/// 3. 身體是跟隨式脊骨（20 節，限制每個關節彎曲角），吻端 = 游標熱點，
///    身體沿著滑鼠走過的路徑彎曲，轉彎自然、不會折疊。
/// 4. 每格 = 灰階色塊 + 深色字元 + 偶爾閃一下品牌色（原版的 shimmer）。
/// 5. 點擊簽名：前衝咬一口 → 點陣水波環 → 紅色 * 熱點 →
///    Label 逐字噴出成彩色字塊 → 停留後化成 '-' 小魚游走。
///
/// 造型、配色、字元集取自 ascii-sharks（MIT, stackedlol/ascii-sharks）。
/// </summary>
public sealed class DotMatrixSharkCursor : FrameworkElement
{
    // ── 可調參數 ────────────────────────────────────────────────────
    /// <summary>點擊時噴出的簽名字樣。</summary>
    public string Label { get; set; } = "LouisTool";
    /// <summary>"hammerhead"（雙髻鯊）或 "classic"（大白鯊）。</summary>
    public string Kind { get; set; } = "hammerhead";
    /// <summary>鯊魚全長（DIP）。建議 180~260。</summary>
    public double Length { get; set; } = 220;
    /// <summary>字元格寬高（DIP）。5×8 在 150% 縮放下約 7.5×12 實體像素。</summary>
    public double CellWidth { get; set; } = 5;
    public double CellHeight { get; set; } = 8;
    /// <summary>游過的地方點亮青色小點。</summary>
    public bool ShowWake { get; set; } = true;
    /// <summary>每個關節最大彎曲（弧度）；越小身體越硬。</summary>
    public double MaxBend { get; set; } = 0.14;
    /// <summary>滑鼠靜止多久後凍結畫面、完全停止重畫（秒）。凍結後 CPU ≈ 0。</summary>
    public double IdleFreezeSeconds { get; set; } = 2.5;
    /// <summary>靜止但尚未凍結時的幀率上限（身體慢慢拉直的那段）。</summary>
    public double IdleFps { get; set; } = 30;

    // ── 原版常數 ────────────────────────────────────────────────────
    private const int N = 20;
    private static readonly Color[] Accents =
    {
        C("#e0892c"), C("#e03a32"), C("#b44ae0"), C("#33c8cc"), C("#3cb54a"), C("#3150e8"), C("#d9c14a"),
    };
    private const string SkinRamp = ":;t%SX8#@", FinGlyphs = "%;tS:", RidgeGlyphs = "@8%";
    private const string EdgeHeavy = ":;", EdgeLight = ".',`";
    private const int FBody = 1, FEdge = 2, FFin = 3, FRidge = 4, FEye = 5;
    private static readonly double[] Sub = { 0.25, 0.3, 0.75, 0.3, 0.25, 0.72, 0.75, 0.72 };
    private static readonly Color Wake = Color.FromRgb(110, 230, 255);
    private static readonly Color FishColor = Color.FromRgb(190, 212, 255);
    private static readonly Color Ink = C("#0e0f13"), Ink2 = C("#1b1d24"), EyeColor = C("#9aa0ad");

    private sealed class Shape
    {
        public required Func<double, double> HalfWidth;
        public required double[] Pectoral, Pelvic, Dorsal, SecondDorsal, Gills, Tint;
        public double[]? Eye;
        public double Caudal;
        public bool Falcate = true;
        public double[]? Head; // span, sweep, depth, notch
    }

    private static double ClassicHalfWidth(double s)
    {
        if (s < 0.13) return 0.09 * Math.Pow(Math.Sin(s / 0.13 * Math.PI / 2), 0.95);
        if (s < 0.3) return 0.09 + 0.016 * Smooth(0.13, 0.3, s);
        if (s < 0.84) return 0.019 + 0.087 * Math.Pow(1 - (s - 0.3) / 0.54, 1.25);
        double k = (s - 0.84) / 0.16;
        return 0.019 * (1 - k) + 0.012 * Math.Sin(k * Math.PI);
    }

    private static readonly Shape Classic = new()
    {
        HalfWidth = ClassicHalfWidth,
        Pectoral = new[] { 0.15, 0.43, 0.55, 0.26 }, Pelvic = new[] { 0.57, 0.65, 0.69, 0.085 },
        Caudal = 0.085, Dorsal = new[] { 0.33, 0.47 }, SecondDorsal = new[] { 0.71, 0.76 },
        Eye = new[] { 0.1, 0.14 }, Gills = new[] { 0.19, 0.29 }, Tint = new[] { 0.92, 0.97, 1.05 },
    };

    private static readonly Shape Hammerhead = new()
    {
        HalfWidth = s => s < 0.06 ? 0.045 : s < 0.3 ? 0.045 + 0.061 * Smooth(0.06, 0.3, s) : ClassicHalfWidth(s) * 0.92,
        Head = new[] { 0.17, 0.035, 0.09, 0.0 },
        Pectoral = new[] { 0.17, 0.4, 0.5, 0.22 }, Pelvic = new[] { 0.56, 0.63, 0.67, 0.075 },
        Caudal = 0.11, Dorsal = new[] { 0.28, 0.44 }, SecondDorsal = new[] { 0.7, 0.74 },
        Eye = null, Gills = new[] { 0.16, 0.26 }, Tint = new[] { 0.96, 0.98, 0.9 },
    };

    // ── 狀態 ────────────────────────────────────────────────────────
    private readonly double[] _nodes = new double[N * 2], _spine = new double[N * 2], _arc = new double[N];
    private double _arcTotal = 1, _phase, _speed, _turn, _heading = Math.PI, _lunge, _lungeT = -1;
    private double _finR, _finL;
    private Shape _shape = Hammerhead;
    private Point _mouse;
    private bool _ready, _visible = true, _attached;
    private Window? _window;
    private readonly Stopwatch _clock = Stopwatch.StartNew();
    private double _last, _t;

    private struct Cell { public int C, R, Flag; public uint Key; public double Shade; }
    private readonly List<Cell> _cells = new(1024);
    private readonly Dictionary<long, double> _wake = new();
    private readonly List<long> _wakeDead = new();

    private sealed class Fx
    {
        public int Type; // 0 ring, 1 heat, 2 glyph
        public double X, Y, T, Life, R1;
        public char Ch; public int I;
        public double Sx, Sy, Vx, Vy, Tx, Ty, Fly, Hold, Swim, Ang;
    }
    private readonly List<Fx> _fx = new();

    private Typeface? _face, _faceBold;
    private double _ppd = 1;

    // ── 效能記錄：設環境變數 SHARK_PERF=1 才啟用，每 2 秒寫一行到 exe 旁的 shark_perf.log ──
    private readonly bool _perf = Environment.GetEnvironmentVariable("SHARK_PERF") == "1";
    private long _pfTicks, _pfRenders, _pfStepTicks, _pfRenderTicks;
    private double _pfLast;

    private void PerfFlush(double now)
    {
        if (!_perf) return;
        if (_pfLast == 0) { _pfLast = now; return; }
        double span = now - _pfLast;
        if (span < 2) return;
        double ms(long t) => t * 1000.0 / Stopwatch.Frequency;
        string line = string.Format(CultureInfo.InvariantCulture,
            "{0:HH:mm:ss} ticks/s={1:F0} renders/s={2:F0} step={3:F2}ms render={4:F2}ms cells={5} fx={6} wake={7} visible={8} speed={9:F0}\n",
            DateTime.Now, _pfTicks / span, _pfRenders / span,
            _pfTicks > 0 ? ms(_pfStepTicks) / _pfTicks : 0, _pfRenders > 0 ? ms(_pfRenderTicks) / _pfRenders : 0,
            _cells.Count, _fx.Count, _wake.Count, _visible, _speed);
        try { System.IO.File.AppendAllText(System.IO.Path.Combine(AppContext.BaseDirectory, "shark_perf.log"), line); } catch { }
        _pfTicks = _pfRenders = _pfStepTicks = _pfRenderTicks = 0;
        _pfLast = now;
    }

    public DotMatrixSharkCursor()
    {
        IsHitTestVisible = false;
        Panel.SetZIndex(this, 9999);
    }

    // ── 掛載 ────────────────────────────────────────────────────────
    public void Attach(Window window)
    {
        if (_attached) return;
        // 使用者在 Windows 關閉「動畫效果」（減少動態效果）時，保留系統游標，不啟用鯊魚
        if (!SystemParameters.ClientAreaAnimation) return;
        _window = window;
        _ppd = VisualTreeHelper.GetDpi(this).PixelsPerDip;
        var family = new FontFamily("Consolas, Cascadia Mono, Courier New");
        _face = new Typeface(family, FontStyles.Normal, FontWeights.SemiBold, FontStretches.Normal);
        _faceBold = new Typeface(family, FontStyles.Normal, FontWeights.Bold, FontStretches.Normal);

        window.PreviewMouseMove += OnMouseMove;
        window.PreviewMouseDown += OnMouseDown;
        window.MouseLeave += OnMouseLeave;
        window.Closed += OnClosed;
        // Mouse.OverrideCursor 是整個應用程式共用的：只在這個視窗是前景時隱藏系統游標，
        // 開啟說明視窗、對話框（ShowDialog）時要還原，否則其他視窗會完全看不到游標
        window.Activated += OnWindowActivated;
        window.Deactivated += OnWindowDeactivated;
        if (window.IsActive) Mouse.OverrideCursor = Cursors.None;

        _attached = true;   // 不先訂閱 Rendering：等滑鼠進來才開始畫（Start）
    }

    // 只在「有東西在動」時訂閱 CompositionTarget.Rendering。
    // 只要有人訂閱，WPF 就會每秒 60 次重畫整個視窗；沒事時一定要退訂。
    private bool _running;
    private double _lastMove, _lastFrame;

    private void Start()
    {
        if (_running || !_attached) return;
        _last = _clock.Elapsed.TotalSeconds;
        _lastFrame = 0;
        CompositionTarget.Rendering += OnRendering;
        _running = true;
    }

    private void Stop()
    {
        if (!_running) return;
        CompositionTarget.Rendering -= OnRendering;
        _running = false;
    }

    /// <summary>True while the shark is replacing the system cursor.</summary>
    public bool IsAttached => _attached;

    public void Detach()
    {
        if (!_attached) return;
        Stop();
        // wipe the last drawn frame, otherwise the shark stays frozen on screen after detaching
        _visible = false; _fx.Clear(); _wake.Clear();
        Compose();
        if (_window != null)
        {
            _window.PreviewMouseMove -= OnMouseMove;
            _window.PreviewMouseDown -= OnMouseDown;
            _window.MouseLeave -= OnMouseLeave;
            _window.Closed -= OnClosed;
            _window.Activated -= OnWindowActivated;
            _window.Deactivated -= OnWindowDeactivated;
        }
        Mouse.OverrideCursor = null;
        _attached = false;
    }

    private void OnClosed(object? s, EventArgs e) => Detach();
    private void OnWindowActivated(object? s, EventArgs e) => Mouse.OverrideCursor = Cursors.None;
    private void OnWindowDeactivated(object? s, EventArgs e)
    {
        Mouse.OverrideCursor = null;
        _visible = false; InvalidateVisual(); Start();   // 鯊魚淡出（清掉最後一幀）後停止重畫
    }
    private void OnMouseLeave(object s, MouseEventArgs e) { _visible = false; InvalidateVisual(); Start(); }
    private void OnMouseMove(object s, MouseEventArgs e) => MoveTo(e.GetPosition(this));
    private void OnMouseDown(object s, MouseButtonEventArgs e)
    {
        if (e.ChangedButton != MouseButton.Left) return;
        var p = e.GetPosition(this);
        Burst(p.X, p.Y);
    }

    /// <summary>移動游標位置（測試載具也可直接呼叫）。</summary>
    public void MoveTo(Point p)
    {
        _mouse = p;
        if (!_ready || !_visible) ResetBody(p);
        _visible = true;
        _lastMove = _clock.Elapsed.TotalSeconds;
        Start();
    }

    private void ResetBody(Point p)
    {
        double seg = Length / (N - 1);
        for (int k = 0; k < N; k++) { _nodes[k * 2] = p.X + seg * k; _nodes[k * 2 + 1] = p.Y; }
        _heading = Math.PI;
        _ready = true;
    }

    // ── 每幀 ────────────────────────────────────────────────────────
    private void OnRendering(object? sender, EventArgs e)
    {
        double now = _clock.Elapsed.TotalSeconds;
        double idle = now - _lastMove;
        bool busy = _fx.Count > 0 || _wake.Count > 0 || _lungeT >= 0 || _spinT >= 0;
        // 沒有特效、且（游標不在視窗內，或已靜止夠久）→ 停止重畫，最後一幀留在畫面上
        if (!busy && (!_visible || !_ready || idle > IdleFreezeSeconds)) { Compose(); PerfFlush(now); Stop(); return; }
        // 靜止中：降到 IdleFps，只做身體拉直
        if (!busy && idle > 0.15 && now - _lastFrame < 1.0 / IdleFps) return;
        _lastFrame = now;
        double dt = Math.Min(0.05, now - _last);
        _last = now; _t = now;
        if (dt <= 0) return;
        long pf0 = Stopwatch.GetTimestamp();
        if (_ready && _visible)
        {
            if (_lungeT >= 0)
            {
                _lungeT += dt;
                double p = _lungeT / 0.32;
                _lunge = p < 1 ? Math.Sin(p * Math.PI) * Length * 0.07 : 0;
                if (p >= 1) _lungeT = -1;
            }
            StepBody(dt, idle > 0.2 ? 2.2 : 0);
            Rasterize(now);
            if (ShowWake && _speed > 30) AddWake();
        }
        AgeWake(dt);
        foreach (var f in _fx) f.T += dt;
        _fx.RemoveAll(f => f.Type == 2 ? f.T >= f.Fly + f.Hold + f.Swim : f.T >= f.Life);
        _pfStepTicks += Stopwatch.GetTimestamp() - pf0; _pfTicks++;
        Compose();
        PerfFlush(now);
    }

    // ── 點擊：以吻端為軸心 360° 轉一圈，尾巴落後形成甩尾 ──────────
    private const double SpinDuration = 0.75;
    private double _spinT = -1, _spinDir = 1, _spinTheta0;

    private static double EaseInOut(double p) => p < 0.5 ? 4 * p * p * p : 1 - Math.Pow(-2 * p + 2, 3) / 2;

    private bool StepSpin(double dt)
    {
        _spinT += dt;
        double p = _spinT / SpinDuration;
        if (p >= 1) { _spinT = -1; _heading = _spinTheta0; return false; }
        double seg = Length / (N - 1);
        _nodes[0] = _mouse.X; _nodes[1] = _mouse.Y;
        double th = _spinTheta0 + _spinDir * Math.PI * 2 * EaseInOut(p);
        double curl = 1.9 * Math.Sin(Math.PI * p);            // 轉最快時尾巴落後最多 → 甩尾
        for (int k = 1; k < N; k++)
        {
            double a = th + Math.PI - _spinDir * curl * Math.Pow(k / (double)(N - 1), 1.4);
            _nodes[k * 2] = _nodes[k * 2 - 2] + Math.Cos(a) * seg;
            _nodes[k * 2 + 1] = _nodes[k * 2 - 1] + Math.Sin(a) * seg;
        }
        _heading = th;
        _turn = _spinDir * 12 * Math.Sin(Math.PI * p);
        _phase += dt * 14;                                    // 尾鰭快速拍打
        EmitFromTail(dt, p);
        return true;
    }

    private void StepBody(double dt, double relax)
    {
        if (_spinT >= 0 && StepSpin(dt)) return;
        double L = Length, seg = L / (N - 1);
        double ox = _nodes[0], oy = _nodes[1];
        _nodes[0] = _mouse.X; _nodes[1] = _mouse.Y;
        double moved = Math.Sqrt((_nodes[0] - ox) * (_nodes[0] - ox) + (_nodes[1] - oy) * (_nodes[1] - oy));
        _speed += (moved / dt - _speed) * Math.Min(1, dt * 6);

        double prev = 0;
        for (int k = 1; k < N; k++)
        {
            double px = _nodes[(k - 1) * 2], py = _nodes[(k - 1) * 2 + 1];
            double a = Math.Atan2(_nodes[k * 2 + 1] - py, _nodes[k * 2] - px);
            if (k > 1) a = prev + Clamp(AngleDiff(prev, a), -MaxBend, MaxBend);
            _nodes[k * 2] = px + Math.Cos(a) * seg;
            _nodes[k * 2 + 1] = py + Math.Sin(a) * seg;
            if (k == 1)
            {
                double h = a + Math.PI;
                _turn += (AngleDiff(_heading, h) / dt - _turn) * Math.Min(1, dt * 3);
                _heading = h;
            }
            prev = a;
        }
        // 靜止時把身體慢慢拉直，凍結時才是自然的直身姿勢
        if (relax > 0)
        {
            double hx = Math.Cos(_heading), hy = Math.Sin(_heading), kf = Math.Min(1, dt * relax);
            for (int k = 1; k < N; k++)
            {
                _nodes[k * 2] += (_nodes[0] - hx * seg * k - _nodes[k * 2]) * kf;
                _nodes[k * 2 + 1] += (_nodes[1] - hy * seg * k - _nodes[k * 2 + 1]) * kf;
            }
        }
        _phase += dt * (2.2 + Math.Min(_speed, 900) / 70);
    }

    // ── 光柵化：移植 sampleShark / rasterizeSharks ─────────────────
    private double _hDist, _hS, _hV, _hShade, _hW, _hReach; private int _hFlag;

    private bool Sample(double px, double py, double L)
    {
        double best = double.MaxValue, bestS = 0, sign = 1, nx = 0, ny = 0;
        for (int k = 0; k < N - 1; k++)
        {
            double ax = _spine[k * 2], ay = _spine[k * 2 + 1];
            double sx = _spine[k * 2 + 2] - ax, sy = _spine[k * 2 + 3] - ay;
            double len2 = sx * sx + sy * sy; if (len2 == 0) len2 = 1;
            double u = Clamp(((px - ax) * sx + (py - ay) * sy) / len2, 0, 1);
            double qx = ax + sx * u - px, qy = ay + sy * u - py, d2 = qx * qx + qy * qy;
            if (d2 < best)
            {
                best = d2; double len = Math.Sqrt(len2);
                bestS = (_arc[k] + u * len) / _arcTotal;
                sign = sx * (py - ay) - sy * (px - ax) >= 0 ? 1 : -1;
                nx = -sy / len * sign; ny = sx / len * sign;
            }
        }
        double a = Math.Sqrt(best), s = bestS, uu = s * L, w = _shape.HalfWidth(s) * L;
        _hDist = a; _hS = s; _hV = a * sign; _hW = w;
        var shp = _shape;
        // 這個 s 位置上，外形最遠能伸多寬（身體、胸鰭、頭翼、腹鰭、尾鰭取最大）→ 用來提早略過空白格
        double m = w;
        if (s > shp.Pectoral[0] - 0.02 && s < shp.Pectoral[2] + 0.02) m = Math.Max(m, Math.Max(_finR, _finL));
        if (shp.Head != null && s < shp.Head[1] + shp.Head[2] + shp.Head[3] + 0.02) m = Math.Max(m, L * shp.Head[0]);
        if (s > shp.Pelvic[0] - 0.02 && s < shp.Pelvic[2] + 0.02) m = Math.Max(m, L * shp.Pelvic[3]);
        if (s > 0.84) m = Math.Max(m, L * shp.Caudal);
        _hReach = m;

        if (shp.Head is { } head && uu < L * (head[1] + head[2] + head[3]))
        {
            double span = L * head[0], r = a / span;
            double front = L * head[1] * r * r + L * head[3] * Math.Max(0, 1 - r / 0.14);
            double depth = L * head[2] * (1 - 0.2 * r * r * r * r);
            if (r <= 1 && uu >= front && uu <= front + depth)
            {
                _hFlag = r > 0.84 ? FEye : FBody;
                _hShade = r > 0.84 ? 0 : 0.62 - 0.2 * r + (uu < front + L * 0.012 ? 0.1 : 0);
                return true;
            }
        }

        if (a <= w)
        {
            double across = a / (w == 0 ? 1 : w), facing = (nx * -0.6 + ny * -0.8) * across;
            double shade = 0.58 - across * across * 0.42 + facing * 0.26; int flag = FBody;
            if (s > shp.Dorsal[0] && s < shp.Dorsal[1] && across < 0.3) { flag = FRidge; shade = 1; }
            else if (s > shp.SecondDorsal[0] && s < shp.SecondDorsal[1] && across < 0.4) shade = Math.Max(shade, 0.82);
            else if (shp.Eye != null && s > shp.Eye[0] && s < shp.Eye[1] && across > 0.6) { flag = FEye; shade = 0; }
            else if (s > shp.Gills[0] && s < shp.Gills[1] && across > 0.58)
            {
                if ((int)Math.Floor((s - shp.Gills[0]) / (shp.Gills[1] - shp.Gills[0]) * 5) % 2 == 0) shade *= 0.45;
            }
            else if (s < 0.05) shade = Math.Max(shade, 0.7);
            _hFlag = flag; _hShade = shade; return true;
        }

        double tip = sign > 0 ? _finR : _finL;
        var p = shp.Pectoral;
        if (InTri(uu, a, L * p[0], 0, L * p[1], 0, L * p[2], tip) &&
            !(shp.Falcate && InTri(uu, a, L * p[1], 0, L * (p[1] + (p[2] - p[1]) * 0.33), tip * 0.55, L * p[2], tip)))
        {
            _hFlag = FFin; _hShade = 0.38 + 0.22 * (1 - Clamp(a / tip, 0, 1)); return true;
        }
        var v = shp.Pelvic;
        if (InTri(uu, a, L * v[0], 0, L * v[1], 0, L * v[2], L * v[3]) ||
            (shp.Caudal > 0 && InTri(uu, a, L * 0.86, 0, L * 0.95, 0, L, L * shp.Caudal)))
        {
            _hFlag = FFin; _hShade = 0.36; return true;
        }
        return false;
    }

    private void Rasterize(double t)
    {
        _cells.Clear();
        double L = Length, cw = CellWidth, ch = CellHeight;
        _shape = Kind == "classic" ? Classic : Hammerhead;
        double swim = _spinT >= 0 ? 1.5 : Clamp(_speed / 300, 0.45, 1.0);
        double lx = Math.Cos(_heading) * _lunge, ly = Math.Sin(_heading) * _lunge;

        for (int k = 0; k < N; k++)
        {
            double s = k / (double)(N - 1);
            int a = Math.Max(0, k - 1), b = Math.Min(N - 1, k + 1);
            double tx = _nodes[a * 2] - _nodes[b * 2], ty = _nodes[a * 2 + 1] - _nodes[b * 2 + 1];
            double tl = Math.Sqrt(tx * tx + ty * ty); if (tl == 0) tl = 1; tx /= tl; ty /= tl;
            double amp = L * 0.05 * (0.1 + 0.9 * Math.Pow(s, 1.8)) * swim;
            double off = amp * Math.Sin(_phase - s * 5.2);
            _spine[k * 2] = _nodes[k * 2] - ty * off + lx * (1 - s * 0.6);
            _spine[k * 2 + 1] = _nodes[k * 2 + 1] + tx * off + ly * (1 - s * 0.6);
        }
        _arc[0] = 0;
        for (int k = 1; k < N; k++)
        {
            double dx = _spine[k * 2] - _spine[k * 2 - 2], dy = _spine[k * 2 + 1] - _spine[k * 2 - 1];
            _arc[k] = _arc[k - 1] + Math.Sqrt(dx * dx + dy * dy);
        }
        _arcTotal = _arc[N - 1] > 0 ? _arc[N - 1] : 1;

        double bank = Clamp(_turn * 0.12, -0.35, 0.35), flex = 1 + 0.06 * Math.Sin(_phase * 0.5);
        double reach = _shape.Pectoral[3];
        _finR = L * reach * flex * (1 - bank);
        _finL = L * reach * flex * (1 + bank);

        double x0 = double.MaxValue, y0 = double.MaxValue, x1 = double.MinValue, y1 = double.MinValue;
        for (int k = 0; k < N; k++)
        {
            x0 = Math.Min(x0, _spine[k * 2]); x1 = Math.Max(x1, _spine[k * 2]);
            y0 = Math.Min(y0, _spine[k * 2 + 1]); y1 = Math.Max(y1, _spine[k * 2 + 1]);
        }
        double R = L * 0.36, hd = 0.5 * Math.Sqrt(cw * cw + ch * ch);
        int c0 = (int)Math.Floor((x0 - R) / cw), c1 = (int)Math.Floor((x1 + R) / cw);
        int r0 = (int)Math.Floor((y0 - R) / ch), r1 = (int)Math.Floor((y1 + R) / ch);

        for (int r = r0; r <= r1; r++)
        for (int c = c0; c <= c1; c++)
        {
            double x = c * cw, y = r * ch;
            bool center = Sample(x + cw / 2, y + ch / 2, L);
            if (!center && _hDist > _hReach + hd) continue;   // 離外形太遠，整格不可能有覆蓋
            int flag = _hFlag; double shade = _hShade, s = _hS, v = _hV;
            int hits;
            if (center && _hDist + hd < _hW) hits = 5;          // 整格都在身體內部，不必超取樣
            else
            {
                hits = center ? 1 : 0;
                for (int k = 0; k < Sub.Length; k += 2)
                    if (Sample(x + Sub[k] * cw, y + Sub[k + 1] * ch, L)) hits++;
            }
            if (hits == 0) continue;
            double cov = hits / 5.0;
            uint key = (uint)(Hash3((int)Math.Round(s * 52), (int)Math.Round(v / (cw * 0.9)), 1) * 4294967295.0);
            if (center && cov >= 0.6)
                _cells.Add(new Cell { C = c, R = r, Key = key, Flag = flag,
                    Shade = Clamp(shade + (Hash3(c, r, (int)Math.Floor(t * 6)) - 0.5) * 0.06, 0, 1) });
            else
                _cells.Add(new Cell { C = c, R = r, Key = key, Flag = FEdge, Shade = cov });
        }
    }

    // ── 尾跡 ────────────────────────────────────────────────────────
    private void AddWake()
    {
        for (int k = 6; k < N; k += 3)
        {
            long c = (long)Math.Floor(_spine[k * 2] / CellWidth), r = (long)Math.Floor(_spine[k * 2 + 1] / CellHeight);
            long id = (r << 32) ^ (c & 0xffffffffL);
            _wake[id] = Math.Min(1, (_wake.TryGetValue(id, out var e) ? e : 0) + 0.25);
        }
    }

    private void AgeWake(double dt)
    {
        _wakeDead.Clear();
        foreach (var kv in _wake) if (kv.Value - dt * 1.1 <= 0) _wakeDead.Add(kv.Key);
        foreach (var id in _wakeDead) _wake.Remove(id);
        var keys = new List<long>(_wake.Keys);
        foreach (var id in keys) _wake[id] -= dt * 1.1;
    }

    // ── 點擊簽名 ────────────────────────────────────────────────────
    /// <summary>手動觸發點擊特效（可綁在按鈕或啟動畫面上）。</summary>
    // 簽名從尾巴甩出：甩尾途中依序在尾鰭位置拍出小漣漪並噴出一個字（帶著尾巴當下的速度），
    // 字減速回彈後排成一列，停在鯊魚尾巴上方；甩最快那一下在尾巴擴散一圈大水波。
    private const double SignEnd = 2.3;   // 點擊後幾秒，字開始化成小魚
    private string? _pendText;
    private int _pendNext;
    private double _pendX0, _pendY0, _tipX = double.NaN, _tipY = double.NaN;
    private bool _pendBig;

    public void Burst(double x, double y)
    {
        _spinT = 0; _spinDir = _turn >= 0 ? 1 : -1; _spinTheta0 = _heading;
        _lastMove = _clock.Elapsed.TotalSeconds;
        Start();
        double L = Length, ch = CellHeight, lw = CellWidth * 2;
        string text = string.IsNullOrEmpty(Label) ? " " : Label;
        double wPx = text.Length * lw;
        double W = ActualWidth > 0 ? ActualWidth : 1e9, H = ActualHeight > 0 ? ActualHeight : 1e9;
        double tailX = x - Math.Cos(_heading) * L * 0.85, tailY = y - Math.Sin(_heading) * L * 0.85;
        double cx = Clamp(tailX, wPx / 2 + 8, Math.Max(wPx / 2 + 8, W - wPx / 2 - 8));
        double cy = Clamp(tailY - ch * 3, ch * 2, Math.Max(ch * 2, H - ch * 3));
        _pendText = text; _pendNext = 0; _pendBig = false;
        _pendX0 = SnapX(cx - wPx / 2); _pendY0 = SnapY(cy);
        _tipX = _tipY = double.NaN;
    }

    private void EmitFromTail(double dt, double p)
    {
        if (_pendText == null) return;
        double tipX = _nodes[(N - 1) * 2], tipY = _nodes[(N - 1) * 2 + 1], vx = 0, vy = 0;
        if (!double.IsNaN(_tipX)) { vx = (tipX - _tipX) / Math.Max(dt, 1e-3); vy = (tipY - _tipY) / Math.Max(dt, 1e-3); }
        _tipX = tipX; _tipY = tipY;
        double vm = Math.Sqrt(vx * vx + vy * vy);
        if (vm > 1300) { vx *= 1300 / vm; vy *= 1300 / vm; }
        double L = Length, lw = CellWidth * 2;
        int n = _pendText.Length;
        if (!_pendBig && p >= 0.5)
        {
            _pendBig = true;
            _fx.Add(new Fx { Type = 0, X = tipX, Y = tipY, Life = 1.0, R1 = L * 0.5 });
        }
        while (_pendNext < n && p >= 0.2 + 0.62 * _pendNext / Math.Max(1, n - 1))
        {
            int i = _pendNext++;
            _fx.Add(new Fx { Type = 0, X = tipX, Y = tipY, Life = 0.55, R1 = L * 0.17 });
            _fx.Add(new Fx
            {
                Type = 2, Ch = _pendText[i], I = i, Sx = tipX, Sy = tipY, Vx = vx, Vy = vy,
                Tx = _pendX0 + i * lw, Ty = _pendY0,
                Fly = 0.12, Hold = Math.Max(0.4, SignEnd - _spinT - 0.12), Swim = 0.8, Ang = Math.Atan2(vy, vx),
            });
        }
        if (_pendNext >= n) _pendText = null;
    }

    /// <summary>臨界阻尼彈簧：從尾巴出發、帶初速度衝出去，再收回到字位。</summary>
    private static Point SpringPos(Fx f, double T)
    {
        const double w = 8;
        double e = Math.Exp(-w * T), ax = f.Sx - f.Tx, ay = f.Sy - f.Ty;
        return new Point(f.Tx + (ax + (f.Vx + w * ax) * T) * e, f.Ty + (ay + (f.Vy + w * ay) * T) * e);
    }

    private double SnapX(double x) => Math.Round(x / CellWidth) * CellWidth;
    private double SnapY(double y) => Math.Round(y / CellHeight) * CellHeight;

    // ── 繪製：點陣圖合成 ────────────────────────────────────────────
    // 每種「字元＋色塊」第一次出現時，用 WPF 畫成一張小點陣圖（tile）快取起來；
    // 之後每幀只把 tile 的像素貼進一張 WriteableBitmap，並只上傳有變動的矩形。
    // 這樣 WPF 每幀只要處理「1 張圖」，而不是數百個 DrawText / DrawRectangle。
    private WriteableBitmap? _wb;
    private int[] _buf = Array.Empty<int>();
    private int _pw, _ph;
    private double _sx = 1, _sy = 1;
    private Int32Rect _prevBox = Int32Rect.Empty;
    private int _bx0, _by0, _bx1, _by1;

    private sealed class Tile { public int W, H; public int[] Px = Array.Empty<int>(); }
    private readonly Dictionary<(char, uint, uint, int), Tile> _tiles = new();

    protected override void OnRender(DrawingContext dc)
    {
        if (_wb != null) dc.DrawImage(_wb, new Rect(0, 0, _pw / _sx, _ph / _sy));
    }

    private bool EnsureSurface()
    {
        var dpi = VisualTreeHelper.GetDpi(this);
        int pw = (int)Math.Ceiling(ActualWidth * dpi.DpiScaleX), ph = (int)Math.Ceiling(ActualHeight * dpi.DpiScaleY);
        if (pw <= 0 || ph <= 0) return false;
        if (_wb != null && pw == _pw && ph == _ph && dpi.DpiScaleX == _sx && dpi.DpiScaleY == _sy) return true;
        _sx = dpi.DpiScaleX; _sy = dpi.DpiScaleY; _pw = pw; _ph = ph; _ppd = dpi.PixelsPerDip;
        _wb = new WriteableBitmap(pw, ph, 96 * _sx, 96 * _sy, PixelFormats.Pbgra32, null);
        _buf = new int[pw * ph];
        _prevBox = Int32Rect.Empty;
        _tiles.Clear();
        InvalidateVisual();   // 只有換新點陣圖時才需要重新 OnRender
        return true;
    }

    private void Compose()
    {
        if (_face == null || !EnsureSurface()) return;
        long pf0 = Stopwatch.GetTimestamp();
        ClearRect(_prevBox);
        _bx0 = _by0 = int.MaxValue; _bx1 = _by1 = int.MinValue;

        double cw = CellWidth, ch = CellHeight, t = _t;
        double dot = Math.Max(2, Math.Round(cw * 0.19)) + 1;
        foreach (var kv in _wake)
        {
            long r = kv.Key >> 32; long c = (int)(kv.Key & 0xffffffffL);
            FillRect(Math.Round(c * cw + cw * 0.66 - dot / 2), Math.Round(r * ch + ch * 0.64 - dot / 2), dot, dot, Wake, kv.Value * 0.8);
        }
        if (_ready && _visible) DrawShark(t);
        DrawFx(t, dot);

        var box = _bx1 > _bx0 && _by1 > _by0 ? new Int32Rect(_bx0, _by0, _bx1 - _bx0, _by1 - _by0) : Int32Rect.Empty;
        var dirty = Union(_prevBox, box);
        if (!IsEmpty(dirty)) _wb!.WritePixels(dirty, _buf, _pw * 4, dirty.X, dirty.Y);
        _prevBox = box;
        _pfRenderTicks += Stopwatch.GetTimestamp() - pf0; _pfRenders++;
    }

    private void DrawShark(double t)
    {
        double cw = CellWidth, ch = CellHeight; var tint = _shape.Tint;
        foreach (var k in _cells)
        {
            double x = k.C * cw, y = k.R * ch; uint key = k.Key; double shade = k.Shade;
            double roll = Hash3((int)key, (int)Math.Floor(t * 2.4 + key % 997 / 97.0), 7);
            if (k.Flag == FEdge)
            {
                string set = shade >= 0.5 ? EdgeHeavy : EdgeLight; double lv = 70 + shade * 80;
                var col = roll < 0.07 ? Accents[key % 7] : Rgb(lv * tint[0], lv * tint[1], lv * tint[2] + 6);
                Blit(GetTile(1, set[(int)((key >> 8) % (uint)set.Length)], col, default), x, y, 1);
                continue;
            }
            double level; char g;
            if (k.Flag == FRidge) { level = 226; g = RidgeGlyphs[(int)((key >> 8) % 3)]; }
            else if (k.Flag == FEye) { level = 12; g = 'o'; }
            else if (k.Flag == FFin) { level = 40 + shade * 120; g = FinGlyphs[(int)((key >> 8) % 5)]; }
            else
            {
                int idx = (int)Clamp(Math.Floor((1 - shade) * 8.99) + (int)((key >> 4) % 3) - 1, 0, 8);
                level = 46 + shade * 160; g = SkinRamp[idx];
            }
            Color fg = k.Flag == FEye ? EyeColor : roll < 0.09 ? Accents[key % 7] : roll < 0.13 ? Colors.White
                : shade > 0.55 ? Ink : Ink2;
            Blit(GetTile(0, g, fg, Rgb(level * tint[0], level * tint[1], level * tint[2] + 4)), x, y, 1);
        }
    }

    private void DrawFx(double t, double dot)
    {
        double cw = CellWidth, ch = CellHeight;
        foreach (var f in _fx)
        {
            if (f.T < 0) continue;
            if (f.Type == 0)
            {
                double p = f.T / f.Life, rad = f.R1 * (1 - Math.Pow(1 - p, 3));
                int steps = Math.Max(12, (int)Math.Round(rad * 0.6));
                _seen.Clear();
                for (int k = 0; k < steps; k++)
                {
                    double th = k / (double)steps * Math.PI * 2;
                    long c = (long)Math.Round((f.X + Math.Cos(th) * rad) / cw), r = (long)Math.Round((f.Y + Math.Sin(th) * rad * 0.8) / ch);
                    if (!_seen.Add((r << 32) ^ (c & 0xffffffffL))) continue;
                    FillRect(c * cw + cw * 0.5 - dot / 2, r * ch + ch * 0.6 - dot / 2, dot, dot, Wake, (1 - p) * 0.9);
                }
            }
            else if (f.Type == 1)
            {
                double p = f.T / f.Life;
                bool hot = p < 0.4;
                var col = hot ? Color.FromRgb(255, 120, 60) : Color.FromRgb(220, 40, 40);
                double a = hot ? 1 - p : Math.Min(1, (1 - p) * 1.4);
                Blit(GetTile(1, p < 0.5 ? '*' : '·', col, default), SnapX(f.X), SnapY(f.Y), a);
            }
            else
            {
                double T = f.T;
                if (T < f.Fly + f.Hold)
                {
                    var q = SpringPos(f, T);
                    if (T < f.Fly)
                        // 剛從水花裡冒出：水色 . → o → O
                        Blit(GetTile(1, ".oO"[Math.Min(2, (int)(T / f.Fly * 3))], Wake, default), SnapX(q.X) + cw / 2, SnapY(q.Y), 0.95);
                    else
                    {
                        double bob = Math.Sin(T * 7 + f.I * 0.8) * 2 * Smooth(0.6, 1.0, T);
                        Chip(f, SnapX(q.X), SnapY(q.Y + bob), Math.Min(1, (T - f.Fly) / 0.08), t);
                    }
                }
                else
                {
                    // 化成小魚，順著被甩出的方向游走
                    double p = (T - f.Fly - f.Hold) / f.Swim, d = p * p * 90;
                    double x = f.Tx + Math.Cos(f.Ang) * d, y = f.Ty + Math.Sin(f.Ang) * d * 0.6;
                    if (p < 0.15) Chip(f, SnapX(x), SnapY(y), 1 - p, t);
                    else Blit(GetTile(1, '-', FishColor, default), SnapX(x), SnapY(y), 1 - p);
                }
            }
        }
    }

    private void Chip(Fx f, double x, double y, double alpha, double t)
    {
        double roll = Hash3(f.I, (int)Math.Floor(t * 5 + f.I), 3);
        Blit(GetTile(2, f.Ch, roll < 0.12 ? Colors.White : Ink, Accents[(f.I + (int)Math.Floor(t * 3)) % 7]),
            x, y - CellHeight * 0.5, alpha);
    }

    private readonly HashSet<long> _seen = new();

    /// <summary>kind 0 = 字元格（色塊＋字元）、1 = 只有字元、2 = 簽名字塊（2×2 格、粗體大字）。</summary>
    private Tile GetTile(int kind, char g, Color fg, Color bg)
    {
        fg = Quant(fg); bg = Quant(bg);
        var key = (g, Pack(fg), Pack(bg), kind);
        if (_tiles.TryGetValue(key, out var tile)) return tile;

        double cw = CellWidth, ch = CellHeight;
        double w = kind == 2 ? cw * 2 : cw, h = kind == 2 ? ch * 2 : ch;
        var dv = new DrawingVisual();
        using (var dc = dv.RenderOpen())
        {
            double size, cx, cy; bool bold = kind == 2;
            if (kind == 2)
            {
                dc.DrawRectangle(new SolidColorBrush(bg), null, new Rect(0, 0, w - 1, h - 1));
                size = Math.Round(ch * 1.45); cx = cw - 0.5; cy = ch;
            }
            else
            {
                if (kind == 0) dc.DrawRectangle(new SolidColorBrush(bg), null, new Rect(1, 1, cw - 1, ch - 1));
                size = Math.Round(ch * 0.8); cx = cw / 2; cy = ch / 2 + 1;
            }
            var ft = new FormattedText(g.ToString(), CultureInfo.InvariantCulture, FlowDirection.LeftToRight,
                bold ? _faceBold! : _face!, size, new SolidColorBrush(fg), _ppd);
            dc.DrawText(ft, new Point(cx - ft.WidthIncludingTrailingWhitespace / 2, cy - ft.Height / 2));
        }
        int pw = (int)Math.Ceiling(w * _sx), ph = (int)Math.Ceiling(h * _sy);
        var rtb = new RenderTargetBitmap(pw, ph, 96 * _sx, 96 * _sy, PixelFormats.Pbgra32);
        rtb.Render(dv);
        tile = new Tile { W = pw, H = ph, Px = new int[pw * ph] };
        rtb.CopyPixels(tile.Px, pw * 4, 0);
        if (_tiles.Count > 6000) _tiles.Clear();
        _tiles[key] = tile;
        return tile;
    }

    // ── 像素操作（預乘 alpha 的 BGRA）────────────────────────────────
    private void Blit(Tile tile, double xDip, double yDip, double alpha)
    {
        int ox = (int)Math.Round(xDip * _sx), oy = (int)Math.Round(yDip * _sy);
        int x0 = Math.Max(0, ox), y0 = Math.Max(0, oy), x1 = Math.Min(_pw, ox + tile.W), y1 = Math.Min(_ph, oy + tile.H);
        if (x0 >= x1 || y0 >= y1) return;
        int k = (int)(Clamp(alpha, 0, 1) * 256);
        if (k <= 0) return;
        var px = tile.Px; var buf = _buf;
        for (int y = y0; y < y1; y++)
        {
            int si = (y - oy) * tile.W + (x0 - ox), di = y * _pw + x0;
            for (int x = x0; x < x1; x++, si++, di++)
            {
                uint s = (uint)px[si];
                if (s == 0) continue;
                if (k < 256) s = Scale(s, k);
                uint sa = s >> 24;
                buf[di] = sa >= 255 ? (int)s : (int)(s + Scale((uint)buf[di], 256 - (int)sa));
            }
        }
        Grow(x0, y0, x1, y1);
    }

    private void FillRect(double xDip, double yDip, double wDip, double hDip, Color c, double alpha)
    {
        int x0 = Math.Max(0, (int)Math.Round(xDip * _sx)), y0 = Math.Max(0, (int)Math.Round(yDip * _sy));
        int x1 = Math.Min(_pw, (int)Math.Round((xDip + wDip) * _sx)), y1 = Math.Min(_ph, (int)Math.Round((yDip + hDip) * _sy));
        if (x0 >= x1 || y0 >= y1) return;
        int a = (int)(Clamp(alpha, 0, 1) * c.A);
        if (a <= 0) return;
        uint s = (uint)a << 24 | (uint)(c.R * a / 255) << 16 | (uint)(c.G * a / 255) << 8 | (uint)(c.B * a / 255);
        int inv = 256 - a; var buf = _buf;
        for (int y = y0; y < y1; y++)
        {
            int di = y * _pw + x0;
            for (int x = x0; x < x1; x++, di++) buf[di] = (int)(s + Scale((uint)buf[di], inv));
        }
        Grow(x0, y0, x1, y1);
    }

    private void ClearRect(Int32Rect r)
    {
        if (IsEmpty(r)) return;
        for (int y = r.Y; y < r.Y + r.Height; y++) Array.Clear(_buf, y * _pw + r.X, r.Width);
    }

    private void Grow(int x0, int y0, int x1, int y1)
    {
        if (x0 < _bx0) _bx0 = x0; if (y0 < _by0) _by0 = y0;
        if (x1 > _bx1) _bx1 = x1; if (y1 > _by1) _by1 = y1;
    }

    private static uint Scale(uint c, int k)
    {
        uint uk = (uint)k;
        return ((c >> 24) * uk >> 8) << 24 | (((c >> 16) & 0xFF) * uk >> 8) << 16
             | (((c >> 8) & 0xFF) * uk >> 8) << 8 | ((c & 0xFF) * uk >> 8);
    }

    private static bool IsEmpty(Int32Rect r) => r.Width <= 0 || r.Height <= 0;

    private static Int32Rect Union(Int32Rect a, Int32Rect b)
    {
        if (IsEmpty(a)) return b;
        if (IsEmpty(b)) return a;
        int x0 = Math.Min(a.X, b.X), y0 = Math.Min(a.Y, b.Y);
        int x1 = Math.Max(a.X + a.Width, b.X + b.Width), y1 = Math.Max(a.Y + a.Height, b.Y + b.Height);
        return new Int32Rect(x0, y0, x1 - x0, y1 - y0);
    }

    private static Color Quant(Color c) => Color.FromArgb(c.A, (byte)(c.R & 0xF8), (byte)(c.G & 0xF8), (byte)(c.B & 0xF8));
    private static uint Pack(Color c) => (uint)(c.A << 24 | c.R << 16 | c.G << 8 | c.B);

    // ── 小工具 ──────────────────────────────────────────────────────
    private static Color Rgb(double r, double g, double b) =>
        Color.FromRgb((byte)Clamp(r, 0, 255), (byte)Clamp(g, 0, 255), (byte)Clamp(b, 0, 255));

    private static Color C(string hex) => (Color)ColorConverter.ConvertFromString(hex);
    private static double Clamp(double v, double lo, double hi) => v < lo ? lo : v > hi ? hi : v;
    private static double Smooth(double e0, double e1, double x) { double t = Clamp((x - e0) / (e1 - e0), 0, 1); return t * t * (3 - 2 * t); }

    private static double AngleDiff(double a, double b)
    {
        double d = (b - a) % (Math.PI * 2);
        if (d > Math.PI) d -= Math.PI * 2;
        if (d < -Math.PI) d += Math.PI * 2;
        return d;
    }

    private static bool InTri(double px, double py, double ax, double ay, double bx, double by, double cx, double cy)
    {
        double d1 = (px - bx) * (ay - by) - (ax - bx) * (py - by);
        double d2 = (px - cx) * (by - cy) - (bx - cx) * (py - cy);
        double d3 = (px - ax) * (cy - ay) - (cx - ax) * (py - ay);
        return !((d1 < 0 || d2 < 0 || d3 < 0) && (d1 > 0 || d2 > 0 || d3 > 0));
    }

    /// <summary>與 ascii-sharks math.ts 的 hash3 相同（Math.imul 語意）。</summary>
    private static double Hash3(int x, int y, int z)
    {
        unchecked
        {
            int h = (x * 374761393) ^ (y * 668265263) ^ (z * 1442695041);
            h = (h ^ (int)((uint)h >> 13)) * 1274126177;
            h ^= (int)((uint)h >> 16);
            return (uint)h / 4294967296.0;
        }
    }
}
