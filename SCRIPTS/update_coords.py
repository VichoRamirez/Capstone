import os
import json
import pymysql
import logging
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Load config
env_path = r"c:\Users\raalv\__Capstone analytics\app\.env"
load_dotenv(env_path)

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "capstone_db"),
}

CACHE_PATH = r"c:\Users\raalv\__Capstone analytics\geocache.json"

def perform_update():
    # 1. Load geocache
    if not os.path.exists(CACHE_PATH):
        logger.error(f"Geocache not found at {CACHE_PATH}")
        return
    
    with open(CACHE_PATH, 'r', encoding='utf-8') as f:
        cache = json.load(f)
    logger.info(f"Loaded {len(cache)} entries from geocache.json")

    # 2. Connect to DB
    try:
        conn = pymysql.connect(
            host=DB_CONFIG["host"],
            port=DB_CONFIG["port"],
            user=DB_CONFIG["user"],
            password=DB_CONFIG["password"],
            database=DB_CONFIG["database"],
            cursorclass=pymysql.cursors.DictCursor
        )
        logger.info("Connected to MySQL database")
    except Exception as e:
        logger.error(f"Failed to connect to DB: {e}")
        return

    try:
        with conn.cursor() as cursor:
            # 3. Fetch orders with missing coordinates
            cursor.execute("SELECT `Número de Orden`, `Dirección cliente`, `Comuna` FROM ventas WHERE Latitud IS NULL OR Longitud IS NULL")
            rows = cursor.fetchall()
            logger.info(f"Found {len(rows)} orders with missing coordinates")

            updates = 0
            skips = 0

            # Fallback patterns to strip
            FALLBACK_PATTERNS = [
                r'^Avenida\s+', r'^Calle\s+', r'^Pasaje\s+', r'^Av\.?\s+', r'^Clle\.?\s+', r'^Pje\.?\s+', r'^Psje\.?\s+', r'^Gral\.?\s+'
            ]

            import re

            for row in rows:
                addr_orig = row["Dirección cliente"]
                order_id = row["Número de Orden"]
                
                # Construct cache key: 
                addr_clean = addr_orig.replace(", Santiago, Chile", "")
                key = f"{addr_clean}|Santiago"
                
                lat, lon = None, None
                if key in cache:
                    lat, lon = cache[key]
                else:
                    # Fallback pass
                    for pattern in FALLBACK_PATTERNS:
                        addr_fb = re.sub(pattern, '', addr_clean, flags=re.IGNORECASE).strip()
                        if addr_fb != addr_clean:
                            key_fb = f"{addr_fb}|Santiago"
                            if key_fb in cache:
                                lat, lon = cache[key_fb]
                                if lat is not None:
                                    logger.info(f"Fallback match found for {order_id}: {key_fb}")
                                    break
                
                if lat is not None and lon is not None:
                    # Update DB
                    cursor.execute(
                        "UPDATE ventas SET Latitud = %s, Longitud = %s WHERE `Número de Orden` = %s",
                        (lat, lon, order_id)
                    )
                    updates += 1
                else:
                    skips += 1

            conn.commit()
            logger.info(f"Successfully updated {updates} orders. Skipped {skips} (not in cache or null).")

    finally:
        conn.close()

if __name__ == "__main__":
    perform_update()
