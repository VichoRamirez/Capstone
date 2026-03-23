import re
import time
import pandas as pd
import osmnx as ox
from typing import Tuple, List, Optional
from database.connection import get_session
from database.repositories.venta_repository import VentaRepository

class CleaningService:
    ABREVIATURAS = {
        r'\bAv\.?\b':       'Avenida',
        r'\bClle\.?\b':     'Calle',
        r'\bPje\.?\b':      'Pasaje',
        r'\bPsje\.?\b':     'Pasaje',
        r'\bGral\.?\b':     'General',
        r'\bSta\.?\b':      'Santa',
        r'\bSto\.?\b':      'Santo',
    }

    COMUNAS_SANTIAGO = [
        'Estación Central', 'Quinta Normal', 'Pedro Aguirre Cerda',
        'San Miguel', 'Lo Prado', 'Lo Barnechea', 'Lo Espejo',
        'Las Condes', 'La Florida', 'La Reina', 'La Cisterna',
        'La Granja', 'La Pintana', 'El Bosque', 'Cerro Navia',
        'San Bernardo', 'San Joaquín', 'San Ramón',
        'Puente Alto', 'Peñalolén', 'Independencia', 'Providencia',
        'Recoleta', 'Vitacura', 'Macul', 'Maipú', 'Ñuñoa',
        'Huechuraba', 'Conchalí', 'Cerrillos', 'Quilicura', 'Renca',
        'Santiago'
    ]

    def __init__(self):
        self.COMUNAS_SANTIAGO.sort(key=len, reverse=True)

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

        # Normalization
        d = re.sub(r'\s+', ' ', d).strip().title()
        d = re.sub(r',?\s*(?:Depto|Dpto|Departamento)\.?\s*\d+', '', d, flags=re.IGNORECASE)
        d = re.sub(r'\bStgo\b', 'Santiago', d)

        for patron, reemplazo in self.ABREVIATURAS.items():
            d = re.sub(patron, reemplazo, d, flags=re.IGNORECASE)

        # Extraction logic
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

        # Remove trailing Santiago/Chile from the base street
        resultado = re.sub(r',?\s*Santiago\b', '', resultado, flags=re.IGNORECASE)
        resultado = re.sub(r',?\s*Chile\b', '', resultado, flags=re.IGNORECASE)
        resultado = re.sub(r'\s+', ' ', resultado).strip().strip(',').strip()
        resultado = resultado.replace(".", "")

        return (resultado, comuna_encontrada)

    def geocode_address(self, address: str) -> Tuple[Optional[float], Optional[float]]:
        """Geocodes an address using OSMnx with rate limiting."""
        try:
            # Respect OSM Nominatim policy: 1.5s wait to be safe
            time.sleep(1.5)
            ox.settings.overpass_settings = '[out:json][timeout:90][user_agent="CapstoneAnalytics/1.0"]'
            point = ox.geocoder.geocode(address)
            return point[0], point[1]
        except Exception as e:
            print(f"Error geocoding {address}: {e}")
            return None, None

    def process_single_order(self, order_data: dict):
        """Processes a single order dict, geocodes and saves to DB."""
        raw_num = order_data.get('numero_orden') or order_data.get('Número de Orden', '')
        print(f"🧹 Cleaning Order: {raw_num}")
        
        raw_rut = order_data.get('rut') or order_data.get('RUT', '')
        raw_addr = order_data.get('direccion_cliente') or order_data.get('Dirección cliente', '')
        raw_nombre = order_data.get('nombre_cliente') or order_data.get('Nombre cliente', '')
        raw_comuna_hint = order_data.get('comuna') or order_data.get('Comuna', '')
        
        clean_rut = self.standardize_rut(raw_rut)
        street, comuna = self.standardize_address(raw_addr)
        
        # Priority: 1. Extracted from address, 2. Manual hint from UI/Field, 3. Default
        final_comuna = comuna or raw_comuna_hint or "Santiago"
        
        # Build geocoding string (needs more context)
        geocode_query = f"{street}, {final_comuna}, Santiago, Chile"
        
        # Build DB address string (user wants it cleaner)
        db_address = f"{street}, Santiago, Chile"

        print(f"📍 Geocoding: {geocode_query}")
        lat, lon = self.geocode_address(geocode_query)
        
        # Date processing
        raw_fecha_ped = order_data.get('fecha_pedido') or order_data.get('Fecha de Pedido')
        raw_fecha_desp = order_data.get('fecha_despacho_solicitada') or order_data.get('Fecha de despacho Solicitada')
        
        fecha_ped = pd.to_datetime(raw_fecha_ped).date() if pd.notna(raw_fecha_ped) else None
        fecha_desp = pd.to_datetime(raw_fecha_desp).date() if pd.notna(raw_fecha_desp) else None

        venta_payload = {
            "numero_orden": str(raw_num),
            "rut": clean_rut,
            "nombre_cliente": raw_nombre,
            "direccion_cliente": db_address,
            "comuna": final_comuna,
            "fecha_pedido": fecha_ped,
            "estado": str(order_data.get('estado') or order_data.get('Estado', 'PENDIENTE')),
            "monto_pedido": int(order_data.get('monto_pedido') or order_data.get('Monto Pedido', 0)),
            "fecha_despacho_solicitada": fecha_desp,
            "latitud": lat,
            "longitud": lon
        }
        
        session = get_session()
        try:
            repo = VentaRepository(session)
            repo.upsert(venta_payload)
            print(f"✅ Order {raw_num} persisted (Lat: {lat}, Lon: {lon})")
        finally:
            session.close()
        return True

    def process_dataframe(self, df: pd.DataFrame):
        """Processes a dataframe by iterating and calling process_single_order."""
        for _, row in df.iterrows():
            self.process_single_order(row.to_dict())
        return True
