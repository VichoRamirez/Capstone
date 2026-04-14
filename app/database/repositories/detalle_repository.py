"""
Repositorio para la tabla `detalle`.

Cada registro representa una línea de ítem dentro de una orden de venta:
un SKU, su cantidad y las dimensiones/pesos unitarios y totales calculados.
La PK compuesta es (numero_orden, SKU, id_usuario).
"""
from database.models import Detalle
from database.repositories.base_repository import BaseRepository
from sqlalchemy.orm import Session
from typing import List


class DetalleRepository(BaseRepository):
    """Repositorio de acceso a datos para el modelo Detalle (tabla `detalle`)."""

    def __init__(self, session: Session):
        super().__init__(session, Detalle)

    def get_by_numero_orden(self, n_orden: str, user_id: int) -> List[Detalle]:
        """Retorna todas las líneas de detalle asociadas a una orden de venta.

        El filtro por id_usuario garantiza el aislamiento multi-tenant.
        """
        return (
            self.session.query(Detalle)
            .filter(Detalle.numero_orden == n_orden, Detalle.id_usuario == user_id)
            .all()
        )

    def add_items(self, items_data: List[dict], user_id: int):
        """Upsert múltiples líneas de detalle. Salta las que ya existen para el usuario.

        Para cada ítem busca el registro por (numero_orden, sku, id_usuario).
        Si existe, actualiza sus campos de dimensiones y pesos.
        Si no existe, crea un registro nuevo.
        Hace un único commit al finalizar todas las filas del lote.
        """
        for item in items_data:
            # Buscar si ya existe la combinación (orden, SKU, usuario)
            existing = (
                self.session.query(Detalle)
                .filter(
                    Detalle.numero_orden == item.get("numero_orden"),
                    Detalle.sku == item.get("sku"),
                    Detalle.id_usuario == user_id,
                )
                .first()
            )
            if existing:
                # Actualizar dimensiones, pesos y cantidades del ítem existente
                existing.descripcion_sku = item.get("descripcion_sku")
                existing.cantidad = item.get("cantidad")
                existing.largo_cm = item.get("largo_cm")
                existing.ancho_cm = item.get("ancho_cm")
                existing.alto_cm = item.get("alto_cm")
                existing.volumen_unitario_m3 = item.get("volumen_unitario_m3")
                existing.peso_unitario_kg = item.get("peso_unitario_kg")
                existing.volumen_total_m3 = item.get("volumen_total_m3")
                existing.peso_total_kg = item.get("peso_total_kg")
            else:
                # Crear nuevo ítem de detalle vinculado al usuario
                obj = Detalle(
                    id_usuario=user_id,
                    numero_orden=item.get("numero_orden"),
                    sku=item.get("sku"),
                    descripcion_sku=item.get("descripcion_sku"),
                    cantidad=item.get("cantidad"),
                    largo_cm=item.get("largo_cm"),
                    ancho_cm=item.get("ancho_cm"),
                    alto_cm=item.get("alto_cm"),
                    volumen_unitario_m3=item.get("volumen_unitario_m3"),
                    peso_unitario_kg=item.get("peso_unitario_kg"),
                    volumen_total_m3=item.get("volumen_total_m3"),
                    peso_total_kg=item.get("peso_total_kg"),
                )
                self.session.add(obj)
        # Commit único al final del lote para minimizar round-trips a la BD
        self.session.commit()
