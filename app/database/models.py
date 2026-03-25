from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Date, DateTime, BigInteger, Text, PrimaryKeyConstraint
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True, autoincrement=True)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)
    username = Column(String(50), nullable=False, unique=True)
    email = Column(String(100), nullable=False, unique=True)
    password = Column(String(100), nullable=False)
    tipo_usuario = Column(String(255), nullable=False, default="Free")

class Producto(Base):
    __tablename__ = "catalogo"
    id_usuario = Column(Integer)
    sku = Column("SKU", String(10), primary_key=True)
    descripcion_sku = Column("Descripción SKU", Text)
    largo_cm = Column("Largo_cm", Float)
    ancho_cm = Column("Ancho_cm", Float)
    alto_cm = Column("Alto_cm", Float)
    volumen_unitario_m3 = Column("Volumen_unitario_m3", Float)
    peso_unitario_kg = Column("Peso_unitario_kg", Float)
    tipo_embalaje = Column("Tipo_embalaje", Text)

class Venta(Base):
    __tablename__ = "ventas"
    id_usuario = Column(Integer)
    numero_orden = Column("Número de Orden", String(50), primary_key=True)
    rut = Column("RUT", Text)
    nombre_cliente = Column("Nombre cliente", Text)
    direccion_cliente = Column("Dirección cliente", Text)
    comuna = Column("Comuna", Text)
    fecha_pedido = Column("Fecha de Pedido", Date)
    estado = Column("Estado", String(15))
    monto_pedido = Column("Monto Pedido", BigInteger)
    fecha_despacho_solicitada = Column("Fecha de despacho Solicitada", Date)
    latitud = Column("Latitud", Float)
    longitud = Column("Longitud", Float)

class Detalle(Base):
    __tablename__ = "detalle"
    id_usuario = Column(Integer)
    numero_orden = Column("Número de Orden", String(50), primary_key=True)
    sku = Column("SKU", String(10), primary_key=True)
    descripcion_sku = Column("Descripción SKU", Text)
    cantidad = Column("Cantidad", BigInteger)
    largo_cm = Column("Largo_cm", Float)
    ancho_cm = Column("Ancho_cm", Float)
    alto_cm = Column("Alto_cm", Float)
    volumen_unitario_m3 = Column("Volumen_unitario_m3", Float)
    peso_unitario_kg = Column("Peso_unitario_kg", Float)
    volumen_total_m3 = Column("Volumen_total_m3", Float)
    peso_total_kg = Column("Peso_total_kg", Float)
    
    __table_args__ = (
        PrimaryKeyConstraint("Número de Orden", "SKU"),
    )
