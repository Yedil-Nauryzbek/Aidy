using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace WpfApp1.Services
{
    public sealed class AudioDeviceService
    {
        private const int MaxProductNameLength = 32;
        private const uint MmSysErrNoError = 0;

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct WaveInCaps
        {
            public ushort ManufacturerId;
            public ushort ProductId;
            public uint DriverVersion;

            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = MaxProductNameLength)]
            public string ProductName;

            public uint Formats;
            public ushort Channels;
            public ushort Reserved;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct WaveOutCaps
        {
            public ushort ManufacturerId;
            public ushort ProductId;
            public uint DriverVersion;

            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = MaxProductNameLength)]
            public string ProductName;

            public uint Formats;
            public ushort Channels;
            public ushort Reserved;
            public uint Support;
        }

        [DllImport("winmm.dll")]
        private static extern uint waveInGetNumDevs();

        [DllImport("winmm.dll", CharSet = CharSet.Unicode)]
        private static extern uint waveInGetDevCaps(UIntPtr deviceId, out WaveInCaps caps, uint capsSize);

        [DllImport("winmm.dll")]
        private static extern uint waveOutGetNumDevs();

        [DllImport("winmm.dll", CharSet = CharSet.Unicode)]
        private static extern uint waveOutGetDevCaps(UIntPtr deviceId, out WaveOutCaps caps, uint capsSize);

        public IReadOnlyList<string> GetInputDevices()
        {
            var devices = new List<string>();
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            var count = waveInGetNumDevs();
            var capsSize = (uint)Marshal.SizeOf<WaveInCaps>();
            for (uint i = 0; i < count; i++)
            {
                var result = waveInGetDevCaps((UIntPtr)i, out var caps, capsSize);
                if (result != MmSysErrNoError)
                {
                    continue;
                }

                var name = (caps.ProductName ?? string.Empty).Trim();
                if (string.IsNullOrWhiteSpace(name) || !seen.Add(name))
                {
                    continue;
                }

                devices.Add(name);
            }

            return devices;
        }

        public IReadOnlyList<string> GetOutputDevices()
        {
            var devices = new List<string>();
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            var count = waveOutGetNumDevs();
            var capsSize = (uint)Marshal.SizeOf<WaveOutCaps>();
            for (uint i = 0; i < count; i++)
            {
                var result = waveOutGetDevCaps((UIntPtr)i, out var caps, capsSize);
                if (result != MmSysErrNoError)
                {
                    continue;
                }

                var name = (caps.ProductName ?? string.Empty).Trim();
                if (string.IsNullOrWhiteSpace(name) || !seen.Add(name))
                {
                    continue;
                }

                devices.Add(name);
            }

            return devices;
        }
    }
}
