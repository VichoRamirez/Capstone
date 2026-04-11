"""
Locust stress-test file for the Capstone Analytics API.

Usage (from the app/ directory):
    locust -f __tests__/stress/locustfile.py --host=http://127.0.0.1:8000

Headless (CI) — results saved to __tests__/results/:
    locust -f __tests__/stress/locustfile.py --host=http://127.0.0.1:8000 \
           --users 50 --spawn-rate 5 --run-time 60s --headless \
           --csv=__tests__/results/locust \
           --html=__tests__/results/locust_report.html

Scenarios:
  - AnonymousUser   : health + fuel-price polling (no auth required)
  - AuthenticatedUser: register → login → catalog → upload cycle
  - OsrmHeavyUser   : repeated /validate-depot calls (Nominatim probe)
"""
import uuid
import io
import random

from locust import HttpUser, TaskSet, task, between, events

# ── shared small CSV payloads ─────────────────────────────────────────────────

_VENTAS_CSV = """\
RUT,Nombre cliente,Numero de Orden,Direccion cliente,Comuna,Fecha de Pedido,Estado,Monto Pedido,Fecha de despacho Solicitada
14.512.240-4,Juan Perez,{order},Av. Providencia 1234,Providencia,2024-01-15,Pendiente,50000,2024-01-20
""".strip()

_DETALLE_CSV = """\
Numero de Orden,SKU,Cantidad
{order},SKU001,2
""".strip()

_CATALOGO_CSV = """\
SKU,Descripcion SKU,Largo_cm,Ancho_cm,Alto_cm,Volumen_unitario_m3,Peso_unitario_kg,Tipo_embalaje
{sku},Producto de prueba,20,15,10,0.003,0.5,Caja
""".strip()


def _uid():
    return abs(uuid.uuid4().int) % 100_000 + 1


def _order_id():
    return f"ORD-{uuid.uuid4().hex[:6].upper()}"


def _sku():
    return f"SK{uuid.uuid4().hex[:4].upper()}"


# ── Anonymous user tasks ──────────────────────────────────────────────────────

class AnonymousTasks(TaskSet):
    @task(5)
    def health(self):
        self.client.get("/health", name="/health")

    @task(3)
    def fuel_diesel(self):
        self.client.get("/fuel/diesel-clp", name="/fuel/diesel-clp")

    @task(2)
    def fuel_gasoline(self):
        fuel = random.choice(["diesel", "gasoline_93", "gasoline_95", "gasoline_97"])
        self.client.get(f"/fuel/prices-clp?fuel_type={fuel}", name="/fuel/prices-clp")

    @task(1)
    def validate_depot(self):
        address = random.choice([
            "Av Libertador Bernardo O'Higgins 1234 Santiago",
            "Av Providencia 1000 Providencia",
            "Av Las Condes 10000 Las Condes",
        ])
        self.client.get(
            f"/validate-depot?address={address}",
            name="/validate-depot",
        )

    @task(1)
    def progress(self):
        self.client.get("/progress", name="/progress")


class AnonymousUser(HttpUser):
    tasks = [AnonymousTasks]
    wait_time = between(0.5, 2.0)


# ── Authenticated user tasks ──────────────────────────────────────────────────

class AuthCycle(TaskSet):
    """Register → login → catalog reads → uploads in a realistic cycle."""

    def on_start(self):
        """Register and log in once per simulated user."""
        suffix = uuid.uuid4().hex[:8]
        self.username = f"stress_{suffix}"
        self.email = f"stress_{suffix}@test.local"
        self.password = "StressTest1234!"
        self.user_id = None

        # Register
        reg = self.client.post(
            "/auth/register",
            json={"username": self.username, "email": self.email, "password": self.password},
            name="/auth/register",
        )
        if reg.status_code == 200:
            self.user_id = reg.json().get("user_id")

        # Login
        login = self.client.post(
            "/auth/login",
            json={"identifier": self.username, "password": self.password},
            name="/auth/login",
        )
        if login.status_code == 200 and self.user_id is None:
            self.user_id = login.json().get("user_id")

    @task(4)
    def read_catalog(self):
        if self.user_id:
            self.client.get(f"/catalog?user_id={self.user_id}", name="/catalog")

    @task(2)
    def upload_catalogo(self):
        if not self.user_id:
            return
        sku = _sku()
        csv = _CATALOGO_CSV.format(sku=sku).encode()
        self.client.post(
            "/upload-catalogo",
            data={"user_id": str(self.user_id)},
            files={"file": ("cat.csv", io.BytesIO(csv), "text/csv")},
            name="/upload-catalogo",
        )

    @task(1)
    def upload_detalle(self):
        if not self.user_id:
            return
        order = _order_id()
        csv = _DETALLE_CSV.format(order=order).encode()
        self.client.post(
            "/upload-detalle",
            data={"user_id": str(self.user_id)},
            files={"file": ("detalle.csv", io.BytesIO(csv), "text/csv")},
            name="/upload-detalle",
        )

    @task(1)
    def next_order_number(self):
        self.client.get("/next-order-number", name="/next-order-number")

    @task(1)
    def login_again(self):
        """Simulate a session refresh / re-login."""
        self.client.post(
            "/auth/login",
            json={"identifier": self.username, "password": self.password},
            name="/auth/login",
        )


class AuthenticatedUser(HttpUser):
    tasks = [AuthCycle]
    wait_time = between(1.0, 4.0)


# ── Job polling tasks ─────────────────────────────────────────────────────────

class JobPollingTasks(TaskSet):
    """Simulate polling an optimization job that doesn't exist (404 path)."""

    @task(3)
    def poll_nonexistent_job(self):
        fake_id = uuid.uuid4().hex
        with self.client.get(
            f"/jobs/{fake_id}/status",
            name="/jobs/{id}/status",
            catch_response=True,
        ) as resp:
            # 404 is expected for nonexistent jobs — mark as success
            if resp.status_code == 404:
                resp.success()

    @task(1)
    def health_check(self):
        self.client.get("/health", name="/health")


class JobPollingUser(HttpUser):
    tasks = [JobPollingTasks]
    wait_time = between(0.2, 1.0)


# ── Event hooks for reporting ─────────────────────────────────────────────────

@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    stats = environment.stats
    print("\n── Stress Test Summary ────────────────────────────────���────")
    for name, entry in stats.entries.items():
        print(
            f"  {name[1]:40s}  "
            f"RPS: {entry.current_rps:6.1f}  "
            f"P50: {entry.get_response_time_percentile(0.5):5.0f}ms  "
            f"P95: {entry.get_response_time_percentile(0.95):5.0f}ms  "
            f"Fail%: {entry.fail_ratio * 100:4.1f}%"
        )
    print("────────────────────────────────────────────────────────────")
