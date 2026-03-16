"""
Configuración general de la aplicación.
Lee variables de entorno desde un archivo .env.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Base de datos MySQL ---
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "capstone_db"),
}

DATABASE_URL = (
    f"mysql+pymysql://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
    f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"
)

# --- Aplicación ---
APP_NAME = os.getenv("APP_NAME", "Capstone Analytics")
DEBUG = os.getenv("DEBUG", "False").lower() == "true"
