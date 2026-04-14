"""
Gestión de la conexión a MySQL usando SQLAlchemy.

Se usa un único engine global (patrón singleton implícito) para toda la
aplicación. Las sesiones se crean por operación y se cierran en un bloque
finally para evitar conexiones colgadas.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from config.settings import DATABASE_URL

# pool_pre_ping=True: SQLAlchemy verifica que la conexión sigue viva antes
# de entregarla desde el pool. Evita errores "MySQL server has gone away"
# después de períodos de inactividad.
engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True)

# autocommit=False, autoflush=False: control explícito de transacciones.
# Cada sesión requiere llamar a session.commit() manualmente.
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_session() -> Session:
    """
    Crea y retorna una nueva sesión de base de datos.

    Patrón de uso esperado:
        session = get_session()
        try:
            # ... operaciones ...
        finally:
            session.close()
    """
    return SessionLocal()
