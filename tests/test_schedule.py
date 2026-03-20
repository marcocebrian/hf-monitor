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

def test_temporada_actual_returns_valid():
    from api.schedule import temporada_actual
    letra, sufijo = temporada_actual()
    assert letra in ('A', 'B')
    assert len(sufijo) == 2 and sufijo.isdigit()

def test_solar_fallback():
    """When NOAA is unreachable, should return default f107=120."""
    from unittest.mock import patch
    import urllib.error
    import api.solar as solar_mod

    with patch('api.solar.urllib.request.urlopen', side_effect=urllib.error.URLError('timeout')):
        result = solar_mod.get_solar_flux()
    assert result == 120.0


# ── Parser fixtures ────────────────────────────────────────────────────────────
# Lines are padded to 180 chars with exact column positions matching real HFCC.
# Position reference (0-indexed slices used by parsear_hfcc):
#   freq [1:6], start [6:10], end [11:15], loc [47:50], power [53:57],
#   azimuth [57:60], days [72:79], lang [102:112], broadcaster [117:121]

SAMPLE_HFCC = (
    ";HFCC sample\n"
    " 151100000 0200                                NOB    500180            1234567                       SPA            RNE                                                            \n"
    "  96501800 2000                                EMR    250  0            1234567                       SPA            RHC                                                            \n"
)

SAMPLE_EIBI = """kHz;UTC;Days;ITU;Station;Language;Target;TxSite;Persist;Start;End
15110;0000-0200;1234567;E;Radio Nacional Espana;spa;Eu;;d;0329;1026
 6000;1800-2000;1234567;CUB;Radio Habana Cuba;spa;AM;;d;0329;1026
"""


def test_parsear_hfcc_returns_entries():
    from api.schedule import parsear_hfcc
    mapa = {}
    parsear_hfcc(SAMPLE_HFCC, mapa, {})
    assert len(mapa) == 2
    entry = mapa.get('15110_0000_0200')
    assert entry is not None
    assert entry['freq'] == '15110'
    assert entry['utc_start'] == '0000'
    assert entry['utc_end'] == '0200'
    assert 'HFCC' in entry['fuentes']
    assert entry['powr_kw'] == 500.0


def test_parsear_eibi_adds_secondary():
    from api.schedule import parsear_hfcc, parsear_eibi
    mapa = {}
    parsear_hfcc(SAMPLE_HFCC, mapa, {})
    parsear_eibi(SAMPLE_EIBI, mapa)
    # 15110 confirmed by EiBi
    assert 'EiBi' in mapa['15110_0000_0200']['fuentes']
    # 6000 added by EiBi (not in HFCC)
    eibi_only = mapa.get('6000_1800_2000')
    assert eibi_only is not None
    assert eibi_only['fuentes'] == {'EiBi'}


def test_lat_tx_resolved_for_known_site():
    from api.schedule import parsear_hfcc, TRANSMITTER_SITES
    mapa = {}
    parsear_hfcc(SAMPLE_HFCC, mapa, {})
    entry = mapa['15110_0000_0200']
    # NOB = Noblejas Spain, should be in TRANSMITTER_SITES
    assert entry['lat_tx'] is not None
    assert entry['lon_tx'] is not None
