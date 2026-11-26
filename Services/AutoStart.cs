using Microsoft.Win32;
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;

namespace WpfApp1.Services
{
    public static class AutoStart
    {
        private const string RunKeyPath = @"Software\Microsoft\Windows\CurrentVersion\Run";
        private const string RunValueName = "Aidy";
        private const string StartupShortcutName = "Aidy.lnk";

        public static bool SetEnabled(bool enabled)
        {
            return enabled ? Enable() : Disable();
        }

        public static bool Enable()
        {
            var exePath = GetExecutablePath();
            if (string.IsNullOrWhiteSpace(exePath) || !File.Exists(exePath))
            {
                return IsEnabled();
            }

            if (TrySetRegistryEntry(exePath))
            {
                // Registry is preferred. Remove fallback shortcut if it exists.
                TryDeleteStartupShortcut();
                return true;
            }

            return TryCreateStartupShortcut(exePath) || IsEnabled();
        }

        public static bool Disable()
        {
            TryRemoveRegistryEntry();
            TryDeleteStartupShortcut();
            return !IsEnabled();
        }

        public static bool IsEnabled()
        {
            var exePath = GetExecutablePath();
            if (string.IsNullOrWhiteSpace(exePath))
            {
                return false;
            }

            return HasMatchingRegistryEntry(exePath) || HasMatchingStartupShortcut(exePath);
        }

        private static bool HasMatchingRegistryEntry(string exePath)
        {
            try
            {
                using var key = Registry.CurrentUser.OpenSubKey(RunKeyPath, writable: false);
                var value = key?.GetValue(RunValueName)?.ToString();
                return IsStartupValueForExecutable(value, exePath);
            }
            catch
            {
                return false;
            }
        }

        private static bool TrySetRegistryEntry(string exePath)
        {
            try
            {
                using var key = Registry.CurrentUser.CreateSubKey(RunKeyPath, writable: true);
                if (key == null)
                {
                    return false;
                }

                key.SetValue(RunValueName, Quote(exePath), RegistryValueKind.String);
                var savedValue = key.GetValue(RunValueName)?.ToString();
                return IsStartupValueForExecutable(savedValue, exePath);
            }
            catch
            {
                return false;
            }
        }

        private static void TryRemoveRegistryEntry()
        {
            try
            {
                using var key = Registry.CurrentUser.OpenSubKey(RunKeyPath, writable: true);
                key?.DeleteValue(RunValueName, throwOnMissingValue: false);
            }
            catch
            {
                // Ignore: should never crash settings screen.
            }
        }

        private static bool HasMatchingStartupShortcut(string exePath)
        {
            var shortcutPath = GetStartupShortcutPath();
            if (!File.Exists(shortcutPath))
            {
                return false;
            }

            var targetPath = TryReadShortcutTarget(shortcutPath);
            return !string.IsNullOrWhiteSpace(targetPath) && PathsEqual(targetPath, exePath);
        }

        private static bool TryCreateStartupShortcut(string exePath)
        {
            object? shell = null;
            object? shortcut = null;

            try
            {
                var shellType = Type.GetTypeFromProgID("WScript.Shell");
                if (shellType == null)
                {
                    return false;
                }

                shell = Activator.CreateInstance(shellType);
                if (shell == null)
                {
                    return false;
                }

                var shortcutPath = GetStartupShortcutPath();
                shortcut = shellType.InvokeMember(
                    "CreateShortcut",
                    BindingFlags.InvokeMethod,
                    binder: null,
                    target: shell,
                    args: new object[] { shortcutPath });
                if (shortcut == null)
                {
                    return false;
                }

                var shortcutType = shortcut.GetType();
                shortcutType.InvokeMember("TargetPath", BindingFlags.SetProperty, null, shortcut, new object[] { exePath });
                shortcutType.InvokeMember("Arguments", BindingFlags.SetProperty, null, shortcut, new object[] { string.Empty });
                shortcutType.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, shortcut, new object[] { Path.GetDirectoryName(exePath) ?? string.Empty });
                shortcutType.InvokeMember("Description", BindingFlags.SetProperty, null, shortcut, new object[] { "Start Aidy with Windows" });
                shortcutType.InvokeMember("IconLocation", BindingFlags.SetProperty, null, shortcut, new object[] { exePath });
                shortcutType.InvokeMember("Save", BindingFlags.InvokeMethod, null, shortcut, args: null);

                return HasMatchingStartupShortcut(exePath);
            }
            catch
            {
                return false;
            }
            finally
            {
                ReleaseComObject(shortcut);
                ReleaseComObject(shell);
            }
        }

        private static string? TryReadShortcutTarget(string shortcutPath)
        {
            object? shell = null;
            object? shortcut = null;

            try
            {
                var shellType = Type.GetTypeFromProgID("WScript.Shell");
                if (shellType == null)
                {
                    return null;
                }

                shell = Activator.CreateInstance(shellType);
                if (shell == null)
                {
                    return null;
                }

                shortcut = shellType.InvokeMember(
                    "CreateShortcut",
                    BindingFlags.InvokeMethod,
                    binder: null,
                    target: shell,
                    args: new object[] { shortcutPath });
                if (shortcut == null)
                {
                    return null;
                }

                return shortcut.GetType()
                    .InvokeMember("TargetPath", BindingFlags.GetProperty, null, shortcut, args: null)
                    ?.ToString();
            }
            catch
            {
                return null;
            }
            finally
            {
                ReleaseComObject(shortcut);
                ReleaseComObject(shell);
            }
        }

        private static void TryDeleteStartupShortcut()
        {
            try
            {
                var shortcutPath = GetStartupShortcutPath();
                if (File.Exists(shortcutPath))
                {
                    File.Delete(shortcutPath);
                }
            }
            catch
            {
                // Ignore: should never crash settings screen.
            }
        }

        private static string GetStartupShortcutPath()
        {
            var startupDir = Environment.GetFolderPath(Environment.SpecialFolder.Startup);
            return Path.Combine(startupDir, StartupShortcutName);
        }

        private static string GetExecutablePath()
        {
            var processPath = Environment.ProcessPath;
            if (!string.IsNullOrWhiteSpace(processPath))
            {
                return processPath;
            }

            try
            {
                var entryAssemblyPath = Assembly.GetEntryAssembly()?.Location;
                if (!string.IsNullOrWhiteSpace(entryAssemblyPath))
                {
                    return entryAssemblyPath;
                }
            }
            catch
            {
                // Ignore.
            }

            try
            {
                var modulePath = Process.GetCurrentProcess().MainModule?.FileName;
                if (!string.IsNullOrWhiteSpace(modulePath))
                {
                    return modulePath;
                }
            }
            catch
            {
                // Ignore.
            }

            return Assembly.GetExecutingAssembly().Location;
        }

        private static bool IsStartupValueForExecutable(string? startupValue, string exePath)
        {
            if (string.IsNullOrWhiteSpace(startupValue))
            {
                return false;
            }

            var candidatePath = ExtractExecutablePath(startupValue);
            return !string.IsNullOrWhiteSpace(candidatePath) && PathsEqual(candidatePath, exePath);
        }

        private static string ExtractExecutablePath(string rawValue)
        {
            var expanded = Environment.ExpandEnvironmentVariables((rawValue ?? string.Empty).Trim());
            if (expanded.StartsWith("\"", StringComparison.Ordinal))
            {
                var endQuote = expanded.IndexOf('"', 1);
                if (endQuote > 1)
                {
                    return expanded.Substring(1, endQuote - 1);
                }
            }

            var firstSpace = expanded.IndexOf(' ');
            return firstSpace > 0 ? expanded[..firstSpace] : expanded;
        }

        private static bool PathsEqual(string left, string right)
        {
            var a = NormalizePath(left);
            var b = NormalizePath(right);
            return string.Equals(a, b, StringComparison.OrdinalIgnoreCase);
        }

        private static string NormalizePath(string path)
        {
            try
            {
                return Path.GetFullPath((path ?? string.Empty).Trim().Trim('"'));
            }
            catch
            {
                return (path ?? string.Empty).Trim().Trim('"');
            }
        }

        private static string Quote(string path)
        {
            var trimmed = (path ?? string.Empty).Trim().Trim('"');
            return $"\"{trimmed}\"";
        }

        private static void ReleaseComObject(object? comObject)
        {
            if (comObject == null)
            {
                return;
            }

            try
            {
                if (Marshal.IsComObject(comObject))
                {
                    Marshal.FinalReleaseComObject(comObject);
                }
            }
            catch
            {
                // Ignore.
            }
        }
    }
}
