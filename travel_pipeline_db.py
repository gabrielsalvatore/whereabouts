#!/usr/bin/env python3
"""
travel_pipeline_db.py — GPS Travel Analytics from Apple Photos SQLite DB
=========================================================================

NO EXPORT NEEDED. Reads directly from your Photos library database.

PREREQUISITES
-------------
1. Grant Terminal Full Disk Access:
   System Settings → Privacy & Security → Full Disk Access → add Terminal

2. Install Python dependencies:
   pip install pandas numpy scikit-learn folium reverse_geocoder tqdm

USAGE
-----
Basic (auto-detects home as most-photographed cluster):
    python travel_pipeline_db.py

With your home city coordinates:
    python travel_pipeline_db.py --home "29.7604,-95.3698"
    (above example = Houston, TX)

Filter to a specific year:
    python travel_pipeline_db.py --home "29.7604,-95.3698" --year 2026

Custom library path (if your library isn't in ~/Pictures/):
    python travel_pipeline_db.py --library "/Volumes/External/My Library.photoslibrary"

OUTPUTS (written to ./travel_output/)
--------------------------------------
  clean_gps.csv      — deduplicated, geocoded GPS records
  clusters.csv       — named places with visit counts and date ranges
  trips.csv          — detected trips with start/end dates
  timeline.csv       — dominant location per month
  stats_report.txt   — human-readable travel summary
  travel_map.html    — interactive map (open in any browser)
"""

import argparse
import math
import os
import sqlite3
import sys
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import folium
import numpy as np
import pandas as pd
import reverse_geocoder as rg
from folium.plugins import HeatMap
from sklearn.cluster import DBSCAN
from tqdm import tqdm

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

# Cluster radius in km. 0.15 = 150m, good for cities.
# Increase to 0.3–0.5 if you get too many tiny clusters in rural/road-trip areas.
CLUSTER_EPS_KM   = 0.15
CLUSTER_MIN_SAMPLES = 3

# Apple stores dates as seconds since 2001-01-01, not Unix epoch
APPLE_EPOCH = 978307200

# Cap on single-day travel distance to filter GPS glitches
MAX_DAILY_JUMP_KM = 15000

# Live Photo companion videos are ≤ this duration
LIVE_PHOTO_MAX_DURATION_S = 4.0

EARTH_R_KM = 6371.0088

# Merge consecutive trip segments whose cluster centroids are within this distance.
# 75 km covers Maceió ↔ Marechal Deodoro (30 km) without merging, say,
# Meadville ↔ Pittsburgh (120 km).
TRIP_MERGE_KM = 75

# Clusters within this distance of a known major airport that have ≤ 1 photo-day
# are flagged as layovers and excluded from trip detection.
AIRPORT_FILTER_KM = 8

# (lat, lon, "IATA code / city") for major airports
MAJOR_AIRPORTS = [
    (33.6407, -84.4277,  "ATL – Atlanta"),
    (33.9425, -118.4081, "LAX – Los Angeles"),
    (40.6413, -73.7781,  "JFK – New York"),
    (40.7769, -73.8740,  "LGA – New York"),
    (40.6895, -74.1745,  "EWR – Newark"),
    (41.9742, -87.9073,  "ORD – Chicago"),
    (41.7868, -87.7522,  "MDW – Chicago Midway"),
    (32.8998, -97.0403,  "DFW – Dallas"),
    (29.9902, -95.3368,  "IAH – Houston"),
    (29.6454, -95.2789,  "HOU – Houston Hobby"),
    (25.7959, -80.2870,  "MIA – Miami"),
    (26.0726, -80.1527,  "FLL – Fort Lauderdale"),
    (37.6213, -122.3790, "SFO – San Francisco"),
    (37.3626, -121.9291, "SJC – San Jose"),
    (47.4502, -122.3088, "SEA – Seattle"),
    (39.8561, -104.6737, "DEN – Denver"),
    (33.4373, -112.0078, "PHX – Phoenix"),
    (42.3656, -71.0096,  "BOS – Boston"),
    (38.9531, -77.4565,  "IAD – Washington Dulles"),
    (38.8512, -77.0402,  "DCA – Washington Reagan"),
    (36.0801, -115.1522, "LAS – Las Vegas"),
    (35.2140, -80.9431,  "CLT – Charlotte"),
    (39.9974, -82.8919,  "CMH – Columbus"),
    (41.4117, -81.8498,  "CLE – Cleveland"),
    (42.2162, -83.3554,  "DTW – Detroit"),
    (44.8848, -93.2223,  "MSP – Minneapolis"),
    (29.9934, -90.2580,  "MSY – New Orleans"),
    (36.1263, -86.6774,  "BNA – Nashville"),
    (30.1975, -97.6664,  "AUS – Austin"),
    (35.0440, -89.9767,  "MEM – Memphis"),
    (21.3187, -157.9224, "HNL – Honolulu"),
    (18.4394, -66.0018,  "SJU – San Juan PR"),
    (-9.8662, -35.7919,  "MCZ – Maceió BR"),
    (-23.4356, -46.4731, "GRU – São Paulo Guarulhos"),
    (-23.6273, -46.6567, "CGH – São Paulo Congonhas"),
]

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — READ FROM SQLITE
# ─────────────────────────────────────────────────────────────────────────────

QUERY = """
SELECT
    a.ZUUID                     AS uuid,
    a.ZFILENAME                 AS filename,
    a.ZDIRECTORY                AS directory,
    a.ZLATITUDE                 AS lat,
    a.ZLONGITUDE                AS lon,
    a.ZDATECREATED              AS date_created_apple,
    a.ZKIND                     AS kind,
    a.ZDURATION                 AS duration,
    a.ZAVALANCHEKIND            AS avalanche_kind,
    a.ZAVALANCHEPICKTYPE        AS avalanche_pick_type,
    a.ZMEDIAGROUPUUID           AS media_group_uuid,
    attr.ZTIMEZONEOFFSET        AS tz_offset_seconds,
    attr.ZTIMEZONENAME          AS tz_name
FROM ZASSET a
LEFT JOIN ZADDITIONALASSETATTRIBUTES attr
    ON attr.ZASSET = a.Z_PK
WHERE
    a.ZTRASHEDSTATE = 0
    AND a.ZLATITUDE  IS NOT NULL
    AND a.ZLATITUDE  != -180.0
    AND a.ZLONGITUDE IS NOT NULL
    AND a.ZLONGITUDE != -180.0
ORDER BY a.ZDATECREATED;
"""

def load_from_db(library_path: Path, db_path_override: Path = None) -> pd.DataFrame:
    if db_path_override:
        db_path = db_path_override
    else:
        db_path = library_path / "database" / "Photos.sqlite"

    if not db_path.exists():
        sys.exit(f"[error] Database not found at {db_path}\n"
                 "        Check --library path and Full Disk Access in System Settings.\n"
                 "        Or copy the database first:\n"
                 "          cp ~/Pictures/Photos\\ Library.photoslibrary/database/Photos.sqlite /tmp/photos_copy.sqlite\n"
                 "        Then re-run with: --db-path /tmp/photos_copy.sqlite")
    try:
        # Try URI read-only first, fall back to regular connection
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.execute("SELECT 1")  # probe
        except (sqlite3.DatabaseError, sqlite3.OperationalError):
            conn = sqlite3.connect(str(db_path))
        df = pd.read_sql_query(QUERY, conn)
        conn.close()
    except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
        sys.exit(
            f"[error] Cannot read database: {e}\n\n"
            "  macOS TCC is blocking access. Two options:\n\n"
            "  Option A — Copy first (from a terminal with Full Disk Access):\n"
            "    cp ~/Pictures/Photos\\ Library.photoslibrary/database/Photos.sqlite /tmp/photos_copy.sqlite\n"
            "  Then re-run with:\n"
            "    python travel_pipeline_db.py --db-path /tmp/photos_copy.sqlite --home '41.6418,-80.1512' --year 2026\n\n"
            "  Option B — Grant Full Disk Access:\n"
            "    System Settings → Privacy & Security → Full Disk Access → add Terminal (or Claude Code)"
        )
    return df


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — TIMESTAMPS
# Apple epoch → UTC datetime → approximate local date from longitude
# If tz_offset_seconds is available, use it for exact local date.
# ─────────────────────────────────────────────────────────────────────────────

def parse_timestamps(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Convert Apple epoch to UTC
    df["datetime_utc"] = pd.to_datetime(
        df["date_created_apple"] + APPLE_EPOCH, unit="s", utc=True, errors="coerce"
    )
    df = df[df["datetime_utc"].notna()].copy()

    # Local date: use tz_offset_seconds if present, else approximate from longitude
    def _local_date(row):
        if pd.notna(row["tz_offset_seconds"]):
            offset = timedelta(seconds=int(row["tz_offset_seconds"]))
        else:
            # Approximate: 15° longitude ≈ 1 hour
            offset = timedelta(hours=round(row["lon"] / 15))
        local_dt = row["datetime_utc"] + offset
        return local_dt.date()

    tqdm.pandas(desc="Computing local dates", leave=False)
    df["local_date"] = df.progress_apply(_local_date, axis=1)
    df["year"]  = df["local_date"].apply(lambda d: d.year)
    df["month"] = df["local_date"].apply(lambda d: d.month)
    return df


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — DEDUPLICATION
# Live Photos: drop video (kind=1) when it shares media_group_uuid with a photo
# Bursts: keep only burst key asset
# Short orphaned videos: drop videos ≤ 4s with no photo pair
# ─────────────────────────────────────────────────────────────────────────────

def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    before = len(df)
    df = df.copy()

    # ── Live Photo pairs via media_group_uuid ─────────────────────────────────
    # kind=0 → photo/HEIC, kind=1 → video/MOV
    paired_groups = df[df["media_group_uuid"].notna()]["media_group_uuid"].unique()
    paired_mask = df["media_group_uuid"].isin(paired_groups)

    # Within each paired group, keep kind=0 (photo); drop kind=1 (video)
    live_video_mask = paired_mask & (df["kind"] == 1)
    df = df[~live_video_mask].copy()

    # ── Orphaned short videos (no photo partner found) ────────────────────────
    orphan_mask = (
        (df["kind"] == 1) &
        df["duration"].notna() &
        (df["duration"] <= LIVE_PHOTO_MAX_DURATION_S)
    )
    df = df[~orphan_mask].copy()

    # ── Burst photos: keep only the key/picked asset ──────────────────────────
    # avalanche_kind != 0 → part of a burst; avalanche_pick_type > 0 → chosen
    if "avalanche_kind" in df.columns and "avalanche_pick_type" in df.columns:
        burst_mask = (
            df["avalanche_kind"].notna() & (df["avalanche_kind"] != 0) &
            (df["avalanche_pick_type"].fillna(0) == 0)
        )
        df = df[~burst_mask].copy()

    removed = before - len(df)
    print(f"      Removed {removed:,} duplicates (Live Photos, orphaned videos, burst frames).")
    print(f"      {len(df):,} unique moments remain.")
    return df.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — GPS VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

def validate_gps(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df = df[
        df["lat"].between(-90, 90) &
        df["lon"].between(-180, 180) &
        ~((df["lat"] == 0) & (df["lon"] == 0))   # Null Island
    ].copy()
    return df


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — CLUSTERING
# ─────────────────────────────────────────────────────────────────────────────

def haversine(lat1, lon1, lat2, lon2) -> float:
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a = math.sin(dφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(dλ/2)**2
    return 2 * EARTH_R_KM * math.asin(math.sqrt(a))

def cluster_gps(df: pd.DataFrame) -> pd.DataFrame:
    coords_rad = np.radians(df[["lat", "lon"]].to_numpy())
    eps_rad = CLUSTER_EPS_KM / EARTH_R_KM

    print(f"      Running DBSCAN (eps={CLUSTER_EPS_KM}km, min_samples={CLUSTER_MIN_SAMPLES}) …")
    db = DBSCAN(
        eps=eps_rad,
        min_samples=CLUSTER_MIN_SAMPLES,
        algorithm="ball_tree",
        metric="haversine",
    ).fit(coords_rad)

    df = df.copy()
    df["cluster_id"] = db.labels_

    # Assign noise points to nearest cluster centroid
    centroids = (
        df[df["cluster_id"] >= 0]
        .groupby("cluster_id")[["lat", "lon"]]
        .mean()
        .reset_index()
    )

    if centroids.empty:
        print("[warn] No clusters formed. Try increasing CLUSTER_EPS_KM at the top of the script.")
        return df

    noise_mask = df["cluster_id"] == -1
    if noise_mask.any():
        centroid_coords = centroids[["lat", "lon"]].to_numpy()
        noise_rows = df[noise_mask].copy()
        for row_idx, row in noise_rows.iterrows():
            dists = [haversine(row["lat"], row["lon"], c[0], c[1]) for c in centroid_coords]
            df.at[row_idx, "cluster_id"] = int(centroids.iloc[np.argmin(dists)]["cluster_id"])

    n_clusters = df["cluster_id"].nunique()
    print(f"      {n_clusters} clusters formed.")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# STEP 6 — REVERSE GEOCODING
# ─────────────────────────────────────────────────────────────────────────────

def reverse_geocode_clusters(df: pd.DataFrame):
    centroids = (
        df.groupby("cluster_id")
        .agg(
            centroid_lat=("lat", "mean"),
            centroid_lon=("lon", "mean"),
            photo_count=("lat", "count"),
            first_date=("local_date", "min"),
            last_date=("local_date", "max"),
        )
        .reset_index()
    )

    coords = list(zip(centroids["centroid_lat"], centroids["centroid_lon"]))
    results = rg.search(coords, mode=1)

    centroids["city"]    = [r.get("name", "")  for r in results]
    centroids["admin1"]  = [r.get("admin1", "") for r in results]
    centroids["country"] = [r.get("cc", "")    for r in results]
    centroids["place_label"] = centroids.apply(
        lambda r: f"{r['city']}, {r['admin1']}, {r['country']}"
                  if r["admin1"] else f"{r['city']}, {r['country']}",
        axis=1
    )

    cluster_map = centroids.set_index("cluster_id")[
        ["place_label", "city", "admin1", "country"]
    ].to_dict("index")

    df = df.copy()
    df["place_label"] = df["cluster_id"].map(lambda c: cluster_map.get(c, {}).get("place_label", "Unknown"))
    df["city"]        = df["cluster_id"].map(lambda c: cluster_map.get(c, {}).get("city", ""))
    df["country"]     = df["cluster_id"].map(lambda c: cluster_map.get(c, {}).get("country", ""))

    return df, centroids


# ─────────────────────────────────────────────────────────────────────────────
# STEP 6b — CONSOLIDATE PLACES BY CITY + COUNTRY
# Multiple DBSCAN clusters that reverse-geocode to the same city are merged
# into one, using the largest cluster's ID as the representative.
# ─────────────────────────────────────────────────────────────────────────────

def consolidate_places_by_city(df: pd.DataFrame, clusters_df: pd.DataFrame):
    # For each (city, country) group, pick the cluster with most photos as rep
    rep = (
        clusters_df.sort_values("photo_count", ascending=False)
        .groupby(["city", "country"])["cluster_id"]
        .first()
        .reset_index()
        .rename(columns={"cluster_id": "rep_id"})
    )
    merged = clusters_df.merge(rep, on=["city", "country"])
    old_to_rep = dict(zip(merged["cluster_id"], merged["rep_id"]))

    before = clusters_df["cluster_id"].nunique()
    df = df.copy()
    df["cluster_id"] = df["cluster_id"].map(old_to_rep).fillna(df["cluster_id"]).astype(int)

    # Rebuild cluster stats after remapping
    new_clusters = (
        df.groupby("cluster_id")
        .agg(
            centroid_lat=("lat", "mean"),
            centroid_lon=("lon", "mean"),
            photo_count=("lat", "count"),
            first_date=("local_date", "min"),
            last_date=("local_date", "max"),
        )
        .reset_index()
    )
    # Re-attach place labels (use any surviving row for that rep_id)
    label_map = (
        merged.drop_duplicates("rep_id")
        .set_index("rep_id")[["city", "admin1", "country", "place_label"]]
    )
    new_clusters = new_clusters.join(label_map, on="cluster_id")

    # Propagate updated labels back to df
    lmap = new_clusters.set_index("cluster_id")[["place_label", "city", "admin1", "country"]].to_dict("index")
    df["place_label"] = df["cluster_id"].map(lambda c: lmap.get(c, {}).get("place_label", "Unknown"))
    df["city"]        = df["cluster_id"].map(lambda c: lmap.get(c, {}).get("city", ""))
    df["country"]     = df["cluster_id"].map(lambda c: lmap.get(c, {}).get("country", ""))

    after = new_clusters["cluster_id"].nunique()
    print(f"      Consolidated {before} clusters → {after} unique places (merged same-city duplicates).")
    return df, new_clusters.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 6c — AIRPORT LAYOVER FILTER
# Clusters whose centroid is within AIRPORT_FILTER_KM of a known major airport
# AND whose visit spans only 1 calendar day are flagged as layovers and excluded
# from trip detection (GPS points are kept in clean_gps.csv).
# ─────────────────────────────────────────────────────────────────────────────

def flag_airport_clusters(clusters_df: pd.DataFrame) -> set:
    """Return a set of cluster_ids that look like airport layovers."""
    layover_ids = set()
    for _, row in clusters_df.iterrows():
        days_visited = (
            pd.to_datetime(row["last_date"]) - pd.to_datetime(row["first_date"])
        ).days + 1
        if days_visited > 1:
            continue  # spent more than a day → real destination
        for alat, alon, aname in MAJOR_AIRPORTS:
            dist = haversine(row["centroid_lat"], row["centroid_lon"], alat, alon)
            if dist <= AIRPORT_FILTER_KM:
                layover_ids.add(int(row["cluster_id"]))
                break
    return layover_ids


# ─────────────────────────────────────────────────────────────────────────────
# STEP 7 — TRIP DETECTION  (+ proximity merge + journey grouping)
# ─────────────────────────────────────────────────────────────────────────────

def merge_nearby_trips(trips: list, clusters_df: pd.DataFrame) -> list:
    """
    Merge consecutive trip segments whose cluster centroids are within
    TRIP_MERGE_KM of each other (and whose date gap is ≤ 1 day).
    Sub-destinations within a merged trip are tracked separately.
    """
    if len(trips) <= 1:
        return trips

    centroid_map = (
        clusters_df.set_index("cluster_id")[["centroid_lat", "centroid_lon"]]
        .to_dict("index")
    )

    merged = [dict(trips[0], sub_destinations=[trips[0]["city"]])]

    for t in trips[1:]:
        last = merged[-1]
        lc, cc = last["cluster_id"], t["cluster_id"]
        gap = (t["start_date"] - last["end_date"]).days

        close_enough = False
        if lc in centroid_map and cc in centroid_map:
            dist = haversine(
                centroid_map[lc]["centroid_lat"], centroid_map[lc]["centroid_lon"],
                centroid_map[cc]["centroid_lat"], centroid_map[cc]["centroid_lon"],
            )
            close_enough = dist <= TRIP_MERGE_KM and gap <= 1

        if close_enough:
            last["end_date"] = t["end_date"]
            last["days"] = (last["end_date"] - last["start_date"]).days + 1
            if t["city"] not in last["sub_destinations"]:
                last["sub_destinations"].append(t["city"])
        else:
            merged.append(dict(t, sub_destinations=[t["city"]]))

    return merged


def detect_journeys(trips_df: pd.DataFrame, max_home_gap_days: int = 3) -> pd.DataFrame:
    """
    Group merged trips into journeys — continuous periods away from home,
    allowing short home gaps (e.g. a 1–2 day return for logistics).
    Returns a journeys DataFrame and adds journey_id to trips_df.
    """
    if trips_df.empty:
        return pd.DataFrame(), trips_df

    trips_sorted = trips_df.sort_values("start_date").reset_index(drop=True)
    journey_id = 0
    jids = []
    current_end = None

    for _, row in trips_sorted.iterrows():
        if current_end is None:
            journey_id += 1
        else:
            gap = (row["start_date"] - current_end).days
            if gap > max_home_gap_days:
                journey_id += 1
        jids.append(journey_id)
        current_end = row["end_date"]

    trips_sorted = trips_sorted.copy()
    trips_sorted["journey_id"] = jids

    journeys = []
    for jid, grp in trips_sorted.groupby("journey_id"):
        countries = list(dict.fromkeys(grp["country"]))
        cities    = list(dict.fromkeys(grp["city"]))
        journeys.append({
            "journey_id":     jid,
            "start_date":     grp["start_date"].min(),
            "end_date":       grp["end_date"].max(),
            "days":           (grp["end_date"].max() - grp["start_date"].min()).days + 1,
            "n_stops":        len(grp),
            "countries":      " / ".join(countries),
            "cities":         " / ".join(cities[:6]) + (" …" if len(cities) > 6 else ""),
            "primary_city":   grp.loc[grp["days"].idxmax(), "city"],
            "primary_country":grp.loc[grp["days"].idxmax(), "country"],
        })

    return pd.DataFrame(journeys), trips_sorted


def detect_trips(df: pd.DataFrame, home_cluster_id: int,
                 layover_ids: set = None, clusters_df: pd.DataFrame = None) -> pd.DataFrame:
    if layover_ids is None:
        layover_ids = set()

    # Dominant cluster per calendar day, excluding home and layovers
    skip_ids = {home_cluster_id} | layover_ids
    daily = (
        df[~df["cluster_id"].isin(skip_ids)]
        .groupby(["local_date", "cluster_id"])
        .size()
        .reset_index(name="count")
        .sort_values(["local_date", "count"], ascending=[True, False])
        .drop_duplicates(subset="local_date", keep="first")
        .sort_values("local_date")
        .reset_index(drop=True)
    )

    # Also include home days to detect trip boundaries
    home_days = set(
        df[df["cluster_id"] == home_cluster_id]["local_date"].unique()
    )

    raw_trips, current = [], None
    all_dates = sorted(df["local_date"].unique())

    for date in all_dates:
        row = daily[daily["local_date"] == date]
        is_home = date in home_days or row.empty

        if is_home:
            if current:
                raw_trips.append(current)
                current = None
        else:
            cid = int(row.iloc[0]["cluster_id"])
            if current and current["cluster_id"] == cid:
                current["end_date"] = date
                current["days"] += 1
            else:
                if current:
                    raw_trips.append(current)
                current = {"cluster_id": cid, "start_date": date, "end_date": date, "days": 1}

    if current:
        raw_trips.append(current)

    if not raw_trips:
        return pd.DataFrame()

    # Attach place labels and state before merging
    place_map = (
        df[["cluster_id", "place_label", "city", "country"]]
        .drop_duplicates("cluster_id")
    )
    # Add admin1/state if available
    if "admin1" in df.columns:
        state_map = df[["cluster_id", "admin1"]].drop_duplicates("cluster_id")
        place_map = place_map.merge(state_map, on="cluster_id", how="left")

    for t in raw_trips:
        pm = place_map[place_map["cluster_id"] == t["cluster_id"]]
        if not pm.empty:
            t["city"]        = pm.iloc[0]["city"]
            t["country"]     = pm.iloc[0]["country"]
            t["state"]       = pm.iloc[0].get("admin1", "")
            t["place_label"] = pm.iloc[0]["place_label"]
        else:
            t.setdefault("city", ""); t.setdefault("country", "")
            t.setdefault("state", ""); t.setdefault("place_label", "Unknown")

    # Proximity merge: collapse consecutive nearby segments
    if clusters_df is not None and len(raw_trips) > 1:
        raw_trips = merge_nearby_trips(raw_trips, clusters_df)

    trips_df = pd.DataFrame(raw_trips)
    # Stringify sub_destinations list
    if "sub_destinations" in trips_df.columns:
        trips_df["sub_destinations"] = trips_df["sub_destinations"].apply(
            lambda x: " / ".join(x) if isinstance(x, list) else x
        )
    return trips_df.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 8 — DISTANCE
# ─────────────────────────────────────────────────────────────────────────────

def estimate_distance(df: pd.DataFrame) -> float:
    daily = (
        df.groupby("local_date")
        .agg(lat=("lat", "median"), lon=("lon", "median"))
        .reset_index()
        .sort_values("local_date")
    )
    total = 0.0
    for i in range(1, len(daily)):
        r1, r2 = daily.iloc[i-1], daily.iloc[i]
        d = haversine(r1["lat"], r1["lon"], r2["lat"], r2["lon"])
        if d <= MAX_DAILY_JUMP_KM:
            total += d
    return total


# ─────────────────────────────────────────────────────────────────────────────
# STEP 9 — MONTHLY TIMELINE
# ─────────────────────────────────────────────────────────────────────────────

def build_timeline(df: pd.DataFrame) -> pd.DataFrame:
    tl = (
        df.groupby(["year", "month", "country", "city"])
        .size()
        .reset_index(name="photo_count")
        .sort_values(["year", "month", "photo_count"], ascending=[True, True, False])
        .drop_duplicates(subset=["year", "month"], keep="first")
        .copy()
    )
    tl["month_label"] = tl.apply(
        lambda r: datetime(int(r["year"]), int(r["month"]), 1).strftime("%B %Y"), axis=1
    )
    return tl[["month_label", "year", "month", "city", "country", "photo_count"]]


# ─────────────────────────────────────────────────────────────────────────────
# STEP 10 — STATS REPORT
# ─────────────────────────────────────────────────────────────────────────────

def generate_stats(df, clusters, trips, total_km, journeys=None) -> str:
    lines = ["=" * 62, "  TRAVEL STATISTICS", "=" * 62]

    countries = sorted(df["country"].dropna().unique())
    cities    = df["city"].dropna().unique()

    lines += [
        f"\n  Photos/videos with GPS : {len(df):,}",
        f"  Days with photos       : {df['local_date'].nunique()}",
        f"  Countries visited      : {len(countries)}  ({', '.join(countries)})",
        f"  Unique cities          : {len(cities)}",
        f"  Estimated distance     : {total_km:,.0f} km  /  {total_km*0.621371:,.0f} mi",
    ]

    lines.append("\n── TOP 10 LOCATIONS " + "─" * 42)
    for _, row in clusters.sort_values("photo_count", ascending=False).head(10).iterrows():
        lines.append(
            f"  {row['place_label']:<42}  {row['photo_count']:>5} photos"
            f"  ({row['first_date']} → {row['last_date']})"
        )

    if journeys is not None and not journeys.empty:
        lines.append(f"\n── JOURNEYS ({len(journeys)}) " + "─" * 46)
        for _, row in journeys.iterrows():
            lines.append(
                f"  {str(row['start_date'])} → {str(row['end_date'])}"
                f"  ({row['days']}d, {row['n_stops']} stop{'s' if row['n_stops']>1 else ''})"
                f"  {row['cities']}"
            )
    elif not trips.empty:
        lines.append(f"\n── TRIPS ({len(trips)}) " + "─" * 50)
        for _, row in trips.iterrows():
            lines.append(
                f"  {str(row['start_date'])} → {str(row['end_date'])}"
                f"  ({row['days']}d)  {row['place_label']}"
            )

    lines.append("\n── MONTHLY SUMMARY " + "─" * 43)
    for _, row in build_timeline(df).iterrows():
        lines.append(f"  {row['month_label']:<14}  {row['city']}, {row['country']}"
                     f"  ({row['photo_count']} photos)")

    lines.append("\n" + "=" * 62)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 11 — FOLIUM MAP
# ─────────────────────────────────────────────────────────────────────────────

def build_map(df, clusters, trips) -> folium.Map:
    m = folium.Map(
        location=[df["lat"].median(), df["lon"].median()],
        zoom_start=4,
        tiles="CartoDB positron",
        prefer_canvas=True,
    )

    # Heatmap
    HeatMap(
        df[["lat", "lon"]].values.tolist(),
        name="GPS density",
        radius=8, blur=10, max_zoom=13,
        gradient={"0.2": "#3b82f6", "0.5": "#8b5cf6", "1.0": "#ef4444"},
    ).add_to(m)

    # Named place markers (top 60 by photo count)
    places_layer = folium.FeatureGroup(name="Named places", show=True)
    for _, row in clusters.sort_values("photo_count", ascending=False).head(60).iterrows():
        days_at = df[df["cluster_id"] == row["cluster_id"]]["local_date"].nunique()
        popup = (
            f"<b>{row['place_label']}</b><br>"
            f"📸 {row['photo_count']} photos<br>"
            f"📅 {days_at} days<br>"
            f"🗓 {row['first_date']} → {row['last_date']}"
        )
        radius = max(6, min(28, int(math.log2(row["photo_count"] + 1) * 3)))
        folium.CircleMarker(
            location=[row["centroid_lat"], row["centroid_lon"]],
            radius=radius,
            color="#1d4ed8", fill=True,
            fill_color="#3b82f6", fill_opacity=0.75,
            popup=folium.Popup(popup, max_width=260),
            tooltip=row["place_label"],
        ).add_to(places_layer)
    places_layer.add_to(m)

    # Trip path polyline
    if not trips.empty and len(trips) > 1:
        trip_layer = folium.FeatureGroup(name="Trip path", show=True)
        c_map = clusters.set_index("cluster_id")[["centroid_lat", "centroid_lon"]].to_dict("index")
        coords = [
            [c_map[r["cluster_id"]]["centroid_lat"], c_map[r["cluster_id"]]["centroid_lon"]]
            for _, r in trips.sort_values("start_date").iterrows()
            if r["cluster_id"] in c_map
        ]
        if len(coords) > 1:
            folium.PolyLine(coords, color="#f59e0b", weight=2.5,
                            opacity=0.7, tooltip="Trip route").add_to(trip_layer)
        trip_layer.add_to(m)

    folium.LayerControl().add_to(m)
    return m


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="GPS Travel Analytics — Apple Photos DB")
    parser.add_argument("--library", default=str(Path.home() / "Pictures/Photos Library.photoslibrary"),
                        help="Path to your .photoslibrary bundle")
    parser.add_argument("--db-path", default=None,
                        help="Direct path to Photos.sqlite (use if TCC blocks library access)")
    parser.add_argument("--home", default=None,
                        help="Home coordinates 'lat,lon' e.g. '29.7604,-95.3698'")
    parser.add_argument("--year", type=int, default=None,
                        help="Filter to a specific year")
    parser.add_argument("--output-dir", default="./travel_output",
                        help="Directory for output files")
    args = parser.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ── 1. Load from DB ───────────────────────────────────────────────────────
    print(f"[1/9] Reading Apple Photos database …")
    db_override = Path(args.db_path) if args.db_path else None
    df = load_from_db(Path(args.library), db_path_override=db_override)
    print(f"      {len(df):,} GPS records loaded.")

    # ── 2. Timestamps ─────────────────────────────────────────────────────────
    print("[2/9] Parsing timestamps …")
    df = parse_timestamps(df)

    # ── 3. Year filter ────────────────────────────────────────────────────────
    if args.year:
        df = df[df["year"] == args.year].copy()
        print(f"      Filtered to {args.year}: {len(df):,} records.")

    if df.empty:
        sys.exit("[error] No records after filtering. Check --year value.")

    # ── 4. Deduplicate ────────────────────────────────────────────────────────
    print("[3/9] Deduplicating Live Photos and bursts …")
    df = deduplicate(df)

    # ── 5. Validate GPS ───────────────────────────────────────────────────────
    print("[4/9] Validating GPS coordinates …")
    df = validate_gps(df)
    print(f"      {len(df):,} records with valid GPS.")

    # ── 6. Cluster ────────────────────────────────────────────────────────────
    print("[5/9] Clustering GPS points …")
    df = cluster_gps(df)

    # ── 7. Reverse geocode ────────────────────────────────────────────────────
    print("[6/9] Reverse geocoding clusters …")
    df, clusters_df = reverse_geocode_clusters(df)
    print(f"      {len(clusters_df)} raw clusters geocoded.")

    # ── 7b. Consolidate same-city duplicates ──────────────────────────────────
    print("      Consolidating same-city clusters …")
    df, clusters_df = consolidate_places_by_city(df, clusters_df)

    # ── 7c. Flag airport layovers ─────────────────────────────────────────────
    layover_ids = flag_airport_clusters(clusters_df)
    if layover_ids:
        print(f"      Flagged {len(layover_ids)} airport/layover cluster(s) — excluded from trips.")

    # ── 8. Home cluster ───────────────────────────────────────────────────────
    if args.home:
        hlat, hlon = [float(x.strip()) for x in args.home.split(",")]
        clusters_df["_dist_home"] = clusters_df.apply(
            lambda r: haversine(r["centroid_lat"], r["centroid_lon"], hlat, hlon), axis=1
        )
        home_id = int(clusters_df.loc[clusters_df["_dist_home"].idxmin(), "cluster_id"])
    else:
        home_id = int(df.groupby("cluster_id").size().idxmax())

    home_label = clusters_df.loc[clusters_df["cluster_id"] == home_id, "place_label"].values
    print(f"      Home cluster: {home_label[0] if len(home_label) else 'unknown'}")

    # ── 9. Trips + journeys ───────────────────────────────────────────────────
    print("[7/9] Detecting trips …")
    trips_df = detect_trips(df, home_id, layover_ids=layover_ids, clusters_df=clusters_df)
    print(f"      {len(trips_df)} trip segments after proximity merge.")
    journeys_df, trips_df = detect_journeys(trips_df)
    print(f"      {len(journeys_df)} journeys detected.")

    # ── 10. Distance ──────────────────────────────────────────────────────────
    print("[8/9] Estimating distance traveled …")
    total_km = estimate_distance(df)
    print(f"      ~{total_km:,.0f} km")

    # ── 11. Write outputs ─────────────────────────────────────────────────────
    print("[9/9] Writing outputs …")

    # clean_gps.csv
    gps_cols = ["uuid", "filename", "datetime_utc", "local_date", "year", "month",
                "lat", "lon", "cluster_id", "place_label", "city", "country",
                "kind", "duration"]
    gps_cols = [c for c in gps_cols if c in df.columns]
    df[gps_cols].to_csv(out / "clean_gps.csv", index=False)
    print(f"      → {out / 'clean_gps.csv'}")

    clusters_df.to_csv(out / "clusters.csv", index=False)
    print(f"      → {out / 'clusters.csv'}")

    if not trips_df.empty:
        trips_df.to_csv(out / "trips.csv", index=False)
        print(f"      → {out / 'trips.csv'}")

    if not journeys_df.empty:
        journeys_df.to_csv(out / "journeys.csv", index=False)
        print(f"      → {out / 'journeys.csv'}")

    build_timeline(df).to_csv(out / "timeline.csv", index=False)
    print(f"      → {out / 'timeline.csv'}")

    stats = generate_stats(df, clusters_df, trips_df, total_km, journeys=journeys_df)
    print("\n" + stats)
    with open(out / "stats_report.txt", "w") as f:
        f.write(stats)
    print(f"\n      → {out / 'stats_report.txt'}")

    m = build_map(df, clusters_df, trips_df)
    m.save(str(out / "travel_map.html"))
    print(f"      → {out / 'travel_map.html'}")

    print("\n✓  Done. Open travel_output/travel_map.html in your browser.")

if __name__ == "__main__":
    main()