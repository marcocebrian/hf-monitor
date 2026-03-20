import json, sys, io
sys.path.insert(0, '.')

def test_solar_fallback():
    """When NOAA is unreachable, should return default f107=120."""
    from unittest.mock import patch
    import urllib.error

    with patch('urllib.request.urlopen', side_effect=urllib.error.URLError('timeout')):
        import importlib, api.solar as solar_mod
        importlib.reload(solar_mod)
        result = solar_mod.get_solar_flux()
    assert result == 120.0
