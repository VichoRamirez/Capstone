import re
from database.models import Venta
from database.repositories.base_repository import BaseRepository
from sqlalchemy.orm import Session
from sqlalchemy import func


class VentaRepository(BaseRepository):
    def __init__(self, session: Session):
        super().__init__(session, Venta)

    def get_by_numero_orden(self, n_orden: str, user_id: int) -> Venta:
        return (
            self.session.query(Venta)
            .filter(Venta.numero_orden == n_orden, Venta.id_usuario == user_id)
            .first()
        )

    def upsert(self, data: dict, user_id: int):
        obj = self.get_by_numero_orden(data.get("numero_orden"), user_id)
        if obj:
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
                id_usuario=user_id,
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
                longitud=data.get("longitud"),
            )
            self.session.add(obj)
        self.session.commit()
        return obj

    def get_next_order_number(self) -> str:
        max_orden = self.session.query(func.max(Venta.numero_orden)).scalar()
        if not max_orden:
            return "1"
        numeric_part = re.search(r'(\d+)', str(max_orden))
        max_num = int(numeric_part.group(1)) if numeric_part else 0
        return str(max_num + 1)
