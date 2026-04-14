"""
Servicio de limpieza y geocodificación de datos para el pipeline VRP/VRPTW.

Extrae la lógica de estandarización del notebook Explorar.ipynb y la expone
como funciones reutilizables para el pipeline de la app. Cubre:
- Normalización de RUT chileno al formato XX.XXX.XXX-D.
- Estandarización de direcciones: expansión de abreviaturas, eliminación de
  datos de departamento, extracción de comuna desde el texto de dirección.
- Geocodificación de direcciones usando la API HTTP de Nominatim con caché
  persistente en disco (geocache.json) para evitar consultas redundantes.
- Limpieza de CSV de ventas y de detalle de pedidos.
- Pipeline completo (run_full_cleaning) que combina todos los pasos anteriores.
"""

import re
import io
import json
import time
import os
import logging
import unicodedata
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ── Configuración de Nominatim ────────────────────────────────────────────
# Apuntar a instancia local. Cambiar a "https://nominatim.openstreetmap.org"
# para usar la API pública (requiere respetar rate limit de 1 req/seg).
NOMINATIM_URL = os.getenv("NOMINATIM_URL", "http://localhost:8088")

# ── Comunas conocidas de Santiago (ordenadas por largo para regex) ─────────

COMUNAS_SANTIAGO = sorted([
    'Estación Central', 'Quinta Normal', 'Pedro Aguirre Cerda',
    'San Miguel', 'Lo Prado', 'Lo Barnechea', 'Lo Espejo',
    'Las Condes', 'La Florida', 'La Reina', 'La Cisterna',
    'La Granja', 'La Pintana', 'El Bosque', 'Cerro Navia',
    'San Bernardo', 'San Joaquín', 'San Ramón',
    'Puente Alto', 'Peñalolén', 'Independencia', 'Providencia',
    'Recoleta', 'Vitacura', 'Macul', 'Maipú', 'Ñuñoa',
    'Huechuraba', 'Conchalí', 'Cerrillos', 'Quilicura', 'Renca',
    'Santiago',
], key=len, reverse=True)

# Abreviaturas comunes -> forma completa
ABREVIATURAS = {
    r'\bAv\.?\b':   'Avenida',
    r'\bClle\.?\b': 'Calle',
    r'\bPje\.?\b':  'Pasaje',
    r'\bPsje\.?\b': 'Pasaje',
    r'\bGral\.?\b': 'General',
    r'\bSta\.?\b':  'Santa',
    r'\bSto\.?\b':  'Santo',
    r'\bGran\b':    'Gran',
}


# ── RUT ───────────────────────────────────────────────────────────────────

def estandarizar_rut(rut: str) -> str:
    """
    Normaliza un RUT chileno al formato XX.XXX.XXX-D.
    Ejemplo: '14512240-4' -> '14.512.240-4'
    """
    if pd.isna(rut):
        return rut
    rut = str(rut).replace(' ', '').replace('-', '').replace('.', '')
    if len(rut) == 9:
        return f"{rut[:2]}.{rut[2:5]}.{rut[5:8]}-{rut[8]}"
    elif len(rut) == 8:
        return f"{rut[:1]}.{rut[1:4]}.{rut[4:7]}-{rut[7]}"
    return rut  # devolver tal cual si no calza


# ── Dirección ─────────────────────────────────────────────────────────────

def _clean_comuna_value(value: object) -> str:
    """Limpia un valor de comuna: elimina espacios extra y valores nulos textuales."""
    raw = str(value).strip() if value is not None else ""
    if not raw or raw.lower() in {"nan", "none", "null"}:
        return ""
    return re.sub(r"\s+", " ", raw).strip()


def _normalize_text(value: object) -> str:
    """
    Normaliza texto para comparación sin distinción de tildes, mayúsculas ni
    caracteres especiales. Usado internamente para detectar comunas en strings.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    # Descomponer caracteres unicode y eliminar diacríticos (tildes, etc.)
    txt = unicodedata.normalize("NFKD", raw)
    txt = txt.encode("ascii", "ignore").decode("ascii")
    txt = txt.lower()
    # Reemplazar cualquier carácter no alfanumérico por espacio
    txt = re.sub(r"[^a-z0-9]+", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


_COMUNA_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _comuna in COMUNAS_SANTIAGO:
    _alias = _normalize_text(_comuna)
    if _alias:
        _COMUNA_ALIAS_TO_CANONICAL[_alias] = _comuna
_COMUNA_ALIAS_TO_CANONICAL.update(
    {
        "estacion central": "Estación Central",
        "nunoa": "Ñuñoa",
        "penalolen": "Peñalolén",
        "conchali": "Conchalí",
        "maipu": "Maipú",
        "lobarnechea": "Lo Barnechea",
        "santiago centro": "Santiago",
    }
)

_GENERIC_SANTIAGO_ALIASES = {
    "santiago",
    "santiago centro",
    "santiago de chile",
    "stgo",
    "stgo centro",
}


def _is_generic_santiago(value: object) -> bool:
    """Retorna True si el valor corresponde a un alias genérico de Santiago (sin comuna específica)."""
    return _normalize_text(value) in _GENERIC_SANTIAGO_ALIASES


def _extract_comuna_from_text(value: object) -> Optional[str]:
    """
    Detecta y retorna la comuna canónica contenida en un texto libre.

    Busca todos los alias conocidos de COMUNAS_SANTIAGO dentro del texto
    normalizado. Si detecta varias, prioriza la más específica (descarta
    "Santiago" genérico salvo que venga acompañado de "centro").
    """
    norm = _normalize_text(value)
    if not norm:
        return None

    matches: list[str] = []
    for alias, canonical in _COMUNA_ALIAS_TO_CANONICAL.items():
        if not alias:
            continue
        if re.search(rf"\b{re.escape(alias)}\b", norm):
            matches.append(canonical)

    if not matches:
        return None

    # Preferir comunas específicas sobre el genérico "Santiago"
    for comuna in matches:
        if comuna != "Santiago":
            return comuna

    # Si solo se detectó "Santiago", exigir mención explícita de "centro"
    if "santiago centro" in norm:
        return "Santiago"
    return None


def _canonicalize_comuna(value: object) -> str:
    """Limpia y canonicaliza un valor de comuna. Si no reconoce la comuna, retorna el valor limpio original."""
    cleaned = _clean_comuna_value(value)
    if not cleaned:
        return ""
    detected = _extract_comuna_from_text(cleaned)
    return detected or cleaned


def estandarizar_direccion_y_comuna(direccion: str) -> tuple[str, Optional[str]]:
    """
    Estandariza una dirección y extrae la comuna contenida en ella.

    Aplica en orden: Title Case, eliminación de depto, normalización de
    alias de Santiago, expansión de abreviaturas, separación calle+número
    y limpieza de tokens residuales ("Santiago", "Chile", comas).
    Retorna (dirección_estandarizada, comuna_detectada_o_None).
    """
    if pd.isna(direccion):
        return (direccion, None)

    d = str(direccion)
    comuna_encontrada = None

    # Normalizar espacios múltiples y aplicar Title Case para consistencia
    d = re.sub(r'\s+', ' ', d).strip().title()

    # Eliminar info de departamento que no es parte de la dirección vial
    d = re.sub(r',?\s*(?:Depto|Dpto|Departamento)\.?\s*\d+', '', d, flags=re.IGNORECASE)

    # Normalizar "Stgo" -> "Santiago", "Santiago De Chile" -> "Santiago"
    d = re.sub(r'\bStgo\b', 'Santiago', d)
    d = re.sub(r'Santiago\s+De\s+Chile', 'Santiago', d, flags=re.IGNORECASE)

    # Expandir abreviaturas comunes (Av., Pje., Gral., etc.)
    for patron, reemplazo in ABREVIATURAS.items():
        d = re.sub(patron, reemplazo, d, flags=re.IGNORECASE)

    # Separar "calle + número" del resto (info de comuna, depto, ciudad)
    match = re.match(r'^(.+?\s+\d+)\b(.*)', d)

    if match:
        calle_numero = match.group(1).strip()
        resto = match.group(2).strip()

        # Buscar la comuna primero en el "resto" (más específico) y luego en toda la dirección
        comuna_encontrada = _extract_comuna_from_text(resto) or _extract_comuna_from_text(d)
        resultado = calle_numero
    else:
        # Si no hay número en la dirección, buscar la comuna en el texto completo
        resultado = d
        comuna_encontrada = _extract_comuna_from_text(resultado)

    # Eliminar tokens "Santiago", "Chile" y comas residuales del string de dirección final
    resultado = re.sub(r',?\s*Santiago(?:\s+Centro)?\b', '', resultado, flags=re.IGNORECASE)
    resultado = re.sub(r',?\s*Chile\b', '', resultado, flags=re.IGNORECASE)
    resultado = re.sub(r'\s+', ' ', resultado).strip().strip(',').strip()
    resultado = resultado.replace(".", "")

    # Ya no forzamos el string "calle, Santiago, Chile" aquí,
    # solo retornamos la calle limpia y la comuna limpia.
    return (resultado, comuna_encontrada)


# ── Geocodificación con OSMnx ─────────────────────────────────────────────

def geocodificar_direccion(calle_numero: str, comuna: str) -> tuple[Optional[float], Optional[float]]:
    """
    Obtiene (latitud, longitud) llamando directamente a la API HTTP de Nominatim.

    Usa NOMINATIM_URL (configurable por variable de entorno) en lugar de osmnx
    para evitar el rate-limit de 1 seg/req que osmnx impone incluso a instancias
    locales. Acota la búsqueda a Chile con countrycodes=cl.
    Retorna (None, None) si la consulta falla o no produce resultados.
    """
    import requests as req
    try:
        query = f"{calle_numero}, {comuna}, Región Metropolitana, Chile"
        url = NOMINATIM_URL.rstrip("/") + "/search"
        params = {
            "q": query,
            "format": "json",
            "limit": 1,          # Solo necesitamos el resultado más relevante
            "countrycodes": "cl",
        }
        headers = {"User-Agent": "CapstoneAnalytics/1.0"}
        response = req.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        results = response.json()
        if results:
            return (float(results[0]["lat"]), float(results[0]["lon"]))
    except Exception as e:
        logger.warning(f"Geocodificación falló para '{calle_numero}, {comuna}': {e}")
    return (None, None)

# Archivo de caché persistente — ruta absoluta para que funcione sin importar el CWD
_SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(_SERVICE_DIR, "..", "..", "..", "geocache.json")
CACHE_FILE = os.path.normpath(CACHE_FILE)  # e.g. /…/Capstone/geocache.json

def cargar_cache() -> dict:
    """Carga el caché de geocodificación desde disco. Retorna dict vacío si el archivo no existe o está corrupto."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {}

def guardar_cache(cache: dict):
    """Persiste el caché de geocodificación en disco como JSON para sobrevivir reinicios."""
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def geocodificar_dataframe(df: pd.DataFrame, col_direccion: str = "Dirección cliente", col_comuna: str = "Comuna") -> pd.DataFrame:
    """
    Agrega columnas Latitud y Longitud al DataFrame geocodificando direcciones únicas.

    Deduplica por (dirección, comuna) antes de consultar la API para minimizar
    llamadas. Usa caché persistente en geocache.json; solo consulta Nominatim
    para direcciones no cacheadas o cacheadas como fallidas. Guarda el caché
    cada 10 consultas nuevas para no perder progreso ante interrupciones.
    """
    cache = cargar_cache()

    # Clave compuesta dirección|comuna para identificar unívocamente cada geocodificación
    df['_geo_key'] = df[col_direccion] + "|" + df[col_comuna]
    direcciones_unicas = df[['_geo_key', col_direccion, col_comuna]].drop_duplicates().dropna()

    nuevas_consultas = 0
    logger.info(f"Procesando {len(direcciones_unicas)} direcciones únicas...")

    for i, row in direcciones_unicas.iterrows():
        try:
            key = row['_geo_key']
            calle = row[col_direccion]
            comuna = row[col_comuna]

            cached = cache.get(key)
            # Re-intentar si no está en caché o si fue cacheado como fallido (None, None)
            if cached is None or cached == [None, None] or cached == (None, None):
                cache[key] = geocodificar_direccion(calle, comuna)
                nuevas_consultas += 1
                if nuevas_consultas % 10 == 0:
                    guardar_cache(cache)  # Persistir progreso parcial
                    logger.info(f"  ... {nuevas_consultas} consultadas a la API Nominatim")
        except Exception as e:
            logger.error(f"Error geocodificando dirección '{calle}, {comuna}': {e}")
            continue

    guardar_cache(cache)

    # Mapear las coordenadas del caché de vuelta al DataFrame completo (incluye duplicados)
    df["Latitud"] = df['_geo_key'].map(lambda x: cache.get(x, (None, None))[0] if pd.notna(x) else None)
    df["Longitud"] = df['_geo_key'].map(lambda x: cache.get(x, (None, None))[1] if pd.notna(x) else None)
    df = df.drop(columns=['_geo_key'])

    n_ok = df["Latitud"].notna().sum()
    n_fail = df["Latitud"].isna().sum()
    logger.info(f"Geocodificación terminada: {n_ok} OK, {n_fail} Fallidas (desde caché y API)")

    return df


# ── Pipeline principal ────────────────────────────────────────────────────

class CleaningResult:
    """
    Encapsula el resultado del pipeline completo de limpieza.

    Atributos:
        df_ventas: DataFrame de ventas limpio y geocodificado.
        df_detalle: DataFrame de detalle de pedidos deduplicado.
        errores: Lista de dicts con estructura {origen, fila, campo, valor_original, error}.
    """

    def __init__(self, df_ventas: pd.DataFrame, df_detalle: pd.DataFrame,
                 errores: list[dict]):
        self.df_ventas = df_ventas
        self.df_detalle = df_detalle
        self.errores = errores  # [{fila, campo, valor_original, error}, ...]


def clean_ventas(csv_text: str) -> tuple[pd.DataFrame, list[dict]]:
    """
    Pipeline de limpieza para el CSV de ventas.

    Aplica en orden: estandarización de RUT y estandarización de direcciones
    con resolución de comuna (la columna "Comuna" del CSV tiene prioridad sobre
    la comuna detectada dentro del texto de la dirección, salvo que sea un alias
    genérico de Santiago). Los errores se acumulan por fila sin interrumpir el
    procesamiento del resto del DataFrame.
    Retorna (DataFrame limpio, lista de errores).
    """
    errores = []

    df = pd.read_csv(
        io.StringIO(csv_text),
        dtype={'RUT': object, 'Nombre cliente': object, 'Número de Orden': object},
        parse_dates=['Fecha de Pedido', 'Fecha de despacho Solicitada']
    )

    # Paso 1: estandarizar RUT fila por fila
    for idx, row in df.iterrows():
        rut_original = row['RUT']
        try:
            df.at[idx, 'RUT'] = estandarizar_rut(rut_original)
        except Exception as e:
            errores.append({
                "fila": int(idx),
                "campo": "RUT",
                "valor_original": str(rut_original),
                "error": str(e)
            })

    # Paso 2: estandarizar dirección y resolver comuna fila por fila
    for idx, row in df.iterrows():
        dir_original = row.get('Dirección cliente', '')
        comuna_original = _canonicalize_comuna(row.get('Comuna', ''))
        try:
            dir_std, comuna_detectada = estandarizar_direccion_y_comuna(dir_original)
            comuna_detectada = _canonicalize_comuna(comuna_detectada)

            # Prioridad de fuente de comuna:
            # 1. Columna "Comuna" explícita y específica (no genérico "Santiago")
            # 2. Comuna extraída del texto de la dirección
            # 3. Columna "Comuna" original (aunque sea genérica)
            if comuna_original and not _is_generic_santiago(comuna_original):
                comuna = comuna_original
            elif comuna_detectada:
                comuna = comuna_detectada
            else:
                comuna = comuna_original
            df.at[idx, 'Dirección cliente'] = dir_std
            df.at[idx, 'Comuna'] = comuna
        except Exception as e:
            errores.append({
                "fila": int(idx),
                "campo": "Dirección cliente",
                "valor_original": str(dir_original),
                "error": str(e)
            })

    return df, errores


def clean_detalle(csv_text: str) -> tuple[pd.DataFrame, list[dict]]:
    """
    Pipeline de limpieza para el CSV de detalle de pedidos.

    Valida la presencia de columnas requeridas y deduplica filas agrupando por
    (Número de Orden, SKU): suma Cantidad, Volumen_total_m3 y Peso_total_kg;
    toma el primer valor para columnas descriptivas/unitarias.
    Retorna (DataFrame limpio, lista de errores).
    """
    errores = []

    df = pd.read_csv(io.StringIO(csv_text))

    # Verificar columnas mínimas antes de procesar; abortar si faltan
    required = ['Número de Orden', 'SKU', 'Cantidad']
    missing = [c for c in required if c not in df.columns]
    if missing:
        errores.append({
            "fila": -1,
            "campo": "columnas",
            "valor_original": str(missing),
            "error": f"Columnas faltantes: {missing}"
        })
        return df, errores

    # Construir el diccionario de agregación dinámicamente según las columnas presentes
    agg_dict = {'Cantidad': 'sum'}

    # Columnas descriptivas/unitarias: tomar el primer valor del grupo
    optional_first = [
        'Descripción SKU', 'Largo_cm', 'Ancho_cm', 'Alto_cm',
        'Volumen_unitario_m3', 'Peso_unitario_kg'
    ]
    # Columnas de totales: sumar dentro del grupo para acumular multi-línea
    optional_sum = ['Volumen_total_m3', 'Peso_total_kg']

    for col in optional_first:
        if col in df.columns:
            agg_dict[col] = 'first'
    for col in optional_sum:
        if col in df.columns:
            agg_dict[col] = 'sum'

    df = df.groupby(['Número de Orden', 'SKU']).agg(agg_dict).reset_index()

    return df, errores


def run_full_cleaning(ventas_csv: str, detalle_csv: str,
                      geocode: bool = True) -> CleaningResult:
    """
    Ejecuta el pipeline completo de limpieza y retorna un CleaningResult.

    Pasos:
    1. Limpia ventas: estandarización de RUT y direcciones (clean_ventas).
    2. Limpia detalle: validación de columnas y deduplicación (clean_detalle).
    3. Geocodifica direcciones del CSV de ventas si geocode=True.
       Las filas que no obtienen coordenadas se registran como errores de
       geocodificación para que el router pueda informarlas al usuario.
    """
    df_ventas, errores_ventas = clean_ventas(ventas_csv)
    df_detalle, errores_detalle = clean_detalle(detalle_csv)

    # Combinar errores de ambas fuentes, etiquetando su origen para trazabilidad
    all_errors = (
        [{"origen": "ventas", **e} for e in errores_ventas] +
        [{"origen": "detalle", **e} for e in errores_detalle]
    )

    if geocode:
        df_ventas = geocodificar_dataframe(df_ventas)

        # Las filas sin coordenadas no pueden ser asignadas a rutas; se reportan como error
        sin_coords = df_ventas[df_ventas['Latitud'].isna()]
        for idx, row in sin_coords.iterrows():
            all_errors.append({
                "origen": "geocodificación",
                "fila": int(idx),
                "campo": "Dirección cliente",
                "valor_original": str(row.get('Dirección cliente', '')),
                "error": "No se pudieron obtener coordenadas"
            })

    return CleaningResult(
        df_ventas=df_ventas,
        df_detalle=df_detalle,
        errores=all_errors
    )
