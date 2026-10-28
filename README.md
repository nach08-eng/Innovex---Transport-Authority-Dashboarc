# TN Transmit Transport Authority

A separate Flask + SQLite authority dashboard prototype for receiving bus/AI alerts, acknowledging emergencies, tracking recurring crowded locations, and assigning available standby buses.

## Requirements
- Python 3.10+
- Internet is not required after Flask is installed.

## Run on Windows PowerShell
```powershell
py --list
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```
Use the Python version actually installed on your computer; change `-3.13` as needed.
Open http://127.0.0.1:5001

## Deploy a demo

GitHub Pages cannot run this project directly: it hosts static files, while this dashboard requires a Flask server and SQLite database for its API and data.

To deploy a demo on Render, push the project files to a GitHub repository, then create a new **Blueprint** in Render and select that repository. Render will use [render.yaml](./render.yaml) to build and start the Flask service. When deployment completes, open the service URL; `/health` is used as the health check.

The included configuration uses Render's free web service. Its local SQLite database is temporary and may be erased when the service restarts or redeploys. This demo configuration is not suitable for operational dispatch or durable alert history. Use a persistent database and an appropriately secured production deployment before relying on it.

## Add driver login accounts
The authority dashboard can provision accounts for the separate driver portal. In Render, set `DRIVER_PORTAL_URL` on the authority service to the driver service's base URL only (for example `https://tn-transmit-smart-crowd-ai.onrender.com`), not its `/login` URL and with no other path. Set `AUTHORITY_CALLBACK_TOKEN` to the exact same secret on both the authority service and the driver portal; keep it private. The driver portal must also have its own `FLASK_SECRET_KEY`. After changing either service's variables, redeploy both services before creating accounts.

Open the authority dashboard over HTTPS and use **Add driver login**. Enter the driver's name, a username (3-50 letters, numbers, dots, underscores or hyphens), and the same `AUTHORITY_CALLBACK_TOKEN` value. The authority generates a strong password and provisions the account through the driver's existing authenticated API. The password is only shown once, is not saved by the authority, and is stored as a hash by the driver portal. Copy it and deliver it to the driver securely. An “Invalid driver provisioning key” response means the entered key does not match the authority service's configured value; if the driver portal key differs, the dashboard reports that the portal rejected it.

## Demo data
Three reserve buses and one on-route bus are seeded as illustrative demo records. Replace them with verified fleet data before operational use.

## Connect the bus project
The authority server exposes `POST /api/alerts`. Send driver alerts to the deployed Render service URL, for example `https://<your-render-service>.onrender.com/api/alerts`. GitHub hosts the source code; the Render service URL is the live API endpoint. For local testing, use `http://127.0.0.1:5001/api/alerts`.

Example:
```json
{
  "event_id": "TNCA-0001",
  "vehicle_id": "TN-TRANSMIT-DEMO-01",
  "route": "Route 21G",
  "stop_name": "Anna Nagar",
  "event_type": "overcrowding",
  "severity": "high",
  "occupancy": 46,
  "capacity": 45,
  "source": "bus_ai",
  "message": "Configured crowd threshold exceeded"
}
```

Driver alerts must use `"source": "driver"`, include the authenticated driver's `driver_username`, and include the driver's alert-status callback URL:
```json
{
  "event_id": "TNCA-0002",
  "vehicle_id": "TN-TRANSMIT-DEMO-01",
  "route": "Route 21G",
  "event_type": "vehicle_breakdown",
  "severity": "high",
  "source": "driver",
  "driver_username": "driver01",
  "callback_url": "https://tn-transmit-smart-crowd-ai.onrender.com/api/authority/alert-status",
  "message": "Vehicle stopped near Anna Nagar"
}
```

The Render Blueprint allowlists the driver portal host in `DRIVER_STATUS_CALLBACK_HOSTS`. Driver callback URLs must use HTTPS and an allowlisted host. The authority sends status callbacks authenticated with the shared `AUTHORITY_CALLBACK_TOKEN`. When an alert is acknowledged or resolved, the portal stores a targeted update for that driver, which appears in the driver's shared updates feed while their dashboard is open. The status API response reports whether delivery succeeded; callback failures do not undo the authority's status update.

Allowed event types include `crowd`, `overcrowding`, `congestion`, `medical_emergency`, `driver_injured`, `vehicle_breakdown`, `safety_threat`, `door_obstruction`, and `other`.

## API summary
- `GET /api/dashboard` dashboard data
- `POST /api/alerts` receive bus/AI or driver alerts
- `POST /api/alerts/<id>/status` acknowledge/resolve an alert and notify the driver's portal
- `DELETE /api/alerts/<id>` delete one alert
- `DELETE /api/alerts` clear alert history
- `POST /api/vehicles` add a fleet vehicle
- `POST /api/allocations` assign an available bus to an alert/route
- `POST /api/allocations/<id>/status` update assignment status
- `GET /health` service health

## Safety/security notes
This is a local prototype, not a production dispatch service. Before deployment add authentication and role-based access, HTTPS, API tokens/signatures, audit logging, rate limits, backups, privacy controls and real driver/vehicle identity verification. Medical alerts need a monitored escalation process and must not rely solely on this prototype. Never send physical door-control commands from this authority dashboard.
