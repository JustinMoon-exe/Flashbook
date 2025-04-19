# python_services/app/routes.py
from fastapi import APIRouter, HTTPException, status, Depends
from .models import Order, MarketData  # Import Pydantic models
from typing import Dict
import redis.asyncio as redis  # Import async redis client
import json
# pydantic_encoder is less needed now with model_dump_json() respecting config
# from pydantic.json import pydantic_encoder
import uuid  # Import the uuid library for generating IDs
import os # For potentially getting Redis URL from environment

# --- Redis Connection ---
# Use environment variables in production, default for local dev
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
redis_pool = None # Initialize pool as None

try:
    # Create a connection pool for efficiency
    redis_pool = redis.ConnectionPool.from_url(REDIS_URL, decode_responses=True, max_connections=10)
    print(f"Redis connection pool created for URL: {REDIS_URL}")
except Exception as e:
    print(f"CRITICAL: Error creating Redis connection pool: {e}")
    # In a real app, you might exit or have fallback logic
    # For now, get_redis will raise HTTPException if pool is None

async def get_redis():
    """FastAPI dependency to get an async Redis connection from the pool."""
    if redis_pool is None:
         # Service is unavailable if Redis isn't configured
         raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Redis service connection is not available."
            )
    # redis-py handles borrowing/returning connections from the pool
    return redis.Redis(connection_pool=redis_pool)

router = APIRouter()

# In-memory storage (serves as a simple cache, may become stale regarding status)
orders_db: Dict[str, Order] = {}
market_data_db: Dict[str, MarketData] = {
    "TEST": MarketData(symbol="TEST", bid="99.5", ask="100.5", last="100.0") # Store as string if using Decimal
}

# Define Redis channel names consistently
ORDER_SUBMIT_CHANNEL = "orders:new"

@router.post("/orders", response_model=Order, status_code=status.HTTP_202_ACCEPTED)
async def submit_order(
    order_input: Order,  # Input model validated by FastAPI based on Order definition
    redis_client: redis.Redis = Depends(get_redis)  # Inject Redis client dependency
):
    """
    Accepts an order request, assigns a unique server-side ID, standardizes data,
    and publishes it to a Redis channel for asynchronous processing by the matching engine.
    """
    # --- Force Server-Side ID Generation & Data Preparation ---
    # Create a new Order instance based on validated input, ensuring unique ID and status
    try:
        # Note: Pydantic v2 uses model_validate to create from dict/other model
        order_data = order_input.model_dump() # Get validated data as dict
        order_data['order_id'] = str(uuid.uuid4()) # Force new UUID
        order_data['status'] = 'new' # Ensure status is 'new'
        order_data['symbol'] = order_data['symbol'].upper() # Standardize symbol
        # remaining_quantity is now handled by the validator in the model
        order = Order.model_validate(order_data) # Create final Order object

    except Exception as e:
        # This might catch issues if model_validate fails unexpectedly
        print(f"Error during Order object creation: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid order data processing: {e}")

    # Store in the local cache (primarily for GET /orders/{id} endpoint)
    orders_db[order.order_id] = order
    print(f"Received order via API, assigned ID: {order.order_id}")
    print(f"Order object details: {order!r}") # Use repr for more detailed object info

    # --- Publish to Redis ---
    try:
        # --- Use Pydantic's built-in JSON serialization ---
        # This respects model_config json_encoders (e.g., Decimal -> str)
        order_json = order.model_dump_json() # Generates JSON string directly
        # --- End Pydantic JSON serialization ---

        print(f"Publishing JSON: {order_json}") # Log the actual JSON being sent

        # Publish the JSON string to the designated Redis channel
        published_count = await redis_client.publish(ORDER_SUBMIT_CHANNEL, order_json)
        # publish returns number of clients that received the message
        print(f"Published order {order.order_id} to Redis channel '{ORDER_SUBMIT_CHANNEL}' ({published_count} subscribers)")

        # Return 202 Accepted: Signal request accepted for async processing.
        return order

    except redis.RedisError as e:
        # Handle specific Redis errors (e.g., connection issues)
        print(f"Redis Error: Failed to publish order {order.order_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Service unavailable: Could not publish order to processing engine due to Redis error."
        )
    except Exception as e:
        # Handle other potential errors during serialization or publish
        print(f"Error publishing order {order.order_id} to Redis: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error while publishing order."
        )

@router.get("/orders/{order_id}", response_model=Order)
async def get_order(order_id: str):
    """
    Retrieves an order by its ID from the API's local cache.
    NOTE: Status/remaining quantity may be stale.
    """
    order = orders_db.get(order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found in API cache")
    print(f"Retrieved order {order_id} from local cache (status might be stale: {order.status})")
    return order

@router.get("/marketdata/{symbol}", response_model=MarketData)
async def get_market_data(symbol: str):
    """
    Retrieves the latest market data for a given symbol (currently hardcoded).
    """
    data = market_data_db.get(symbol.upper()) # Use standardized symbol
    if data is None:
         return MarketData(symbol=symbol.upper()) # Return default structure
    return data

@router.get("/orders", response_model=Dict[str, Order])
async def get_all_orders():
     """ Gets all orders currently held in the API's in-memory cache (for debugging). """
     return orders_db

# Add Redis cleanup on shutdown
@router.on_event("shutdown")
async def shutdown_redis():
    if redis_pool:
        print("Closing Redis connection pool...")
        # For redis-py >= 4.2
        await redis_pool.disconnect(inuse_connections=True) # Close pool and active connections
        print("Redis connection pool closed.")