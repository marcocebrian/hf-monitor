# api/schedule.py
"""
Vercel Python serverless function.
Fetches HFCC + EiBi Spanish-language shortwave schedules,
resolves transmitter coordinates, normalizes day strings,
and returns a JSON array cached 6 hours at the Vercel edge.
"""

import calendar as _cal
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
        raise ValueError(f'No .txt or .csv member found in ZIP archive')


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
TRANSMITTER_SITES = {
'A': (-36.43, 145.40, 'Shepparton Australia'),
    'B': (-22.50, -43.18, 'Petropolis Brazil'),
    'C': (39.90, 116.40, 'Beijing China'),
    'D': (51.20, 6.36, 'Juelich Germany'),
    'E': (39.88, -3.44, 'Noblejas Spain'),
    'F': (46.96, 2.20, 'Issoudun France'),
    'G': (52.32, -2.72, 'Woofferton UK'),
    'H': (47.18, 18.42, 'Szekesfehervar Hungary'),
    'I': (48.25, 11.68, 'Ismaning Germany'),
    'J': (35.83, 140.43, 'Yamata Japan'),
    'K': (35.93, 126.90, 'Kimjae South Korea'),
    'L': (49.75, 6.28, 'Junglinster Luxembourg'),
    'M': (-18.90, 47.50, 'Antananarivo Madagascar'),
    'N': (52.53, 5.28, 'Flevoland Netherlands'),
    'O': (17.67, 54.02, 'Thumrait Oman'),
    'P': (37.95, -8.87, 'Sines Portugal'),
    'Q': (24.25, 54.55, 'Al-Dhafra UAE'),
    'R': (44.33, 26.03, 'Tiganesti Romania'),
    'S': (24.70, 46.70, 'Riyadh Saudi Arabia'),
    'T': (39.65, 32.65, 'Emirler Turkey'),
    'U': (35.75, -119.25, 'Delano USA'),
    'V': (21.03, 105.85, 'Hanoi Vietnam'),
    'W': (54.90, -2.95, 'Skelton UK'),
    'X': (34.34, 108.93, 'Xian China'),
    'Y': (15.35, 44.20, 'Sanaa Yemen'),
    'Z': (-26.55, 28.15, 'Meyerton South Africa'),
    # Common numeric codes for specific/private stations often found in EiBi
    '1': (-38.00, 176.00, 'Rangitaiki New Zealand'),
    '2': (-36.00, 175.00, 'Rankin New Zealand'),
    '3': (35.00, -120.00, 'KBSW USA'),
    '4': (25.00, -80.00, 'WRMI USA'),
    '5': (36.00, -85.00, 'WWCR USA'),
    '6': (36.00, -85.00, 'WBCQ USA'),
    '8': (50.00, 10.00, 'AFN Europe'),
    '9': (30.00, -90.00, 'WRNO USA'),
    'A-A': (43 + 17/60, 77 + 0/60, 'Alma Ata'), # [cite: 1]
    'AAR': (52 + 8/60, 4 + 38/60, 'Alphen aan den Rijn'), # [cite: 2]
    'ABG': (33 + 19/60, 44 + 15/60, 'Abu Ghraib (Bagdadh)'), # [cite: 2]
    'ABH': (26 + 2/60, 50 + 37/60, 'Abu Hayan'), # [cite: 2]
    'ABJ': (5 + 21/60, -(3 + 57/60), 'Abidjan'), # [cite: 2]
    'ABS': (31 + 10/60, 30 + 5/60, 'Abis'), # [cite: 2, 3]
    'ABU': (24 + 23/60, 54 + 17/60, 'Abu Dhabi'), # [cite: 3]
    'ABZ': (30 + 16/60, 31 + 22/60, 'Abu zaabal'), # [cite: 3]
    'ACC': (5 + 31/60, -(0 + 10/60), 'Accra'), # [cite: 3]
    'ACH': (37 + 57/60, 58 + 23/60, 'Achkabad'), # [cite: 3, 4]
    'ADD': (8 + 58/60, 38 + 43/60, 'Addis Ababa'), # [cite: 4]
    'ADH': (45 + 9/60, 6 + 1/60, 'Alpes d Huez'), # [cite: 4]
    'ADR': (33 + 27/60, 36 + 30/60, 'Adra'), # [cite: 4, 5]
    'AGT': (13 + 20/60, 144 + 39/60, 'Agat, Guam'), # [cite: 5]
    'AHW': (31 + 20/60, 48 + 40/60, 'Ahwaz'), # [cite: 5]
    'AIA': (18 + 13/60, -(63 + 1/60), 'Anguilla'), # [cite: 5]
    'AIJ': (33 + 13/60, -(96 + 52/60), 'Denton, TX'), # [cite: 5, 6]
    'AIZ': (23 + 43/60, 92 + 43/60, 'Aizawl'), # [cite: 6]
    'AKA': (31 + 44/60, 36 + 26/60, 'Al Karanah'), # [cite: 6]
    'ALF': (15 + 30/60, 32 + 28/60, 'Al-Fatihab'), # [cite: 6]
    'ALG': (28 + 0/60, 78 + 6/60, 'Aligarh'), # [cite: 6, 7]
    'ALH': (12 + 50/60, 45 + 2/60, 'Al Hiswah'), # [cite: 7]
    'ALI': (-(23 + 42/60), 133 + 53/60, 'Alice Springs'), # [cite: 7]
    'ALK': (25 + 25/60, 51 + 25/60, 'Al khaisah'), # [cite: 7]
    'ALO': (-(10 + 18/60), 150 + 28/60, 'Alotau'), # [cite: 7, 8]
    'AMB': (-(3 + 42/60), 128 + 5/60, 'Ambon'), # [cite: 8]
    'AMM': (31 + 57/60, 35 + 56/60, 'Amman'), # [cite: 8]
    'ANK': (52 + 32/60, 6 + 15/60, 'Ankum'), # [cite: 8]
    'APA': (-(23 + 0/60), -(45 + 0/60), 'Aparecida'), # [cite: 8, 9]
    'ARA': (7 + 4/60, -(70 + 41/60), 'Arauca'), # [cite: 9]
    'ARM': (45 + 0/60, 40 + 49/60, 'Armavir'), # [cite: 9]
    'ARQ': (-(16 + 25/60), -(71 + 32/60), 'Arequipa'), # [cite: 9, 10]
    'ASC': (-(7 + 54/60), -(14 + 23/60), 'Ascension'), # [cite: 10]
    'ASU': (-(25 + 19/60), -(57 + 29/60), 'Asuncion'), # [cite: 10]
    'ATG': (17 + 6/60, -(61 + 48/60), 'Antigua'), # [cite: 10]
    'AUR': (44 + 30/60, -(0 + 9/60), 'Auros'), # [cite: 10, 11]
    'AVL': (38 + 23/60, 23 + 36/60, 'Avlis'), # [cite: 11]
    'AVO': (46 + 11/60, 6 + 46/60, 'Avoriaz Morzine'), # [cite: 11]
    'B-A': (5 + 30/60, 95 + 22/60, 'Banda Aceh'), # [cite: 11]
    'BAB': (32 + 30/60, 44 + 30/60, 'Babel'), # [cite: 11, 12]
    'BAC': (52 + 15/60, 20 + 50/60, 'Babice'), # [cite: 12]
    'BAF': (5 + 28/60, 10 + 24/60, 'Bafoussam'), # [cite: 12]
    'BAI': (1 + 21/60, 172 + 56/60, 'Bairiki'), # [cite: 12, 13]
    'BAK': (40 + 24/60, 49 + 45/60, 'Baku'), # [cite: 13]
    'BAN': (13 + 47/60, 100 + 30/60, 'Bangkok'), # [cite: 13]
    'BAO': (38 + 39/60, 115 + 44/60, 'Baoding'), # [cite: 13]
    'BAT': (1 + 48/60, 9 + 46/60, 'Bata'), # [cite: 13, 14]
    'BAY': (38 + 58/60, 105 + 35/60, 'Bayenhaote'), # [cite: 14]
    'BBY': (19 + 11/60, 72 + 49/60, 'Bombay'), # [cite: 14]
    'BCL': (52 + 6/60, 6 + 32/60, 'Borculo'), # [cite: 14]
    'BCQ': (46 + 20/60, -(67 + 50/60), 'Monticello, ME'), # [cite: 14, 15]
    'BDD': (-(3 + 18/60), 17 + 21/60, 'Bandundu'), # [cite: 15]
    'BDG': (-(6 + 53/60), 107 + 37/60, 'Bandung'), # [cite: 15]
    'BDN': (-(3 + 22/60), 114 + 40/60, 'Bandjarmasin'), # [cite: 15]
    'BEC': (31 + 34/60, -(2 + 21/60), 'Bechar'), # [cite: 15, 16]
    'BEI': (39 + 57/60, 116 + 27/60, 'Beijing'), # [cite: 16]
    'BEL': (-(19 + 54/60), -(43 + 54/60), 'Belo Horizonte'), # [cite: 16]
    'BEN': (-(2 + 44/60), 102 + 18/60, 'Benkule'), # [cite: 16]
    'BEO': (44 + 34/60, 20 + 9/60, 'Beograd'), # [cite: 16, 17]
    'BER': (4 + 34/60, 13 + 43/60, 'Bertoua'), # [cite: 17]
    'BGA': (-(12 + 35/60), 13 + 25/60, 'Benguela'), # [cite: 17]
    'BGL': (13 + 14/60, 77 + 13/60, 'Bangalore'), # [cite: 17, 18]
    'BGZ': (32 + 8/60, 20 + 4/60, 'Benghazi'), # [cite: 18]
    'BHO': (23 + 10/60, 77 + 38/60, 'Bhopal'), # [cite: 18]
    'BI': (42 + 54/60, 74 + 37/60, 'Bichkek'), # [cite: 18]
    'BIA': (-(1 + 0/60), 135 + 30/60, 'Biak'), # [cite: 18, 19]
    'BIB': (49 + 41/60, 8 + 29/60, 'Biblis'), # [cite: 19]
    'BIJ': (44 + 41/60, 19 + 9/60, 'Bijeljina'), # [cite: 19]
    'BJI': (34 + 30/60, 107 + 10/60, 'Baoji'), # [cite: 19]
    'BKA': (-(5 + 25/60), 154 + 40/60, 'Buka'), # [cite: 19, 20]
    'BKO': (12 + 39/60, -(8 + 1/60), 'Bamako'), # [cite: 20]
    'BLA': (-(15 + 42/60), 35 + 2/60, 'Blantyre'), # [cite: 20]
    'BLG': (50 + 16/60, 127 + 30/60, 'Blagovechtchen'), # [cite: 20, 21]
    'BLN': (52 + 30/60, 13 + 20/60, 'Berlin (Deutschlandradio)'), # [cite: 21]
    'BNG': (4 + 21/60, 18 + 35/60, 'Bangui'), # [cite: 21]
    'BOA': (2 + 51/60, -(60 + 43/60), 'Boa Vista'), # [cite: 21]
    'BOC': (14 + 48/60, 120 + 55/60, 'Bocaue'), # [cite: 21]
    'BOG': (4 + 36/60, -(74 + 4/60), 'Bogota'), # [cite: 21, 22]
    'BOH': (34 + 47/60, -(76 + 56/60), 'Newport'), # [cite: 22]
    'BON': (12 + 12/60, -(68 + 18/60), 'Bonaire'), # [cite: 22]
    'BOT': (-(21 + 57/60), 27 + 39/60, 'Moepeng Hill'), # [cite: 22, 23]
    'BOU': (36 + 44/60, 2 + 53/60, 'Bouchaoui'), # [cite: 23]
    'BR': (52 + 20/60, 23 + 35/60, 'Brest'), # [cite: 23]
    'BRA': (-(15 + 51/60), -(47 + 56/60), 'Brasilia'), # [cite: 23]
    'BRE': (53 + 5/60, 8 + 50/60, 'Bremen (RB/SFB)'), # [cite: 23, 24]
    'BRG': (55 + 29/60, 8 + 39/60, 'Bramming'), # [cite: 24]
    'BRI': (-(27 + 19/60), 153 + 1/60, 'Brisbane'), # [cite: 24]
    'BRN': (-(19 + 31/60), 147 + 20/60, 'Brandon'), # [cite: 24]
    'BRT': (33 + 53/60, 35 + 30/60, 'Beirut'), # [cite: 24, 25]
    'BRZ': (-(4 + 15/60), 15 + 18/60, 'Brazzaville'), # [cite: 25]
    'BUD': (47 + 28/60, 19 + 3/60, 'Budapest'), # [cite: 25]
    'BUE': (-(34 + 36/60), -(58 + 22/60), 'Buenos Aires'), # [cite: 25]
    'BUK': (-(0 + 18/60), 100 + 22/60, 'Bukittinggi'), # [cite: 25, 26]
    'BWW': (36 + 17/60, -(86 + 6/60), 'Lebanon, TN'), # [cite: 26]
    'BY': (39 + 21/60, -(84 + 21/60), 'Bethany, OH'), # [cite: 26]
    'CAB': (-(27 + 2/60), -(48 + 39/60), 'Camboriu'), # [cite: 26]
    'CAC': (-(22 + 39/60), -(45 + 1/60), 'Cachoeira Paulista'), # [cite: 26, 27]
    'CAH': (43 + 48/60, 125 + 23/60, 'Changchun'), # [cite: 27]
    'CAK': (39 + 58/60, 32 + 40/60, 'Cakirlar'), # [cite: 27]
    'CAL': (22 + 27/60, 88 + 18/60, 'Calcutta'), # [cite: 27]
    'CAM': (-(20 + 24/60), -(54 + 35/60), 'Campo Grande'), # [cite: 27, 28]
    'CAN': (10 + 5/60, 105 + 46/60, 'Cantho'), # [cite: 28]
    'CAR': (-(24 + 54/60), 113 + 43/60, 'Carnarvon'), # [cite: 28]
    'CCH': (-(46 + 33/60), -(71 + 42/60), 'Chile Chico'), # [cite: 28]
    'CDA': (22 + 13/60, 114 + 15/60, 'Cape Daguliar'), # [cite: 28, 29]
    'CDM': (22 + 44/60, -(98 + 56/60), 'Cd. Mante'), # [cite: 29]
    'CDU': (30 + 42/60, 104 + 0/60, 'Chengdu'), # [cite: 29]
    'CER': (41 + 0/60, 20 + 0/60, 'Cerrik'), # [cite: 29]
    'CGY': (50 + 54/60, -(113 + 52/60), 'Calgary, AB'), # [cite: 29, 30]
    'CHA': (9 + 45/60, -(82 + 54/60), 'Cahuita, Costa Rica'), # [cite: 30]
    'CHC': (37 + 56/60, 127 + 46/60, 'ChunCheon'), # [cite: 30]
    'CHE': (40 + 58/60, 117 + 35/60, 'Chengde'), # [cite: 30]
    'CHI': (28 + 34/60, -(106 + 2/60), 'Chihuahua'), # [cite: 30, 31]
    'CHT': (46 + 14/60, 6 + 50/60, 'Chatel'), # [cite: 31]
    'CK2': (20 + 43/60, 105 + 33/60, 'Xuanmai'), # [cite: 31]
    'CLM': (-(22 + 30/60), -(68 + 55/60), 'Calama'), # [cite: 31]
    'CLS': (37 + 30/60, 14 + 4/60, 'Caltanissetta'), # [cite: 31, 32]
    'CLZ': (10 + 30/60, -(66 + 52/60), 'Calabozo'), # [cite: 32]
    'CNI': (13 + 8/60, 80 + 7/60, 'Chennai'), # [cite: 32]
    'COA': (-(17 + 20/60), -(66 + 20/60), 'Cochabamba'), # [cite: 32]
    'COC': (-(37 + 3/60), -(73 + 10/60), 'Concepcion CHL'), # [cite: 32, 33]
    'COI': (-(45 + 30/60), -(72 + 6/60), 'Coihaique'), # [cite: 33]
    'COL': (6 + 54/60, 79 + 48/60, 'Colombo'), # [cite: 33]
    'COM': (-(11 + 42/60), 43 + 15/60, 'Comores'), # [cite: 33]
    'CON': (9 + 32/60, -(13 + 40/60), 'Conakry'), # [cite: 33, 34]
    'COP': (-(23 + 24/60), -(57 + 27/60), 'Concepcion PRG'), # [cite: 34]
    'COT': (6 + 21/60, 2 + 25/60, 'Cotonou'), # [cite: 34]
    'COY': (-(45 + 24/60), -(72 + 43/60), 'Coyhaique'), # [cite: 34]
    'CRI': (10 + 0/60, -(83 + 30/60), 'Cariari'), # [cite: 34, 35]
    'CUR': (-(25 + 23/60), -(49 + 10/60), 'Curitiba'), # [cite: 35]
    'CUZ': (-(13 + 30/60), -(72 + 0/60), 'Cuzco'), # [cite: 35]
    'CYP': (34 + 43/60, 33 + 19/60, 'Limassol'), # [cite: 35, 36]
    'CZB': (45 + 53/60, 6 + 24/60, 'Clusaz Balme'), # [cite: 36]
    'CZJ': (45 + 54/60, 6 + 28/60, 'Clusaz Jument'), # [cite: 36]
    'DAK': (14 + 39/60, -(17 + 26/60), 'Dakar'), # [cite: 36]
    'DAL': (12 + 41/60, 108 + 3/60, 'Daclac'), # [cite: 36, 37]
    'DAR': (-(12 + 25/60), 136 + 37/60, 'Darwin'), # [cite: 37]
    'DAT': (51 + 39/60, 7 + 20/60, 'Datteln'), # [cite: 37]
    'DB': (38 + 40/60, 68 + 50/60, 'Duchanbe'), # [cite: 37]
    'DBA': (25 + 14/60, 55 + 16/60, 'Dubai'), # [cite: 37, 38]
    'DEA': (45 + 41/60, 16 + 27/60, 'Deanovec'), # [cite: 38]
    'DEL': (28 + 43/60, 77 + 12/60, 'Delhi'), # [cite: 38]
    'DEN': (-(8 + 45/60), 115 + 15/60, 'Denpasar'), # [cite: 38, 39]
    'DES': (-(6 + 50/60), 39 + 14/60, 'Dar-es-Salaam'), # [cite: 39]
    'DHA': (24 + 11/60, 54 + 14/60, 'Dhabayya'), # [cite: 39]
    'DIL': (-(8 + 33/60), 125 + 35/60, 'Dili'), # [cite: 39]
    'DIO': (47 + 25/60, 18 + 57/60, 'Diosd'), # [cite: 39, 40]
    'DIR': (24 + 39/60, 46 + 37/60, 'Diriyya'), # [cite: 40]
    'DJI': (11 + 35/60, 43 + 5/60, 'Djibouti'), # [cite: 40]
    'DKA': (23 + 43/60, 90 + 26/60, 'Dhaka'), # [cite: 40]
    'DL': (35 + 45/60, -(119 + 10/60), 'Delano, CA'), # [cite: 40, 41]
    'DOF': (18 + 54/60, 108 + 39/60, 'Dongfang'), # [cite: 41]
    'DOL': (-(6 + 5/60), 39 + 14/60, 'Dole'), # [cite: 41]
    'DON': (-(18 + 49/60), 34 + 52/60, 'Dondo'), # [cite: 41, 42]
    'DOU': (4 + 4/60, 9 + 41/60, 'Douala'), # [cite: 42]
    'DOV': (33 + 20/60, 35 + 30/60, 'Doves'), # [cite: 42]
    'DRU': (-(9 + 5/60), 143 + 10/60, 'Daru'), # [cite: 42]
    'DRW': (-(12 + 25/60), 130 + 38/60, 'Darwin, NT'), # [cite: 42, 43]
    'DUB': (53 + 21/60, -(6 + 16/60), 'Dublin, Ireland'), # [cite: 43]
    'EJU': (7 + 23/60, -(1 + 22/60), 'Ejura'), # [cite: 43]
    'EKA': (7 + 6/60, 79 + 54/60, 'Ekala'), # [cite: 43]
    'EKB': (56 + 50/60, 60 + 36/60, 'Ekaterinburg'), # [cite: 43, 44]
    'ELB': (52 + 26/60, 5 + 52/60, 'Elburg'), # [cite: 44]
    'EMR': (39 + 29/60, 32 + 51/60, 'Emirler'), # [cite: 44]
    'ENN': (-(27 + 21/60), -(55 + 52/60), 'Encarnacion'), # [cite: 44]
    'ERL': (49 + 33/60, 11 + 1/60, 'Erlangen'), # [cite: 44, 45]
    'ERV': (40 + 10/60, 44 + 30/60, 'Erevan'), # [cite: 45]
    'EWN': (33 + 30/60, -(86 + 28/60), 'Vandiver, AL'), # [cite: 45]
    'FAK': (-(2 + 55/60), 132 + 17/60, 'Fakfak'), # [cite: 45]
    'FBS': (15 + 16/60, 145 + 48/60, 'Marpi, Saipan'), # [cite: 45, 46]
    'FIG': (-(25 + 31/60), -(54 + 30/60), 'Foz do Iguacu'), # [cite: 46]
    'FIL': (-(22 + 18/60), -(60 + 10/60), 'Filadelfia'), # [cite: 46]
    'FLE': (52 + 21/60, 5 + 27/60, 'Flevo'), # [cite: 46]
    'FLI': (44 + 13/60, 12 + 3/60, 'Forli, Italy'), # [cite: 46, 47]
    'FLO': (-(27 + 35/60), -(48 + 31/60), 'Florianopolis'), # [cite: 47]
    'FOR': (-(3 + 46/60), -(38 + 41/60), 'Fortaleza'), # [cite: 47]
    'FRE': (59 + 11/60, 10 + 58/60, 'Frederikstad'), # [cite: 47]
    'FUK': (33 + 33/60, 130 + 27/60, 'Fukuoka'), # [cite: 47, 48]
    'GA': (35 + 42/60, -(77 + 9/60), 'Greenville, NC (Site A)'), # [cite: 48]
    'GAB': (-(1 + 40/60), 13 + 31/60, 'Moyabi'), # [cite: 48]
    'GAL': (46 + 44/60, 26 + 50/60, 'Galbeni'), # [cite: 48, 49]
    'GAR': (9 + 18/60, 13 + 25/60, 'Garoua'), # [cite: 49]
    'GAU': (26 + 11/60, 91 + 50/60, 'Gauhati'), # [cite: 49]
    'GB': (35 + 28/60, -(77 + 12/60), 'Greenville, NC (Site B)'), # [cite: 49]
    'GBN': (-(24 + 34/60), 25 + 58/60, 'Gaborone'), # [cite: 49, 50]
    'GDB': (45 + 56/60, 6 + 26/60, 'Gd. Bornand'), # [cite: 50, 51]
    'GDR': (8 + 46/60, 38 + 40/60, 'Gedja Dera'), # [cite: 51]
    'GEI': (37 + 33/60, -(122 + 14/60), 'Redwood City, CA'), # [cite: 51]
    'GEM': (36 + 24/60, 94 + 59/60, 'Geermu'), # [cite: 51]
    'GIT': (-(3 + 29/60), 29 + 56/60, 'Gitega'), # [cite: 51, 52]
    'GIY': (26 + 25/60, 106 + 36/60, 'Guiyang'), # [cite: 52]
    'GJA': (-(23 + 59/60), -(46 + 15/60), 'Guarujá'), # [cite: 52]
    'GJW': (8 + 47/60, 38 + 39/60, 'Gedja Jewe'), # [cite: 52, 53]
    'GKP': (23 + 52/60, 83 + 28/60, 'Gorakhpur'), # [cite: 53]
    'GOD': (8 + 30/60, -(13 + 14/60), 'Goderich'), # [cite: 53]
    'GOH': (53 + 32/60, 11 + 36/60, 'Goehren'), # [cite: 53]
    'GOI': (-(16 + 43/60), -(49 + 18/60), 'Goiania'), # [cite: 53, 54]
    'GR': (35 + 35/60, -(77 + 22/60), 'Greenville, NC'), # [cite: 54]
    'GRA': (14 + 34/60, -(90 + 31/60), 'Granja Pavon'), # [cite: 54]
    'GRW': (8 + 24/60, 48 + 28/60, 'Garowe'), # [cite: 54]
    'GTG': (34 + 58/60, -(84 + 22/60), 'McCaysville, GA'), # [cite: 54, 55]
    'GTK': (27 + 22/60, 88 + 37/60, 'Gangtok'), # [cite: 55]
    'GUA': (14 + 30/60, -(90 + 20/60), 'Guatemala'), # [cite: 55]
    'GUF': (4 + 54/60, -(52 + 36/60), 'Montsinery'), # [cite: 55]
    'GUW': (26 + 11/60, 91 + 50/60, 'Guwahati'), # [cite: 55, 56]
    'GWE': (-(19 + 26/60), 29 + 51/60, 'Gweru'), # [cite: 56]
    'GYM': (-(10 + 49/60), -(65 + 21/60), 'Guayaramerin'), # [cite: 56]
    'HAB': (23 + 0/60, -(82 + 30/60), 'La Habana'), # [cite: 56]
    'HAN': (20 + 59/60, 105 + 52/60, 'Hanoi'), # [cite: 56, 57]
    'HAR': (9 + 33/60, 44 + 3/60, 'Hargeysa'), # [cite: 57]
    'HB': (55 + 49/60, 13 + 44/60, 'Horby'), # [cite: 57]
    'HBA': (21 + 16/60, 106 + 12/60, 'Ha Bac'), # [cite: 57, 58]
    'HBN': (7 + 21/60, 134 + 31/60, 'Rep. of Palau'), # [cite: 58]
    'HCN': (10 + 51/60, 106 + 38/60, 'Ho Chi Minh V'), # [cite: 58]
    'HEE': (52 + 40/60, 6 + 3/60, 'Heerde'), # [cite: 58]
    'HER': (29 + 4/60, -(110 + 55/60), 'Hermosillo'), # [cite: 58, 59]
    'HEZ': (35 + 6/60, 102 + 54/60, 'Hezuo'), # [cite: 59]
    'HFX': (44 + 41/60, -(63 + 40/60), 'Halifax, NS'), # [cite: 59]
    'HIL': (55 + 53/60, 12 + 16/60, 'Hilleroed'), # [cite: 59]
    'HIR': (34 + 22/60, 132 + 26/60, 'Heroshima'), # [cite: 59, 60]
    'HLR': (49 + 2/60, 119 + 45/60, 'Hailar'), # [cite: 60]
    'HOL': (47 + 52/60, 11 + 44/60, 'Holzkirchen'), # [cite: 60]
    'HON': (-(9 + 25/60), 160 + 3/60, 'Honiara'), # [cite: 60]
    'HRA': (45 + 8/60, -(68 + 34/60), 'Greenbush, ME'), # [cite: 60, 61]
    'HRB': (45 + 49/60, 126 + 52/60, 'Harbin'), # [cite: 61]
    'HRI': (32 + 41/60, -(81 + 8/60), 'Furman, SC'), # [cite: 61]
    'HSB': (21 + 20/60, 105 + 45/60, 'Ha Son Binh'), # [cite: 61]
    'HST': (50 + 40/60, 12 + 41/60, 'Hartenstein Saxony'), # [cite: 61, 62]
    'HSW': (40 + 1/60, -(85 + 57/60), 'Noblesville, IN'), # [cite: 62]
    'HUA': (-(12 + 5/60), -(75 + 10/60), 'Huancayo'), # [cite: 62]
    'HUE': (16 + 25/60, 107 + 40/60, 'Hue'), # [cite: 62]
    'HUH': (41 + 12/60, 111 + 30/60, 'Huhhot'), # [cite: 62, 63]
    'HUN': (-(18 + 47/60), -(66 + 48/60), 'Huanuni'), # [cite: 63]
    'HVI': (55 + 36/60, 12 + 28/60, 'Hvidovre'), # [cite: 63]
    'HWA': (37 + 13/60, 126 + 47/60, 'HwaSung'), # [cite: 63]
    'HYD': (17 + 20/60, 78 + 33/60, 'Hyderabad'), # [cite: 63, 64]
    'IAB': (48 + 24/60, 24 + 56/60, 'IABLUNIV'), # [cite: 64]
    'IAK': (62 + 1/60, 129 + 48/60, 'Iakutsk'), # [cite: 64]
    'IBA': (15 + 20/60, 119 + 58/60, 'Iba'), # [cite: 64, 65]
    'IBI': (-(21 + 43/60), -(48 + 47/60), 'Ibitinga'), # [cite: 65]
    'IBN': (7 + 23/60, 3 + 54/60, 'Ibadan'), # [cite: 65]
    'ICN': (51 + 12/60, 3 + 53/60, 'Overslag'), # [cite: 65]
    'IKO': (7 + 23/60, 3 + 56/60, 'Ikorodu'), # [cite: 65, 66]
    'IMF': (40 + 36/60, -(116 + 36/60), 'Beowawe, NV'), # [cite: 66]
    'IMP': (24 + 37/60, 93 + 54/60, 'Imphal'), # [cite: 66]
    'INB': (39 + 54/60, -(76 + 35/60), 'Red Lion, PA'), # [cite: 66]
    'IPE': (40 + 52/60, -(73 + 55/60), 'Alpine, NJ'), # [cite: 66, 67]
    'IQI': (-(3 + 45/60), -(73 + 12/60), 'Iquitos'), # [cite: 67]
    'IRA': (7 + 32/60, 79 + 30/60, 'Iranawila'), # [cite: 67]
    'IRK': (52 + 18/60, 104 + 18/60, 'Irkutsk'), # [cite: 67]
    'ISH': (55 + 36/60, 12 + 21/60, 'Ishoej'), # [cite: 67, 68]
    'ISL': (33 + 27/60, 73 + 12/60, 'Islamabad'), # [cite: 68]
    'ISM': (48 + 15/60, 11 + 45/60, 'Ismaning (BR)'), # [cite: 68]
    'ISR': (32 + 4/60, 34 + 47/60, 'Jerusalem'), # [cite: 68]
    'ISS': (46 + 57/60, 1 + 59/60, 'Issoudun'), # [cite: 68, 69]
    'ITA': (27 + 4/60, 93 + 36/60, 'Itanagar'), # [cite: 69]
    'IUJ': (46 + 55/60, 143 + 10/60, 'Iujnsakhalinsry'), # [cite: 69]
    'IVF': (48 + 56/60, 24 + 48/60, 'Ivanofrankovsk'), # [cite: 69]
    'JAA': (37 + 2/60, 126 + 51/60, 'JangAn'), # [cite: 69, 70]
    'JAI': (26 + 54/60, 75 + 45/60, 'Jaipur'), # [cite: 70]
    'JAK': (-(6 + 12/60), 106 + 51/60, 'Djakarta'), # [cite: 70]
    'JAM': (32 + 45/60, 75 + 0/60, 'Jammu'), # [cite: 70]
    'JAR': (10 + 45/60, 7 + 33/60, 'Jarji'), # [cite: 70, 71]
    'JAY': (-(2 + 35/60), 140 + 40/60, 'Jayapura'), # [cite: 71]
    'JBR': (47 + 35/60, 19 + 52/60, 'Jaszbereny'), # [cite: 71]
    'JCR': (37 + 26/60, -(86 + 2/60), 'Millerstown, KY'), # [cite: 71]
    'JED': (21 + 32/60, 39 + 10/60, 'Jeddah'), # [cite: 71, 72]
    'JES': (32 + 8/60, -(106 + 35/60), 'Vado, NM'), # [cite: 72]
    'JEY': (18 + 55/60, 82 + 34/60, 'Jeypore'), # [cite: 72]
    'JHR': (30 + 39/60, -(87 + 5/60), 'Milton, FL'), # [cite: 72, 73]
    'JIE': (37 + 26/60, -(86 + 2/60), 'Millerstown, KY'), # [cite: 73]
    'JIG': (53 + 26/60, 49 + 30/60, 'Jigulevsk'), # [cite: 73]
    'JIN': (28 + 7/60, 119 + 39/60, 'Jinhua'), # [cite: 73]
    'JMB': (-(1 + 38/60), 103 + 34/60, 'Jambi'), # [cite: 73, 74]
    'JUL': (50 + 57/60, 6 + 22/60, 'Juelich'), # [cite: 74]
    'JUN': (49 + 40/60, 6 + 19/60, 'Junglinster'), # [cite: 74]
    'K/A': (50 + 30/60, 137 + 5/60, 'Komsomolsk Amur'), # [cite: 74]
    'KAB': (35 + 0/60, 69 + 0/60, 'Kabul'), # [cite: 74, 75]
    'KAC': (24 + 55/60, 67 + 0/60, 'Karachi'), # [cite: 75]
    'KAJ': (3 + 1/60, 101 + 46/60, 'Kajang'), # [cite: 75]
    'KAL': (50 + 26/60, 40 + 40/60, 'Kalatch'), # [cite: 75]
    'KAM': (35 + 46/60, 51 + 27/60, 'Kamalabad'), # [cite: 75, 76]
    'KAR': (9 + 35/60, 1 + 9/60, 'Kara'), # [cite: 76]
    'KAS': (39 + 30/60, 76 + 0/60, 'Kashi'), # [cite: 76]
    'KAT': (27 + 42/60, 85 + 12/60, 'Kathmandu'), # [cite: 76]
    'KAV': (40 + 52/60, 24 + 50/60, 'Kavalla'), # [cite: 76, 77]
    'KAW': (35 + 50/60, 139 + 43/60, 'Tokyo Kawagu'), # [cite: 77]
    'KAZ': (55 + 47/60, 49 + 8/60, 'Kazan'), # [cite: 77]
    'KBD': (29 + 16/60, 47 + 53/60, 'KABD'), # [cite: 77, 78]
    'KCH': (47 + 0/60, 28 + 30/60, 'Kichinev'), # [cite: 78]
    'KDI': (-(3 + 38/60), 125 + 26/60, 'Kendari'), # [cite: 78]
    'KEN': (57 + 25/60, 80 + 56/60, 'Kenga'), # [cite: 78]
    'KGA': (-(5 + 53/60), 22 + 25/60, 'Kananga'), # [cite: 78, 79]
    'KGS': (59 + 27/60, 28 + 43/60, 'Kinghisepp'), # [cite: 79]
    'KHB': (48 + 33/60, 135 + 15/60, 'Khabarovsk'), # [cite: 79]
    'KHR': (50 + 0/60, 36 + 17/60, 'Kharkov'), # [cite: 79]
    'KIG': (-(1 + 53/60), 30 + 4/60, 'Kigali'), # [cite: 79, 80]
    'KIM': (35 + 50/60, 126 + 50/60, 'Kimjae'), # [cite: 80]
    'KIN': (-(4 + 23/60), 15 + 23/60, 'Kinshasa'), # [cite: 80]
    'KIS': (0 + 30/60, 25 + 11/60, 'Kisangani'), # [cite: 80]
    'KKT': (22 + 27/60, 88 + 18/60, 'Kolkata'), # [cite: 80, 81]
    'KLG': (54 + 42/60, 20 + 30/60, 'Kaliningrad'), # [cite: 81]
    'KLL': (50 + 28/60, 6 + 31/60, 'Kall'), # [cite: 81]
    'KLZ': (52 + 44/60, 6 + 59/60, 'Klazienaveen'), # [cite: 81, 82]
    'KMO': (40 + 21/60, 45 + 7/60, 'Kamo'), # [cite: 82]
    'KMP': (0 + 20/60, 32 + 36/60, 'Kampala'), # [cite: 82]
    'KNG': (40 + 58/60, 126 + 36/60, 'Kanggye'), # [cite: 82]
    'KNX': (-(15 + 48/60), 128 + 41/60, 'Kununurra WA'), # [cite: 82, 83]
    'KOH': (25 + 39/60, 94 + 6/60, 'Kohima'), # [cite: 83]
    'KOM': (-(1 + 16/60), 37 + 9/60, 'Koma Rock'), # [cite: 83]
    'KON': (60 + 12/60, 36 + 53/60, 'Konevo'), # [cite: 83]
    'KRA': (1 + 25/60, 103 + 43/60, 'Kranji (SIMCOM)'), # [cite: 83, 84]
    'KRP': (56 + 15/60, 9 + 4/60, 'Karup'), # [cite: 84]
    'KRS': (56 + 1/60, 92 + 54/60, 'Krasnoiarsk'), # [cite: 84]
    'KS': (54 + 55/60, 24 + 0/60, 'Kaunas'), # [cite: 84]
    'K-S': (35 + 54/60, 128 + 49/60, 'Kyung San'), # [cite: 84, 85]
    'KSG': (26 + 55/60, 88 + 19/60, 'Kurseong'), # [cite: 85]
    'KTH': (-(14 + 28/60), 132 + 16/60, 'Katherine'), # [cite: 85]
    'KUJ': (40 + 5/60, 125 + 5/60, 'Kujang'), # [cite: 85]
    'KUM': (32 + 15/60, 130 + 44/60, 'Kumamoto'), # [cite: 85, 86]
    'KUN': (25 + 10/60, 102 + 50/60, 'Kunming'), # [cite: 86]
    'KUP': (-(10 + 10/60), 123 + 30/60, 'Kupang'), # [cite: 86]
    'KUR': (51 + 46/60, 36 + 12/60, 'Kursk'), # [cite: 86, 87]
    'KV': (50 + 27/60, 30 + 13/60, 'Kiev'), # [cite: 87]
    'KVG': (-(2 + 34/60), 150 + 47/60, 'Kavieng'), # [cite: 87]
    'KVI': (59 + 4/60, 5 + 27/60, 'Kvitsoy'), # [cite: 87]
    'KWH': (52 + 18/60, 13 + 37/60, 'Konigs Wusterhausen'), # [cite: 87, 88]
    'KWT': (29 + 31/60, 47 + 41/60, 'Kuwait'), # [cite: 88]
    'LAG': (6 + 34/60, 3 + 21/60, 'Lagos'), # [cite: 88]
    'LAI': (59 + 11/60, 24 + 23/60, 'Laitse'), # [cite: 88]
    'LAM': (49 + 36/60, 8 + 33/60, 'Lampertheim'), # [cite: 88, 89]
    'LAN': (36 + 2/60, 103 + 50/60, 'Lanzhou'), # [cite: 89]
    'LAR': (45 + 33/60, 6 + 30/60, 'Les Arcs'), # [cite: 89]
    'LAW': (-(6 + 40/60), 146 + 54/60, 'Lae'), # [cite: 89, 90]
    'LDA': (44 + 59/60, 6 + 10/60, 'Les Deux Alpes'), # [cite: 90]
    'LDR': (3 + 7/60, 35 + 36/60, 'Lodwar'), # [cite: 90]
    'LEH': (34 + 8/60, 77 + 29/60, 'Leh'), # [cite: 90]
    'LGS': (46 + 10/60, 6 + 40/60, 'Les Gets'), # [cite: 90, 91]
    'LHA': (29 + 30/60, 90 + 59/60, 'Lhasa'), # [cite: 91]
    'LIB': (0 + 25/60, 9 + 26/60, 'Libreville'), # [cite: 91]
    'LIM': (-(12 + 6/60), -(77 + 3/60), 'Lima'), # [cite: 91, 92]
    'LIN': (36 + 52/60, 111 + 40/60, 'Lingshi'), # [cite: 92]
    'LIS': (38 + 45/60, -(8 + 40/60), 'Lisbon-Sao Gabriel'), # [cite: 92]
    'LIT': (49 + 48/60, 16 + 10/60, 'Litomysl'), # [cite: 92]
    'LKW': (26 + 53/60, 81 + 3/60, 'Lucknow'), # [cite: 92, 93]
    'LLA': (-(18 + 37/60), -(67 + 34/60), 'Llallangua'), # [cite: 93]
    'LMS': (45 + 18/60, 6 + 31/60, 'Les Menuires'), # [cite: 93]
    'LNR': (24 + 50/60, -(99 + 34/60), 'Linares'), # [cite: 93]
    'LOB': (-(12 + 18/60), 13 + 36/60, 'Lobito'), # [cite: 93, 94]
    'LOJ': (-(3 + 59/60), -(79 + 12/60), 'Loja'), # [cite: 94]
    'LON': (-(23 + 18/60), -(51 + 13/60), 'Londrina'), # [cite: 94]
    'LOR': (-(11 + 30/60), -(75 + 56/60), 'La Oroya'), # [cite: 94]
    'LPC': (45 + 27/60, 6 + 41/60, 'La Plagne Cham'), # [cite: 94, 95]
    'LPL': (45 + 33/60, 6 + 43/60, 'La Plagne Lac'), # [cite: 95]
    'LPR': (45 + 33/60, 6 + 40/60, 'La Plagne Roch'), # [cite: 95]
    'LPZ': (-(16 + 20/60), -(68 + 7/60), 'La Paz'), # [cite: 95]
    'LRG': (-(2 + 2/60), 143 + 17/60, 'Lorengau'), # [cite: 95, 96]
    'LSO': (-(29 + 19/60), 27 + 32/60, 'Lancers Gap'), # [cite: 96]
    'LUB': (-(11 + 41/60), 27 + 32/60, 'Lubumbashi'), # [cite: 96]
    'LUS': (-(15 + 30/60), 28 + 15/60, 'Lusaka'), # [cite: 96]
    'LUV': (-(15 + 32/60), 28 + 0/60, 'Lusaka (Voice of Hope)'), # [cite: 96, 97]
    'LV': (49 + 50/60, 24 + 0/60, 'Lvov'), # [cite: 97]
    'MAA': (56 + 6/60, 10 + 12/60, 'Marslet'), # [cite: 97]
    'MAC': (-(2 + 20/60), -(78 + 12/60), 'Macas'), # [cite: 97]
    'MAD': (13 + 8/60, 80 + 7/60, 'Madras'), # [cite: 97, 98]
    'MAG': (29 + 10/60, 48 + 2/60, 'Magwa'), # [cite: 98]
    'MAH': (-(17 + 0/60), -(149 + 0/60), 'Mahina'), # [cite: 98]
    'MAK': (24 + 21/60, 54 + 34/60, 'Makta'), # [cite: 98, 99]
    'MAL': (14 + 52/60, 120 + 48/60, 'Malolos'), # [cite: 99]
    'MAN': (-(26 + 34/60), 31 + 59/60, 'Manzini'), # [cite: 99]
    'MAP': (-(25 + 57/60), 32 + 28/60, 'Maputo'), # [cite: 99]
    'MAR': (14 + 41/60, 120 + 59/60, 'Marulas'), # [cite: 99, 100]
    'MAS': (36 + 15/60, 59 + 33/60, 'Mashhad'), # [cite: 100]
    'MAT': (-(8 + 9/60), 115 + 30/60, 'Mataram'), # [cite: 100]
    'MAU': (-(20 + 19/60), 57 + 31/60, 'Mauritius'), # [cite: 100, 101]
    'MAX': (38 + 57/60, -(8 + 46/60), 'Maxoqueira'), # [cite: 101]
    'MBA': (0 + 4/60, 18 + 17/60, 'Mbandaka'), # [cite: 101]
    'MBO': (3 + 45/60, 8 + 47/60, 'Malabo'), # [cite: 101]
    'MBU': (-(6 + 9/60), 23 + 35/60, 'Mbujimayi'), # [cite: 101, 102]
    'MC': (43 + 44/60, 7 + 26/60, 'Monte Carlo'), # [cite: 102]
    'MDC': (-(18 + 48/60), 47 + 36/60, 'Madagascar'), # [cite: 102]
    'MDG': (-(5 + 12/60), 145 + 46/60, 'Madang'), # [cite: 102]
    'MDO': (1 + 12/60, 125 + 26/60, 'Manado'), # [cite: 102, 103]
    'MED': (3 + 35/60, 98 + 41/60, 'Medan'), # [cite: 103]
    'MEK': (13 + 32/60, 39 + 33/60, 'Mekele'), # [cite: 103]
    'MEL': (-(32 + 26/60), -(54 + 13/60), 'Melo'), # [cite: 103]
    'MEN': (-(6 + 8/60), 143 + 39/60, 'Mendi'), # [cite: 103, 104]
    'MER': (-(8 + 33/60), 140 + 27/60, 'Merauke'), # [cite: 104]
    'MET': (20 + 58/60, 105 + 39/60, 'Metri'), # [cite: 104]
    'MEX': (19 + 16/60, -(99 + 3/60), 'Mexico City'), # [cite: 104, 105]
    'MEY': (-(26 + 35/60), 28 + 8/60, 'Meyerton'), # [cite: 105]
    'MGV': (45 + 51/60, 6 + 39/60, 'Megeve'), # [cite: 105]
    'MIL': (45 + 27/60, 9 + 11/60, 'Milano, Italy'), # [cite: 105]
    'MIN': (23 + 29/60, 120 + 27/60, 'Minhsiung'), # [cite: 105, 106]
    'MIR': (4 + 23/60, 113 + 39/60, 'Miri'), # [cite: 106]
    'MIT': (52 + 54/60, 40 + 11/60, 'Mitchurinsk'), # [cite: 106]
    'MKI': (-(0 + 48/60), 134 + 0/60, 'Manokwari'), # [cite: 106]
    'MLA': (48 + 57/60, 8 + 51/60, 'Muehlacker (SDR)'), # [cite: 106, 107]
    'MLK': (40 + 29/60, -(76 + 17/60), 'Bethel, PA'), # [cite: 107]
    'MLT': (35 + 50/60, 14 + 34/60, 'Malta'), # [cite: 107]
    'MNA': (-(3 + 4/60), -(60 + 0/60), 'Manaus'), # [cite: 107]
    'MNG': (12 + 9/60, -(86 + 26/60), 'Managua'), # [cite: 107, 108]
    'MNS': (53 + 53/60, 27 + 31/60, 'Minsk'), # [cite: 108]
    'MOG': (2 + 2/60, 45 + 21/60, 'Mogadishu'), # [cite: 108]
    'MOK': (30 + 3/60, 31 + 15/60, 'Mokattam'), # [cite: 108, 109]
    'MON': (6 + 18/60, -(10 + 40/60), 'Monrovia'), # [cite: 109]
    'MOR': (35 + 34/60, -(5 + 58/60), 'Morocco'), # [cite: 109]
    'MOS': (48 + 0/60, 16 + 28/60, 'Moosbrunn'), # [cite: 109]
    'MRD': (20 + 58/60, -(89 + 30/60), 'Merida'), # [cite: 109, 110]
    'MRJ': (33 + 22/60, 35 + 34/60, 'Marjayoun'), # [cite: 110]
    'MRL': (45 + 22/60, 6 + 33/60, 'Meribel'), # [cite: 110]
    'MRT': (50 + 48/60, 5 + 48/60, 'Margraten'), # [cite: 110]
    'MSK': (55 + 45/60, 37 + 18/60, 'Moskva'), # [cite: 110, 111]
    'MTL': (45 + 24/60, -(73 + 42/60), 'Montreal, QU'), # [cite: 111]
    'MUA': (33 + 49/60, 132 + 45/60, 'Matsuyama'), # [cite: 111]
    'MUG': (39 + 5/60, -(8 + 41/60), 'Muge'), # [cite: 111]
    'MUL': (-(8 + 53/60), 13 + 20/60, 'Mulenvos'), # [cite: 111, 112]
    'MUM': (19 + 11/60, 72 + 49/60, 'Mumbai'), # [cite: 112]
    'MUN': (48 + 6/60, 11 + 36/60, 'Munich'), # [cite: 112]
    'MUR': (68 + 58/60, 32 + 46/60, 'Murmansk'), # [cite: 112, 113]
    'MVD': (-(34 + 47/60), -(56 + 8/60), 'Montevideo'), # [cite: 113]
    'MWV': (-(15 + 43/60), 46 + 26/60, 'Madagascar World Voice'), # [cite: 113]
    'N.N': (56 + 17/60, 44 + 0/60, 'Nishii Novgorod'), # [cite: 113]
    'N/A': (53 + 10/60, 140 + 47/60, 'Nikolaevsk Amur'), # [cite: 113]
    'NAB': (-(3 + 15/60), 135 + 36/60, 'Nabire'), # [cite: 113, 114]
    'NAD': (35 + 3/60, -(2 + 55/60), 'Nador'), # [cite: 114]
    'NAG': (35 + 28/60, 140 + 13/60, 'Tokyo Nagara'), # [cite: 114]
    'NAI': (-(1 + 21/60), 36 + 45/60, 'Nairobi'), # [cite: 114]
    'NAK': (15 + 49/60, 100 + 4/60, 'Nakhon Sawan'), # [cite: 114, 115]
    'NAM': (-(15 + 11/60), 12 + 5/60, 'Namibe'), # [cite: 115]
    'NAN': (28 + 38/60, 115 + 56/60, 'Nanchang'), # [cite: 115]
    'NAU': (52 + 38/60, 12 + 54/60, 'Nauen'), # [cite: 115]
    'NAY': (35 + 3/60, 136 + 58/60, 'Nagoya'), # [cite: 115, 116]
    'NDJ': (12 + 8/60, 15 + 3/60, 'Ndjamena'), # [cite: 116]
    'NIA': (13 + 30/60, 2 + 6/60, 'Niamey'), # [cite: 116]
    'NIJ': (51 + 51/60, 5 + 50/60, 'Nijmegen'), # [cite: 116, 117]
    'NJG': (32 + 2/60, 118 + 44/60, 'Nanjing'), # [cite: 117]
    'NLS': (59 + 45/60, -(151 + 44/60), 'Anchor Pt, Alaska'), # [cite: 117]
    'NNN': (22 + 47/60, 108 + 11/60, 'Nanning'), # [cite: 117]
    'NOB': (39 + 57/60, -(3 + 26/60), 'Noblejas'), # [cite: 117, 118]
    'NOU': (18 + 14/60, -(16 + 0/60), 'Nouakchott'), # [cite: 118]
    'NVS': (55 + 4/60, 82 + 58/60, 'Novosibirsk'), # [cite: 118]
    'OKH': (59 + 30/60, 143 + 0/60, 'Okhotsk'), # [cite: 118]
    'OM': (54 + 59/60, 73 + 23/60, 'Omsk'), # [cite: 118, 119]
    'OMA': (20 + 36/60, 58 + 53/60, 'Masirah'), # [cite: 119]
    'OR': (54 + 31/60, 30 + 27/60, 'Orcha'), # [cite: 119]
    'ORB': (51 + 46/60, 54 + 47/60, 'Orenburg'), # [cite: 119]
    'ORG': (31 + 55/60, 5 + 4/60, 'Ourgla'), # [cite: 119, 120]
    'ORU': (-(17 + 55/60), -(67 + 19/60), 'Oruro'), # [cite: 120]
    'OSA': (34 + 33/60, 135 + 31/60, 'Osaka'), # [cite: 120]
    'OSC': (-(23 + 32/60), -(46 + 47/60), 'Osasco'), # [cite: 120, 121]
    'OTT': (45 + 18/60, -(75 + 45/60), 'Ottawa'), # [cite: 121]
    'OUA': (12 + 22/60, -(1 + 31/60), 'Ouagadougou'), # [cite: 121]
    'OUL': (36 + 43/60, 2 + 57/60, 'Ouled Fayet'), # [cite: 121]
    'OYA': (36 + 17/60, 139 + 48/60, 'Tokyo Oyama'), # [cite: 121, 122]
    'P.K': (52 + 59/60, 158 + 39/60, 'Petropavlo Kam.'), # [cite: 122, 123]
    'PAD': (-(0 + 6/60), 100 + 21/60, 'Padang'), # [cite: 123]
    'PAK': (0 + 15/60, 101 + 30/60, 'Pakanbaru'), # [cite: 123]
    'PAL': (41 + 59/60, 3 + 12/60, 'Playa de Pals'), # [cite: 123]
    'PAN': (15 + 28/60, 73 + 51/60, 'Panaji'), # [cite: 123, 124]
    'PAR': (9 + 20/60, 2 + 38/60, 'Parakou'), # [cite: 124]
    'PAY': (-(45 + 22/60), -(72 + 41/60), 'Pt Aysen'), # [cite: 124]
    'PBL': (11 + 37/60, 92 + 45/60, 'Port Blair'), # [cite: 124]
    'P-C': (3 + 34/60, 98 + 26/60, 'Padang Cermin'), # [cite: 124, 125]
    'PEK': (39 + 55/60, 116 + 25/60, 'Beijing'), # [cite: 125]
    'PEN': (5 + 25/60, 100 + 19/60, 'Penang'), # [cite: 125]
    'PES': (34 + 0/60, 71 + 30/60, 'Peshawar'), # [cite: 125]
    'PGA': (-(0 + 27/60), 117 + 10/60, 'Palangkaraya'), # [cite: 125, 126]
    'PHN': (11 + 34/60, 104 + 51/60, 'Phnom-penh'), # [cite: 126]
    'PHP': (16 + 26/60, 120 + 17/60, 'Poro'), # [cite: 126]
    'PHT': (15 + 21/60, 120 + 37/60, 'Tinang 1'), # [cite: 126]
    'PHX': (15 + 21/60, 120 + 38/60, 'Tinang 2'), # [cite: 126, 127]
    'PIN': (53 + 40/60, 9 + 48/60, 'Pinneberg'), # [cite: 127]
    'PJC': (-(22 + 33/60), -(55 + 45/60), 'P J Caballero'), # [cite: 127]
    'PLD': (42 + 4/60, 24 + 41/60, 'Plovdiv'), # [cite: 127]
    'PLI': (25 + 5/60, 121 + 27/60, 'Pali'), # [cite: 127, 128]
    'PLU': (-(0 + 36/60), 129 + 36/60, 'Palu'), # [cite: 128]
    'PMB': (5 + 49/60, -(55 + 12/60), 'Paramaribo'), # [cite: 128]
    'PMG': (-(0 + 18/60), 104 + 22/60, 'Palembang'), # [cite: 128, 129]
    'PMR': (-(9 + 27/60), 147 + 11/60, 'Pt. Moresby'), # [cite: 129]
    'POD': (50 + 9/60, 15 + 9/60, 'Podebrady'), # [cite: 129]
    'PON': (-(0 + 5/60), 109 + 16/60, 'Pontianak'), # [cite: 129]
    'POP': (-(8 + 45/60), 148 + 14/60, 'Popondetta'), # [cite: 129, 130]
    'POR': (61 + 28/60, 21 + 35/60, 'Pori'), # [cite: 130]
    'POT': (-(19 + 30/60), -(65 + 50/60), 'Potosi'), # [cite: 130]
    'PPR': (18 + 34/60, -(72 + 20/60), 'Pt au Prince'), # [cite: 130]
    'PTA': (-(30 + 3/60), -(51 + 10/60), 'Porto Alegre'), # [cite: 130, 131]
    'PTN': (52 + 14/60, 5 + 37/60, 'Putten'), # [cite: 131]
    'PTR': (-(18 + 20/60), -(69 + 36/60), 'Putre'), # [cite: 131]
    'PTV': (-(8 + 45/60), -(63 + 54/60), 'Porto Velho'), # [cite: 131]
    'PUG': (15 + 28/60, 119 + 50/60, 'Palauig'), # [cite: 131, 132]
    'PUT': (7 + 58/60, 79 + 47/60, 'Puttalam'), # [cite: 132]
    'PVL': (-(17 + 44/60), 168 + 33/60, 'Port Vila'), # [cite: 132]
    'PYO': (39 + 5/60, 125 + 23/60, 'Pyongyang'), # [cite: 132]
    'PZV': (61 + 48/60, 34 + 20/60, 'Petrozavodsk'), # [cite: 132, 133]
    'QIQ': (47 + 2/60, 124 + 3/60, 'Qiqihar'), # [cite: 133]
    'QTA': (30 + 15/60, 67 + 0/60, 'Quetta'), # [cite: 133]
    'QUI': (-(0 + 14/60), -(78 + 20/60), 'Quito'), # [cite: 133, 134]
    'RAB': (-(4 + 13/60), 152 + 12/60, 'Rabaul'), # [cite: 134]
    'RAC': (23 + 24/60, 85 + 22/60, 'Ranchi'), # [cite: 134]
    'RAN': (-(38 + 50/60), 176 + 25/60, 'Rangitaiki'), # [cite: 134]
    'RAS': (26 + 2/60, 50 + 37/60, 'RAS Hayyan'), # [cite: 134, 135]
    'RAV': (44 + 25/60, 12 + 11/60, 'Ravenna, Italy'), # [cite: 135]
    'RAW': (33 + 30/60, 73 + 0/60, 'Rawalpindi'), # [cite: 135]
    'REY': (64 + 5/60, -(21 + 50/60), 'Reykjavik'), # [cite: 135]
    'RHO': (36 + 18/60, 28 + 0/60, 'Rhodes'), # [cite: 135, 136]
    'RIA': (54 + 37/60, 39 + 41/60, 'Riazan'), # [cite: 136]
    'RIG': (56 + 58/60, 24 + 7/60, 'Riga'), # [cite: 136]
    'RIO': (-(22 + 57/60), -(43 + 13/60), 'Rio de Janeiro'), # [cite: 136]
    'RIY': (24 + 30/60, 46 + 23/60, 'Riyadh'), # [cite: 136, 137]
    'RME': (57 + 2/60, 24 + 1/60, 'Riga 2 (Radio Merkurs)'), # [cite: 137]
    'RMI': (27 + 28/60, -(80 + 56/60), 'Okeechobee, FL'), # [cite: 137]
    'RMP': (50 + 48/60, -(2 + 38/60), 'Rampisham'), # [cite: 137]
    'RND': (56 + 31/60, 9 + 55/60, 'Randers'), # [cite: 137, 138]
    'RNO': (29 + 50/60, -(90 + 7/60), 'New Orleans, LA'), # [cite: 138]
    'ROB': (48 + 36/60, 11 + 33/60, 'Rohrbach'), # [cite: 138]
    'ROH': (48 + 1/60, 9 + 7/60, 'Rohrdorf (BR)'), # [cite: 138]
    'ROM': (41 + 48/60, 12 + 31/60, 'Roma'), # [cite: 138, 139]
    'RPR': (-(21 + 8/60), -(47 + 52/60), 'Ribeirao Preto'), # [cite: 139]
    'RSO': (48 + 23/60, 20 + 0/60, 'Rimavska Sobota'), # [cite: 139]
    'RUF': (14 + 8/60, -(17 + 5/60), 'Rufisque'), # [cite: 139]
    'S.P': (59 + 57/60, 30 + 1/60, 'Sanct-Peterburg'), # [cite: 139, 140]
    'SA1': (-(23 + 40/60), -(46 + 45/60), 'Sao Paulo 1'), # [cite: 140]
    'SA2': (-(23 + 33/60), -(46 + 38/60), 'Sao Paulo 2'), # [cite: 140]
    'SA3': (-(23 + 31/60), -(46 + 34/60), 'Sao Paulo 3'), # [cite: 140]
    'SA4': (-(23 + 39/60), -(46 + 36/60), 'Sao Paulo 4'), # [cite: 140, 141]
    'SA5': (-(23 + 33/60), -(46 + 38/60), 'Sao Paulo 5'), # [cite: 141]
    'SAB': (32 + 47/60, 12 + 29/60, 'Sabrata'), # [cite: 141]
    'SAC': (45 + 53/60, -(64 + 19/60), 'Sackville'), # [cite: 141]
    'SAI': (15 + 7/60, 145 + 42/60, 'Agingan Pt, Saipan'), # [cite: 141, 142]
    'SAL': (33 + 9/60, 44 + 35/60, 'Salman Pack'), # [cite: 142]
    'SAM': (53 + 20/60, 50 + 10/60, 'Samara'), # [cite: 142]
    'SAN': (15 + 22/60, 44 + 11/60, 'Sanaa'), # [cite: 142]
    'SAO': (0 + 18/60, 6 + 42/60, 'Sao Tome'), # [cite: 142, 143]
    'SAP': (43 + 5/60, 141 + 36/60, 'Sapporo'), # [cite: 143]
    'SBH': (25 + 52/60, 14 + 50/60, 'Sebha'), # [cite: 143]
    'SCR': (-(19 + 2/60), -(65 + 17/60), 'Sucre'), # [cite: 143, 144]
    'SCV': (45 + 51/60, 6 + 40/60, 'S Gervais'), # [cite: 144]
    'SCZ': (-(17 + 46/60), -(63 + 11/60), 'S Cruz'), # [cite: 144]
    'SDA': (13 + 20/60, 144 + 39/60, 'Agat, Guam'), # [cite: 144]
    'SEB': (23 + 40/60, 58 + 13/60, 'Seeb'), # [cite: 144, 145]
    'SED': (33 + 58/60, 44 + 10/60, 'Salah El Deel'), # [cite: 145]
    'SEM': (-(6 + 59/60), 110 + 23/60, 'Semarang'), # [cite: 145]
    'SER': (-(1 + 48/60), 136 + 26/60, 'Serui'), # [cite: 145]
    'SEY': (-(4 + 36/60), 55 + 28/60, 'Mahe, Seychelles'), # [cite: 145, 146]
    'SFA': (34 + 48/60, 10 + 53/60, 'Sfax'), # [cite: 146]
    'SGH': (31 + 15/60, 121 + 29/60, 'Shanghai'), # [cite: 146]
    'SGO': (-(33 + 27/60), -(70 + 41/60), 'Santiago'), # [cite: 146]
    'SGP': (1 + 24/60, 103 + 51/60, 'Singapore'), # [cite: 146, 147]
    'SHB': (32 + 41/60, -(81 + 8/60), 'Furman, SC'), # [cite: 147]
    'SHG': (25 + 26/60, 91 + 49/60, 'Shillong'), # [cite: 147]
    'SHI': (41 + 21/60, 19 + 35/60, 'Shijak'), # [cite: 147]
    'SHO': (36 + 4/60, 139 + 38/60, 'Tokyo Shobu'), # [cite: 147, 148]
    'SHP': (-(36 + 20/60), 145 + 25/60, 'Shepperton'), # [cite: 148]
    'SIB': (2 + 18/60, 111 + 49/60, 'Sibu'), # [cite: 148]
    'SIM': (31 + 0/60, 77 + 5/60, 'Shimla'), # [cite: 148, 149]
    'SIN': (37 + 57/60, -(8 + 45/60), 'Sines'), # [cite: 149]
    'SIR': (29 + 27/60, 55 + 41/60, 'Sirjan'), # [cite: 149]
    'SIS': (27 + 48/60, -(107 + 35/60), 'Sisoguichi'), # [cite: 149]
    'SIT': (55 + 2/60, 23 + 49/60, 'Sitkunai'), # [cite: 149, 150]
    'SJG': (2 + 34/60, -(72 + 38/60), 'San Jose del Guaviare'), # [cite: 150]
    'SJS': (47 + 34/60, -(52 + 49/60), 'S Johns, NF'), # [cite: 150]
    'SJV': (43 + 55/60, 18 + 20/60, 'Sarajevo'), # [cite: 150]
    'SKA': (-(7 + 33/60), 110 + 48/60, 'Surakarta'), # [cite: 150, 151]
    'SKN': (54 + 44/60, -(2 + 54/60), 'Skelton'), # [cite: 151]
    'SLA': (21 + 58/60, 59 + 27/60, 'A\'Seela'), # [cite: 151]
    'SLD': (15 + 32/60, 38 + 55/60, 'Selae Daro'), # [cite: 151]
    'SLP': (22 + 1/60, -(100 + 59/60), 'S Luis Potosi'), # [cite: 151, 152]
    'SLU': (-(2 + 32/60), -(44 + 3/60), 'Sao Luis'), # [cite: 152]
    'SLV': (-(12 + 58/60), -(38 + 29/60), 'Salvador'), # [cite: 152]
    'SMD': (-(0 + 28/60), 117 + 11/60, 'Samarinda'), # [cite: 152]
    'SMF': (44 + 56/60, 34 + 6/60, 'Simferopol'), # [cite: 152, 153]
    'SMG': (42 + 3/60, 12 + 19/60, 'S. Maria di Galeria'), # [cite: 153]
    'SMR': (-(29 + 44/60), -(53 + 33/60), 'Santa Maria'), # [cite: 153]
    'SNG': (1 + 25/60, 103 + 44/60, 'Kranji (Merlin)'), # [cite: 153]
    'SOF': (42 + 40/60, 23 + 20/60, 'Sofia'), # [cite: 153, 154]
    'SON': (13 + 45/60, -(89 + 45/60), 'Sonsonate'), # [cite: 154]
    'SOR': (-(0 + 52/60), 131 + 25/60, 'Sorong'), # [cite: 154]
    'SOT': (46 + 39/60, 6 + 44/60, 'Sottens'), # [cite: 154]
    'SRI': (34 + 0/60, 74 + 50/60, 'Srinagar'), # [cite: 154, 155]
    'SRN': (54 + 12/60, 45 + 6/60, 'Saransk'), # [cite: 155]
    'SRP': (54 + 54/60, 37 + 25/60, 'Serpukhov'), # [cite: 155]
    'SSV': (13 + 44/60, -(89 + 9/60), 'S Salvador'), # [cite: 155]
    'STA': (1 + 33/60, 110 + 20/60, 'Stapok'), # [cite: 155, 156]
    'STD': (18 + 30/60, -(69 + 57/60), 'Santo Domingo'), # [cite: 156]
    'STM': (-(2 + 26/60), -(54 + 41/60), 'Santarem'), # [cite: 156]
    'STR': (49 + 13/60, 37 + 57/60, 'Starobelsk'), # [cite: 156]
    'SUC': (-(2 + 0/60), -(78 + 0/60), 'Sucua'), # [cite: 156, 157]
    'SUL': (29 + 10/60, 47 + 45/60, 'Sulaibiyah'), # [cite: 157]
    'SUM': (-(11 + 7/60), 13 + 54/60, 'Sumbre'), # [cite: 157]
    'SUR': (-(7 + 13/60), 112 + 43/60, 'Surabaja'), # [cite: 157]
    'SUW': (37 + 16/60, 127 + 1/60, 'Suwon'), # [cite: 157, 158]
    'SVE': (59 + 37/60, 5 + 19/60, 'Sveio'), # [cite: 158]
    'SVK': (61 + 41/60, 50 + 31/60, 'Syktyvkar'), # [cite: 158]
    'SXX': (-(18 + 25/60), -(66 + 35/60), 'Centro Minero Siglo XX'), # [cite: 158]
    'SZG': (38 + 4/60, 114 + 28/60, 'Shijiazhuang'), # [cite: 158, 159]
    'SZV': (47 + 10/60, 18 + 24/60, 'Szekesfehervar'), # [cite: 159]
    'TAC': (41 + 19/60, 69 + 17/60, 'Tashkent'), # [cite: 159]
    'TAI': (25 + 9/60, 121 + 24/60, 'Taipei'), # [cite: 159]
    'TAL': (59 + 27/60, 24 + 47/60, 'Tallinn'), # [cite: 159, 160]
    'TAN': (35 + 48/60, -(5 + 55/60), 'Tangier'), # [cite: 160]
    'TAP': (14 + 57/60, -(92 + 8/60), 'Tapachula'), # [cite: 160]
    'TAR': (-(6 + 28/60), -(76 + 27/60), 'Tarapoto'), # [cite: 160]
    'TBL': (41 + 40/60, 44 + 45/60, 'Tbilisi'), # [cite: 160, 161]
    'TBN': (40 + 39/60, -(112 + 3/60), 'Salt Lake City, UT'), # [cite: 161]
    'TBO': (8 + 5/60, -(76 + 43/60), 'Turbo'), # [cite: 161]
    'TCG': (51 + 22/60, 71 + 3/60, 'Tchoelinograd'), # [cite: 161]
    'TCH': (52 + 5/60, 113 + 20/60, 'Tchita'), # [cite: 161, 162]
    'TCN': (-(18 + 0/60), -(70 + 13/60), 'Tacna'), # [cite: 162]
    'TCO': (1 + 47/60, -(78 + 48/60), 'Tumaco'), # [cite: 162]
    'TEM': (-(38 + 41/60), -(72 + 35/60), 'Temuco'), # [cite: 162, 163]
    'TEN': (-(19 + 40/60), 134 + 10/60, 'Tennant Greek'), # [cite: 163]
    'TGG': (14 + 4/60, -(87 + 14/60), 'Tegucigalpa'), # [cite: 163]
    'TGN': (45 + 27/60, 6 + 54/60, 'Tignes'), # [cite: 163]
    'THE': (40 + 50/60, 23 + 0/60, 'Thessaloniki'), # [cite: 163, 164]
    'THI': (27 + 28/60, 89 + 39/60, 'Thimphu'), # [cite: 164]
    'THU': (17 + 38/60, 53 + 56/60, 'Thumrayt'), # [cite: 164]
    'TIA': (34 + 33/60, 105 + 42/60, 'Tianshui'), # [cite: 164]
    'TIG': (44 + 42/60, 26 + 6/60, 'Tiganesti'), # [cite: 164, 165]
    'TIN': (15 + 3/60, 145 + 36/60, 'Tinian Islands'), # [cite: 165]
    'TJC': (34 + 47/60, -(76 + 53/60), 'Newport,NC'), # [cite: 165]
    'TJK': (-(5 + 24/60), 105 + 15/60, 'Tanjungkarang'), # [cite: 165]
    'TLX': (17 + 15/60, -(97 + 40/60), 'Tlaxiaco'), # [cite: 165, 166]
    'TMI': (44 + 34/60, -(122 + 50/60), 'Lebanon,OR'), # [cite: 166]
    'TOG': (6 + 16/60, 1 + 12/60, 'Togblekope'), # [cite: 166]
    'TOK': (35 + 55/60, 139 + 45/60, 'Tokyo'), # [cite: 166]
    'TOM': (56 + 30/60, 85 + 2/60, 'Tomsk'), # [cite: 166, 167]
    'TOR': (43 + 20/60, -(79 + 38/60), 'Toronto'), # [cite: 167]
    'TRI': (32 + 54/60, 13 + 11/60, 'Tripoli'), # [cite: 167]
    'TRJ': (-(21 + 32/60), -(64 + 45/60), 'Tarija'), # [cite: 167]
    'TRM': (8 + 44/60, 81 + 10/60, 'Trincomalee (Perkara)'), # [cite: 167, 168]
    'TSH': (25 + 13/60, 121 + 29/60, 'Tanshui'), # [cite: 168]
    'TUA': (6 + 11/60, 116 + 12/60, 'Tuaran'), # [cite: 168]
    'TUL': (54 + 12/60, 37 + 48/60, 'Tula'), # [cite: 168]
    'TUM': (-(3 + 32/60), -(80 + 30/60), 'Tumbes'), # [cite: 168, 169]
    'TV': (56 + 52/60, 35 + 35/60, 'Tver'), # [cite: 169]
    'TVD': (8 + 29/60, 76 + 59/60, 'Trivandrum'), # [cite: 169]
    'TWR': (13 + 17/60, 144 + 40/60, 'Agana, Guam'), # [cite: 169]
    'TWW': (36 + 17/60, -(86 + 6/60), 'Lebanon, TN'), # [cite: 169, 170]
    'TXM': (19 + 17/60, -(98 + 26/60), 'Texmelucan'), # [cite: 170]
    'U-B': (47 + 55/60, 107 + 0/60, 'Ulan Bator'), # [cite: 170]
    'UDO': (17 + 25/60, 102 + 48/60, 'Udorn'), # [cite: 170]
    'UJU': (-(5 + 10/60), 119 + 25/60, 'Ujungpandang'), # [cite: 170, 171]
    'ULB': (56 + 57/60, 24 + 16/60, 'Ulbroka'), # [cite: 171]
    'URU': (43 + 35/60, 87 + 30/60, 'Urumqi'), # [cite: 171]
    'VAI': (-(2 + 42/60), 141 + 18/60, 'Vanimo'), # [cite: 171, 172]
    'VAL': (18 + 13/60, -(63 + 1/60), 'The Valley'), # [cite: 172]
    'VAN': (49 + 8/60, -(123 + 12/60), 'Vancouver, BC'), # [cite: 172]
    'VAT': (41 + 54/60, 12 + 27/60, 'Vatican City'), # [cite: 172]
    'VDN': (43 + 39/60, 22 + 40/60, 'Vidin'), # [cite: 172, 173]
    'VIB': (45 + 26/60, 6 + 58/60, 'Val Iseres Bell'), # [cite: 173]
    'VIE': (17 + 58/60, 102 + 33/60, 'Vientiane'), # [cite: 173]
    'VIF': (45 + 26/60, 7 + 1/60, 'Val Isere Form'), # [cite: 173]
    'VIL': (-(25 + 45/60), -(56 + 26/60), 'Villarrica'), # [cite: 173, 174]
    'VIN': (49 + 13/60, 28 + 26/60, 'Vinnitsa'), # [cite: 174]
    'VKO': (48 + 31/60, 17 + 44/60, 'Velke Kostolany'), # [cite: 174]
    'VLD': (43 + 12/60, 131 + 51/60, 'Vladivostok'), # [cite: 174]
    'VLG': (48 + 42/60, 44 + 28/60, 'Volgograd'), # [cite: 174]
    'VLL': (45 + 2/60, 5 + 33/60, 'Villars Lans'), # [cite: 174, 175]
    'VLR': (45 + 9/60, 6 + 25/60, 'Valloire'), # [cite: 175]
    'VN1': (21 + 12/60, 105 + 22/60, 'Sontay'), # [cite: 175]
    'VNI': (21 + 12/60, 105 + 22/60, 'Sontay'), # [cite: 175, 176]
    'VOH': (34 + 15/60, -(118 + 38/60), 'Rancho Simi, CA'), # [cite: 176]
    'VOL': (59 + 12/60, 40 + 6/60, 'Vologda'), # [cite: 176]
    'VOR': (51 + 38/60, 39 + 14/60, 'Voronej'), # [cite: 176]
    'VRN': (43 + 3/60, 27 + 40/60, 'Varna'), # [cite: 176, 177]
    'VRX': (19 + 10/60, -(96 + 7/60), 'Veracruz'), # [cite: 177]
    'VVC': (4 + 9/60, -(73 + 38/60), 'Villavicencio'), # [cite: 177]
    'WAL': (51 + 4/60, 12 + 59/60, 'Waldheim'), # [cite: 177]
    'WAM': (-(3 + 48/60), 139 + 53/60, 'Wamena'), # [cite: 177, 178]
    'WAV': (50 + 44/60, 4 + 34/60, 'Wavre'), # [cite: 178]
    'WBS': (32 + 50/60, -(83 + 38/60), 'Macon, GA'), # [cite: 178]
    'WCR': (36 + 13/60, -(86 + 54/60), 'Nashville, TN'), # [cite: 178]
    'WEL': (-(41 + 5/60), 174 + 50/60, 'Wellington'), # [cite: 178, 179]
    'WER': (48 + 5/60, 10 + 41/60, 'Wertachtal'), # [cite: 179]
    'WEW': (-(3 + 35/60), 143 + 40/60, 'Wewak'), # [cite: 179]
    'WHR': (19 + 2/60, -(155 + 40/60), 'Naalehu, Hawaii'), # [cite: 179]
    'WIN': (-(22 + 33/60), 17 + 13/60, 'Windhoek'), # [cite: 179, 180]
    'WIS': (52 + 40/60, 9 + 46/60, 'Winsen'), # [cite: 180]
    'WIW': (51 + 57/60, 6 + 40/60, 'Winterswijk'), # [cite: 180]
    'WNM': (53 + 12/60, 7 + 19/60, 'Weenermoor'), # [cite: 180]
    'WOF': (52 + 19/60, -(2 + 43/60), 'Woofferton'), # [cite: 180, 181]
    'WRB': (35 + 37/60, -(86 + 1/60), 'Morrison, TN'), # [cite: 181]
    'WUH': (30 + 36/60, 114 + 20/60, 'Wuhan'), # [cite: 181]
    'WWA': (52 + 4/60, 20 + 52/60, 'Warszawa'), # [cite: 181]
    'XIA': (34 + 12/60, 108 + 54/60, 'Xian'), # [cite: 181, 182]
    'XIC': (27 + 49/60, 102 + 14/60, 'Xichang'), # [cite: 182]
    'XIN': (36 + 38/60, 101 + 36/60, 'Xining'), # [cite: 182]
    'XIY': (34 + 49/60, 113 + 23/60, 'Xingyang'), # [cite: 182, 183]
    'YAM': (36 + 10/60, 139 + 50/60, 'Tokyo Yamata'), # [cite: 183]
    'YAN': (16 + 52/60, 96 + 10/60, 'Yangoon'), # [cite: 183]
    'YAO': (3 + 51/60, 11 + 32/60, 'Yaounde'), # [cite: 183]
    'YFR': (27 + 28/60, -(80 + 56/60), 'Obsolete, changed to RMI'), # [cite: 183]
    'YIN': (38 + 30/60, 106 + 12/60, 'Yinchuan'), # [cite: 183, 184]
    'YOG': (-(7 + 47/60), 110 + 26/60, 'Yogyakarta'), # [cite: 184]
    'ZAH': (29 + 28/60, 60 + 53/60, 'Zahedan'), # [cite: 184]
    'ZWO': (52 + 29/60, 6 + 6/60, 'Zwolle') # [cite: 184, 185]


    
}

COUNTRY_CENTERS = {
  # South America
  'ARG': (-34.6, -58.4),      # Argentina
  'BOL': (-16.5, -68.1),      # Bolivia
  'CHL': (-33.4, -70.7),      # Chile
  'COL': (4.7, -74.1),        # Colombia
  'CUB': (23.1, -82.3),       # Cuba
  'EQA': (-1.8, -78.2),       # Ecuador
  'HND': (14.8, -86.3),       # Honduras
  'MEX': (19.4, -99.1),       # Mexico
  'NCG': (12.9, -85.0),       # Nicaragua
  'PRU': (-9.2, -75.0),       # Peru (EiBi uses PRU, not PER)
  'VEN': (10.5, -66.9),       # Venezuela
  
  # Europe
  'ESP': (39.5, -3.7),        # Spain
  'E': (39.5, -3.7),          # Spain (alternate code)
  'ROU': (46.0, 25.0),        # Romania
  'RUS': (61.5, 105.0),       # Russia
  'TUR': (39.0, 35.0),        # Turkey
  'CVA': (41.9, 12.5),        # Vatican City
  'D': (51.2, 10.5),          # Germany
  'F': (46.9, 2.2),           # France
  'G': (52.3, -2.7),          # United Kingdom
  'I': (48.2, 11.7),          # Italy
  'L': (49.8, 6.3),           # Luxembourg
  'N': (52.5, 5.3),           # Netherlands
  'P': (39.5, -8.9),          # Portugal
  'S': (24.7, 46.7),          # Saudi Arabia (also used for Sweden sometimes)
  
  # Asia
  'CHN': (35.9, 104.2),       # China
  'INS': (-2.5, 118.0),       # Indonesia
  'KOR': (37.5, 127.5),       # South Korea
  'KRE': (40.3, 127.5),       # North Korea
  'VTN': (16.0, 106.0),       # Vietnam
  'J': (35.7, 139.7),         # Japan
  'O': (17.7, 54.0),          # Oman
  'Q': (24.3, 54.6),          # UAE
  'Y': (15.4, 44.2),          # Yemen
  
  # North America
  'USA': (37.8, -96.9),       # United States
  'CDN': (56.1, -106.3),      # Canada
  
  # Africa
  'Z': (-26.5, 28.2),         # South Africa
  'M': (-18.9, 47.5),         # Madagascar
  
  # Oceania
  'A': (-36.4, 145.4),        # Australia
  '1': (-38.0, 176.0),        # New Zealand (RNZI)
  '2': (-36.0, 175.0),        # New Zealand (alternate)
  
  # Other common EiBi codes
  'B': (-22.5, -43.2),        # Brazil
  'H': (47.2, 18.4),          # Hungary
  'T': (39.7, 32.7),          # Turkey (alternate)
  'W': (54.9, -2.9),          # United Kingdom (alternate)
  'X': (34.3, 108.9),         # China (alternate)

  # ── Broadcaster codes that appear as LOC in EiBi/HFCC when site is unknown ──
  # These are broadcaster ITU codes used as fallback transmitter identifiers
  'CNR': (39.9, 116.4),       # China National Radio → Beijing area
  'CRI': (39.9, 116.4),       # China Radio International → Beijing area
  'AIR': (28.6,  77.2),       # All India Radio → Delhi
  'VOA': (38.9, -77.0),       # Voice of America → Washington DC (HQ)
  'BBC': (51.5,  -0.1),       # BBC World Service → London
  'DWL': (50.7,   7.1),       # Deutsche Welle → Bonn
  'RFI': (48.9,   2.3),       # Radio France Internationale → Paris
  'REE': (39.9,  -3.4),       # Radio Exterior de España → Noblejas
  'RHC': (23.1, -82.4),       # Radio Habana Cuba → Havana
  'KBS': (37.5, 126.9),       # Korean Broadcasting System → Seoul
  'NHK': (35.7, 139.7),       # NHK Japan → Tokyo
  'RAE': (-34.6, -58.4),      # Radio Argentina al Exterior → Buenos Aires
  'RNZ': (-41.3, 174.8),      # Radio New Zealand → Wellington
  'VAT': (41.9,  12.5),       # Vatican Radio → Vatican City
  'TWR': (12.2, -68.3),       # Trans World Radio → Bonaire
  'AWR': (28.6,  77.2),       # Adventist World Radio (various)
  'FEC': (14.6, 121.0),       # Far East Broadcasting → Philippines
  'RMI': (27.5, -80.9),       # Radio Miami International → Okeechobee FL
}

POTENCIA_TIPICA = {
    'ABS': 100, 'BCQ':  50, 'BEI': 500, 'EMR': 250, 'EWN': 100,
    'GAL': 250, 'GB':  250, 'HAB': 100, 'ISS': 500, 'JAK':  50,
    'KAS': 500, 'KIM': 250, 'MWV': 100, 'NOB': 500, 'PGA': 250,
    'RMI': 100, 'SMG': 500, 'SZG': 500, 'TIG': 250, 'TRT': 500,
    'URU': 500, 'ASC': 250, 'BON': 500, 'CUB': 100, 'CVA': 500,
    'D':   100, 'E':   500, 'F':   500, 'G':   300, 'GUF': 500,
    'HOL': 400, 'IRN': 500, 'MDA': 500, 'MDG': 250, 'PHL': 250,
    'ROU': 250, 'RUS': 250, 'SNG': 250, 'THA': 250, 'TWN': 250,
    'UAE': 500, 'USA': 250, 'UZB': 200,
}


def resolver_sitio_eibi(campo8, itu_pais):
    c = campo8.strip()
    if c.startswith('/'):
        loc = c[1:].split('-')[0].upper()
    else:
        loc = itu_pais.upper()
    if loc not in TRANSMITTER_SITES and loc in COUNTRY_CENTERS:
        lat, lon = COUNTRY_CENTERS[loc]
        TRANSMITTER_SITES[loc] = (lat, lon, loc)
    powr = POTENCIA_TIPICA.get(loc, 100)
    return loc, powr


def _resolve_tx(loc_code):
    """Return (lat_tx, lon_tx) or (None, None) if unknown."""
    if loc_code in TRANSMITTER_SITES:
        lat, lon, _ = TRANSMITTER_SITES[loc_code]
        return lat, lon
    if loc_code in COUNTRY_CENTERS:
        return COUNTRY_CENTERS[loc_code]
    return None, None


def cargar_broadcasters(texto):
    mapa = {}
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith(';'):
            continue
        code = linea[:3].strip()
        name = linea[3:].strip()
        if code and name:
            mapa[code] = name
    return mapa


def parsear_hfcc(texto, mapa, brc_nombres):
    """
    Parse HFCC fixed-width schedule. Adds entries to mapa in-place.
    Key = '{freq}_{utc_start}_{utc_end}'.
    Each entry: { freq, utc_start, utc_end, dias, emisora, fuentes,
                  lat_tx, lon_tx, powr_kw, azimuth }
    """
    añadidas = 0
    for linea in texto.splitlines():
        if not linea or linea.startswith(';'):
            continue
        if len(linea) < 120:
            continue
        lang = linea[102:112].upper()
        if not any(c in lang for c in ('SPA', 'SP', 'ESP')):
            continue

        freq     = linea[1:6].strip()
        try:
            if float(freq) < 2300:
                continue
        except ValueError:
            continue

        utc_start = linea[6:10].strip().zfill(4)
        utc_end   = linea[11:15].strip().zfill(4)
        loc       = linea[47:50].strip().upper()
        try:
            powr_kw = float(linea[53:57].strip() or 0)
        except ValueError:
            powr_kw = 0.0
        try:
            azimuth = int(linea[57:60].strip() or 0)
        except ValueError:
            azimuth = None
        dias_raw  = linea[72:79].strip()
        brc_code  = linea[117:121].strip()
        emisora   = brc_nombres.get(brc_code, brc_code)

        lat_tx, lon_tx = _resolve_tx(loc)
        clave = f'{freq}_{utc_start}_{utc_end}'

        if clave in mapa:
            mapa[clave]['fuentes'].add('HFCC')
        else:
            mapa[clave] = {
                'freq':      freq,
                'utc_start': utc_start,
                'utc_end':   utc_end,
                'dias':      normalizar_dias(dias_raw),
                'emisora':   emisora,
                'fuentes':   {'HFCC'},
                'lat_tx':    lat_tx,
                'lon_tx':    lon_tx,
                'powr_kw':   powr_kw,
                'azimuth':   azimuth,
            }
            añadidas += 1
    return añadidas


def parsear_eibi(texto, mapa):
    """Parse EiBi CSV. HFCC has priority; EiBi only adds or confirms."""
    idiomas_spa = {'spa', 's', 'spanish', 'esp'}
    lector = csv.reader(texto.splitlines(), delimiter=';')
    next(lector, None)
    añadidas = confirmadas = 0
    for fila in lector:
        if len(fila) < 6:
            continue
        if fila[5].strip().lower() not in idiomas_spa:
            continue
        freq = fila[0].strip()
        try:
            if float(freq) < 2300:
                continue
        except ValueError:
            continue
        horario  = fila[1].strip()
        tiempos  = horario.split('-')
        if len(tiempos) != 2:
            continue
        utc_start = tiempos[0].strip().zfill(4)
        utc_end   = tiempos[1].strip().zfill(4)
        dias_raw  = fila[2].strip() if len(fila) > 2 else ''
        itu_pais  = fila[3].strip() if len(fila) > 3 else ''
        emisora   = fila[4].strip() if len(fila) > 4 else 'Desconocida'
        campo_tx  = fila[7].strip() if len(fila) > 7 else ''
        loc, powr = resolver_sitio_eibi(campo_tx, itu_pais)
        lat_tx, lon_tx = _resolve_tx(loc)
        clave = f'{freq}_{utc_start}_{utc_end}'

        if clave in mapa:
            mapa[clave]['fuentes'].add('EiBi')
            confirmadas += 1
        else:
            mapa[clave] = {
                'freq':      freq,
                'utc_start': utc_start,
                'utc_end':   utc_end,
                'dias':      normalizar_dias(dias_raw),
                'emisora':   emisora,
                'fuentes':   {'EiBi'},
                'lat_tx':    lat_tx,
                'lon_tx':    lon_tx,
                'powr_kw':   powr,
                'azimuth':   None,
            }
            añadidas += 1
    return añadidas, confirmadas
