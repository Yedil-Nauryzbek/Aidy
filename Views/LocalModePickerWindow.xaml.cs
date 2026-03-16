// WpfApp1/Views/LocalModePickerWindow.xaml.cs
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;

namespace WpfApp1.Views
{
    public record PickerResult(string ActionType, string Target, string DisplayName);

    internal sealed class MenuNode
    {
        public string         Label      { get; init; } = string.Empty;
        public string         Icon       { get; init; } = "\u25B8";
        public List<MenuNode>? Children  { get; init; }
        public string?        ActionType { get; init; }
        public string?        Target     { get; init; }
        public bool HasChildren => Children is { Count: > 0 };
    }

    public partial class LocalModePickerWindow : Window
    {
        public PickerResult? Result { get; private set; }

        private Border? _active1;
        private Border? _active2;
        private DispatcherTimer? _collapseTimer;

        private static readonly Brush TransBrush  = Brushes.Transparent;
        private static readonly Brush HoverBrush  = new SolidColorBrush(Color.FromRgb(0x13, 0x21, 0x37));
        private static readonly Brush ActiveBrush = new SolidColorBrush(Color.FromRgb(0x17, 0x28, 0x42));

        private static readonly Brush IconFg  = new SolidColorBrush(Color.FromRgb(0x55, 0x68, 0x88));
        private static readonly Brush LabelFg = new SolidColorBrush(Color.FromRgb(0xCC, 0xD8, 0xFF));
        private static readonly Brush ArrowFg = new SolidColorBrush(Color.FromRgb(0x30, 0x40, 0x60));

        public LocalModePickerWindow(int slotIndex = 0)
        {
            InitializeComponent();
            SlotLabel.Text = $"Slot {slotIndex + 1}";
            PopulatePanel(Level1Panel, BuildRootMenu(), 1);
        }

        // ── Menu tree ──────────────────────────────────────────────────────────

        private static List<MenuNode> BuildRootMenu()
        {
            int[] vol = { 5, 10, 15, 25, 50, 75, 100 };
            int[] tmr = { 1, 3, 5, 10, 15, 30, 45, 60 };

            return new List<MenuNode>
            {
                new() {
                    Label = "Apps", Icon = "\u25C8",
                    Children = new List<MenuNode>
                    {
                        new() {
                            Label = "Open", Icon = "\u25B7",
                            Children = KnownApps()
                                .Select(a => new MenuNode { Label = a.Label, Icon = a.Icon,
                                    ActionType = "open_app", Target = a.Id })
                                .ToList()
                        },
                        new() {
                            Label = "Close", Icon = "\u25B7",
                            Children = KnownApps()
                                .Select(a => new MenuNode { Label = a.Label, Icon = a.Icon,
                                    ActionType = "close_app", Target = a.Id })
                                .ToList()
                        },
                    }
                },

                new() { Label = "Restart Computer", Icon = "\u21BA",
                        ActionType = "function", Target = "restart_computer" },

                new() { Label = "Shutdown",          Icon = "\u23FB",
                        ActionType = "function", Target = "shutdown_computer" },

                new() {
                    Label = "Open Website", Icon = "\U0001F310",
                    Children = new List<MenuNode>
                    {
                        new() { Label = "WhatsApp", Icon = "\U0001F4AC",
                                ActionType = "function", Target = "website_whatsapp" },
                        new() { Label = "ChatGPT",  Icon = "\U0001F916",
                                ActionType = "function", Target = "website_chatgpt" },
                        new() { Label = "YouTube",  Icon = "\u25B6",
                                ActionType = "function", Target = "website_youtube" },
                    }
                },

                new() {
                    Label = "Sound", Icon = "\U0001F50A",
                    Children = new List<MenuNode>
                    {
                        new() {
                            Label = "Decrease", Icon = "\U0001F509",
                            Children = vol.Select(v => new MenuNode
                                { Label = $"{v}%", Icon = "\u2212",
                                  ActionType = "function", Target = $"volume_down_{v}" }).ToList()
                        },
                        new() {
                            Label = "Increase", Icon = "\U0001F50A",
                            Children = vol.Select(v => new MenuNode
                                { Label = $"{v}%", Icon = "+",
                                  ActionType = "function", Target = $"volume_up_{v}" }).ToList()
                        },
                    }
                },

                new() {
                    Label = "Brightness", Icon = "\u2600",
                    Children = new List<MenuNode>
                    {
                        new() {
                            Label = "Decrease", Icon = "\u25CF",
                            Children = vol.Select(v => new MenuNode
                                { Label = $"{v}%", Icon = "\u2212",
                                  ActionType = "function", Target = $"brightness_down_{v}" }).ToList()
                        },
                        new() {
                            Label = "Increase", Icon = "\u25CB",
                            Children = vol.Select(v => new MenuNode
                                { Label = $"{v}%", Icon = "+",
                                  ActionType = "function", Target = $"brightness_up_{v}" }).ToList()
                        },
                    }
                },

                new() {
                    Label = "Timer", Icon = "\u23F1",
                    Children = tmr.Select(m => new MenuNode
                        { Label = $"{m} min", Icon = "\u23F1",
                          ActionType = "function", Target = $"timer_{m}" }).ToList()
                },
            };
        }

        // Apps that Aidy actually supports, matching ids in apps.json
        private static List<(string Label, string Icon, string Id)> KnownApps() => new()
        {
            ("Chrome",          "\U0001F310", "chrome"),
            ("Opera",           "\U0001F534", "opera"),
            ("Yandex Browser",  "\U0001F7E1", "yandex_browser"),
            ("Yandex Music",    "\U0001F3B5", "yandex_music"),
            ("Telegram",        "\u2708",  "telegram"),
            ("Discord",         "\U0001F4AC", "discord"),
            ("Spotify",         "\U0001F3B5", "spotify"),
            ("File Explorer",   "\U0001F4C1", "explorer"),
            ("Settings",        "\u2699",  "settings"),
            ("Calculator",      "\U0001F522", "calculator"),
            ("Notepad",         "\U0001F4DD", "notepad"),
            ("Task Manager",    "\u2699\uFE0F", "task_manager"),
            ("VS Code",         "\U0001F4BB", "vscode"),
            ("Steam",           "\U0001F3AE", "steam"),
            ("YouTube",         "\u25B6",  "youtube"),
            ("ChatGPT",         "\U0001F916", "gpt"),
            ("WhatsApp",        "\U0001F4AC", "whatsapp_web"),
        };

        // ── Panel building ─────────────────────────────────────────────────────

        private void PopulatePanel(StackPanel panel, IEnumerable<MenuNode> nodes, int level)
        {
            panel.Children.Clear();
            foreach (var node in nodes)
                panel.Children.Add(CreateItem(node, level));
        }

        private UIElement CreateItem(MenuNode node, int level)
        {
            var border = new Border
            {
                CornerRadius = new CornerRadius(8),
                Padding      = new Thickness(8, 7, 8, 7),
                Cursor       = Cursors.Hand,
                Background   = TransBrush,
            };

            var grid = new Grid();
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(26) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

            var iconBlock = new TextBlock
            {
                Text                = node.Icon,
                FontSize            = 13,
                Foreground          = IconFg,
                VerticalAlignment   = VerticalAlignment.Center,
                HorizontalAlignment = HorizontalAlignment.Center,
            };
            Grid.SetColumn(iconBlock, 0);
            grid.Children.Add(iconBlock);

            var labelBlock = new TextBlock
            {
                Text              = node.Label,
                FontSize          = 13,
                Foreground        = LabelFg,
                VerticalAlignment = VerticalAlignment.Center,
            };
            Grid.SetColumn(labelBlock, 1);
            grid.Children.Add(labelBlock);

            if (node.HasChildren)
            {
                var arrow = new TextBlock
                {
                    Text              = "\u203A",
                    FontSize          = 18,
                    Foreground        = ArrowFg,
                    VerticalAlignment = VerticalAlignment.Center,
                };
                Grid.SetColumn(arrow, 2);
                grid.Children.Add(arrow);
            }

            border.Child = grid;

            // Hover: navigate + highlight
            border.MouseEnter += (_, _) =>
            {
                if (level == 1)
                {
                    if (_active1 != null && _active1 != border)
                        _active1.Background = TransBrush;
                    _active1 = border;
                    OnHoverL1(node);
                }
                else if (level == 2)
                {
                    if (_active2 != null && _active2 != border)
                        _active2.Background = TransBrush;
                    _active2 = border;
                    OnHoverL2(node);
                }
                border.Background = node.HasChildren ? ActiveBrush : HoverBrush;
            };

            border.MouseLeave += (_, _) =>
            {
                // Keep the active branch item highlighted
                bool keep = (level == 1 && border == _active1 && node.HasChildren) ||
                            (level == 2 && border == _active2 && node.HasChildren);
                if (!keep)
                    border.Background = TransBrush;
            };

            // Click: commit leaf items
            if (!node.HasChildren)
                border.MouseLeftButtonUp += (_, _) => Commit(node);

            return border;
        }

        // ── Navigation ─────────────────────────────────────────────────────────

        private void OnHoverL1(MenuNode node)
        {
            if (node.HasChildren)
            {
                PopulatePanel(Level2Panel, node.Children!, 2);
                Sep12.Visibility = Level2Scroll.Visibility = Visibility.Visible;
                // collapse L3 when L1 selection changes
                Sep23.Visibility = Level3Scroll.Visibility = Visibility.Collapsed;
                _active2 = null;
            }
            else
            {
                Sep12.Visibility = Level2Scroll.Visibility = Visibility.Collapsed;
                Sep23.Visibility = Level3Scroll.Visibility = Visibility.Collapsed;
            }
        }

        private void OnHoverL2(MenuNode node)
        {
            if (node.HasChildren)
            {
                PopulatePanel(Level3Panel, node.Children!, 3);
                Sep23.Visibility = Level3Scroll.Visibility = Visibility.Visible;
            }
            else
            {
                Sep23.Visibility = Level3Scroll.Visibility = Visibility.Collapsed;
            }
        }

        private void Commit(MenuNode node)
        {
            Result = new PickerResult(node.ActionType!, node.Target!, node.Label);
            DialogResult = true;
        }

        // ── Window controls ────────────────────────────────────────────────────

        private void Panels_MouseEnter(object sender, MouseEventArgs e)
        {
            // Cancel any pending collapse when cursor re-enters the panels area
            _collapseTimer?.Stop();
        }

        private void Panels_MouseLeave(object sender, MouseEventArgs e)
        {
            // Use a short delay before collapsing to allow cursor to traverse
            // gaps between panels (separators, layout changes during resize).
            _collapseTimer?.Stop();
            _collapseTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(200) };
            _collapseTimer.Tick += (_, _) =>
            {
                _collapseTimer.Stop();

                // Only collapse if cursor is truly outside the panels area
                var panelsElement = sender as UIElement;
                if (panelsElement != null && panelsElement.IsMouseOver)
                    return;

                Sep12.Visibility = Level2Scroll.Visibility = Visibility.Collapsed;
                Sep23.Visibility = Level3Scroll.Visibility = Visibility.Collapsed;

                if (_active1 != null) { _active1.Background = TransBrush; _active1 = null; }
                if (_active2 != null) { _active2.Background = TransBrush; _active2 = null; }
            };
            _collapseTimer.Start();
        }

        private void CloseBtn_Click(object sender, RoutedEventArgs e) => DialogResult = false;

        private void Header_MouseDown(object sender, MouseButtonEventArgs e)
        {
            if (e.LeftButton == MouseButtonState.Pressed) DragMove();
        }
    }
}
