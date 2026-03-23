import sys
import os
from datetime import datetime

# Add app root to path for database and backend imports if needed
# Although we use API calls, we might need some shared config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication
from simulation.ui import SimulationUI
from simulation.api_client import ApiClient

class SimulationApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.api = ApiClient()
        self.view = SimulationUI()
        self.view.place_order.connect(self.handle_order)
        
        self.load_initial_data()

    def load_initial_data(self):
        products = self.api.get_catalog()
        if products:
            self.view.load_catalog(products)
        else:
            print("Warning: Catalog is empty or backend is offline.")
        
        self.refresh_order_number()

    def refresh_order_number(self):
        next_num = self.api.get_next_order_number()
        self.view.inp_n_orden.setText(next_num)

    def handle_order(self, venta, items):
        # Add date
        venta["fecha_pedido"] = datetime.now().strftime("%Y-%m-%d")
        
        payload = {
            "venta": venta,
            "items": items
        }
        
        result = self.api.submit_order(payload)
        
        if "error" in result:
            self.view.show_error(result["error"])
        else:
            self.view.show_success(result["message"])
            self.refresh_order_number()

    def run(self):
        self.view.show()
        sys.exit(self.app.exec())

if __name__ == "__main__":
    sim = SimulationApp()
    sim.run()
