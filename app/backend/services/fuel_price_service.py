"""
Servicio de precios de combustible en CLP/L para Chile.

Soporta:
- diesel
- gasoline_93
- gasoline_95
- gasoline_97

Estrategia:
1) API CNE (v4 estaciones) con token/login opcional.
2) Fallback oficial Bencina en Linea (CNE) por reporte zonal.
3) Fallback público con rango estricto de CLP para evitar lecturas irreales.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import time
import unicodedata
import urllib.parse
import urllib.request
from typing import Any


SUPPORTED_FUEL_TYPES = ("diesel", "gasoline_93", "gasoline_95", "gasoline_97")

_FUEL_DEF = {
    "diesel": {
        "label": "Diesel",
        "aliases": (
            "diesel",
            "diésel",
            "petroleo diesel",
            "petroleo diésel",
            "petróleo diesel",
            "petrodiesel",
        ),
        "range_min": 850.0,
        "range_max": 3000.0,
        "fallback_url": "https://www.globalpetrolprices.com/Chile/diesel_prices/",
    },
    "gasoline_93": {
        "label": "Gasolina 93",
        "aliases": (
            "gasolina 93",
            "bencina 93",
            "gasoline 93",
            "93 octanos",
        ),
        "range_min": 850.0,
        "range_max": 3200.0,
        "fallback_url": "https://www.globalpetrolprices.com/Chile/gasoline_prices/",
    },
    "gasoline_95": {
        "label": "Gasolina 95",
        "aliases": (
            "gasolina 95",
            "bencina 95",
            "gasoline 95",
            "95 octanos",
        ),
        "range_min": 850.0,
        "range_max": 3200.0,
        "fallback_url": "https://www.globalpetrolprices.com/Chile/gasoline_prices/",
    },
    "gasoline_97": {
        "label": "Gasolina 97",
        "aliases": (
            "gasolina 97",
            "bencina 97",
            "gasoline 97",
            "97 octanos",
        ),
        "range_min": 850.0,
        "range_max": 3200.0,
        "fallback_url": "https://www.globalpetrolprices.com/Chile/gasoline_prices/",
    },
}

_FUEL_ALIASES = {
    "diesel": "diesel",
    "diésel": "diesel",
    "petroleo diesel": "diesel",
    "petrodiesel": "diesel",
    "gasolina_93": "gasoline_93",
    "gasolina93": "gasoline_93",
    "gasolina 93": "gasoline_93",
    "bencina_93": "gasoline_93",
    "bencina93": "gasoline_93",
    "bencina 93": "gasoline_93",
    "gasoline_93": "gasoline_93",
    "gasoline93": "gasoline_93",
    "gasoline 93": "gasoline_93",
    "93": "gasoline_93",
    "gasolina_95": "gasoline_95",
    "gasolina95": "gasoline_95",
    "gasolina 95": "gasoline_95",
    "bencina_95": "gasoline_95",
    "bencina95": "gasoline_95",
    "bencina 95": "gasoline_95",
    "gasoline_95": "gasoline_95",
    "gasoline95": "gasoline_95",
    "gasoline 95": "gasoline_95",
    "95": "gasoline_95",
    "gasolina_97": "gasoline_97",
    "gasolina97": "gasoline_97",
    "gasolina 97": "gasoline_97",
    "bencina_97": "gasoline_97",
    "bencina97": "gasoline_97",
    "bencina 97": "gasoline_97",
    "gasoline_97": "gasoline_97",
    "gasoline97": "gasoline_97",
    "gasoline 97": "gasoline_97",
    "97": "gasoline_97",
}

_CACHE_TTL_SEC = int(os.getenv("FUEL_PRICE_CACHE_TTL_SEC", "3600"))
_CACHE: dict[str, dict[str, Any]] = {}

_CNE_MAIN = "https://api.cne.cl"
_CNE_BETA = "https://beta.api.cne.cl"
_BENCINA_EN_LINEA_REPORT = "https://api.bencinaenlinea.cl/api/estaciones/precios_combustibles/reporte_zonal"

_BENCINA_FUEL_IDS: dict[str, set[int]] = {
    "diesel": {3, 11},       # DI, ADI
    "gasoline_93": {1, 8},   # 93, A93
    "gasoline_95": {7, 9},   # 95, A95
    "gasoline_97": {2, 10},  # 97, A97
}

_FUEL_KEYS = ("combustible", "tipo", "producto", "fuel", "nombre", "descripcion", "octanaje")
_PRICE_KEYS = ("precio", "valor", "price", "monto", "pventa", "venta")


def _normalize_text(value: str) -> str:
    """Normaliza texto a minúsculas sin acentos ni caracteres especiales para comparaciones robustas."""
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    # Elimina diacríticos (acentos, tildes) usando descomposición NFKD
    norm = unicodedata.normalize("NFKD", raw)
    norm = norm.encode("ascii", "ignore").decode("ascii")
    # Reemplaza todo carácter no alfanumérico por espacio
    norm = "".join(ch if ch.isalnum() else " " for ch in norm)
    return " ".join(norm.split())


def normalize_fuel_type(fuel_type: str | None) -> str:
    """
    Convierte cualquier variante del nombre de combustible a su clave canónica
    (ej. 'bencina 95' → 'gasoline_95'). Retorna 'diesel' si no hay coincidencia.
    """
    text = _normalize_text(str(fuel_type or "diesel"))
    if not text:
        return "diesel"
    # Búsqueda directa en el diccionario de alias planos
    if text in _FUEL_ALIASES:
        return _FUEL_ALIASES[text]
    # Búsqueda por alias definidos en _FUEL_DEF (normalización de texto)
    for canonical, cfg in _FUEL_DEF.items():
        aliases = cfg.get("aliases", ())
        for alias in aliases:
            if text == _normalize_text(str(alias)):
                return str(canonical)
    return "diesel"


def _matches_fuel(text: str, fuel_type: str) -> bool:
    """
    Determina si un texto (nombre de producto de una API) corresponde al tipo de combustible dado.
    Incluye reglas especiales para diesel y detección por número de octanos.
    """
    t = _normalize_text(text)
    if not t:
        return False
    aliases = _FUEL_DEF.get(fuel_type, {}).get("aliases", ())
    alias_norm = [_normalize_text(str(a)) for a in aliases]
    # Reglas específicas para diesel (incluye variantes con tilde y compuestos)
    if fuel_type == "diesel":
        if "diesel" in t or "petrodiesel" in t:
            return True
        if "petroleo" in t and "diesel" in t:
            return True
    for a in alias_norm:
        if not a:
            continue
        if a in t:
            return True
    # Para gasolinas: detectar por número de octanos junto con la palabra 'gasolina'/'bencina'
    if fuel_type.startswith("gasoline_"):
        octane = fuel_type.split("_")[-1]
        if octane in t and ("gasolina" in t or "bencina" in t):
            return True
    return False


def _as_float(value: Any) -> float | None:
    """
    Convierte un valor a float tolerando formatos numéricos con comas y puntos
    como separadores (notación europea y americana). Retorna None si no es parseable.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None

    s = value.strip()
    if not s:
        return None
    # Elimina caracteres no numéricos excepto coma, punto y guión
    s = re.sub(r"[^\d,.\-]", "", s)
    if not s:
        return None

    # Ambos separadores presentes: determinar cuál es el decimal según posición del último
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "")
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        if s.count(",") > 1:
            s = s.replace(",", "")
        else:
            head, tail = s.split(",", 1)
            if len(tail) <= 2:
                s = f"{head}.{tail}"
            elif tail == "000" and head.isdigit() and len(head) >= 3:
                s = f"{head}.0"
            elif len(head) <= 2:
                # "1,639" -> 1639
                s = f"{head}{tail}"
            else:
                s = f"{head}{tail}"
    elif "." in s:
        if s.count(".") > 1:
            s = s.replace(".", "")
        else:
            head, tail = s.split(".", 1)
            if len(tail) <= 2:
                s = f"{head}.{tail}"
            elif tail == "000" and head.isdigit() and len(head) >= 3:
                s = f"{head}.0"
            elif len(head) <= 2:
                # "1.639" -> 1639
                s = f"{head}{tail}"

    try:
        return float(s)
    except Exception:
        return None


def _price_in_range(value: float, fuel_type: str) -> bool:
    """Verifica que un precio en CLP/L esté dentro del rango plausible para el tipo de combustible."""
    cfg = _FUEL_DEF.get(fuel_type, _FUEL_DEF["diesel"])
    lo = float(cfg.get("range_min", 850.0))
    hi = float(cfg.get("range_max", 3200.0))
    return lo <= float(value) <= hi


def _robust_center(values: list[float]) -> float:
    """
    Calcula la mediana robusta de una lista de precios. Para muestras grandes (>=8),
    aplica una poda del 10% en cada extremo (media truncada) antes de calcular la mediana,
    eliminando valores atípicos como precios de estaciones con datos erróneos.
    """
    vals = sorted(float(v) for v in values)
    if not vals:
        raise ValueError("No values")
    if len(vals) < 8:
        return float(statistics.median(vals))
    # Poda simétrica del 10% por extremo para descartar outliers
    trim = max(1, int(len(vals) * 0.1))
    trimmed = vals[trim:-trim] if len(vals) > 2 * trim else vals
    return float(statistics.median(trimmed))


def _http_json(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout_sec: float = 10.0,
) -> Any:
    """Realiza una petición HTTP y retorna la respuesta parseada como JSON."""
    req_headers = {"User-Agent": "CapstoneAnalytics/1.0 (fuel-price)"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(
        url,
        data=data,
        headers=req_headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        text = resp.read().decode("utf-8")
    return json.loads(text)


def _http_text(url: str, timeout_sec: float = 10.0) -> str:
    """Realiza una petición HTTP GET y retorna el cuerpo de la respuesta como texto plano."""
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "CapstoneAnalytics/1.0 (fuel-price)"},
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _extract_fuel_prices(payload: Any, fuel_type: str) -> list[float]:
    """
    Recorre recursivamente un JSON de respuesta de API y extrae todos los precios en CLP/L
    que corresponden al tipo de combustible solicitado. Soporta distintas estructuras de respuesta.
    """
    prices: list[float] = []

    def add_if_valid(v: Any):
        """Agrega un precio a la lista si es numérico y está en el rango válido para el combustible."""
        num = _as_float(v)
        if num is None:
            return
        if _price_in_range(float(num), fuel_type):
            prices.append(float(num))

    def scan_price_fields(obj: dict):
        """Busca campos de precio en un dict usando las claves conocidas de precio (_PRICE_KEYS)."""
        for k, v in obj.items():
            lk = _normalize_text(str(k))
            if any(pk in lk for pk in _PRICE_KEYS):
                add_if_valid(v)

    def walk(node: Any):
        if isinstance(node, dict):
            # Caso 1: llave contiene nombre de combustible
            for k, v in node.items():
                if _matches_fuel(str(k), fuel_type):
                    if isinstance(v, dict):
                        scan_price_fields(v)
                    elif isinstance(v, list):
                        for item in v:
                            if isinstance(item, dict):
                                scan_price_fields(item)
                            else:
                                add_if_valid(item)
                    else:
                        add_if_valid(v)

            # Caso 2: objeto trae combustible + precio en el mismo nivel
            fuel_tokens = []
            for k, v in node.items():
                lk = _normalize_text(str(k))
                if any(fk in lk for fk in _FUEL_KEYS) and isinstance(v, str):
                    fuel_tokens.append(str(v))
                if lk in ("tipo", "name", "nombre", "producto") and isinstance(v, str):
                    fuel_tokens.append(str(v))
            fuel_text = " ".join(fuel_tokens)
            if _matches_fuel(fuel_text, fuel_type):
                scan_price_fields(node)

            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return sorted(set(round(float(v), 4) for v in prices))


def _try_cne_login(base_url: str, email: str, password: str, timeout_sec: float) -> str | None:
    """
    Intenta obtener un token de autenticación de la API de la CNE usando email y contraseña.
    Prueba múltiples variantes del endpoint de login y retorna el token si lo obtiene.
    """
    login_candidates: list[tuple[str, str, bytes, dict[str, str]]] = [
        (
            f"{base_url.rstrip('/')}/api/login",
            "POST",
            json.dumps({"email": email, "password": password}).encode("utf-8"),
            {"Content-Type": "application/json"},
        ),
        (
            f"{base_url.rstrip('/')}/api/?{urllib.parse.urlencode({'email': email, 'password': password})}",
            "POST",
            b"",
            {},
        ),
    ]

    for url, method, body, headers in login_candidates:
        try:
            payload = _http_json(
                url,
                method=method,
                data=body,
                headers=headers,
                timeout_sec=timeout_sec,
            )
            if not isinstance(payload, dict):
                continue
            token = (
                payload.get("token")
                or payload.get("access_token")
                or (payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}).get("token")
            )
            if isinstance(token, str) and token.strip():
                return token.strip()
        except Exception:
            continue
    return None


def _fetch_from_cne(fuel_type: str, timeout_sec: float = 10.0) -> tuple[float, dict] | None:
    """
    Consulta la API oficial de la CNE (v4/estaciones) para obtener el precio de combustible.
    Admite token de API directo o credenciales para login automático. Prueba endpoints
    principal y beta. Retorna (precio_clp, metadatos) o None si no hay datos.
    """
    token = str(os.getenv("CNE_API_TOKEN", "") or "").strip()
    email = str(os.getenv("CNE_API_EMAIL", "") or "").strip()
    password = str(os.getenv("CNE_API_PASSWORD", "") or "").strip()

    bases = [_CNE_MAIN, _CNE_BETA]
    # Si no hay token directo pero sí credenciales, intentar login para obtenerlo
    if not token and email and password:
        for base in bases:
            token = _try_cne_login(base, email, password, timeout_sec) or token
            if token:
                break

    endpoints = [f"{b}/api/v4/estaciones" for b in bases]
    last_err = None
    for url in endpoints:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            payload = _http_json(
                url,
                method="GET",
                headers=headers,
                timeout_sec=timeout_sec,
            )
            prices = _extract_fuel_prices(payload, fuel_type=fuel_type)
            if not prices:
                continue
            value = _robust_center(prices)
            return value, {
                "source": "cne_api_v4",
                "endpoint": url,
                "fuel_type": fuel_type,
                "sample_size": len(prices),
                "range_clp": {
                    "min": round(float(min(prices)), 4),
                    "max": round(float(max(prices)), 4),
                },
            }
        except Exception as e:
            last_err = str(e)
            continue

    if last_err:
        raise RuntimeError(f"CNE API unavailable: {last_err}")
    return None


def _parse_group_ids(raw: Any) -> set[int]:
    """
    Extrae un conjunto de IDs enteros desde un campo de respuesta que puede ser un número,
    string o lista. Usado para identificar el tipo de combustible en la API Bencina en Línea.
    """
    ids: set[int] = set()
    if raw is None:
        return ids
    if isinstance(raw, (int, float)):
        try:
            ids.add(int(raw))
        except Exception:
            return ids
        return ids
    text = str(raw).strip()
    if not text:
        return ids
    # Extrae todos los números enteros presentes en el string
    for token in re.findall(r"\d+", text):
        try:
            ids.add(int(token))
        except Exception:
            continue
    return ids


def _fetch_from_bencina_en_linea(
    fuel_type: str,
    timeout_sec: float = 10.0,
) -> tuple[float, dict] | None:
    """
    Consulta el reporte zonal de Bencina en Línea (CNE) para obtener el precio promedio
    del combustible por zona. Filtra por IDs de tipo de combustible y unidad de cobro por litro.
    Retorna (precio_clp, metadatos) o None si no hay datos válidos.
    """
    target_ids = set(_BENCINA_FUEL_IDS.get(fuel_type, set()))
    if not target_ids:
        return None

    payload = _http_json(
        _BENCINA_EN_LINEA_REPORT,
        method="GET",
        timeout_sec=timeout_sec,
    )
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        return None

    prices: list[float] = []
    matched_rows = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        unit = _normalize_text(str(row.get("unidad_cobro_nombre_corto", "")))
        if "l" not in unit:
            # Evitar m3 (GLP/GNC) u otras unidades que no son litros
            continue

        row_ids = _parse_group_ids(row.get("grupo_tipo_combustible"))
        cid = _as_float(row.get("combustible_id"))
        if cid is not None:
            try:
                row_ids.add(int(cid))
            except Exception:
                pass
        if not (row_ids & target_ids):
            continue

        value = _as_float(row.get("precio_promedio"))
        if value is None:
            continue
        if _price_in_range(float(value), fuel_type):
            prices.append(float(value))
            matched_rows += 1

    if not prices:
        return None

    value = _robust_center(prices)
    return value, {
        "source": "bencinaenlinea_reporte_zonal",
        "endpoint": _BENCINA_EN_LINEA_REPORT,
        "fuel_type": fuel_type,
        "target_ids": sorted(target_ids),
        "sample_size": len(prices),
        "matched_rows": int(matched_rows),
        "range_clp": {
            "min": round(float(min(prices)), 4),
            "max": round(float(max(prices)), 4),
        },
    }


def _fetch_from_global_fallback(fuel_type: str, timeout_sec: float = 10.0) -> tuple[float, dict] | None:
    """
    Último recurso: extrae el precio de combustible desde GlobalPetrolPrices.com mediante
    scraping HTML con expresiones regulares que buscan montos en CLP junto al texto 'Chile'.
    Retorna (precio_clp, metadatos) o None si no se encuentran valores en rango.
    """
    cfg = _FUEL_DEF.get(fuel_type, _FUEL_DEF["diesel"])
    url = str(cfg.get("fallback_url"))
    html = _http_text(url, timeout_sec=timeout_sec)
    candidates: list[float] = []

    # Patrones regex para detectar precios en CLP dentro del HTML
    patterns = [
        r"CLP[^0-9]{0,24}([0-9][0-9.,]{2,10})",
        r"([0-9][0-9.,]{2,10})[^0-9]{0,24}CLP",
        r"Chile[^0-9]{0,40}([0-9][0-9.,]{2,10})[^0-9]{0,12}CLP",
    ]
    for pat in patterns:
        for match in re.finditer(pat, html, flags=re.IGNORECASE):
            num = _as_float(match.group(1))
            if num is None:
                continue
            if _price_in_range(float(num), fuel_type):
                candidates.append(float(num))

    if not candidates:
        return None

    value = _robust_center(candidates)
    return value, {
        "source": "globalpetrolprices_fallback",
        "endpoint": url,
        "fuel_type": fuel_type,
        "sample_size": len(candidates),
        "range_clp": {
            "min": round(float(min(candidates)), 4),
            "max": round(float(max(candidates)), 4),
        },
    }


def fetch_fuel_price_clp(
    fuel_type: str = "diesel",
    *,
    force_refresh: bool = False,
    timeout_sec: float = 10.0,
) -> tuple[float, dict]:
    """
    Obtiene el precio de combustible en CLP/litro para Chile con estrategia de caché y fallback.

    Orden de fuentes:
      1. CNE API v4 (principal)
      2. Bencina en Línea - reporte zonal (secundaria)
      3. GlobalPetrolPrices.com scraping (último recurso)

    Si el caché aún es válido (TTL configurado en FUEL_PRICE_CACHE_TTL_SEC), se retorna sin
    hacer peticiones externas. Lanza RuntimeError si ninguna fuente entrega datos.
    """
    selected = normalize_fuel_type(fuel_type)
    now = time.time()
    entry = _CACHE.get(selected) or {}
    # Retornar desde caché si no se fuerza actualización y el precio aún es fresco
    if (
        not force_refresh
        and entry.get("price_clp") is not None
        and (now - float(entry.get("ts", 0.0))) < float(_CACHE_TTL_SEC)
    ):
        meta = dict(entry.get("meta") or {})
        meta["cache"] = "hit"
        meta["fuel_type"] = selected
        return float(entry["price_clp"]), meta

    errors: list[str] = []

    # Intento 1: API oficial CNE
    try:
        out = _fetch_from_cne(selected, timeout_sec=timeout_sec)
        if out is not None:
            price, meta = out
            meta = dict(meta or {})
            meta["updated_at"] = int(now)
            meta["cache"] = "miss"
            _CACHE[selected] = {
                "ts": float(now),
                "price_clp": float(price),
                "meta": dict(meta),
            }
            return float(price), meta
    except Exception as e:
        errors.append(str(e))

    # Intento 2: Bencina en Línea (reporte zonal CNE)
    try:
        out = _fetch_from_bencina_en_linea(selected, timeout_sec=timeout_sec)
        if out is not None:
            price, meta = out
            meta = dict(meta or {})
            meta["updated_at"] = int(now)
            meta["cache"] = "miss"
            _CACHE[selected] = {
                "ts": float(now),
                "price_clp": float(price),
                "meta": dict(meta),
            }
            return float(price), meta
    except Exception as e:
        errors.append(str(e))

    # Intento 3: Scraping de GlobalPetrolPrices.com
    try:
        out = _fetch_from_global_fallback(selected, timeout_sec=timeout_sec)
        if out is not None:
            price, meta = out
            meta = dict(meta or {})
            meta["updated_at"] = int(now)
            meta["cache"] = "miss"
            _CACHE[selected] = {
                "ts": float(now),
                "price_clp": float(price),
                "meta": dict(meta),
            }
            return float(price), meta
    except Exception as e:
        errors.append(str(e))

    msg = "; ".join(errors) if errors else "No fuel price source returned data."
    raise RuntimeError(msg)


def fetch_diesel_price_clp(
    *,
    force_refresh: bool = False,
    timeout_sec: float = 10.0,
) -> tuple[float, dict]:
    """Atajo que llama a fetch_fuel_price_clp fijando el tipo de combustible en 'diesel'."""
    return fetch_fuel_price_clp(
        "diesel",
        force_refresh=bool(force_refresh),
        timeout_sec=float(timeout_sec),
    )
