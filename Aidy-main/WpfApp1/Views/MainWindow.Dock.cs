using System;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;

namespace WpfApp1.Views
{
    public partial class MainWindow
    {
        private bool _isDockMode;
        private int _dockEdge;
        private Panel? _dockOrbParent;
        private int _dockOrbIndex;
        private Rect _dockRestoreBounds;
        private WindowState _dockRestoreState;
        private ResizeMode _dockRestoreResize;
        private bool _dockRestoreTopmost;
        private double _dockRestoreMinWidth, _dockRestoreMinHeight, _dockRestoreOrbScale;
        private Thickness _dockRestoreOrbMargin;
        private VerticalAlignment _dockRestoreOrbAlignment;

        private void DockToggle_Click(object sender, RoutedEventArgs e)
        {
            e.Handled = true;
            SetDockMode(!_isDockMode);
        }

        private void SetDockMode(bool enabled)
        {
            if (enabled == _isDockMode) return;
            if (enabled)
            {
                // Move the existing visual, keeping all storyboard targets,
                // bindings, microphone telemetry and playback states intact.
                _dockOrbParent = (Panel)OrbHost.Parent;
                _dockOrbIndex = _dockOrbParent.Children.IndexOf(OrbHost);
                _dockRestoreBounds = WindowState == WindowState.Normal
                    ? new Rect(Left, Top, Width, Height) : RestoreBounds;
                _dockRestoreState = WindowState;
                _dockRestoreResize = ResizeMode;
                _dockRestoreTopmost = Topmost;
                _dockRestoreMinWidth = MinWidth;
                _dockRestoreMinHeight = MinHeight;
                _dockRestoreOrbMargin = OrbHost.Margin;
                _dockRestoreOrbAlignment = OrbHost.VerticalAlignment;
                _dockRestoreOrbScale = OrbScale.ScaleX;
                var workArea = GetDockWorkArea();

                _isDockMode = true;
                WindowState = WindowState.Normal;
                MinWidth = 114;
                MinHeight = 114;
                ResizeMode = ResizeMode.NoResize;
                Topmost = true;
                _dockOrbParent.Children.Remove(OrbHost);
                OrbHost.Margin = new Thickness(0);
                OrbHost.VerticalAlignment = VerticalAlignment.Center;
                OrbScale.ScaleX = OrbScale.ScaleY = 0.175;
                DockOrbSlot.Children.Add(OrbHost);
                AppScaleHost.Visibility = Visibility.Collapsed;
                DockRoot.Visibility = Visibility.Visible;
                PositionDockAtEdge(workArea);
                // Avoid a stale focused control keeping hover chrome visible.
                Keyboard.ClearFocus();
            }
            else
            {
                _isDockMode = false;
                DockOrbSlot.Children.Remove(OrbHost);
                OrbHost.Margin = _dockRestoreOrbMargin;
                OrbHost.VerticalAlignment = _dockRestoreOrbAlignment;
                OrbScale.ScaleX = OrbScale.ScaleY = _dockRestoreOrbScale;
                _dockOrbParent!.Children.Insert(_dockOrbIndex, OrbHost);
                DockRoot.Visibility = Visibility.Collapsed;
                AppScaleHost.Visibility = Visibility.Visible;
                MinWidth = _dockRestoreMinWidth;
                MinHeight = _dockRestoreMinHeight;
                Width = _dockRestoreBounds.Width;
                Height = _dockRestoreBounds.Height;
                Left = _dockRestoreBounds.Left;
                Top = _dockRestoreBounds.Top;
                ResizeMode = _dockRestoreResize;
                Topmost = _dockRestoreTopmost;
                WindowState = _dockRestoreState;
                DockToggleButton.Focus();
            }
        }

        private void DockEdge_Click(object sender, RoutedEventArgs e)
        {
            if (!_isDockMode) return;
            e.Handled = true;
            var area = GetDockWorkArea();
            _dockEdge = (_dockEdge + 1) % 4;
            PositionDockAtEdge(area);
        }

        private void PositionDockAtEdge(Rect area)
        {
            bool vertical = _dockEdge % 2 == 1;
            double angle = _dockEdge * 90;
            DockEdgeRotation.Angle = angle;
            Width = vertical ? 114 : 480;
            Height = vertical ? 480 : 114;
            // Rotate the silhouette into the selected edge, but keep the live
            // microphone/speaking symbol and button icons upright.
            DockOrbSlot.RenderTransform = new RotateTransform(-angle);
            DockBrandPanel.RenderTransform = new RotateTransform(-angle);
            DockBrandPanel.Width = vertical ? 40 : double.NaN;
            DockBrandPanel.Margin = new Thickness(24, vertical ? 14 : 7, 0, 0);
            DockStatusText.Visibility = vertical ? Visibility.Collapsed : Visibility.Visible;
            foreach (UIElement child in DockControls.Children)
            {
                child.RenderTransformOrigin = new Point(.5, .5);
                child.RenderTransform = new RotateTransform(-angle);
            }
            Left = _dockEdge switch
            {
                1 => area.Right - Width,
                3 => area.Left,
                _ => area.Left + (area.Width - Width) / 2
            };
            Top = _dockEdge switch
            {
                0 => area.Top,
                2 => area.Bottom - Height,
                _ => area.Top + (area.Height - Height) / 2
            };
        }

        private Rect GetDockWorkArea()
        {
            var hwnd = new WindowInteropHelper(this).Handle;
            var monitor = MonitorFromWindow(hwnd, 2 /* nearest */);
            var info = new DockMonitorInfo { Size = Marshal.SizeOf<DockMonitorInfo>() };
            if (!GetMonitorInfo(monitor, ref info)) return SystemParameters.WorkArea;
            var area = new Rect(new Point(info.Work.Left, info.Work.Top),
                                new Point(info.Work.Right, info.Work.Bottom));
            // Win32 rectangles are physical pixels; WPF positions use DIPs.
            return PresentationSource.FromVisual(this)?.CompositionTarget is { } target
                ? Rect.Transform(area, target.TransformFromDevice) : area;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct DockNativeRect { public int Left, Top, Right, Bottom; }
        [StructLayout(LayoutKind.Sequential)]
        private struct DockMonitorInfo
        {
            public int Size;
            public DockNativeRect Monitor, Work;
            public uint Flags;
        }
        [DllImport("user32.dll")]
        private static extern IntPtr MonitorFromWindow(IntPtr hwnd, uint flags);
        [DllImport("user32.dll", CharSet = CharSet.Auto)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool GetMonitorInfo(IntPtr monitor, ref DockMonitorInfo info);
    }
}
