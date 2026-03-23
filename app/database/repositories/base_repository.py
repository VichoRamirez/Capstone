"""
Repositorio base con operaciones CRUD genéricas.
"""
from sqlalchemy.orm import Session
from database.models import Base


class BaseRepository:
    """Repositorio genérico para operaciones CRUD."""

    def __init__(self, session: Session, model: type[Base]):
        self.session = session
        self.model = model

    def get_all(self) -> list:
        return self.session.query(self.model).all()

    def get_by_id(self, record_id: int):
        return self.session.query(self.model).get(record_id)

    def create(self, **kwargs):
        instance = self.model(**kwargs)
        self.session.add(instance)
        self.session.commit()
        self.session.refresh(instance)
        return instance

    def update(self, record_id: int, **kwargs):
        instance = self.get_by_id(record_id)
        if instance is None:
            return None
        for key, value in kwargs.items():
            setattr(instance, key, value)
        self.session.commit()
        self.session.refresh(instance)
        return instance

    def delete(self, record_id: int) -> bool:
        instance = self.get_by_id(record_id)
        if instance is None:
            return False
        self.session.delete(instance)
        self.session.commit()
        return True
