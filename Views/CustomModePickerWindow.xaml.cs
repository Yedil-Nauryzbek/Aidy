// WpfApp1/Views/CustomModePickerWindow.xaml.cs
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

    public partial class CustomModePickerWindow : Window
    {
        public PickerResult? Result { get; private set; }

        private Border? _active1;
        private Border? _active2;
        private DispatcherTimer? _collapseTimer;

        // Guard flag: suppresses spurious MouseLeave events that WPF fires
        // when the window resizes due to SizeToContent after panel visibility
        // changes.  Set before changing visibility, cleared after layout settles.
        private bool _layoutGuard;

        private static readonly Brush TransBrush  = Brushes.Transparent;
        private static readonly Brush HoverBrush  = new SolidColorBrush(Color.FromRgb(0x13, 0x21, 0x37));
        private static readonly Brush ActiveBrush = new SolidColorBrush(Color.FromRgb(0x17, 0x28, 0x42));

        private static readonly Brush IconFg  = new SolidColorBrush(Color.FromRgb(0x55, 0x68, 0x88));
        private static readonly Brush LabelFg = new SolidColorBrush(Color.FromRgb(0xCC, 0xD8, 0xFF));
        private static readonly Brush ArrowFg = new SolidColorBrush(Color.FromRgb(0x30, 0x40, 0x60));

        public CustomModePickerWindow(int slotIndex = 0)
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
                            Children = new List<MenuNode>
                            {
                                new() { Label = "Close everything", Icon = "\u2716",
                                        ActionType = "function", Target = "close_everything" },
                            }
                            .Concat(KnownApps()
                                .Select(a => new MenuNode { Label = a.Label, Icon = a.Icon,
                                    ActionType = "close_app", Target = a.Id }))
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
                        new() { Label = "Custom URL...", Icon = "\u270E",
                                ActionType = "_custom_url_", Target = "" },
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
                // Cancel any pending collapse — user is still inside the menu.
                _collapseTimer?.Stop();

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

        /// <summary>
        /// Raises the layout guard, stops any collapse timer, then clears
        /// the guard once the current layout pass and all pending input
        /// events have been processed.  This prevents the spurious
        /// <c>MouseLeave</c> that WPF fires on the panels StackPanel
        /// when the window resizes (due to <c>SizeToContent</c>) from
        /// collapsing the menu.
        /// </summary>
        private void BeginLayoutGuard()
        {
            _layoutGuard = true;
            _collapseTimer?.Stop();
            // DispatcherPriority.Input ensures the guard stays up until
            // all mouse events triggered by the layout change have fired.
            Dispatcher.BeginInvoke(DispatcherPriority.Input, () =>
            {
                _layoutGuard = false;
            });
        }

        private void OnHoverL1(MenuNode node)
        {
            if (node.HasChildren)
            {
                BeginLayoutGuard();
                PopulatePanel(Level2Panel, node.Children!, 2);
                Sep12.Visibility = Level2Scroll.Visibility = Visibility.Visible;
                // collapse L3 when L1 selection changes
                Sep23.Visibility = Level3Scroll.Visibility = Visibility.Collapsed;
                _active2 = null;
            }
            else
            {
                BeginLayoutGuard();
                Sep12.Visibility = Level2Scroll.Visibility = Visibility.Collapsed;
                Sep23.Visibility = Level3Scroll.Visibility = Visibility.Collapsed;
            }
        }

        private void OnHoverL2(MenuNode node)
        {
            if (node.HasChildren)
            {
                BeginLayoutGuard();
                PopulatePanel(Level3Panel, node.Children!, 3);
                Sep23.Visibility = Level3Scroll.Visibility = Visibility.Visible;
            }
            else
            {
                BeginLayoutGuard();
                Sep23.Visibility = Level3Scroll.Visibility = Visibility.Collapsed;
            }
        }

        private void Commit(MenuNode node)
        {
            if (node.ActionType == "_custom_url_")
            {
                PromptCustomUrl();
                return;
            }

            Result = new PickerResult(node.ActionType!, node.Target!, node.Label);
            DialogResult = true;
        }

        private void PromptCustomUrl()
        {
            // Show inline URL input panel
            _urlInputPanel ??= CreateUrlInputPanel();
            if (!_urlInputPanelAdded)
            {
                // Find the root border and add the input panel
                if (Content is Border rootBorder && rootBorder.Child is Grid rootGrid)
                {
                    rootGrid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
                    Grid.SetRow(_urlInputPanel, rootGrid.RowDefinitions.Count - 1);
                    rootGrid.Children.Add(_urlInputPanel);
                    _urlInputPanelAdded = true;
                }
            }
            _urlInputPanel.Visibility = Visibility.Visible;
            _urlTextBox?.Focus();
            _urlTextBox?.SelectAll();
        }

        private Grid? _urlInputPanel;
        private TextBox? _urlTextBox;
        private bool _urlInputPanelAdded;

        private Grid CreateUrlInputPanel()
        {
            var panel = new Grid { Margin = new Thickness(16, 0, 16, 16) };
            panel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            panel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

            var label = new TextBlock
            {
                Text = "Paste website link:",
                FontSize = 12,
                Foreground = new SolidColorBrush(Color.FromRgb(0xAA, 0xBB, 0xDD)),
                Margin = new Thickness(0, 0, 0, 6),
            };
            Grid.SetRow(label, 0);
            panel.Children.Add(label);

            var inputRow = new Grid();
            inputRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            inputRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

            _urlTextBox = new TextBox
            {
                FontSize = 13,
                Padding = new Thickness(10, 8, 10, 8),
                Background = new SolidColorBrush(Color.FromRgb(0x0F, 0x17, 0x28)),
                Foreground = new SolidColorBrush(Color.FromRgb(0xD8, 0xE4, 0xFF)),
                BorderBrush = new SolidColorBrush(Color.FromRgb(0x2A, 0x3A, 0x5D)),
                BorderThickness = new Thickness(1),
                CaretBrush = new SolidColorBrush(Color.FromRgb(0x57, 0xF2, 0x87)),
                Text = "https://",
            };
            // Allow Enter key to confirm
            _urlTextBox.KeyDown += (_, args) =>
            {
                if (args.Key == Key.Enter)
                    CommitCustomUrl();
            };
            Grid.SetColumn(_urlTextBox, 0);
            inputRow.Children.Add(_urlTextBox);

            var confirmBtn = new Button
            {
                Content = "\u2713",
                Width = 36,
                Height = 36,
                Margin = new Thickness(6, 0, 0, 0),
                Cursor = Cursors.Hand,
                FocusVisualStyle = null,
            };
            confirmBtn.Click += (_, _) => CommitCustomUrl();
            // Style the button
            var btnTemplate = new ControlTemplate(typeof(Button));
            var btnBorder = new FrameworkElementFactory(typeof(Border));
            btnBorder.SetValue(Border.CornerRadiusProperty, new CornerRadius(8));
            btnBorder.SetValue(Border.BackgroundProperty, new SolidColorBrush(Color.FromRgb(0x17, 0x28, 0x42)));
            btnBorder.SetValue(Border.BorderBrushProperty, new SolidColorBrush(Color.FromRgb(0x57, 0xF2, 0x87)));
            btnBorder.SetValue(Border.BorderThicknessProperty, new Thickness(1));
            var btnText = new FrameworkElementFactory(typeof(TextBlock));
            btnText.SetValue(TextBlock.TextProperty, "\u2713");
            btnText.SetValue(TextBlock.FontSizeProperty, 16.0);
            btnText.SetValue(TextBlock.ForegroundProperty, new SolidColorBrush(Color.FromRgb(0x57, 0xF2, 0x87)));
            btnText.SetValue(TextBlock.HorizontalAlignmentProperty, HorizontalAlignment.Center);
            btnText.SetValue(TextBlock.VerticalAlignmentProperty, VerticalAlignment.Center);
            btnBorder.AppendChild(btnText);
            btnTemplate.VisualTree = btnBorder;
            confirmBtn.Template = btnTemplate;

            Grid.SetColumn(confirmBtn, 1);
            inputRow.Children.Add(confirmBtn);

            Grid.SetRow(inputRow, 1);
            panel.Children.Add(inputRow);

            return panel;
        }

        private void CommitCustomUrl()
        {
            var url = _urlTextBox?.Text?.Trim() ?? "";
            if (string.IsNullOrWhiteSpace(url) || url == "https://")
                return;

            // Auto-prepend https:// if no scheme
            if (!url.StartsWith("http://", StringComparison.OrdinalIgnoreCase) &&
                !url.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
            {
                url = "https://" + url;
            }

            // Extract display name from domain
            string display;
            try
            {
                var uri = new Uri(url);
                display = uri.Host.Replace("www.", "");
            }
            catch
            {
                display = url;
            }

            Result = new PickerResult("open_url", url, display);
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
            // During a layout guard (panel visibility just changed and the
            // window is resizing), WPF fires spurious MouseLeave events.
            // Ignore them — the guard will be cleared once layout settles.
            if (_layoutGuard)
                return;

            // Use a short delay before collapsing to allow cursor to traverse
            // gaps between panels (separators, layout changes during resize).
            _collapseTimer?.Stop();
            _collapseTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(300) };
            _collapseTimer.Tick += (_, _) =>
            {
                _collapseTimer.Stop();

                // If a new layout guard appeared while the timer was running,
                // the collapse is no longer valid.
                if (_layoutGuard)
                    return;

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
