from database.models import Venta
from database.repositories.base_repository import BaseRepository
from sqlalchemy.orm import Session

class VentaRepository(BaseRepository):
    def __init__(self, session: Session):
        super().__init__(session, Venta)

    def get_by_numero_orden(self, n_orden: str) -> Venta:
        return self.session.query(Venta).filter(Venta.numero_orden == n_orden).first()

    def upsert(self, data: dict):
        obj = self.get_by_numero_orden(data.get("numero_orden"))
        if obj:
            # Update fields
            # Note: manually mapping columns to dict keys if they differ
            obj.rut = data.get("rut")
            obj.nombre_cliente = data.get("nombre_cliente")
            obj.direccion_cliente = data.get("direccion_cliente")
            obj.comuna = data.get("comuna")
            obj.fecha_pedido = data.get("fecha_pedido")
            obj.estado = data.get("estado")
            obj.monto_pedido = data.get("monto_pedido")
            obj.fecha_despacho_solicitada = data.get("fecha_despacho_solicitada")
            obj.latitud = data.get("latitud")
            obj.longitud = data.get("longitud")
        else:
            obj = Venta(
                numero_orden=data.get("numero_orden"),
                rut=data.get("rut"),
                nombre_cliente=data.get("nombre_cliente"),
                direccion_cliente=data.get("direccion_cliente"),
                comuna=data.get("comuna"),
                fecha_pedido=data.get("fecha_pedido"),
                estado=data.get("estado"),
                monto_pedido=data.get("monto_pedido"),
                fecha_despacho_solicitada=data.get("fecha_despacho_solicitada"),
                latitud=data.get("latitud"),
                longitud=data.get("longitud")
            )
            self.session.add(obj)
        self.session.commit()
        return obj

    def get_next_order_number(self) -> str:
        """Finds the highest existing order number and increments it."""
        all_orders = self.session.query(Venta.numero_orden).all()
        max_num = 0
        for (order_str,) in all_orders:
            try:
                # Try to extract numbers from "ORD-123" or "123"
                numeric_part = re.search(r'(\d+)', order_str)
                if numeric_part:
                    num = int(numeric_part.group(1))
                    if num > max_num:
                        max_num = num
            except:
                continue
        
        return str(max_num + 1)

import re # Needed for the regex in get_next_order_number
