import json
import urllib.request
import urllib.error

class ApiClient:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url

    def get_catalog(self):
        try:
            with urllib.request.urlopen(f"{self.base_url}/catalog") as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"Error fetching catalog: {e}")
            return []

    def get_next_order_number(self):
        try:
            with urllib.request.urlopen(f"{self.base_url}/next-order-number") as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("next", "1")
        except Exception as e:
            print(f"Error fetching next order number: {e}")
            return "1"

    def submit_order(self, order_payload: dict):
        url = f"{self.base_url}/simulation/order"
        data = json.dumps(order_payload).encode("utf-8")
        req = urllib.request.Request(
            url, 
            data=data, 
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8")
            return {"error": f"HTTP {e.code}: {msg}"}
        except Exception as e:
            return {"error": str(e)}
