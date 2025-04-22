# market_simulator/state.py

import asyncio
from decimal import Decimal
from typing import Dict, Deque
from collections import deque
import redis.asyncio as redis

from .config import TRADE_CACHE_SIZE # Import config value

# --- Shared Market & Exchange State Cache ---
market_bbo_cache: Dict[str, Dict] = {}
market_trade_cache: Dict[str, Deque] = {}

# --- Simulation Control State ---
simulation_paused = asyncio.Event() # Event to signal pause state (set=paused, clear=running)
simulation_paused.set() # Start paused by default? Or clear() to start running? Let's start running.
# simulation_paused.clear() # Uncomment to start running

# --- Global exchange statistics ---
exchange_total_trades: int = 0
exchange_total_value_traded: Decimal = Decimal("0.0")
exchange_stats_lock = asyncio.Lock() # Lock for updating global stats

# --- Submitted Orders Tracking ---
submitted_orders_map: Dict[str, Dict] = {}
submitted_orders_lock = asyncio.Lock() # Lock for accessing submitted orders map

# --- Redis Publisher Client ---
redis_publisher: redis.Redis | None = None # Set during startup