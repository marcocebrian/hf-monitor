# api/schedule.py
"""
Vercel Python serverless function.
Fetches HFCC + EiBi Spanish-language shortwave schedules,
resolves transmitter coordinates, normalizes day strings,
and returns a JSON array cached 6 hours at the Vercel edge.
"""

import csv, io, json, math, ssl, urllib.request, urllib.error, zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler

# ── Day normalization ─────────────────────────────────────────────────────────

_DIA_MAP = {'Mo': 1, 'Tu': 2, 'We': 3, 'Th': 4, 'Fr': 5, 'Sa': 6, 'Su': 7}

def normalizar_dias(dias_str):
    """
    Convert any HFCC/EiBi day encoding to HFCC numeric string.
    '1234567'  → '1234567'
    'Mo-Fr'    → '12345'
    'MoWeFr'   → '135'
    ''/'daily' → '1234567'
    """
    d = dias_str.strip() if dias_str else ''
    if not d or d.lower() in ('daily', 'irr', 'irreg', 'alt', 'tent', 'test'):
        return '1234567'
    if d.isdigit():
        return d
    # Range 'Mo-Fr', 'Tu-Sa', 'Fr-Mo'
    if len(d) == 5 and d[2] == '-':
        ini = _DIA_MAP.get(d[:2])
        fin = _DIA_MAP.get(d[3:])
        if ini and fin:
            if ini <= fin:
                return ''.join(str(i) for i in range(ini, fin + 1))
            else:
                return ''.join(str(i) for i in list(range(ini, 8)) + list(range(1, fin + 1)))
    # List 'MoWeFr', 'Sa', 'Su'
    dias_set = []
    i = 0
    while i < len(d) - 1:
        code = d[i:i+2]
        if code in _DIA_MAP:
            dias_set.append(_DIA_MAP[code])
            i += 2
        else:
            i += 1
    if dias_set:
        return ''.join(str(x) for x in sorted(set(dias_set)))
    return '1234567'


# ── Season detection + download utilities ─────────────────────────────────────

import calendar as _cal

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

HEADERS = {'User-Agent': 'HFMonitor/1.0 (shortwave.app)'}
TIMEOUT = 5


def temporada_actual():
    """Return (letra, sufijo_2dig) e.g. ('B', '25') or ('A', '26')."""
    ahora = datetime.now(timezone.utc)
    año = ahora.year

    def ultimo_domingo(y, m):
        ultimo_dia = _cal.monthrange(y, m)[1]
        d = datetime(y, m, ultimo_dia, tzinfo=timezone.utc)
        d = d.replace(day=d.day - (d.weekday() + 1) % 7)
        return d

    cambio_a = ultimo_domingo(año, 3)
    cambio_b = ultimo_domingo(año, 10)

    if ahora < cambio_a:
        return 'B', str(año - 1)[-2:]
    elif ahora < cambio_b:
        return 'A', str(año)[-2:]
    else:
        return 'B', str(año)[-2:]


def _decode(raw):
    for enc in ('utf-8', 'iso-8859-1', 'latin-1'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def descargar_texto(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as r:
        return _decode(r.read())


def descargar_zip_memoria(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as r:
        zip_data = io.BytesIO(r.read())
    with zipfile.ZipFile(zip_data) as z:
        for nombre in z.namelist():
            if nombre.lower().endswith(('.txt', '.csv')):
                return _decode(z.read(nombre))
        return _decode(z.read(z.namelist()[0]))


def intentar_urls(lista_urls, modo='texto'):
    """Try each URL in order; return (text, url_used) or raise."""
    errores = []
    for url in lista_urls:
        try:
            if modo == 'zip':
                return descargar_zip_memoria(url), url
            else:
                return descargar_texto(url), url
        except Exception as e:
            errores.append(f'{url} → {e}')
    raise ConnectionError('All URLs failed:\n' + '\n'.join(errores))
