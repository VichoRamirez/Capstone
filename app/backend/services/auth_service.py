"""
Servicio de autenticación.
Gestiona el registro e inicio de sesión de usuarios con contraseñas hasheadas (bcrypt + salt).
"""
import bcrypt
from datetime import datetime
from database.connection import get_session
from database.repositories.usuario_repository import UsuarioRepository


def hash_password(plain_password: str) -> str:
    """Genera un hash bcrypt con salt automático."""
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(plain_password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica una contraseña en texto plano contra el hash almacenado."""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )


def register_user(username: str, email: str, password: str) -> dict:
    """
    Registra un nuevo usuario.
    Retorna dict con info del usuario o un error.
    """
    if not username or not email or not password:
        return {"error": "Todos los campos son obligatorios."}

    session = get_session()
    try:
        repo = UsuarioRepository(session)

        # Verificar si el username ya existe
        if repo.get_by_username(username):
            return {"error": f"El nombre de usuario '{username}' ya está registrado."}

        # Verificar si el email ya existe
        if repo.get_by_email(email):
            return {"error": f"El correo '{email}' ya está registrado."}

        # Hashear la contraseña
        hashed_pw = hash_password(password)

        # Crear usuario
        user = repo.create_user(
            username=username,
            email=email,
            hashed_password=hashed_pw,
        )

        return {
            "user_id": user.id,
            "username": user.username,
            "email": user.email,
            "tipo_usuario": user.tipo_usuario,
            "message": "Cuenta creada exitosamente.",
        }
    except Exception as e:
        return {"error": f"Error al crear la cuenta: {str(e)}"}
    finally:
        session.close()


def login_user(identifier: str, password: str) -> dict:
    """
    Inicia sesión por username o email.
    Retorna dict con info del usuario o un error.
    """
    if not identifier or not password:
        return {"error": "Debe ingresar usuario/correo y contraseña."}

    session = get_session()
    try:
        repo = UsuarioRepository(session)

        # Buscar por username primero, luego por email
        user = repo.get_by_username(identifier)
        if not user:
            user = repo.get_by_email(identifier)

        # Si no existe el usuario o la contraseña no coincide, se rechaza
        if not user or not verify_password(password, user.password):
            return {"error": "Credenciales incorrectas."}

        return {
            "user_id": user.id,
            "username": user.username,
            "email": user.email,
            "tipo_usuario": user.tipo_usuario,
            "message": "Inicio de sesión exitoso.",
        }
    except Exception as e:
        return {"error": f"Error al iniciar sesión: {str(e)}"}
    finally:
        session.close()

def reset_password(username: str, email: str, new_password: str) -> dict:
    """
    Restaura la contraseña de un usuario mediante su nombre de usuario y correo.
    Retorna un dict con el resultado de la operación.
    """
    if not username or not email or not new_password:
        return {"error": "Todos los campos son obligatorios."}

    session = get_session()
    try:
        repo = UsuarioRepository(session)
        user = repo.get_by_username(username)

        # Validamos que exista y que el email coincida
        if not user or user.email != email:
            return {"error": "Los datos proporcionados no coinciden con ningún registro."}
        
        hashed_pw = hash_password(new_password)
        repo.update_password(user, hashed_pw)

        return {
            "message": "Contraseña actualizada exitosamente."
        }
    except Exception as e:
        return {"error": f"Error al restablecer la contraseña: {str(e)}"}
    finally:
        session.close()
