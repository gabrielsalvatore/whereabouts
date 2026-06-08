# Whereabouts

GPS travel analytics pipeline that reads directly from your Apple Photos SQLite database — no export needed.

## What it does

1. Reads GPS metadata from your Photos library database
2. Deduplicates Live Photos, burst frames, and short companion videos
3. Clusters GPS points using DBSCAN (150m radius)
4. Consolidates same-city duplicate clusters
5. Filters airport layovers from trip detection
6. Reverse geocodes cluster centroids to city/country names
7. Detects trips, merges nearby consecutive segments (≤75km), and groups them into journeys
8. Outputs a local dashboard with an interactive map, trip journal, lessons, and city contacts

## Outputs

| File | Description |
|------|-------------|
| `clusters.csv` | Named places with photo counts and date ranges |
| `trips.csv` | Individual trip segments with state, sub-destinations, journey ID |
| `journeys.csv` | High-level journeys (consecutive periods away from home) |
| `timeline.csv` | Dominant location per month |
| `stats_report.txt` | Human-readable travel summary |
| `travel_dashboard.html` | Interactive map dashboard (served locally) |

## Setup

### 1. Grant Full Disk Access to Terminal
System Settings → Privacy & Security → Full Disk Access → add Terminal

### 2. Install dependencies
```bash
pip3 install pandas numpy scikit-learn folium reverse_geocoder tqdm
```

### 3. Copy the Photos database
Run this from a terminal that has Full Disk Access:
```bash
cp ~/Pictures/Photos\ Library.photoslibrary/database/Photos.sqlite \
   ~/Documents/Projects/travel-tracker/photos_db.sqlite
```
This only copies metadata (GPS, timestamps, filenames) — not your actual photos.

## Running the pipeline

```bash
python3 src/travel_pipeline_db.py \
  --db-path photos_db.sqlite \
  --home "41.6418,-80.1512" \
  --year 2026 \
  --output-dir output
```

| Flag | Description |
|------|-------------|
| `--db-path` | Path to your copied Photos.sqlite |
| `--home` | Your home coordinates `"lat,lon"` |
| `--year` | Filter to a specific year (omit for all years) |
| `--output-dir` | Where output files are written |

## Running the dashboard

```bash
# Regenerate dashboard HTML from latest output
python3 src/generate_dashboard.py

# Start the local server (opens browser automatically)
python3 src/server.py
```

Then open **http://localhost:8765**

The dashboard lets you:
- Browse all trips on an interactive map
- Click any dot to open the trip detail panel
- Write journal entries and lessons per trip
- Track people you know in each city

Notes are saved to `notes.json` on disk.

## Configuration

Key parameters at the top of `src/travel_pipeline_db.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `CLUSTER_EPS_KM` | 0.15 | DBSCAN cluster radius in km |
| `TRIP_MERGE_KM` | 75 | Merge consecutive trips within this distance |
| `AIRPORT_FILTER_KM` | 8 | Exclude clusters within this distance of a major airport |
| `MAX_DAILY_JUMP_KM` | 15000 | Cap per-day distance to filter GPS glitches |

## Architecture

```
photos_db.sqlite  (read-only copy, gitignored)
    ↓  src/travel_pipeline_db.py
output/clusters.csv + trips.csv + journeys.csv + ...
    ↓  src/generate_dashboard.py
output/travel_dashboard.html  ←→  src/server.py  ←→  notes.json
```
