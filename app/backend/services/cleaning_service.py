"""
Servicio de limpieza de datos.
Combina la lógica de estandarización del pipeline avanzado con la persistencia en base de datos de SaaS.
"""

import re
import io
import json
import time
import os
import logging
from typing import Optional, Tuple, List

import pandas as pd
import requests as req
from database.connection import get_session
from database.repositories.venta_repository import VentaRepository

logger = logging.getLogger(__name__)

# ── Configuración de Nominatim ────────────────────────────────────────────
NOMINATIM_URL = os.getenv("NOMINATIM_URL", "http://localhost:8088")

# Archivo de caché persistente
_SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.normpath(os.path.join(_SERVICE_DIR, "..", "..", "..", "geocache.json"))

class CleaningResult:
    """Encapsula el resultado de la limpieza con errores encontrados."""
    def __init__(self, df_ventas: pd.DataFrame, df_detalle: pd.DataFrame, errores: List[dict]):
        self.df_ventas = df_ventas
        self.df_detalle = df_detalle
        self.errores = errores

class CleaningService:
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

    FALLBACK_PREFIXES = [
        r'^Avenida\s+', r'^Calle\s+', r'^Pasaje\s+', r'^Av\.?\s+', r'^Clle\.?\s+', r'^Pje\.?\s+', r'^Psje\.?\s+', r'^Gral\.?\s+'
    ]

    def __init__(self):
        self.cache = self._cargar_cache()
        self.failed_addresses = [] 
        self.nominatim_enabled = True # Circuit breaker

    # ── Cache Management ──
    def _cargar_cache(self) -> dict:
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                pass
        return {}

    def _guardar_cache(self):
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error guardando caché: {e}")

    # ── Standardization ──
    def standardize_rut(self, rut: str) -> str:
        if not rut or pd.isna(rut): return ""
        rut = str(rut).replace(' ', '').replace('-', '').replace('.', '')
        if len(rut) == 9:
            return f"{rut[:2]}.{rut[2:5]}.{rut[5:8]}-{rut[8]}"
        elif len(rut) == 8:
            return f"{rut[:1]}.{rut[1:4]}.{rut[4:7]}-{rut[7]}"
        return rut

    def standardize_address(self, address: str) -> Tuple[str, Optional[str]]:
        if not address or pd.isna(address):
            return ("", None)

        d = str(address)
        comuna_encontrada = None

        d = re.sub(r'\s+', ' ', d).strip().title()
        d = re.sub(r',?\s*(?:Depto|Dpto|Departamento)\.?\s*\d+', '', d, flags=re.IGNORECASE)
        d = re.sub(r'\bStgo\b', 'Santiago', d)
        d = re.sub(r'Santiago\s+De\s+Chile', 'Santiago', d, flags=re.IGNORECASE)

        for patron, reemplazo in self.ABREVIATURAS.items():
            d = re.sub(patron, reemplazo, d, flags=re.IGNORECASE)

        match = re.match(r'^(.+?\s+\d+)\b(.*)', d)
        if match:
            calle_numero = match.group(1).strip()
            resto = match.group(2).strip()
            for comuna in self.COMUNAS_SANTIAGO:
                patron = r',?\s*' + re.escape(comuna) + r'\s*,?'
                if re.search(patron, resto, flags=re.IGNORECASE):
                    comuna_encontrada = comuna
                    break
            resultado = calle_numero
        else:
            resultado = d
            for comuna in self.COMUNAS_SANTIAGO:
                patron = r',?\s*' + re.escape(comuna) + r'\s*,?'
                if re.search(patron, resultado, flags=re.IGNORECASE):
                    comuna_encontrada = comuna
                    resultado = re.sub(patron, '', resultado, flags=re.IGNORECASE).strip()
                    break

        resultado = re.sub(r',?\s*Santiago\b', '', resultado, flags=re.IGNORECASE)
        resultado = re.sub(r',?\s*Chile\b', '', resultado, flags=re.IGNORECASE)
        resultado = re.sub(r'\s+', ' ', resultado).strip().strip(',').strip()
        resultado = resultado.replace(".", "")

        if comuna_encontrada is None:
            comuna_encontrada = 'Santiago'

        return (resultado, comuna_encontrada)

    # ── Geocoding ──
    def _geocode_query(self, query: str) -> Tuple[Optional[float], Optional[float]]:
        """Internal helper to hit the Nominatim API with 2s timeout and circuit breaker."""
        if not self.nominatim_enabled:
            return (None, None)

        try:
            url = NOMINATIM_URL.rstrip("/") + "/search"
            params = {"q": query, "format": "json", "limit": 1, "countrycodes": "cl"}
            headers = {"User-Agent": "CapstoneAnalytics/1.0"}
            
            if "localhost" not in NOMINATIM_URL:
                time.sleep(1.0)
                
            response = req.get(url, params=params, headers=headers, timeout=2) # Reduced to 2s
            response.raise_for_status()
            results = response.json()
            if results:
                return (float(results[0]["lat"]), float(results[0]["lon"]))
        except req.exceptions.ConnectionError:
            logger.error("No se pudo conectar con el servidor Nominatim. Desactivando para este lote.")
            self.nominatim_enabled = False
        except Exception as e:
            logger.debug(f"Query '{query}' failed: {e}")
        return (None, None)

    def geocode_address(self, calle_numero: str, comuna: str) -> Tuple[Optional[float], Optional[float]]:
        key = f"{calle_numero}|{comuna}"
        cached = self.cache.get(key)
        if cached and cached != [None, None] and cached != (None, None):
            return tuple(cached)

        # 1st Pass: Original standardized address
        query1 = f"{calle_numero}, {comuna}, Región Metropolitana, Chile"
        coords = self._geocode_query(query1)

        # 2nd Pass: Fallback (removing common prefixes)
        if coords == (None, None):
            for pattern in self.FALLBACK_PREFIXES:
                clean_addr = re.sub(pattern, '', calle_numero, flags=re.IGNORECASE).strip()
                if clean_addr != calle_numero:
                    query_fb = f"{clean_addr}, {comuna}, Región Metropolitana, Chile"
                    coords = self._geocode_query(query_fb)
                    if coords != (None, None):
                        logger.info(f"Fallback success: '{calle_numero}' -> '{clean_addr}'")
                        break

        if coords != (None, None):
            self.cache[key] = coords
            self._guardar_cache()
            return coords
        
        # 3rd Pass: Just address + Chile (generic)
        if coords == (None, None):
            query_gen = f"{calle_numero}, Chile"
            coords = self._geocode_query(query_gen)
            if coords != (None, None):
                self.cache[key] = coords
                self._guardar_cache()
                return coords

        self.cache[key] = (None, None)
        return (None, None)

    # ── Database Operations (SaaS) ──
    def process_single_order(self, order_data: dict) -> dict:
        """
        Processes a single order, geocodes and saves to DB.
        Returns a result dict: {status: 'ok'|'error', numero_orden: str, error: str|None}
        """
        raw_num = str(order_data.get('numero_orden') or order_data.get('Número de Orden', ''))
        if not raw_num:
            return {"status": "error", "numero_orden": "N/A", "error": "Número de orden faltante"}

        try:
            raw_rut = order_data.get('rut') or order_data.get('RUT', '')
            raw_addr = order_data.get('direccion_cliente') or order_data.get('Dirección cliente', '')
            raw_nombre = order_data.get('nombre_cliente') or order_data.get('Nombre cliente', '')
            raw_comuna_hint = order_data.get('comuna') or order_data.get('Comuna', '')
            
            clean_rut = self.standardize_rut(raw_rut)
            street, comuna = self.standardize_address(raw_addr)
            final_comuna = comuna or raw_comuna_hint or "Santiago"
            
            lat, lon = self.geocode_address(street, final_comuna)
            if lat is None or lon is None:
                return {"status": "error", "numero_orden": raw_num, "error": f"Geocodificación falló para: {street}, {final_comuna}"}
            
            raw_fecha_ped = order_data.get('fecha_pedido') or order_data.get('Fecha de Pedido')
            raw_fecha_desp = order_data.get('fecha_despacho_solicitada') or order_data.get('Fecha de despacho Solicitada')
            
            fecha_ped = pd.to_datetime(raw_fecha_ped).date() if pd.notna(raw_fecha_ped) else None
            fecha_desp = pd.to_datetime(raw_fecha_desp).date() if pd.notna(raw_fecha_desp) else None

            venta_payload = {
                "numero_orden": raw_num,
                "rut": clean_rut,
                "nombre_cliente": raw_nombre,
                "direccion_cliente": f"{street}, Santiago, Chile",
                "comuna": final_comuna,
                "fecha_pedido": fecha_ped,
                "estado": str(order_data.get('estado') or order_data.get('Estado', 'Pendiente')),
                "monto_pedido": int(order_data.get('monto_pedido') or order_data.get('Monto Pedido', 0)),
                "fecha_despacho_solicitada": fecha_desp,
                "latitud": lat,
                "longitud": lon
            }
            
            session = get_session()
            try:
                repo = VentaRepository(session)
                repo.upsert(venta_payload)
            finally:
                session.close()
            return {"status": "ok", "numero_orden": raw_num}
        except Exception as e:
            logger.error(f"Error procesando pedido {raw_num}: {e}")
            return {"status": "error", "numero_orden": raw_num, "error": str(e)}

    def process_dataframe(self, df: pd.DataFrame) -> List[dict]:
        """Processes a dataframe and returns a list of result dicts."""
        results = []
        for _, row in df.iterrows():
            res = self.process_single_order(row.to_dict())
            results.append(res)
        return results

    def get_pending_orders_df(self, user_id: Optional[int] = None) -> pd.DataFrame:
        """
        Fetches all 'PENDIENTE' orders from DB joined with their volume/weight totals
        from the 'detalle' table. Returns a DataFrame suitable for the optimizer.
        """
        from database.models import Venta, Detalle
        from sqlalchemy import func
        
        session = get_session()
        try:
            # 1. Query Detalle totals per order
            stats = session.query(
                Detalle.numero_orden,
                func.sum(Detalle.peso_total_kg).label("Peso_total_pedido"),
                func.sum(Detalle.volumen_total_m3).label("Volumen_total_pedido")
            ).group_by(Detalle.numero_orden).subquery()

            # 2. Query Pending Ventas joined with stats
            query = session.query(Venta, stats.c.Peso_total_pedido, stats.c.Volumen_total_pedido)\
                .outerjoin(stats, Venta.numero_orden == stats.c.numero_orden)\
                .filter(Venta.estado == "Pendiente")
            
            if user_id is not None:
                query = query.filter(Venta.id_usuario == user_id)
            
            rows = query.all()
            
            data = []
            for v, peso, vol in rows:
                data.append({
                    "Número de Orden": v.numero_orden,
                    "RUT": v.rut,
                    "Nombre cliente": v.nombre_cliente,
                    "Dirección cliente": v.direccion_cliente,
                    "Comuna": v.comuna,
                    "Fecha de despacho Solicitada": v.fecha_despacho_solicitada,
                    "Latitud": v.latitud,
                    "Longitud": v.longitud,
                    "Peso_total_pedido": peso or 0.0,
                    "Volumen_total_pedido": vol or 0.0,
                    "Monto Pedido": v.monto_pedido
                })
            
            return pd.DataFrame(data)
        finally:
            session.close()

    # ── Pipeline Methods (Advanced) ──
    def clean_ventas(self, csv_text: str) -> Tuple[pd.DataFrame, List[dict]]:
        errores = []
        df = pd.read_csv(
            io.StringIO(csv_text),
            dtype={'RUT': object, 'Nombre cliente': object, 'Número de Orden': object},
            parse_dates=['Fecha de Pedido', 'Fecha de despacho Solicitada']
        )

        for idx, row in df.iterrows():
            rut_orig = row['RUT']
            try:
                df.at[idx, 'RUT'] = self.standardize_rut(rut_orig)
            except Exception as e:
                errores.append({"fila": int(idx), "campo": "RUT", "valor_original": str(rut_orig), "error": str(e)})

            dir_orig = row.get('Dirección cliente', '')
            try:
                dir_std, comuna = self.standardize_address(dir_orig)
                df.at[idx, 'Dirección cliente'] = dir_std
                df.at[idx, 'Comuna'] = comuna
            except Exception as e:
                errores.append({"fila": int(idx), "campo": "Dirección cliente", "valor_original": str(dir_orig), "error": str(e)})

        return df, errores

    def clean_detalle(self, csv_text: str) -> Tuple[pd.DataFrame, List[dict]]:
        errores = []
        df = pd.read_csv(io.StringIO(csv_text))
        required = ['Número de Orden', 'SKU', 'Cantidad']
        missing = [c for c in required if c not in df.columns]
        if missing:
            errores.append({"fila": -1, "campo": "columnas", "valor_original": str(missing), "error": f"Columnas faltantes: {missing}"})
            return df, errores

        agg_dict = {'Cantidad': 'sum'}
        optional_first = ['Descripción SKU', 'Largo_cm', 'Ancho_cm', 'Alto_cm', 'Volumen_unitario_m3', 'Peso_unitario_kg']
        optional_sum = ['Volumen_total_m3', 'Peso_total_kg']
        for col in optional_first:
            if col in df.columns: agg_dict[col] = 'first'
        for col in optional_sum:
            if col in df.columns: agg_dict[col] = 'sum'

        df = df.groupby(['Número de Orden', 'SKU']).agg(agg_dict).reset_index()
        return df, errores

    def geocodificar_dataframe(self, df: pd.DataFrame, col_dir: str = "Dirección cliente", col_com: str = "Comuna") -> pd.DataFrame:
        logger.info(f"Geocodificando dataframe con {len(df)} filas...")
        lats, lons = [], []
        for _, row in df.iterrows():
            lat, lon = self.geocode_address(row[col_dir], row[col_com])
            lats.append(lat)
            lons.append(lon)
        df["Latitud"] = lats
        df["Longitud"] = lons
        return df

    def run_full_cleaning(self, ventas_csv: str, detalle_csv: str, geocode: bool = True) -> CleaningResult:
        df_ventas, err_v = self.clean_ventas(ventas_csv)
        df_detalle, err_d = self.clean_detalle(detalle_csv)
        all_err = [{"origen": "ventas", **e} for e in err_v] + [{"origen": "detalle", **e} for e in err_d]

        if geocode:
            df_ventas = self.geocodificar_dataframe(df_ventas)
            sin_coords = df_ventas[df_ventas['Latitud'].isna()]
            
            if not sin_coords.empty:
                failed_file = "failed_geocoding.csv"
                sin_coords[['Número de Orden', 'Dirección cliente', 'Comuna']].to_csv(failed_file, index=False)
                logger.info(f"Saved {len(sin_coords)} failed addresses to {failed_file}")

            for idx, row in sin_coords.iterrows():
                all_err.append({
                    "origen": "geocodificación", "fila": int(idx), "campo": "Dirección cliente",
                    "valor_original": str(row.get('Dirección cliente', '')), "error": "No se pudieron obtener coordenadas"
                })

        return CleaningResult(df_ventas, df_detalle, all_err)

# Standalone function for router.py compatibility (if called directly)
def run_full_cleaning(ventas_csv: str, detalle_csv: str, geocode: bool = True) -> CleaningResult:
    svc = CleaningService()
    return svc.run_full_cleaning(ventas_csv, detalle_csv, geocode)
