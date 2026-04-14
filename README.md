# DORIS Tracker

Multi-device camera tracking system via Iridium / RockBLOCK satellite messaging.

## Architecture

- **Backend**: FastAPI (Python) with SQLAlchemy ORM
- **Frontend**: Single-page Leaflet map with Bootstrap 5
- **Database**: SQLite (local dev) / Supabase PostgreSQL (production)
- **Deployment**: DigitalOcean App Platform + Supabase free tier

## Local Development

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cd backend
python main.py
```

- API: http://localhost:8000
- Tracker map: http://localhost:8000/ui
- Swagger docs: http://localhost:8000/docs

### Test with fake data

With the server running:

```bash
cd backend
python test_webhook.py
```

## API Routes

- `POST /rockblock-webhook` -- RockBLOCK webhook (form-encoded)
- `GET /api/devices` -- All devices with latest position
- `GET /api/devices/{imei}/messages` -- Message history for a device
- `GET /api/messages/recent?hours=24` -- Recent messages across all devices

## Device Configuration

Edit `backend/devices.json` to map RockBLOCK IMEIs to friendly names:

```json
{
  "300234010753370": {"name": "DORIS 3"},
  "300234010753371": {"name": "DORIS 4"}
}
```

Restart the app after changes.

## Message Format

Devices send plain-text payloads via Iridium SBD:

```
LAT:21.432552,LON:-157.789331,ALT:20.5,SAT:4,V:14.93,LEAK:0,MAXD:1.1m
```

## Production Deployment

1. Create a Supabase project (free tier) and copy the PostgreSQL connection string
2. Push this branch to GitHub
3. Create a DigitalOcean App Platform app using `.do/app.yaml`
4. Set `DATABASE_URL` to the Supabase connection string in app settings
5. Configure Rock Seven webhook URL to `https://<app>.ondigitalocean.app/rockblock-webhook`
