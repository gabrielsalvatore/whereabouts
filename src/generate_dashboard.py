#!/usr/bin/env python3
"""
generate_dashboard.py — Builds travel_dashboard.html with baked-in trip data.

Run any time you update travel_analysis.json:
    python3 generate_dashboard.py
"""
import json
import math
from pathlib import Path

ROOT      = Path(__file__).parent.parent   # travel-tracker/
DATA_PATH = ROOT / "output" / "travel_analysis.json"
CLUS_PATH = ROOT / "output" / "clusters.csv"
TRIP_PATH = ROOT / "output" / "trips.csv"
OUT_PATH  = ROOT / "output" / "travel_dashboard.html"

# ── read base analysis JSON ───────────────────────────────────────────────────
with open(DATA_PATH) as f:
    data = json.load(f)

# ── read clusters CSV for centroid coordinates ────────────────────────────────
import csv

def read_csv(path):
    with open(path) as f:
        return list(csv.DictReader(f))

clusters_raw = read_csv(CLUS_PATH)
trips_raw    = read_csv(TRIP_PATH)

# ── metro area mapping ────────────────────────────────────────────────────────
METRO_AREAS = [
    (34.0522, -118.2437, "Los Angeles",   65),  # Hollywood, Santa Monica, Burbank, OC coast
    (42.3601,  -71.0589, "Boston",        55),  # Cambridge, Somerville, Foxborough
    (40.7128,  -74.0060, "New York City", 55),  # Brooklyn, Queens, Jersey City
    (27.9506,  -82.4572, "Tampa",         45),  # St Pete Beach, Clearwater
    (21.3069, -157.8583, "Honolulu",      70),  # All of Oahu incl. Pupukea, North Shore
    (-23.5505, -46.6333, "São Paulo",     55),  # Guarulhos
    (18.4655,  -66.1057, "San Juan",      65),  # Fajardo, Rincon area
    (33.7490,  -84.3880, "Atlanta",       45),  # Hapeville
    (29.9511,  -90.0715, "New Orleans",   35),  # Arabi, Jefferson, Marrero
    (33.6700,  -86.8000, "Birmingham",    35),  # Dixiana
    (37.7749, -122.4194, "San Francisco", 55),  # Sausalito, Mountain View, Stanford
    (47.6062, -122.3321, "Seattle",       45),
    (38.9072,  -77.0369, "Washington DC", 50),
    (25.7617,  -80.1918, "Miami",         60),  # Homestead, Doral
    (32.7157, -117.1611, "San Diego",     50),  # Coronado, Encinitas, Vista
    (39.9526,  -75.1652, "Philadelphia",  45),
    (41.8781,  -87.6298, "Chicago",       50),
    (29.7604,  -95.3698, "Houston",       50),  # Aldine
    (-9.6658,  -35.7350, "Maceió",        25),
    (40.4406,  -79.9959, "Pittsburgh",    35),  # Bloomfield, Mount Oliver, West View
    (41.4993,  -81.6944, "Cleveland",     40),  # Brook Park, Middleburg Heights
    (37.3382, -121.8863, "San Jose",      35),  # Alum Rock, Seven Trees
    (36.1699, -115.1398, "Las Vegas",     50),  # Paradise, Sandy Valley
    (35.8271,  -78.8010, "Raleigh",       45),  # Durham, Morrisville, Cary
]

def _hav(lat1, lon1, lat2, lon2):
    R = 6371.0088
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a = math.sin(dφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(dλ/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def nearest_metro(lat, lon):
    if lat is None or lon is None:
        return None
    best_name, best_dist = None, float("inf")
    for mlat, mlon, name, radius_km in METRO_AREAS:
        d = _hav(lat, lon, mlat, mlon)
        if d <= radius_km and d < best_dist:
            best_dist, best_name = d, name
    return best_name

# cluster_id → {lat, lon}
centroid = {
    r["cluster_id"]: {"lat": float(r["centroid_lat"]), "lon": float(r["centroid_lon"])}
    for r in clusters_raw
    if r.get("centroid_lat") and r.get("centroid_lon")
}

# ── rebuild places with coordinates ──────────────────────────────────────────
places = []
for c in sorted(clusters_raw, key=lambda x: -int(x["photo_count"])):
    cid = c["cluster_id"]
    co  = centroid.get(cid, {})
    lat = co.get("lat")
    lon = co.get("lon")
    places.append({
        "cluster_id":  cid,
        "place":       c["place_label"],
        "city":        c["city"],
        "state":       c["admin1"],
        "country":     c["country"],
        "metro":       nearest_metro(lat, lon),
        "lat":         lat,
        "lon":         lon,
        "photos":      int(c["photo_count"]),
        "first_visit": c["first_date"],
        "last_visit":  c["last_date"],
    })

# ── rebuild trips with coordinates ───────────────────────────────────────────
trips = []
for t in trips_raw:
    cid   = t.get("cluster_id", "")
    co    = centroid.get(cid, {})
    lat   = co.get("lat")
    lon   = co.get("lon")
    metro = nearest_metro(lat, lon)
    city  = metro or t["city"]
    state = t.get("state", "")
    country = t["country"]
    if metro:
        dest = f"{metro}, {state}, {country}" if state else f"{metro}, {country}"
    else:
        dest = t["place_label"]
    trips.append({
        "cluster_id":  cid,
        "destination": dest,
        "city":        city,
        "country":     country,
        "start":       t["start_date"],
        "end":         t["end_date"],
        "days":        int(t["days"]),
        "lat":         lat,
        "lon":         lon,
    })

# ── inject missing metro trips ────────────────────────────────────────────────
# If a metro-mapped cluster has ≥ MIN_PHOTOS but no trip covers that metro
# during the same date window, add it so big-city visits aren't silently dropped.
from datetime import date as _date

MIN_PHOTOS_FOR_TRIP = 5

def _days_between(d1, d2):
    return (_date.fromisoformat(d2) - _date.fromisoformat(d1)).days + 1

def _overlaps(s1, e1, s2, e2):
    return not (e1 < s2 or s1 > e2)

for c in clusters_raw:
    if int(c["photo_count"]) < MIN_PHOTOS_FOR_TRIP:
        continue
    cid = c["cluster_id"]
    co  = centroid.get(cid, {})
    lat = co.get("lat")
    lon = co.get("lon")
    metro = nearest_metro(lat, lon)
    if not metro:
        continue
    first, last = c["first_date"], c["last_date"]
    # skip if any existing trip already covers this metro in the same window
    if any(t["city"] == metro and _overlaps(first, last, t["start"], t["end"])
           for t in trips):
        continue
    state   = c.get("admin1", "")
    country = c["country"]
    dest    = f"{metro}, {state}, {country}" if state else f"{metro}, {country}"
    trips.append({
        "cluster_id":  cid,
        "destination": dest,
        "city":        metro,
        "country":     country,
        "start":       first,
        "end":         last,
        "days":        _days_between(first, last),
        "lat":         lat,
        "lon":         lon,
    })

trips.sort(key=lambda t: t["start"])

data["places_visited"] = places
data["trips"]          = trips

DATA_JS = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

# ── HTML template ─────────────────────────────────────────────────────────────
HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Travel Dashboard · 2026</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#0d0d12;--sb:#111118;--card:#18181f;--border:#25252f;
  --accent:#6366f1;--accent2:#818cf8;
  --text:#e2e2ee;--muted:#5a5a72;--dim:#888;
  --green:#22c55e;--red:#ef4444;
  font-family:system-ui,-apple-system,'Segoe UI',sans-serif;
  font-size:14px;
}}
body{{background:var(--bg);color:var(--text);height:100vh;overflow:hidden;display:flex}}

/* ── Sidebar ── */
#sidebar{{
  width:280px;min-width:280px;background:var(--sb);
  border-right:1px solid var(--border);
  display:flex;flex-direction:column;overflow:hidden;z-index:10;
}}
.sb-head{{padding:18px 14px 0}}
.sb-title{{font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-bottom:10px}}
.tabs{{display:flex;gap:4px;padding-bottom:12px;border-bottom:1px solid var(--border)}}
.tab{{
  flex:1;padding:6px 0;background:none;border:1px solid var(--border);
  border-radius:6px;color:var(--muted);font-size:12px;font-weight:600;cursor:pointer;
  transition:all .15s;
}}
.tab.on{{background:var(--accent);border-color:var(--accent);color:#fff}}
.tab:hover:not(.on){{color:var(--text);border-color:var(--dim)}}
#sb-list{{flex:1;overflow-y:auto;padding:4px 0}}

/* trip items */
.m-label{{font-size:9px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);padding:10px 14px 3px}}
.t-item{{
  display:flex;align-items:center;gap:9px;padding:7px 14px;
  cursor:pointer;transition:background .1s;position:relative;
}}
.t-item:hover{{background:rgba(99,102,241,.08)}}
.t-item.on{{background:rgba(99,102,241,.14);border-left:2px solid var(--accent)}}
.t-item.on .t-dot{{background:var(--accent)}}
.t-dot{{width:7px;height:7px;border-radius:50%;background:var(--border);flex-shrink:0;transition:background .2s}}
.t-info{{flex:1;min-width:0}}
.t-city{{font-size:13px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.t-sub{{font-size:11px;color:var(--muted);margin-top:1px}}
.t-note{{font-size:11px;opacity:.6}}

/* city items */
.sb-search{{
  margin:8px 10px;padding:7px 10px;
  background:var(--card);border:1px solid var(--border);border-radius:7px;
  color:var(--text);font-size:13px;outline:none;width:calc(100% - 20px);
}}
.sb-search:focus{{border-color:var(--accent)}}
.c-item{{display:flex;align-items:center;justify-content:space-between;padding:7px 14px;cursor:pointer;transition:background .1s}}
.c-item:hover{{background:rgba(99,102,241,.08)}}
.c-item.on{{background:rgba(99,102,241,.14);border-left:2px solid var(--accent)}}
.c-name{{font-size:13px;font-weight:500}}
.c-country{{font-size:11px;color:var(--muted)}}
.c-badge{{font-size:10px;padding:2px 6px;border-radius:8px;background:rgba(99,102,241,.15);color:var(--accent2)}}

/* ── Map area ── */
#map-wrap{{flex:1;position:relative;overflow:hidden}}
#map{{position:absolute;inset:0;z-index:1}}

/* Leaflet dark overrides */
.leaflet-container{{background:#0d0d12}}
.leaflet-control-zoom a{{background:var(--card);border-color:var(--border);color:var(--text)}}
.leaflet-control-zoom a:hover{{background:var(--border)}}
.leaflet-control-attribution{{background:rgba(13,13,18,.7);color:var(--muted);font-size:9px}}
.leaflet-control-attribution a{{color:var(--muted)}}
.leaflet-tooltip{{
  background:var(--card);border:1px solid var(--border);
  color:var(--text);border-radius:6px;font-size:12px;
  padding:4px 8px;box-shadow:0 4px 12px rgba(0,0,0,.5);
}}
.leaflet-tooltip-left:before,.leaflet-tooltip-right:before{{border-right-color:var(--border);border-left-color:var(--border)}}

/* ── Detail panel (slides in from right over the map) ── */
#detail{{
  position:absolute;top:0;right:-420px;bottom:0;width:400px;
  background:var(--sb);border-left:1px solid var(--border);
  z-index:400;display:flex;flex-direction:column;
  transition:right .25s cubic-bezier(.4,0,.2,1);
  box-shadow:-8px 0 32px rgba(0,0,0,.4);
}}
#detail.open{{right:0}}
.d-head{{padding:20px 18px 0;border-bottom:1px solid var(--border);flex-shrink:0}}
.d-close{{
  position:absolute;top:14px;right:14px;
  background:none;border:none;color:var(--muted);font-size:18px;
  cursor:pointer;line-height:1;padding:4px;border-radius:4px;
}}
.d-close:hover{{color:var(--text);background:var(--border)}}
.d-title{{font-size:17px;font-weight:700;margin-bottom:3px;padding-right:28px}}
.d-sub{{font-size:12px;color:var(--muted);margin-bottom:12px}}
.d-tabs{{display:flex}}
.d-tab{{
  padding:8px 12px;background:none;border:none;
  border-bottom:2px solid transparent;color:var(--muted);
  font-size:12px;font-weight:600;cursor:pointer;transition:all .15s;
}}
.d-tab.on{{color:var(--text);border-bottom-color:var(--accent)}}
.d-tab:hover:not(.on){{color:var(--text)}}
#d-body{{flex:1;overflow-y:auto;padding:18px}}

/* cards */
.card{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px;margin-bottom:12px}}
.card-title{{font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:10px}}

/* inputs */
textarea,input[type=text]{{
  width:100%;background:var(--bg);border:1px solid var(--border);
  border-radius:7px;padding:10px 12px;color:var(--text);
  font-size:13px;font-family:inherit;outline:none;transition:border-color .15s;
}}
textarea{{resize:vertical;line-height:1.65}}
textarea:focus,input[type=text]:focus{{border-color:var(--accent)}}

/* buttons */
.btn{{padding:7px 14px;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;border:none;transition:all .15s}}
.btn-p{{background:var(--accent);color:#fff}}
.btn-p:hover{{background:var(--accent2)}}
.btn-g{{background:transparent;border:1px solid var(--border);color:var(--muted)}}
.btn-g:hover{{color:var(--text);border-color:var(--dim)}}
.btn-d{{background:transparent;border:none;color:var(--muted);padding:4px 7px;font-size:12px}}
.btn-d:hover{{color:var(--red)}}

/* lessons */
.lesson{{display:flex;align-items:flex-start;gap:8px;padding:9px 0;border-bottom:1px solid var(--border)}}
.lesson:last-child{{border-bottom:none}}
.l-bullet{{color:var(--accent);flex-shrink:0;margin-top:1px}}
.l-text{{flex:1;font-size:13px;line-height:1.5}}

/* person cards */
.person{{background:var(--bg);border:1px solid var(--border);border-radius:9px;padding:12px 13px;margin-bottom:8px;display:flex;align-items:flex-start;gap:10px}}
.p-avatar{{width:32px;height:32px;border-radius:50%;background:linear-gradient(135deg,var(--accent),#8b5cf6);display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;color:#fff;flex-shrink:0}}
.p-name{{font-size:13px;font-weight:600}}
.p-contact{{font-size:11px;color:var(--accent2);margin-top:2px}}
.p-notes{{font-size:12px;color:var(--dim);margin-top:4px;line-height:1.4}}

/* add-form */
.add-form{{background:var(--card);border:1px solid var(--border);border-radius:9px;padding:13px;margin-bottom:12px;display:none}}
.add-form.open{{display:block}}
.f-row{{display:flex;gap:8px;margin-bottom:8px}}
.f-col{{flex:1}}
.f-label{{font-size:10px;color:var(--muted);margin-bottom:4px;display:block;font-weight:600;letter-spacing:.06em;text-transform:uppercase}}

/* flash */
.flash{{font-size:11px;color:var(--green);opacity:0;transition:opacity .3s;display:inline-block;margin-left:8px}}
.flash.show{{opacity:1}}

/* scrollbar */
::-webkit-scrollbar{{width:5px}}
::-webkit-scrollbar-track{{background:transparent}}
::-webkit-scrollbar-thumb{{background:var(--border);border-radius:3px}}
::-webkit-scrollbar-thumb:hover{{background:var(--dim)}}
</style>
</head>
<body>

<!-- SIDEBAR -->
<aside id="sidebar">
  <div class="sb-head">
    <div class="sb-title">2026 Travel</div>
    <div class="tabs">
      <button class="tab on" onclick="switchTab('trips')">✈️ Trips</button>
      <button class="tab"    onclick="switchTab('people')">👥 People</button>
    </div>
  </div>
  <div id="sb-list"></div>
</aside>

<!-- MAP + DETAIL PANEL -->
<div id="map-wrap">
  <div id="map"></div>

  <div id="detail">
    <button class="d-close" onclick="closeDetail()">✕</button>
    <div class="d-head">
      <div class="d-title" id="d-title"></div>
      <div class="d-sub"   id="d-sub"></div>
      <div class="d-tabs">
        <button class="d-tab on" onclick="setDTab('journal')">Journal</button>
        <button class="d-tab"    onclick="setDTab('lessons')">Lessons</button>
        <button class="d-tab"    onclick="setDTab('companions')" id="d-tab-with">With</button>
        <button class="d-tab"    onclick="setDTab('people')" id="d-tab-people">People</button>
      </div>
    </div>
    <div id="d-body"></div>
  </div>
</div>

<script>
const DATA = {DATA_JS};

// ── Storage ───────────────────────────────────────────────────────────────────
let NOTES = {{trips:{{}},people:{{}}}};

async function initNotes(){{
  try{{
    const r = await fetch('/api/notes');
    if(r.ok) NOTES = await r.json();
    NOTES.trips        = NOTES.trips        || {{}};
    NOTES.people       = NOTES.people       || {{}};
    NOTES.trip_deleted = NOTES.trip_deleted || [];
    NOTES.trip_renamed = NOTES.trip_renamed || {{}};
    NOTES.trip_added   = NOTES.trip_added   || [];
  }} catch(e){{ console.warn('Server not reachable; notes will not persist.', e) }}
}}

function persist(d){{
  NOTES = d;
  fetch('/api/notes',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(d)}})
    .catch(e=>console.error('Save failed:',e));
}}

function ann(){{ return NOTES }}
function tripKey(t){{ return t.start+'_'+(t.cluster_id||t.city) }}

let _tripsById = {{}};
function effectiveTrips(){{
  const a=ann();
  const deleted=new Set(a.trip_deleted||[]);
  const renamed=a.trip_renamed||{{}};
  const base=DATA.trips
    .filter(t=>!deleted.has(tripKey(t)))
    .map(t=>{{ const k=tripKey(t); return renamed[k]?{{...t,destination:renamed[k]}}:t; }});
  const all=[...base,...(a.trip_added||[])].sort((a,b)=>a.start.localeCompare(b.start));
  _tripsById={{}};
  all.forEach(t=>{{ _tripsById[tripKey(t)]=t; }});
  return all;
}}

// ── State ─────────────────────────────────────────────────────────────────────
let sbTab='trips', selTrip=null, selCity=null, dTab='journal';

// ── Leaflet map ───────────────────────────────────────────────────────────────
let leafMap, markersByCluster={{}}, activeMarker=null;

function initMap(){{
  leafMap = L.map('map',{{zoomControl:false,preferCanvas:true}});
  L.control.zoom({{position:'bottomright'}}).addTo(leafMap);

  L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png',{{
    attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> © <a href="https://carto.com/">CARTO</a>',
    maxZoom:19
  }}).addTo(leafMap);

  const bounds=[];

  DATA.places_visited.forEach(place=>{{
    if(place.lat==null||place.lon==null) return;
    const r = Math.max(5, Math.min(22, Math.log2(place.photos+1)*2.8));
    const m = L.circleMarker([place.lat,place.lon],{{
      radius:r, fillColor:'#6366f1', color:'rgba(255,255,255,0.5)',
      weight:1.5, fillOpacity:0.72, interactive:true
    }});
    m.bindTooltip(`<b>${{place.metro||place.city}}</b><br>${{place.photos}} photos`,{{sticky:true}});
    m.on('click',()=>onMarkerClick(place.cluster_id));
    markersByCluster[place.cluster_id]=m;
    m.addTo(leafMap);
    bounds.push([place.lat,place.lon]);
  }});

  if(bounds.length) leafMap.fitBounds(bounds,{{padding:[30,30]}});
}}

function onMarkerClick(cid){{
  if(sbTab==='people'){{
    const place=DATA.places_visited.find(p=>p.cluster_id===cid);
    if(place) selC(place.metro||place.city);
  }} else {{
    const trip=Object.values(_tripsById).find(t=>t.cluster_id===cid);
    if(trip) selT(trip);
  }}
}}

function highlightMarker(cid){{
  if(activeMarker){{
    activeMarker.setStyle({{fillColor:'#6366f1',color:'rgba(255,255,255,0.5)',weight:1.5,fillOpacity:0.72}});
  }}
  activeMarker = markersByCluster[cid]||null;
  if(activeMarker){{
    activeMarker.setStyle({{fillColor:'#f59e0b',color:'#fff',weight:2,fillOpacity:0.95}});
    activeMarker.bringToFront();
  }}
}}

function filterMapForPeople(){{
  const locs=(ann().locations)||[];
  Object.values(markersByCluster).forEach(m=>{{ if(leafMap.hasLayer(m)) leafMap.removeLayer(m); }});
  // one dot per city: pick the most-photographed place for each location label
  const bestByLoc={{}};
  DATA.places_visited.forEach(place=>{{
    if(place.lat==null||place.lon==null) return;
    const label=place.metro||place.city;
    if(!locs.includes(label)) return;
    if(!bestByLoc[label]||place.photos>bestByLoc[label].photos) bestByLoc[label]=place;
  }});
  const bounds=[];
  Object.values(bestByLoc).forEach(place=>{{
    const m=markersByCluster[place.cluster_id];
    if(!m) return;
    m.addTo(leafMap);
    bounds.push([place.lat,place.lon]);
  }});
  if(bounds.length) leafMap.fitBounds(bounds,{{padding:[30,30]}});
}}

function showAllMarkers(){{
  const bounds=[];
  DATA.places_visited.forEach(place=>{{
    if(place.lat==null||place.lon==null) return;
    const m=markersByCluster[place.cluster_id];
    if(m&&!leafMap.hasLayer(m)) m.addTo(leafMap);
    bounds.push([place.lat,place.lon]);
  }});
  if(bounds.length) leafMap.fitBounds(bounds,{{padding:[30,30]}});
}}

// ── Sidebar ───────────────────────────────────────────────────────────────────
function switchTab(t){{
  sbTab=t;
  document.querySelectorAll('.tab').forEach((el,i)=>el.classList.toggle('on',(i===0&&t==='trips')||(i===1&&t==='people')));
  renderSB();  // seeds locations if first visit
  if(t==='people') filterMapForPeople();
  else showAllMarkers();
}}

function renderSB(){{ sbTab==='trips'?renderTripsSB():renderPeopleSB() }}

function renderTripsSB(){{
  const a=ann();
  const trips=effectiveTrips();
  const groups={{}};
  trips.forEach(t=>{{
    const d=new Date(t.start+'T12:00:00');
    const mo=d.toLocaleString('en-US',{{month:'long',year:'numeric'}});
    if(!groups[mo]) groups[mo]=[];
    groups[mo].push(t);
  }});
  let h=`
  <div style="padding:8px 10px 4px;display:flex;justify-content:flex-end">
    <button class="btn btn-p" style="padding:5px 10px;font-size:12px" onclick="showAddTrip()">+ Add</button>
  </div>
  <div id="add-trip-wrap" style="display:none;padding:8px 10px;background:var(--card);margin:0 8px 8px;border-radius:8px;border:1px solid var(--border)">
    <div style="font-size:12px;color:var(--muted);margin-bottom:8px;font-weight:600">New trip</div>
    <input type="text" id="at-dest" placeholder="City / destination" style="margin-bottom:6px">
    <input type="text" id="at-country" placeholder="Country code (e.g. US, BR, PR)" style="margin-bottom:6px">
    <div style="display:flex;gap:6px;margin-bottom:8px">
      <input type="date" id="at-start" style="flex:1">
      <input type="date" id="at-end" style="flex:1">
    </div>
    <div style="display:flex;gap:6px">
      <button class="btn btn-p" style="flex:1;font-size:12px" onclick="saveAddTrip()">Save</button>
      <button class="btn btn-g" style="font-size:12px" onclick="hideAddTrip()">Cancel</button>
    </div>
  </div>`;
  for(const [mo,mTrips] of Object.entries(groups)){{
    h+=`<div class="m-label">${{mo}}</div>`;
    mTrips.forEach(t=>{{
      const k=tripKey(t), isOn=selTrip&&tripKey(selTrip)===k;
      const ta=a.trips[k]||{{}};
      const hasNote=(Array.isArray(ta.journal)?ta.journal.length:!!ta.journal)||(ta.lessons&&ta.lessons.length)||(ta.companions&&ta.companions.length);
      h+=`<div class="t-item ${{isOn?'on':''}}" id="tr-${{k}}">
        <div class="t-dot" style="cursor:pointer" onclick="selTByKey('${{k}}')"></div>
        <div class="t-info" style="cursor:pointer;flex:1;min-width:0" onclick="selTByKey('${{k}}')">
          <div class="t-city">${{flag(t.country)}} ${{esc(t.destination||t.city)}}</div>
          <div class="t-sub">${{fdate(t.start)}}${{t.start!==t.end?' → '+fdate(t.end):''}} · ${{t.days}}d</div>
        </div>
        ${{hasNote?'<span class="t-note">📝</span>':''}}
        <button class="btn btn-g" style="padding:3px 6px;font-size:11px;flex-shrink:0" title="Edit" onclick="event.stopPropagation();showEditTrip('${{k}}')">✎</button>
        <button class="btn btn-d" style="padding:3px 6px;font-size:11px;flex-shrink:0" title="Delete" onclick="event.stopPropagation();deleteTrip('${{k}}')">✕</button>
      </div>`;
    }});
  }}
  document.getElementById('sb-list').innerHTML=h;
}}

function selTByKey(k){{ const t=_tripsById[k]; if(t) selT(t); }}

function showAddTrip(){{
  document.getElementById('add-trip-wrap').style.display='block';
  document.getElementById('at-dest').focus();
}}
function hideAddTrip(){{ document.getElementById('add-trip-wrap').style.display='none'; }}
function saveAddTrip(){{
  const dest=document.getElementById('at-dest').value.trim();
  const country=(document.getElementById('at-country').value.trim()||'US').toUpperCase();
  const start=document.getElementById('at-start').value;
  const end=document.getElementById('at-end').value||start;
  if(!dest||!start){{ alert('Destination and start date are required.'); return; }}
  const days=Math.max(1,Math.round((new Date(end)-new Date(start))/(86400000))+1);
  const trip={{
    cluster_id:'custom_'+Date.now(), destination:dest, city:dest.split(',')[0].trim(),
    country, start, end, days, lat:null, lon:null
  }};
  const a=ann(); a.trip_added=[...(a.trip_added||[]),trip]; persist(a);
  hideAddTrip(); renderSB(); selT(trip);
}}

function showEditTrip(k){{
  const t=_tripsById[k]; if(!t) return;
  const row=document.getElementById('tr-'+k); if(!row) return;
  row.innerHTML=`
    <div style="flex:1;padding:2px 0">
      <input type="text" id="et-dest-${{k}}" value="${{esc(t.destination||t.city)}}" style="margin-bottom:4px"
        onkeydown="if(event.key==='Enter')saveEditTrip('${{k}}');if(event.key==='Escape')renderSB()">
      <div style="display:flex;gap:4px">
        <input type="date" id="et-start-${{k}}" value="${{t.start}}" style="flex:1;font-size:11px">
        <input type="date" id="et-end-${{k}}" value="${{t.end}}" style="flex:1;font-size:11px">
      </div>
    </div>
    <button class="btn btn-p" style="padding:4px 8px;font-size:11px;flex-shrink:0" onclick="saveEditTrip('${{k}}')">Save</button>
    <button class="btn btn-g" style="padding:4px 8px;font-size:11px;flex-shrink:0" onclick="renderSB()">✕</button>`;
  document.getElementById('et-dest-'+k).select();
}}
function saveEditTrip(k){{
  const dest=document.getElementById('et-dest-'+k)?.value.trim();
  const start=document.getElementById('et-start-'+k)?.value;
  const end=document.getElementById('et-end-'+k)?.value;
  if(!dest) return;
  const a=ann();
  // check if custom trip or base trip
  const isCustom=(a.trip_added||[]).some(t=>tripKey(t)===k);
  if(isCustom){{
    const idx=(a.trip_added||[]).findIndex(t=>tripKey(t)===k);
    if(idx>=0){{
      const days=Math.max(1,Math.round((new Date(end)-new Date(start))/(86400000))+1);
      a.trip_added[idx]={{...a.trip_added[idx],destination:dest,city:dest.split(',')[0].trim(),start,end,days}};
    }}
  }} else {{
    a.trip_renamed=a.trip_renamed||{{}};
    a.trip_renamed[k]=dest;
  }}
  persist(a); renderSB();
  if(selTrip&&tripKey(selTrip)===k){{ effectiveTrips(); selT(_tripsById[k]||selTrip); openDetail(); }}
}}
function deleteTrip(k){{
  const t=_tripsById[k];
  if(!confirm(`Remove "${{t?.destination||t?.city}}"?`)) return;
  const a=ann();
  const isCustom=(a.trip_added||[]).some(t=>tripKey(t)===k);
  if(isCustom) a.trip_added=(a.trip_added||[]).filter(t=>tripKey(t)!==k);
  else {{ a.trip_deleted=[...(a.trip_deleted||[]),k]; }}
  persist(a);
  if(selTrip&&tripKey(selTrip)===k){{ selTrip=null; closeDetail(); }}
  renderSB();
}}

// ── People sidebar: user-managed location list ────────────────────────────────
// Locations are stored in NOTES.locations (ordered array of label strings).
// On first load they are seeded from places_visited: top city per state,
// so you get "Honolulu" not "Waimanalo Beach", "São Paulo" not "Guarulhos", etc.

function seedLocations(a){{
  const seen=new Set(), locations=[];
  DATA.places_visited.forEach(p=>{{
    if(!p.metro) return;  // skip small towns with no metro mapping
    if(!seen.has(p.metro)){{ seen.add(p.metro); locations.push(p.metro); }}
  }});
  a.locations=locations;
}}

function renderPeopleSB(){{
  const a=ann();
  if(!a.locations||a.locations.length===0) {{ seedLocations(a); persist(a); }}

  let h=`
  <div style="padding:8px 10px 4px;display:flex;gap:6px">
    <input class="sb-search" id="loc-search" placeholder="Search…" oninput="filterLocs(this.value)" style="flex:1">
    <button class="btn btn-p" style="padding:6px 10px;font-size:12px" title="Add location" onclick="showAddLoc()">+</button>
  </div>
  <div id="add-loc-wrap" style="display:none;padding:0 10px 8px">
    <input type="text" id="add-loc-inp" placeholder="Location name (e.g. Boston, Oahu…)"
      onkeydown="if(event.key==='Enter')saveNewLoc();if(event.key==='Escape')hideAddLoc()">
    <div style="display:flex;gap:6px;margin-top:6px">
      <button class="btn btn-p" style="flex:1;font-size:12px" onclick="saveNewLoc()">Add</button>
      <button class="btn btn-g" style="font-size:12px" onclick="hideAddLoc()">Cancel</button>
    </div>
  </div>
  <div id="loc-list">`;

  a.locations.forEach((loc,idx)=>{{
    const cnt=(a.people[loc]||[]).length;
    const isOn=selCity===loc;
    h+=`<div class="c-item ${{isOn?'on':''}}" id="loc-${{idx}}">
      <div style="flex:1;cursor:pointer;min-width:0" onclick="selC('${{esc(loc)}}')">
        <div class="c-name" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
          ${{esc(loc)}}
        </div>
        <div class="c-country">${{cnt}} contact${{cnt!==1?'s':''}}</div>
      </div>
      <div style="display:flex;align-items:center;gap:1px;flex-shrink:0">
        ${{cnt?`<span class="c-badge" style="margin-right:4px">${{cnt}}</span>`:''}}
        <button class="btn btn-d" style="padding:3px 6px;font-size:13px" title="Rename"
          onclick="startRename(${{idx}},event)">✎</button>
        <button class="btn btn-d" style="padding:3px 6px;font-size:13px" title="Remove"
          onclick="deleteLoc(${{idx}})">✕</button>
      </div>
    </div>`;
  }});

  h+='</div>';
  document.getElementById('sb-list').innerHTML=h;
}}

function filterLocs(q){{
  const ql=q.toLowerCase();
  document.querySelectorAll('#loc-list .c-item').forEach(el=>{{
    const name=el.querySelector('.c-name');
    if(name) el.style.display=name.textContent.toLowerCase().includes(ql)?'':'none';
  }});
}}

function showAddLoc(){{
  document.getElementById('add-loc-wrap').style.display='block';
  document.getElementById('add-loc-inp').focus();
}}
function hideAddLoc(){{
  document.getElementById('add-loc-wrap').style.display='none';
  document.getElementById('add-loc-inp').value='';
}}
function saveNewLoc(){{
  const val=document.getElementById('add-loc-inp').value.trim();
  if(!val) return;
  const a=ann();
  if(!a.locations.includes(val)) a.locations.push(val);
  persist(a); hideAddLoc(); renderSB(); filterMapForPeople(); selC(val);
}}

function startRename(idx,evt){{
  evt.stopPropagation();
  const a=ann(), loc=a.locations[idx];
  const el=document.getElementById('loc-'+idx);
  el.innerHTML=`
    <input type="text" id="ren-inp" value="${{esc(loc)}}" style="flex:1"
      onkeydown="if(event.key==='Enter')saveRename(${{idx}});if(event.key==='Escape')renderSB()">
    <button class="btn btn-p" style="padding:4px 9px;font-size:12px" onclick="saveRename(${{idx}})">Save</button>
    <button class="btn btn-g" style="padding:4px 9px;font-size:12px" onclick="renderSB()">✕</button>`;
  document.getElementById('ren-inp').select();
}}

function saveRename(idx){{
  const newName=document.getElementById('ren-inp').value.trim();
  if(!newName) return;
  const a=ann(), oldName=a.locations[idx];
  if(newName!==oldName){{
    // Move contacts to new name
    if(a.people[oldName]){{
      a.people[newName]=[...(a.people[newName]||[]),...a.people[oldName]];
      delete a.people[oldName];
    }}
    a.locations[idx]=newName;
    if(selCity===oldName) selCity=newName;
  }}
  persist(a); renderSB(); filterMapForPeople();
  if(selCity===newName) openCityDetail(newName);
}}

function deleteLoc(idx){{
  const a=ann(), loc=a.locations[idx];
  const cnt=(a.people[loc]||[]).length;
  const msg=cnt
    ?`Remove "${{loc}}"? This will delete ${{cnt}} contact${{cnt!==1?'s':''}}.`
    :`Remove "${{loc}}"?`;
  if(!confirm(msg)) return;
  a.locations.splice(idx,1);
  delete a.people[loc];
  if(selCity===loc){{ selCity=null; closeDetail(); }}
  persist(a); renderSB(); filterMapForPeople();
}}

// ── Selection ─────────────────────────────────────────────────────────────────
function selT(trip){{
  selTrip=trip; dTab='journal';
  // switch sidebar to trips tab if needed
  if(sbTab!=='trips'){{ sbTab='trips'; document.querySelectorAll('.tab').forEach((el,i)=>el.classList.toggle('on',i===0)); showAllMarkers(); }}
  renderSB();
  openDetail();
  highlightMarker(selTrip.cluster_id);
  // pan map to marker
  if(selTrip.lat&&selTrip.lon) leafMap.panTo([selTrip.lat,selTrip.lon],{{animate:true,duration:.5}});
}}

function selC(city){{
  selCity=city; renderSB(); openCityDetail(city);
}}

// ── Detail panel ──────────────────────────────────────────────────────────────
function openDetail(){{
  const t=selTrip, k=tripKey(t), a=ann();
  const ta=a.trips[k]||{{journal:'',lessons:[]}};
  const people=(a.people[t.city]||[]);

  document.getElementById('d-title').textContent=`${{flag(t.country)}} ${{t.destination}}`;
  document.getElementById('d-sub').textContent=
    `${{fdate(t.start)}}${{t.start!==t.end?' → '+fdate(t.end):''}} · ${{t.days}} day${{t.days>1?'s':''}} · ${{t.country}}`;

  // update tab badges
  const companions=ta.companions||[];
  document.getElementById('d-tab-with').textContent=companions.length?`With (${{companions.length}})`:'With';
  const ptab=document.getElementById('d-tab-people');
  ptab.textContent=people.length?`People (${{people.length}})`:'People';

  // sync tab highlights
  document.querySelectorAll('.d-tab').forEach((el,i)=>el.classList.toggle('on',
    (i===0&&dTab==='journal')||(i===1&&dTab==='lessons')||(i===2&&dTab==='companions')||(i===3&&dTab==='people')));

  document.getElementById('d-body').innerHTML=renderDTab(dTab,ta,people,t);
  document.getElementById('detail').classList.add('open');
}}

function openCityDetail(city){{
  const a=ann(), people=a.people[city]||[];

  // Best place for this city: match on metro label or exact city, pick most-photographed
  const place=DATA.places_visited
    .filter(p=>(p.metro||p.city)===city)
    .sort((a,b)=>b.photos-a.photos)[0]
    ||DATA.places_visited.find(p=>p.city===city);
  const country=place?.country||DATA.trips.find(t=>t.city===city)?.country||'';

  document.getElementById('d-title').textContent=`${{flag(country)}} ${{city}}`;
  document.getElementById('d-sub').textContent=country;

  // show only people tab for city view
  document.querySelectorAll('.d-tab').forEach((el,i)=>el.classList.toggle('on',i===2));
  document.getElementById('d-tab-people').textContent=people.length?`People (${{people.length}})`:'People';
  document.getElementById('d-body').innerHTML=renderPeopleTab(people,city);
  document.getElementById('detail').classList.add('open');

  // Pan map to the city's marker
  if(place?.lat&&place?.lon){{
    highlightMarker(place.cluster_id);
    leafMap.panTo([place.lat,place.lon],{{animate:true,duration:.5}});
  }}
}}

function closeDetail(){{
  document.getElementById('detail').classList.remove('open');
  if(activeMarker){{
    activeMarker.setStyle({{fillColor:'#6366f1',color:'rgba(255,255,255,0.5)',weight:1.5,fillOpacity:0.72}});
    activeMarker=null;
  }}
  selTrip=null; selCity=null;
  renderSB();
}}

function setDTab(t){{
  dTab=t;
  if(selTrip) openDetail();
  else if(selCity) openCityDetail(selCity);
}}

// ── Detail tab renderers ──────────────────────────────────────────────────────
function renderDTab(tab,ta,people,t){{
  if(tab==='journal'){{
    // migrate old string format → array
    const raw=ta.journal;
    const entries=Array.isArray(raw)?raw:(raw?[{{text:raw,date:''}}]:[]);
    const entryHTML=entries.map((e,i)=>`
      <div class="lesson">
        <div style="flex:1">
          ${{e.date?`<div style="font-size:11px;color:var(--muted);margin-bottom:3px">${{e.date}}</div>`:''}}
          <div style="white-space:pre-wrap">${{esc(e.text)}}</div>
        </div>
        <button class="btn btn-d" onclick="rmEntry(${{i}})">✕</button>
      </div>`).join('');
    return `<div class="card">
      <div class="card-title">Journal</div>
      ${{entries.length===0?'<div style="color:var(--muted);font-size:13px;padding:4px 0 10px">What happened? How did it feel? What surprised you…</div>':''}}
      <div id="j-list">${{entryHTML}}</div>
      <div style="margin-top:10px">
        <textarea id="j-input" rows="4" placeholder="Add an entry…"></textarea>
        <div style="display:flex;gap:8px;margin-top:8px">
          <button class="btn btn-p" onclick="addEntry()">Add</button>
        </div>
      </div>
    </div>`;
  }}

  if(tab==='lessons'){{
    const ls=ta.lessons||[];
    return `<div class="card">
    <div class="card-title">Lessons &amp; Takeaways</div>
    ${{ls.length===0?'<div style="color:var(--muted);font-size:13px;padding:4px 0 10px">What did this trip teach you?</div>':''}}
    <div id="l-list">${{ls.map((l,i)=>`<div class="lesson"><span class="l-bullet">◆</span><span class="l-text">${{esc(l)}}</span><button class="btn btn-d" onclick="rmLesson(${{i}})">✕</button></div>`).join('')}}</div>
    <div style="display:flex;gap:8px;margin-top:10px">
      <input type="text" id="l-input" placeholder="Add a lesson…" onkeydown="if(event.key==='Enter')addLesson()">
      <button class="btn btn-p" onclick="addLesson()">Add</button>
    </div></div>`;
  }}

  if(tab==='companions') return renderCompanionsTab(ta.companions||[]);
  if(tab==='people') return renderPeopleTab(people,t.city);
  return '';
}}

function renderPeopleTab(people,city){{
  const cards=people.map(p=>`
  <div class="person">
    <div class="p-avatar">${{p.name[0].toUpperCase()}}</div>
    <div style="flex:1">
      <div class="p-name">${{esc(p.name)}}</div>
      ${{p.contact?`<div class="p-contact">${{esc(p.contact)}}</div>`:''}}
      ${{p.notes?`<div class="p-notes">${{esc(p.notes)}}</div>`:''}}
    </div>
    <button class="btn btn-d" onclick="rmPerson('${{esc(city)}}','${{p.id}}')">✕</button>
  </div>`).join('');

  return `
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
    <span style="font-size:12px;color:var(--muted)">People in ${{esc(city)}}</span>
    <button class="btn btn-p" onclick="toggleForm('pf')">+ Add</button>
  </div>
  <div class="add-form" id="pf">
    <div class="f-row">
      <div class="f-col"><label class="f-label">Name *</label><input type="text" id="pf-name" placeholder="Full name"></div>
      <div class="f-col"><label class="f-label">Contact</label><input type="text" id="pf-contact" placeholder="@handle or email"></div>
    </div>
    <div style="margin-bottom:9px"><label class="f-label">Notes</label>
      <input type="text" id="pf-notes" placeholder="How you know them, what to catch up on…"></div>
    <div style="display:flex;gap:8px">
      <button class="btn btn-p" onclick="addPerson('${{esc(city)}}')">Save</button>
      <button class="btn btn-g" onclick="toggleForm('pf')">Cancel</button>
    </div>
  </div>
  ${{people.length===0?`<div style="text-align:center;padding:32px 0;color:var(--muted)">👤<br><br>No contacts for ${{esc(city)}} yet</div>`:''}}
  <div id="people-list">${{cards}}</div>`;
}}

// ── Actions ───────────────────────────────────────────────────────────────────
function _journalEntries(ta){{
  const raw=ta.journal;
  return Array.isArray(raw)?raw:(raw?[{{text:raw,date:''}}]:[]);
}}
function _renderJList(entries){{
  document.getElementById('j-list').innerHTML=entries.map((e,i)=>`
    <div class="lesson">
      <div style="flex:1">
        ${{e.date?`<div style="font-size:11px;color:var(--muted);margin-bottom:3px">${{e.date}}</div>`:''}}
        <div style="white-space:pre-wrap">${{esc(e.text)}}</div>
      </div>
      <button class="btn btn-d" onclick="rmEntry(${{i}})">✕</button>
    </div>`).join('');
}}
function addEntry(){{
  const inp=document.getElementById('j-input'), txt=inp.value.trim(); if(!txt) return;
  const k=tripKey(selTrip), a=ann();
  a.trips[k]=a.trips[k]||{{}};
  const entries=_journalEntries(a.trips[k]);
  entries.push({{text:txt, date:new Date().toISOString().slice(0,10)}});
  a.trips[k].journal=entries;
  persist(a); inp.value=''; inp.focus();
  _renderJList(entries); renderSB();
}}
function rmEntry(idx){{
  const k=tripKey(selTrip), a=ann();
  const entries=_journalEntries(a.trips[k]);
  entries.splice(idx,1);
  a.trips[k].journal=entries;
  persist(a); _renderJList(entries); renderSB();
}}

function addLesson(){{
  const inp=document.getElementById('l-input'), txt=inp.value.trim(); if(!txt) return;
  const k=tripKey(selTrip), a=ann();
  a.trips[k]=a.trips[k]||{{}};
  a.trips[k].lessons=a.trips[k].lessons||[];
  a.trips[k].lessons.push(txt); persist(a); inp.value='';
  document.getElementById('l-list').innerHTML=a.trips[k].lessons.map((l,i)=>`
    <div class="lesson"><span class="l-bullet">◆</span><span class="l-text">${{esc(l)}}</span>
    <button class="btn btn-d" onclick="rmLesson(${{i}})">✕</button></div>`).join('');
  renderSB();
}}

function rmLesson(idx){{
  const k=tripKey(selTrip), a=ann();
  a.trips[k].lessons.splice(idx,1); persist(a);
  document.getElementById('l-list').innerHTML=a.trips[k].lessons.map((l,i)=>`
    <div class="lesson"><span class="l-bullet">◆</span><span class="l-text">${{esc(l)}}</span>
    <button class="btn btn-d" onclick="rmLesson(${{i}})">✕</button></div>`).join('');
  renderSB();
}}

// ── Travel companions ──────────────────────────────────────────────────────────
function _cmpListHTML(companions){{
  return companions.map((c,i)=>`
    <div class="lesson">
      <div style="flex:1">
        <div style="font-weight:500">${{esc(c.name)}}</div>
        ${{c.note?`<div style="font-size:12px;color:var(--muted);margin-top:2px">${{esc(c.note)}}</div>`:''}}
      </div>
      <button class="btn btn-d" onclick="rmCompanion(${{i}})">✕</button>
    </div>`).join('');
}}
function renderCompanionsTab(companions){{
  return `<div class="card">
    <div class="card-title">Traveled with</div>
    ${{companions.length===0?'<div style="color:var(--muted);font-size:13px;padding:4px 0 10px">Who came on this trip?</div>':''}}
    <div id="cmp-list">${{_cmpListHTML(companions)}}</div>
    <div style="margin-top:12px;display:flex;flex-direction:column;gap:6px">
      <input type="text" id="cmp-name" placeholder="Name"
        onkeydown="if(event.key==='Enter')addCompanion()">
      <input type="text" id="cmp-note" placeholder="Note (optional)"
        onkeydown="if(event.key==='Enter')addCompanion()">
      <button class="btn btn-p" onclick="addCompanion()">Add</button>
    </div>
  </div>`;
}}
function addCompanion(){{
  const nameEl=document.getElementById('cmp-name');
  const name=nameEl.value.trim(); if(!name) return;
  const note=document.getElementById('cmp-note').value.trim();
  const k=tripKey(selTrip), a=ann();
  a.trips[k]=a.trips[k]||{{}};
  a.trips[k].companions=a.trips[k].companions||[];
  a.trips[k].companions.push({{id:'cmp_'+Date.now(),name,note}});
  persist(a);
  nameEl.value=''; document.getElementById('cmp-note').value=''; nameEl.focus();
  document.getElementById('cmp-list').innerHTML=_cmpListHTML(a.trips[k].companions);
  const c=a.trips[k].companions;
  document.getElementById('d-tab-with').textContent=c.length?`With (${{c.length}})`:'With';
  renderSB();
}}
function rmCompanion(idx){{
  const k=tripKey(selTrip), a=ann();
  a.trips[k].companions.splice(idx,1); persist(a);
  document.getElementById('cmp-list').innerHTML=_cmpListHTML(a.trips[k].companions);
  const c=a.trips[k].companions;
  document.getElementById('d-tab-with').textContent=c.length?`With (${{c.length}})`:'With';
  renderSB();
}}

function toggleForm(id){{ document.getElementById(id).classList.toggle('open') }}

function addPerson(city){{
  const name=document.getElementById('pf-name').value.trim(); if(!name) return;
  const contact=document.getElementById('pf-contact').value.trim();
  const notes=document.getElementById('pf-notes').value.trim();
  const a=ann();
  a.people[city]=a.people[city]||[];
  a.people[city].push({{id:Date.now().toString(),name,contact,notes}});
  // Register this city in the People sidebar if not already there
  if(!a.locations) a.locations=[];
  if(!a.locations.includes(city)) a.locations.push(city);
  persist(a);
  if(selTrip) openDetail(); else openCityDetail(city);
  renderSB();
}}

function rmPerson(city,id){{
  const a=ann(); a.people[city]=(a.people[city]||[]).filter(p=>p.id!==id); persist(a);
  if(selTrip) openDetail(); else openCityDetail(city);
  renderSB();
}}

// ── Helpers ───────────────────────────────────────────────────────────────────
function fdate(d){{ return new Date(d+'T12:00:00').toLocaleDateString('en-US',{{month:'short',day:'numeric'}}) }}
function esc(s){{ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') }}
function flag(cc){{ if(!cc) return ''; try{{ return cc.toUpperCase().replace(/./g,c=>String.fromCodePoint(c.charCodeAt(0)+127397)) }}catch{{return ''}} }}

// ── Boot ──────────────────────────────────────────────────────────────────────
initNotes().then(()=>{{ effectiveTrips(); initMap(); renderSB(); }});
</script>
</body>
</html>"""

OUT_PATH.write_text(HTML, encoding="utf-8")
print(f"✓ Generated: {OUT_PATH}")
print(f"  Size: {OUT_PATH.stat().st_size / 1024:.0f} KB")
