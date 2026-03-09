"""
Servicio de datos: contiene la lógica de negocio.
El frontend NUNCA debe acceder a la BD directamente; siempre pasa por aquí.
"""
from database.connection import get_session
from database.repositories.producto_repository import ProductoRepository


class DataService:
    """Capa de lógica de negocio para datos de productos."""

    def obtener_productos(self) -> list[dict]:
        session = get_session()
        try:
            repo = ProductoRepository(session)
            productos = repo.get_all()
            return [
                {
                    "id": p.id,
                    "nombre": p.nombre,
                    "categoria": p.categoria,
                    "precio": p.precio,
                }
                for p in productos
            ]
        finally:
            session.close()

    def obtener_productos_por_categoria(self, categoria: str) -> list[dict]:
        session = get_session()
        try:
            repo = ProductoRepository(session)
            productos = repo.get_by_categoria(categoria)
            return [
                {
                    "id": p.id,
                    "nombre": p.nombre,
                    "categoria": p.categoria,
                    "precio": p.precio,
                }
                for p in productos
            ]
        finally:
            session.close()

    def crear_producto(self, nombre: str, categoria: str, precio: float) -> dict:
        session = get_session()
        try:
            repo = ProductoRepository(session)
            p = repo.create(nombre=nombre, categoria=categoria, precio=precio)
            return {"id": p.id, "nombre": p.nombre, "categoria": p.categoria, "precio": p.precio}
        finally:
            session.close()

    def eliminar_producto(self, producto_id: int) -> bool:
        session = get_session()
        try:
            repo = ProductoRepository(session)
            return repo.delete(producto_id)
        finally:
            session.close()
