from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, 
    QPushButton, QLineEdit, QFormLayout, QSpinBox, 
    QMessageBox, QFrame, QScrollArea, QListWidgetItem,
    QDateEdit
)
from PyQt6.QtCore import Qt, pyqtSignal, QDate

class ProductItem(QFrame):
    add_to_cart = pyqtSignal(dict)

    def __init__(self, product):
        super().__init__()
        self.product = product
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            ProductItem {
                background-color: #ffffff;
                border-radius: 8px;
                border: 1px solid #e0e0e0;
                padding: 10px;
            }
            ProductItem:hover {
                border: 1px solid #2196F3;
            }
        """)

        layout = QHBoxLayout(self)
        
        info_layout = QVBoxLayout()
        name_label = QLabel(f"<b>{product['sku']}</b>: {product['descripcion']}")
        name_label.setWordWrap(True)
        specs_label = QLabel(f"<font color='#757575'>{product['tipo_embalaje']} | {product['volumen']} m³ | {product['peso']} kg</font>")
        info_layout.addWidget(name_label)
        info_layout.addWidget(specs_label)
        
        layout.addLayout(info_layout, 1)

        self.btn_add = QPushButton("Add to Cart")
        self.btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add.clicked.connect(lambda: self.add_to_cart.emit(self.product))
        layout.addWidget(self.btn_add)

class SimulationUI(QWidget):
    place_order = pyqtSignal(dict, list) # (Venta data, Items list)

    def __init__(self):
        super().__init__()
        self.cart = []
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Order Simulation - Customer Portal")
        self.resize(1000, 700)
        
        main_layout = QHBoxLayout(self)

        # --- Left: Catalog ---
        catalog_section = QVBoxLayout()
        catalog_section.addWidget(QLabel("<h2>Product Catalog</h2>"))
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.catalog_widget = QWidget()
        self.catalog_layout = QVBoxLayout(self.catalog_widget)
        self.catalog_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.catalog_widget)
        
        catalog_section.addWidget(self.scroll)
        main_layout.addLayout(catalog_section, 2)

        # --- Right: Cart & Checkout ---
        right_panel = QVBoxLayout()
        right_panel.setSpacing(20)

        # Cart
        cart_box = QFrame()
        cart_box.setStyleSheet("background-color: #f5f5f5; border-radius: 8px; padding: 10px;")
        cart_layout = QVBoxLayout(cart_box)
        cart_layout.addWidget(QLabel("<b>Shopping Cart</b>"))
        
        self.cart_list = QListWidget()
        cart_layout.addWidget(self.cart_list)
        
        self.btn_clear = QPushButton("Clear Cart")
        self.btn_clear.clicked.connect(self.clear_cart)
        cart_layout.addWidget(self.btn_clear)
        
        right_panel.addWidget(cart_box, 1)

        # Checkout Form
        form_box = QFrame()
        form_box.setStyleSheet("background-color: #ffffff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 15px;")
        form_layout = QFormLayout(form_box)
        form_layout.addRow(QLabel("<b>Checkout Details</b>"), QLabel(""))

        self.inp_n_orden = QLineEdit()
        self.inp_n_orden.setPlaceholderText("e.g. ORD-1001")
        form_layout.addRow("Order #:", self.inp_n_orden)

        self.inp_rut = QLineEdit()
        self.inp_rut.setPlaceholderText("e.g. 12.345.678-9")
        form_layout.addRow("Customer RUT:", self.inp_rut)

        self.inp_nombre = QLineEdit()
        form_layout.addRow("Customer Name:", self.inp_nombre)

        self.inp_direccion = QLineEdit()
        self.inp_direccion.setPlaceholderText("e.g. Av Providencia 1234")
        form_layout.addRow("Address:", self.inp_direccion)

        self.inp_comuna = QLineEdit()
        self.inp_comuna.setPlaceholderText("e.g. Providencia")
        form_layout.addRow("Comuna:", self.inp_comuna)

        self.inp_fecha_despacho = QDateEdit()
        self.inp_fecha_despacho.setCalendarPopup(True)
        self.inp_fecha_despacho.setDate(QDate.currentDate().addDays(1))
        form_layout.addRow("Requested Delivery:", self.inp_fecha_despacho)

        self.inp_monto = QLineEdit()
        self.inp_monto.setPlaceholderText("Total amount in CLP")
        self.inp_monto.setText("0")
        form_layout.addRow("Total Amount:", self.inp_monto)

        self.btn_submit = QPushButton("PLACE ORDER")
        self.btn_submit.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; height: 40px; border-radius: 4px;")
        self.btn_submit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_submit.clicked.connect(self._on_submit)
        form_layout.addRow(self.btn_submit)

        right_panel.addWidget(form_box, 2)
        main_layout.addLayout(right_panel, 1)

    def load_catalog(self, products):
        # Clear existing
        for i in reversed(range(self.catalog_layout.count())): 
            self.catalog_layout.itemAt(i).widget().setParent(None)
        
        for p in products:
            item = ProductItem(p)
            item.add_to_cart.connect(self.add_item_to_cart)
            self.catalog_layout.addWidget(item)

    def add_item_to_cart(self, product):
        self.cart.append(product)
        self.cart_list.addItem(f"{product['sku']} - {product['descripcion']}")
        self.update_total()

    def clear_cart(self):
        self.cart = []
        self.cart_list.clear()
        self.update_total()

    def update_total(self):
        # In a real app we'd have prices, for now just a dummy counter
        # Or maybe count items
        pass

    def _on_submit(self):
        if not self.cart:
            QMessageBox.warning(self, "Empty Cart", "Please add at least one product.")
            return
        
        if not self.inp_n_orden.text() or not self.inp_direccion.text():
            QMessageBox.warning(self, "Missing Info", "Order # and Address are required.")
            return

        self.btn_submit.setEnabled(False)
        self.btn_submit.setText("PLACING ORDER...")

        venta = {
            "numero_orden": self.inp_n_orden.text(),
            "rut": self.inp_rut.text(),
            "nombre_cliente": self.inp_nombre.text(),
            "direccion_cliente": self.inp_direccion.text(),
            "comuna": self.inp_comuna.text(),
            "estado": "PENDIENTE",
            "monto_pedido": int(self.inp_monto.text() or 0),
            "fecha_despacho_solicitada": self.inp_fecha_despacho.date().toString("yyyy-MM-dd")
        }
        
        items = []
        for p in self.cart:
            items.append({
                "sku": p["sku"],
                "descripcion_sku": p["descripcion"],
                "cantidad": 1, # Simple simulation
                "largo_cm": p["largo"],
                "ancho_cm": p["ancho"],
                "alto_cm": p["alto"],
                "volumen_unitario_m3": p["volumen"],
                "peso_unitario_kg": p["peso"],
                "volumen_total_m3": p["volumen"],
                "peso_total_kg": p["peso"]
            })

        self.place_order.emit(venta, items)

    def show_success(self, msg):
        QMessageBox.information(self, "Order Placed", msg)
        self.clear_cart()
        self.inp_n_orden.clear()
        self.btn_submit.setEnabled(True)
        self.btn_submit.setText("PLACE ORDER")

    def show_error(self, msg):
        QMessageBox.critical(self, "Error", msg)
        self.btn_submit.setEnabled(True)
        self.btn_submit.setText("PLACE ORDER")
