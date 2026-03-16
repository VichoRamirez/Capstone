"""
Controlador: conecta las señales del frontend con los servicios del backend.
"""
from backend.services.data_service import DataService


class DataController:
    """Orquesta la comunicación entre la vista y el servicio de datos."""

    def __init__(self):
        self.service = DataService()

    def cargar_productos(self) -> tuple[list[str], list[list]]:
        """Retorna (headers, rows) listos para mostrar en la tabla."""
        productos = self.service.obtener_productos()
        if not productos:
            return [], []
        headers = ["ID", "Nombre", "Categoría", "Precio"]
        rows = [[p["id"], p["nombre"], p["categoria"], p["precio"]] for p in productos]
        return headers, rows

    def cargar_por_categoria(self, categoria: str) -> tuple[list[str], list[list]]:
        productos = self.service.obtener_productos_por_categoria(categoria)
        if not productos:
            return [], []
        headers = ["ID", "Nombre", "Categoría", "Precio"]
        rows = [[p["id"], p["nombre"], p["categoria"], p["precio"]] for p in productos]
        return headers, rows

    def agregar_producto(self, nombre: str, categoria: str, precio: float) -> dict:
        return self.service.crear_producto(nombre, categoria, precio)

    def eliminar_producto(self, producto_id: int) -> bool:
        return self.service.eliminar_producto(producto_id)
