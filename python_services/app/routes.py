# python_services/app/routes.py
from fastapi import APIRouter, HTTPException, status
from .models import Order, MarketData
from typing import Dict

router = APIRouter()

# In-memory storage (replace with actual logic later)
orders_db: Dict[str, Order] = {}
market_data_db: Dict[str, MarketData] = {
    "TEST": MarketData(symbol="TEST", bid=99.5, ask=100.5, last=100.0)
}

@router.post("/orders", response_model=Order, status_code=status.HTTP_201_CREATED)
async def submit_order(order: Order):
    """
    Submits a new order.
    Generates order_id if not provided.
    Stores the order in the in-memory db.
    (Later: This will forward the order to the matching engine).
    """
    if order.order_id in orders_db:
         raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Order with ID {order.order_id} already exists."
         )
    # Ensure status is 'new' on submission
    order.status = 'new'
    orders_db[order.order_id] = order
    print(f"Received order: {order.model_dump_json()}") # Log received order
    # TODO Phase 4: Send order to matching engine (e.g., via Redis pub/sub)
    return order

@router.get("/orders/{order_id}", response_model=Order)
async def get_order(order_id: str):
    """
    Retrieves an order by its ID.
    """
    order = orders_db.get(order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return order

@router.get("/marketdata/{symbol}", response_model=MarketData)
async def get_market_data(symbol: str):
    """
    Retrieves the latest market data for a given symbol.
    (Later: This data might come from the matching engine or a separate feed).
    """
    data = market_data_db.get(symbol.upper())
    if data is None:
         # For simplicity, return default if not found, or could be 404
         # raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Market data not found for symbol")
         return MarketData(symbol=symbol.upper()) # Return empty data for unknown symbol
    return data

# Example endpoint to view all orders (for debugging)
@router.get("/orders", response_model=Dict[str, Order])
async def get_all_orders():
     """ Gets all orders currently in the system (for debugging). """
     return orders_db