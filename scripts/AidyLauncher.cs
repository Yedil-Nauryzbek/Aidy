using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

[assembly: AssemblyTitle("Aidy")]
[assembly: AssemblyDescription("Launch Aidy from the current project files")]
[assembly: AssemblyProduct("Aidy")]
[assembly: AssemblyVersion("1.0.0.0")]

internal static class AidyLauncher
{
    private static readonly string LogPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "Aidy", "logs", "desktop-launcher.log");

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr window);

    [DllImport("user32.dll")]
    private static extern bool ShowWindowAsync(IntPtr window, int command);

    [DllImport("user32.dll")]
    private static extern bool IsIconic(IntPtr window);

    [STAThread]
    private static int Main()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        try
        {
            string root;
            using (var reader = new StreamReader(
                Assembly.GetExecutingAssembly().GetManifestResourceStream("Aidy.ProjectRoot"), Encoding.UTF8))
                root = reader.ReadToEnd().Trim();

            string project = Path.Combine(root, "WpfApp1.csproj");
            if (!File.Exists(project)) project = Path.Combine(root, "WpfApp1", "WpfApp1.csproj");
            string projectDirectory = Path.GetDirectoryName(project);
            string executable = Path.Combine(projectDirectory, "bin", "Release", "net8.0-windows", "WpfApp1.exe");
            if (!File.Exists(project))
                throw new FileNotFoundException(
                    "Папка проекта Aidy перемещена или удалена.\n" +
                    "Запустите scripts\\install-desktop.ps1 из новой папки проекта.\n\n" + root);

            string user = WindowsIdentity.GetCurrent().User.Value;
            using (var mutex = new Mutex(false, "Local\\Aidy.SourceLauncher." + user))
            {
                bool acquired;
                try { acquired = mutex.WaitOne(0); }
                catch (AbandonedMutexException) { acquired = true; }
                if (!acquired) return 0;
                try
                {
                    if (ActivateRunningApplication(executable)) return 0;
                    return ShowProgress(root, project, executable);
                }
                finally { mutex.ReleaseMutex(); }
            }
        }
        catch (Exception exception)
        {
            ShowError(exception);
            return 1;
        }
    }

    private static int ShowProgress(string root, string project, string executable)
    {
        int result = 0;
        using (var form = new Form())
        {
            form.Text = "Aidy";
            form.ClientSize = new Size(380, 126);
            form.StartPosition = FormStartPosition.CenterScreen;
            form.FormBorderStyle = FormBorderStyle.FixedDialog;
            form.ControlBox = false;
            form.BackColor = Color.FromArgb(20, 25, 36);
            form.ForeColor = Color.White;
            form.Font = new Font("Segoe UI", 11);
            form.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            form.Controls.Add(new Label {
                Text = "Aidy — подготовка к запуску",
                AutoSize = true, Location = new Point(22, 19)
            });
            form.Controls.Add(new Label {
                Text = "Проверяем и собираем изменения…",
                AutoSize = true, Location = new Point(22, 50),
                ForeColor = Color.FromArgb(177, 193, 210), Font = new Font("Segoe UI", 9)
            });
            form.Controls.Add(new ProgressBar {
                Location = new Point(22, 86), Size = new Size(336, 9),
                Style = ProgressBarStyle.Marquee
            });
            form.Shown += async delegate
            {
                try { await Task.Run(() => BuildAndLaunch(root, project, executable)); }
                catch (Exception exception)
                {
                    result = 1;
                    form.Hide();
                    ShowError(exception);
                }
                finally { form.Close(); }
            };
            Application.Run(form);
        }
        return result;
    }

    private static void BuildAndLaunch(string root, string project, string executable)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(LogPath));
        File.WriteAllText(LogPath, DateTimeOffset.Now.ToString("O") + "\r\nProject: " + project + "\r\n", Encoding.UTF8);
        string dotnet = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "dotnet", "dotnet.exe");
        if (!File.Exists(dotnet)) dotnet = "dotnet.exe";
        var start = new ProcessStartInfo {
            FileName = dotnet,
            Arguments = "build \"" + project + "\" -c Release --nologo --verbosity minimal",
            WorkingDirectory = root,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8
        };
        start.EnvironmentVariables["DOTNET_CLI_UI_LANGUAGE"] = "en";
        using (var build = Process.Start(start))
        {
            // Drain both streams concurrently so a verbose build cannot deadlock.
            var output = build.StandardOutput.ReadToEndAsync();
            var error = build.StandardError.ReadToEndAsync();
            build.WaitForExit();
            Task.WaitAll(output, error);
            File.AppendAllText(LogPath, output.Result + error.Result, Encoding.UTF8);
            if (build.ExitCode != 0)
                throw new InvalidOperationException(
                    "Не удалось собрать изменения Aidy. Исправьте ошибку в проекте и запустите Aidy снова.");
        }

        if (!File.Exists(executable))
            throw new FileNotFoundException("После сборки не найден файл приложения: " + executable);
        if (ActivateRunningApplication(executable)) return;
        var applicationStart = new ProcessStartInfo {
            FileName = executable,
            WorkingDirectory = Path.GetDirectoryName(executable),
            UseShellExecute = false,
            CreateNoWindow = true
        };
        string python = Path.Combine(root, ".venv", "Scripts", "python.exe");
        if (File.Exists(python)) applicationStart.EnvironmentVariables["AIDY_PYTHON"] = python;
        using (var application = Process.Start(applicationStart))
        {
            File.AppendAllText(LogPath, "Started PID " + application.Id + ": " + executable + "\r\n", Encoding.UTF8);
            // Keep the launch mutex until the application has created its window.
            application.WaitForInputIdle(10000);
            if (application.HasExited && application.ExitCode != 0)
                throw new InvalidOperationException("Aidy завершился при запуске. Подробности в aidy-ui.log.");
        }
    }

    private static bool ActivateRunningApplication(string executable)
    {
        foreach (var process in Process.GetProcessesByName("WpfApp1"))
        {
            using (process)
            {
                try
                {
                    if (!string.Equals(process.MainModule.FileName, executable, StringComparison.OrdinalIgnoreCase))
                        continue;
                    IntPtr window = process.MainWindowHandle;
                    if (window != IntPtr.Zero)
                    {
                        if (IsIconic(window)) ShowWindowAsync(window, 9);
                        SetForegroundWindow(window);
                    }
                    return true;
                }
                catch (System.ComponentModel.Win32Exception) { }
                catch (InvalidOperationException) { }
            }
        }
        return false;
    }

    private static void ShowError(Exception exception)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(LogPath));
            File.AppendAllText(LogPath, exception + "\r\n", Encoding.UTF8);
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        MessageBox.Show(exception.Message + "\n\nЖурнал запуска:\n" + LogPath,
            "Aidy — ошибка запуска", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}
