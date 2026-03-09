"""
Repositorio específico para Producto.
"""
from database.models.producto import Producto
from database.repositories.base_repository import BaseRepository
from sqlalchemy.orm import Session


class ProductoRepository(BaseRepository):

    def __init__(self, session: Session):
        super().__init__(session, Producto)

    def get_by_categoria(self, categoria: str) -> list[Producto]:
        return (
            self.session.query(Producto)
            .filter(Producto.categoria == categoria)
            .all()
        )
