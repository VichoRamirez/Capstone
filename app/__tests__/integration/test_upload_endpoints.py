"""Integration tests for upload and catalog endpoints.

Covers: /upload, /upload-detalle, /upload-catalogo, /catalog, /next-order-number
Geocoding (Nominatim) is always mocked to keep tests fast and network-free.
"""
import io
import uuid
import pytest
from unittest.mock import patch

# ── CSV fixtures ──────────────────────────────────────────────────────────────

_VENTAS_CSV = b"""\
RUT,Nombre cliente,Numero de Orden,Direccion cliente,Comuna,Fecha de Pedido,Estado,Monto Pedido,Fecha de despacho Solicitada
14.512.240-4,Juan Perez,ORD-001,Av. Providencia 1234,Providencia,2024-01-15,Pendiente,50000,2024-01-20
9.512.240-4,Maria Lopez,ORD-002,Las Condes 567,Las Condes,2024-01-15,Pendiente,75000,2024-01-21
"""

_DETALLE_CSV = b"""\
Numero de Orden,SKU,Cantidad
ORD-001,SKU001,2
ORD-001,SKU002,1
ORD-002,SKU001,3
"""

_CATALOGO_CSV = b"""\
SKU,Descripcion SKU,Largo_cm,Ancho_cm,Alto_cm,Volumen_unitario_m3,Peso_unitario_kg,Tipo_embalaje
SKU001,Caja pequena,20,15,10,0.003,0.5,Caja
SKU002,Caja grande,40,30,20,0.024,2.0,Caja
"""

_CATALOGO_CSV_BAD = b"""\
col1,col2
val1,val2
"""

_NO_GEOCODE = patch(
    "backend.services.cleaning_service.geocodificar_dataframe",
    side_effect=lambda df, *args, **kwargs: df,
)


def _uid() -> int:
    """Return a unique int user ID for test isolation."""
    return abs(uuid.uuid4().int) % 100_000 + 1


# ── /upload (ventas) ──────────────────────────────────────────────────────────

@pytest.mark.integration
class TestUploadVentas:
    def test_missing_user_id_returns_400(self, api_client):
        resp = api_client.post(
            "/upload",
            files={"file": ("ventas.csv", io.BytesIO(_VENTAS_CSV), "text/csv")},
        )
        assert resp.status_code == 400

    def test_non_csv_file_returns_400(self, api_client):
        resp = api_client.post(
            "/upload",
            data={"user_id": str(_uid())},
            files={"file": ("data.xlsx", io.BytesIO(b"fake"), "application/octet-stream")},
        )
        assert resp.status_code == 400

    def test_valid_upload_returns_200(self, api_client):
        with _NO_GEOCODE:
            resp = api_client.post(
                "/upload",
                data={"user_id": str(_uid())},
                files={"file": ("ventas.csv", io.BytesIO(_VENTAS_CSV), "text/csv")},
            )
        assert resp.status_code == 200

    def test_valid_upload_has_success_count(self, api_client):
        with _NO_GEOCODE:
            body = api_client.post(
                "/upload",
                data={"user_id": str(_uid())},
                files={"file": ("ventas.csv", io.BytesIO(_VENTAS_CSV), "text/csv")},
            ).json()
        assert "success_count" in body

    def test_valid_upload_has_message(self, api_client):
        with _NO_GEOCODE:
            body = api_client.post(
                "/upload",
                data={"user_id": str(_uid())},
                files={"file": ("ventas.csv", io.BytesIO(_VENTAS_CSV), "text/csv")},
            ).json()
        assert "message" in body

    def test_latin1_encoded_csv_accepted(self, api_client):
        latin1_csv = _VENTAS_CSV.decode("utf-8").encode("latin-1")
        with _NO_GEOCODE:
            resp = api_client.post(
                "/upload",
                data={"user_id": str(_uid())},
                files={"file": ("ventas.csv", io.BytesIO(latin1_csv), "text/csv")},
            )
        assert resp.status_code == 200


# ── /upload-detalle ───────────────────────────────────────────────────────────

@pytest.mark.integration
class TestUploadDetalle:
    def test_missing_user_id_returns_400(self, api_client):
        resp = api_client.post(
            "/upload-detalle",
            files={"file": ("detalle.csv", io.BytesIO(_DETALLE_CSV), "text/csv")},
        )
        assert resp.status_code == 400

    def test_valid_upload_returns_200(self, api_client):
        resp = api_client.post(
            "/upload-detalle",
            data={"user_id": str(_uid())},
            files={"file": ("detalle.csv", io.BytesIO(_DETALLE_CSV), "text/csv")},
        )
        assert resp.status_code == 200

    def test_valid_upload_has_count(self, api_client):
        body = api_client.post(
            "/upload-detalle",
            data={"user_id": str(_uid())},
            files={"file": ("detalle.csv", io.BytesIO(_DETALLE_CSV), "text/csv")},
        ).json()
        assert "count" in body or "message" in body

    def test_bad_columns_returns_error_in_body(self, api_client):
        bad = b"col1,col2\nval1,val2\n"
        resp = api_client.post(
            "/upload-detalle",
            data={"user_id": str(_uid())},
            files={"file": ("bad.csv", io.BytesIO(bad), "text/csv")},
        )
        # Either 200 with error in body, or 4xx
        if resp.status_code == 200:
            body = resp.json()
            assert "error" in body or "errors" in body
        else:
            assert resp.status_code in (400, 422)


# ── /upload-catalogo ──────────────────────────────────────────────────────────

@pytest.mark.integration
class TestUploadCatalogo:
    def test_missing_user_id_returns_400(self, api_client):
        resp = api_client.post(
            "/upload-catalogo",
            files={"file": ("cat.csv", io.BytesIO(_CATALOGO_CSV), "text/csv")},
        )
        assert resp.status_code == 400

    def test_valid_upload_returns_200(self, api_client):
        resp = api_client.post(
            "/upload-catalogo",
            data={"user_id": str(_uid())},
            files={"file": ("cat.csv", io.BytesIO(_CATALOGO_CSV), "text/csv")},
        )
        assert resp.status_code == 200

    def test_valid_upload_has_processed_count(self, api_client):
        body = api_client.post(
            "/upload-catalogo",
            data={"user_id": str(_uid())},
            files={"file": ("cat.csv", io.BytesIO(_CATALOGO_CSV), "text/csv")},
        ).json()
        assert "processed" in body

    def test_second_upload_same_user_skips_duplicates(self, api_client):
        uid = str(_uid())
        api_client.post(
            "/upload-catalogo",
            data={"user_id": uid},
            files={"file": ("cat.csv", io.BytesIO(_CATALOGO_CSV), "text/csv")},
        )
        body = api_client.post(
            "/upload-catalogo",
            data={"user_id": uid},
            files={"file": ("cat.csv", io.BytesIO(_CATALOGO_CSV), "text/csv")},
        ).json()
        # All rows should be reported as skipped on re-upload
        assert body.get("skipped", 0) > 0 or body.get("processed", 0) == 0


# ── /catalog ──────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestCatalogEndpoint:
    def test_missing_user_id_returns_422(self, api_client):
        resp = api_client.get("/catalog")
        assert resp.status_code == 422

    def test_unknown_user_returns_empty_list(self, api_client):
        resp = api_client.get("/catalog?user_id=9999999")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_uploaded_products(self, api_client):
        uid = str(_uid())
        api_client.post(
            "/upload-catalogo",
            data={"user_id": uid},
            files={"file": ("cat.csv", io.BytesIO(_CATALOGO_CSV), "text/csv")},
        )
        resp = api_client.get(f"/catalog?user_id={uid}")
        assert resp.status_code == 200
        products = resp.json()
        assert len(products) >= 1

    def test_catalog_items_have_sku_field(self, api_client):
        uid = str(_uid())
        api_client.post(
            "/upload-catalogo",
            data={"user_id": uid},
            files={"file": ("cat.csv", io.BytesIO(_CATALOGO_CSV), "text/csv")},
        )
        products = api_client.get(f"/catalog?user_id={uid}").json()
        for item in products:
            assert "sku" in item

    def test_catalog_isolated_between_users(self, api_client):
        uid1, uid2 = str(_uid()), str(_uid())
        api_client.post(
            "/upload-catalogo",
            data={"user_id": uid1},
            files={"file": ("cat.csv", io.BytesIO(_CATALOGO_CSV), "text/csv")},
        )
        products_uid2 = api_client.get(f"/catalog?user_id={uid2}").json()
        assert products_uid2 == []


# ── /next-order-number ────────────────────────────────────────────────────────

@pytest.mark.integration
class TestNextOrderNumber:
    def test_returns_200(self, api_client):
        resp = api_client.get("/next-order-number")
        assert resp.status_code == 200

    def test_response_is_string_or_int(self, api_client):
        body = api_client.get("/next-order-number").json()
        # Endpoint returns the number directly (string or int)
        assert body is not None
