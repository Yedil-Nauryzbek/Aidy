using Microsoft.Win32;
using System.Reflection;

namespace WpfApp1.Services
{
    public static class AutoStart
    {
        private const string AppName = "AIDY";

        public static void Enable()
        {
            using var key = Registry.CurrentUser.OpenSubKey(
                @"Software\Microsoft\Windows\CurrentVersion\Run", true);

            key?.SetValue(AppName, Assembly.GetExecutingAssembly().Location);
        }

        public static void Disable()
        {
            using var key = Registry.CurrentUser.OpenSubKey(
                @"Software\Microsoft\Windows\CurrentVersion\Run", true);

            key?.DeleteValue(AppName, false);
        }
    }
}
