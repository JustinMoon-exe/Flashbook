# market_simulator/tasks.py

import asyncio
import logging
import json
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import random
from typing import List, Dict

import redis.asyncio as redis
from redis.exceptions import ConnectionError as RedisConnectionError

from . import state # Access shared state (caches, locks, publisher, pause_event)
from .config import (
    AGENT_ACTION_INTERVAL_SECONDS, AGENT_STATUS_PUBLISH_INTERVAL_SECONDS,
    EXCHANGE_STATS_PUBLISH_INTERVAL_SECONDS, AGENT_STATUS_CHANNEL, EXCHANGE_STATS_CHANNEL
)
from .agent import Agent
import httpx

log = logging.getLogger(__name__)


# --- MODIFIED: Main Simulation Loop Task (Adds Pause Check) ---
async def simulation_loop(agents: list[Agent], http_client: httpx.AsyncClient):
    """Main loop where agents decide and act."""
    log.info(f"Starting simulation loop for {len(agents)} agents...")
    try:
        while True:
            try:
                # --- PAUSE CHECK ---
                if state.simulation_paused.is_set():
                    log.info("Simulation loop paused...")
                    await state.simulation_paused.wait() # Wait until event is cleared
                    log.info("Simulation loop resumed.")
                # -------------------

                start_time = asyncio.get_event_loop().time()

                agent_tasks = [agent.decide_and_act(http_client) for agent in agents]
                await asyncio.gather(*agent_tasks)

                end_time = asyncio.get_event_loop().time()
                elapsed = end_time - start_time
                wait_time = max(0, AGENT_ACTION_INTERVAL_SECONDS - elapsed)

                log.debug(f"Finished agent cycle in {elapsed:.3f}s. Waiting {wait_time:.3f}s...")
                # Allow sleep to be interrupted if paused immediately after a cycle
                await asyncio.wait_for(asyncio.sleep(wait_time), timeout=wait_time + 0.1)

            except asyncio.TimeoutError: continue # Expected if sleep finishes normally
            except asyncio.CancelledError: log.info("Simulation loop cancelled."); break
            except Exception as e: log.error(f"Error in simulation loop: {e}", exc_info=True); await asyncio.sleep(5)
    finally: log.info("Simulation loop finished.")


# --- MODIFIED: Background Task to Publish Stats (Adds Pause Check) ---
async def stats_publisher(agent_map: Dict[str, Agent]):
    """Periodically publishes agent status and exchange statistics."""
    log.info("[Stats Publisher] Starting...")
    last_exchange_publish_time = datetime.now(timezone.utc)
    for agent in agent_map.values():
        agent.last_status_publish_time = datetime.now(timezone.utc) - timedelta(seconds=random.uniform(0, AGENT_STATUS_PUBLISH_INTERVAL_SECONDS))

    try:
        while True:
            try:
                 # --- PAUSE CHECK ---
                if state.simulation_paused.is_set():
                    log.debug("Stats publisher paused...")
                    await state.simulation_paused.wait() # Wait until event is cleared
                    log.debug("Stats publisher resumed.")
                 # -------------------

                await asyncio.sleep(1.0) # Check timing frequently
                now = datetime.now(timezone.utc)

                if not state.redis_publisher: log.warning("[Stats] Redis publisher N/A."); await asyncio.sleep(4.0); continue

                # --- Publish Agent Statuses ---
                agents_to_publish = [a for a in agent_map.values() if now - a.last_status_publish_time >= timedelta(seconds=AGENT_STATUS_PUBLISH_INTERVAL_SECONDS)]
                if agents_to_publish:
                    log.debug(f"Publishing status for {len(agents_to_publish)} agents.")
                    for agent in agents_to_publish:
                        try:
                            current_bbo = state.market_bbo_cache.get(agent.symbol)
                            status_data = agent.get_status(current_bbo=current_bbo)
                            status_json = json.dumps(status_data)
                            await state.redis_publisher.publish(AGENT_STATUS_CHANNEL, status_json)
                            agent.last_status_publish_time = now
                            log.info(f"Published status {agent.agent_id} - Pos:{status_data['position']}, Trades:{status_data['trade_count']}, RealPNL:{status_data['realized_pnl']:.2f}, UnrealPNL:{status_data['unrealized_pnl']:.2f}")
                        except RedisConnectionError as e: log.error(f"Redis error publishing status for {agent.agent_id}: {e}")
                        except Exception as e: log.error(f"Error getting/publishing status for {agent.agent_id}: {e}", exc_info=True)

                # --- Publish Global Exchange Stats ---
                if now - last_exchange_publish_time >= timedelta(seconds=EXCHANGE_STATS_PUBLISH_INTERVAL_SECONDS):
                    current_trades = 0; current_value = Decimal("0.0")
                    async with state.exchange_stats_lock: current_trades = state.exchange_total_trades; current_value = state.exchange_total_value_traded
                    exchange_stats = {"timestamp": now.isoformat(), "total_trades": current_trades, "total_volume_value": float(current_value.quantize(Decimal("0.01")))}
                    exchange_stats_json = json.dumps(exchange_stats)
                    try:
                        await state.redis_publisher.publish(EXCHANGE_STATS_CHANNEL, exchange_stats_json)
                        log.info(f"Pub exchange stats: Trades={exchange_stats['total_trades']}, Val={exchange_stats['total_volume_value']:.2f}")
                        last_exchange_publish_time = now
                    except RedisConnectionError as e: log.error(f"Redis error publishing exchange stats: {e}")
                    except Exception as e: log.error(f"Error publishing exchange stats: {e}", exc_info=True)

            except asyncio.CancelledError: log.info("[Stats Publisher] Cancelled."); break
            except Exception as e: log.error(f"[Stats Publisher] Error in loop: {e}", exc_info=True); await asyncio.sleep(5)
    finally: log.info("[Stats Publisher] Shutting down.")

# --- monitor_tasks function remains the same ---
async def monitor_tasks(tasks: list[asyncio.Task | None]):
    """Periodically checks the status of essential running tasks."""
    # ... (same as before) ...
    log.info("[Monitor] Starting task monitor...")
    try:
        while True:
            await asyncio.sleep(30) # Check interval
            active_tasks = [task for task in tasks if task and not task.done()]
            if not active_tasks: log.info("[Monitor] All monitored essential tasks appear done. Exiting monitor."); break
            task_names = [t.get_name() for t in active_tasks if t.get_name()]; log.debug(f"[Monitor] Checking task status... {len(active_tasks)} essential tasks still running: {task_names}")
    except asyncio.CancelledError: log.info("[Monitor] Task monitor cancelled.")
    except Exception as e: log.error(f"[Monitor] Error in monitor loop: {e}", exc_info=True)
    finally: log.info("[Monitor] Task monitor stopped.")