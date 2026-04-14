"""
Repositorio base con operaciones CRUD genéricas.

Todos los repositorios específicos heredan de esta clase e inyectan
la sesión de BD. Esto facilita el testing (se puede pasar una sesión
de prueba) y mantiene la lógica de BD separada de los servicios.
"""

from sqlalchemy.orm import Session
from database.models import Base


class BaseRepository:
    """
    Repositorio genérico para operaciones CRUD sobre cualquier modelo ORM.

    Parámetros:
        session: Sesión SQLAlchemy abierta. El caller es responsable de
                 abrirla y cerrarla (patrón session-per-request).
        model:   Clase ORM (subclase de Base) que este repositorio maneja.
    """

    def __init__(self, session: Session, model: type[Base]):
        self.session = session
        self.model = model

    def get_all(self) -> list:
        """Retorna todos los registros del modelo."""
        return self.session.query(self.model).all()

    def get_by_id(self, record_id: int):
        """Busca un registro por su PK entera. Retorna None si no existe."""
        return self.session.query(self.model).get(record_id)

    def create(self, **kwargs):
        """Crea un nuevo registro, hace commit y refresca la instancia."""
        instance = self.model(**kwargs)
        self.session.add(instance)
        self.session.commit()
        self.session.refresh(instance)
        return instance

    def update(self, record_id: int, **kwargs):
        """
        Actualiza los campos de un registro existente.
        Retorna None si el registro no existe.
        """
        instance = self.get_by_id(record_id)
        if instance is None:
            return None
        for key, value in kwargs.items():
            setattr(instance, key, value)
        self.session.commit()
        self.session.refresh(instance)
        return instance

    def delete(self, record_id: int) -> bool:
        """
        Elimina un registro por su PK entera.
        Retorna True si fue eliminado, False si no existía.
        """
        instance = self.get_by_id(record_id)
        if instance is None:
            return False
        self.session.delete(instance)
        self.session.commit()
        return True
