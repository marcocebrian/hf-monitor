# HF Monitor · Español

A live shortwave radio broadcast monitor focused on Spanish-language stations. Applies a custom HF propagation model to score reception likelihood for any city in the world, updated on every page load.

**Live:** https://hf-monitor.vercel.app *(replace with your Vercel URL)*

---

## What it does

- Fetches schedule data from **HFCC** (authoritative) and **EiBi** (complementary) — two industry-standard shortwave broadcast databases
- Calculates propagation scores in the browser using a custom HF model (distance, day/night path, solar flux, band optimality, transmitter power, beam azimuth)
- Lets you search for any city worldwide and instantly see reception scores for all stations from that location
- Shows which broadcasts are live right now, with a 60-second auto-refresh
- Displays local time for your chosen city alongside UTC schedules
- Dark-themed dashboard, fully responsive

---

## Architecture

```
SpanishSW/
├── api/
│   ├── schedule.py     # Vercel serverless — fetches & parses HFCC + EiBi
│   └── solar.py        # Vercel serverless — proxies NOAA solar flux (F10.7)
├── public/
│   └── index.html      # Full frontend: dark theme + JS propagation engine
├── src/
│   └── lista_emisoras_avanzado.py   # Original local script (reference only)
├── tests/
│   └── test_schedule.py
├── requirements.txt
└── vercel.json
```

**Data flow:**

1. Browser loads `index.html` → animated loading overlay appears
2. Two parallel requests fire: `GET /api/schedule` and `GET /api/solar`
3. `/api/schedule` fetches the HFCC ZIP + EiBi CSV server-side, parses them, filters for Spanish-language HF entries (≥ 2300 kHz), and returns a JSON array
4. `/api/solar` proxies the NOAA observed solar cycle index and returns `{ "f107": 143.2 }`
5. Both responses resolve → overlay dismissed, table renders
6. If a city was previously selected (stored in `localStorage`), propagation scores are calculated immediately

---

## Propagation model

All propagation math runs client-side in JavaScript, ported from the Python original. The engine scores each broadcast 0–100 and assigns a label:

| Score | Label |
|---|---|
| ≥ 70 | Alta |
| ≥ 45 | Media |
| ≥ 20 | Baja |
| < 20 | Improbable |

Factors considered:
- **Distance** (haversine great-circle) — base score by distance tier
- **Day/night path** — checks solar time at the orthodromic midpoint of the tx→rx path
- **Solar flux (F10.7)** — scales the optimal frequency window up/down
- **Band optimality** — how well the broadcast frequency matches the optimal band for the path distance
- **Transmitter power** — log-scaled contribution
- **Beam azimuth** — HFCC fixed-beam alignment vs. the computed tx→rx bearing
- **Hard filters** — paths > 13,000 km, very short paths, night-time MUF exceeded, low-frequency long-path

City geocoding uses [Nominatim](https://nominatim.openstreetmap.org) (no API key required). Timezone conversion uses [tz-lookup](https://github.com/darkskyapp/tz-lookup) for DST-aware local time display.

---

## Data sources

| Source | Update frequency | Edge cache |
|---|---|---|
| [HFCC](https://www.hfcc.org) | Seasonal (A: Mar–Oct, B: Oct–Mar) | 6 hours |
| [EiBi](http://eibispace.de) | Weekly | 6 hours |
| [NOAA solar flux](https://www.swpc.noaa.gov) | Daily | 1 hour |

The server detects the current ITU season automatically and fetches the correct schedule file. HFCC is the authoritative source; EiBi adds entries not in HFCC and confirms overlapping ones.

---

## Deployment (Vercel Hobby — free)

### Requirements

- A [Vercel](https://vercel.com) account (no credit card needed for Hobby tier)
- A GitHub account

### Steps

```bash
git clone https://github.com/marcocebrian/hf-monitor.git
cd hf-monitor
git remote set-url origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

Then:

1. Go to **vercel.com** → **Add New Project** → import your repo
2. Framework preset: **Other**
3. Build command: *(leave empty)*
4. Output directory: *(leave empty)*
5. Click **Deploy**

Vercel reads `vercel.json` automatically. No environment variables are required.

### `vercel.json`

```json
{
  "functions": {
    "api/schedule.py": { "runtime": "python3.12", "maxDuration": 10 },
    "api/solar.py":    { "runtime": "python3.12", "maxDuration": 5  }
  },
  "rewrites": [
    { "source": "/", "destination": "/public/index.html" }
  ]
}
```

### Cold start note

The first request to `/api/schedule` after an idle period downloads the HFCC ZIP + EiBi CSV (typically 3–6 seconds). With the 6-hour edge cache, this happens at most a few times per day. The loading animation covers the latency.

---

## Running locally

```bash
pip install pytest
pytest tests/ -v
```

For full local testing with live APIs, install the Vercel CLI:

```bash
npm i -g vercel
vercel dev
# → http://localhost:3000
```

---

## Vercel Hobby limits

| Limit | Value |
|---|---|
| Bandwidth | 100 GB / month |
| Function invocations | 100,000 / month |
| Max function duration | 10 s |
| Domains | Free `.vercel.app` subdomain |

---

## License

MIT
