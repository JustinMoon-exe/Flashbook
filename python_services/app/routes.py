# python_services/app/routes.py
from fastapi import (
    APIRouter,
    HTTPException,
    status,
    Depends,
    WebSocket,          # For WebSocket endpoint
    WebSocketDisconnect # For handling disconnects
)
from .models import Order, MarketData      # Import Pydantic models
from .ws_manager import manager             # Import the WebSocket connection manager instance
from typing import Dict, Any, List          # For type hints
import redis.asyncio as redis              # Import async redis client
import json
import uuid                                # Import the uuid library for generating IDs
import os                                  # For potentially getting Redis URL from environment
import logging                             # Use standard logging
from datetime import datetime, timezone    # Ensure timezone is imported

log = logging.getLogger(__name__) # Logger for this module

# --- Redis Connection Setup ---
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
redis_pool = None

try:
    redis_pool = redis.ConnectionPool.from_url(
        REDIS_URL, decode_responses=True, max_connections=10
    )
    log.info(f"Redis connection pool created for URL: {REDIS_URL}")
except Exception as e:
    log.critical(f"CRITICAL: Error creating Redis connection pool on startup: {e}")

async def get_redis():
    """FastAPI dependency to get an async Redis connection from the pool."""
    if redis_pool is None:
         raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Redis service connection is not available."
            )
    return redis.Redis(connection_pool=redis_pool)

# --- API Router Setup ---
router = APIRouter()

# --- In-memory Storage (Simple Cache - Should be replaced/synced) ---
# WARNING: This cache can become stale if Rust engine state changes without API calls
orders_db: Dict[str, Any] = {} # Store raw dicts or validated models? Needs sync strategy.
market_data_db: Dict[str, MarketData] = {
    "TEST": MarketData(symbol="TEST", bid="99.5", ask="100.5", last="100.0"),
    "SIM": MarketData(symbol="SIM", bid="49.9", ask="50.1", last="50.0") # Example
}

# --- Redis Channel Definitions ---
ORDER_SUBMIT_CHANNEL = "orders:new"
SIMULATOR_CONTROL_CHANNEL = "simulator:control" # For UI -> Simulator communication

# --- REST API Endpoints ---

@router.post(
    "/orders",
    response_model=Dict[str, str], # Return simple message and ID
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a New Order",
    description="Receives order details, assigns a unique ID, sets initial status, adds timestamp, "
                "and publishes it to Redis for the matching engine. "
                "Does *not* wait for matching.",
)
async def submit_order(
    order_input: Order, # Receive the Pydantic model for input validation
    redis_client: redis.Redis = Depends(get_redis)
):
    # Prepare order data dictionary, adding necessary fields
    try:
        order_data = order_input.model_dump() # Dump initial data from input model
        order_id = str(uuid.uuid4())          # Generate unique ID
        timestamp_now = datetime.now(timezone.utc) # Get current UTC time

        # Add/overwrite generated/required fields for the Rust engine
        order_data['id'] = order_id # Use 'id' (lowercase) matching Rust struct
        order_data['timestamp'] = timestamp_now.isoformat() # Add ISO format timestamp
        order_data['status'] = 'new' # Set initial status explicitly
        order_data['symbol'] = order_data['symbol'].upper() # Ensure uppercase symbol

        # Ensure remaining_quantity matches quantity if not provided or invalid
        # This makes the payload match the Rust Order struct expectation after deserialization
        qty = order_data.get('quantity')
        rem_qty = order_data.get('remaining_quantity')
        if qty is None or qty <= 0:
            raise ValueError("Order quantity must be positive.")
        if rem_qty is None or not isinstance(rem_qty, int) or rem_qty > qty or rem_qty <= 0:
             order_data['remaining_quantity'] = qty
             log.debug(f"Set remaining_quantity={qty} for order {order_id}")

        log.info(f"Generated Order ID (FastAPI): {order_id}")
        log.debug(f"Order data prepared for Redis: {order_data}")

    except ValueError as ve:
        log.error(f"Value error preparing order data: {ve}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e:
        log.error(f"Error preparing order data: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid order data processing: {e}")

    # Store a representation locally if needed (optional)
    # This is a simple cache and doesn't reflect the true state from the engine
    orders_db[order_id] = order_data # Store the dictionary we're publishing
    log.debug(f"Order {order_id} added to local API cache (may become stale).")

    # Publish to Redis
    try:
        # Use Python's json.dumps with default=str for robust serialization
        order_json = json.dumps(order_data, default=str)
        log.info(f"Published Order ID (FastAPI->Redis): {order_id}")
        log.debug(f"Publishing JSON to '{ORDER_SUBMIT_CHANNEL}': {order_json}")
        published_count = await redis_client.publish(ORDER_SUBMIT_CHANNEL, order_json)
        log.info(f"Published order {order_id} to '{ORDER_SUBMIT_CHANNEL}' ({published_count} subscribers)")

        # Return the generated ID and a success message (Accepted)
        log.info(f"Returning Order ID (FastAPI Response): {order_id}")
        return {"message": "Order received and published", "order_id": order_id}

    except redis.RedisError as e:
        log.error(f"Redis Error publishing order {order_id}: {e}")
        # Attempt to remove from local cache if publish failed? Depends on desired consistency.
        orders_db.pop(order_id, None)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Service unavailable: Could not publish order.")
    except Exception as e:
        log.exception(f"Error publishing order {order_id}: {e}")
        orders_db.pop(order_id, None)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error while publishing order.")


@router.get(
    "/orders/{order_id}",
    response_model=Order, # Expect to return a valid Order model
    summary="Get Order Status by ID (API Cache)",
    description="Retrieves the status of a specific order **from the API's local cache**. "
                "Note: This cache might be stale compared to the matching engine's state.",
)
async def get_order(order_id: str):
    # Retrieve from local cache (which stores dicts now)
    order_data = orders_db.get(order_id)
    if order_data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found in API cache")

    try:
        # Validate the cached data against the Pydantic model before returning
        order = Order.model_validate(order_data)
        log.info(f"Retrieved order {order_id} from local cache (status: {order.status})")
        return order
    except Exception as e:
        log.error(f"Cached data for order {order_id} failed validation: {e}. Data: {order_data}")
        # Decide whether to return raw data or error
        # Returning error is safer as data might be corrupt/invalid
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Cached order data invalid.")


@router.get(
    "/marketdata/{symbol}",
    response_model=MarketData,
    summary="Get Market Data for Symbol (API Cache)",
     description="Retrieves the latest market data for a given symbol **from the API's local cache**. "
                 "Note: This cache is simple and might be stale.",
)
async def get_market_data(symbol: str):
    data = market_data_db.get(symbol.upper())
    if data is None:
         # Return default empty data if symbol not found in cache
         log.warning(f"Market data for symbol '{symbol.upper()}' not found in API cache.")
         return MarketData(symbol=symbol.upper()) # Return with symbol, but empty prices
    log.debug(f"Retrieved market data for {symbol.upper()} from local cache.")
    return data

@router.get(
    "/orders",
    response_model=Dict[str, Order], # Expect dict where values are Order models
    summary="Get All Orders (API Cache - Debug)",
    description="Retrieves all orders currently held **in the API's local cache**. "
                "Useful for debugging but potentially large and may contain stale data.",
)
async def get_all_orders():
     # Validate all cached orders before returning (can be slow if many orders)
     validated_orders = {}
     for order_id, order_data in orders_db.items():
         try:
             validated_orders[order_id] = Order.model_validate(order_data)
         except Exception as e:
             log.warning(f"Skipping invalid order data in cache for {order_id}: {e}")
     log.info(f"Returning {len(validated_orders)} orders from local API cache.")
     return validated_orders

# --- WebSocket Endpoint ---

@router.websocket("/ws/dashboard")
async def websocket_dashboard_endpoint(
    websocket: WebSocket,
    # Inject redis dependency needed for publishing control messages
    redis_client: redis.Redis = Depends(get_redis)
):
    """
    Handles WebSocket connections for the real-time dashboard.
    - Accepts connections.
    - Listens for incoming messages (control commands from UI).
    - Publishes received control commands to Redis for the simulator.
    - Relies on a background task (in main.py) to broadcast market events TO the client.
    """
    await manager.connect(websocket)
    client_host = websocket.client.host if websocket.client else "Unknown"
    client_port = websocket.client.port if websocket.client else "N/A"
    client_id = f"{client_host}:{client_port}"
    log.info(f"WebSocket client connected: {client_id}")

    try:
        while True:
            # Listen for messages from the client (control commands)
            data = await websocket.receive_text()
            log.debug(f"Received message from client {client_id}: {data[:100]}...") # Log truncated msg

            # --- Handle Incoming Control Messages ---
            try:
                 control_message: Dict[str, Any] = json.loads(data)
                 log.info(f"Parsed control message from {client_id}: {control_message}")

                 # Basic validation of control message structure
                 if not isinstance(control_message, dict) or \
                    "agent_id" not in control_message or \
                    "parameter" not in control_message or \
                    "value" not in control_message:
                     log.warning(f"Invalid control message format received from {client_id}: {data}")
                     await manager.send_personal_message('{"error": "Invalid control message format"}', websocket)
                     continue # Skip this message

                 # Publish the validated control message to Redis for the simulator
                 # Re-serialize the validated dictionary
                 control_json = json.dumps(control_message)
                 await redis_client.publish(SIMULATOR_CONTROL_CHANNEL, control_json)
                 log.info(f"Published control message to '{SIMULATOR_CONTROL_CHANNEL}' for agent {control_message.get('agent_id')}")
                 # Optional: Send confirmation back to UI
                 # await manager.send_personal_message(f'{{"status": "Control message for agent {control_message.get("agent_id")} received"}}', websocket)

            except json.JSONDecodeError:
                 log.warning(f"Received non-JSON WebSocket message from {client_id}, ignoring: {data}")
                 await manager.send_personal_message('{"error": "Invalid JSON received"}', websocket)
            except redis.RedisError as e:
                 log.error(f"Redis Error publishing control message from {client_id}: {e}")
                 await manager.send_personal_message(f'{{"error": "Failed to publish control message: Redis error"}}', websocket)
            except Exception as e:
                 # Catch other errors during processing
                 log.exception(f"Error processing WebSocket message from {client_id}: {e}") # Log traceback
                 await manager.send_personal_message(f'{{"error": "Error processing message: {e}"}}', websocket)

    except WebSocketDisconnect:
        # This is expected when the client closes the connection
        manager.disconnect(websocket)
        log.info(f"Client {client_id} disconnected.")
    except Exception as e:
        # Catch other potential errors during the connection lifetime
        log.exception(f"Unexpected WebSocket error for client {client_id}: {e}")
        # Ensure disconnect is called even on unexpected errors
        with contextlib.suppress(Exception): # Avoid errors during cleanup
             manager.disconnect(websocket)


# --- Router Shutdown Event ---
# Note: prefer cleanup in main.py lifespan manager for central resource handling
@router.on_event("shutdown")
async def shutdown_event():
    log.info("Router shutdown event triggered (cleanup handled in main.py lifespan).")
    pass