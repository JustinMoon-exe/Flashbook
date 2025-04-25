# market_simulator/state.py

import asyncio
from decimal import Decimal
from typing import Dict, Deque, Tuple 
from collections import deque
import redis.asyncio as redis
from datetime import datetime 

from .config import TRADE_CACHE_SIZE

market_bbo_cache: Dict[str, Dict] = {}
market_trade_cache: Dict[str, Deque] = {}

simulation_paused = asyncio.Event()
simulation_paused.clear() 


shocked_mid_price_override: Dict[str, Tuple[Decimal, datetime]] = {}
shock_override_lock = asyncio.Lock() 


exchange_total_trades: int = 0
exchange_total_value_traded: Decimal = Decimal("0.0")
exchange_stats_lock = asyncio.Lock()

submitted_orders_map: Dict[str, Dict] = {}
submitted_orders_lock = asyncio.Lock()

redis_publisher: redis.Redis | None = None 