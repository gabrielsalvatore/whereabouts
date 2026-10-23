#!/usr/bin/env python3
"""
export_analysis.py — Export all travel data to a single JSON for analysis.

No filtering, no metro-area mapping — raw output from the pipeline.

Usage:
    python3 src/export_analysis.py
    python3 src/export_analysis.py --output my_travel_data.json

Output: output/travel_analysis_full.json (default)
"""
import csv
import json
import argparse
from pathlib import Path
from datetime import date as _date

ROOT     = Path(__file__).parent.parent
OUT_DIR  = ROOT / "output"

def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(OUT_DIR / "travel_analysis_full.json"))
    parser.add_argument("--no-photos", action="store_true",
                        help="Omit individual photo records (much smaller file)")
    args = parser.parse_args()

    # ── places (all clusters, unfiltered) ────────────────────────────────────
    clusters = read_csv(OUT_DIR / "clusters.csv")
    places = []
    for c in clusters:
        lat = float(c["centroid_lat"]) if c.get("centroid_lat") else None
        lon = float(c["centroid_lon"]) if c.get("centroid_lon") else None
        first = c["first_date"]
        last  = c["last_date"]
        try:
            days = (_date.fromisoformat(last) - _date.fromisoformat(first)).days + 1
        except Exception:
            days = 1
        places.append({
            "cluster_id":   c["cluster_id"],
            "city":         c["city"],
            "state":        c.get("admin1", "") or "",
            "country":      c["country"],
            "coordinates":  {"lat": lat, "lon": lon},
            "photo_count":  int(c["photo_count"]),
            "first_visit":  first,
            "last_visit":   last,
            "days_present": days,
            "label":        c["place_label"],
        })
    places.sort(key=lambda p: p["first_visit"])

    # ── individual photos ─────────────────────────────────────────────────────
    photos_raw = read_csv(OUT_DIR / "clean_gps.csv")
    photos = []
    for p in photos_raw:
        photos.append({
            "date":       p["local_date"],
            "datetime":   p.get("datetime_utc", ""),
            "lat":        float(p["lat"])  if p.get("lat")  else None,
            "lon":        float(p["lon"])  if p.get("lon")  else None,
            "city":       p.get("city", ""),
            "state":      "",   # not in clean_gps; available via cluster lookup
            "country":    p.get("country", ""),
            "cluster_id": p.get("cluster_id", ""),
            "place":      p.get("place_label", ""),
            "type":       "video" if p.get("kind") == "1" else "photo",
        })
    photos.sort(key=lambda p: p["date"])

    # ── photo counts per place per day ────────────────────────────────────────
    from collections import defaultdict
    daily = defaultdict(lambda: defaultdict(int))
    for p in photos:
        daily[p["cluster_id"]][p["date"]] += 1
    for place in places:
        place["daily_counts"] = dict(sorted(daily[place["cluster_id"]].items()))

    # ── trips ─────────────────────────────────────────────────────────────────
    trips = []
    for t in read_csv(OUT_DIR / "trips.csv"):
        trips.append({
            "cluster_id":      t["cluster_id"],
            "city":            t["city"],
            "country":         t["country"],
            "state":           t.get("state", "") or "",
            "label":           t["place_label"],
            "start":           t["start_date"],
            "end":             t["end_date"],
            "days":            int(t["days"]),
            "sub_destinations": [s.strip() for s in t.get("sub_destinations","").split("/") if s.strip()],
            "journey_id":      t.get("journey_id", ""),
        })

    # ── journeys ──────────────────────────────────────────────────────────────
    journeys = []
    for j in read_csv(OUT_DIR / "journeys.csv"):
        journeys.append({
            "journey_id":    j["journey_id"],
            "start":         j["start_date"],
            "end":           j["end_date"],
            "days":          int(j["days"]),
            "stops":         int(j["n_stops"]),
            "countries":     [c.strip() for c in j.get("countries","").split("/") if c.strip()],
            "cities":        [c.strip() for c in j.get("cities","").split("/") if c.strip()],
            "primary_city":  j.get("primary_city",""),
            "primary_country": j.get("primary_country",""),
        })

    # ── monthly timeline ──────────────────────────────────────────────────────
    timeline = []
    for row in read_csv(OUT_DIR / "timeline.csv"):
        timeline.append({
            "year_month":  row.get("year_month", row.get("month", "")),
            "city":        row.get("city", ""),
            "country":     row.get("country", ""),
            "label":       row.get("place_label", ""),
            "photos":      int(row.get("photo_count", 0)),
        })

    # ── stats text ────────────────────────────────────────────────────────────
    stats_path = OUT_DIR / "stats_report.txt"
    stats_text = stats_path.read_text("utf-8") if stats_path.exists() else ""

    # ── assemble ──────────────────────────────────────────────────────────────
    output = {
        "meta": {
            "generated":         str(_date.today()),
            "total_places":      len(places),
            "total_photos":      len(photos),
            "total_trips":       len(trips),
            "total_journeys":    len(journeys),
            "date_range": {
                "start": photos[0]["date"]  if photos else "",
                "end":   photos[-1]["date"] if photos else "",
            },
        },
        "stats_summary": stats_text,
        "places":   places,
        "trips":    trips,
        "journeys": journeys,
        "timeline": timeline,
        "photos":   [] if args.no_photos else photos,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    size_kb = out_path.stat().st_size // 1024
    print(f"✓ Exported: {out_path}")
    print(f"  {len(places)} places · {len(photos)} photos · {len(trips)} trips · {len(journeys)} journeys")
    print(f"  Size: {size_kb} KB")

if __name__ == "__main__":
    main()
