// WpfApp1/App.xaml.cs
using System.Windows;
using System.Threading;
using System.Security.Principal;
using System.Windows.Media.Animation;

namespace WpfApp1
{
    public partial class App : Application
    {
        private Mutex? _instanceMutex;

        protected override void OnStartup(StartupEventArgs e)
        {
            // Leave CPU time for local speech inference on integrated-graphics
            // laptops. The translucent glow does not need 60 redraws per second.
            Timeline.DesiredFrameRateProperty.OverrideMetadata(typeof(Timeline), new PropertyMetadata(30));
            var user = WindowsIdentity.GetCurrent().User?.Value ?? "current-user";
            _instanceMutex = new Mutex(true, $"Local\\Aidy.Desktop.{user}", out var firstInstance);
            if (!firstInstance)
            {
                _instanceMutex.Dispose();
                _instanceMutex = null;
                Shutdown();
                return;
            }
            base.OnStartup(e);
        }

        protected override void OnExit(ExitEventArgs e)
        {
            _instanceMutex?.ReleaseMutex();
            _instanceMutex?.Dispose();
            base.OnExit(e);
        }
    }
}
