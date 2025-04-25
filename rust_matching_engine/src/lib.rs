use rust_decimal::Decimal;
use rust_decimal_macros::dec;
use chrono::{DateTime, Utc};
use uuid::Uuid;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, VecDeque};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, PartialOrd, Ord)]
#[serde(rename_all = "lowercase")]
pub enum OrderSide {
    Buy,
    Sell,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum OrderStatus {
    #[default]
    New,
    Accepted,
    Rejected,
    Filled,
    PartiallyFilled,
    Cancelled,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Order {
    pub id: Uuid,
    pub side: OrderSide,
    pub symbol: String,
    #[serde(with = "rust_decimal::serde::str")]
    pub price: Decimal,
    pub quantity: u64,
    pub timestamp: DateTime<Utc>,
    #[serde(default)]
    pub status: OrderStatus,
    #[serde(default, alias = "quantity")]
    pub remaining_quantity: u64,
}

impl Order {
    pub fn ensure_remaining_quantity(&mut self) {
        if self.remaining_quantity == 0 || self.remaining_quantity > self.quantity {
            log::debug!(
                "Order {} received/processed with invalid remaining_quantity, resetting to {}.",
                self.id,
                self.quantity
            );
            self.remaining_quantity = self.quantity;
        }
        if self.status == OrderStatus::New {
            self.remaining_quantity = self.quantity;
        }
    }

    #[cfg(test)]
    pub fn new(side: OrderSide, symbol: String, price: Decimal, quantity: u64) -> Self {
        let id = Uuid::new_v4();
        Order {
            id,
            side,
            symbol,
            price,
            quantity,
            timestamp: Utc::now(),
            status: OrderStatus::New,
            remaining_quantity: quantity,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Trade {
    #[serde(default = "Uuid::new_v4")]
    pub trade_id: Uuid,
    pub symbol: String,
    #[serde(with = "rust_decimal::serde::str")]
    pub price: Decimal,
    pub quantity: u64,
    pub taker_order_id: Uuid,
    pub maker_order_id: Uuid,
    pub timestamp: DateTime<Utc>,
}

impl Trade {
    pub fn new(
        symbol: String,
        price: Decimal,
        quantity: u64,
        taker_order_id: Uuid,
        maker_order_id: Uuid,
    ) -> Self {
        Trade {
            trade_id: Uuid::new_v4(),
            symbol,
            price,
            quantity,
            taker_order_id,
            maker_order_id,
            timestamp: Utc::now(),
        }
    }
}

mod decimal_option_serde_as_string {
    use rust_decimal::Decimal;
    use serde::{Serializer, Deserializer, Deserialize};

    pub fn serialize<S>(value: &Option<Decimal>, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        match value {
            Some(ref d) => rust_decimal::serde::str::serialize(d, serializer),
            None => serializer.serialize_none(),
        }
    }

    pub fn deserialize<'de, D>(deserializer: D) -> Result<Option<Decimal>, D::Error>
    where
        D: Deserializer<'de>,
    {
        let opt_str = Option::<String>::deserialize(deserializer)?;
        match opt_str {
            Some(s) => Decimal::from_str_radix(&s, 10)
                .map(Some)
                .map_err(serde::de::Error::custom),
            None => Ok(None),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct BboUpdate {
    pub symbol: String,
    #[serde(default, with = "decimal_option_serde_as_string")]
    pub bid_price: Option<Decimal>,
    pub bid_qty: Option<u64>,
    #[serde(default, with = "decimal_option_serde_as_string")]
    pub ask_price: Option<Decimal>,
    pub ask_qty: Option<u64>,
    pub timestamp: DateTime<Utc>,
}

impl BboUpdate {
    pub fn new(
        symbol: String,
        bid_price: Option<Decimal>,
        bid_qty: Option<u64>,
        ask_price: Option<Decimal>,
        ask_qty: Option<u64>,
    ) -> Self {
        BboUpdate {
            symbol,
            bid_price,
            bid_qty: bid_qty.filter(|&q| q > 0),
            ask_price,
            ask_qty: ask_qty.filter(|&q| q > 0),
            timestamp: Utc::now(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PriceLevelInfo {
    #[serde(with = "rust_decimal::serde::str")]
    pub price: Decimal,
    pub quantity: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct OrderBookSnapshot {
    pub symbol: String,
    pub bids: Vec<PriceLevelInfo>,
    pub asks: Vec<PriceLevelInfo>,
    pub timestamp: DateTime<Utc>,
}

impl OrderBookSnapshot {
    pub fn new(symbol: String, bids: Vec<PriceLevelInfo>, asks: Vec<PriceLevelInfo>) -> Self {
        OrderBookSnapshot { symbol, bids, asks, timestamp: Utc::now() }
    }
}

type PriceLevel = VecDeque<Order>;

#[derive(Debug, Default)]
pub struct OrderBook {
    symbol: String,
    bids: BTreeMap<Decimal, PriceLevel>,
    asks: BTreeMap<Decimal, PriceLevel>,
    last_bbo: Option<BboUpdate>,
    last_snapshot: Option<OrderBookSnapshot>,
}

impl OrderBook {
    pub fn new(symbol: String) -> Self {
        OrderBook { symbol, ..Default::default() }
    }

    pub fn symbol(&self) -> &str { &self.symbol }
    pub fn last_bbo(&self) -> &Option<BboUpdate> { &self.last_bbo }
    pub fn last_bbo_mut(&mut self) -> &mut Option<BboUpdate> { &mut self.last_bbo }
    pub fn last_snapshot(&self) -> &Option<OrderBookSnapshot> { &self.last_snapshot }
    pub fn last_snapshot_mut(&mut self) -> &mut Option<OrderBookSnapshot> { &mut self.last_snapshot }

    pub fn get_bbo_with_qty(&self) -> (Option<Decimal>, Option<u64>, Option<Decimal>, Option<u64>) {
        let best_bid_price = self.bids.keys().last().cloned();
        let best_bid_qty = best_bid_price.and_then(|p| self.bids.get(&p).map(|level| level.iter().map(|o| o.remaining_quantity).sum()));
        let best_ask_price = self.asks.keys().next().cloned();
        let best_ask_qty = best_ask_price.and_then(|p| self.asks.get(&p).map(|level| level.iter().map(|o| o.remaining_quantity).sum()));
        (best_bid_price, best_bid_qty.filter(|&q| q > 0), best_ask_price, best_ask_qty.filter(|&q| q > 0))
    }

    pub fn get_snapshot(&self, depth: usize) -> OrderBookSnapshot {
        let bids_snapshot: Vec<PriceLevelInfo> = self.bids.iter().rev()
            .take(depth)
            .map(|(&price, level)| PriceLevelInfo { price, quantity: level.iter().map(|o| o.remaining_quantity).sum() })
            .filter(|lvl| lvl.quantity > 0)
            .collect();
        let asks_snapshot: Vec<PriceLevelInfo> = self.asks.iter()
            .take(depth)
            .map(|(&price, level)| PriceLevelInfo { price, quantity: level.iter().map(|o| o.remaining_quantity).sum() })
            .filter(|lvl| lvl.quantity > 0)
            .collect();
        OrderBookSnapshot::new(self.symbol.clone(), bids_snapshot, asks_snapshot)
    }

    pub fn clear_book(&mut self) {
        log::warn!("Clearing all orders from book: {}", self.symbol);
        let bid_count: usize = self.bids.values().map(VecDeque::len).sum();
        let ask_count: usize = self.asks.values().map(VecDeque::len).sum();
        self.bids.clear();
        self.asks.clear();
        self.last_bbo = None;
        self.last_snapshot = None;
        log::info!("Book {} cleared. Removed {} bids, {} asks.", self.symbol, bid_count, ask_count);
    }

    pub fn add_order(&mut self, mut order: Order) -> (OrderStatus, Vec<Trade>) {
        order.ensure_remaining_quantity();

        if order.symbol != self.symbol {
            log::error!("Order Rejected (Symbol mismatch): {:?}", order);
            order.status = OrderStatus::Rejected;
            return (order.status, vec![]);
        }
        if order.price <= dec!(0) {
            log::error!("Order Rejected (Invalid price): {:?}", order);
            order.status = OrderStatus::Rejected;
            return (order.status, vec![]);
        }
        if order.quantity == 0 {
            log::error!("Order Rejected (Zero quantity): {:?}", order);
            order.status = OrderStatus::Rejected;
            return (order.status, vec![]);
        }

        if order.status == OrderStatus::New {
            order.status = OrderStatus::Accepted;
        }

        log::info!(
            "Processing order: Id={}, Side={:?}, Price={}, Qty={}, Rem={}",
            order.id,
            order.side,
            order.price,
            order.quantity,
            order.remaining_quantity
        );

        let mut trades = Vec::new();
        let mut taker_final_status = order.status;

        match order.side {
            OrderSide::Buy => {
                let mut asks_to_remove = Vec::new();
                for (&ask_price, price_level) in self.asks.iter_mut() {
                    if order.remaining_quantity == 0 { break; }
                    if ask_price > order.price { break; }
                    for maker_order in price_level.iter_mut() {
                        if order.remaining_quantity == 0 { break; }
                        let trade_quantity = std::cmp::min(order.remaining_quantity, maker_order.remaining_quantity);
                        if trade_quantity > 0 {
                            trades.push(Trade::new(self.symbol.clone(), maker_order.price, trade_quantity, order.id, maker_order.id));
                            order.remaining_quantity -= trade_quantity;
                            maker_order.remaining_quantity -= trade_quantity;
                            maker_order.status = if maker_order.remaining_quantity == 0 { OrderStatus::Filled } else { OrderStatus::PartiallyFilled };
                            log::debug!("Maker ask {} status -> {:?}, Rem: {}", maker_order.id, maker_order.status, maker_order.remaining_quantity);
                        }
                    }
                    price_level.retain(|o| o.status != OrderStatus::Filled);
                    if price_level.is_empty() {
                        asks_to_remove.push(ask_price);
                    }
                }
                for price in asks_to_remove {
                    self.asks.remove(&price);
                    log::debug!("Removed empty ask level: {}", price);
                }

                if order.remaining_quantity == 0 {
                    taker_final_status = OrderStatus::Filled;
                    log::info!("Taker buy order {} fully filled.", order.id);
                } else {
                    if order.remaining_quantity < order.quantity {
                        taker_final_status = OrderStatus::PartiallyFilled;
                    }
                    log::info!(
                        "Adding resting buy order {} to book. Status: {:?}, Rem: {}",
                        order.id,
                        taker_final_status,
                        order.remaining_quantity
                    );
                    order.status = taker_final_status;
                    self.bids.entry(order.price).or_default().push_back(order);
                }
            }
            OrderSide::Sell => {
                let mut bids_to_remove = Vec::new();
                for (&bid_price, price_level) in self.bids.iter_mut().rev() {
                    if order.remaining_quantity == 0 { break; }
                    if bid_price < order.price { break; }
                    for maker_order in price_level.iter_mut() {
                        if order.remaining_quantity == 0 { break; }
                        let trade_quantity = std::cmp::min(order.remaining_quantity, maker_order.remaining_quantity);
                        if trade_quantity > 0 {
                            trades.push(Trade::new(self.symbol.clone(), maker_order.price, trade_quantity, order.id, maker_order.id));
                            order.remaining_quantity -= trade_quantity;
                            maker_order.remaining_quantity -= trade_quantity;
                            maker_order.status = if maker_order.remaining_quantity == 0 { OrderStatus::Filled } else { OrderStatus::PartiallyFilled };
                            log::debug!("Maker bid {} status -> {:?}, Rem: {}", maker_order.id, maker_order.status, maker_order.remaining_quantity);
                        }
                    }
                    price_level.retain(|o| o.status != OrderStatus::Filled);
                    if price_level.is_empty() {
                        bids_to_remove.push(bid_price);
                    }
                }
                for price in bids_to_remove {
                    self.bids.remove(&price);
                    log::debug!("Removed empty bid level: {}", price);
                }

                if order.remaining_quantity == 0 {
                    taker_final_status = OrderStatus::Filled;
                    log::info!("Taker sell order {} fully filled.", order.id);
                } else {
                    if order.remaining_quantity < order.quantity {
                        taker_final_status = OrderStatus::PartiallyFilled;
                    }
                    log::info!(
                        "Adding resting sell order {} to book. Status: {:?}, Rem: {}",
                        order.id,
                        taker_final_status,
                        order.remaining_quantity
                    );
                    order.status = taker_final_status;
                    self.asks.entry(order.price).or_default().push_back(order);
                }
            }
        }
        (taker_final_status, trades)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rust_decimal_macros::dec;
    use std::sync::Once;

    static INIT: Once = Once::new();
    fn setup_logging() { INIT.call_once(|| { let _ = env_logger::builder().is_test(true).try_init(); }); }
    fn create_test_order(side: OrderSide, price: Decimal, qty: u64) -> Order { Order::new(side, "TEST".to_string(), price, qty) }

    #[test] fn test_add_order_empty_book() { /* ... */ }
    #[test] fn test_simple_full_match() { /* ... */ }
    #[test] fn test_partial_match_taker_remaining() { /* ... */ }
    #[test] fn test_partial_match_maker_remaining() { /* ... */ }
    #[test] fn test_match_multiple_makers_at_same_price() { /* ... */ }
    #[test] fn test_match_multiple_price_levels() { /* ... */ }
    #[test] fn test_add_invalid_order_rejected() { /* ... */ }
    #[test] fn test_get_bbo_with_qty_logic() { /* ... */ }
    #[test] fn test_get_snapshot() { /* ... */ }
    #[test]
    fn test_clear_book() {
        setup_logging();
        let mut book = OrderBook::new("TEST".to_string());
        book.add_order(create_test_order(OrderSide::Buy, dec!(99.0), 10));
        book.add_order(create_test_order(OrderSide::Sell, dec!(100.0), 8));
        book.last_bbo = Some(BboUpdate::new("TEST".to_string(), None, None, None, None));
        book.last_snapshot = Some(OrderBookSnapshot::new("TEST".to_string(), vec![], vec![]));
        book.clear_book();
        assert!(book.bids.is_empty() && book.asks.is_empty());
        assert!(book.last_bbo.is_none());
        assert!(book.last_snapshot.is_none());
        let (bp,bq,ap,aq) = book.get_bbo_with_qty();
        assert!(bp.is_none() && bq.is_none() && ap.is_none() && aq.is_none());
        assert!(book.get_snapshot(5).bids.is_empty() && book.get_snapshot(5).asks.is_empty());
    }
}
