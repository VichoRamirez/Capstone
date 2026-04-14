"""
Modelos ORM de SQLAlchemy para el esquema MySQL del SaaS.

Todas las tablas usan CASCADE en el FK hacia `usuarios.id`, de modo que
al eliminar un usuario se eliminan automáticamente todos sus datos
(ventas, detalle, catálogo). Esto simplifica la gestión multi-tenant.
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Date, DateTime,
    BigInteger, Text, PrimaryKeyConstraint, ForeignKey
)
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


# ── Tabla: usuarios ───────────────────────────────────────────────────────
# Registro de cuentas SaaS. La PK es autoincremental (id).
# tipo_usuario diferencia niveles de servicio: "Free" por defecto.
class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True, autoincrement=True)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)
    username = Column(String(50), nullable=False, unique=True)
    email = Column(String(100), nullable=False, unique=True)
    # Contraseña almacenada como hash bcrypt (nunca en texto plano)
    password = Column(String(100), nullable=False)
    # "Free" | (futuros: "Pro", "Enterprise")
    tipo_usuario = Column(String(255), nullable=False, default="Free")


# ── Tabla: catalogo ───────────────────────────────────────────────────────
# Dimensiones y peso de cada SKU por usuario. PK compuesta (SKU, id_usuario)
# permite que distintos usuarios tengan el mismo SKU con datos diferentes.
class Producto(Base):
    __tablename__ = "catalogo"
    # FK con CASCADE: si el usuario es eliminado, su catálogo también
    id_usuario = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"))
    sku = Column("SKU", String(10))
    descripcion_sku = Column("Descripción SKU", Text)
    # Dimensiones físicas del producto (en cm y m³)
    largo_cm = Column("Largo_cm", Float)
    ancho_cm = Column("Ancho_cm", Float)
    alto_cm = Column("Alto_cm", Float)
    volumen_unitario_m3 = Column("Volumen_unitario_m3", Float)
    peso_unitario_kg = Column("Peso_unitario_kg", Float)
    tipo_embalaje = Column("Tipo_embalaje", Text)

    __table_args__ = (
        # PK compuesta: un SKU puede repetirse entre usuarios
        PrimaryKeyConstraint("SKU", "id_usuario"),
    )


# ── Tabla: ventas ─────────────────────────────────────────────────────────
# Órdenes de venta por usuario. PK compuesta (Número de Orden, id_usuario).
# Estado controla el flujo de despacho:
#   "Pendiente"     → orden aún no despachada (incluida en optimizaciones)
#   "Entregado"     → entrega confirmada
#   "No entregado"  → intento fallido de entrega
class Venta(Base):
    __tablename__ = "ventas"
    id_usuario = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"))
    numero_orden = Column("Número de Orden", String(50))
    rut = Column("RUT", Text)
    nombre_cliente = Column("Nombre cliente", Text)
    direccion_cliente = Column("Dirección cliente", Text)
    comuna = Column("Comuna", Text)
    fecha_pedido = Column("Fecha de Pedido", Date)
    # Estado debe ser uno de: "Pendiente" | "Entregado" | "No entregado"
    estado = Column("Estado", String(15))
    monto_pedido = Column("Monto Pedido", BigInteger)
    fecha_despacho_solicitada = Column("Fecha de despacho Solicitada", Date)
    # Coordenadas geocodificadas (None si la dirección no pudo geocodificarse)
    latitud = Column("Latitud", Float)
    longitud = Column("Longitud", Float)

    __table_args__ = (
        PrimaryKeyConstraint("Número de Orden", "id_usuario"),
    )


# ── Tabla: detalle ────────────────────────────────────────────────────────
# Líneas de detalle de cada orden: qué SKUs contiene y en qué cantidad.
# PK compuesta (Número de Orden, SKU, id_usuario) permite múltiples SKUs
# por orden y aísla el detalle entre usuarios.
# Los campos de volumen y peso total se calculan al momento de la carga
# (Cantidad × volumen_unitario_m3, Cantidad × peso_unitario_kg).
class Detalle(Base):
    __tablename__ = "detalle"
    id_usuario = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"))
    numero_orden = Column("Número de Orden", String(50))
    sku = Column("SKU", String(10))
    descripcion_sku = Column("Descripción SKU", Text)
    cantidad = Column("Cantidad", BigInteger)
    largo_cm = Column("Largo_cm", Float)
    ancho_cm = Column("Ancho_cm", Float)
    alto_cm = Column("Alto_cm", Float)
    volumen_unitario_m3 = Column("Volumen_unitario_m3", Float)
    peso_unitario_kg = Column("Peso_unitario_kg", Float)
    # Totales precalculados: Cantidad × unitario
    volumen_total_m3 = Column("Volumen_total_m3", Float)
    peso_total_kg = Column("Peso_total_kg", Float)

    __table_args__ = (
        PrimaryKeyConstraint("Número de Orden", "SKU", "id_usuario"),
    )
