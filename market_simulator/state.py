# market_simulator/state.py

import asyncio
from decimal import Decimal
from typing import Dict, Deque, Tuple # Added Tuple
from collections import deque
import redis.asyncio as redis
from datetime import datetime # Added datetime

from .config import TRADE_CACHE_SIZE

# --- Shared Market & Exchange State Cache ---
market_bbo_cache: Dict[str, Dict] = {}
market_trade_cache: Dict[str, Deque] = {}

# --- Simulation Control State ---
simulation_paused = asyncio.Event()
simulation_paused.clear() # Start running

# *** NEW: Market Event Override State ***
# Stores { symbol: (target_mid_price, expiry_timestamp_utc) }
shocked_mid_price_override: Dict[str, Tuple[Decimal, datetime]] = {}
shock_override_lock = asyncio.Lock() # Lock for accessing the override dict
# *** END NEW ***

# --- Global exchange statistics ---
exchange_total_trades: int = 0
exchange_total_value_traded: Decimal = Decimal("0.0")
exchange_stats_lock = asyncio.Lock()

# --- Submitted Orders Tracking ---
submitted_orders_map: Dict[str, Dict] = {}
submitted_orders_lock = asyncio.Lock()

# --- Redis Publisher Client ---
redis_publisher: redis.Redis | None = None # Set during startup