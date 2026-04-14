"""
Repositorio para la tabla `catalogo`.

Almacena las dimensiones físicas y peso unitario de cada SKU por usuario.
La PK compuesta es (SKU, id_usuario), lo que permite que distintos tenants
tengan catálogos independientes con los mismos códigos de producto.
"""
from database.models import Producto
from database.repositories.base_repository import BaseRepository
from sqlalchemy.orm import Session
from typing import List


class ProductoRepository(BaseRepository):
    """Repositorio de acceso a datos para el modelo Producto (tabla `catalogo`)."""

    def __init__(self, session: Session):
        super().__init__(session, Producto)

    def get_all_by_user(self, user_id: int) -> list:
        """Retorna todos los productos del catálogo del usuario indicado."""
        return (
            self.session.query(Producto)
            .filter(Producto.id_usuario == user_id)
            .all()
        )

    def get_by_sku_and_user(self, sku: str, user_id: int):
        """Busca un producto por SKU dentro del catálogo del usuario.

        Retorna None si el SKU no existe para ese tenant.
        """
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
                # Normalizar el SKU tolerando distintas convenciones de nombre de columna
                sku = str(row.get("sku") or row.get("SKU") or "").strip()
                if not sku:
                    # Fila sin SKU válido: no se puede insertar
                    skipped += 1
                    continue
                existing = self.get_by_sku_and_user(sku, user_id)
                if existing:
                    # El SKU ya está en el catálogo del usuario; no se sobreescribe
                    skipped += 1
                    continue
                # Aceptar tanto nombres de columna en minúscula como con mayúsculas/tildes
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
                # Commit individual por fila para que un error no revierta todo el lote
                self.session.commit()
                inserted += 1
            except Exception as e:
                # Revertir solo la fila fallida y registrar el error para el reporte
                self.session.rollback()
                errors.append({"sku": row.get("SKU", ""), "error": str(e)})
        return {"inserted": inserted, "skipped": skipped, "errors": errors}
