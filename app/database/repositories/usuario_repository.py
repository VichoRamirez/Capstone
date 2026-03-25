"""
Repositorio específico para Usuario.
"""
from database.models import Usuario
from database.repositories.base_repository import BaseRepository
from sqlalchemy.orm import Session


class UsuarioRepository(BaseRepository):

    def __init__(self, session: Session):
        super().__init__(session, Usuario)

    def get_by_username(self, username: str) -> Usuario | None:
        return (
            self.session.query(Usuario)
            .filter(Usuario.username == username)
            .first()
        )

    def get_by_email(self, email: str) -> Usuario | None:
        return (
            self.session.query(Usuario)
            .filter(Usuario.email == email)
            .first()
        )

    def create_user(self, username: str, email: str, hashed_password: str) -> Usuario:
        """Crea un nuevo usuario con la contraseña ya hasheada."""
        user = Usuario(
            username=username,
            email=email,
            password=hashed_password,
        )
        self.session.add(user)
        self.session.commit()
        self.session.refresh(user)
        return user
