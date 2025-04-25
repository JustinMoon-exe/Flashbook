# market_simulator/strategies.py

import random
import logging
from decimal import Decimal
from typing import TYPE_CHECKING, List, Dict, Tuple, Optional, Callable, Coroutine, Any
from datetime import datetime, timezone

if TYPE_CHECKING:
    import httpx
    from .agent import Agent

from . import state
from .config import (
    MM_DESIRED_SPREAD, MM_BASE_ORDER_QTY, MM_RISK_CHECK_PERCENT,
    MOMENTUM_WINDOW, MOMENTUM_THRESHOLD, MOMENTUM_BASE_ORDER_QTY,
    NOISE_BASE_ORDER_QTY, NOISE_TRADE_PROBABILITY, NOISE_RISK_CHECK_PERCENT,
    MAX_POSITION_LIMIT
)
from .utils import safe_decimal, quantize_price

log = logging.getLogger(__name__)

StrategyFunction = Callable[
    ['Agent', 'httpx.AsyncClient', Optional[Dict], List[Tuple[Decimal, int, datetime]]],
    Coroutine[Any, Any, List[Optional[Dict]]]
]

def get_current_mid_price(symbol: str, current_bbo: Optional[Dict]) -> Optional[Decimal]:
    """Gets the effective mid-price, considering BBO and potential shock override."""
    override_price = None
    now = datetime.now(timezone.utc)

    override_data = state.shocked_mid_price_override.get(symbol)
    if override_data:
        price, expiry = override_data
        if now < expiry:
            log.debug(f"[{symbol}] Using shocked price override: {price:.2f}")
            override_price = price
        else:
            pass

    if override_price is not None:
        return override_price

    if current_bbo:
        bid = safe_decimal(current_bbo.get('bid_price'))
        ask = safe_decimal(current_bbo.get('ask_price'))
        if bid and ask:
            return (bid + ask) / 2
        elif bid:
            return bid
        elif ask:
            return ask
        else:
            return None
    else:
        return None

async def strategy_noise(
    agent: 'Agent',
    http_client: 'httpx.AsyncClient',
    current_bbo: Optional[Dict],
    recent_trades: List
) -> List[Optional[Dict]]:
    order_payloads = []
    if random.random() < NOISE_TRADE_PROBABILITY:
        side = random.choice(["buy", "sell"])
        base_mid_price = get_current_mid_price(agent.symbol, current_bbo)
        if base_mid_price is None:
            base_mid_price = Decimal("100.0") if agent.symbol == "ABC" else Decimal("50.0")
            log.debug(f"A:{agent.agent_id} [N] No BBO/Shock, using default mid {base_mid_price}")
        try:
            volatility = base_mid_price * Decimal("0.005")
            price_offset = Decimal(random.uniform(float(-volatility), float(volatility)))
            price = quantize_price(base_mid_price + price_offset) or quantize_price(base_mid_price)
        except Exception:
            price = quantize_price(base_mid_price)
        if price is None:
            return order_payloads

        base_qty = NOISE_BASE_ORDER_QTY
        quantity = max(1, int(base_qty * agent.risk_factor * random.uniform(0.5, 1.5)))

        estimated_cost = price * quantity
        if estimated_cost > agent.bankroll * NOISE_RISK_CHECK_PERCENT:
            log.debug(f"A:{agent.agent_id} [N] Skip cost")
            return order_payloads
        potential_pos_change = quantity if side == "buy" else -quantity
        if abs(agent.position + potential_pos_change) > MAX_POSITION_LIMIT:
            log.debug(f"A:{agent.agent_id} [N] Skip pos limit")
            return order_payloads

        order_payload = {"symbol": agent.symbol, "side": side, "price": str(price), "quantity": quantity}
        log.info(f"A:{agent.agent_id} [N] Attempt {side} {quantity}@{price} (BaseMid:{base_mid_price:.2f})")
        order_payloads.append(order_payload)

    return order_payloads

async def strategy_market_maker(
    agent: 'Agent',
    http_client: 'httpx.AsyncClient',
    current_bbo: Optional[Dict],
    recent_trades: List
) -> List[Optional[Dict]]:
    order_payloads = []
    symbol = agent.symbol
    desired_spread = MM_DESIRED_SPREAD.get(symbol, Decimal("0.10"))
    base_qty = MM_BASE_ORDER_QTY.get(symbol, 10)
    order_qty = max(1, int(base_qty * agent.risk_factor * 2))

    mid_price = get_current_mid_price(symbol, current_bbo)
    if mid_price is None:
        mid_price = Decimal("100.0") if symbol == "ABC" else Decimal("50.0")
        log.debug(f"A:{agent.agent_id} [MM] No BBO/Shock, using default mid {mid_price}")

    half_spread = desired_spread / 2
    place_bid_price = quantize_price(mid_price - half_spread)
    place_ask_price = quantize_price(mid_price + half_spread)

    if current_bbo:
        bid = safe_decimal(current_bbo.get('bid_price'))
        ask = safe_decimal(current_bbo.get('ask_price'))
        if bid and place_ask_price and place_ask_price <= bid:
            place_ask_price = quantize_price(bid + Decimal("0.01"))
        if ask and place_bid_price and place_bid_price >= ask:
            place_bid_price = quantize_price(ask - Decimal("0.01"))
        place_bid_price = quantize_price(place_bid_price)
        place_ask_price = quantize_price(place_ask_price)

    if place_bid_price:
        required_capital = place_bid_price * order_qty
        if required_capital > agent.bankroll * MM_RISK_CHECK_PERCENT:
            place_bid_price = None
            log.debug(f"A:{agent.agent_id} [MM] Skip bid, cap")
        elif agent.position >= 0 and abs(agent.position + order_qty) > MAX_POSITION_LIMIT:
            place_bid_price = None
            log.debug(f"A:{agent.agent_id} [MM] Skip bid, pos limit")
    if place_ask_price:
        if agent.position <= 0 and abs(agent.position - order_qty) > MAX_POSITION_LIMIT:
            place_ask_price = None
            log.debug(f"A:{agent.agent_id} [MM] Skip ask, pos limit")

    if place_bid_price:
        bid_payload = {"symbol": symbol, "side": "buy", "price": str(place_bid_price), "quantity": order_qty}
        log.info(f"A:{agent.agent_id} [MM] Q BUY {order_qty}@{place_bid_price} (Mid:{mid_price:.2f})")
        order_payloads.append(bid_payload)
    if place_ask_price:
        ask_payload = {"symbol": symbol, "side": "sell", "price": str(place_ask_price), "quantity": order_qty}
        log.info(f"A:{agent.agent_id} [MM] Q SELL {order_qty}@{place_ask_price} (Mid:{mid_price:.2f})")
        order_payloads.append(ask_payload)

    return order_payloads

async def strategy_momentum(
    agent: 'Agent',
    http_client: 'httpx.AsyncClient',
    current_bbo: Optional[Dict],
    recent_trades: List[Tuple[Decimal, int, datetime]]
) -> List[Optional[Dict]]:
    order_payloads = []
    if not recent_trades or len(recent_trades) < MOMENTUM_WINDOW:
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

    effective_mid = get_current_mid_price(agent.symbol, current_bbo)

    side = target_price = None
    if price_diff > MOMENTUM_THRESHOLD:
        side = "buy"
        ask_price = safe_decimal(current_bbo.get('ask_price')) if current_bbo else None
        ref_price = ask_price or effective_mid or last_price
        target_price = ref_price * Decimal("1.001") if ref_price else None
    elif price_diff < -MOMENTUM_THRESHOLD:
        side = "sell"
        bid_price = safe_decimal(current_bbo.get('bid_price')) if current_bbo else None
        ref_price = bid_price or effective_mid or last_price
        target_price = ref_price * Decimal("0.999") if ref_price else None

    if side and target_price:
        price = quantize_price(target_price)
        if price is None:
            return order_payloads

        base_qty = MOMENTUM_BASE_ORDER_QTY
        quantity = max(1, int(base_qty * agent.risk_factor))

        estimated_cost = price * quantity
        if estimated_cost > agent.bankroll * Decimal("0.15"):
            log.debug(f"A:{agent.agent_id} [M] Skip cost")
            return order_payloads
        potential_pos_change = quantity if side == "buy" else -quantity
        if abs(agent.position + potential_pos_change) > MAX_POSITION_LIMIT:
            log.debug(f"A:{agent.agent_id} [M] Skip pos limit")
            return order_payloads

        eff_mid_str = f"{effective_mid:.2f}" if effective_mid else "N/A"
        log.info(f"A:{agent.agent_id} [M] Trend ({price_diff:+.2f}). Attempt {side} {quantity}@{price} (EffMid:{eff_mid_str})")
        order_payload = {"symbol": agent.symbol, "side": side, "price": str(price), "quantity": quantity}
        order_payloads.append(order_payload)

    return order_payloads

STRATEGY_FUNCTIONS: Dict[str, StrategyFunction] = {
    "noise": strategy_noise,
    "market_maker": strategy_market_maker,
    "momentum": strategy_momentum
}
