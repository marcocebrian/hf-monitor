import sys
sys.path.insert(0, '.')

def test_solar_fallback():
    """When NOAA is unreachable, should return default f107=120."""
    from unittest.mock import patch
    import urllib.error
    import api.solar as solar_mod

    with patch('api.solar.urllib.request.urlopen', side_effect=urllib.error.URLError('timeout')):
        result = solar_mod.get_solar_flux()
    assert result == 120.0
