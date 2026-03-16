"""
Modelo de ejemplo: Producto.
Reemplazar o extender según las necesidades del proyecto.
"""
from sqlalchemy import Column, Integer, String, Float
from database.models.base import Base


class Producto(Base):
    __tablename__ = "productos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nombre = Column(String(255), nullable=False)
    categoria = Column(String(100))
    precio = Column(Float)

    def __repr__(self):
        return f"<Producto(id={self.id}, nombre='{self.nombre}')>"
