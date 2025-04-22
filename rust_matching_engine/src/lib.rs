// rust_matching_engine/src/lib.rs
use rust_decimal::Decimal;
use rust_decimal_macros::dec;
use chrono::{DateTime, Utc};
use uuid::Uuid;
// Import Serializer/Deserializer traits
use serde::{Deserialize, Serialize, Serializer, Deserializer};
use std::collections::{BTreeMap, VecDeque};

// --- Enums ---

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, PartialOrd, Ord)]
#[serde(rename_all = "lowercase")] // Handles "buy"/"sell" from JSON
pub enum OrderSide {
    Buy,
    Sell,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")] // Handles "new", "filled", etc. from JSON
pub enum OrderStatus {
    #[default] // Mark New as the default variant for derive(Default)
    New,
    Accepted,     // Order added to the book (internal state change)
    Rejected,     // Order invalid or rejected (e.g., by risk)
    Filled,       // Order completely filled
    PartiallyFilled, // Order partially filled
    Cancelled,    // Order cancelled (logic TBD)
}

// --- Core Structures ---

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Order {
    // *** REMOVED default attribute: Expect ID from incoming JSON ***
    pub id: Uuid,
    pub side: OrderSide,
    pub symbol: String,
    #[serde(with = "rust_decimal::serde::str")]
    pub price: Decimal,
    pub quantity: u64,
    pub timestamp: DateTime<Utc>, // Ensure FastAPI sends this
    #[serde(default)] // Keep default for status
    pub status: OrderStatus,
    // Use default + alias to initialize from quantity if missing/null
    #[serde(default, alias = "quantity")]
    pub remaining_quantity: u64,
}

// Example helper if more complex initialization needed post-deserialization
// Not strictly required with the serde attribute above, but shows pattern
impl Order {
    pub fn ensure_remaining_quantity(&mut self) {
         // If remaining is 0 or nonsensical, reset it to initial quantity
         if self.remaining_quantity == 0 || self.remaining_quantity > self.quantity {
             self.remaining_quantity = self.quantity;
         }
         // If status is New, ensure remaining is same as quantity
         if self.status == OrderStatus::New {
            self.remaining_quantity = self.quantity;
         }
    }
    // Keep original constructor if needed for tests/internal use
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
            remaining_quantity: quantity, // Initialize remaining quantity
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
    pub taker_order_id: Uuid, // Should now match the original IDs
    pub maker_order_id: Uuid, // Should now match the original IDs
    pub timestamp: DateTime<Utc>,
}

impl Trade {
     pub fn new(symbol: String, price: Decimal, quantity: u64, taker_order_id: Uuid, maker_order_id: Uuid) -> Self {
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

// --- Custom Serde Helper for Option<Decimal> as String/Null ---
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
        let opt_str = Option::<String>::deserialize(deserializer)?;
        match opt_str {
            // Use from_str which handles standard decimal strings
            Some(s) => Decimal::from_str_radix(&s, 10).map(Some).map_err(serde::de::Error::custom),
            None => Ok(None),
        }
    }
}


// --- BBO Update Structure ---
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct BboUpdate {
    pub symbol: String,
    #[serde(default, with = "decimal_option_serde_as_string")] // Add default, use custom helper
    pub bid_price: Option<Decimal>,
    pub bid_qty: Option<u64>,
    #[serde(default, with = "decimal_option_serde_as_string")] // Add default, use custom helper
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
            bid_qty: bid_qty.filter(|&q| q > 0), // Filter out 0 qty
            ask_price,
            ask_qty: ask_qty.filter(|&q| q > 0), // Filter out 0 qty
            timestamp: Utc::now(),
        }
    }
}

// --- Order Book Snapshot Structures ---

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PriceLevelInfo {
    #[serde(with = "rust_decimal::serde::str")] // Serialize price as string
    pub price: Decimal,
    pub quantity: u64, // Total quantity at this level
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct OrderBookSnapshot {
    pub symbol: String,
    pub bids: Vec<PriceLevelInfo>, // Top N bids (highest prices first)
    pub asks: Vec<PriceLevelInfo>, // Top N asks (lowest prices first)
    pub timestamp: DateTime<Utc>,
}

impl OrderBookSnapshot {
    pub fn new(symbol: String, bids: Vec<PriceLevelInfo>, asks: Vec<PriceLevelInfo>) -> Self {
        OrderBookSnapshot {
            symbol,
            bids,
            asks,
            timestamp: Utc::now(),
        }
    }
}


// --- Order Book Logic ---

type PriceLevel = VecDeque<Order>; // Queue of orders at a specific price

#[derive(Debug, Default)]
pub struct OrderBook {
    symbol: String,
    bids: BTreeMap<Decimal, PriceLevel>, // Highest bid is last key
    asks: BTreeMap<Decimal, PriceLevel>, // Lowest ask is first key
    last_bbo: Option<BboUpdate>,
    last_snapshot: Option<OrderBookSnapshot>,
}

impl OrderBook {
    pub fn new(symbol: String) -> Self {
        OrderBook {
            symbol,
            bids: BTreeMap::new(),
            asks: BTreeMap::new(),
            last_bbo: None,
            last_snapshot: None,
        }
    }

    pub fn symbol(&self) -> &str { &self.symbol }
    pub fn last_bbo(&self) -> &Option<BboUpdate> { &self.last_bbo }
    pub fn last_bbo_mut(&mut self) -> &mut Option<BboUpdate> { &mut self.last_bbo }
    pub fn last_snapshot(&self) -> &Option<OrderBookSnapshot> { &self.last_snapshot }
    pub fn last_snapshot_mut(&mut self) -> &mut Option<OrderBookSnapshot> { &mut self.last_snapshot }

    pub fn get_bbo_with_qty(&self) -> (Option<Decimal>, Option<u64>, Option<Decimal>, Option<u64>) {
        let best_bid_price = self.bids.keys().last().cloned();
        let best_bid_qty = best_bid_price.and_then(|price| {
            self.bids.get(&price).map(|level| level.iter().map(|o| o.remaining_quantity).sum())
        });

        let best_ask_price = self.asks.keys().next().cloned();
        let best_ask_qty = best_ask_price.and_then(|price| {
            self.asks.get(&price).map(|level| level.iter().map(|o| o.remaining_quantity).sum())
        });

        (best_bid_price, best_bid_qty.filter(|&q| q > 0), best_ask_price, best_ask_qty.filter(|&q| q > 0))
    }

    pub fn get_snapshot(&self, depth: usize) -> OrderBookSnapshot {
        let bids_snapshot: Vec<PriceLevelInfo> = self.bids.iter().rev()
            .take(depth)
            .map(|(&price, level)| PriceLevelInfo {
                price,
                quantity: level.iter().map(|order| order.remaining_quantity).sum(),
            })
            .filter(|level| level.quantity > 0)
            .collect();

        let asks_snapshot: Vec<PriceLevelInfo> = self.asks.iter()
            .take(depth)
            .map(|(&price, level)| PriceLevelInfo {
                price,
                quantity: level.iter().map(|order| order.remaining_quantity).sum(),
            })
            .filter(|level| level.quantity > 0)
            .collect();

        OrderBookSnapshot::new(self.symbol.clone(), bids_snapshot, asks_snapshot)
    }


    // --- Core Matching Logic ---
    pub fn add_order(&mut self, mut order: Order) -> (OrderStatus, Vec<Trade>) {
        // Ensure remaining quantity is sensible after deserialization
        order.ensure_remaining_quantity();

        // Basic validation
        if order.symbol != self.symbol {
            log::error!("Mismatched symbols: Order '{}' vs Book '{}'", order.symbol, self.symbol);
            order.status = OrderStatus::Rejected;
            return (order.status, vec![]);
        }
        if order.price <= dec!(0) || order.quantity == 0 {
             log::error!("Order has invalid price or quantity: {:?}", order);
             order.status = OrderStatus::Rejected;
            return (order.status, vec![]);
        }

        // If it arrived with New status, mark as Accepted by the book
        if order.status == OrderStatus::New {
             order.status = OrderStatus::Accepted;
        }

        log::info!("Processing order: Id={}, Side={:?}, Price={}, Qty={}, Rem={}",
                 order.id, order.side, order.price, order.quantity, order.remaining_quantity);

        let mut trades = Vec::new();
        let mut final_status = order.status; // Start with current status

        match order.side {
            OrderSide::Buy => {
                let mut asks_to_remove = Vec::new();
                let mut makers_touched = Vec::new(); // Track makers for order updates

                // Iterate through ask levels that can be matched
                for (&ask_price, price_level) in self.asks.iter_mut() {
                    if order.remaining_quantity == 0 || ask_price > order.price { break; }

                    let mut makers_in_level_touched = Vec::new();
                    for maker_order in price_level.iter_mut() {
                        if order.remaining_quantity == 0 { break; }

                        let trade_quantity = std::cmp::min(order.remaining_quantity, maker_order.remaining_quantity);
                        if trade_quantity > 0 {
                            // Create trade: Taker is the incoming order, Maker is from book
                            trades.push(Trade::new(self.symbol.clone(), maker_order.price, trade_quantity, order.id, maker_order.id));

                            // Update quantities
                            order.remaining_quantity -= trade_quantity;
                            maker_order.remaining_quantity -= trade_quantity;

                            // Update maker status
                            maker_order.status = if maker_order.remaining_quantity == 0 { OrderStatus::Filled } else { OrderStatus::PartiallyFilled };
                            log::debug!("Maker order {} status updated to {:?}", maker_order.id, maker_order.status);
                            // Add a clone for publishing update later
                            makers_in_level_touched.push(maker_order.clone());
                        }
                    }
                    // Add all touched makers from this level to the main list
                    makers_touched.append(&mut makers_in_level_touched);

                    // Clean up filled orders within the level
                    price_level.retain(|o| o.status != OrderStatus::Filled);
                    if price_level.is_empty() { asks_to_remove.push(ask_price); }
                }
                // Remove empty price levels from the book
                for price in asks_to_remove { self.asks.remove(&price); log::debug!("Removed empty ask level at price {}", price); }

                // Determine final status of the incoming (taker) order
                if order.remaining_quantity == 0 {
                    final_status = OrderStatus::Filled;
                    log::info!("Taker buy order {} fully filled.", order.id);
                } else {
                    if order.remaining_quantity < order.quantity { final_status = OrderStatus::PartiallyFilled; }
                    else { final_status = OrderStatus::Accepted; } // Remained accepted if no fill
                    log::info!("Adding resting buy order {} to book. Status: {:?}, Rem: {}", order.id, final_status, order.remaining_quantity);
                    order.status = final_status; // Update the order struct itself
                    self.bids.entry(order.price).or_default().push_back(order.clone()); // Store clone in book
                }

                // TODO: Publish updates for makers_touched
            }
            OrderSide::Sell => {
                let mut bids_to_remove = Vec::new();
                let mut makers_touched = Vec::new(); // Track makers for order updates

                // Iterate through bid levels (highest first) that can be matched
                for (&bid_price, price_level) in self.bids.iter_mut().rev() { // .rev() for highest bids first
                     if order.remaining_quantity == 0 || bid_price < order.price { break; }

                     let mut makers_in_level_touched = Vec::new();
                     for maker_order in price_level.iter_mut() {
                        if order.remaining_quantity == 0 { break; }

                         let trade_quantity = std::cmp::min(order.remaining_quantity, maker_order.remaining_quantity);
                        if trade_quantity > 0 {
                            // Create trade: Taker is the incoming order, Maker is from book
                            trades.push(Trade::new(self.symbol.clone(), maker_order.price, trade_quantity, order.id, maker_order.id));

                            // Update quantities
                            order.remaining_quantity -= trade_quantity;
                            maker_order.remaining_quantity -= trade_quantity;

                            // Update maker status
                            maker_order.status = if maker_order.remaining_quantity == 0 { OrderStatus::Filled } else { OrderStatus::PartiallyFilled };
                             log::debug!("Maker order {} status updated to {:?}", maker_order.id, maker_order.status);
                             // Add a clone for publishing update later
                            makers_in_level_touched.push(maker_order.clone());
                        }
                    }
                    // Add all touched makers from this level to the main list
                    makers_touched.append(&mut makers_in_level_touched);

                    // Clean up filled orders within the level
                    price_level.retain(|o| o.status != OrderStatus::Filled);
                    if price_level.is_empty() { bids_to_remove.push(bid_price); }
                }
                // Remove empty price levels from the book
                 for price in bids_to_remove { self.bids.remove(&price); log::debug!("Removed empty bid level at price {}", price); }

                // Determine final status of the incoming (taker) order
                if order.remaining_quantity == 0 {
                    final_status = OrderStatus::Filled;
                    log::info!("Taker sell order {} fully filled.", order.id);
                } else {
                     if order.remaining_quantity < order.quantity { final_status = OrderStatus::PartiallyFilled; }
                     else { final_status = OrderStatus::Accepted; } // Remained accepted if no fill
                     log::info!("Adding resting sell order {} to book. Status: {:?}, Rem: {}", order.id, final_status, order.remaining_quantity);
                     order.status = final_status; // Update the order struct itself
                    self.asks.entry(order.price).or_default().push_back(order.clone()); // Store clone in book
                }
                 // TODO: Publish updates for makers_touched
            }
        }
        (final_status, trades) // Return taker's final status and generated trades
        // Note: Maker order updates need separate handling/publishing mechanism
    }

} // End impl OrderBook


// --- Unit Tests ---
#[cfg(test)]
mod tests {
    use super::*;
    use rust_decimal_macros::dec;
    // Remove unused imports: use std::time::Duration;
    use std::sync::Once;

    static INIT: Once = Once::new();
    fn setup_logging() {
        INIT.call_once(|| { let _ = env_logger::builder().is_test(true).try_init(); });
    }

    // --- Helper to create a basic order for tests ---
    fn create_test_order(side: OrderSide, price: Decimal, qty: u64) -> Order {
        Order {
            id: Uuid::new_v4(),
            symbol: "TEST".to_string(),
            side,
            price,
            quantity: qty,
            timestamp: Utc::now(),
            status: OrderStatus::New,
            remaining_quantity: qty,
        }
    }

    #[test]
    fn test_add_order_empty_book() {
        setup_logging();
        let mut book = OrderBook::new("TEST".to_string());
        let order = create_test_order(OrderSide::Buy, dec!(100.0), 10);
        let order_id = order.id;

        let (status, trades) = book.add_order(order);

        assert_eq!(status, OrderStatus::Accepted); // Should just be added to the book
        assert!(trades.is_empty());
        assert_eq!(book.bids.len(), 1);
        assert_eq!(book.asks.len(), 0);
        assert_eq!(book.bids[&dec!(100.0)].front().unwrap().id, order_id);
        assert_eq!(book.bids[&dec!(100.0)].front().unwrap().remaining_quantity, 10);
    }

    #[test]
    fn test_simple_full_match() {
        setup_logging();
        let mut book = OrderBook::new("TEST".to_string());
        let ask_order = create_test_order(OrderSide::Sell, dec!(100.0), 10);
        let ask_id = ask_order.id;
        book.add_order(ask_order); // Add initial ask

        let buy_order = create_test_order(OrderSide::Buy, dec!(100.0), 10);
        let buy_id = buy_order.id;
        let (status, trades) = book.add_order(buy_order);

        assert_eq!(status, OrderStatus::Filled); // Taker order should be fully filled
        assert_eq!(trades.len(), 1);
        assert_eq!(trades[0].quantity, 10);
        assert_eq!(trades[0].price, dec!(100.0));
        assert_eq!(trades[0].taker_order_id, buy_id);
        assert_eq!(trades[0].maker_order_id, ask_id);
        assert!(book.asks.is_empty()); // Maker should be gone
        assert!(book.bids.is_empty()); // Taker shouldn't rest
    }

     #[test]
    fn test_partial_match_taker_remaining() {
        setup_logging();
        let mut book = OrderBook::new("TEST".to_string());
        let ask_order = create_test_order(OrderSide::Sell, dec!(100.0), 5); // Maker has 5
        let ask_id = ask_order.id;
        book.add_order(ask_order);

        let buy_order = create_test_order(OrderSide::Buy, dec!(100.0), 10); // Taker wants 10
        let buy_id = buy_order.id;
        let (status, trades) = book.add_order(buy_order);

        assert_eq!(status, OrderStatus::PartiallyFilled); // Taker partially filled, rests
        assert_eq!(trades.len(), 1);
        assert_eq!(trades[0].quantity, 5); // Trade quantity is maker's qty
        assert_eq!(trades[0].price, dec!(100.0));
        assert_eq!(trades[0].taker_order_id, buy_id);
        assert_eq!(trades[0].maker_order_id, ask_id);
        assert!(book.asks.is_empty()); // Maker should be gone
        assert_eq!(book.bids.len(), 1); // Taker should rest
        assert_eq!(book.bids[&dec!(100.0)].front().unwrap().id, buy_id);
        assert_eq!(book.bids[&dec!(100.0)].front().unwrap().remaining_quantity, 5); // Taker remaining 5
    }

    #[test]
    fn test_partial_match_maker_remaining() {
        setup_logging();
        let mut book = OrderBook::new("TEST".to_string());
        let ask_order = create_test_order(OrderSide::Sell, dec!(100.0), 15); // Maker has 15
        let ask_id = ask_order.id;
        book.add_order(ask_order);

        let buy_order = create_test_order(OrderSide::Buy, dec!(100.0), 10); // Taker wants 10
        let buy_id = buy_order.id;
        let (status, trades) = book.add_order(buy_order);

        assert_eq!(status, OrderStatus::Filled); // Taker fully filled
        assert_eq!(trades.len(), 1);
        assert_eq!(trades[0].quantity, 10); // Trade quantity is taker's qty
        assert_eq!(trades[0].price, dec!(100.0));
        assert_eq!(trades[0].taker_order_id, buy_id);
        assert_eq!(trades[0].maker_order_id, ask_id);
        assert_eq!(book.asks.len(), 1); // Maker should remain
        assert_eq!(book.asks[&dec!(100.0)].front().unwrap().id, ask_id);
        assert_eq!(book.asks[&dec!(100.0)].front().unwrap().remaining_quantity, 5); // Maker remaining 5
        assert!(book.bids.is_empty()); // Taker shouldn't rest
    }

    #[test]
    fn test_match_multiple_makers_at_same_price() {
        setup_logging();
        let mut book = OrderBook::new("TEST".to_string());
        let ask1 = create_test_order(OrderSide::Sell, dec!(100.0), 5);
        let ask1_id = ask1.id;
        book.add_order(ask1);
        // std::thread::sleep(Duration::from_millis(1)); // Ensure time priority
        let ask2 = create_test_order(OrderSide::Sell, dec!(100.0), 8);
        let ask2_id = ask2.id;
        book.add_order(ask2);

        let buy_order = create_test_order(OrderSide::Buy, dec!(100.0), 10); // Taker wants 10
        let buy_id = buy_order.id;
        let (status, trades) = book.add_order(buy_order);

        assert_eq!(status, OrderStatus::Filled); // Taker fully filled
        assert_eq!(trades.len(), 2); // Should match both makers

        // Trade 1 (against ask1 - first in time)
        assert_eq!(trades[0].quantity, 5);
        assert_eq!(trades[0].price, dec!(100.0));
        assert_eq!(trades[0].taker_order_id, buy_id);
        assert_eq!(trades[0].maker_order_id, ask1_id);

        // Trade 2 (against ask2)
        assert_eq!(trades[1].quantity, 5); // Takes 5 from ask2
        assert_eq!(trades[1].price, dec!(100.0));
        assert_eq!(trades[1].taker_order_id, buy_id);
        assert_eq!(trades[1].maker_order_id, ask2_id);


        assert_eq!(book.asks.len(), 1); // Level should still exist
        assert_eq!(book.asks[&dec!(100.0)].len(), 1); // Only ask2 should remain
        assert_eq!(book.asks[&dec!(100.0)].front().unwrap().id, ask2_id);
        assert_eq!(book.asks[&dec!(100.0)].front().unwrap().remaining_quantity, 3); // ask2 remaining 3
        assert!(book.bids.is_empty()); // Taker shouldn't rest
    }

     #[test]
    fn test_match_multiple_price_levels() {
        setup_logging();
        let mut book = OrderBook::new("TEST".to_string());
        // Add asks at different prices
        let ask1 = create_test_order(OrderSide::Sell, dec!(100.0), 5); // Best ask
        let ask1_id = ask1.id;
        book.add_order(ask1);
        let ask2 = create_test_order(OrderSide::Sell, dec!(100.1), 8);
        let ask2_id = ask2.id;
        book.add_order(ask2);

        let buy_order = create_test_order(OrderSide::Buy, dec!(100.1), 10); // Taker willing to pay 100.1, wants 10
        let buy_id = buy_order.id;
        let (status, trades) = book.add_order(buy_order);

        assert_eq!(status, OrderStatus::Filled); // Taker fully filled
        assert_eq!(trades.len(), 2); // Matches both levels

        // Trade 1 (against best ask price 100.0)
        assert_eq!(trades[0].quantity, 5);
        assert_eq!(trades[0].price, dec!(100.0)); // Trade occurs at maker's price
        assert_eq!(trades[0].taker_order_id, buy_id);
        assert_eq!(trades[0].maker_order_id, ask1_id);

        // Trade 2 (against next ask price 100.1)
        assert_eq!(trades[1].quantity, 5); // Takes remaining 5 from ask2
        assert_eq!(trades[1].price, dec!(100.1)); // Trade occurs at maker's price
        assert_eq!(trades[1].taker_order_id, buy_id);
        assert_eq!(trades[1].maker_order_id, ask2_id);

        assert_eq!(book.asks.len(), 1); // Level 100.1 should still exist
        assert_eq!(book.asks[&dec!(100.1)].len(), 1); // ask2 should remain
        assert_eq!(book.asks[&dec!(100.1)].front().unwrap().id, ask2_id);
        assert_eq!(book.asks[&dec!(100.1)].front().unwrap().remaining_quantity, 3); // ask2 remaining 3
        assert!(book.bids.is_empty()); // Taker shouldn't rest
    }

    #[test]
    fn test_add_invalid_order_rejected() {
        setup_logging();
        let mut book = OrderBook::new("TEST".to_string());

        // Zero price
        let order1 = create_test_order(OrderSide::Buy, dec!(0), 10);
        let (status1, trades1) = book.add_order(order1);
        assert_eq!(status1, OrderStatus::Rejected);
        assert!(trades1.is_empty());

        // Zero quantity
        let order2 = create_test_order(OrderSide::Sell, dec!(100.0), 0);
        let (status2, trades2) = book.add_order(order2);
        assert_eq!(status2, OrderStatus::Rejected);
        assert!(trades2.is_empty());

        // Mismatched symbol
        let mut order3 = create_test_order(OrderSide::Buy, dec!(100.0), 10);
        order3.symbol = "OTHER".to_string();
        let (status3, trades3) = book.add_order(order3);
        assert_eq!(status3, OrderStatus::Rejected);
        assert!(trades3.is_empty());

        assert!(book.bids.is_empty() && book.asks.is_empty());
    }

    #[test]
    fn test_get_bbo_with_qty_logic() {
        setup_logging();
        let mut book = OrderBook::new("TEST".to_string());

        // Empty book
        assert_eq!(book.get_bbo_with_qty(), (None, None, None, None));

        // Only bids
        book.add_order(create_test_order(OrderSide::Buy, dec!(99.8), 5));
        book.add_order(create_test_order(OrderSide::Buy, dec!(99.7), 10));
        book.add_order(create_test_order(OrderSide::Buy, dec!(99.8), 7));
        assert_eq!(book.get_bbo_with_qty(), (Some(dec!(99.8)), Some(12), None, None));

        // Add asks
        book.add_order(create_test_order(OrderSide::Sell, dec!(100.1), 9));
        book.add_order(create_test_order(OrderSide::Sell, dec!(100.2), 15));
        book.add_order(create_test_order(OrderSide::Sell, dec!(100.1), 11));
        assert_eq!(book.get_bbo_with_qty(), (Some(dec!(99.8)), Some(12), Some(dec!(100.1)), Some(20)));

        // Fill one side completely
        let taker_buy = create_test_order(OrderSide::Buy, dec!(101.0), 100); // Takes all asks
        book.add_order(taker_buy);
        assert_eq!(book.get_bbo_with_qty(), (Some(dec!(99.8)), Some(12), None, None)); // Only bids left (and resting part of taker if any)
    }

    #[test]
    fn test_get_snapshot() {
        setup_logging();
        let mut book = OrderBook::new("TEST".to_string());
        let depth = 3;

        // Add bids
        book.add_order(create_test_order(OrderSide::Buy, dec!(99.8), 5));
        book.add_order(create_test_order(OrderSide::Buy, dec!(99.7), 10));
        book.add_order(create_test_order(OrderSide::Buy, dec!(99.8), 7)); // Aggregate @ 99.8
        book.add_order(create_test_order(OrderSide::Buy, dec!(99.5), 20));
        book.add_order(create_test_order(OrderSide::Buy, dec!(99.6), 8));

        // Add asks
        book.add_order(create_test_order(OrderSide::Sell, dec!(100.2), 15));
        book.add_order(create_test_order(OrderSide::Sell, dec!(100.1), 9));
        book.add_order(create_test_order(OrderSide::Sell, dec!(100.3), 25));
        book.add_order(create_test_order(OrderSide::Sell, dec!(100.1), 11)); // Aggregate @ 100.1
        book.add_order(create_test_order(OrderSide::Sell, dec!(100.5), 30));

        let snapshot = book.get_snapshot(depth);

        assert_eq!(snapshot.symbol, "TEST");
        assert!(snapshot.bids.len() <= depth);
        assert!(snapshot.asks.len() <= depth);
        assert_eq!(snapshot.bids.len(), 3); // Expecting 3 distinct non-empty bid levels
        assert_eq!(snapshot.asks.len(), 3); // Expecting 3 distinct non-empty ask levels


        // Verify bids (highest first)
        assert_eq!(snapshot.bids[0].price, dec!(99.8));
        assert_eq!(snapshot.bids[0].quantity, 12); // 5 + 7
        assert_eq!(snapshot.bids[1].price, dec!(99.7));
        assert_eq!(snapshot.bids[1].quantity, 10);
        assert_eq!(snapshot.bids[2].price, dec!(99.6));
        assert_eq!(snapshot.bids[2].quantity, 8);

        // Verify asks (lowest first)
        assert_eq!(snapshot.asks[0].price, dec!(100.1));
        assert_eq!(snapshot.asks[0].quantity, 20); // 9 + 11
        assert_eq!(snapshot.asks[1].price, dec!(100.2));
        assert_eq!(snapshot.asks[1].quantity, 15);
        assert_eq!(snapshot.asks[2].price, dec!(100.3));
        assert_eq!(snapshot.asks[2].quantity, 25);
    }
} // End tests module