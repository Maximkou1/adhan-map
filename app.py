from flask import Flask, request, jsonify, send_from_directory
import pandas as pd
import math
from datetime import datetime, timedelta
import pytz
import os
import time

app = Flask(__name__)

FOLDER_PATH = os.path.dirname(os.path.abspath(__file__))
CSV_FILE = os.path.join(FOLDER_PATH, 'mosques_list.csv')

if os.path.exists(CSV_FILE):
    df = pd.read_csv(CSV_FILE)
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.dropna(subset=['lat', 'lon'])
    df = df[pd.to_numeric(df['lat'], errors='coerce').notna()]
    df = df[pd.to_numeric(df['lon'], errors='coerce').notna()]
    print(f"*** Loaded {len(df)} mosques")
else:
    print("CSV file not found!")
    df = pd.DataFrame()

ADHAN_DURATION_MINUTES = 5  # minutes
PRAYER_COLORS = {
    "Fajr": "#ff8fa3", "Dhuhr": "#f7b801", "Asr": "#f18701",
    "Maghrib": "#f35b04", "Isha": "#3d348b"
}

stats_cache = {"data": None, "timestamp": 0, "ttl": 30}  # 30 secs


def get_solar_prayer_lon(lat, prayer_name, date_dt):
    day_of_year = date_dt.timetuple().tm_yday
    declination = 23.45 * math.sin(math.radians(360 / 365 * (day_of_year - 81)))
    p_name = prayer_name.lower()

    target_angle = 0
    if p_name == "fajr":
        target_angle = -18.0
    elif p_name == "dhuhr":
        target_angle = 0.0
    elif p_name == "maghrib":
        target_angle = -0.83
    elif p_name == "isha":
        target_angle = -17.0
    elif p_name == "asr":
        try:
            diff_rad = math.radians(abs(lat - declination))
            if diff_rad > math.radians(80):
                return None
            shadow_ratio = 1.0 + math.tan(diff_rad)
            target_angle = math.degrees(math.atan(1.0 / shadow_ratio))
            if target_angle < 1.0:
                return None
        except (ZeroDivisionError, ValueError):
            return None

    try:
        lat_r, decl_r, alt_r = map(math.radians, [lat, declination, target_angle])
        cos_h = (math.sin(alt_r) - math.sin(lat_r) * math.sin(decl_r)) / (math.cos(lat_r) * math.cos(decl_r))
        if cos_h > 1 or cos_h < -1:
            return None
        h = math.degrees(math.acos(cos_h))
        angle = math.radians(360 / 365 * (day_of_year - 81))
        eot = 9.87 * math.sin(2 * angle) - 7.53 * math.cos(angle)
        time_offset = -h / 15.0 if p_name == "fajr" else (0 if p_name == "dhuhr" else h / 15.0)
        current_utc_decimal = date_dt.hour + date_dt.minute / 60.0 + date_dt.second / 3600.0
        lon = (current_utc_decimal + eot / 60 - 12 - time_offset) * -15
        return ((lon + 180) % 360) - 180
    except (ZeroDivisionError, ValueError):
        return None


def is_lon_in_band(lon, lon_now, lon_ago):
    if lon_now is None or lon_ago is None:
        return False
    start, end = min(lon_now, lon_ago), max(lon_now, lon_ago)
    if abs(start - end) > 180:
        return lon >= end or lon <= start
    return start <= lon <= end


@app.route('/api/get_adhans')
def get_adhans():
    bbox = request.args.get('bbox')
    now_utc = datetime.now(pytz.utc)

    if bbox:
        s, w, n, e = map(float, bbox.split(','))
        if w > e:
            mask = (df['lat'] >= s) & (df['lat'] <= n) & ((df['lon'] >= w) | (df['lon'] <= e))
        else:
            mask = (df['lat'] >= s) & (df['lat'] <= n) & (df['lon'] >= w) & (df['lon'] <= e)
        work_df = df[mask].copy()
    else:
        work_df = df.copy()

    active_mosques = []
    inactive_mosques = []

    for _, row in work_df.iterrows():
        try:
            lat = float(row['lat'])
            lon = float(row['lon'])
            name = str(row['name']) if pd.notna(row['name']) else "Mosque"

            if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                continue
        except (ValueError, TypeError):
            continue

        active_prayer = None
        for prayer in PRAYER_COLORS:
            ln = get_solar_prayer_lon(lat, prayer, now_utc)
            la = get_solar_prayer_lon(lat, prayer, now_utc - timedelta(minutes=ADHAN_DURATION_MINUTES))
            if is_lon_in_band(lon, ln, la):
                active_prayer = prayer
                break

        mosque_data = {
            "n": name,
            "lt": lat,
            "ln": lon,
            "a": active_prayer is not None,
            "p": active_prayer
        }

        if active_prayer is not None:
            active_mosques.append(mosque_data)
        else:
            inactive_mosques.append(mosque_data)

    max_inactive = 3000
    if len(inactive_mosques) > max_inactive:
        import random
        inactive_mosques = random.sample(inactive_mosques, max_inactive)

    results = active_mosques + inactive_mosques
    print(f"Returning {len(results)} mosques: {len(active_mosques)} ACTIVE")
    return jsonify(results)


@app.route('/api/stats')
def get_stats():
    # checking cache
    current_time = time.time()
    if stats_cache["data"] and (current_time - stats_cache["timestamp"]) < stats_cache["ttl"]:
        print("Returning cached stats")
        return jsonify(stats_cache["data"])

    start = time.time()
    now_utc = datetime.now(pytz.utc)
    now_minute = now_utc.replace(second=0, microsecond=0)

    stats = {"total": len(df), "prayers": {p: {"count": 0, "target": None} for p in PRAYER_COLORS}}

    # first counting longitude ranges for each adhan
    prayer_lon_ranges = {}
    for prayer in PRAYER_COLORS:
        ref_lat = 30
        ln_now = get_solar_prayer_lon(ref_lat, prayer, now_minute)
        ln_ago = get_solar_prayer_lon(ref_lat, prayer, now_minute - timedelta(minutes=ADHAN_DURATION_MINUTES))

        if ln_now is not None and ln_ago is not None:
            prayer_lon_ranges[prayer] = (ln_now, ln_ago)

    print(f"Processing {len(df)} mosques...")

    for idx, row in df.iterrows():
        lat = row['lat']
        lon = row['lon']

        for prayer in PRAYER_COLORS:
            if prayer not in prayer_lon_ranges:
                continue

            ln = get_solar_prayer_lon(lat, prayer, now_minute)
            la = get_solar_prayer_lon(lat, prayer, now_minute - timedelta(minutes=ADHAN_DURATION_MINUTES))

            if is_lon_in_band(lon, ln, la):
                stats['prayers'][prayer]['count'] += 1

                if not stats['prayers'][prayer]['target']:
                    stats['prayers'][prayer]['target'] = [float(lat), float(lon)]
                break

    elapsed = time.time() - start
    print(f"Stats calculated in {elapsed:.2f}s: {[(p, stats['prayers'][p]['count']) for p in PRAYER_COLORS]}")

    stats_cache["data"] = stats
    stats_cache["timestamp"] = current_time

    return jsonify(stats)


@app.route('/')
def index():
    return send_from_directory('templates', 'index.html')


if __name__ == '__main__':
    app.run(debug=True, port=5000)
