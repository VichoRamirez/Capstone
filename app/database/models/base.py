"""
Modelo base de SQLAlchemy para todos los modelos del proyecto.
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Clase base para todos los modelos ORM."""
    pass
