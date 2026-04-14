"""
Configuración general de la aplicación Capstone Analytics.

Lee las variables de entorno desde el archivo app/config/.env mediante python-dotenv
y expone las constantes de configuración utilizadas por el backend:
conexión a MySQL, servidor FastAPI, y URL base de OSRM para el cálculo de rutas.
"""
import os
from urllib.parse import quote_plus
from dotenv import load_dotenv

# Carga las variables del archivo .env ubicado en el mismo directorio que este módulo
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# --- Base de datos MySQL ---
# Diccionario con los parámetros de conexión individuales; se usa también para
# construir DATABASE_URL y para validar la conectividad en tiempo de arranque
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),       # Host del servidor MySQL
    "port": int(os.getenv("DB_PORT", 3306)),          # Puerto TCP de MySQL (default 3306)
    "user": os.getenv("DB_USER", "root"),             # Usuario de la base de datos
    "password": os.getenv("DB_PASSWORD", ""),         # Contraseña (puede contener caracteres especiales)
    "database": os.getenv("DB_NAME", "capstone_db"),  # Nombre del esquema/base de datos
}

# La contraseña se codifica en URL-encoding para manejar caracteres especiales como '@' o '#'
encoded_password = quote_plus(DB_CONFIG["password"])

# URL de conexión para SQLAlchemy usando el driver pymysql (compatible con MySQL 8 + caching_sha2_password)
DATABASE_URL = (
    f"mysql+pymysql://{DB_CONFIG['user']}:{encoded_password}"
    f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"
)

# --- Aplicación ---
APP_NAME = os.getenv("APP_NAME", "Capstone Analytics")  # Nombre mostrado en la UI y logs
DEBUG = os.getenv("DEBUG", "False").lower() == "true"    # Activa logs verbosos y recarga automática

# --- Backend API ---
API_HOST = os.getenv("API_HOST", "0.0.0.0")      # Interfaz de red donde escucha FastAPI
API_PORT = int(os.getenv("API_PORT", 8000))        # Puerto HTTP del servidor FastAPI

# --- OSRM (motor de ruteo local) ---
# OSRM se usa para calcular matrices de distancia/tiempo reales en lugar de Haversine.
# Si el servidor OSRM local no está disponible, el optimizador cae a Haversine como fallback.
OSRM_LOCAL_BASE_URL = os.getenv("OSRM_LOCAL_BASE_URL", "http://127.0.0.1:5010")
# Puertos reservados para instancias OSRM externas o de prueba (separados por coma)
OSRM_RESERVED_PORTS = os.getenv("OSRM_RESERVED_PORTS", "5000,5001")
