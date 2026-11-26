using System;
using System.Runtime.InteropServices;
using System.Windows.Input;
using System.Windows.Interop;

namespace WpfApp1.Services
{
    public sealed class GlobalHotkeyService : IDisposable
    {
        private const int WhKeyboardLl = 13;
        private const int WmKeyDown = 0x0100;
        private const int WmKeyUp = 0x0101;
        private const int WmSysKeyDown = 0x0104;
        private const int WmSysKeyUp = 0x0105;

        private readonly LowLevelKeyboardProc _hookProc;
        private IntPtr _hookHandle = IntPtr.Zero;
        private int _targetVirtualKey;
        private bool _isPressed;

        public GlobalHotkeyService()
        {
            _hookProc = HookCallback;
            UpdateKey(Key.LeftCtrl);
        }

        public event Action? HotkeyDown;
        public event Action? HotkeyUp;

        public bool Enabled { get; set; }

        public void Start()
        {
            if (_hookHandle != IntPtr.Zero)
            {
                return;
            }

            _hookHandle = SetWindowsHookEx(WhKeyboardLl, _hookProc, IntPtr.Zero, 0);
        }

        public void UpdateKey(Key key)
        {
            var normalized = key == Key.None || key == Key.System ? Key.LeftCtrl : key;
            _targetVirtualKey = KeyInterop.VirtualKeyFromKey(normalized);
            _isPressed = false;
        }

        public void Dispose()
        {
            if (_hookHandle != IntPtr.Zero)
            {
                UnhookWindowsHookEx(_hookHandle);
                _hookHandle = IntPtr.Zero;
            }
        }

        private IntPtr HookCallback(int nCode, IntPtr wParam, IntPtr lParam)
        {
            if (nCode >= 0 && Enabled)
            {
                var message = wParam.ToInt32();
                var isDown = message == WmKeyDown || message == WmSysKeyDown;
                var isUp = message == WmKeyUp || message == WmSysKeyUp;

                if (isDown || isUp)
                {
                    var info = Marshal.PtrToStructure<KbdLlHookStruct>(lParam);
                    if ((int)info.vkCode == _targetVirtualKey)
                    {
                        if (isDown)
                        {
                            if (!_isPressed)
                            {
                                _isPressed = true;
                                HotkeyDown?.Invoke();
                            }
                        }
                        else if (_isPressed)
                        {
                            _isPressed = false;
                            HotkeyUp?.Invoke();
                        }
                    }
                }
            }

            return CallNextHookEx(_hookHandle, nCode, wParam, lParam);
        }

        private delegate IntPtr LowLevelKeyboardProc(int nCode, IntPtr wParam, IntPtr lParam);

        [StructLayout(LayoutKind.Sequential)]
        private struct KbdLlHookStruct
        {
            public uint vkCode;
            public uint scanCode;
            public uint flags;
            public uint time;
            public UIntPtr dwExtraInfo;
        }

        [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        private static extern IntPtr SetWindowsHookEx(
            int idHook,
            LowLevelKeyboardProc lpfn,
            IntPtr hMod,
            uint dwThreadId);

        [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool UnhookWindowsHookEx(IntPtr hhk);

        [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        private static extern IntPtr CallNextHookEx(
            IntPtr hhk,
            int nCode,
            IntPtr wParam,
            IntPtr lParam);
    }
}
