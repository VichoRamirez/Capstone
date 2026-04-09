"""
Build a time-dependent traffic-factor dataset using routing APIs.

Outputs:
- Raw samples CSV (one row per API query).
- Aggregated profile CSV (weekday/hour/zone statistics).
- Model-ready JSON (hour factors + zone factors normalized around 1.0).

The factor is defined as:
    traffic_factor = live_duration_min / freeflow_duration_min
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import random
import ssl
import statistics
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CLEAN_DATA_DIR = PROJECT_ROOT / "CLEAN_DATA"
DEFAULT_OUTPUT_DIR = DEFAULT_CLEAN_DATA_DIR / "traffic_profiles"
DEFAULT_DEPOT_LAT = -33.4489
DEFAULT_DEPOT_LON = -70.6693
USER_AGENT = "Capstone-Traffic-Profile/1.0"


_ZONE_COMUNAS = {
    "centro": {
        "santiago",
        "estacion central",
        "recoleta",
        "independencia",
        "quinta normal",
    },
    "oriente": {
        "las condes",
        "vitacura",
        "lo barnechea",
        "providencia",
        "nunoa",
        "la reina",
        "penalolen",
        "macul",
    },
    "poniente": {
        "maipu",
        "pudahuel",
        "cerrillos",
        "lo prado",
        "cerro navia",
        "renca",
        "quinta normal",
    },
    "sur": {
        "san miguel",
        "san joaquin",
        "la florida",
        "la granja",
        "san ramon",
        "la cisterna",
        "el bosque",
        "pedro aguirre cerda",
        "puente alto",
    },
    "norte": {
        "quilicura",
        "huechuraba",
        "conchali",
        "colina",
        "lampa",
        "tiltil",
    },
}


_WEEKDAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "lunes": 0,
    "lun": 0,
    "tue": 1,
    "tuesday": 1,
    "martes": 1,
    "mar": 1,
    "wed": 2,
    "wednesday": 2,
    "miercoles": 2,
    "miércoles": 2,
    "mie": 2,
    "thu": 3,
    "thursday": 3,
    "jueves": 3,
    "jue": 3,
    "fri": 4,
    "friday": 4,
    "viernes": 4,
    "vie": 4,
    "sat": 5,
    "saturday": 5,
    "sabado": 5,
    "sábado": 5,
    "sab": 5,
    "sun": 6,
    "sunday": 6,
    "domingo": 6,
    "dom": 6,
}

_WEEKDAY_LABEL = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lon: float
    zone_tag: str
    comuna: str
    source_file: str
    row_idx: int


@dataclass(frozen=True)
class AddressCandidate:
    address: str
    comuna: str
    source_file: str
    row_idx: int


@dataclass(frozen=True)
class QueryJob:
    weekday_idx: int
    weekday_label: str
    hour: int
    zone_tag: str
    origin_lat: float
    origin_lon: float
    destination_lat: float
    destination_lon: float
    departure_iso: str


@dataclass(frozen=True)
class RouteTiming:
    duration_live_min: float
    duration_freeflow_min: float
    distance_km: float

    @property
    def factor(self) -> float:
        den = max(1e-9, float(self.duration_freeflow_min))
        return float(self.duration_live_min / den)


def _normalize_text(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    norm = unicodedata.normalize("NFKD", raw)
    norm = norm.encode("ascii", "ignore").decode("ascii")
    norm = "".join(ch if ch.isalnum() else " " for ch in norm)
    return " ".join(norm.split())


def _infer_zone_from_comuna(comuna_raw: str) -> str:
    comuna = _normalize_text(comuna_raw)
    if not comuna:
        return "metropolitana"
    for zone_tag, comunas in _ZONE_COMUNAS.items():
        if comuna in comunas:
            return zone_tag
    return "metropolitana"


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(" ", "")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        if text.count(",") == 1:
            head, tail = text.split(",", 1)
            if len(tail) <= 2:
                text = f"{head}.{tail}"
            else:
                text = f"{head}{tail}"
        else:
            text = text.replace(",", "")
    try:
        return float(text)
    except Exception:
        return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    rlat1, rlon1 = math.radians(lat1), math.radians(lon1)
    rlat2, rlon2 = math.radians(lat2), math.radians(lon2)
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = math.sin(dlat / 2.0) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2.0) ** 2
    return r * 2.0 * math.asin(math.sqrt(max(0.0, a)))


def _load_geocache(path: Path) -> tuple[dict[str, list[Any]], dict[str, tuple[float, float]]]:
    if not path.exists():
        return {}, {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception:
        return {}, {}
    if not isinstance(raw, dict):
        return {}, {}

    cache_exact: dict[str, list[Any]] = {}
    cache_norm: dict[str, tuple[float, float]] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            continue
        lat = _as_float(value[0])
        lon = _as_float(value[1])
        if lat is None or lon is None:
            cache_exact[key] = [None, None]
            continue
        lat_f = float(lat)
        lon_f = float(lon)
        cache_exact[key] = [lat_f, lon_f]
        cache_norm[_normalize_text(key)] = (lat_f, lon_f)
    return cache_exact, cache_norm


def _save_geocache(path: Path, cache_exact: dict[str, list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(cache_exact, fh, ensure_ascii=False, indent=2)


def _build_cache_keys(address: str, comuna: str) -> list[str]:
    addr = str(address or "").strip()
    com = str(comuna or "").strip()
    variants = []
    if addr and com:
        variants.append(f"{addr}|{com}")
    if addr:
        variants.append(f"{addr}|Santiago")
    norm_addr = _normalize_text(addr)
    norm_com = _normalize_text(com)
    if norm_addr and norm_com:
        variants.append(f"{norm_addr}|{norm_com}")
    if norm_addr:
        variants.append(f"{norm_addr}|santiago")

    out = []
    seen = set()
    for item in variants:
        if item and item not in seen:
            out.append(item)
            seen.add(item)
    return out


def _lookup_geocache(
    cache_exact: dict[str, list[Any]],
    cache_norm: dict[str, tuple[float, float]],
    address: str,
    comuna: str,
) -> Optional[tuple[float, float]]:
    for key in _build_cache_keys(address, comuna):
        if key in cache_exact:
            value = cache_exact.get(key) or [None, None]
            lat = _as_float(value[0])
            lon = _as_float(value[1])
            if lat is None or lon is None:
                continue
            return float(lat), float(lon)
        norm_key = _normalize_text(key)
        if norm_key in cache_norm:
            lat, lon = cache_norm[norm_key]
            return float(lat), float(lon)
    return None


def _geocode_nominatim(
    *,
    address: str,
    comuna: str,
    nominatim_url: str,
    timeout_sec: float,
) -> Optional[tuple[float, float]]:
    base = str(nominatim_url or "").strip().rstrip("/")
    if not base:
        return None
    query = f"{address}, {comuna}, Region Metropolitana, Chile"
    params = {
        "q": query,
        "format": "jsonv2",
        "limit": "1",
        "countrycodes": "cl",
        "addressdetails": "0",
    }
    url = f"{base}/search?{urllib.parse.urlencode(params)}"
    payload = _http_json(url, timeout_sec=float(timeout_sec))
    if isinstance(payload, dict) and isinstance(payload.get("error"), str):
        return None
    if isinstance(payload, dict) and "lat" in payload and "lon" in payload:
        lat = _as_float(payload.get("lat"))
        lon = _as_float(payload.get("lon"))
        if lat is not None and lon is not None:
            return float(lat), float(lon)
    if isinstance(payload, list):
        if not payload:
            return None
        item = payload[0] or {}
        lat = _as_float(item.get("lat"))
        lon = _as_float(item.get("lon"))
        if lat is not None and lon is not None:
            return float(lat), float(lon)
    return None


def _discover_csvs(clean_data_dir: Path) -> list[Path]:
    if not clean_data_dir.exists():
        return []
    out = sorted(p for p in clean_data_dir.glob("*.csv") if p.is_file())
    return out


def _pick_column(fieldnames: list[str], candidates: list[str]) -> Optional[str]:
    mapped = {_normalize_text(name): name for name in fieldnames}
    for cand in candidates:
        key = _normalize_text(cand)
        if key in mapped:
            return mapped[key]
    for key_norm, original in mapped.items():
        for cand in candidates:
            c_norm = _normalize_text(cand)
            if c_norm and c_norm in key_norm:
                return original
    return None


def _read_points_from_csv(path: Path, max_rows: int) -> tuple[list[GeoPoint], list[AddressCandidate]]:
    points: list[GeoPoint] = []
    addresses: list[AddressCandidate] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except Exception:
            dialect = csv.excel
        reader = csv.DictReader(fh, dialect=dialect)
        if not reader.fieldnames:
            return points, addresses

        lat_col = _pick_column(reader.fieldnames, ["Latitud", "Latitude", "Lat"])
        lon_col = _pick_column(reader.fieldnames, ["Longitud", "Longitude", "Lon", "Lng", "Long"])
        comuna_col = _pick_column(reader.fieldnames, ["Comuna", "Municipio", "District"])
        address_col = _pick_column(
            reader.fieldnames,
            [
                "Direccion cliente",
                "Dirección cliente",
                "Direccion",
                "Dirección",
                "Address",
                "Street",
            ],
        )

        row_idx = 0
        for row in reader:
            row_idx += 1
            if max_rows > 0 and row_idx > max_rows:
                break

            comuna = str(row.get(comuna_col, "") or "").strip() if comuna_col else ""
            if lat_col and lon_col:
                lat = _as_float(row.get(lat_col))
                lon = _as_float(row.get(lon_col))
                if lat is not None and lon is not None:
                    if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
                        zone_tag = _infer_zone_from_comuna(comuna)
                        points.append(
                            GeoPoint(
                                lat=float(lat),
                                lon=float(lon),
                                zone_tag=str(zone_tag),
                                comuna=str(comuna),
                                source_file=str(path.name),
                                row_idx=int(row_idx),
                            )
                        )
                        continue

            if address_col:
                address = str(row.get(address_col, "") or "").strip()
                if address:
                    addresses.append(
                        AddressCandidate(
                            address=str(address),
                            comuna=str(comuna or "Santiago"),
                            source_file=str(path.name),
                            row_idx=int(row_idx),
                        )
                    )
    return points, addresses


def _parse_hours(hours_text: str) -> list[int]:
    text = str(hours_text or "").strip()
    if not text:
        return list(range(24))
    out: list[int] = []
    for chunk in text.split(","):
        part = chunk.strip()
        if not part:
            continue
        if "-" in part:
            a_s, b_s = part.split("-", 1)
            a = int(a_s)
            b = int(b_s)
            lo = max(0, min(a, b))
            hi = min(23, max(a, b))
            out.extend(range(lo, hi + 1))
        else:
            val = int(part)
            if 0 <= val <= 23:
                out.append(val)
    dedup = sorted(set(out))
    if not dedup:
        raise ValueError("No valid hour found in --hours")
    return dedup


def _parse_weekdays(weekdays_text: str) -> list[int]:
    raw = str(weekdays_text or "").strip()
    if not raw:
        return [1, 2, 3]
    out: list[int] = []
    for token in raw.split(","):
        key = _normalize_text(token)
        if not key:
            continue
        if key not in _WEEKDAY_ALIASES:
            raise ValueError(f"Invalid weekday token: {token}")
        out.append(int(_WEEKDAY_ALIASES[key]))
    dedup = sorted(set(out))
    if not dedup:
        raise ValueError("No valid weekday found in --weekdays")
    return dedup


def _nearest_weekday_date(today: dt.date, weekday_idx: int) -> dt.date:
    delta = (int(weekday_idx) - int(today.weekday())) % 7
    return today + dt.timedelta(days=delta)


def _iso_departure_for_hour(date_value: dt.date, hour: int, tz_name: str) -> str:
    tz = ZoneInfo(str(tz_name))
    depart = dt.datetime(
        date_value.year,
        date_value.month,
        date_value.day,
        int(hour),
        0,
        0,
        tzinfo=tz,
    )
    return depart.isoformat(timespec="seconds")


def _http_json(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[dict[str, str]] = None,
    payload: Optional[dict[str, Any]] = None,
    timeout_sec: float = 20.0,
) -> Any:
    data_bytes: Optional[bytes] = None
    req_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    if headers:
        req_headers.update(headers)
    if payload is not None:
        data_bytes = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, method=str(method).upper(), headers=req_headers, data=data_bytes)
    if url.startswith("https://"):
        ctx = ssl.create_default_context()
        try:
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        except Exception:
            pass
        with urllib.request.urlopen(req, timeout=float(timeout_sec), context=ctx) as resp:
            body = resp.read().decode("utf-8")
    else:
        with urllib.request.urlopen(req, timeout=float(timeout_sec)) as resp:
            body = resp.read().decode("utf-8")
    return json.loads(body)


def _parse_duration_seconds(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    if text.endswith("s"):
        text = text[:-1]
    try:
        return float(text)
    except Exception:
        return 0.0


def _first_number(mapping: dict[str, Any], keys: tuple[str, ...]) -> float:
    for key in keys:
        if key not in mapping:
            continue
        val = _as_float(mapping.get(key))
        if val is not None:
            return float(val)
    return 0.0


class RoutingProvider:
    name = "base"

    def query(
        self,
        *,
        origin_lat: float,
        origin_lon: float,
        destination_lat: float,
        destination_lon: float,
        departure_iso: str,
    ) -> RouteTiming:
        raise NotImplementedError


class TomTomRoutingProvider(RoutingProvider):
    name = "tomtom"

    def __init__(self, api_key: str, timeout_sec: float = 20.0):
        self.api_key = str(api_key or "").strip()
        self.timeout_sec = float(timeout_sec)
        if not self.api_key:
            raise ValueError("Missing TomTom API key")

    def query(
        self,
        *,
        origin_lat: float,
        origin_lon: float,
        destination_lat: float,
        destination_lon: float,
        departure_iso: str,
    ) -> RouteTiming:
        point_pair = f"{origin_lat:.6f},{origin_lon:.6f}:{destination_lat:.6f},{destination_lon:.6f}"
        point_pair_enc = urllib.parse.quote(point_pair, safe=":,.")
        base = f"https://api.tomtom.com/routing/1/calculateRoute/{point_pair_enc}/json"
        params = {
            "key": self.api_key,
            "traffic": "true",
            "travelMode": "car",
            "routeType": "fastest",
            "computeBestOrder": "false",
            "departAt": str(departure_iso),
        }
        url = f"{base}?{urllib.parse.urlencode(params)}"
        payload = _http_json(url, timeout_sec=self.timeout_sec)
        if not isinstance(payload, dict):
            raise RuntimeError("TomTom response is not a JSON object")
        routes = payload.get("routes") or []
        if not routes:
            raise RuntimeError("TomTom response without routes")
        summary = (routes[0] or {}).get("summary") or {}
        if not isinstance(summary, dict):
            raise RuntimeError("TomTom summary missing")

        live_sec = _first_number(
            summary,
            (
                "travelTimeInSeconds",
                "trafficTimeInSeconds",
                "travelTimeSeconds",
            ),
        )
        free_sec = _first_number(
            summary,
            (
                "noTrafficTravelTimeInSeconds",
                "trafficFreeFlowTravelTimeInSeconds",
                "freeFlowTravelTimeInSeconds",
                "noTrafficTimeInSeconds",
            ),
        )
        dist_m = _first_number(summary, ("lengthInMeters", "distanceInMeters"))
        if live_sec <= 0.0:
            raise RuntimeError("TomTom live duration missing")
        if free_sec <= 0.0:
            free_sec = live_sec

        return RouteTiming(
            duration_live_min=float(live_sec / 60.0),
            duration_freeflow_min=float(free_sec / 60.0),
            distance_km=float(dist_m / 1000.0) if dist_m > 0 else 0.0,
        )


class GoogleRoutesProvider(RoutingProvider):
    name = "google_routes"

    def __init__(self, api_key: str, timeout_sec: float = 20.0):
        self.api_key = str(api_key or "").strip()
        self.timeout_sec = float(timeout_sec)
        if not self.api_key:
            raise ValueError("Missing Google Routes API key")

    def query(
        self,
        *,
        origin_lat: float,
        origin_lon: float,
        destination_lat: float,
        destination_lon: float,
        departure_iso: str,
    ) -> RouteTiming:
        url = "https://routes.googleapis.com/directions/v2:computeRoutes"
        payload = {
            "origin": {
                "location": {
                    "latLng": {
                        "latitude": float(origin_lat),
                        "longitude": float(origin_lon),
                    }
                }
            },
            "destination": {
                "location": {
                    "latLng": {
                        "latitude": float(destination_lat),
                        "longitude": float(destination_lon),
                    }
                }
            },
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
            "departureTime": str(departure_iso),
            "computeAlternativeRoutes": False,
            "languageCode": "es-CL",
            "units": "METRIC",
        }
        headers = {
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": "routes.duration,routes.staticDuration,routes.distanceMeters",
        }
        body = _http_json(
            url,
            method="POST",
            headers=headers,
            payload=payload,
            timeout_sec=self.timeout_sec,
        )
        if not isinstance(body, dict):
            raise RuntimeError("Google Routes response is not a JSON object")
        routes = body.get("routes") or []
        if not routes:
            raise RuntimeError("Google Routes response without routes")
        route0 = routes[0] or {}
        if not isinstance(route0, dict):
            raise RuntimeError("Google Routes route payload invalid")
        live_sec = _parse_duration_seconds(route0.get("duration"))
        free_sec = _parse_duration_seconds(route0.get("staticDuration"))
        dist_m = _first_number(route0, ("distanceMeters",))
        if live_sec <= 0.0:
            raise RuntimeError("Google live duration missing")
        if free_sec <= 0.0:
            free_sec = live_sec

        return RouteTiming(
            duration_live_min=float(live_sec / 60.0),
            duration_freeflow_min=float(free_sec / 60.0),
            distance_km=float(dist_m / 1000.0) if dist_m > 0 else 0.0,
        )


def _build_provider(name: str, api_key: str, timeout_sec: float) -> RoutingProvider:
    provider_name = str(name or "").strip().lower()
    if provider_name == "tomtom":
        return TomTomRoutingProvider(api_key=api_key, timeout_sec=timeout_sec)
    if provider_name in {"google", "google_routes", "google-routes"}:
        return GoogleRoutesProvider(api_key=api_key, timeout_sec=timeout_sec)
    raise ValueError(f"Unknown provider: {name}")


def _sample_jobs(
    *,
    points_by_zone: dict[str, list[GeoPoint]],
    weekdays: list[int],
    hours: list[int],
    timezone_name: str,
    samples_per_zone_hour: int,
    min_pair_km: float,
    depot_lat: float,
    depot_lon: float,
    depot_share: float,
    seed: int,
) -> list[QueryJob]:
    rng = random.Random(int(seed))
    all_points = [p for items in points_by_zone.values() for p in items]
    if not all_points:
        return []

    zones = sorted(points_by_zone.keys())
    jobs: list[QueryJob] = []
    today = dt.datetime.now(ZoneInfo(str(timezone_name))).date()

    for weekday_idx in weekdays:
        day_date = _nearest_weekday_date(today, int(weekday_idx))
        weekday_label = _WEEKDAY_LABEL[int(weekday_idx)]

        for hour in hours:
            depart_iso = _iso_departure_for_hour(day_date, int(hour), str(timezone_name))
            for zone_tag in zones:
                zone_points = points_by_zone.get(zone_tag, []) or all_points
                for _ in range(max(1, int(samples_per_zone_hour))):
                    dst = rng.choice(zone_points)
                    origin_lat = float(depot_lat)
                    origin_lon = float(depot_lon)

                    if rng.random() > float(depot_share):
                        for _attempt in range(10):
                            cand = rng.choice(all_points)
                            d_km = _haversine_km(cand.lat, cand.lon, dst.lat, dst.lon)
                            if d_km >= float(min_pair_km):
                                origin_lat = float(cand.lat)
                                origin_lon = float(cand.lon)
                                break

                    if _haversine_km(origin_lat, origin_lon, dst.lat, dst.lon) < float(min_pair_km):
                        continue

                    jobs.append(
                        QueryJob(
                            weekday_idx=int(weekday_idx),
                            weekday_label=str(weekday_label),
                            hour=int(hour),
                            zone_tag=str(zone_tag),
                            origin_lat=float(origin_lat),
                            origin_lon=float(origin_lon),
                            destination_lat=float(dst.lat),
                            destination_lon=float(dst.lon),
                            departure_iso=str(depart_iso),
                        )
                    )
    return jobs


def _quantile(values: list[float], q: float) -> float:
    vals = sorted(float(v) for v in values)
    if not vals:
        return 0.0
    if len(vals) == 1:
        return float(vals[0])
    pos = (len(vals) - 1) * max(0.0, min(1.0, float(q)))
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(vals[lo])
    frac = pos - lo
    return float(vals[lo] * (1.0 - frac) + vals[hi] * frac)


def _clamp(value: float, lo: float, hi: float) -> float:
    return float(max(float(lo), min(float(hi), float(value))))


def _aggregate_samples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    for row in samples:
        key = (
            str(row.get("weekday", "")),
            int(row.get("hour", 0)),
            str(row.get("zone_tag", "metropolitana")),
        )
        grouped.setdefault(key, []).append(row)

    out: list[dict[str, Any]] = []
    for key in sorted(grouped.keys(), key=lambda x: (x[0], int(x[1]), x[2])):
        weekday, hour, zone_tag = key
        rows = grouped[key]
        factors = [float(r.get("traffic_factor", 1.0)) for r in rows]
        live_min = [float(r.get("duration_live_min", 0.0)) for r in rows]
        free_min = [float(r.get("duration_freeflow_min", 0.0)) for r in rows]
        dist = [float(r.get("distance_km", 0.0)) for r in rows]

        out.append(
            {
                "weekday": str(weekday),
                "hour": int(hour),
                "zone_tag": str(zone_tag),
                "samples": int(len(rows)),
                "factor_min": round(float(min(factors)), 6),
                "factor_p10": round(_quantile(factors, 0.10), 6),
                "factor_median": round(_quantile(factors, 0.50), 6),
                "factor_mean": round(float(sum(factors) / len(factors)), 6),
                "factor_p90": round(_quantile(factors, 0.90), 6),
                "factor_max": round(float(max(factors)), 6),
                "factor_std": round(float(statistics.pstdev(factors)) if len(factors) > 1 else 0.0, 6),
                "duration_live_min_mean": round(float(sum(live_min) / len(live_min)), 4),
                "duration_freeflow_min_mean": round(float(sum(free_min) / len(free_min)), 4),
                "distance_km_mean": round(float(sum(dist) / len(dist)), 4),
            }
        )
    return out


def _build_model_ready_profile(
    aggregated_rows: list[dict[str, Any]],
    *,
    profile_name: str,
    provider_name: str,
    timezone_name: str,
    generation_meta: dict[str, Any],
) -> dict[str, Any]:
    if not aggregated_rows:
        raise ValueError("No aggregated rows to build profile")

    factors_all = [float(r.get("factor_median", 1.0)) for r in aggregated_rows]
    base_global = statistics.median(factors_all) if factors_all else 1.0
    base_global = max(1e-6, float(base_global))

    by_hour: dict[int, list[float]] = {}
    by_zone: dict[str, list[float]] = {}
    grid: list[dict[str, Any]] = []

    for row in aggregated_rows:
        hour = int(row.get("hour", 0))
        zone = str(row.get("zone_tag", "metropolitana"))
        val = float(row.get("factor_median", 1.0))
        by_hour.setdefault(hour, []).append(val)
        by_zone.setdefault(zone, []).append(val)
        grid.append(
            {
                "weekday": str(row.get("weekday", "")),
                "hour": int(hour),
                "zone_tag": str(zone),
                "traffic_factor_median": round(val, 6),
                "samples": int(row.get("samples", 0)),
            }
        )

    hour_factors = []
    for hour in sorted(by_hour.keys()):
        med = statistics.median(by_hour[hour])
        norm = _clamp(float(med / base_global), 0.72, 2.35)
        hour_factors.append(
            {
                "hour": int(hour),
                "factor": round(float(norm), 6),
                "raw_median_factor": round(float(med), 6),
            }
        )

    zone_factors = {}
    zone_stats = []
    for zone in sorted(by_zone.keys()):
        med = statistics.median(by_zone[zone])
        norm = _clamp(float(med / base_global), 0.72, 2.35)
        zone_factors[zone] = round(float(norm), 6)
        zone_stats.append(
            {
                "zone_tag": str(zone),
                "factor": round(float(norm), 6),
                "raw_median_factor": round(float(med), 6),
            }
        )

    return {
        "profile_name": str(profile_name),
        "provider": str(provider_name),
        "timezone": str(timezone_name),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "normalization_base_median_factor": round(float(base_global), 6),
        "hour_factors": hour_factors,
        "zone_factors": zone_factors,
        "zone_factors_verbose": zone_stats,
        "grid": grid,
        "meta": generation_meta,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        with path.open("w", encoding="utf-8", newline="") as fh:
            fh.write("")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def _enrich_points_from_addresses(
    *,
    address_candidates: list[AddressCandidate],
    geocache_path: Path,
    live_geocode: bool,
    nominatim_url: str,
    geocode_timeout_sec: float,
    geocode_sleep_sec: float,
    max_geocode_queries: int,
) -> tuple[list[GeoPoint], dict[str, Any]]:
    cache_exact, cache_norm = _load_geocache(geocache_path)
    points: list[GeoPoint] = []

    dedup: dict[tuple[str, str], AddressCandidate] = {}
    for item in address_candidates:
        key = (_normalize_text(item.address), _normalize_text(item.comuna))
        if key[0]:
            dedup.setdefault(key, item)

    cache_hits = 0
    live_hits = 0
    live_attempts = 0
    live_failures = 0
    stored_cache_updates = 0

    for cand in dedup.values():
        address = str(cand.address or "").strip()
        comuna = str(cand.comuna or "Santiago").strip() or "Santiago"
        hit = _lookup_geocache(cache_exact, cache_norm, address, comuna)
        if hit is not None:
            lat, lon = hit
            points.append(
                GeoPoint(
                    lat=float(lat),
                    lon=float(lon),
                    zone_tag=_infer_zone_from_comuna(comuna),
                    comuna=str(comuna),
                    source_file=str(cand.source_file),
                    row_idx=int(cand.row_idx),
                )
            )
            cache_hits += 1
            continue

        if not live_geocode:
            continue
        if live_attempts >= int(max_geocode_queries):
            continue

        live_attempts += 1
        coords = None
        try:
            coords = _geocode_nominatim(
                address=address,
                comuna=comuna,
                nominatim_url=nominatim_url,
                timeout_sec=float(geocode_timeout_sec),
            )
        except Exception:
            coords = None
        if coords is None:
            live_failures += 1
            cache_exact[f"{address}|{comuna}"] = [None, None]
        else:
            lat, lon = coords
            if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
                points.append(
                    GeoPoint(
                        lat=float(lat),
                        lon=float(lon),
                        zone_tag=_infer_zone_from_comuna(comuna),
                        comuna=str(comuna),
                        source_file=str(cand.source_file),
                        row_idx=int(cand.row_idx),
                    )
                )
                live_hits += 1
                cache_exact[f"{address}|{comuna}"] = [float(lat), float(lon)]
                cache_norm[_normalize_text(f"{address}|{comuna}")] = (float(lat), float(lon))
                stored_cache_updates += 1
            else:
                live_failures += 1
                cache_exact[f"{address}|{comuna}"] = [None, None]

        if geocode_sleep_sec > 0.0:
            time.sleep(float(geocode_sleep_sec))

    if stored_cache_updates > 0:
        _save_geocache(geocache_path, cache_exact)

    meta = {
        "address_candidates_total": int(len(address_candidates)),
        "address_candidates_unique": int(len(dedup)),
        "geocache_hits": int(cache_hits),
        "live_geocode_enabled": bool(live_geocode),
        "live_geocode_attempts": int(live_attempts),
        "live_geocode_hits": int(live_hits),
        "live_geocode_failures": int(live_failures),
        "geocache_updates": int(stored_cache_updates),
        "geocache_path": str(geocache_path),
    }
    return points, meta


def build_dataset(args: argparse.Namespace) -> dict[str, Any]:
    input_csvs: list[Path] = []
    if args.sales_csv:
        input_csvs = [Path(p).expanduser().resolve() for p in args.sales_csv]
    else:
        input_csvs = _discover_csvs(Path(args.clean_data_dir).expanduser().resolve())
    if not input_csvs:
        raise RuntimeError("No CSV files found. Provide --sales-csv or verify CLEAN_DATA path.")

    all_points: list[GeoPoint] = []
    all_addresses: list[AddressCandidate] = []
    for csv_path in input_csvs:
        if not csv_path.exists():
            continue
        pts, addrs = _read_points_from_csv(csv_path, max_rows=int(args.max_rows_per_csv))
        all_points.extend(pts)
        all_addresses.extend(addrs)

    geocode_meta = {
        "address_candidates_total": int(len(all_addresses)),
        "address_candidates_unique": 0,
        "geocache_hits": 0,
        "live_geocode_enabled": bool(args.live_geocode),
        "live_geocode_attempts": 0,
        "live_geocode_hits": 0,
        "live_geocode_failures": 0,
        "geocache_updates": 0,
        "geocache_path": str(Path(args.geocache_path).expanduser().resolve()),
    }

    if not all_points and all_addresses:
        geocoded_points, geocode_meta = _enrich_points_from_addresses(
            address_candidates=all_addresses,
            geocache_path=Path(args.geocache_path).expanduser().resolve(),
            live_geocode=bool(args.live_geocode and (not args.dry_run)),
            nominatim_url=str(args.nominatim_url),
            geocode_timeout_sec=float(args.geocode_timeout_sec),
            geocode_sleep_sec=float(args.geocode_sleep_sec),
            max_geocode_queries=int(args.max_geocode_queries),
        )
        all_points.extend(geocoded_points)

    if not all_points:
        raise RuntimeError(
            "No valid points found. Include lat/lon columns or enable address geocoding fallback."
        )

    rng = random.Random(int(args.seed))
    rng.shuffle(all_points)
    points_by_zone: dict[str, list[GeoPoint]] = {}
    for p in all_points:
        points_by_zone.setdefault(p.zone_tag, []).append(p)
    for zone in list(points_by_zone.keys()):
        cap = int(args.max_points_per_zone)
        if cap > 0 and len(points_by_zone[zone]) > cap:
            points_by_zone[zone] = points_by_zone[zone][:cap]

    weekdays = _parse_weekdays(args.weekdays)
    hours = _parse_hours(args.hours)

    jobs = _sample_jobs(
        points_by_zone=points_by_zone,
        weekdays=weekdays,
        hours=hours,
        timezone_name=str(args.timezone),
        samples_per_zone_hour=int(args.samples_per_zone_hour),
        min_pair_km=float(args.min_pair_km),
        depot_lat=float(args.depot_lat),
        depot_lon=float(args.depot_lon),
        depot_share=float(args.depot_share),
        seed=int(args.seed),
    )
    if not jobs:
        raise RuntimeError("No OD jobs generated. Try lowering --min-pair-km or increasing data.")

    if int(args.max_queries) > 0:
        jobs = jobs[: int(args.max_queries)]

    provider: Optional[RoutingProvider] = None
    provider_name = str(args.provider).strip().lower()
    if not args.dry_run:
        api_key = str(args.api_key or "").strip()
        if not api_key:
            if provider_name == "tomtom":
                api_key = str(os.getenv("TOMTOM_API_KEY", "")).strip()
            else:
                api_key = str(os.getenv("GOOGLE_MAPS_API_KEY", "")).strip()
        provider = _build_provider(provider_name, api_key=api_key, timeout_sec=float(args.request_timeout_sec))

    samples: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    cache: dict[tuple[Any, ...], RouteTiming] = {}
    req_sleep = max(0.0, float(args.request_sleep_sec))
    retries = max(0, int(args.max_retries))

    started_at = time.time()
    for idx, job in enumerate(jobs, start=1):
        cache_key = (
            round(float(job.origin_lat), 5),
            round(float(job.origin_lon), 5),
            round(float(job.destination_lat), 5),
            round(float(job.destination_lon), 5),
            str(job.departure_iso),
            str(provider_name),
        )
        timing: Optional[RouteTiming] = None

        if cache_key in cache:
            timing = cache[cache_key]
        elif args.dry_run:
            base_km = _haversine_km(job.origin_lat, job.origin_lon, job.destination_lat, job.destination_lon)
            base_min = 60.0 * base_km / 30.0
            synthetic = 1.0 + (0.35 if 7 <= int(job.hour) <= 9 or 17 <= int(job.hour) <= 19 else 0.1)
            timing = RouteTiming(
                duration_live_min=float(base_min * synthetic),
                duration_freeflow_min=float(base_min),
                distance_km=float(base_km),
            )
            cache[cache_key] = timing
        else:
            assert provider is not None
            last_err = ""
            for attempt in range(retries + 1):
                try:
                    timing = provider.query(
                        origin_lat=float(job.origin_lat),
                        origin_lon=float(job.origin_lon),
                        destination_lat=float(job.destination_lat),
                        destination_lon=float(job.destination_lon),
                        departure_iso=str(job.departure_iso),
                    )
                    cache[cache_key] = timing
                    break
                except Exception as exc:
                    last_err = str(exc)
                    if attempt < retries:
                        time.sleep(min(4.0, 0.8 * (2 ** attempt)))
            if timing is None:
                failed.append(
                    {
                        "index": int(idx),
                        "weekday": str(job.weekday_label),
                        "hour": int(job.hour),
                        "zone_tag": str(job.zone_tag),
                        "departure_iso": str(job.departure_iso),
                        "origin_lat": round(float(job.origin_lat), 6),
                        "origin_lon": round(float(job.origin_lon), 6),
                        "destination_lat": round(float(job.destination_lat), 6),
                        "destination_lon": round(float(job.destination_lon), 6),
                        "error": str(last_err or "query_failed"),
                    }
                )
                continue
            if req_sleep > 0:
                time.sleep(req_sleep)

        assert timing is not None
        factor = _clamp(float(timing.factor), 0.5, 4.0)
        samples.append(
            {
                "provider": str(provider_name),
                "weekday": str(job.weekday_label),
                "hour": int(job.hour),
                "zone_tag": str(job.zone_tag),
                "departure_iso": str(job.departure_iso),
                "origin_lat": round(float(job.origin_lat), 6),
                "origin_lon": round(float(job.origin_lon), 6),
                "destination_lat": round(float(job.destination_lat), 6),
                "destination_lon": round(float(job.destination_lon), 6),
                "distance_km": round(float(timing.distance_km), 4),
                "duration_live_min": round(float(timing.duration_live_min), 4),
                "duration_freeflow_min": round(float(timing.duration_freeflow_min), 4),
                "traffic_factor": round(float(factor), 6),
            }
        )

    elapsed_sec = time.time() - started_at
    if not samples:
        raise RuntimeError("No successful samples were collected from API queries.")

    aggregated = _aggregate_samples(samples)
    generation_meta = {
        "queries_requested": int(len(jobs)),
        "queries_success": int(len(samples)),
        "queries_failed": int(len(failed)),
        "elapsed_sec": round(float(elapsed_sec), 3),
        "provider": str(provider_name),
        "timezone": str(args.timezone),
        "weekdays": [str(_WEEKDAY_LABEL[w]) for w in weekdays],
        "hours": [int(h) for h in hours],
        "samples_per_zone_hour": int(args.samples_per_zone_hour),
        "min_pair_km": float(args.min_pair_km),
        "depot": [float(args.depot_lat), float(args.depot_lon)],
        "data_sources": [str(p) for p in input_csvs],
        "geocoding": geocode_meta,
        "dry_run": bool(args.dry_run),
    }
    model_ready = _build_model_ready_profile(
        aggregated_rows=aggregated,
        profile_name=str(args.profile_name),
        provider_name=str(provider_name),
        timezone_name=str(args.timezone),
        generation_meta=generation_meta,
    )

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    samples_path = out_dir / f"traffic_factor_samples_{ts}.csv"
    aggregated_path = out_dir / f"traffic_factor_profile_{ts}.csv"
    profile_path = out_dir / f"traffic_profile_model_ready_{ts}.json"
    failed_path = out_dir / f"traffic_factor_failed_{ts}.csv"

    _write_csv(samples_path, samples)
    _write_csv(aggregated_path, aggregated)
    _write_json(profile_path, model_ready)
    if failed:
        _write_csv(failed_path, failed)

    return {
        "samples_path": str(samples_path),
        "aggregated_path": str(aggregated_path),
        "profile_path": str(profile_path),
        "failed_path": str(failed_path) if failed else "",
        "meta": generation_meta,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build hourly traffic-factor datasets with API timings "
            "(live traffic vs free-flow baseline)."
        )
    )
    parser.add_argument(
        "--provider",
        default="tomtom",
        choices=["tomtom", "google_routes"],
        help="Routing API provider.",
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="API key for selected provider. If missing, uses env var (TOMTOM_API_KEY or GOOGLE_MAPS_API_KEY).",
    )
    parser.add_argument(
        "--clean-data-dir",
        default=str(DEFAULT_CLEAN_DATA_DIR),
        help="Directory used to auto-discover CSVs when --sales-csv is not provided.",
    )
    parser.add_argument(
        "--sales-csv",
        action="append",
        default=[],
        help=(
            "CSV path with either Latitud/Longitud/Comuna columns or "
            "Direccion+Comuna columns (for geocode fallback). Can be repeated."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Output directory for generated dataset files.",
    )
    parser.add_argument(
        "--profile-name",
        default="santiago_api_calibrated_v1",
        help="Name embedded in the model-ready profile JSON.",
    )
    parser.add_argument(
        "--timezone",
        default="America/Santiago",
        help="IANA timezone for departure timestamps.",
    )
    parser.add_argument(
        "--weekdays",
        default="TUE,WED,THU",
        help="Comma-separated weekdays (e.g., MON,TUE,WED). Spanish names are also accepted.",
    )
    parser.add_argument(
        "--hours",
        default="6-21",
        help="Hours to sample. Format examples: '6-21' or '7,8,9,17,18,19'.",
    )
    parser.add_argument(
        "--samples-per-zone-hour",
        type=int,
        default=6,
        help="OD queries sampled per zone and hour.",
    )
    parser.add_argument(
        "--min-pair-km",
        type=float,
        default=2.0,
        help="Skip OD pairs shorter than this distance (km).",
    )
    parser.add_argument(
        "--depot-lat",
        type=float,
        default=float(DEFAULT_DEPOT_LAT),
        help="Depot latitude used for most sampled origins.",
    )
    parser.add_argument(
        "--depot-lon",
        type=float,
        default=float(DEFAULT_DEPOT_LON),
        help="Depot longitude used for most sampled origins.",
    )
    parser.add_argument(
        "--depot-share",
        type=float,
        default=0.7,
        help="Fraction [0..1] of sampled queries that start at depot.",
    )
    parser.add_argument(
        "--max-rows-per-csv",
        type=int,
        default=20000,
        help="Max rows read per input CSV before sampling.",
    )
    parser.add_argument(
        "--max-points-per-zone",
        type=int,
        default=600,
        help="Cap points retained per zone before OD sampling.",
    )
    parser.add_argument(
        "--geocache-path",
        default=str(PROJECT_ROOT / "geocache.json"),
        help="Path to geocode cache JSON used by address fallback.",
    )
    parser.add_argument(
        "--live-geocode",
        action="store_true",
        help="Allow live Nominatim geocoding for addresses missing in cache.",
    )
    parser.add_argument(
        "--nominatim-url",
        default=str(os.getenv("NOMINATIM_URL", "https://nominatim.openstreetmap.org")),
        help="Base URL for Nominatim geocoding API.",
    )
    parser.add_argument(
        "--geocode-timeout-sec",
        type=float,
        default=10.0,
        help="HTTP timeout per live geocoding request.",
    )
    parser.add_argument(
        "--geocode-sleep-sec",
        type=float,
        default=1.0,
        help="Sleep between live geocoding requests (respect public rate limits).",
    )
    parser.add_argument(
        "--max-geocode-queries",
        type=int,
        default=500,
        help="Max live geocoding requests allowed in one run.",
    )
    parser.add_argument(
        "--max-queries",
        type=int,
        default=0,
        help="Hard cap on total API queries (0 = no cap).",
    )
    parser.add_argument(
        "--request-timeout-sec",
        type=float,
        default=18.0,
        help="HTTP timeout per API request.",
    )
    parser.add_argument(
        "--request-sleep-sec",
        type=float,
        default=0.12,
        help="Sleep between successful API requests.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Retries per failed API request.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducible sampling.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No external API calls. Generates synthetic timings to validate pipeline.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    result = build_dataset(args)

    print("Traffic profile dataset generated")
    print(f"- samples:    {result['samples_path']}")
    print(f"- aggregated: {result['aggregated_path']}")
    print(f"- profile:    {result['profile_path']}")
    if result.get("failed_path"):
        print(f"- failed:     {result['failed_path']}")
    meta = result.get("meta", {})
    print(
        "- queries:    "
        f"{meta.get('queries_success', 0)}/{meta.get('queries_requested', 0)}"
        f" (failed={meta.get('queries_failed', 0)})"
    )
    print(f"- provider:   {meta.get('provider', '')}")
    print(f"- dry_run:    {meta.get('dry_run', False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
