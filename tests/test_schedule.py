import sys
sys.path.insert(0, '.')

def test_normalizar_dias_daily():
    from api.schedule import normalizar_dias
    assert normalizar_dias('daily')   == '1234567'
    assert normalizar_dias('')        == '1234567'
    assert normalizar_dias('irr')     == '1234567'

def test_normalizar_dias_hfcc_numeric():
    from api.schedule import normalizar_dias
    assert normalizar_dias('1234567') == '1234567'
    assert normalizar_dias('15')      == '15'
    assert normalizar_dias('246')     == '246'

def test_normalizar_dias_eibi_range():
    from api.schedule import normalizar_dias
    assert normalizar_dias('Mo-Fr') == '12345'
    assert normalizar_dias('Tu-Sa') == '23456'
    assert normalizar_dias('Sa-Mo') == '671'    # wraps Sunday (Sa=6, Su=7, Mo=1)

def test_normalizar_dias_eibi_list():
    from api.schedule import normalizar_dias
    assert normalizar_dias('MoWeFr') == '135'
    assert normalizar_dias('Sa')     == '6'
    assert normalizar_dias('Su')     == '7'

def test_solar_fallback():
    """When NOAA is unreachable, should return default f107=120."""
    from unittest.mock import patch
    import urllib.error
    import api.solar as solar_mod

    with patch('api.solar.urllib.request.urlopen', side_effect=urllib.error.URLError('timeout')):
        result = solar_mod.get_solar_flux()
    assert result == 120.0
