# market_simulator/simulator.py

import asyncio
import logging
import contextlib
from typing import List, Dict

import httpx
import redis.asyncio as redis

from . import config
from . import state
from .agent import Agent
from .listeners import market_data_listener, control_listener
from .tasks import simulation_loop, stats_publisher, monitor_tasks

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("MarketSimulatorMain")


async def main():
    log.info("Initializing Market Simulator...")
    agents_list: List[Agent] = []
    agents_map: Dict[str, Agent] = {}
    agent_id_counter = 1

    for symbol, agent_configs in config.AGENT_CONFIG.items():
        for agent_conf in agent_configs:
            agent_id = f"Agent_{agent_id_counter}"
            try:
                agent = Agent(
                    agent_id=agent_id,
                    symbol=symbol.upper(),
                    initial_strategy=agent_conf["type"],
                    risk_factor=agent_conf["risk"],
                    bankroll=agent_conf["bankroll"],
                )
                agents_list.append(agent)
                agents_map[agent_id] = agent
                agent_id_counter += 1
            except Exception as e:
                log.error(f"Failed to create agent {agent_id} with config {agent_conf}: {e}", exc_info=True)

    if not agents_list:
        log.critical("No agents created. Exiting.")
        return

    log.info(f"Created {len(agents_list)} agents.")

    listener_task = control_task = stats_task = simulation_task = monitor_task = None
    redis_market_listener_client: redis.Redis | None = None
    redis_control_listener_client: redis.Redis | None = None
    market_pubsub: redis.client.PubSub | None = None
    control_pubsub: redis.client.PubSub | None = None
    http_client: httpx.AsyncClient | None = None
    tasks_to_await = []

    try:
        try:
            log.info("Connecting Market Redis Listener...")
            redis_market_listener_client = redis.from_url(config.REDIS_URL, decode_responses=False)
            await redis_market_listener_client.ping()
            market_pubsub = redis_market_listener_client.pubsub(ignore_subscribe_messages=True)
            await market_pubsub.psubscribe(config.BBO_CHANNEL_PATTERN)
            await market_pubsub.subscribe(config.TRADE_CHANNEL)
            log.info(f"[Main] Market PubSub subscribed to p:{config.BBO_CHANNEL_PATTERN}, c:{config.TRADE_CHANNEL}")
            listener_task = asyncio.create_task(
                market_data_listener(market_pubsub, agents_map),
                name="MarketDataListener",
            )
            tasks_to_await.append(listener_task)
        except Exception as e:
            log.error(f"FAILED to setup Market Redis listener: {e}", exc_info=True)
            if redis_market_listener_client:
                await redis_market_listener_client.aclose()

        try:
            log.info("Connecting Control Redis Listener...")
            redis_control_listener_client = redis.from_url(config.REDIS_URL, decode_responses=False)
            await redis_control_listener_client.ping()
            control_pubsub = redis_control_listener_client.pubsub(ignore_subscribe_messages=True)
            await control_pubsub.subscribe(config.SIMULATOR_CONTROL_CHANNEL)
            log.info(f"[Main] Control PubSub subscribed to {config.SIMULATOR_CONTROL_CHANNEL}")
            control_task = asyncio.create_task(
                control_listener(control_pubsub, agents_map),
                name="ControlListener",
            )
            tasks_to_await.append(control_task)
        except Exception as e:
            log.error(f"FAILED to setup Control Redis listener: {e}", exc_info=True)
            if redis_control_listener_client:
                await redis_control_listener_client.aclose()

        try:
            log.info("Connecting Redis Publisher...")
            state.redis_publisher = redis.from_url(config.REDIS_URL, decode_responses=True)
            await state.redis_publisher.ping()
            log.info("Redis Publisher Connected.")
            stats_task = asyncio.create_task(stats_publisher(agents_map), name="StatsPublisher")
            tasks_to_await.append(stats_task)
        except Exception as e:
            log.error(f"FAILED connect Redis publisher: {e}")
            state.redis_publisher = None
            if stats_task:
                stats_task.cancel()
                stats_task = None

        if not state.redis_publisher:
            log.warning("Stats Publisher failed to connect. Stats will not be sent.")
        if not listener_task:
            log.warning("Market Listener failed. Agent state might not update correctly.")
        if not control_task:
            log.warning("Control Listener failed. Agents cannot be controlled via UI.")

        http_client = httpx.AsyncClient()
        simulation_task = asyncio.create_task(simulation_loop(agents_list, http_client), name="SimulationLoop")
        tasks_to_await.append(simulation_task)

        monitor_task = asyncio.create_task(monitor_tasks(tasks_to_await), name="MonitorTasks")

        log.info("Running main event loop - waiting for tasks to complete...")
        essential_tasks = [t for t in tasks_to_await if t]
        if not essential_tasks:
            log.critical("No essential tasks started successfully. Exiting.")
            if http_client:
                await http_client.aclose()
            for client in [redis_market_listener_client, redis_control_listener_client, state.redis_publisher]:
                if client:
                    await client.aclose()
            return

        done, pending = await asyncio.wait(essential_tasks, return_when=asyncio.FIRST_COMPLETED)
        log.warning(f"An essential task completed or failed. Initiating shutdown. Done: {len(done)}, Pending: {len(pending)}")
        for task in done:
            try:
                task.result()
                log.info(f"Task {task.get_name()} completed normally.")
            except asyncio.CancelledError:
                log.info(f"Task {task.get_name()} was cancelled.")
            except Exception as e:
                log.error(f"Task {task.get_name()} failed with exception: {e}", exc_info=True)

    except asyncio.CancelledError:
        log.info("Main execution task cancelled.")
    except Exception as e:
        log.error(f"Unhandled error during main execution: {e}", exc_info=True)
    finally:
        log.info("Initiating graceful shutdown...")
        all_tasks = [t for t in tasks_to_await + [monitor_task] if t]
        for task in all_tasks:
            if not task.done():
                task.cancel()
        try:
            results = await asyncio.wait_for(asyncio.gather(*all_tasks, return_exceptions=True), timeout=5.0)
            log.info(f"Gather results after cancellation: {results}")
        except asyncio.TimeoutError:
            log.warning("Timeout waiting for tasks to cancel during shutdown.")
        except Exception as e:
            log.error(f"Error during task gathering on shutdown: {e}")

        if http_client:
            with contextlib.suppress(Exception):
                await http_client.aclose()
                log.info("HTTP client closed.")

        log.info("Cleaning up Redis connections...")
        for client in [redis_market_listener_client, redis_control_listener_client, state.redis_publisher]:
            if client:
                try:
                    await client.aclose()
                    log.debug(f"Closed Redis client explicitly: {client}")
                except Exception as e:
                    log.error(f"Error closing Redis client {client}: {e}")

        log.info("Market Simulator finished.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Simulator stopped by user (KeyboardInterrupt).")
    except Exception as e:
        log.critical(f"Unhandled exception at top level: {e}", exc_info=True)
