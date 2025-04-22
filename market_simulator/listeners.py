# market_simulator/listeners.py

import asyncio
import json
import logging
from decimal import Decimal
from datetime import datetime, timezone, timedelta # Import timedelta
from collections import deque
from typing import Dict, Any

import redis.asyncio as redis
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError

from . import state # Access shared state (caches, locks, agent map, price overrides)
from . import config # Import config for channel names, INITIAL_AGENT_CONFIG, SHOCK_EXPIRY
from .utils import safe_decimal, quantize_price # Import quantize_price
from .agent import Agent

log = logging.getLogger(__name__)

# --- Market Data Listener (Only updates caches) ---
async def market_data_listener(pubsub: redis.client.PubSub, agent_map: Dict[str, Agent]):
    log.info("[Market Listener] Starting...")
    try:
        while True:
            try:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not message: await asyncio.sleep(0.01); continue

                msg_type=message.get("type"); channel_raw=message.get("channel"); data_raw=message.get("data"); pattern_raw=message.get("pattern")
                channel = channel_raw.decode('utf-8') if isinstance(channel_raw, bytes) else channel_raw
                pattern = pattern_raw.decode('utf-8') if isinstance(pattern_raw, bytes) else pattern_raw

                log.debug(f"[Market Listener] Raw: T={msg_type}, Chan='{channel}', Pat='{pattern}'")

                # BBO Update Processing
                if msg_type == "pmessage" and pattern == config.BBO_CHANNEL_PATTERN:
                    try:
                        data_str=data_raw.decode('utf-8'); bbo_data=json.loads(data_str); symbol=bbo_data.get("symbol")
                        if symbol: bid_p = safe_decimal(bbo_data.get('bid_price')); ask_p = safe_decimal(bbo_data.get('ask_price')); state.market_bbo_cache[symbol] = {'bid_price': bid_p,'bid_qty': bbo_data.get('bid_qty'),'ask_price': ask_p,'ask_qty': bbo_data.get('ask_qty'),'timestamp': bbo_data.get('timestamp')}; log.debug(f"Updated BBO cache for {symbol}")
                    except Exception as e: log.error(f"[Market] BBO Error: {e}. Data: {data_raw}", exc_info=True)

                # Trade Update Processing (caches trades, updates GLOBAL stats, triggers agent fills)
                elif msg_type == "message" and channel == config.TRADE_CHANNEL:
                    try:
                        data_str = data_raw.decode('utf-8'); trade_data = json.loads(data_str); symbol = trade_data.get("symbol")
                        if symbol:
                            price = safe_decimal(trade_data.get('price')); qty = int(trade_data['quantity']) if trade_data.get('quantity') else 0; ts_str = trade_data.get('timestamp'); ts = datetime.fromisoformat(ts_str.replace('Z','+00:00')) if ts_str else datetime.now(timezone.utc)
                            if price and qty > 0:
                                if symbol not in state.market_trade_cache: state.market_trade_cache[symbol] = deque(maxlen=config.TRADE_CACHE_SIZE)
                                state.market_trade_cache[symbol].append((price, qty, ts)); log.debug(f"Added trade to cache for {symbol}")
                                async with state.exchange_stats_lock: state.exchange_total_trades += 1; state.exchange_total_value_traded += (price * Decimal(qty))
                                # Agent Fill Processing
                                taker_id = trade_data.get("taker_order_id"); maker_id = trade_data.get("maker_order_id"); trade_id_short = trade_data.get('trade_id','unknown')[:6]; log.info(f"[Market] Trade {trade_id_short} Recvd - TakerOID:{taker_id[-6:] if taker_id else 'N/A'}, MakerOID:{maker_id[-6:] if maker_id else 'N/A'}")
                                taker_info = None; maker_info = None
                                async with state.submitted_orders_lock: taker_info = state.submitted_orders_map.get(taker_id); maker_info = state.submitted_orders_map.get(maker_id)
                                # log.info(f"[Market] Trade {trade_id_short} Map Lookup: Taker:{bool(taker_info)}, Maker:{bool(maker_info)}") # Reduce noise
                                agent_map_snapshot = agent_map.copy()
                                agent_to_update_taker = agent_map_snapshot.get(taker_info["agent_id"]) if taker_info else None
                                agent_to_update_maker = agent_map_snapshot.get(maker_info["agent_id"]) if maker_info else None
                                # log.info(f"[Market] Trade {trade_id_short} Agent Lookup: Taker:{bool(agent_to_update_taker)}, Maker:{bool(agent_to_update_maker)}") # Reduce noise
                                if agent_to_update_taker: agent_to_update_taker.update_on_fill(taker_id, trade_data["price"], qty, is_taker=True)
                                elif taker_id: log.warning(f"[Market] Taker Agent NOT FOUND for OID: {taker_id[-6:]}")
                                if agent_to_update_maker: agent_to_update_maker.update_on_fill(maker_id, trade_data["price"], qty, is_taker=False)
                                elif maker_id: log.warning(f"[Market] Maker Agent NOT FOUND for OID: {maker_id[-6:]}")
                    except Exception as e: log.error(f"[Market] Trade Processing Error: {e}. Data: {data_raw}", exc_info=True)
                await asyncio.sleep(0.01)
            except RedisTimeoutError: continue
            except RedisConnectionError as e: log.error(f"[Market] Connection Error: {e}. Retrying..."); await asyncio.sleep(5); continue # Simple retry for now
            except asyncio.CancelledError: log.info("[Market Listener] Cancelled."); break
            except Exception as e: log.error(f"[Market Listener] Loop Error: {e}", exc_info=True); await asyncio.sleep(1)
    finally: log.info("[Market Listener] Shutting down.")


# --- MODIFIED: Control Listener Handles Reset/Pause/Market Events ---
async def control_listener(pubsub: redis.client.PubSub, agent_map: Dict[str, Agent]):
    """Listens for control commands AND market events."""
    log.info("[Control/Event Listener] Starting...")
    try:
        # Subscription happened in main()

        while True:
            try:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not message: await asyncio.sleep(0.01); continue

                channel = message["channel"].decode('utf-8')
                data_raw = message["data"]
                log.debug(f"[Ctrl/Event] Received on '{channel}'")

                # --- Handle Market Event Channel ---
                if channel == config.MARKET_EVENTS_CHANNEL:
                    try:
                        event_data = json.loads(data_raw.decode('utf-8'))
                        symbol = event_data.get("symbol")
                        shift = event_data.get("percent_shift")
                        if symbol and shift is not None:
                            log.warning(f">>> MARKET EVENT received via Redis: Symbol={symbol}, Shift={shift*100:.2f}% <<<")

                            # Calculate new target mid-price
                            current_mid = Decimal("100.0") if symbol == "ABC" else Decimal("50.0") # Default
                            current_bbo = state.market_bbo_cache.get(symbol)
                            if current_bbo:
                                bid = safe_decimal(current_bbo.get('bid_price'))
                                ask = safe_decimal(current_bbo.get('ask_price'))
                                if bid and ask: current_mid = (bid + ask) / 2
                                elif bid: current_mid = bid
                                elif ask: current_mid = ask
                                else: log.warning(f"[Event] No BBO prices for {symbol}, using default {current_mid}")
                            else:
                                 log.warning(f"[Event] No BBO cache for {symbol}, using default {current_mid}")

                            new_mid_price = quantize_price(current_mid * (Decimal(1) + Decimal(str(shift))))
                            expiry_time = datetime.now(timezone.utc) + timedelta(seconds=config.SHOCK_EXPIRY_DURATION_SECONDS)

                            if new_mid_price:
                                async with state.shock_override_lock:
                                    state.shocked_mid_price_override[symbol] = (new_mid_price, expiry_time)
                                log.info(f"[Event] Set price override for {symbol} to {new_mid_price:.2f} until {expiry_time.isoformat()}")
                            else:
                                log.error(f"[Event] Failed to calculate valid new mid price for {symbol}")

                        else: log.warning(f"[Event] Invalid market event payload: {event_data}")
                    except Exception as e: log.error(f"[Event] Error processing market event: {e}. Data: {data_raw}", exc_info=True)
                    continue # Handled market event

                # --- Handle Simulator Control Channel ---
                if channel == config.SIMULATOR_CONTROL_CHANNEL:
                    control_cmd = {}
                    try:
                        data_str = data_raw.decode('utf-8')
                        control_cmd = json.loads(data_str)
                        log.info(f"Rcvd ctrl cmd: {control_cmd}")
                        cmd_type = control_cmd.get("command")

                        if cmd_type == "set_pause": log.warning(">>> SIM PAUSED <<<"); state.simulation_paused.set(); continue
                        elif cmd_type == "set_resume": log.warning(">>> SIM RESUMED <<<"); state.simulation_paused.clear(); continue
                        elif cmd_type == "reset_simulator":
                            log.warning(">>> SIM RESET initiated <<<"); state.simulation_paused.set(); await asyncio.sleep(0.1)
                            async with state.exchange_stats_lock: state.exchange_total_trades = 0; state.exchange_total_value_traded = Decimal("0.0")
                            state.market_bbo_cache.clear(); state.market_trade_cache.clear();
                            async with state.submitted_orders_lock: state.submitted_orders_map.clear()
                            async with state.shock_override_lock: state.shocked_mid_price_override.clear() # Clear price shocks too
                            log.info("[Reset] Cleared caches & overrides.")
                            agent_id_counter = 1
                            for symbol, agent_configs in config.INITIAL_AGENT_CONFIG.items():
                                 for initial_conf in agent_configs:
                                     agent_id = f"Agent_{agent_id_counter}"
                                     if agent_id in agent_map: agent_map[agent_id].reset_state(initial_conf)
                                     else: log.error(f"[Reset] Agent {agent_id} not in map!")
                                     agent_id_counter += 1
                            log.info("[Reset] Agents reset."); log.warning(">>> SIM RESET COMPLETE (Remains PAUSED) <<<"); continue

                        # Handle Agent Parameter Commands
                        agent_id = control_cmd.get("agent_id"); param = control_cmd.get("parameter"); value = control_cmd.get("value")
                        if agent_id and param and value is not None:
                            agent = agent_map.get(agent_id)
                            if agent:
                                log.info(f"Applying control '{param}'='{value}' to Agent {agent_id}")
                                if param == "set_active": agent.set_active(bool(value)) if isinstance(value, bool) else log.warning("Invalid bool")
                                elif param == "change_strategy": agent.set_strategy(str(value)) if isinstance(value, str) else log.warning("Invalid str")
                                elif param == "set_risk": agent.set_risk_factor(float(value)) if isinstance(value, (int, float)) else log.warning("Invalid float")
                                elif param == "set_bankroll": agent.set_bankroll(float(value)) if isinstance(value, (int, float)) else log.warning("Invalid float")
                                else: log.warning(f"Unknown param '{param}'")
                            else: log.warning(f"Agent '{agent_id}' not found.")
                        elif not cmd_type: log.warning(f"Invalid agent control format: {control_cmd}")

                    except json.JSONDecodeError as e: log.error(f"Error decoding ctrl cmd JSON: {e}. Cmd: {data_raw}")
                    except Exception as e: log.error(f"Error proc ctrl cmd: {e}. Cmd: {control_cmd}", exc_info=True)

                await asyncio.sleep(0.01)
            except RedisTimeoutError: continue
            except RedisConnectionError as e: log.error(f"[Ctrl/Event] Connection Error: {e}. Retrying..."); await asyncio.sleep(5); continue # Simple retry
            except asyncio.CancelledError: log.info("[Ctrl/Event Listener] Cancelled."); break
            except Exception as e: log.error(f"[Ctrl/Event Listener] Loop Error: {e}", exc_info=True); await asyncio.sleep(1)
    finally: log.info("[Ctrl/Event Listener] Shutting down.")