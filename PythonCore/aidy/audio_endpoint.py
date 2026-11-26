"""Default speaker volume through Windows Core Audio using comtypes only.

pycaw is not a project dependency, so system.set_volume_percent could never
work; these few interface declarations replace it (read and set).
"""
from ctypes import POINTER, c_float, c_int, c_uint, c_void_p
from ctypes.wintypes import BOOL, DWORD

import comtypes
from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

CLSID_MMDeviceEnumerator = GUID('{BCDE0395-E52F-467C-8E3D-C4579291692E}')
E_RENDER, E_MULTIMEDIA = 0, 1


class IAudioEndpointVolume(IUnknown):
    _iid_ = GUID('{5CDF2C82-841E-4546-9722-0CF74078229A}')
    _methods_ = [
        COMMETHOD([], HRESULT, 'RegisterControlChangeNotify', (['in'], c_void_p, 'pNotify')),
        COMMETHOD([], HRESULT, 'UnregisterControlChangeNotify', (['in'], c_void_p, 'pNotify')),
        COMMETHOD([], HRESULT, 'GetChannelCount', (['out'], POINTER(c_uint), 'pnChannelCount')),
        COMMETHOD([], HRESULT, 'SetMasterVolumeLevel', (['in'], c_float, 'fLevelDB'),
                  (['in'], POINTER(GUID), 'pguidEventContext')),
        COMMETHOD([], HRESULT, 'SetMasterVolumeLevelScalar', (['in'], c_float, 'fLevel'),
                  (['in'], POINTER(GUID), 'pguidEventContext')),
        COMMETHOD([], HRESULT, 'GetMasterVolumeLevel', (['out'], POINTER(c_float), 'pfLevelDB')),
        COMMETHOD([], HRESULT, 'GetMasterVolumeLevelScalar', (['out'], POINTER(c_float), 'pfLevel')),
        COMMETHOD([], HRESULT, 'SetChannelVolumeLevel', (['in'], c_uint, 'nChannel'), (['in'], c_float, 'fLevelDB'),
                  (['in'], POINTER(GUID), 'pguidEventContext')),
        COMMETHOD([], HRESULT, 'SetChannelVolumeLevelScalar', (['in'], c_uint, 'nChannel'), (['in'], c_float, 'fLevel'),
                  (['in'], POINTER(GUID), 'pguidEventContext')),
        COMMETHOD([], HRESULT, 'GetChannelVolumeLevel', (['in'], c_uint, 'nChannel'),
                  (['out'], POINTER(c_float), 'pfLevelDB')),
        COMMETHOD([], HRESULT, 'GetChannelVolumeLevelScalar', (['in'], c_uint, 'nChannel'),
                  (['out'], POINTER(c_float), 'pfLevel')),
        COMMETHOD([], HRESULT, 'SetMute', (['in'], BOOL, 'bMute'), (['in'], POINTER(GUID), 'pguidEventContext')),
        COMMETHOD([], HRESULT, 'GetMute', (['out'], POINTER(BOOL), 'pbMute')),
    ]


class IMMDevice(IUnknown):
    _iid_ = GUID('{D666063F-1587-4E43-81F1-B948E807363F}')
    _methods_ = [
        COMMETHOD([], HRESULT, 'Activate', (['in'], POINTER(GUID), 'iid'), (['in'], DWORD, 'dwClsCtx'),
                  (['in'], c_void_p, 'pActivationParams'), (['out'], POINTER(POINTER(IUnknown)), 'ppInterface')),
    ]


class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID('{A95664D2-9614-4F35-A746-DE8DB63617E6}')
    _methods_ = [
        COMMETHOD([], HRESULT, 'EnumAudioEndpoints', (['in'], c_int, 'dataFlow'), (['in'], DWORD, 'dwStateMask'),
                  (['out'], POINTER(c_void_p), 'ppDevices')),
        COMMETHOD([], HRESULT, 'GetDefaultAudioEndpoint', (['in'], c_int, 'dataFlow'), (['in'], c_int, 'role'),
                  (['out'], POINTER(POINTER(IMMDevice)), 'ppEndpoint')),
    ]


def _endpoint():
    try:
        comtypes.CoInitialize()
    except OSError:
        pass  # this thread already uses another COM apartment model; COM is usable
    enumerator = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)
    device = enumerator.GetDefaultAudioEndpoint(E_RENDER, E_MULTIMEDIA)
    return device.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None).QueryInterface(IAudioEndpointVolume)


def get_master():
    """(percent 0-100, muted) of the default speakers."""
    volume = _endpoint()
    return round(volume.GetMasterVolumeLevelScalar() * 100), bool(volume.GetMute())


def set_master(percent):
    _endpoint().SetMasterVolumeLevelScalar(max(0, min(100, int(percent))) / 100.0, None)
