# python_services/app/main.py
from fastapi import FastAPI
from contextlib import asynccontextmanager
import asyncio
import redis.asyncio as redis
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError
import json
import os
import logging
from typing import Dict, Any, List

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("FastAPI_App")

from .routes import router, redis_pool
from .ws_manager import manager

# --- Define Redis Channels ---
AGENT_STATUS_CHANNEL = "agent:status"
EXCHANGE_STATS_CHANNEL = "exchange:stats"
TRADES_CHANNEL = "trades:executed"
ORDERS_CHANNEL = "orders:updated"
BBO_PATTERN = "marketdata:bbo:*"
BOOK_PATTERN = "marketdata:book:*"

# Channels for direct subscription
DIRECT_CHANNELS = [TRADES_CHANNEL, ORDERS_CHANNEL, AGENT_STATUS_CHANNEL, EXCHANGE_STATS_CHANNEL]
# Patterns for pattern subscription
PATTERN_CHANNELS = [BBO_PATTERN, BOOK_PATTERN]

# --- Helper to process and broadcast ---
async def process_and_broadcast(app_msg_type: str, channel: str, data_raw: Any):
    global manager
    try:
        data_str = data_raw.decode('utf-8') if isinstance(data_raw, bytes) else data_raw
        payload_dict = json.loads(data_str)
        log.debug(f"--- Parsed payload for {app_msg_type}: {payload_dict}")

        broadcast_data_dict = { "type": app_msg_type, "channel": channel, "payload": payload_dict }
        broadcast_data_json = json.dumps(broadcast_data_dict)
        log.debug(f"--- Prepared broadcast JSON for {app_msg_type}: {broadcast_data_json[:200]}...")

        if manager.active_connections:
             active_conn_count = len(manager.active_connections)
             log.info(f"--- Attempting broadcast of {app_msg_type} to {active_conn_count} clients...")
             await manager.broadcast(broadcast_data_json)
             log.info(f"--- Broadcast successful for {app_msg_type} event.")
        else:
             log.debug(f"--- Skipping broadcast for {app_msg_type}: No clients connected.")

    except json.JSONDecodeError as json_err: log.error(f"!!! Listener JSON Decode Error: {json_err}. Raw Data: '{data_raw}'")
    except Exception as broadcast_err: log.error(f"!!! Listener Broadcast/Processing Error: {broadcast_err}", exc_info=True)


# --- NEW: Listener for DIRECT Channel Messages ---
async def direct_channel_listener(pubsub: redis.client.PubSub):
    log.info("--- Direct Channel Listener Task Started ---")
    try:
        # Subscribe should happen before listen
        if DIRECT_CHANNELS:
             await pubsub.subscribe(*DIRECT_CHANNELS)
             log.info(f"--- Direct Listener Subscribed to: {DIRECT_CHANNELS}")
        else:
             log.warning("--- Direct Listener: No direct channels to subscribe to.")
             return # Nothing to do

        log.info("--- Entering Direct Listener Loop ---")
        while True: # Removed outer try/finally, handled by lifespan cancellation
            try:
                # Use get_message with timeout for direct subscriptions
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)

                if message and message.get("type") == "message":
                    log.info(f"##### RAW DIRECT MESSAGE RECEIVED by Listener: {message}")
                    channel = message['channel'].decode('utf-8') if isinstance(message['channel'], bytes) else message['channel']
                    data_raw = message['data']

                    app_msg_type = "unknown"
                    if channel == TRADES_CHANNEL: app_msg_type = "trade"
                    elif channel == ORDERS_CHANNEL: app_msg_type = "order_update"
                    elif channel == AGENT_STATUS_CHANNEL: app_msg_type = "agent_status"
                    elif channel == EXCHANGE_STATS_CHANNEL: app_msg_type = "exchange_stats"

                    if app_msg_type != "unknown":
                        await process_and_broadcast(app_msg_type, channel, data_raw)
                    else:
                        log.warning(f"--- Direct Listener ignoring unhandled channel: {channel}")

                await asyncio.sleep(0.01) # Small yield

            except RedisTimeoutError: continue # Expected when no message arrives
            except RedisConnectionError as e: log.error(f"--- Direct Listener Connection Error: {e}."); await asyncio.sleep(5); continue
            except asyncio.CancelledError: log.info("--- Direct Listener task cancelled."); break
            except Exception as e: log.error(f"--- Direct Listener Error in loop: {e}", exc_info=True); await asyncio.sleep(1)
    finally:
         log.info("--- Direct Channel Listener Task Finishing ---")


# --- NEW: Listener for PATTERN Channel Messages ---
async def pattern_channel_listener(pubsub: redis.client.PubSub):
    log.info("--- Pattern Channel Listener Task Started ---")
    try:
        # Subscribe should happen before listen
        if PATTERN_CHANNELS:
             await pubsub.psubscribe(*PATTERN_CHANNELS)
             log.info(f"--- Pattern Listener PSubscribed to: {PATTERN_CHANNELS}")
        else:
             log.warning("--- Pattern Listener: No patterns to subscribe to.")
             return

        log.info("--- Entering Pattern Listener Loop ---")
        while True:
            try:
                # Use get_message with timeout for pattern subscriptions
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)

                if message and message.get("type") == "pmessage":
                    log.info(f"##### RAW PATTERN MESSAGE RECEIVED by Listener: {message}")
                    channel = message['channel'].decode('utf-8') if isinstance(message['channel'], bytes) else message['channel']
                    data_raw = message['data']
                    pattern = message.get('pattern')
                    pattern = pattern.decode('utf-8') if isinstance(pattern, bytes) else pattern

                    app_msg_type = "unknown"
                    if pattern == BBO_PATTERN: app_msg_type = "bbo_update"
                    elif pattern == BOOK_PATTERN: app_msg_type = "book_snapshot"

                    if app_msg_type != "unknown":
                        await process_and_broadcast(app_msg_type, channel, data_raw) # Pass actual channel
                    else:
                        log.warning(f"--- Pattern Listener ignoring unhandled pattern: {pattern}")

                await asyncio.sleep(0.01) # Small yield

            except RedisTimeoutError: continue # Expected
            except RedisConnectionError as e: log.error(f"--- Pattern Listener Connection Error: {e}."); await asyncio.sleep(5); continue
            except asyncio.CancelledError: log.info("--- Pattern Listener task cancelled."); break
            except Exception as e: log.error(f"--- Pattern Listener Error in loop: {e}", exc_info=True); await asyncio.sleep(1)
    finally:
        log.info("--- Pattern Channel Listener Task Finishing ---")


# --- Lifespan Manager (Creates separate listeners/pubsubs) ---
direct_listener_task: asyncio.Task | None = None
pattern_listener_task: asyncio.Task | None = None
redis_direct_client: redis.Redis | None = None
redis_pattern_client: redis.Redis | None = None
pubsub_direct: redis.client.PubSub | None = None
pubsub_pattern: redis.client.PubSub | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Application startup...")
    global direct_listener_task, pattern_listener_task
    global redis_direct_client, redis_pattern_client
    global pubsub_direct, pubsub_pattern

    if redis_pool:
        try:
            # Setup for Direct Listener
            redis_direct_client = redis.Redis(connection_pool=redis_pool)
            await redis_direct_client.ping()
            pubsub_direct = redis_direct_client.pubsub(ignore_subscribe_messages=True)
            direct_listener_task = asyncio.create_task(direct_channel_listener(pubsub_direct), name="DirectListenerTask")
            log.info("Direct listener background task created.")

            # Setup for Pattern Listener
            redis_pattern_client = redis.Redis(connection_pool=redis_pool)
            await redis_pattern_client.ping()
            pubsub_pattern = redis_pattern_client.pubsub(ignore_subscribe_messages=True)
            pattern_listener_task = asyncio.create_task(pattern_channel_listener(pubsub_pattern), name="PatternListenerTask")
            log.info("Pattern listener background task created.")

        except RedisConnectionError as e: log.error(f"ERROR listener startup: Redis Connection: {e}"); direct_listener_task = None; pattern_listener_task = None
        except Exception as e: log.error(f"ERROR listener startup: {e}", exc_info=True); direct_listener_task = None; pattern_listener_task = None
    else: log.warning("Main Redis pool not available. Listener tasks not started.")

    yield # Application runs

    # --- Shutdown ---
    log.info("Application shutdown initiated...")
    tasks_to_cancel = [direct_listener_task, pattern_listener_task]
    for task in tasks_to_cancel:
        if task and not task.done():
            log.info(f"Cancelling task: {task.get_name()}...")
            task.cancel()
    # Await cancellations
    await asyncio.gather(*[t for t in tasks_to_cancel if t], return_exceptions=True)
    log.info("Listener tasks cancelled.")

    # Cleanup pubsub objects and associated clients
    with contextlib.suppress(Exception):
         if pubsub_direct: await pubsub_direct.close(); log.info("Closed direct PubSub object.")
    # with contextlib.suppress(Exception): # Client might be closed via pool disconnect
    #      if redis_direct_client: await redis_direct_client.aclose()
    with contextlib.suppress(Exception):
         if pubsub_pattern: await pubsub_pattern.close(); log.info("Closed pattern PubSub object.")
    # with contextlib.suppress(Exception):
    #      if redis_pattern_client: await redis_pattern_client.aclose()

    # Main pool cleanup
    if redis_pool:
         log.info("Closing main Redis connection pool (from lifespan)...")
         with contextlib.suppress(Exception): await redis_pool.disconnect(inuse_connections=True); log.info("Main Redis connection pool closed.")
    log.info("Lifespan shutdown complete.")


# --- FastAPI App Instance ---
app = FastAPI(
    title="Flashbook Trading System API",
    description="API for submitting orders, getting market data, and real-time dashboard updates via WebSocket.",
    version="0.5.3", # Version bump for separate listeners
    lifespan=lifespan
)

# Mount Routes AFTER App Instance Creation
app.include_router(router, prefix="/api/v1")

# Define Root Endpoint AFTER App Instance Creation
@app.get("/")
async def read_root():
    """ Basic health check endpoint. """
    return {"message": "Flashbook API & WebSocket Service is running"}