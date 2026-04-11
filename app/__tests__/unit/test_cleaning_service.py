"""Unit tests for backend.services.cleaning_service.

No DB or network calls required — geocoding is mocked where needed.
"""
import pytest
import pandas as pd

from backend.services.cleaning_service import (
    estandarizar_rut,
    estandarizar_direccion_y_comuna,
    clean_ventas,
    clean_detalle,
)


# ── estandarizar_rut ──────────────────────────────────────────────────────────

class TestEstandarizarRut:
    def test_9_digit_with_dash(self):
        assert estandarizar_rut("14512240-4") == "14.512.240-4"

    def test_9_digit_no_dash(self):
        assert estandarizar_rut("145122404") == "14.512.240-4"

    def test_already_formatted(self):
        assert estandarizar_rut("14.512.240-4") == "14.512.240-4"

    def test_8_digit_rut(self):
        # Single-digit prefix: 9.512.240-4
        assert estandarizar_rut("9512240-4") == "9.512.240-4"

    def test_rut_with_spaces(self):
        assert estandarizar_rut("14 512 240-4") == "14.512.240-4"

    def test_k_digit_verifier(self):
        result = estandarizar_rut("5126663-k")
        assert result.endswith("-k")

    def test_short_rut_returned_as_is(self):
        # Ambiguous length: returned unchanged
        result = estandarizar_rut("12345")
        assert result == "12345"

    def test_uppercase_k_verifier(self):
        result = estandarizar_rut("5126663-K")
        assert result.endswith("-K")


# ── estandarizar_direccion_y_comuna ───────────────────────────────────────────

class TestEstandarizarDireccionYComuna:
    def test_returns_two_element_tuple(self):
        result = estandarizar_direccion_y_comuna("Av. Providencia 1234")
        assert isinstance(result, tuple) and len(result) == 2

    def test_detects_las_condes(self):
        _, comuna = estandarizar_direccion_y_comuna("Calle Falsa 123, Las Condes")
        assert comuna == "Las Condes"

    def test_detects_nunoa(self):
        _, comuna = estandarizar_direccion_y_comuna("Av. Irarrázaval 555, Ñuñoa")
        assert comuna == "Ñuñoa"

    def test_detects_providencia(self):
        _, comuna = estandarizar_direccion_y_comuna("Av. Providencia 1234, Providencia")
        assert comuna == "Providencia"

    def test_no_recognizable_comuna_returns_none(self):
        _, comuna = estandarizar_direccion_y_comuna("Calle Desconocida 999")
        assert comuna is None

    def test_address_string_returned(self):
        addr, _ = estandarizar_direccion_y_comuna("Av. Libertad 100, Maipú")
        assert isinstance(addr, str) and addr != ""


# ── CSV fixtures ──────────────────────────────────────────────────────────────

_VENTAS_VALID = """\
RUT,Nombre cliente,Número de Orden,Dirección cliente,Comuna,Fecha de Pedido,Estado,Monto Pedido,Fecha de despacho Solicitada
14.512.240-4,Juan Perez,ORD-001,Av. Providencia 1234,Providencia,2024-01-15,Pendiente,50000,2024-01-20
9.512.240-4,Maria Lopez,ORD-002,Calle Las Condes 567,Las Condes,2024-01-15,Pendiente,75000,2024-01-21
"""

_VENTAS_BAD_COLUMNS = """\
col1,col2
val1,val2
"""

_DETALLE_VALID = """\
Número de Orden,SKU,Cantidad
ORD-001,SKU001,2
ORD-001,SKU002,1
ORD-002,SKU001,3
"""

_DETALLE_DUPLICATES = """\
Número de Orden,SKU,Cantidad
ORD-001,SKU001,2
ORD-001,SKU001,3
"""

_DETALLE_MISSING_COLS = """\
col1,col2
a,b
"""


# ── clean_ventas ──────────────────────────────────────────────────────────────

class TestCleanVentas:
    def test_returns_dataframe_and_list(self):
        df, errors = clean_ventas(_VENTAS_VALID)
        assert isinstance(df, pd.DataFrame)
        assert isinstance(errors, list)

    def test_preserves_row_count(self):
        df, _ = clean_ventas(_VENTAS_VALID)
        assert len(df) == 2

    def test_rut_contains_dots_and_dash(self):
        df, _ = clean_ventas(_VENTAS_VALID)
        for rut in df["RUT"]:
            assert "." in str(rut) and "-" in str(rut)

    def test_no_errors_on_clean_data(self):
        _, errors = clean_ventas(_VENTAS_VALID)
        assert errors == []

    def test_address_column_preserved(self):
        df, _ = clean_ventas(_VENTAS_VALID)
        assert "Dirección cliente" in df.columns

    def test_comuna_column_populated(self):
        df, _ = clean_ventas(_VENTAS_VALID)
        # At least one commune should be filled
        assert df["Comuna"].notna().any()


# ── clean_detalle ─────────────────────────────────────────────────────────────

class TestCleanDetalle:
    def test_returns_dataframe_and_list(self):
        df, errors = clean_detalle(_DETALLE_VALID)
        assert isinstance(df, pd.DataFrame)
        assert isinstance(errors, list)

    def test_three_distinct_pairs(self):
        df, _ = clean_detalle(_DETALLE_VALID)
        assert len(df) == 3

    def test_deduplication_sums_cantidad(self):
        df, _ = clean_detalle(_DETALLE_DUPLICATES)
        assert len(df) == 1
        assert int(df.iloc[0]["Cantidad"]) == 5  # 2 + 3

    def test_missing_required_columns_error(self):
        _, errors = clean_detalle(_DETALLE_MISSING_COLS)
        assert len(errors) > 0
        assert any("faltantes" in str(e).lower() or "Columnas" in str(e) for e in errors)

    def test_output_contains_numero_orden(self):
        df, _ = clean_detalle(_DETALLE_VALID)
        assert "Número de Orden" in df.columns

    def test_output_contains_sku(self):
        df, _ = clean_detalle(_DETALLE_VALID)
        assert "SKU" in df.columns

    def test_output_contains_cantidad(self):
        df, _ = clean_detalle(_DETALLE_VALID)
        assert "Cantidad" in df.columns

    def test_all_cantidades_positive(self):
        df, _ = clean_detalle(_DETALLE_VALID)
        assert (df["Cantidad"] > 0).all()
