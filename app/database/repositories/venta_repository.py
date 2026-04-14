"""
Repositorio para la tabla `ventas`.

Extiende BaseRepository con operaciones específicas de órdenes de venta:
búsqueda por número de orden, upsert completo de una venta y cálculo del
próximo número de orden correlativo.
"""
import re
from database.models import Venta
from database.repositories.base_repository import BaseRepository
from sqlalchemy.orm import Session
from sqlalchemy import func


class VentaRepository(BaseRepository):
    """Repositorio de acceso a datos para el modelo Venta (tabla `ventas`)."""

    def __init__(self, session: Session):
        super().__init__(session, Venta)

    def get_by_numero_orden(self, n_orden: str, user_id: int) -> Venta:
        """Retorna la venta que coincide con el número de orden y el usuario dado.

        El filtro por id_usuario garantiza el aislamiento multi-tenant.
        Retorna None si no existe.
        """
        return (
            self.session.query(Venta)
            .filter(Venta.numero_orden == n_orden, Venta.id_usuario == user_id)
            .first()
        )

    def upsert(self, data: dict, user_id: int):
        """Inserta o actualiza una venta para el usuario indicado.

        Si ya existe una venta con el mismo numero_orden e id_usuario, actualiza
        todos sus campos. Si no existe, crea un registro nuevo y lo agrega a la sesión.
        Hace commit al final en ambos casos.
        """
        obj = self.get_by_numero_orden(data.get("numero_orden"), user_id)
        if obj:
            # Actualizar todos los campos de la venta existente
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
            # Crear nueva venta con el id_usuario para mantener el aislamiento por tenant
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
        """Calcula el siguiente número de orden correlativo global (sin filtro por usuario).

        Obtiene el máximo numero_orden almacenado, extrae su parte numérica
        mediante regex y retorna el entero siguiente como string.
        Si no hay órdenes previas, retorna "1".
        """
        # Obtener el valor máximo actual del campo numero_orden en toda la tabla
        max_orden = self.session.query(func.max(Venta.numero_orden)).scalar()
        if not max_orden:
            return "1"
        # Extraer la parte numérica del string (ej. "ORD-042" → 42)
        numeric_part = re.search(r'(\d+)', str(max_orden))
        max_num = int(numeric_part.group(1)) if numeric_part else 0
        return str(max_num + 1)
