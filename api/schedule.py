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
