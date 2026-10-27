from flask import Flask, render_template, request, jsonify
import sqlite3, os, json
from datetime import datetime, timezone
from pathlib import Path
import re
import secrets
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "authority.db"
app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT,
            vehicle_id TEXT NOT NULL DEFAULT 'UNKNOWN',
            route TEXT NOT NULL DEFAULT 'Unknown route',
            stop_name TEXT NOT NULL DEFAULT 'Unknown location',
            event_type TEXT NOT NULL DEFAULT 'crowd',
            severity TEXT NOT NULL DEFAULT 'medium',
            occupancy INTEGER,
            capacity INTEGER,
            message TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'bus_system',
            status TEXT NOT NULL DEFAULT 'NEW',
            created_at TEXT NOT NULL,
            acknowledged_at TEXT,
            resolved_at TEXT,
            callback_url TEXT
        );
        CREATE TABLE IF NOT EXISTS vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_id TEXT UNIQUE NOT NULL,
            route TEXT NOT NULL DEFAULT 'Unassigned',
            status TEXT NOT NULL DEFAULT 'AVAILABLE',
            driver_name TEXT NOT NULL DEFAULT '',
            last_location TEXT NOT NULL DEFAULT 'Depot',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS allocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id INTEGER,
            vehicle_id TEXT NOT NULL,
            route TEXT NOT NULL,
            assigned_to TEXT NOT NULL DEFAULT '',
            eta_minutes INTEGER NOT NULL DEFAULT 10,
            status TEXT NOT NULL DEFAULT 'ASSIGNED',
            created_at TEXT NOT NULL,
            FOREIGN KEY(alert_id) REFERENCES alerts(id)
        );
        """)
        alert_columns = {row["name"] for row in con.execute("PRAGMA table_info(alerts)")}
        if "callback_url" not in alert_columns:
            con.execute("ALTER TABLE alerts ADD COLUMN callback_url TEXT")
        count = con.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]
        if count == 0:
            con.executemany("""INSERT INTO vehicles(vehicle_id,route,status,driver_name,last_location,updated_at)
                VALUES(?,?,?,?,?,?)""", [
                ("TN-RESERVE-101", "Standby", "AVAILABLE", "Standby Driver A", "Central Depot", now_iso()),
                ("TN-RESERVE-102", "Standby", "AVAILABLE", "Standby Driver B", "North Depot", now_iso()),
                ("TN-RESERVE-103", "Standby", "AVAILABLE", "Standby Driver C", "South Depot", now_iso()),
                ("TN-BUS-204", "Route 21G", "ON_ROUTE", "Driver Demo", "Anna Nagar", now_iso()),
            ])

@app.get("/")
def index():
    return render_template("index.html")

@app.get("/api/dashboard")
def dashboard():
    with db() as con:
        alerts = [
            {key: value for key, value in dict(row).items() if key != "callback_url"}
            for row in con.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT 100")
        ]
        vehicles = [dict(r) for r in con.execute("SELECT * FROM vehicles ORDER BY vehicle_id")]
        allocations = [dict(r) for r in con.execute("""
            SELECT a.*, al.stop_name, al.event_type FROM allocations a
            LEFT JOIN alerts al ON al.id=a.alert_id ORDER BY a.id DESC LIMIT 50
        """)]
        totals = {
            "active_alerts": con.execute("SELECT COUNT(*) FROM alerts WHERE status != 'RESOLVED'").fetchone()[0],
            "emergencies": con.execute("SELECT COUNT(*) FROM alerts WHERE event_type IN ('medical_emergency','driver_injured','safety_threat') AND status != 'RESOLVED'").fetchone()[0],
            "available_buses": con.execute("SELECT COUNT(*) FROM vehicles WHERE status='AVAILABLE'").fetchone()[0],
            "crowd_events": con.execute("SELECT COUNT(*) FROM alerts WHERE event_type IN ('crowd','overcrowding','congestion')").fetchone()[0],
        }
        # Group crowd events by hour from local ISO timestamp; malformed values safely skipped.
        rows = con.execute("SELECT created_at FROM alerts WHERE event_type IN ('crowd','overcrowding','congestion')").fetchall()
        hours = [0] * 24
        for row in rows:
            try:
                h = datetime.fromisoformat(row["created_at"]).hour
                hours[h] += 1
            except Exception:
                pass
        # recurring locations
        hotspots = [dict(r) for r in con.execute("""
            SELECT stop_name, route, COUNT(*) AS events,
                   MAX(created_at) AS last_seen
            FROM alerts WHERE event_type IN ('crowd','overcrowding','congestion')
            GROUP BY stop_name, route ORDER BY events DESC LIMIT 8
        """)]
    return jsonify({"alerts": alerts, "vehicles": vehicles, "allocations": allocations,
                    "totals": totals, "hourly": hours, "hotspots": hotspots})

def validate_driver_callback_url(callback_url):
    try:
        parsed = urlsplit(callback_url)
        hostname = (parsed.hostname or "").lower()
        allowed_hosts = {
            host.strip().lower()
            for host in os.environ.get("DRIVER_STATUS_CALLBACK_HOSTS", "").split(",")
            if host.strip()
        }
        valid = (
            parsed.scheme == "https"
            and hostname in allowed_hosts
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and not parsed.fragment
            and bool(parsed.path)
        )
    except ValueError:
        valid = False
    return valid

class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, new_url):
        return None

def notify_driver(callback_url, payload):
    request_data = json.dumps(payload).encode("utf-8")
    callback_request = Request(
        callback_url,
        data=request_data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with build_opener(NoRedirectHandler).open(callback_request, timeout=5) as response:
            if 200 <= response.status < 300:
                return {"sent": True}
            return {"sent": False, "error": f"Driver callback returned HTTP {response.status}"}
    except HTTPError as error:
        return {"sent": False, "error": f"Driver callback returned HTTP {error.code}"}
    except (URLError, TimeoutError, OSError) as error:
        return {"sent": False, "error": f"Driver callback failed: {error}"}

@app.post("/api/alerts")
def receive_alert():
    data = request.get_json(silent=True) or request.form.to_dict()
    source = str(data.get("source") or "bus_system").strip().lower()[:40]
    callback_url = str(data.get("callback_url") or "").strip()
    if source == "driver":
        if not callback_url:
            return jsonify({"ok": False, "error": "callback_url is required for driver alerts"}), 400
        if not validate_driver_callback_url(callback_url):
            return jsonify({
                "ok": False,
                "error": "callback_url must use HTTPS and a host configured in DRIVER_STATUS_CALLBACK_HOSTS",
            }), 400
    vehicle_id = str(data.get("vehicle_id") or data.get("bus_id") or "UNKNOWN")[:80]
    event_type = str(data.get("event_type") or "crowd").lower()[:50]
    allowed = {"crowd", "overcrowding", "congestion", "medical_emergency", "driver_injured",
               "vehicle_breakdown", "safety_threat", "door_obstruction", "other"}
    if event_type not in allowed:
        event_type = "other"
    severity = str(data.get("severity") or ("critical" if event_type in {"medical_emergency","driver_injured","safety_threat"} else "high" if event_type in {"overcrowding","congestion","door_obstruction"} else "medium")).lower()
    if severity not in {"low","medium","high","critical"}:
        severity = "medium"
    try: occupancy = int(data.get("occupancy")) if data.get("occupancy") not in (None, "") else None
    except (ValueError, TypeError): occupancy = None
    try: capacity = int(data.get("capacity") or data.get("maximum_capacity")) if data.get("capacity") or data.get("maximum_capacity") else None
    except (ValueError, TypeError): capacity = None
    created = str(data.get("timestamp") or now_iso())[:80]
    with db() as con:
        cur = con.execute("""INSERT INTO alerts(event_id,vehicle_id,route,stop_name,event_type,severity,occupancy,capacity,message,source,status,created_at,callback_url)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            str(data.get("event_id") or "")[:100], vehicle_id,
            str(data.get("route") or "Unknown route")[:100],
            str(data.get("stop_name") or data.get("location") or "Unknown location")[:160],
            event_type, severity, occupancy, capacity,
            str(data.get("message") or data.get("details") or "")[:1000],
            source, "NEW", created, callback_url or None))
        alert_id = cur.lastrowid
    return jsonify({"ok": True, "alert_id": alert_id, "status": "NEW", "received_at": now_iso()}), 201

@app.post("/api/alerts/<int:alert_id>/status")
def alert_status(alert_id):
    data = request.get_json(silent=True) or {}
    status = str(data.get("status") or "").upper()
    if status not in {"ACKNOWLEDGED", "RESOLVED"}:
        return jsonify({"ok": False, "error": "status must be ACKNOWLEDGED or RESOLVED"}), 400
    with db() as con:
        row = con.execute(
            "SELECT id, event_id, vehicle_id, source, callback_url FROM alerts WHERE id=?",
            (alert_id,),
        ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "Alert not found"}), 404
        updated_at = now_iso()
        if status == "ACKNOWLEDGED":
            con.execute("UPDATE alerts SET status=?, acknowledged_at=? WHERE id=?", (status, updated_at, alert_id))
        else:
            con.execute("UPDATE alerts SET status=?, resolved_at=? WHERE id=?", (status, updated_at, alert_id))
    notification = None
    if row["source"] == "driver":
        if row["callback_url"]:
            notification = notify_driver(row["callback_url"], {
                "event": "alert.status.updated",
                "alert_id": alert_id,
                "event_id": row["event_id"],
                "vehicle_id": row["vehicle_id"],
                "status": status,
                "updated_at": updated_at,
            })
        else:
            notification = {"sent": False, "error": "No callback URL is registered for this alert"}
    return jsonify({"ok": True, "id": alert_id, "status": status, "status_notification": notification})

@app.delete("/api/alerts/<int:alert_id>")
def delete_alert(alert_id):
    with db() as con:
        cur = con.execute("DELETE FROM alerts WHERE id=?", (alert_id,))
    return jsonify({"ok": cur.rowcount > 0})

@app.delete("/api/alerts")
def clear_alerts():
    with db() as con:
        con.execute("DELETE FROM alerts")
    return jsonify({"ok": True})

@app.post("/api/vehicles")
def add_vehicle():
    data = request.get_json(silent=True) or {}
    vehicle_id = str(data.get("vehicle_id") or "").strip()[:80]
    if not vehicle_id:
        return jsonify({"ok": False, "error": "vehicle_id is required"}), 400
    with db() as con:
        try:
            con.execute("""INSERT INTO vehicles(vehicle_id,route,status,driver_name,last_location,updated_at)
              VALUES(?,?,?,?,?,?)""", (vehicle_id, str(data.get("route") or "Standby"),
              str(data.get("status") or "AVAILABLE").upper(), str(data.get("driver_name") or ""),
              str(data.get("last_location") or "Depot"), now_iso()))
        except sqlite3.IntegrityError:
            return jsonify({"ok": False, "error": "Vehicle ID already exists"}), 409
    return jsonify({"ok": True}), 201

@app.post("/api/drivers/provision")
def provision_driver():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "A JSON object is required"}), 400
    configured_token = os.environ.get("AUTHORITY_CALLBACK_TOKEN", "").strip()
    if not configured_token:
        return jsonify({
            "ok": False,
            "error": "AUTHORITY_CALLBACK_TOKEN is not configured on the authority service",
        }), 503
    provided_token = request.headers.get("Authorization", "")
    if not configured_token or not secrets.compare_digest(
        provided_token, f"Bearer {configured_token}"
    ):
        return jsonify({"ok": False, "error": "Invalid driver provisioning key"}), 401

    portal_url = os.environ.get("DRIVER_PORTAL_URL", "").strip().rstrip("/")
    if not portal_url:
        return jsonify({"ok": False, "error": "DRIVER_PORTAL_URL is not configured"}), 503
    try:
        parsed_url = urlsplit(portal_url)
        local_http = parsed_url.scheme == "http" and parsed_url.hostname in {"localhost", "127.0.0.1"}
        valid_url = (
            (parsed_url.scheme == "https" or local_http)
            and bool(parsed_url.hostname)
            and (local_http or parsed_url.port in (None, 443))
            and parsed_url.path in ("", "/")
            and parsed_url.username is None
            and parsed_url.password is None
            and not parsed_url.query
            and not parsed_url.fragment
        )
    except ValueError:
        valid_url = False
    if not valid_url:
        return jsonify({
            "ok": False,
            "error": "DRIVER_PORTAL_URL must be the HTTPS driver service base URL only, without /login or any other path",
        }), 503

    username = str(data.get("username") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]{3,50}", username):
        return jsonify({
            "ok": False,
            "error": "Username must be 3-50 characters: letters, numbers, dot, underscore or hyphen.",
        }), 400
    display_name = str(data.get("display_name") or "").strip()
    if not display_name or len(display_name) > 100:
        return jsonify({"ok": False, "error": "Driver name is required and must be at most 100 characters."}), 400

    password = secrets.token_urlsafe(24)
    remote_payload = json.dumps({
        "username": username,
        "password": password,
        "display_name": display_name,
    }).encode("utf-8")
    remote_request = Request(
        f"{portal_url}/api/authority/drivers",
        data=remote_payload,
        headers={
            "Authorization": f"Bearer {configured_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with build_opener(NoRedirectHandler).open(remote_request, timeout=10) as response:
            response_body = response.read()
            response_status = response.status
    except HTTPError as error:
        response_body = error.read()
        response_status = error.code
    except (URLError, TimeoutError, OSError) as error:
        return jsonify({"ok": False, "error": f"Could not contact the driver portal: {error}"}), 502

    try:
        portal_response = json.loads(response_body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        portal_response = {}
    if response_status == 401:
        return jsonify({
            "ok": False,
            "error": "The driver portal rejected the shared key. Set the same AUTHORITY_CALLBACK_TOKEN on both Render services, then redeploy both.",
        }), 502
    if response_status in {301, 302, 303, 307, 308}:
        return jsonify({
            "ok": False,
            "error": "The driver portal redirected the provisioning request. Set DRIVER_PORTAL_URL to the driver service base URL only (for example https://tn-transmit-smart-crowd-ai.onrender.com), without /login, then redeploy the authority service.",
        }), 502
    if not isinstance(portal_response, dict) or response_status != 201 or portal_response.get("ok") is not True:
        if not isinstance(portal_response, dict):
            portal_response = {}
        error = portal_response.get("error")
        if not isinstance(error, str) or not error:
            error = f"Driver portal returned HTTP {response_status}"
        return jsonify({"ok": False, "error": error}), response_status if 400 <= response_status < 600 else 502

    return jsonify({
        "ok": True,
        "username": username,
        "display_name": display_name,
        "password": password,
    }), 201

@app.post("/api/allocations")
def allocate_bus():
    data = request.get_json(silent=True) or {}
    vehicle_id = str(data.get("vehicle_id") or "")
    try: alert_id = int(data.get("alert_id")) if data.get("alert_id") else None
    except (ValueError, TypeError): alert_id = None
    try: eta = max(0, min(240, int(data.get("eta_minutes") or 10)))
    except (ValueError, TypeError): eta = 10
    with db() as con:
        vehicle = con.execute("SELECT * FROM vehicles WHERE vehicle_id=?", (vehicle_id,)).fetchone()
        if not vehicle:
            return jsonify({"ok": False, "error": "Vehicle not found"}), 404
        if vehicle["status"] != "AVAILABLE":
            return jsonify({"ok": False, "error": "This bus is not marked AVAILABLE"}), 409
        alert = con.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone() if alert_id else None
        route = str(data.get("route") or (alert["route"] if alert else vehicle["route"]))
        cur = con.execute("""INSERT INTO allocations(alert_id,vehicle_id,route,assigned_to,eta_minutes,status,created_at)
          VALUES(?,?,?,?,?,?,?)""", (alert_id, vehicle_id, route, str(data.get("assigned_to") or "Control Room"),
          eta, "ASSIGNED", now_iso()))
        con.execute("UPDATE vehicles SET status='ALLOCATED', route=?, updated_at=? WHERE vehicle_id=?", (route, now_iso(), vehicle_id))
        allocation_id = cur.lastrowid
    return jsonify({"ok": True, "allocation_id": allocation_id, "vehicle_id": vehicle_id, "route": route}), 201

@app.post("/api/allocations/<int:allocation_id>/status")
def allocation_status(allocation_id):
    data = request.get_json(silent=True) or {}
    status = str(data.get("status") or "").upper()
    if status not in {"EN_ROUTE", "ARRIVED", "CANCELLED", "COMPLETED"}:
        return jsonify({"ok": False, "error": "Unsupported allocation status"}), 400
    with db() as con:
        row = con.execute("SELECT vehicle_id FROM allocations WHERE id=?", (allocation_id,)).fetchone()
        if not row: return jsonify({"ok": False, "error": "Allocation not found"}), 404
        con.execute("UPDATE allocations SET status=? WHERE id=?", (status, allocation_id))
        if status in {"CANCELLED", "COMPLETED"}:
            con.execute("UPDATE vehicles SET status='AVAILABLE', updated_at=? WHERE vehicle_id=?", (now_iso(), row["vehicle_id"]))
        else:
            con.execute("UPDATE vehicles SET status='ALLOCATED', updated_at=? WHERE vehicle_id=?", (now_iso(), row["vehicle_id"]))
    return jsonify({"ok": True, "status": status})

@app.delete("/api/vehicles/<path:vehicle_id>")
def delete_vehicle(vehicle_id):
    with db() as con:
        cur = con.execute("DELETE FROM vehicles WHERE vehicle_id=?", (vehicle_id,))
    return jsonify({"ok": cur.rowcount > 0})

@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "TN Transmit Transport Authority", "time": now_iso()})

init_db()
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)
