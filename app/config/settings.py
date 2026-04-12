"""
Configuración general de la aplicación.
Lee variables de entorno desde un archivo .env.
"""
import os
from urllib.parse import quote_plus
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# --- Base de datos MySQL ---
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "capstone_db"),
}

# Encode password to handle special characters like '@'
encoded_password = quote_plus(DB_CONFIG["password"])

DATABASE_URL = (
    f"mysql+pymysql://{DB_CONFIG['user']}:{encoded_password}"
    f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"
)

# --- Aplicación ---
APP_NAME = os.getenv("APP_NAME", "Capstone Analytics")
DEBUG = os.getenv("DEBUG", "False").lower() == "true"

# --- Backend API ---
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", 8000))

# --- OSRM (codex routing pipeline) ---
OSRM_LOCAL_BASE_URL = os.getenv("OSRM_LOCAL_BASE_URL", "http://127.0.0.1:5010")
OSRM_RESERVED_PORTS = os.getenv("OSRM_RESERVED_PORTS", "5000,5001")
