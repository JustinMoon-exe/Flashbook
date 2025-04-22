# market_simulator/strategies.py

import random
import logging
from decimal import Decimal
from typing import TYPE_CHECKING, List, Dict, Tuple, Optional, Callable, Coroutine, Any
from datetime import datetime

if TYPE_CHECKING:
    import httpx
    from .agent import Agent # Import Agent for type hinting

# Import necessary modules using relative paths
from .config import (
    MM_DESIRED_SPREAD, MM_BASE_ORDER_QTY, MM_RISK_CHECK_PERCENT,
    MOMENTUM_WINDOW, MOMENTUM_THRESHOLD, MOMENTUM_BASE_ORDER_QTY,
    NOISE_BASE_ORDER_QTY, NOISE_TRADE_PROBABILITY, NOISE_RISK_CHECK_PERCENT
)
from .utils import safe_decimal, quantize_price

log = logging.getLogger(__name__)

# Type hint for strategy functions
StrategyFunction = Callable[['Agent', 'httpx.AsyncClient', Optional[Dict], List[Tuple[Decimal, int, datetime]]], Coroutine[Any, Any, List[Optional[Dict]]]]

# --- Strategy Implementations ---

async def strategy_noise(agent: 'Agent', http_client: 'httpx.AsyncClient', current_bbo: Optional[Dict], recent_trades: List) -> List[Optional[Dict]]:
    order_payloads = []
    if random.random() < NOISE_TRADE_PROBABILITY:
        side = random.choice(["buy", "sell"])
        mid_price = Decimal("100.0") if agent.symbol == "TEST" else Decimal("50.0") # Default mid
        if current_bbo:
            bid = safe_decimal(current_bbo.get('bid_price'))
            ask = safe_decimal(current_bbo.get('ask_price'))
            if bid and ask: mid_price = (bid + ask) / 2
            elif bid: mid_price = bid * Decimal("0.998")
            elif ask: mid_price = ask * Decimal("1.002")

        try:
            volatility = mid_price * Decimal("0.005") # 0.5% volatility
            price_offset = Decimal(random.uniform(float(-volatility), float(volatility)))
            price = quantize_price(mid_price + price_offset) or quantize_price(mid_price)
        except Exception: price = quantize_price(mid_price)

        if price is None:
            log.debug(f"A:{agent.agent_id} [N] Price calc failed.")
            return order_payloads

        base_qty = NOISE_BASE_ORDER_QTY
        quantity = max(1, int(base_qty * agent.risk_factor * random.uniform(0.5, 1.5)))

        estimated_cost = price * quantity
        if estimated_cost > agent.bankroll * NOISE_RISK_CHECK_PERCENT:
            log.debug(f"A:{agent.agent_id} [N] Skip, cost {estimated_cost:.2f} > {NOISE_RISK_CHECK_PERCENT*100}% bankroll")
            return order_payloads

        order_payload = {"symbol": agent.symbol, "side": side, "price": str(price), "quantity": quantity}
        log.info(f"A:{agent.agent_id} [N] Attempt {side} {quantity}@{price} (R:{agent.risk_factor:.1f})")
        order_payloads.append(order_payload)

    return order_payloads


async def strategy_market_maker(agent: 'Agent', http_client: 'httpx.AsyncClient', current_bbo: Optional[Dict], recent_trades: List) -> List[Optional[Dict]]:
    order_payloads = []
    if not current_bbo:
        log.debug(f"A:{agent.agent_id} [MM] No BBO for {agent.symbol}.")
        return order_payloads

    symbol = agent.symbol
    desired_spread = MM_DESIRED_SPREAD.get(symbol, Decimal("0.10"))
    base_qty = MM_BASE_ORDER_QTY.get(symbol, 10)
    order_qty = max(1, int(base_qty * agent.risk_factor * 2))

    bid = safe_decimal(current_bbo.get('bid_price'))
    ask = safe_decimal(current_bbo.get('ask_price'))
    place_bid_price, place_ask_price = None, None

    if bid and ask:
        mid = (bid + ask) / 2
        half_spread = desired_spread / 2
        place_bid_price = quantize_price(mid - half_spread)
        place_ask_price = quantize_price(mid + half_spread)
    elif bid:
        place_bid_price = quantize_price(bid - desired_spread / 2)
        place_ask_price = quantize_price(bid + desired_spread / 2)
    elif ask:
        place_bid_price = quantize_price(ask - desired_spread / 2)
        place_ask_price = quantize_price(ask + desired_spread / 2)
    else:
        mid_price = Decimal("100.0") if symbol == "TEST" else Decimal("50.0")
        place_bid_price = quantize_price(mid_price - desired_spread / 2)
        place_ask_price = quantize_price(mid_price + desired_spread / 2)

    if bid and place_ask_price and place_ask_price <= bid: place_ask_price = quantize_price(bid + Decimal("0.01"))
    if ask and place_bid_price and place_bid_price >= ask: place_bid_price = quantize_price(ask - Decimal("0.01"))

    place_bid_price = quantize_price(place_bid_price)
    place_ask_price = quantize_price(place_ask_price)

    if place_bid_price:
        required_capital = place_bid_price * order_qty
        if required_capital > agent.bankroll * MM_RISK_CHECK_PERCENT:
            log.debug(f"A:{agent.agent_id} [MM] Skip bid, cap {required_capital:.2f} > {MM_RISK_CHECK_PERCENT*100}% bankroll")
            place_bid_price = None

    # TODO: Cancel existing orders before placing new ones? Or let them expire/fill?

    if place_bid_price:
        bid_payload = {"symbol": symbol, "side": "buy", "price": str(place_bid_price), "quantity": order_qty}
        log.info(f"A:{agent.agent_id} [MM] Q BUY {order_qty}@{place_bid_price}")
        order_payloads.append(bid_payload)
    if place_ask_price:
        ask_payload = {"symbol": symbol, "side": "sell", "price": str(place_ask_price), "quantity": order_qty}
        log.info(f"A:{agent.agent_id} [MM] Q SELL {order_qty}@{place_ask_price}")
        order_payloads.append(ask_payload)

    return order_payloads


async def strategy_momentum(agent: 'Agent', http_client: 'httpx.AsyncClient', current_bbo: Optional[Dict], recent_trades: List[Tuple[Decimal, int, datetime]]) -> List[Optional[Dict]]:
    order_payloads = []
    if not recent_trades or len(recent_trades) < MOMENTUM_WINDOW:
        log.debug(f"A:{agent.agent_id} [M] Skip, trades {len(recent_trades)} < {MOMENTUM_WINDOW}")
        return order_payloads

    try:
        prices = [trade[0] for trade in recent_trades[-MOMENTUM_WINDOW:]]
        last_price = prices[-1]
        avg_price = sum(prices) / len(prices)
        price_diff = last_price - avg_price
        log.debug(f"A:{agent.agent_id} [M] LastP={last_price:.2f}, AvgP={avg_price:.2f}, Diff={price_diff:.2f}")
    except Exception as e:
        log.error(f"A:{agent.agent_id} [M] Calc error: {e}")
        return order_payloads

    side, target_price = None, None
    if price_diff > MOMENTUM_THRESHOLD: # Use updated threshold
        side = "buy"
        ask_price = safe_decimal(current_bbo.get('ask_price')) if current_bbo else None
        target_price = ask_price or last_price
        target_price = target_price * Decimal("1.001") if target_price else None
    elif price_diff < -MOMENTUM_THRESHOLD:
        side = "sell"
        bid_price = safe_decimal(current_bbo.get('bid_price')) if current_bbo else None
        target_price = bid_price or last_price
        target_price = target_price * Decimal("0.999") if target_price else None

    if side and target_price:
        price = quantize_price(target_price)
        if price is None: return order_payloads

        base_qty = MOMENTUM_BASE_ORDER_QTY # Use updated base qty
        quantity = max(1, int(base_qty * agent.risk_factor))

        estimated_cost = price * quantity
        if estimated_cost > agent.bankroll * Decimal("0.15"): # Risk check %
            log.debug(f"A:{agent.agent_id} [M] Skip, cost {estimated_cost:.2f} > 15% bankroll")
            return order_payloads

        order_payload = {"symbol": agent.symbol, "side": side, "price": str(price), "quantity": quantity}
        log.info(f"A:{agent.agent_id} [M] Trend ({price_diff:+.2f}). Attempt {side} {quantity}@{price}")
        order_payloads.append(order_payload)
    else:
        log.debug(f"A:{agent.agent_id} [M] No signal (Diff:{price_diff:.2f} vs Thresh:{MOMENTUM_THRESHOLD}).")

    return order_payloads

# --- Strategy Function Mapping ---
STRATEGY_FUNCTIONS: Dict[str, StrategyFunction] = {
    "noise": strategy_noise,
    "market_maker": strategy_market_maker,
    "momentum": strategy_momentum
}