# market_simulator/listeners.py

import asyncio
import json
import logging
from decimal import Decimal
from datetime import datetime, timezone
from collections import deque
from typing import Dict, Any

import redis.asyncio as redis
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError

# Use relative imports for modules within the package
from . import state # Access shared state (caches, locks, agent map)
from .config import (
    BBO_CHANNEL_PATTERN, TRADE_CHANNEL, SIMULATOR_CONTROL_CHANNEL, TRADE_CACHE_SIZE
)
from .utils import safe_decimal
from .agent import Agent # Import Agent for type hinting

log = logging.getLogger(__name__)

# --- Redis Listener for Market Data ---
async def market_data_listener(pubsub: redis.client.PubSub, agent_map: Dict[str, Agent]):
    """Listens to BBO and Trade channels, updates local caches and agent state."""
    log.info("[Market Listener] Starting...")
    try:
        # Subscription should have happened in main() before starting this task

        while True:
            try:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not message:
                    await asyncio.sleep(0.01)
                    continue

                msg_type = message.get("type")
                channel_raw = message.get("channel")
                data_raw = message.get("data")
                pattern_raw = message.get("pattern")

                channel = channel_raw.decode('utf-8') if isinstance(channel_raw, bytes) else channel_raw
                pattern = pattern_raw.decode('utf-8') if isinstance(pattern_raw, bytes) else pattern_raw

                log.debug(f"[Market Listener] Raw: T={msg_type}, Chan='{channel}', Pat='{pattern}'")

                # --- BBO Update Processing ---
                if msg_type == "pmessage" and pattern == BBO_CHANNEL_PATTERN:
                    try:
                        data_str = data_raw.decode('utf-8')
                        bbo_data = json.loads(data_str)
                        symbol = bbo_data.get("symbol")
                        if symbol:
                            bid_p = safe_decimal(bbo_data.get('bid_price'))
                            ask_p = safe_decimal(bbo_data.get('ask_price'))
                            # Update shared state cache
                            state.market_bbo_cache[symbol] = {
                                'bid_price': bid_p,
                                'bid_qty': bbo_data.get('bid_qty'),
                                'ask_price': ask_p,
                                'ask_qty': bbo_data.get('ask_qty'),
                                'timestamp': bbo_data.get('timestamp')
                            }
                            log.debug(f"Updated BBO cache for {symbol}")
                    except json.JSONDecodeError as e: log.error(f"[Market] BBO JSON Decode Error: {e}. Data: '{data_raw}'")
                    except Exception as e: log.error(f"[Market] BBO Processing Error: {e}. Data: {data_raw}", exc_info=True)

                # --- Trade Update Processing ---
                elif msg_type == "message" and channel == TRADE_CHANNEL:
                    try:
                        data_str = data_raw.decode('utf-8')
                        trade_data = json.loads(data_str)
                        symbol = trade_data.get("symbol")
                        if symbol:
                            price = safe_decimal(trade_data.get('price'))
                            qty = int(trade_data['quantity']) if trade_data.get('quantity') else 0
                            ts_str = trade_data.get('timestamp')
                            ts = datetime.fromisoformat(ts_str.replace('Z','+00:00')) if ts_str else datetime.now(timezone.utc)

                            if price and qty > 0:
                                # Update trade cache (shared state)
                                if symbol not in state.market_trade_cache:
                                    state.market_trade_cache[symbol] = deque(maxlen=TRADE_CACHE_SIZE)
                                state.market_trade_cache[symbol].append((price, qty, ts))
                                log.debug(f"Added trade to cache for {symbol}")

                                # Update global stats (shared state)
                                async with state.exchange_stats_lock:
                                    state.exchange_total_trades += 1
                                    state.exchange_total_value_traded += (price * Decimal(qty))

                                # --- Agent Fill Processing ---
                                taker_id = trade_data.get("taker_order_id")
                                maker_id = trade_data.get("maker_order_id")
                                trade_id_short = trade_data.get('trade_id','unknown')[:6]
                                log.info(f"[Market] Trade {trade_id_short} Recvd - TakerOID:{taker_id[-6:] if taker_id else 'N/A'}, MakerOID:{maker_id[-6:] if maker_id else 'N/A'}")

                                taker_info = None; maker_info = None
                                # Access shared submitted orders map under lock
                                async with state.submitted_orders_lock:
                                    taker_info = state.submitted_orders_map.get(taker_id)
                                    maker_info = state.submitted_orders_map.get(maker_id)
                                log.info(f"[Market] Trade {trade_id_short} Order Map Lookup: TakerInfo Found: {bool(taker_info)}, MakerInfo Found: {bool(maker_info)}")

                                agent_map_snapshot = agent_map.copy() # Use snapshot
                                agent_to_update_taker = agent_map_snapshot.get(taker_info["agent_id"]) if taker_info else None
                                agent_to_update_maker = agent_map_snapshot.get(maker_info["agent_id"]) if maker_info else None
                                log.info(f"[Market] Trade {trade_id_short} Agent Lookup: TakerAgent Found: {bool(agent_to_update_taker)}, MakerAgent Found: {bool(agent_to_update_maker)}")

                                # Call update_on_fill for relevant agents
                                if agent_to_update_taker:
                                    log.info(f"[Market] Trade {trade_id_short} Calling update_on_fill for Taker Agent: {agent_to_update_taker.agent_id} (OID: {taker_id[-6:]})")
                                    agent_to_update_taker.update_on_fill(taker_id, trade_data["price"], qty, is_taker=True)
                                elif taker_id:
                                     log.warning(f"[Market] Trade {trade_id_short} Taker Agent NOT FOUND for OID: {taker_id[-6:]} (Info from map: {taker_info})")

                                if agent_to_update_maker:
                                    log.info(f"[Market] Trade {trade_id_short} Calling update_on_fill for Maker Agent: {agent_to_update_maker.agent_id} (OID: {maker_id[-6:]})")
                                    agent_to_update_maker.update_on_fill(maker_id, trade_data["price"], qty, is_taker=False)
                                elif maker_id:
                                    log.warning(f"[Market] Trade {trade_id_short} Maker Agent NOT FOUND for OID: {maker_id[-6:]} (Info from map: {maker_info})")

                    except json.JSONDecodeError as e: log.error(f"[Market] Trade JSON Decode Error: {e}. Data: '{data_raw}'")
                    except Exception as e: log.error(f"[Market] Trade Processing Error: {e}. Data: {data_raw}", exc_info=True)

                await asyncio.sleep(0.01) # Yield control

            except RedisTimeoutError: continue
            except RedisConnectionError as e:
                log.error(f"[Market] Connection Error: {e}. Attempting resubscribe...")
                await asyncio.sleep(5)
                try:
                     log.warning("[Market Listener] Attempting simple re-subscribe/p-subscribe after connection error...")
                     await pubsub.psubscribe(BBO_CHANNEL_PATTERN)
                     await pubsub.subscribe(TRADE_CHANNEL)
                     log.info("[Market Listener] Re-subscribed successfully after connection error.")
                except Exception as resub_e:
                     log.error(f"[Market Listener] Failed to resubscribe after connection error: {resub_e}")
                     await asyncio.sleep(10)
                continue
            except asyncio.CancelledError:
                log.info("[Market Listener] Cancelled.")
                break
            except Exception as e:
                log.error(f"[Market Listener] Loop Error: {e}", exc_info=True)
                await asyncio.sleep(1)
    finally:
        log.info("[Market Listener] Shutting down.")

# --- Redis Listener for Control Messages ---
async def control_listener(pubsub: redis.client.PubSub, agent_map: Dict[str, Agent]):
    """Listens for control commands and updates the corresponding agent."""
    log.info("[Control Listener] Starting...")
    try:
        # Subscription should have happened in main()

        while True:
            try:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message.get("type") == "message":
                    channel = message["channel"].decode('utf-8')
                    data_raw = message["data"]
                    log.debug(f"[Control] Received on '{channel}'")

                    if channel == SIMULATOR_CONTROL_CHANNEL:
                        control_cmd = {}
                        try:
                            data_str = data_raw.decode('utf-8')
                            control_cmd = json.loads(data_str)
                            log.info(f"Rcvd ctrl cmd: {control_cmd}")

                            agent_id = control_cmd.get("agent_id")
                            param = control_cmd.get("parameter")
                            value = control_cmd.get("value")

                            if agent_id and param and value is not None:
                                agent = agent_map.get(agent_id)
                                if agent:
                                    log.info(f"Applying control '{param}'='{value}' to Agent {agent_id}")
                                    if param == "set_active": agent.set_active(bool(value)) if isinstance(value, bool) else log.warning(f"Invalid bool value for set_active: {value}")
                                    elif param == "change_strategy": agent.set_strategy(str(value)) if isinstance(value, str) else log.warning(f"Invalid str value for change_strategy: {value}")
                                    elif param == "set_risk": agent.set_risk_factor(float(value)) if isinstance(value, (int, float)) else log.warning(f"Invalid float value for set_risk: {value}")
                                    elif param == "set_bankroll": agent.set_bankroll(float(value)) if isinstance(value, (int, float)) else log.warning(f"Invalid float value for set_bankroll: {value}")
                                    else: log.warning(f"Unknown control param '{param}' for Agent {agent_id}")
                                else: log.warning(f"Agent '{agent_id}' not found for control command.")
                            else: log.warning(f"Invalid control cmd format/missing fields: {control_cmd}")
                        except json.JSONDecodeError as e: log.error(f"Error decoding ctrl cmd JSON: {e}. Cmd: {data_raw}")
                        except Exception as e: log.error(f"Error proc ctrl cmd: {e}. Cmd: {control_cmd}", exc_info=True)

                await asyncio.sleep(0.01) # Yield control

            except RedisTimeoutError: continue
            except RedisConnectionError as e:
                log.error(f"[Control] Connection Error: {e}. Attempting resubscribe...")
                await asyncio.sleep(5)
                try:
                     log.warning("[Control Listener] Attempting simple re-subscribe after connection error...")
                     await pubsub.subscribe(SIMULATOR_CONTROL_CHANNEL)
                     log.info("[Control Listener] Resubscribed successfully after connection error.")
                except Exception as resub_e:
                     log.error(f"[Control Listener] Failed to resubscribe after connection error: {resub_e}")
                     await asyncio.sleep(10)
                continue
            except asyncio.CancelledError:
                log.info("[Control Listener] Cancelled.")
                break
            except Exception as e:
                log.error(f"[Control Listener] Loop Error: {e}", exc_info=True)
                await asyncio.sleep(1)
    finally:
        log.info("[Control Listener] Shutting down.")