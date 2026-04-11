from database.models import Producto
from database.repositories.base_repository import BaseRepository
from sqlalchemy.orm import Session
from typing import List


class ProductoRepository(BaseRepository):
    def __init__(self, session: Session):
        super().__init__(session, Producto)

    def get_all_by_user(self, user_id: int) -> list:
        return (
            self.session.query(Producto)
            .filter(Producto.id_usuario == user_id)
            .all()
        )

    def get_by_sku_and_user(self, sku: str, user_id: int):
        return (
            self.session.query(Producto)
            .filter(Producto.sku == sku, Producto.id_usuario == user_id)
            .first()
        )

    def upsert_for_user(self, rows: List[dict], user_id: int) -> dict:
        """
        Inserta los productos que no existen para el usuario.
        Salta los que ya tienen el mismo (SKU, id_usuario).
        Retorna conteos de insertados, saltados y errores por fila.
        """
        inserted = 0
        skipped = 0
        errors: list[dict] = []
        for row in rows:
            try:
                sku = str(row.get("sku") or row.get("SKU") or "").strip()
                if not sku:
                    skipped += 1
                    continue
                existing = self.get_by_sku_and_user(sku, user_id)
                if existing:
                    skipped += 1
                    continue
                obj = Producto(
                    id_usuario=user_id,
                    sku=sku,
                    descripcion_sku=row.get("descripcion_sku") or row.get("Descripción SKU") or row.get("Descripcion SKU"),
                    largo_cm=row.get("largo_cm") or row.get("Largo_cm"),
                    ancho_cm=row.get("ancho_cm") or row.get("Ancho_cm"),
                    alto_cm=row.get("alto_cm") or row.get("Alto_cm"),
                    volumen_unitario_m3=row.get("volumen_unitario_m3") or row.get("Volumen_unitario_m3"),
                    peso_unitario_kg=row.get("peso_unitario_kg") or row.get("Peso_unitario_kg"),
                    tipo_embalaje=row.get("tipo_embalaje") or row.get("Tipo_embalaje"),
                )
                self.session.add(obj)
                self.session.commit()
                inserted += 1
            except Exception as e:
                self.session.rollback()
                errors.append({"sku": row.get("SKU", ""), "error": str(e)})
        return {"inserted": inserted, "skipped": skipped, "errors": errors}
