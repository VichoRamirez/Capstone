"""
Módulo de enrutamiento vial para el optimizador VRP/VRPTW.

Provee:
- Matrices de distancia/tiempo reales usando la API Table de OSRM.
- Fallback automático a distancia Haversine si OSRM no está disponible.
- Velocidad promedio observada entre nodos.
- Selección de k vecinos más cercanos mediante Dijkstra.
- Cálculo de distancia al depósito mediante A*.
- Auto-inicio del servidor OSRM local si está configurado y no responde.
"""

from __future__ import annotations

import heapq
import json
import math
import os
import shutil
import ssl
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

Coord = Tuple[float, float]  # (lon, lat)
Arc = Tuple[int, int]
OSRM_USER_AGENT = "Capstone-VRP-Lab/1.0 (+https://github.com/VichoRamirez/Capstone)"
DEFAULT_PUBLIC_OSRM_BASE = "http://router.project-osrm.org"
DEFAULT_LOCAL_OSRM_BASE = "http://127.0.0.1:5010"
DEFAULT_LOCAL_OSRM_PORT_CANDIDATES = (5010, 5002, 5003, 5004, 5005)
DEFAULT_LOCAL_OSRM_RESERVED_PORTS = (5000, 5001)
DEFAULT_ALT_OSRM_BASES = (
    "https://routing.openstreetmap.de/routed-car",
    "http://routing.openstreetmap.de/routed-car",
)
LOCAL_OSRM_PROBE_COORD = "-70.669300,-33.448900"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOCAL_OSRM_DATA_DIR = PROJECT_ROOT / "app" / "models" / "Soporte" / "osrm_local"

_LOCAL_OSRM_LOCK = threading.Lock()
_LOCAL_OSRM_START_ATTEMPTED = False
_LOCAL_OSRM_READY_BASE = ""
_LOCAL_OSRM_LAST_ERROR = ""
_LOCAL_OSRM_DATASET_USED = ""


def _env_truthy(name: str, default: bool) -> bool:
    """Lee una variable de entorno y la interpreta como booleano (0/false/no/off → False)."""
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _normalize_osrm_base(base: str) -> str:
    """Elimina barras finales y agrega esquema 'http://' si la URL base no lo tiene."""
    out = (base or "").strip().rstrip("/")
    if not out:
        return ""
    if "://" not in out:
        out = "http://" + out
    return out


def _configured_local_osrm_base() -> str:
    """Retorna la URL base del servidor OSRM local según la variable de entorno OSRM_LOCAL_BASE_URL."""
    return _normalize_osrm_base(os.getenv("OSRM_LOCAL_BASE_URL", DEFAULT_LOCAL_OSRM_BASE))


def _reserved_local_osrm_ports() -> set[int]:
    """Retorna el conjunto de puertos locales reservados que no deben usarse para OSRM."""
    raw = os.getenv("OSRM_RESERVED_PORTS")
    if raw is None:
        return set(DEFAULT_LOCAL_OSRM_RESERVED_PORTS)
    out: set[int] = set()
    for tok in str(raw).split(","):
        tt = tok.strip()
        if not tt:
            continue
        try:
            port = int(tt)
        except Exception:
            continue
        if 1 <= port <= 65535:
            out.add(int(port))
    return out if out else set(DEFAULT_LOCAL_OSRM_RESERVED_PORTS)


def _is_localhost_base(base: str) -> bool:
    """Verifica si una URL base apunta a localhost (127.0.0.1 o 'localhost')."""
    try:
        host = (urllib.parse.urlparse(_normalize_osrm_base(base)).hostname or "").strip().lower()
    except Exception:
        return False
    return host in {"127.0.0.1", "localhost"}


def _base_port(base: str) -> int:
    """Extrae el número de puerto de una URL base OSRM. Retorna 0 si no hay puerto explícito."""
    try:
        parsed = urllib.parse.urlparse(_normalize_osrm_base(base))
        if parsed.port:
            return int(parsed.port)
    except Exception:
        pass
    return 0


def _is_reserved_local_base(base: str) -> bool:
    """Determina si una URL base local usa un puerto reservado (que no debe usarse para OSRM)."""
    if not _is_localhost_base(base):
        return False
    return _base_port(base) in _reserved_local_osrm_ports()


def _local_osrm_base_candidates() -> List[str]:
    """
    Genera la lista de URLs base OSRM locales a probar, comenzando por la ya confirmada
    como activa (_LOCAL_OSRM_READY_BASE), seguida de los puertos candidatos configurados.
    Excluye puertos reservados y elimina duplicados.
    """
    configured = _configured_local_osrm_base()
    parsed = urllib.parse.urlparse(configured)
    scheme = parsed.scheme or "http"
    host = parsed.hostname or "127.0.0.1"
    base_port = int(parsed.port or 5010)

    ports = [base_port] + list(DEFAULT_LOCAL_OSRM_PORT_CANDIDATES)
    reserved_ports = _reserved_local_osrm_ports()
    bases: List[str] = []
    if _LOCAL_OSRM_READY_BASE and not _is_reserved_local_base(_LOCAL_OSRM_READY_BASE):
        bases.append(_LOCAL_OSRM_READY_BASE)
    for port in ports:
        if int(port) in reserved_ports:
            continue
        bases.append(f"{scheme}://{host}:{int(port)}")

    dedup: List[str] = []
    seen = set()
    for b in bases:
        nb = _normalize_osrm_base(b)
        if nb and nb not in seen:
            dedup.append(nb)
            seen.add(nb)
    return dedup


def _local_osrm_base() -> str:
    """Retorna la URL base del OSRM local: la confirmada como activa o la configurada por defecto."""
    if _LOCAL_OSRM_READY_BASE:
        return _LOCAL_OSRM_READY_BASE
    return _configured_local_osrm_base()


def _candidate_osrm_bases(osrm_base_url: str) -> List[str]:
    """
    Construye la lista priorizada de endpoints OSRM a intentar. Si OSRM_PREFER_LOCAL=true
    (por defecto), los servidores locales se colocan primero. Incluye variantes http/https
    para cada endpoint remoto y elimina duplicados.
    """
    base = _normalize_osrm_base(osrm_base_url)
    if not base:
        base = DEFAULT_PUBLIC_OSRM_BASE

    configured = [base]
    if base != DEFAULT_PUBLIC_OSRM_BASE:
        configured.append(DEFAULT_PUBLIC_OSRM_BASE)
    configured.extend(DEFAULT_ALT_OSRM_BASES)

    local_bases = _local_osrm_base_candidates()
    prefer_local = _env_truthy("OSRM_PREFER_LOCAL", True)
    # Poner servidores locales al principio si se prefiere el enrutamiento local
    if prefer_local:
        configured = local_bases + configured
    else:
        configured.extend(local_bases)

    candidates: List[str] = []
    for item in configured:
        normalized = _normalize_osrm_base(item)
        if not normalized:
            continue
        if _is_reserved_local_base(normalized):
            continue
        candidates.append(normalized)
        try:
            host = (urllib.parse.urlparse(normalized).hostname or "").lower()
        except Exception:
            host = ""
        if host in {"127.0.0.1", "localhost"}:
            continue
        # Para endpoints custom mantenemos fallback http/https.
        if normalized.startswith("https://"):
            candidates.append("http://" + normalized[len("https://") :])
        elif normalized.startswith("http://"):
            candidates.append("https://" + normalized[len("http://") :])

    dedup: List[str] = []
    seen = set()
    for c in candidates:
        if c not in seen:
            dedup.append(c)
            seen.add(c)
    return dedup


def haversine_km(coord_a: Coord, coord_b: Coord) -> float:
    """Great-circle distance in km between two (lon, lat) points."""
    lon1, lat1 = coord_a
    lon2, lat2 = coord_b
    radius_km = 6371.0

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(1e-15, 1.0 - a)))
    return radius_km * c


def _curl_fetch_json(url: str, timeout_sec: float) -> Dict[str, Any]:
    """
    Ejecuta curl como subproceso para obtener JSON desde una URL OSRM.
    Se usa como alternativa cuando urllib no puede resolver conexiones locales en Windows.
    """
    curl_path = shutil.which("curl")
    if not curl_path:
        raise RuntimeError("curl_not_found")

    max_time = max(1.0, float(timeout_sec))
    connect_timeout = max(1.0, min(max_time, 3.0))
    cmd = [
        curl_path,
        "-sS",
        "--max-time",
        f"{max_time:.1f}",
        "--connect-timeout",
        f"{connect_timeout:.1f}",
        "-H",
        f"User-Agent: {OSRM_USER_AGENT}",
        "-H",
        "Accept: application/json",
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or f"curl_exit_{proc.returncode}").strip()
        raise RuntimeError(detail)

    text = (proc.stdout or "").strip()
    if not text:
        raise RuntimeError("empty_response")
    try:
        payload = json.loads(text)
    except Exception as exc:
        snippet = text[:180].replace("\n", " ")
        raise RuntimeError(f"invalid_json_response: {exc} | snippet={snippet}")
    if not isinstance(payload, dict):
        raise RuntimeError("json_payload_not_object")
    return payload


def _fetch_json(url: str, timeout_sec: float) -> Dict[str, Any]:
    """
    Obtiene JSON desde una URL intentando primero con curl y luego con urllib.
    Combina los errores de ambos métodos en el mensaje de excepción si ambos fallan.
    """
    curl_error = ""
    try:
        return _curl_fetch_json(url, timeout_sec=timeout_sec)
    except Exception as exc:
        curl_error = str(exc)

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": OSRM_USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        if url.startswith("https://"):
            ctx = ssl.create_default_context()
            try:
                ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            except Exception:
                pass
            with urllib.request.urlopen(req, timeout=timeout_sec, context=ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        if curl_error:
            raise RuntimeError(f"curl={curl_error} | urllib={exc}")
        raise


def _local_osrm_probe_url(base: str) -> str:
    """Construye la URL de sondeo OSRM (nearest) para verificar conectividad del servidor."""
    normalized = _normalize_osrm_base(base)
    return f"{normalized}/nearest/v1/driving/{LOCAL_OSRM_PROBE_COORD}?number=1"


def _is_osrm_base_reachable(base: str, timeout_sec: float) -> bool:
    """
    Verifica si un endpoint OSRM responde correctamente enviando una petición de prueba
    al punto de referencia en Santiago. Retorna True solo si la respuesta es 'Ok'.
    """
    normalized = _normalize_osrm_base(base)
    if not normalized:
        return False
    try:
        payload = _fetch_json(_local_osrm_probe_url(normalized), timeout_sec=max(1.0, float(timeout_sec)))
    except Exception:
        return False
    return payload.get("code") == "Ok"


def _prepared_osrm_dataset(path_or_stem: str) -> str:
    """
    Verifica si un dataset OSRM está preprocesado (al menos 2 archivos auxiliares presentes).
    Retorna el path stem si está listo, o '' si faltan archivos de preprocesamiento.
    """
    stem = (path_or_stem or "").strip()
    if not stem:
        return ""
    if stem.endswith(".osm.pbf"):
        stem = stem[: -len(".osm.pbf")] + ".osrm"
    # Archivos generados por osrm-partition y osrm-customize (algoritmo MLD)
    suffixes = [".partition", ".cells", ".ebg", ".mldgr"]
    available = 0
    for sx in suffixes:
        if Path(stem + sx).exists():
            available += 1
    return stem if available >= 2 else ""


def _local_osrm_dataset_candidates() -> List[str]:
    """
    Retorna la lista de datasets OSRM locales preprocesados disponibles. Prioriza el
    configurado en OSRM_LOCAL_DATASET, luego busca nombres predeterminados del mapa de Chile.
    """
    candidates: List[str] = []
    env_dataset = os.getenv("OSRM_LOCAL_DATASET", "").strip()
    if env_dataset:
        candidates.append(env_dataset)
    # Nombres predeterminados del dataset de Chile en el directorio de soporte
    candidates.extend(
        [
            str(LOCAL_OSRM_DATA_DIR / "chile-latest.osrm"),
            str(LOCAL_OSRM_DATA_DIR / "chile-260329.osrm"),
            str(LOCAL_OSRM_DATA_DIR / "chile.osrm"),
        ]
    )
    seen = set()
    out: List[str] = []
    for raw in candidates:
        prepared = _prepared_osrm_dataset(raw)
        if prepared and prepared not in seen:
            out.append(prepared)
            seen.add(prepared)
    return out


def _find_osrm_routed_binary() -> str:
    """
    Localiza el ejecutable osrm-routed en el sistema. Busca primero en OSRM_ROUTED_BIN,
    luego en el PATH del sistema y finalmente en rutas comunes de Homebrew/Linux.
    Retorna el path absoluto o '' si no se encuentra.
    """
    explicit = os.getenv("OSRM_ROUTED_BIN", "").strip()
    candidates = [explicit] if explicit else []
    auto = shutil.which("osrm-routed")
    if auto:
        candidates.append(auto)
    # Rutas comunes en macOS (Homebrew) y Linux
    candidates.extend(["/opt/homebrew/bin/osrm-routed", "/usr/local/bin/osrm-routed"])
    for path in candidates:
        if not path:
            continue
        pp = Path(path)
        if pp.exists() and os.access(str(pp), os.X_OK):
            return str(pp)
    return ""


def _tail_text(path: Path, max_chars: int = 360) -> str:
    """Lee los últimos max_chars caracteres de un archivo de log para diagnóstico de errores."""
    try:
        txt = path.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return ""
    if not txt:
        return ""
    if len(txt) <= max_chars:
        return txt.replace("\n", " ")
    return txt[-max_chars:].replace("\n", " ")


def _local_osrm_startup_timeout_sec() -> float:
    """Retorna el tiempo máximo de espera (segundos) para que OSRM local inicie. Por defecto 24 s."""
    raw = os.getenv("OSRM_LOCAL_STARTUP_TIMEOUT_SEC", "").strip()
    if raw:
        try:
            return max(3.0, min(float(raw), 120.0))
        except Exception:
            pass
    return 24.0


def _local_osrm_port(base: str) -> int:
    """Extrae el puerto de una URL base local. Retorna 5000 si no hay puerto explícito."""
    try:
        parsed = urllib.parse.urlparse(_normalize_osrm_base(base))
        if parsed.port:
            return int(parsed.port)
    except Exception:
        pass
    return 5000


def _maybe_autostart_local_osrm(timeout_sec: float = 2.0) -> Tuple[str, str]:
    """
    Intenta iniciar automáticamente el servidor OSRM local si no está corriendo.
    Solo realiza un intento de inicio por proceso (controlado por _LOCAL_OSRM_START_ATTEMPTED).
    Retorna (base_url_activa, mensaje_de_error). El inicio es thread-safe via _LOCAL_OSRM_LOCK.
    """
    global _LOCAL_OSRM_START_ATTEMPTED
    global _LOCAL_OSRM_READY_BASE
    global _LOCAL_OSRM_LAST_ERROR
    global _LOCAL_OSRM_DATASET_USED

    probe_timeout = max(1.0, min(float(timeout_sec), 3.0))
    local_bases = _local_osrm_base_candidates()
    for local_base in local_bases:
        if _is_osrm_base_reachable(local_base, timeout_sec=probe_timeout):
            _LOCAL_OSRM_READY_BASE = local_base
            _LOCAL_OSRM_LAST_ERROR = ""
            return local_base, ""

    if _LOCAL_OSRM_START_ATTEMPTED:
        return "", _LOCAL_OSRM_LAST_ERROR

    if not _env_truthy("OSRM_LOCAL_AUTOSTART", True):
        if not _LOCAL_OSRM_LAST_ERROR:
            _LOCAL_OSRM_LAST_ERROR = "local_osrm_unreachable_and_autostart_disabled"
        return "", _LOCAL_OSRM_LAST_ERROR

    with _LOCAL_OSRM_LOCK:
        for local_base in _local_osrm_base_candidates():
            if _is_osrm_base_reachable(local_base, timeout_sec=probe_timeout):
                _LOCAL_OSRM_READY_BASE = local_base
                _LOCAL_OSRM_LAST_ERROR = ""
                return local_base, ""

        _LOCAL_OSRM_START_ATTEMPTED = True
        routed_bin = _find_osrm_routed_binary()
        if not routed_bin:
            _LOCAL_OSRM_LAST_ERROR = "osrm_routed_not_found"
            return "", _LOCAL_OSRM_LAST_ERROR

        datasets = _local_osrm_dataset_candidates()
        if not datasets:
            _LOCAL_OSRM_LAST_ERROR = f"local_osrm_dataset_not_prepared_under_{LOCAL_OSRM_DATA_DIR}"
            return "", _LOCAL_OSRM_LAST_ERROR

        dataset = datasets[0]
        _LOCAL_OSRM_DATASET_USED = dataset
        algo = os.getenv("OSRM_LOCAL_ALGORITHM", "mld").strip() or "mld"

        log_path = LOCAL_OSRM_DATA_DIR / "osrm-routed.log"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        startup_timeout = _local_osrm_startup_timeout_sec()
        per_base_timeout = max(8.0, startup_timeout / max(1, len(local_bases)))
        attempt_errors: List[str] = []

        for local_base in local_bases:
            port = _local_osrm_port(local_base)
            cmd = [routed_bin, "--algorithm", algo, "--port", str(port), dataset]
            proc: Optional[subprocess.Popen[str]] = None
            try:
                with log_path.open("a", encoding="utf-8") as logf:
                    logf.write(
                        f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] autostart base={local_base} cmd={' '.join(cmd)}\n"
                    )
                    proc = subprocess.Popen(
                        cmd,
                        stdin=subprocess.DEVNULL,
                        stdout=logf,
                        stderr=subprocess.STDOUT,
                        text=True,
                        start_new_session=True,
                    )
            except Exception as exc:
                attempt_errors.append(f"{local_base}: launch_error={exc}")
                continue

            deadline = time.time() + per_base_timeout
            while time.time() < deadline:
                if _is_osrm_base_reachable(local_base, timeout_sec=1.2):
                    _LOCAL_OSRM_READY_BASE = local_base
                    _LOCAL_OSRM_LAST_ERROR = ""
                    return local_base, ""
                if proc is not None and proc.poll() is not None:
                    break
                time.sleep(0.45)

            if _is_osrm_base_reachable(local_base, timeout_sec=1.5):
                _LOCAL_OSRM_READY_BASE = local_base
                _LOCAL_OSRM_LAST_ERROR = ""
                return local_base, ""

            exit_code = proc.poll() if proc is not None else None
            tail = _tail_text(log_path, max_chars=220)
            status = f"exit={exit_code}" if exit_code is not None else "startup_timeout"
            attempt_errors.append(f"{local_base}: {status} tail={tail}")

        detail = " | ".join(attempt_errors[:3])
        if len(attempt_errors) > 3:
            detail += f" | ... +{len(attempt_errors) - 3} intentos"
        _LOCAL_OSRM_LAST_ERROR = f"local_osrm_autostart_failed dataset={dataset} {detail}".strip()
        return "", _LOCAL_OSRM_LAST_ERROR


def _local_osrm_meta() -> Dict[str, Any]:
    """Retorna un dict con el estado actual del servidor OSRM local para incluir en metadatos."""
    return {
        "configured_base": _configured_local_osrm_base(),
        "local_base": _local_osrm_base(),
        "ready_base": _LOCAL_OSRM_READY_BASE,
        "reserved_ports": sorted(_reserved_local_osrm_ports()),
        "start_attempted": _LOCAL_OSRM_START_ATTEMPTED,
        "dataset": _LOCAL_OSRM_DATASET_USED,
        "error": _LOCAL_OSRM_LAST_ERROR,
    }


def _looks_like_timeout_error(exc: Exception) -> bool:
    """Detecta si una excepción es un error de timeout para decidir si reintentar la petición."""
    msg = str(exc).lower()
    return "timed out" in msg or "timeout" in msg


def _fetch_osrm_table(
    coords: Dict[int, Coord],
    nodes: List[int],
    osrm_base_url: str,
    timeout_sec: float,
    source_positions: Optional[Sequence[int]] = None,
    destination_positions: Optional[Sequence[int]] = None,
) -> Tuple[List[List[Optional[float]]], List[List[Optional[float]]], str]:
    """
    Obtiene las matrices de distancia y duración desde OSRM probando todos los endpoints candidatos.
    Retorna (matriz_distancias_m, matriz_duraciones_s, endpoint_utilizado).
    """
    errors: List[str] = []
    for base in _candidate_osrm_bases(osrm_base_url):
        try:
            distances, durations = _fetch_osrm_table_single_base(
                coords=coords,
                nodes=nodes,
                base=base,
                timeout_sec=timeout_sec,
                source_positions=source_positions,
                destination_positions=destination_positions,
            )
            return distances, durations, base
        except Exception as exc:
            errors.append(f"{base}: {exc}")

    raise RuntimeError(" ; ".join(errors))


def _fetch_osrm_table_single_base(
    coords: Dict[int, Coord],
    nodes: List[int],
    base: str,
    timeout_sec: float,
    source_positions: Optional[Sequence[int]] = None,
    destination_positions: Optional[Sequence[int]] = None,
) -> Tuple[List[List[Optional[float]]], List[List[Optional[float]]]]:
    """
    Llama al endpoint /table/v1/driving de OSRM para un único servidor base.
    Retorna (matriz_distancias_m, matriz_duraciones_s). Lanza RuntimeError si la respuesta no es 'Ok'.
    """
    coord_tokens = [f"{coords[n][0]:.6f},{coords[n][1]:.6f}" for n in nodes]
    coord_str = ";".join(coord_tokens)
    params: Dict[str, str] = {"annotations": "distance,duration"}
    if source_positions is not None:
        params["sources"] = ";".join(str(int(i)) for i in source_positions)
    if destination_positions is not None:
        params["destinations"] = ";".join(str(int(i)) for i in destination_positions)
    query = urllib.parse.urlencode(params)
    url = f"{base}/table/v1/driving/{coord_str}?{query}"
    payload = _fetch_json(url, timeout_sec=timeout_sec)
    if payload.get("code") != "Ok":
        raise RuntimeError(f"OSRM table error: {payload.get('code')}")
    distances = payload.get("distances")
    durations = payload.get("durations")
    if not isinstance(distances, list) or not isinstance(durations, list):
        raise RuntimeError("OSRM response does not include distance/duration matrices.")
    return distances, durations


def _select_working_osrm_base(
    coords: Dict[int, Coord],
    nodes: List[int],
    osrm_base_url: str,
    timeout_sec: float,
) -> str:
    """
    Selecciona el primer endpoint OSRM candidato que responde correctamente a una petición
    de prueba con dos nodos. Intenta arrancar el servidor local si está configurado.
    Lanza RuntimeError si ningún candidato responde.
    """
    errors: List[str] = []
    _, local_boot_error = _maybe_autostart_local_osrm(timeout_sec=max(1.5, min(float(timeout_sec) * 1.5, 4.5)))
    candidates = _candidate_osrm_bases(osrm_base_url)
    if not candidates:
        raise RuntimeError("No hay endpoints OSRM candidatos.")

    if len(nodes) >= 2:
        probe_nodes = [nodes[0], nodes[1]]
    elif len(nodes) == 1:
        probe_nodes = [nodes[0]]
    else:
        probe_nodes = []

    for base in candidates:
        try:
            if len(probe_nodes) >= 2:
                probe_timeout = max(2.5, min(float(timeout_sec) * 1.5, 6.0))
                _fetch_osrm_table_single_base(
                    coords=coords,
                    nodes=probe_nodes,
                    base=base,
                    timeout_sec=probe_timeout,
                )
            return base
        except Exception as exc:
            errors.append(f"{base}: {exc}")
    if local_boot_error:
        errors.append(f"local_boot: {local_boot_error}")
    raise RuntimeError(" ; ".join(errors))


def _fetch_osrm_table_blockwise(
    coords: Dict[int, Coord],
    nodes: List[int],
    osrm_base_url: str,
    timeout_sec: float,
    max_osrm_points: int,
) -> Tuple[List[List[Optional[float]]], List[List[Optional[float]]], str, int, List[str]]:
    """Fetch full NxN OSRM matrices using block requests when N > max_osrm_points.

    Each request uses up to `max_osrm_points` coordinates by combining one source block
    and one destination block.
    """
    n = len(nodes)
    distances_full: List[List[Optional[float]]] = [[None for _ in range(n)] for _ in range(n)]
    durations_full: List[List[Optional[float]]] = [[None for _ in range(n)] for _ in range(n)]

    # Bloques algo mas chicos ayudan con endpoints publicos saturados.
    block_size = max(2, min(max_osrm_points // 2, 40))
    endpoint_used = ""
    req_count = 0
    errors: List[str] = []
    consecutive_failures = 0
    max_consecutive_failures = 2

    for i0 in range(0, n, block_size):
        src_nodes = nodes[i0 : i0 + block_size]
        for j0 in range(0, n, block_size):
            dst_nodes = nodes[j0 : j0 + block_size]

            src_set = set(src_nodes)
            query_nodes = list(src_nodes)
            for nn in dst_nodes:
                if nn not in src_set:
                    query_nodes.append(nn)

            local_idx = {node: pos for pos, node in enumerate(query_nodes)}
            src_positions = [local_idx[nn] for nn in src_nodes]
            dst_positions = [local_idx[nn] for nn in dst_nodes]

            req_count += 1
            try:
                retry_exception: Optional[Exception] = None
                block_d: List[List[Optional[float]]]
                block_t: List[List[Optional[float]]]
                for attempt in range(2):
                    effective_timeout = float(timeout_sec) if attempt == 0 else max(float(timeout_sec) * 2.0, 5.0)
                    try:
                        block_d, block_t = _fetch_osrm_table_single_base(
                            coords=coords,
                            nodes=query_nodes,
                            base=osrm_base_url,
                            timeout_sec=effective_timeout,
                            source_positions=src_positions,
                            destination_positions=dst_positions,
                        )
                        break
                    except Exception as exc:
                        retry_exception = exc
                        if attempt == 0 and _looks_like_timeout_error(exc):
                            continue
                        raise
                else:
                    raise retry_exception if retry_exception is not None else RuntimeError("OSRM block unknown failure")
                if not endpoint_used:
                    endpoint_used = osrm_base_url

                if len(block_d) != len(src_nodes) or len(block_t) != len(src_nodes):
                    raise RuntimeError("OSRM block dimensions mismatch on source axis.")
                for r_idx in range(len(src_nodes)):
                    if len(block_d[r_idx]) != len(dst_nodes) or len(block_t[r_idx]) != len(dst_nodes):
                        raise RuntimeError("OSRM block dimensions mismatch on destination axis.")
                    gi = i0 + r_idx
                    for c_idx in range(len(dst_nodes)):
                        gj = j0 + c_idx
                        distances_full[gi][gj] = block_d[r_idx][c_idx]
                        durations_full[gi][gj] = block_t[r_idx][c_idx]
                consecutive_failures = 0
            except Exception as exc:
                i1 = i0 + len(src_nodes) - 1
                j1 = j0 + len(dst_nodes) - 1
                errors.append(f"block[{i0}:{i1}->{j0}:{j1}] {osrm_base_url}: {exc}")
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    errors.append(
                        f"aborted_remaining_blocks_after_{max_consecutive_failures}_consecutive_failures"
                    )
                    return distances_full, durations_full, endpoint_used, req_count, errors

    return distances_full, durations_full, endpoint_used, req_count, errors


def _fetch_osrm_route_geometry_single(
    coords: Dict[int, Coord],
    route_nodes: Sequence[int],
    osrm_base_url: str,
    timeout_sec: float,
) -> Tuple[List[Coord], float, float, str]:
    """
    Obtiene la geometría GeoJSON de una ruta desde OSRM para una secuencia de nodos.
    Retorna (lista_de_coordenadas, distancia_km, duracion_min, endpoint_usado).
    """
    coord_tokens = [f"{coords[n][0]:.6f},{coords[n][1]:.6f}" for n in route_nodes]
    coord_str = ";".join(coord_tokens)
    query = urllib.parse.urlencode({"overview": "full", "geometries": "geojson", "steps": "false"})
    errors: List[str] = []

    for base in _candidate_osrm_bases(osrm_base_url):
        url = f"{base}/route/v1/driving/{coord_str}?{query}"
        try:
            payload = _fetch_json(url, timeout_sec=timeout_sec)

            if payload.get("code") != "Ok":
                raise RuntimeError(f"OSRM route error: {payload.get('code')}")

            routes = payload.get("routes")
            if not isinstance(routes, list) or not routes:
                raise RuntimeError("OSRM route response does not include routes.")

            route0 = routes[0]
            geometry = route0.get("geometry", {})
            coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
            if not isinstance(coordinates, list) or len(coordinates) < 2:
                raise RuntimeError("OSRM route response does not include valid geometry coordinates.")

            path: List[Coord] = []
            for xy in coordinates:
                if not isinstance(xy, (list, tuple)) or len(xy) < 2:
                    continue
                path.append((float(xy[0]), float(xy[1])))

            if len(path) < 2:
                raise RuntimeError("OSRM route geometry is too short.")

            distance_km = float(route0.get("distance", 0.0)) / 1000.0
            duration_min = float(route0.get("duration", 0.0)) / 60.0
            return path, distance_km, duration_min, base
        except Exception as exc:
            errors.append(f"{base}: {exc}")

    raise RuntimeError(" ; ".join(errors))


def _fetch_osrm_route_geometry(
    coords: Dict[int, Coord],
    route_nodes: Sequence[int],
    osrm_base_url: str,
    timeout_sec: float,
    max_waypoints_per_call: int = 90,
) -> Tuple[List[Coord], float, float, str]:
    """
    Obtiene la geometría completa de una ruta dividiéndola en fragmentos si supera
    max_waypoints_per_call. Une los segmentos eliminando el punto duplicado en la unión.
    Retorna (path_completo, distancia_total_km, duracion_total_min, endpoint_usado).
    """
    normalized_nodes: List[int] = []
    for n in route_nodes:
        node = int(n)
        if node not in coords:
            continue
        if normalized_nodes and normalized_nodes[-1] == node:
            continue
        normalized_nodes.append(node)

    if len(normalized_nodes) < 2:
        raise RuntimeError("OSRM route needs at least two valid nodes.")

    limit = max(2, int(max_waypoints_per_call))
    if len(normalized_nodes) <= limit:
        return _fetch_osrm_route_geometry_single(
            coords=coords,
            route_nodes=normalized_nodes,
            osrm_base_url=osrm_base_url,
            timeout_sec=timeout_sec,
        )

    full_path: List[Coord] = []
    total_km = 0.0
    total_min = 0.0
    endpoint_used = ""
    start = 0

    while start < len(normalized_nodes) - 1:
        end = min(start + limit - 1, len(normalized_nodes) - 1)
        chunk_nodes = normalized_nodes[start : end + 1]
        chunk_path, chunk_km, chunk_min, endpoint = _fetch_osrm_route_geometry_single(
            coords=coords,
            route_nodes=chunk_nodes,
            osrm_base_url=osrm_base_url,
            timeout_sec=timeout_sec,
        )
        if not endpoint_used:
            endpoint_used = endpoint
        if full_path and chunk_path:
            full_path.extend(chunk_path[1:])
        else:
            full_path.extend(chunk_path)
        total_km += chunk_km
        total_min += chunk_min
        start = end

    return full_path, total_km, total_min, endpoint_used


def build_real_distance_time_speed_matrices(
    coords: Dict[int, Coord],
    *,
    avg_speed_kmh: float = 32.0,
    use_osrm: bool = True,
    osrm_base_url: str = DEFAULT_PUBLIC_OSRM_BASE,
    timeout_sec: float = 3.0,
    max_osrm_points: int = 100,
) -> Tuple[Dict[Arc, float], Dict[Arc, float], Dict[Arc, float], Dict[str, object]]:
    """Build distance/time/speed matrices.

    Distances are in km, times in minutes, speeds in km/h.
    Uses OSRM when possible and falls back to haversine + average speed.
    """
    nodes = sorted(coords.keys())
    idx_of = {node: pos for pos, node in enumerate(nodes)}

    distances_osrm: Optional[List[List[Optional[float]]]] = None
    durations_osrm: Optional[List[List[Optional[float]]]] = None
    osrm_used = False
    osrm_endpoint = ""
    osrm_error = ""
    selected_osrm_base = _normalize_osrm_base(osrm_base_url) or DEFAULT_PUBLIC_OSRM_BASE
    local_boot_error = ""
    candidate_bases = _candidate_osrm_bases(selected_osrm_base)

    osrm_block_requests = 0
    if use_osrm:
        try:
            _, local_boot_error = _maybe_autostart_local_osrm(
                timeout_sec=max(1.5, min(float(timeout_sec) * 1.5, 4.5))
            )
            selected_osrm_base = _select_working_osrm_base(
                coords=coords,
                nodes=nodes,
                osrm_base_url=selected_osrm_base,
                timeout_sec=timeout_sec,
            )
            table_timeout = max(1.0, float(timeout_sec))
            if len(nodes) > max_osrm_points:
                table_timeout = max(table_timeout, 4.5)
            if len(nodes) <= max_osrm_points:
                distances_osrm, durations_osrm = _fetch_osrm_table_single_base(
                    coords=coords,
                    nodes=nodes,
                    base=selected_osrm_base,
                    timeout_sec=table_timeout,
                )
                osrm_endpoint = selected_osrm_base
                osrm_block_requests = 1
            else:
                distances_osrm, durations_osrm, osrm_endpoint, osrm_block_requests, block_errors = _fetch_osrm_table_blockwise(
                    coords=coords,
                    nodes=nodes,
                    osrm_base_url=selected_osrm_base,
                    timeout_sec=table_timeout,
                    max_osrm_points=max_osrm_points,
                )
                if block_errors:
                    osrm_error = " ; ".join(block_errors[:3])
                    if len(block_errors) > 3:
                        osrm_error += f" ; ... +{len(block_errors) - 3} bloques"
            if local_boot_error:
                if local_boot_error in osrm_error:
                    pass
                elif osrm_error:
                    osrm_error = f"{osrm_error} ; local_boot: {local_boot_error}"
                else:
                    osrm_error = f"local_boot: {local_boot_error}"
            osrm_used = True
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            osrm_error = str(exc)
            if local_boot_error and local_boot_error not in osrm_error:
                osrm_error = f"{osrm_error} ; local_boot: {local_boot_error}"
            osrm_used = False

    d: Dict[Arc, float] = {}
    t: Dict[Arc, float] = {}
    speed: Dict[Arc, float] = {}
    missing_pairs: List[Arc] = []
    observed_speeds: List[float] = []

    for i in nodes:
        for j in nodes:
            if i == j:
                d[i, j] = 0.0
                t[i, j] = 0.0
                speed[i, j] = 0.0
                continue

            dist_km: Optional[float] = None
            dur_min: Optional[float] = None
            pair_speed: Optional[float] = None

            if osrm_used and distances_osrm is not None and durations_osrm is not None:
                di = idx_of[i]
                dj = idx_of[j]
                dist_m = distances_osrm[di][dj]
                dur_s = durations_osrm[di][dj]
                if dist_m is not None and dur_s is not None and dur_s > 0:
                    dist_km = float(dist_m) / 1000.0
                    dur_min = float(dur_s) / 60.0
                    pair_speed = dist_km / (dur_min / 60.0) if dur_min > 0 else avg_speed_kmh
                    if pair_speed > 0:
                        observed_speeds.append(pair_speed)

            if dist_km is None:
                dist_km = haversine_km(coords[i], coords[j])
                missing_pairs.append((i, j))

            d[i, j] = round(dist_km, 4)

            if dur_min is None:
                t[i, j] = -1.0  # placeholder; set after we decide fallback speed.
                speed[i, j] = -1.0
            else:
                t[i, j] = round(dur_min, 4)
                speed[i, j] = round(max(1e-6, pair_speed or avg_speed_kmh), 4)

    if osrm_used and not observed_speeds:
        osrm_used = False
        if not osrm_error:
            osrm_error = "OSRM respondió sin pares utilizables; se aplicó fallback completo."

    max_avg_speed = max(observed_speeds) if observed_speeds else avg_speed_kmh
    fallback_speed = max(avg_speed_kmh, max_avg_speed)

    for (i, j) in missing_pairs:
        dist_km = d[i, j]
        speed[i, j] = round(fallback_speed, 4)
        t[i, j] = round(60.0 * dist_km / max(1e-6, fallback_speed), 4)

    meta = {
        "osrm_used": osrm_used,
        "osrm_endpoint": osrm_endpoint,
        "osrm_base_selected": selected_osrm_base,
        "osrm_error": osrm_error,
        "osrm_block_requests": osrm_block_requests,
        "osrm_candidates": candidate_bases,
        "osrm_local": _local_osrm_meta(),
        "max_avg_speed_kmh": round(float(max_avg_speed), 4),
        "fallback_speed_kmh": round(float(fallback_speed), 4),
        "missing_pairs": len(missing_pairs),
        "node_count": len(nodes),
    }
    return d, t, speed, meta


def _build_adjacency(nodes: List[int], d: Dict[Arc, float]) -> Dict[int, List[Tuple[int, float]]]:
    """
    Construye una lista de adyacencia dirigida desde la matriz de distancias.
    Excluye arcos con distancia negativa, infinita o nula (self-loops).
    """
    adj: Dict[int, List[Tuple[int, float]]] = {n: [] for n in nodes}
    for i in nodes:
        for j in nodes:
            if i == j:
                continue
            w = d.get((i, j))
            if w is None or w < 0 or math.isinf(w):
                continue
            adj[i].append((j, float(w)))
    return adj


def _dijkstra_k_nearest(
    source: int,
    k: int,
    adj: Dict[int, List[Tuple[int, float]]],
) -> List[int]:
    """
    Encuentra los k nodos más cercanos al nodo fuente usando Dijkstra con cola de prioridad.
    Retorna los nodos en orden creciente de distancia, excluyendo el propio fuente.
    """
    if k <= 0:
        return []

    dist: Dict[int, float] = {source: 0.0}
    visited = set()
    pq: List[Tuple[float, int]] = [(0.0, source)]
    nearest: List[int] = []

    while pq and len(nearest) < k:
        cur_d, node = heapq.heappop(pq)
        if node in visited:
            continue
        visited.add(node)

        if node != source:
            nearest.append(node)
            if len(nearest) >= k:
                break

        for nxt, w in adj.get(node, []):
            cand = cur_d + w
            if cand < dist.get(nxt, float("inf")):
                dist[nxt] = cand
                heapq.heappush(pq, (cand, nxt))

    return nearest


def _astar_shortest_path(
    source: int,
    target: int,
    adj: Dict[int, List[Tuple[int, float]]],
    coords: Dict[int, Coord],
) -> float:
    """
    Calcula la distancia mínima entre dos nodos usando A* con Haversine como heurística admisible.
    Retorna inf si no existe camino entre source y target.
    """
    if source == target:
        return 0.0

    g_score: Dict[int, float] = {source: 0.0}
    # La heurística es la distancia Haversine al destino (subestima la distancia real por carretera)
    open_heap: List[Tuple[float, float, int]] = [(haversine_km(coords[source], coords[target]), 0.0, source)]
    closed = set()

    while open_heap:
        _, cur_g, node = heapq.heappop(open_heap)
        if node in closed:
            continue
        if node == target:
            return cur_g
        closed.add(node)

        for nxt, w in adj.get(node, []):
            cand_g = cur_g + w
            if cand_g < g_score.get(nxt, float("inf")):
                g_score[nxt] = cand_g
                h = haversine_km(coords[nxt], coords[target])
                heapq.heappush(open_heap, (cand_g + h, cand_g, nxt))

    return float("inf")


def build_k_nearest_sets_with_search(
    coords: Dict[int, Coord],
    d: Dict[Arc, float],
    *,
    k_nearest: int,
    depot: int = 0,
) -> Tuple[Dict[int, List[int]], Dict[int, float]]:
    """Build k-nearest sets using Dijkstra and A*.

    - For each client node: Dijkstra-based k nearest.
    - For depot node: A*-based k nearest from depot.
    Returns:
      nearest_by_node: {node: [neighbor1, ...]}
      depot_costs: {client: path_distance_from_depot}
    """
    nodes = sorted(coords.keys())
    adj = _build_adjacency(nodes, d)

    nearest_by_node: Dict[int, List[int]] = {}
    for i in nodes:
        if i == depot:
            continue
        nearest_by_node[i] = _dijkstra_k_nearest(i, k_nearest, adj)

    depot_costs: Dict[int, float] = {}
    depot_ranked: List[Tuple[float, int]] = []
    if depot in coords:
        for j in nodes:
            if j == depot:
                continue
            c = _astar_shortest_path(depot, j, adj, coords)
            depot_costs[j] = c
            if math.isfinite(c):
                depot_ranked.append((c, j))
        depot_ranked.sort(key=lambda x: x[0])
        nearest_by_node[depot] = [j for _, j in depot_ranked[:k_nearest]]

    return nearest_by_node, depot_costs


def _dijkstra_shortest_paths(
    source: int,
    adj: Dict[int, List[Tuple[int, float]]],
) -> Dict[int, float]:
    """
    Calcula las distancias mínimas desde un nodo fuente a todos los nodos alcanzables
    usando Dijkstra. Retorna un dict {nodo: distancia_mínima}.
    """
    dist: Dict[int, float] = {source: 0.0}
    pq: List[Tuple[float, int]] = [(0.0, source)]

    while pq:
        cur, node = heapq.heappop(pq)
        if cur > dist.get(node, float("inf")):
            continue
        for nxt, w in adj.get(node, []):
            cand = cur + w
            if cand < dist.get(nxt, float("inf")):
                dist[nxt] = cand
                heapq.heappush(pq, (cand, nxt))
    return dist


def build_search_distance_time_matrices(
    coords: Dict[int, Coord],
    d: Dict[Arc, float],
    t: Dict[Arc, float],
    *,
    k_nearest: int = 5,
    depot: int = 0,
) -> Tuple[Dict[Arc, float], Dict[Arc, float], Dict[str, Any]]:
    """Refine dense matrices through Dijkstra/A* over a sparse candidate graph.

    Strategy:
    - Build neighbors with Dijkstra (k-nearest for each node).
    - Keep a sparse directed graph with those arcs + explicit depot links.
    - Recompute all-pairs shortest paths (distance and time) with Dijkstra.
    - Force client->depot distance through A* (target fixed on depot).
    """
    nodes = sorted(coords.keys())
    if not nodes:
        return {}, {}, {"search_enabled": False, "reason": "no_nodes"}

    k = max(1, int(k_nearest))
    nearest_by_node, depot_astar_costs = build_k_nearest_sets_with_search(
        coords=coords,
        d=d,
        k_nearest=k,
        depot=depot,
    )

    sparse_arcs = set()
    for i in nodes:
        for j in nearest_by_node.get(i, []):
            if i != j:
                sparse_arcs.add((int(i), int(j)))

    # Always keep direct links between every node and depot.
    if depot in nodes:
        for i in nodes:
            if i == depot:
                continue
            sparse_arcs.add((int(i), int(depot)))
            sparse_arcs.add((int(depot), int(i)))

    # Ensure at least one outgoing arc per client to avoid isolated nodes.
    for i in nodes:
        if i == depot:
            continue
        has_out = any(src == i for (src, _dst) in sparse_arcs)
        if has_out:
            continue
        candidates = [j for j in nodes if j != i]
        if not candidates:
            continue
        best = min(candidates, key=lambda jj: float(d.get((i, jj), float("inf"))))
        sparse_arcs.add((int(i), int(best)))

    adj_d: Dict[int, List[Tuple[int, float]]] = {int(n): [] for n in nodes}
    adj_t: Dict[int, List[Tuple[int, float]]] = {int(n): [] for n in nodes}
    for i, j in sparse_arcs:
        wd = d.get((i, j))
        wt = t.get((i, j))
        if wd is not None and wd >= 0 and math.isfinite(float(wd)):
            adj_d[i].append((j, float(wd)))
        if wt is not None and wt >= 0 and math.isfinite(float(wt)):
            adj_t[i].append((j, float(wt)))

    d_out: Dict[Arc, float] = {}
    t_out: Dict[Arc, float] = {}
    fallback_pairs_d = 0
    fallback_pairs_t = 0
    astar_to_depot_applied = 0
    astar_to_depot_fallbacks = 0

    for src in nodes:
        src_i = int(src)
        sp_d = _dijkstra_shortest_paths(src_i, adj_d)
        sp_t = _dijkstra_shortest_paths(src_i, adj_t)

        for dst in nodes:
            dst_i = int(dst)
            if src_i == dst_i:
                d_out[src_i, dst_i] = 0.0
                t_out[src_i, dst_i] = 0.0
                continue

            best_d = sp_d.get(dst_i)
            best_t = sp_t.get(dst_i)

            if best_d is None or not math.isfinite(best_d):
                best_d = float(d.get((src_i, dst_i), 0.0))
                fallback_pairs_d += 1
            if best_t is None or not math.isfinite(best_t):
                best_t = float(t.get((src_i, dst_i), 0.0))
                fallback_pairs_t += 1

            d_out[src_i, dst_i] = round(float(best_d), 4)
            t_out[src_i, dst_i] = round(float(best_t), 4)

        # Requirement: A* with target fixed on depot.
        if depot in nodes and src_i != depot:
            astar_cost = _astar_shortest_path(src_i, int(depot), adj_d, coords)
            if math.isfinite(astar_cost):
                d_out[src_i, int(depot)] = round(float(astar_cost), 4)
                astar_to_depot_applied += 1
            else:
                astar_to_depot_fallbacks += 1

    sample_nodes = nodes[: min(6, len(nodes))]
    nearest_preview = {
        str(int(i)): [int(j) for j in nearest_by_node.get(i, [])[:k]]
        for i in sample_nodes
    }

    meta = {
        "search_enabled": True,
        "k_nearest": k,
        "depot": int(depot),
        "node_count": len(nodes),
        "sparse_arc_count": len(sparse_arcs),
        "dijkstra_fallback_pairs_distance": int(fallback_pairs_d),
        "dijkstra_fallback_pairs_time": int(fallback_pairs_t),
        "astar_to_depot_applied": int(astar_to_depot_applied),
        "astar_to_depot_fallbacks": int(astar_to_depot_fallbacks),
        "nearest_preview": nearest_preview,
        "depot_astar_preview": {
            str(int(i)): round(float(v), 4)
            for i, v in list(depot_astar_costs.items())[: min(8, len(depot_astar_costs))]
            if math.isfinite(float(v))
        },
    }
    return d_out, t_out, meta


def build_route_polyline(
    route_nodes: Sequence[int],
    coords: Dict[int, Coord],
    *,
    use_osrm: bool = True,
    osrm_base_url: str = DEFAULT_PUBLIC_OSRM_BASE,
    timeout_sec: float = 4.0,
) -> Tuple[List[Coord], Dict[str, Any]]:
    """Build route geometry for display.

    Returns:
      - path: list of (lon, lat) coordinates
      - meta: diagnostics including whether OSRM geometry was used
    """
    normalized_nodes: List[int] = []
    for n in route_nodes:
        node = int(n)
        if node not in coords:
            continue
        if normalized_nodes and normalized_nodes[-1] == node:
            continue
        normalized_nodes.append(node)

    if len(normalized_nodes) < 2:
        return [], {"osrm_used": False, "error": "not_enough_nodes", "distance_km": 0.0, "duration_min": 0.0}

    osrm_error = ""
    local_boot_error = ""
    if use_osrm:
        _, local_boot_error = _maybe_autostart_local_osrm(timeout_sec=max(1.5, min(float(timeout_sec) * 1.5, 4.5)))
        try:
            path, dist_km, dur_min, endpoint = _fetch_osrm_route_geometry(
                coords=coords,
                route_nodes=normalized_nodes,
                osrm_base_url=osrm_base_url,
                timeout_sec=timeout_sec,
            )
            return path, {
                "osrm_used": True,
                "osrm_endpoint": endpoint,
                "error": "",
                "distance_km": round(float(dist_km), 4),
                "duration_min": round(float(dur_min), 4),
            }
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            osrm_error = str(exc)
    if local_boot_error:
        if local_boot_error in osrm_error:
            pass
        elif osrm_error:
            osrm_error = f"{osrm_error} ; local_boot: {local_boot_error}"
        else:
            osrm_error = f"local_boot: {local_boot_error}"

    fallback_path = [coords[n] for n in normalized_nodes]
    fallback_dist = 0.0
    for i in range(len(fallback_path) - 1):
        fallback_dist += haversine_km(fallback_path[i], fallback_path[i + 1])

    return fallback_path, {
        "osrm_used": False,
        "osrm_endpoint": "",
        "error": osrm_error,
        "distance_km": round(float(fallback_dist), 4),
        "duration_min": None,
    }


def build_route_polylines(
    routes: Sequence[Sequence[int]],
    coords: Dict[int, Coord],
    *,
    use_osrm: bool = True,
    osrm_base_url: str = DEFAULT_PUBLIC_OSRM_BASE,
    timeout_sec: float = 4.0,
) -> Tuple[List[List[Coord]], Dict[str, Any]]:
    """Build geometries for a batch of routes.

    Returns:
      - polylines aligned with routes
      - aggregate metadata
    """
    polylines: List[List[Coord]] = []
    per_route_meta: List[Dict[str, Any]] = []
    osrm_used_count = 0
    endpoints_used = set()

    osrm_enabled = use_osrm
    for route in routes:
        path, meta = build_route_polyline(
            route_nodes=route,
            coords=coords,
            use_osrm=osrm_enabled,
            osrm_base_url=osrm_base_url,
            timeout_sec=timeout_sec,
        )
        polylines.append(path)
        per_route_meta.append(meta)
        if bool(meta.get("osrm_used")):
            osrm_used_count += 1
            if meta.get("osrm_endpoint"):
                endpoints_used.add(str(meta.get("osrm_endpoint")))
        elif osrm_enabled and meta.get("error"):
            # If OSRM is unreachable, avoid retrying on every route.
            osrm_enabled = False

    aggregate = {
        "osrm_routes_used": osrm_used_count,
        "total_routes": len(polylines),
        "osrm_endpoints_used": sorted(endpoints_used),
        "per_route": per_route_meta,
    }
    return polylines, aggregate
