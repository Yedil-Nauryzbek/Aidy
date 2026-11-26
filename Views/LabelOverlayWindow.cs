using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;

namespace WpfApp1.Views
{
    /// <summary>One numbered badge; coordinates are physical screen pixels from the UIA worker.</summary>
    public sealed record OverlayLabel(int N, double X, double Y, double W, double H);

    /// <summary>
    /// Numbers on clickable controls for voice clicks ("show numbers", "click 7").
    /// Click-through and never activated: the window in front must keep focus,
    /// because the worker refuses a click when the foreground window changed.
    /// </summary>
    public sealed class LabelOverlayWindow : Window
    {
        private const int GWL_EXSTYLE = -20;
        private const int WS_EX_TRANSPARENT = 0x20;
        private const int WS_EX_LAYERED = 0x80000;
        private const int WS_EX_TOOLWINDOW = 0x80;
        private const int WS_EX_NOACTIVATE = 0x8000000;

        [DllImport("user32.dll")] private static extern int GetWindowLong(IntPtr hwnd, int index);
        [DllImport("user32.dll")] private static extern int SetWindowLong(IntPtr hwnd, int index, int value);

        private readonly Canvas _canvas = new();
        private static readonly Brush BadgeBrush = new SolidColorBrush(Color.FromArgb(240, 255, 214, 0));

        public LabelOverlayWindow()
        {
            WindowStyle = WindowStyle.None;
            AllowsTransparency = true;
            Background = Brushes.Transparent;
            Topmost = true;
            ShowInTaskbar = false;
            ShowActivated = false;
            Focusable = false;
            IsHitTestVisible = false;
            ResizeMode = ResizeMode.NoResize;
            Left = SystemParameters.VirtualScreenLeft;
            Top = SystemParameters.VirtualScreenTop;
            Width = SystemParameters.VirtualScreenWidth;
            Height = SystemParameters.VirtualScreenHeight;
            Content = _canvas;
            SourceInitialized += (_, _) =>
            {
                var hwnd = new WindowInteropHelper(this).Handle;
                SetWindowLong(hwnd, GWL_EXSTYLE, GetWindowLong(hwnd, GWL_EXSTYLE)
                    | WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE);
            };
        }

        public void ShowLabels(IReadOnlyList<OverlayLabel> labels)
        {
            _canvas.Children.Clear();
            if (labels.Count == 0)
            {
                Hide();
                return;
            }
            if (!IsVisible)
                Show();
            var toDip = PresentationSource.FromVisual(this)?.CompositionTarget?.TransformFromDevice ?? Matrix.Identity;
            foreach (var label in labels)
            {
                var corner = toDip.Transform(new Point(label.X, label.Y));
                var badge = new Border
                {
                    Background = BadgeBrush,
                    BorderBrush = Brushes.Black,
                    BorderThickness = new Thickness(1),
                    CornerRadius = new CornerRadius(3),
                    Padding = new Thickness(3, 0, 3, 0),
                    Child = new TextBlock
                    {
                        Text = label.N.ToString(),
                        FontSize = 13,
                        FontWeight = FontWeights.Bold,
                        Foreground = Brushes.Black,
                    },
                };
                Canvas.SetLeft(badge, corner.X - Left);
                Canvas.SetTop(badge, corner.Y - Top);
                _canvas.Children.Add(badge);
            }
        }
    }
}
