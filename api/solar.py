from http.server import BaseHTTPRequestHandler
import json, urllib.request, ssl

NOAA_URL = (
    "https://services.swpc.noaa.gov/json/solar-cycle/"
    "observed-solar-cycle-indices.json"
)
HEADERS = {'User-Agent': 'HFMonitor/1.0 (shortwave.app)'}
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def get_solar_flux():
    try:
        req = urllib.request.Request(NOAA_URL, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=5, context=SSL_CTX) as r:
            data = json.loads(r.read().decode('utf-8'))
        if data:
            return float(data[-1]['f10.7'])
    except Exception:
        pass
    return 120.0


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = json.dumps({'f107': get_solar_flux()}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'public, s-maxage=3600')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        pass
