"""
Repositorio de operaciones sobre la tabla `usuarios`.

Extiende BaseRepository con búsquedas por username y email,
y operaciones específicas de autenticación (crear usuario, actualizar contraseña).
"""

from database.models import Usuario
from database.repositories.base_repository import BaseRepository
from sqlalchemy.orm import Session


class UsuarioRepository(BaseRepository):

    def __init__(self, session: Session):
        super().__init__(session, Usuario)

    def get_by_username(self, username: str) -> Usuario | None:
        """Busca un usuario por nombre de usuario (case-sensitive). Retorna None si no existe."""
        return (
            self.session.query(Usuario)
            .filter(Usuario.username == username)
            .first()
        )

    def get_by_email(self, email: str) -> Usuario | None:
        """Busca un usuario por correo electrónico. Retorna None si no existe."""
        return (
            self.session.query(Usuario)
            .filter(Usuario.email == email)
            .first()
        )

    def create_user(self, username: str, email: str, hashed_password: str) -> Usuario:
        """
        Crea un nuevo usuario con la contraseña ya hasheada (bcrypt).
        El tipo_usuario queda en "Free" por defecto (definido en el modelo).
        """
        user = Usuario(
            username=username,
            email=email,
            password=hashed_password,
        )
        self.session.add(user)
        self.session.commit()
        self.session.refresh(user)
        return user

    def update_password(self, user: Usuario, hashed_password: str) -> Usuario:
        """
        Reemplaza la contraseña almacenada por el nuevo hash bcrypt.
        El caller es responsable de validar identidad antes de llamar esto.
        """
        user.password = hashed_password
        self.session.commit()
        self.session.refresh(user)
        return user
