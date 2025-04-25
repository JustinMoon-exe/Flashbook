# tests/test_api.py
from fastapi.testclient import TestClient
from python_services.app.main import app 
import pytest 

client = TestClient(app) 

def test_read_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Flashbook API is running"}

def test_submit_order():
    order_data = {
        "symbol": "TEST",
        "side": "buy",
        "price": 105.5,
        "quantity": 10
    }
    response = client.post("/api/v1/orders", json=order_data)
    assert response.status_code == 201 
    response_json = response.json()
    assert response_json["symbol"] == order_data["symbol"]
    assert response_json["side"] == order_data["side"]
    assert response_json["price"] == order_data["price"]
    assert response_json["quantity"] == order_data["quantity"]
    assert response_json["status"] == "new" 
    assert "order_id" in response_json
    assert "timestamp" in response_json

    
    pytest.created_order_id = response_json["order_id"]


def test_get_submitted_order():
    
    assert hasattr(pytest, "created_order_id"), "Order ID was not created in previous test"
    order_id = pytest.created_order_id

    response = client.get(f"/api/v1/orders/{order_id}")
    assert response.status_code == 200
    response_json = response.json()
    assert response_json["order_id"] == order_id
    assert response_json["symbol"] == "TEST"

def test_get_nonexistent_order():
    response = client.get("/api/v1/orders/nonexistent-id-123")
    assert response.status_code == 404 

def test_get_market_data():
    response = client.get("/api/v1/marketdata/TEST")
    assert response.status_code == 200
    response_json = response.json()
    assert response_json["symbol"] == "TEST"
    assert "bid" in response_json
    assert "ask" in response_json
    assert "last" in response_json
    assert "timestamp" in response_json

def test_get_market_data_unknown_symbol():
     response = client.get("/api/v1/marketdata/UNKNOWN")
     assert response.status_code == 200 
     response_json = response.json()
     assert response_json["symbol"] == "UNKNOWN"
     assert response_json["bid"] is None
     assert response_json["ask"] is None
     assert response_json["last"] is None