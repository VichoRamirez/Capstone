from database.models import Detalle
from database.repositories.base_repository import BaseRepository
from sqlalchemy.orm import Session
from typing import List


class DetalleRepository(BaseRepository):
    def __init__(self, session: Session):
        super().__init__(session, Detalle)

    def get_by_numero_orden(self, n_orden: str, user_id: int) -> List[Detalle]:
        return (
            self.session.query(Detalle)
            .filter(Detalle.numero_orden == n_orden, Detalle.id_usuario == user_id)
            .all()
        )

    def add_items(self, items_data: List[dict], user_id: int):
        """Upsert múltiples líneas de detalle. Salta las que ya existen para el usuario."""
        for item in items_data:
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
        self.session.commit()
