// rust_matching_engine/src/lib.rs
use rust_decimal::Decimal;
use rust_decimal_macros::dec;
use chrono::{DateTime, Utc};
use uuid::Uuid;
use serde::{Deserialize, Serialize}; // Keep only needed traits
use std::collections::{BTreeMap, VecDeque};

// --- Enums ---

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

// --- Core Structures ---

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
    #[serde(default, alias = "quantity")] // Initialize remaining_quantity from quantity if missing
    pub remaining_quantity: u64,
}

impl Order {
    // Helper method to ensure remaining_quantity is valid after deserialization or modification
    pub fn ensure_remaining_quantity(&mut self) {
         if self.remaining_quantity == 0 || self.remaining_quantity > self.quantity {
             log::debug!("Order {} received/processed with invalid remaining_quantity, resetting to {}.", self.id, self.quantity);
             self.remaining_quantity = self.quantity;
         }
         // If status is New (e.g., just deserialized), ensure remaining is same as quantity
         if self.status == OrderStatus::New {
            self.remaining_quantity = self.quantity;
         }
    }

    // Constructor used mainly for testing now
    #[cfg(test)] // Only compile this for tests
    pub fn new(side: OrderSide, symbol: String, price: Decimal, quantity: u64) -> Self {
        let id = Uuid::new_v4();
        Order {
            id, side, symbol, price, quantity,
            timestamp: Utc::now(), status: OrderStatus::New, remaining_quantity: quantity,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
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
     pub fn new(symbol: String, price: Decimal, quantity: u64, taker_order_id: Uuid, maker_order_id: Uuid) -> Self {
         Trade {
             trade_id: Uuid::new_v4(), symbol, price, quantity,
             taker_order_id, maker_order_id, timestamp: Utc::now(),
         }
     }
}

// --- Custom Serde Helper for Option<Decimal> ---
// This module handles serializing Option<Decimal> to string or null,
// and deserializing from string or null back to Option<Decimal>.
mod decimal_option_serde_as_string {
    use rust_decimal::Decimal;
    use serde::{Serializer, Deserializer, Deserialize};

    pub fn serialize<S>(value: &Option<Decimal>, serializer: S) -> Result<S::Ok, S::Error>
    where S: Serializer {
        match value {
            Some(ref d) => rust_decimal::serde::str::serialize(d, serializer),
            None => serializer.serialize_none(),
        }
    }

    pub fn deserialize<'de, D>(deserializer: D) -> Result<Option<Decimal>, D::Error>
    where D: Deserializer<'de> {
        // Deserialize as an Option<String> first
        let opt_str = Option::<String>::deserialize(deserializer)?;
        match opt_str {
            // If Some(string), attempt to parse it as Decimal
            Some(s) => Decimal::from_str_radix(&s, 10)
                         .map(Some) // Wrap successful parse in Some
                         .map_err(serde::de::Error::custom), // Convert Decimal error to serde error
            // If None string, result is Ok(None) for Option<Decimal>
            None => Ok(None),
        }
    }
}


// --- BBO Update Structure ---
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct BboUpdate {
    pub symbol: String,
    #[serde(default, with = "decimal_option_serde_as_string")] // Use helper for Option<Decimal>
    pub bid_price: Option<Decimal>,
    pub bid_qty: Option<u64>,
    #[serde(default, with = "decimal_option_serde_as_string")] // Use helper for Option<Decimal>
    pub ask_price: Option<Decimal>,
    pub ask_qty: Option<u64>,
    pub timestamp: DateTime<Utc>,
}

impl BboUpdate {
    pub fn new(symbol: String, bid_price: Option<Decimal>, bid_qty: Option<u64>, ask_price: Option<Decimal>, ask_qty: Option<u64>) -> Self {
        BboUpdate {
            symbol,
            bid_price,
            bid_qty: bid_qty.filter(|&q| q > 0), // Don't report 0 qty
            ask_price,
            ask_qty: ask_qty.filter(|&q| q > 0), // Don't report 0 qty
            timestamp: Utc::now(),
        }
    }
}

// --- Order Book Snapshot Structures ---
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

// --- Order Book Logic ---
type PriceLevel = VecDeque<Order>;

#[derive(Debug, Default)]
pub struct OrderBook {
    symbol: String,
    // Bids sorted ascending, highest bid is last()
    bids: BTreeMap<Decimal, PriceLevel>,
    // Asks sorted ascending, lowest ask is first()
    asks: BTreeMap<Decimal, PriceLevel>,
    // Cache last published states to avoid redundant publishes
    last_bbo: Option<BboUpdate>,
    last_snapshot: Option<OrderBookSnapshot>,
}

impl OrderBook {
    pub fn new(symbol: String) -> Self {
        OrderBook { symbol, ..Default::default() } // Use default for empty BTreeMaps/Options
    }

    // --- Getters ---
    pub fn symbol(&self) -> &str { &self.symbol }
    pub fn last_bbo(&self) -> &Option<BboUpdate> { &self.last_bbo }
    pub fn last_bbo_mut(&mut self) -> &mut Option<BboUpdate> { &mut self.last_bbo }
    pub fn last_snapshot(&self) -> &Option<OrderBookSnapshot> { &self.last_snapshot }
    pub fn last_snapshot_mut(&mut self) -> &mut Option<OrderBookSnapshot> { &mut self.last_snapshot }

    // --- BBO Calculation ---
    pub fn get_bbo_with_qty(&self) -> (Option<Decimal>, Option<u64>, Option<Decimal>, Option<u64>) {
        let best_bid_price = self.bids.keys().last().cloned();
        let best_bid_qty = best_bid_price.and_then(|p| self.bids.get(&p).map(|level| level.iter().map(|o| o.remaining_quantity).sum()));
        let best_ask_price = self.asks.keys().next().cloned();
        let best_ask_qty = best_ask_price.and_then(|p| self.asks.get(&p).map(|level| level.iter().map(|o| o.remaining_quantity).sum()));
        (best_bid_price, best_bid_qty.filter(|&q| q > 0), best_ask_price, best_ask_qty.filter(|&q| q > 0))
    }

    // --- Snapshot Calculation ---
    pub fn get_snapshot(&self, depth: usize) -> OrderBookSnapshot {
        let bids_snapshot: Vec<PriceLevelInfo> = self.bids.iter().rev() // Highest price first
            .take(depth)
            .map(|(&price, level)| PriceLevelInfo { price, quantity: level.iter().map(|o| o.remaining_quantity).sum() })
            .filter(|lvl| lvl.quantity > 0) // Exclude empty levels
            .collect();
        let asks_snapshot: Vec<PriceLevelInfo> = self.asks.iter() // Lowest price first
            .take(depth)
            .map(|(&price, level)| PriceLevelInfo { price, quantity: level.iter().map(|o| o.remaining_quantity).sum() })
            .filter(|lvl| lvl.quantity > 0) // Exclude empty levels
            .collect();
        OrderBookSnapshot::new(self.symbol.clone(), bids_snapshot, asks_snapshot)
    }

    // --- Clear Book Method ---
    pub fn clear_book(&mut self) {
        log::warn!("Clearing all orders from book: {}", self.symbol);
        let bid_count: usize = self.bids.values().map(VecDeque::len).sum();
        let ask_count: usize = self.asks.values().map(VecDeque::len).sum();
        self.bids.clear();
        self.asks.clear();
        // Reset last known states to ensure cleared state is published
        self.last_bbo = None;
        self.last_snapshot = None;
        log::info!("Book {} cleared. Removed {} bids, {} asks.", self.symbol, bid_count, ask_count);
    }

    // --- Core Matching Logic ---
    pub fn add_order(&mut self, mut order: Order) -> (OrderStatus, Vec<Trade>) {
        order.ensure_remaining_quantity(); // Ensure valid state after potentially deserializing

        // --- Basic Validation ---
        if order.symbol != self.symbol { log::error!("Order Rejected (Symbol mismatch): {:?}", order); order.status = OrderStatus::Rejected; return (order.status, vec![]); }
        if order.price <= dec!(0) { log::error!("Order Rejected (Invalid price): {:?}", order); order.status = OrderStatus::Rejected; return (order.status, vec![]); }
        if order.quantity == 0 { log::error!("Order Rejected (Zero quantity): {:?}", order); order.status = OrderStatus::Rejected; return (order.status, vec![]); }
        // Potentially add self-match prevention here if needed

        // --- Set Initial Status ---
        if order.status == OrderStatus::New { order.status = OrderStatus::Accepted; } // Mark as accepted by engine

        log::info!("Processing order: Id={}, Side={:?}, Price={}, Qty={}, Rem={}", order.id, order.side, order.price, order.quantity, order.remaining_quantity);

        let mut trades = Vec::new();
        let mut taker_final_status = order.status; // Track the incoming order's final status

        match order.side {
            OrderSide::Buy => {
                let mut asks_to_remove = Vec::new();
                // Collect modified maker orders to potentially publish updates later
                // let mut makers_touched = Vec::new();

                // Iterate mutable asks, lowest price first
                for (&ask_price, price_level) in self.asks.iter_mut() {
                    if order.remaining_quantity == 0 { break; } // Taker filled
                    if ask_price > order.price { break; } // Price level too high

                    // Iterate orders at this price level (FIFO)
                    // let mut makers_in_level_touched = Vec::new();
                    for maker_order in price_level.iter_mut() {
                        if order.remaining_quantity == 0 { break; } // Taker filled

                        let trade_quantity = std::cmp::min(order.remaining_quantity, maker_order.remaining_quantity);
                        if trade_quantity > 0 {
                            trades.push(Trade::new(self.symbol.clone(), maker_order.price, trade_quantity, order.id, maker_order.id));
                            order.remaining_quantity -= trade_quantity;
                            maker_order.remaining_quantity -= trade_quantity;
                            maker_order.status = if maker_order.remaining_quantity == 0 { OrderStatus::Filled } else { OrderStatus::PartiallyFilled };
                            log::debug!("Maker ask {} status -> {:?}, Rem: {}", maker_order.id, maker_order.status, maker_order.remaining_quantity);
                            // makers_in_level_touched.push(maker_order.clone()); // Clone state AFTER modification
                        }
                    }
                    // makers_touched.append(&mut makers_in_level_touched);

                    // Remove fully filled maker orders from the deque
                    price_level.retain(|o| o.status != OrderStatus::Filled);
                    // If the level is now empty, mark it for removal from the BTreeMap
                    if price_level.is_empty() { asks_to_remove.push(ask_price); }
                }
                // Remove empty price levels outside the main loop
                for price in asks_to_remove { self.asks.remove(&price); log::debug!("Removed empty ask level: {}", price); }

                // --- Determine Taker's Final State ---
                if order.remaining_quantity == 0 {
                    taker_final_status = OrderStatus::Filled;
                    log::info!("Taker buy order {} fully filled.", order.id);
                } else {
                    // If it traded *some* amount, it's PartiallyFilled
                    if order.remaining_quantity < order.quantity { taker_final_status = OrderStatus::PartiallyFilled; }
                    // Otherwise, its status remains Accepted (if it didn't trade at all)

                    log::info!("Adding resting buy order {} to book. Status: {:?}, Rem: {}", order.id, taker_final_status, order.remaining_quantity);
                    order.status = taker_final_status; // Update the order's status
                    // Add the (potentially partially filled) order to the bid side
                    self.bids.entry(order.price).or_default().push_back(order); // order is moved here
                }
                // TODO: Publish maker order updates based on makers_touched
            }
            OrderSide::Sell => {
                 let mut bids_to_remove = Vec::new();
                 // let mut makers_touched = Vec::new();

                 // Iterate mutable bids, highest price first (.rev())
                 for (&bid_price, price_level) in self.bids.iter_mut().rev() {
                     if order.remaining_quantity == 0 { break; } // Taker filled
                     if bid_price < order.price { break; } // Price level too low

                     // let mut makers_in_level_touched = Vec::new();
                     for maker_order in price_level.iter_mut() {
                        if order.remaining_quantity == 0 { break; }
                         let trade_quantity = std::cmp::min(order.remaining_quantity, maker_order.remaining_quantity);
                        if trade_quantity > 0 {
                            trades.push(Trade::new(self.symbol.clone(), maker_order.price, trade_quantity, order.id, maker_order.id));
                            order.remaining_quantity -= trade_quantity;
                            maker_order.remaining_quantity -= trade_quantity;
                            maker_order.status = if maker_order.remaining_quantity == 0 { OrderStatus::Filled } else { OrderStatus::PartiallyFilled };
                             log::debug!("Maker bid {} status -> {:?}, Rem: {}", maker_order.id, maker_order.status, maker_order.remaining_quantity);
                            // makers_in_level_touched.push(maker_order.clone());
                        }
                    }
                    // makers_touched.append(&mut makers_in_level_touched);
                    price_level.retain(|o| o.status != OrderStatus::Filled);
                    if price_level.is_empty() { bids_to_remove.push(bid_price); }
                }
                 for price in bids_to_remove { self.bids.remove(&price); log::debug!("Removed empty bid level: {}", price); }

                // --- Determine Taker's Final State ---
                if order.remaining_quantity == 0 {
                    taker_final_status = OrderStatus::Filled;
                    log::info!("Taker sell order {} fully filled.", order.id);
                } else {
                     if order.remaining_quantity < order.quantity { taker_final_status = OrderStatus::PartiallyFilled; }
                     // else { taker_final_status = OrderStatus::Accepted; }

                     log::info!("Adding resting sell order {} to book. Status: {:?}, Rem: {}", order.id, taker_final_status, order.remaining_quantity);
                     order.status = taker_final_status;
                    self.asks.entry(order.price).or_default().push_back(order); // order is moved here
                }
                 // TODO: Publish maker order updates based on makers_touched
            }
        }
        (taker_final_status, trades) // Return final status of the taker order & generated trades
    }
}

// --- Unit Tests ---
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
    #[test] fn test_clear_book() { setup_logging(); let mut book = OrderBook::new("TEST".to_string()); book.add_order(create_test_order(OrderSide::Buy, dec!(99.0), 10)); book.add_order(create_test_order(OrderSide::Sell, dec!(100.0), 8)); book.last_bbo = Some(BboUpdate::new("TEST".to_string(), None, None, None, None)); book.last_snapshot = Some(OrderBookSnapshot::new("TEST".to_string(), vec![], vec![])); book.clear_book(); assert!(book.bids.is_empty() && book.asks.is_empty()); assert!(book.last_bbo.is_none()); assert!(book.last_snapshot.is_none()); let (bp,bq,ap,aq) = book.get_bbo_with_qty(); assert!(bp.is_none() && bq.is_none() && ap.is_none() && aq.is_none()); assert!(book.get_snapshot(5).bids.is_empty() && book.get_snapshot(5).asks.is_empty()); }
}