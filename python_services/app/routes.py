# python_services/app/routes.py
from fastapi import (
    APIRouter, HTTPException, status, Depends, WebSocket, WebSocketDisconnect
)
from .models import Order, MarketData      # Using models defined in this package
from .ws_manager import manager             # WebSocket manager instance
import redis.asyncio as redis
import json
import uuid
import os
import logging
from datetime import datetime, timezone
from typing import Dict, Any
import contextlib # For suppress

log = logging.getLogger(__name__)

# --- Redis Connection Setup ---
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
redis_pool = None
try:
    redis_pool = redis.ConnectionPool.from_url(REDIS_URL, decode_responses=True, max_connections=10)
    log.info(f"Redis connection pool created: {REDIS_URL}")
except Exception as e: log.critical(f"CRITICAL: Redis pool creation failed: {e}")

async def get_redis():
    if redis_pool is None: raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis unavailable.")
    return redis.Redis(connection_pool=redis_pool)

# --- API Router Setup ---
router = APIRouter()

# --- In-memory Storage (Simple Cache) ---
orders_db: Dict[str, Any] = {}
market_data_db: Dict[str, MarketData] = {}

# --- Redis Channel Definitions ---
ORDER_SUBMIT_CHANNEL = "orders:new"
SIMULATOR_CONTROL_CHANNEL = "simulator:control"
ENGINE_CONTROL_CHANNEL = "engine:control"
MARKET_EVENTS_CHANNEL = "market:events" # Channel for market events

# --- REST API Endpoints ---
@router.post("/orders", response_model=Dict[str, str], status_code=status.HTTP_202_ACCEPTED, summary="Submit Order")
async def submit_order(order_input: Order, redis_client: redis.Redis = Depends(get_redis)):
    try:
        order_data = order_input.model_dump(); order_id = str(uuid.uuid4()); timestamp_now = datetime.now(timezone.utc)
        order_data['id'] = order_id; order_data['timestamp'] = timestamp_now.isoformat(); order_data['status'] = 'new'; order_data['symbol'] = order_data['symbol'].upper()
        qty = order_data.get('quantity'); rem_qty = order_data.get('remaining_quantity')
        if qty is None or qty <= 0: raise ValueError("Order quantity must be positive.")
        if rem_qty is None or not isinstance(rem_qty, int) or rem_qty > qty or rem_qty <= 0: order_data['remaining_quantity'] = qty; log.debug(f"Set remaining_quantity={qty} for order {order_id}")
        log.info(f"Generated Order ID (FastAPI): {order_id}"); orders_db[order_id] = order_data # Cache locally
    except ValueError as ve: log.error(f"Value error: {ve}"); raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e: log.error(f"Error preparing order: {e}", exc_info=True); raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid data.")
    try:
        order_json = json.dumps(order_data, default=str); pub_count = await redis_client.publish(ORDER_SUBMIT_CHANNEL, order_json); log.info(f"Published order {order_id} to '{ORDER_SUBMIT_CHANNEL}' ({pub_count} subs)"); return {"message": "Order received", "order_id": order_id}
    except redis.RedisError as e: log.error(f"Redis Error publishing order {order_id}: {e}"); orders_db.pop(order_id, None); raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Publish error.")
    except Exception as e: log.exception(f"Error publishing order {order_id}: {e}"); orders_db.pop(order_id, None); raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error.")

@router.get("/orders/{order_id}", response_model=Order, summary="Get Order (Cache)")
async def get_order(order_id: str):
    order_data = orders_db.get(order_id);
    if order_data is None: raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    try: return Order.model_validate(order_data)
    except Exception as e: raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Cached data invalid.")
@router.get("/marketdata/{symbol}", response_model=MarketData, summary="Get Market Data (Cache)")
async def get_market_data(symbol: str):
    data = market_data_db.get(symbol.upper()); return data if data else MarketData(symbol=symbol.upper())
@router.get("/orders", response_model=Dict[str, Order], summary="Get All Orders (Cache)")
async def get_all_orders():
     validated = {}; [validated.update({k: Order.model_validate(v)}) for k, v in orders_db.items() if isinstance(v, dict)]; return validated


# --- WebSocket Endpoint ---
@router.websocket("/ws/dashboard")
async def websocket_dashboard_endpoint(websocket: WebSocket, redis_client: redis.Redis = Depends(get_redis)):
    await manager.connect(websocket)
    client_id = f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "Unknown"
    log.info(f"WebSocket client connected: {client_id}")
    try:
        while True:
            data = await websocket.receive_text()
            log.debug(f"WS Rcvd from {client_id}: {data[:100]}...")
            try:
                 control_message: Dict[str, Any] = json.loads(data)
                 log.info(f"Parsed WS message: {control_message}")
                 command = control_message.get("command") # Look for top-level command first

                 if command == "set_pause":
                     await redis_client.publish(SIMULATOR_CONTROL_CHANNEL, json.dumps({"command": "set_pause"}))
                     log.info(f"Published 'set_pause' to simulator"); await manager.send_personal_message('{"status": "Pause sent"}', websocket)
                 elif command == "set_resume":
                     await redis_client.publish(SIMULATOR_CONTROL_CHANNEL, json.dumps({"command": "set_resume"}))
                     log.info(f"Published 'set_resume' to simulator"); await manager.send_personal_message('{"status": "Resume sent"}', websocket)
                 elif command == "reset":
                     log.warning(f"RESET command received from {client_id}"); await redis_client.publish(SIMULATOR_CONTROL_CHANNEL, json.dumps({"command": "reset_simulator"})); await redis_client.publish(ENGINE_CONTROL_CHANNEL, json.dumps({"command": "reset_engine"})); orders_db.clear(); market_data_db.clear(); log.info("Cleared API caches & sent reset commands."); await manager.send_personal_message('{"status": "Reset sent"}', websocket)
                 elif command == "market_event": # Check for market event command
                     payload = control_message.get("payload")
                     if payload and "symbol" in payload and "percent_shift" in payload:
                          event_json = json.dumps(payload)
                          # Publish to the dedicated market event channel for the engine
                          await redis_client.publish(MARKET_EVENTS_CHANNEL, event_json)
                          log.info(f"Published market event to '{MARKET_EVENTS_CHANNEL}': {event_json}")
                          await manager.send_personal_message('{"status": "Market event sent"}', websocket)
                     else:
                          log.warning(f"Invalid market_event payload from {client_id}: {payload}"); await manager.send_personal_message('{"error": "Invalid market event payload"}', websocket)
                 elif "agent_id" in control_message and "parameter" in control_message and "value" in control_message: # Assume agent control otherwise
                     await redis_client.publish(SIMULATOR_CONTROL_CHANNEL, json.dumps(control_message))
                     log.info(f"Published agent control to '{SIMULATOR_CONTROL_CHANNEL}' for {control_message.get('agent_id')}")
                 else:
                      log.warning(f"Invalid command/msg format from {client_id}: {data}"); await manager.send_personal_message('{"error": "Invalid format"}', websocket)

            except json.JSONDecodeError: log.warning(f"Non-JSON from {client_id}: {data}"); await manager.send_personal_message('{"error": "Invalid JSON"}', websocket)
            except redis.RedisError as e: log.error(f"Redis Error from {client_id}: {e}"); await manager.send_personal_message(f'{{"error": "Redis error"}}', websocket)
            except Exception as e: log.exception(f"Error processing WS msg from {client_id}: {e}"); await manager.send_personal_message(f'{{"error": "Processing error"}}', websocket)
    except WebSocketDisconnect: log.info(f"Client {client_id} disconnected.")
    except Exception as e: log.exception(f"WebSocket error for {client_id}: {e}")
    finally: manager.disconnect(websocket)

# --- Router Shutdown ---
@router.on_event("shutdown")
async def shutdown_event(): pass