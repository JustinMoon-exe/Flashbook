# market_simulator/agent.py

import logging
import asyncio
import json
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from typing import Dict, Any, Optional, TYPE_CHECKING, List 
from collections import deque

if TYPE_CHECKING:
    import httpx
    from .strategies import StrategyFunction 

from . import state 
from . import config
from . import strategies as agent_strategies 
from . import api_client
from .utils import safe_decimal

log = logging.getLogger(__name__)

class Agent:
    
    def __init__(self, agent_id: str, symbol: str, initial_strategy: str = "noise", risk_factor: float = 0.5, bankroll: float = 10000.0):
        self.agent_id = agent_id; self.symbol = symbol.upper(); self.is_active = True
        self.strategy_name = initial_strategy if initial_strategy in agent_strategies.STRATEGY_FUNCTIONS else "noise"
        self.risk_factor = max(0.1, min(2.0, risk_factor)); self.bankroll = Decimal(str(bankroll))
        self.position = 0; self.average_entry_price = Decimal("0.0"); self.realized_pnl = Decimal("0.0")
        self.trade_count = 0; self.total_traded_value = Decimal("0.0")
        self.last_status_publish_time = datetime.now(timezone.utc); self.open_order_ids: set[str] = set()
        log.info(f"Initialized Agent {self.agent_id} {self.symbol} Strat:{self.strategy_name}, Risk:{self.risk_factor}, Bankroll:{self.bankroll:.2f}")

    def set_active(self, active: bool): 
        if self.is_active != active: self.is_active = active; log.info(f"Agent {self.agent_id} activity set to: {self.is_active}")
    def set_strategy(self, strategy: str): 
        if strategy in agent_strategies.STRATEGY_FUNCTIONS:
            if self.strategy_name != strategy: self.strategy_name = strategy; log.info(f"Agent {self.agent_id} strategy changed to: {self.strategy_name}")
        else: log.warning(f"Agent {self.agent_id}: Unknown strategy '{strategy}'")
    def set_risk_factor(self, risk: float): 
        old_risk = self.risk_factor; self.risk_factor = max(0.1, min(2.0, risk));
        if old_risk != self.risk_factor: log.info(f"Agent {self.agent_id} risk factor changed from {old_risk:.2f} to: {self.risk_factor:.2f}")
    def set_bankroll(self, amount: float): 
        try:
            new_bankroll = Decimal(str(amount))
            if new_bankroll >= 0: self.bankroll = new_bankroll; log.info(f"Agent {self.agent_id} bankroll set to: {self.bankroll:.2f}")
            else: log.warning(f"Agent {self.agent_id}: Invalid negative bankroll amount {amount}")
        except (ValueError, InvalidOperation, TypeError) as e: log.warning(f"Agent {self.agent_id}: Could not parse bankroll amount '{amount}'. Error: {e}")

    def update_on_fill(self, filled_order_id: str, trade_price_str: str, trade_quantity: int, is_taker: bool):
        log.info(f"A:{self.agent_id}: ENTER update_on_fill for OrderID: {filled_order_id[-6:]}. Current State: Pos={self.position}, AvgPx={self.average_entry_price:.2f}, PNL={self.realized_pnl:.2f}, Trades={self.trade_count}")
        try:
            original_order = state.submitted_orders_map.get(filled_order_id);
            if not original_order: log.warning(f"A:{self.agent_id}: Order {filled_order_id} not found in map."); self.open_order_ids.discard(filled_order_id); return
            if original_order["agent_id"] != self.agent_id: log.debug(f"A:{self.agent_id}: Fill for other agent {filled_order_id[-6:]}."); self.open_order_ids.discard(filled_order_id); return
            side = original_order["side"]
            try: price = Decimal(trade_price_str); trade_value = price * Decimal(trade_quantity)
            except Exception as e: log.error(f"A:{self.agent_id}: Invalid price/qty {filled_order_id}: P='{trade_price_str}', Q={trade_quantity}. E: {e}"); return
            old_pos = self.position; old_avg_price = self.average_entry_price; old_bankroll = self.bankroll; old_trade_count = self.trade_count; pnl_increment = Decimal("0.0")
            log.info(f"A:{self.agent_id}: Proc fill Order:{filled_order_id[-6:]} Side:{side} Qty:{trade_quantity} @ {price} Taker:{is_taker}")
            self.trade_count += 1; self.total_traded_value += trade_value
            if side == "buy": self.bankroll -= trade_value; self.position += trade_quantity
            elif side == "sell": self.bankroll += trade_value; self.position -= trade_quantity
            new_pos = self.position
            if old_pos == 0 and new_pos != 0: self.average_entry_price = price; log.debug(f"A:{self.agent_id}: Opened pos {new_pos}. AvgPx={price:.2f}")
            elif old_pos != 0 and new_pos == 0: 
                 if old_pos > 0: pnl_increment = (price - old_avg_price) * abs(Decimal(old_pos))
                 else: pnl_increment = (old_avg_price - price) * abs(Decimal(old_pos))
                 log.debug(f"A:{self.agent_id}: Flattened {'L' if old_pos > 0 else 'S'}. PNL Calc"); self.realized_pnl += pnl_increment; self.average_entry_price = Decimal("0.0"); log.debug(f"A:{self.agent_id}: Pos Flat. AvgPx reset. PNL Inc: {pnl_increment:.2f}")
            elif old_pos * new_pos < 0: 
                quantity_closed = abs(Decimal(old_pos));
                if old_pos > 0: pnl_increment = (price - old_avg_price) * quantity_closed
                else: pnl_increment = (old_avg_price - price) * quantity_closed
                log.debug(f"A:{self.agent_id}: Flipped {'L->S' if old_pos > 0 else 'S->L'}. PNL Calc"); self.realized_pnl += pnl_increment; self.average_entry_price = price; log.debug(f"A:{self.agent_id}: Flipped pos. New AvgPx={price:.2f}. PNL Inc: {pnl_increment:.2f}")
            elif abs(new_pos) < abs(old_pos): 
                quantity_closed = abs(Decimal(old_pos)) - abs(Decimal(new_pos));
                if old_pos > 0: pnl_increment = (price - old_avg_price) * quantity_closed
                else: pnl_increment = (old_avg_price - price) * quantity_closed
                log.debug(f"A:{self.agent_id}: Reduced {'L' if old_pos > 0 else 'S'} Pos. PNL Calc"); self.realized_pnl += pnl_increment; log.debug(f"A:{self.agent_id}: Reduced pos. AvgPx rem {self.average_entry_price:.2f}. PNL Inc: {pnl_increment:.2f}")
            elif abs(new_pos) > abs(old_pos): 
                if old_pos * new_pos > 0: new_avg_price = (abs(Decimal(old_pos)) * old_avg_price + Decimal(trade_quantity) * price) / abs(Decimal(new_pos)); self.average_entry_price = new_avg_price; log.debug(f"A:{self.agent_id}: Increased pos. New AvgPx = {new_avg_price:.4f}")
                else: log.warning(f"A:{self.agent_id}: Unexpected inc state: old={old_pos}, new={new_pos}. Reset avg px."); self.average_entry_price = price
            log.info(f"A:{self.agent_id}: EXIT update_on_fill. State Change: Pos({old_pos} -> {new_pos}), AvgPx({old_avg_price:.2f} -> {self.average_entry_price:.2f}), Trades({old_trade_count} -> {self.trade_count}), Bankroll({old_bankroll:.2f} -> {self.bankroll:.2f}), PNL_Inc({pnl_increment:.2f}), Total PNL({self.realized_pnl:.2f})")
            self.open_order_ids.discard(filled_order_id) 
        except Exception as e: log.error(f"A:{self.agent_id}: UNEXPECTED Error processing fill {filled_order_id}: {e}", exc_info=True)


    
    def get_status(self, current_bbo: Optional[Dict] = None) -> Dict[str, Any]:
        unrealized_pnl = Decimal("0.0")
        if self.position != 0 and current_bbo:
            bid = safe_decimal(current_bbo.get('bid_price')); ask = safe_decimal(current_bbo.get('ask_price')); mark_price = None
            if self.position > 0: mark_price = bid
            elif self.position < 0: mark_price = ask
            if mark_price is None:
                if bid and ask: mark_price = (bid + ask) / 2
                elif bid: mark_price = bid
                elif ask: mark_price = ask
            if mark_price is not None:
                if self.position > 0: unrealized_pnl = (mark_price - self.average_entry_price) * Decimal(self.position)
                else: unrealized_pnl = (self.average_entry_price - mark_price) * abs(Decimal(self.position))

        open_orders_details = []
        
        for order_id in self.open_order_ids:
            order_details = state.submitted_orders_map.get(order_id)
            if order_details:
                open_orders_details.append({
                    "id": order_id,
                    "side": order_details.get("side"),
                    "price": order_details.get("price", "N/A"), 
                    "quantity": order_details.get("quantity"),
                })
            else:
                 log.warning(f"A:{self.agent_id}: Open order ID {order_id} not found in map during get_status.")

        open_orders_details.sort(key=lambda x: (x.get('side', ''), safe_decimal(x.get('price', '0')) or Decimal(0)), reverse=True) 

        return {
            "agent_id": self.agent_id, "symbol": self.symbol, "is_active": self.is_active, "strategy": self.strategy_name,
            "risk_factor": self.risk_factor, "bankroll": float(self.bankroll.quantize(Decimal("0.01"))),
            "position": self.position, "average_entry_price": float(self.average_entry_price.quantize(Decimal("0.04"))) if self.position != 0 else 0.0,
            "realized_pnl": float(self.realized_pnl.quantize(Decimal("0.01"))), "unrealized_pnl": float(unrealized_pnl.quantize(Decimal("0.01"))),
            "trade_count": self.trade_count, "total_traded_value": float(self.total_traded_value.quantize(Decimal("0.01"))),
            "open_orders": open_orders_details, 
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    async def decide_and_act(self, http_client: 'httpx.AsyncClient'):
        if not self.is_active or self.bankroll < config.MIN_BANKROLL_THRESHOLD:
            if self.bankroll < config.MIN_BANKROLL_THRESHOLD: log.warning(f"A:{self.agent_id}: Skipping, bankroll {self.bankroll:.2f} < {config.MIN_BANKROLL_THRESHOLD}")
            return

        current_bbo = state.market_bbo_cache.get(self.symbol)
        recent_trades = list(state.market_trade_cache.get(self.symbol, deque()))

        strategy_func: StrategyFunction = agent_strategies.STRATEGY_FUNCTIONS.get(self.strategy_name, agent_strategies.strategy_noise)
        order_payloads_or_none = await strategy_func(self, http_client, current_bbo, recent_trades)

        order_payloads = []
        if isinstance(order_payloads_or_none, dict): order_payloads = [order_payloads_or_none]
        elif isinstance(order_payloads_or_none, list): order_payloads = order_payloads_or_none
        if not order_payloads: return

        submit_tasks = [api_client.submit_order_to_api(http_client, payload) for payload in order_payloads if payload]
        if not submit_tasks: return

        api_responses = await asyncio.gather(*submit_tasks)

        for i, api_response_data in enumerate(api_responses):
            if api_response_data and order_payloads[i]:
                order_payload_submitted = order_payloads[i]
                order_id = api_response_data.get("order_id")

                if order_id:
                    self.open_order_ids.add(order_id)
                    order_info_to_store = {
                        "agent_id": self.agent_id, "symbol": self.symbol,
                        "side": order_payload_submitted["side"],
                        "price": order_payload_submitted.get("price"),
                        "quantity": order_payload_submitted["quantity"]
                    }
                    async with state.submitted_orders_lock: state.submitted_orders_map[order_id] = order_info_to_store
                    log.info(f"A:{self.agent_id}: Stored order {order_id[-6:]} in map: {order_info_to_store}")

                    if state.redis_publisher: 
                        try:
                            action_message = {"timestamp": datetime.now(timezone.utc).isoformat(), "agent_id": self.agent_id, "action": "submit_order", "strategy": self.strategy_name, **order_payload_submitted, "api_order_id": order_id, "api_status": "accepted"}
                            action_json = json.dumps({k: v for k, v in action_message.items() if v is not None})
                            await state.redis_publisher.publish(config.AGENT_ACTION_CHANNEL, action_json)
                            log.debug(f"A:{self.agent_id}: Published action for order {order_id[-6:]}")
                        except Exception as e: log.error(f"A:{self.agent_id}: Error publishing action for {order_id[-6:]}: {e}", exc_info=True)
                else: log.warning(f"A:{self.agent_id}: Order submission OK but no order_id: {api_response_data}")
            elif order_payloads[i]: log.warning(f"A:{self.agent_id}: Failed API submission for: {order_payloads[i]}")

    def reset_state(self, initial_config: Dict):
        log.warning(f"Resetting state for Agent {self.agent_id}")
        self.strategy_name = initial_config["type"] if initial_config["type"] in agent_strategies.STRATEGY_FUNCTIONS else "noise"
        self.risk_factor = max(0.1, min(2.0, initial_config["risk"]))
        self.bankroll = Decimal(str(initial_config["bankroll"]))
        self.position = 0; self.average_entry_price = Decimal("0.0"); self.realized_pnl = Decimal("0.0")
        self.trade_count = 0; self.total_traded_value = Decimal("0.0")
        self.last_status_publish_time = datetime.now(timezone.utc); self.open_order_ids.clear(); self.is_active = True
        log.info(f"Agent {self.agent_id} state reset complete.")