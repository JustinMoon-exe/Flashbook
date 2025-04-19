// rust_matching_engine/src/lib.rs
use rust_decimal::Decimal;
use rust_decimal_macros::dec;
use chrono::{DateTime, Utc};
use uuid::Uuid;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, VecDeque};
// Removed unused std::cmp::Ordering

// --- Enums ---

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, PartialOrd, Ord)]
#[serde(rename_all = "lowercase")] // Handles "buy"/"sell" from JSON
pub enum OrderSide {
    Buy,
    Sell,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
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
    // Use Uuid type, Serde handles string conversion automatically
    #[serde(default = "Uuid::new_v4")]
    pub id: Uuid,
    pub side: OrderSide,
    pub symbol: String,
    // Ensure this attribute is present to handle string price from JSON
    #[serde(with = "rust_decimal::serde::str")]
    pub price: Decimal,
    pub quantity: u64,
    pub timestamp: DateTime<Utc>,
    #[serde(default)] // Uses OrderStatus::default() which is New
    pub status: OrderStatus,
    // Ensure remaining_quantity is deserialized/serialized
    // Default makes sense if Python model always initializes it
    #[serde(default)]
    pub remaining_quantity: u64,
}

impl Order {
    // Constructor might need update if Python sends remaining_quantity
    pub fn new(side: OrderSide, symbol: String, price: Decimal, quantity: u64) -> Self {
        Order {
            id: Uuid::new_v4(),
            side,
            symbol,
            price,
            quantity,
            timestamp: Utc::now(),
            status: OrderStatus::New,
            remaining_quantity: quantity, // Initialize remaining here
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Trade {
    pub trade_id: Uuid,
    pub symbol: String,
    // Ensure this attribute is present to handle string price from JSON
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

// --- Order Book Logic ---

type PriceLevel = VecDeque<Order>; // Queue of orders at a specific price

#[derive(Debug, Default)]
pub struct OrderBook {
    symbol: String,
    // Bids: Use BTreeMap for sorted prices (ascending). Iterate reversed for best bid.
    bids: BTreeMap<Decimal, PriceLevel>,
    // Asks: Use BTreeMap for sorted prices (ascending). Iterate normally for best ask.
    asks: BTreeMap<Decimal, PriceLevel>,
}

impl OrderBook {
    pub fn new(symbol: String) -> Self {
        OrderBook {
            symbol,
            bids: BTreeMap::new(),
            asks: BTreeMap::new(),
        }
    }

    pub fn symbol(&self) -> &str {
        &self.symbol
    }

    // --- Core Matching Logic ---
    // Returns the final status of the incoming order and any trades generated.
    pub fn add_order(&mut self, mut order: Order) -> (OrderStatus, Vec<Trade>) {
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

        // Initialize remaining_quantity if it wasn't set correctly (safety net)
        if order.remaining_quantity == 0 || order.remaining_quantity > order.quantity {
             order.remaining_quantity = order.quantity;
        }
        // Mark as Accepted before attempting matching (if it passes initial checks)
        order.status = OrderStatus::Accepted;

        log::info!("Processing order: Id={}, Side={:?}, Price={}, Qty={}, Rem={}",
                 order.id, order.side, order.price, order.quantity, order.remaining_quantity);

        let mut trades = Vec::new();
        let mut final_status = order.status; // Start with Accepted

        match order.side {
            OrderSide::Buy => {
                // Match against asks (lowest asking price first)
                let mut asks_to_remove = Vec::new(); // Price levels to remove after iteration

                // Iterate over ask levels mutably, lowest price first
                for (&ask_price, price_level) in self.asks.iter_mut() {
                    if order.remaining_quantity == 0 || ask_price > order.price {
                        break; // Stop if taker filled or asks are too expensive
                    }

                    // Process orders within this price level (FIFO)
                    for maker_order in price_level.iter_mut() {
                        if order.remaining_quantity == 0 { break; } // Taker filled

                        let trade_quantity = std::cmp::min(order.remaining_quantity, maker_order.remaining_quantity);

                        if trade_quantity > 0 {
                            let trade = Trade::new(
                                self.symbol.clone(),
                                maker_order.price, // Trade at the resting maker's price
                                trade_quantity,
                                order.id,       // Taker is the incoming buy order
                                maker_order.id, // Maker is the resting sell order
                            );
                            log::debug!("Generated Trade: {:?}", trade);
                            trades.push(trade);

                            // Update quantities
                            order.remaining_quantity -= trade_quantity;
                            maker_order.remaining_quantity -= trade_quantity;

                            // Update maker order status
                            if maker_order.remaining_quantity == 0 {
                                maker_order.status = OrderStatus::Filled;
                                log::debug!("Maker order {} fully filled.", maker_order.id);
                            } else {
                                maker_order.status = OrderStatus::PartiallyFilled;
                                log::debug!("Maker order {} partially filled, remaining: {}.", maker_order.id, maker_order.remaining_quantity);
                            }
                        }
                    } // End loop through orders at this price level

                    // Remove fully filled maker orders from the front of the queue
                    price_level.retain(|o| o.status != OrderStatus::Filled);

                    // If the price level queue is now empty, mark it for removal
                    if price_level.is_empty() {
                        asks_to_remove.push(ask_price);
                    }
                } // End loop through ask price levels

                // Remove empty ask levels outside the borrow loop
                for price in asks_to_remove {
                    self.asks.remove(&price);
                    log::debug!("Removed empty ask level at price {}", price);
                }

                // Determine final status for the incoming buy order
                if order.remaining_quantity == 0 {
                    final_status = OrderStatus::Filled;
                    log::info!("Taker buy order {} fully filled.", order.id);
                } else {
                    // If it traded at all, it's partially filled, otherwise just accepted
                    if order.remaining_quantity < order.quantity {
                        final_status = OrderStatus::PartiallyFilled;
                    } // else status remains Accepted

                    log::info!("Adding resting buy order {} to book. Status: {:?}, Rem: {}",
                             order.id, final_status, order.remaining_quantity);
                    // Add remaining part of the buy order to the bids book
                    order.status = final_status; // Update status before adding
                    self.bids.entry(order.price)
                        .or_insert_with(VecDeque::new)
                        .push_back(order); // Add to end of queue (FIFO)
                }
            } // End Buy Side Match

            OrderSide::Sell => {
                // Match against bids (highest bid price first)
                let mut bids_to_remove = Vec::new();

                // Iterate over bid levels mutably, highest price first (using .rev())
                for (&bid_price, price_level) in self.bids.iter_mut().rev() {
                    if order.remaining_quantity == 0 || bid_price < order.price {
                        break; // Stop if taker filled or bids are too low
                    }

                    for maker_order in price_level.iter_mut() {
                        if order.remaining_quantity == 0 { break; }

                        let trade_quantity = std::cmp::min(order.remaining_quantity, maker_order.remaining_quantity);

                        if trade_quantity > 0 {
                            let trade = Trade::new(
                                self.symbol.clone(),
                                maker_order.price, // Trade occurs at the maker's price
                                trade_quantity,
                                order.id,       // Taker is the incoming sell order
                                maker_order.id, // Maker is the resting buy order
                            );
                            log::debug!("Generated Trade: {:?}", trade);
                            trades.push(trade);

                            order.remaining_quantity -= trade_quantity;
                            maker_order.remaining_quantity -= trade_quantity;

                            if maker_order.remaining_quantity == 0 {
                                maker_order.status = OrderStatus::Filled;
                                log::debug!("Maker order {} fully filled.", maker_order.id);
                            } else {
                                maker_order.status = OrderStatus::PartiallyFilled;
                                log::debug!("Maker order {} partially filled, remaining: {}.", maker_order.id, maker_order.remaining_quantity);
                            }
                        }
                    } // End loop through orders at this price level

                    // Remove filled maker orders
                    price_level.retain(|o| o.status != OrderStatus::Filled);

                    if price_level.is_empty() {
                        bids_to_remove.push(bid_price);
                    }
                } // End loop through bid price levels

                // Remove empty bid levels
                for price in bids_to_remove {
                    self.bids.remove(&price);
                    log::debug!("Removed empty bid level at price {}", price);
                }

                // Determine final status for the incoming sell order
                if order.remaining_quantity == 0 {
                    final_status = OrderStatus::Filled;
                    log::info!("Taker sell order {} fully filled.", order.id);
                } else {
                    if order.remaining_quantity < order.quantity {
                        final_status = OrderStatus::PartiallyFilled;
                    } // else status remains Accepted

                    log::info!("Adding resting sell order {} to book. Status: {:?}, Rem: {}",
                             order.id, final_status, order.remaining_quantity);
                    // Add remaining part of the sell order to the asks book
                    order.status = final_status; // Update status before adding
                    self.asks.entry(order.price)
                        .or_insert_with(VecDeque::new)
                        .push_back(order); // Add to end of queue (FIFO)
                }
            } // End Sell Side Match
        } // End match order.side

        (final_status, trades)
    } // End add_order fn

    // Helper to get current best bid/ask (optional for now)
    pub fn get_bbo(&self) -> (Option<Decimal>, Option<Decimal>) {
        let best_bid = self.bids.keys().last().cloned(); // BTreeMap keys are sorted ascending, last is highest
        let best_ask = self.asks.keys().next().cloned(); // BTreeMap keys are sorted ascending, first is lowest
        (best_bid, best_ask)
    }

} // End impl OrderBook


// --- Unit Tests ---
#[cfg(test)]
mod tests {
    use super::*;
    use rust_decimal_macros::dec;
    use std::time::Duration; // For potential delays in tests

    // Helper to initialize logging for tests (run only once)
    use std::sync::Once;
    static INIT: Once = Once::new();
    fn setup_logging() {
        INIT.call_once(|| {
            env_logger::builder().is_test(true).try_init().unwrap();
        });
    }

    #[test]
    fn test_add_order_empty_book() {
        setup_logging();
        let mut book = OrderBook::new("TEST".to_string());
        let buy_order = Order::new(OrderSide::Buy, "TEST".to_string(), dec!(100.0), 10);
        let sell_order = Order::new(OrderSide::Sell, "TEST".to_string(), dec!(101.0), 5);

        let (status_buy, trades_buy) = book.add_order(buy_order.clone());
        assert_eq!(status_buy, OrderStatus::Accepted); // Should just be accepted, not traded
        assert!(trades_buy.is_empty());
        assert_eq!(book.bids.len(), 1);
        assert_eq!(book.bids.get(&dec!(100.0)).unwrap().len(), 1);
        assert_eq!(book.asks.len(), 0);

        let (status_sell, trades_sell) = book.add_order(sell_order.clone());
        assert_eq!(status_sell, OrderStatus::Accepted); // Should just be accepted
        assert!(trades_sell.is_empty());
        assert_eq!(book.asks.len(), 1);
        assert_eq!(book.asks.get(&dec!(101.0)).unwrap().len(), 1);
        assert_eq!(book.bids.len(), 1); // Bid should still be there
    }

    #[test]
    fn test_simple_full_match() {
        setup_logging();
        let mut book = OrderBook::new("TEST".to_string());
        // Maker Sell order rests first
        let maker_sell = Order::new(OrderSide::Sell, "TEST".to_string(), dec!(100.0), 10);
        let (status_maker, _) = book.add_order(maker_sell.clone());
        assert_eq!(status_maker, OrderStatus::Accepted);
        assert_eq!(book.asks.get(&dec!(100.0)).unwrap().len(), 1);

        // Taker Buy order comes in at the same price
        let taker_buy = Order::new(OrderSide::Buy, "TEST".to_string(), dec!(100.0), 10);
        let (status_taker, trades_taker) = book.add_order(taker_buy.clone());

        assert_eq!(status_taker, OrderStatus::Filled); // Taker fully filled
        assert_eq!(trades_taker.len(), 1);

        let trade = &trades_taker[0];
        assert_eq!(trade.price, dec!(100.0)); // Trade at maker price
        assert_eq!(trade.quantity, 10);
        assert_eq!(trade.taker_order_id, taker_buy.id);
        assert_eq!(trade.maker_order_id, maker_sell.id);

        // Book should be empty now
        assert!(book.asks.is_empty());
        assert!(book.bids.is_empty());
    }

    #[test]
    fn test_partial_match_taker_remaining() {
        setup_logging();
        let mut book = OrderBook::new("TEST".to_string());
        let maker_sell = Order::new(OrderSide::Sell, "TEST".to_string(), dec!(100.0), 5); // Maker offers 5
        book.add_order(maker_sell.clone()); // Add maker

        // Taker wants 10
        let taker_buy = Order::new(OrderSide::Buy, "TEST".to_string(), dec!(100.0), 10);
        let (status_taker, trades_taker) = book.add_order(taker_buy.clone());

        assert_eq!(status_taker, OrderStatus::PartiallyFilled); // Taker partially filled, becomes resting bid
        assert_eq!(trades_taker.len(), 1);

        let trade = &trades_taker[0];
        assert_eq!(trade.quantity, 5); // Only 5 could trade
        assert_eq!(trade.maker_order_id, maker_sell.id);

        // Asks should be empty, Bids should have remaining 5 from taker
        assert!(book.asks.is_empty());
        assert_eq!(book.bids.len(), 1);
        let resting_bid_level = book.bids.get(&dec!(100.0)).unwrap();
        assert_eq!(resting_bid_level.len(), 1);
        assert_eq!(resting_bid_level[0].id, taker_buy.id);
        assert_eq!(resting_bid_level[0].remaining_quantity, 5); // 10 - 5 = 5
        assert_eq!(resting_bid_level[0].status, OrderStatus::PartiallyFilled); // Status updated correctly
    }

    #[test]
    fn test_partial_match_maker_remaining() {
        setup_logging();
        let mut book = OrderBook::new("TEST".to_string());
        let maker_sell = Order::new(OrderSide::Sell, "TEST".to_string(), dec!(100.0), 15); // Maker offers 15
        book.add_order(maker_sell.clone());

        // Taker only wants 10
        let taker_buy = Order::new(OrderSide::Buy, "TEST".to_string(), dec!(100.0), 10);
        let (status_taker, trades_taker) = book.add_order(taker_buy.clone());

        assert_eq!(status_taker, OrderStatus::Filled); // Taker fully filled
        assert_eq!(trades_taker.len(), 1);

        let trade = &trades_taker[0];
        assert_eq!(trade.quantity, 10);
        assert_eq!(trade.maker_order_id, maker_sell.id);

        // Bids should be empty, Asks should have remaining 5 from maker
        assert!(book.bids.is_empty());
        assert_eq!(book.asks.len(), 1);
        let resting_ask_level = book.asks.get(&dec!(100.0)).unwrap();
        assert_eq!(resting_ask_level.len(), 1);
        assert_eq!(resting_ask_level[0].id, maker_sell.id);
        assert_eq!(resting_ask_level[0].remaining_quantity, 5); // 15 - 10 = 5
        assert_eq!(resting_ask_level[0].status, OrderStatus::PartiallyFilled);
    }

    #[test]
    fn test_match_multiple_makers_at_same_price() {
        setup_logging();
        let mut book = OrderBook::new("TEST".to_string());
        // Add two resting sell orders at the same price, respecting time priority
        let maker_sell1 = Order::new(OrderSide::Sell, "TEST".to_string(), dec!(100.0), 5);
        std::thread::sleep(Duration::from_millis(1)); // Ensure timestamp difference
        let maker_sell2 = Order::new(OrderSide::Sell, "TEST".to_string(), dec!(100.0), 8);

        book.add_order(maker_sell1.clone());
        book.add_order(maker_sell2.clone());
        assert_eq!(book.asks.get(&dec!(100.0)).unwrap().len(), 2); // Both orders at this level

        // Taker buys 10 shares, should fill maker1 then partially fill maker2
        let taker_buy = Order::new(OrderSide::Buy, "TEST".to_string(), dec!(100.5), 10); // Price crosses book
        let (status_taker, trades_taker) = book.add_order(taker_buy.clone());

        assert_eq!(status_taker, OrderStatus::Filled);
        assert_eq!(trades_taker.len(), 2); // Two distinct trades

        // Trade 1 (against maker1 - first in time)
        assert_eq!(trades_taker[0].quantity, 5);
        assert_eq!(trades_taker[0].price, dec!(100.0));
        assert_eq!(trades_taker[0].maker_order_id, maker_sell1.id);

        // Trade 2 (against maker2 - second in time)
        assert_eq!(trades_taker[1].quantity, 5); // Taker needed 10 total, 5 filled by maker1
        assert_eq!(trades_taker[1].price, dec!(100.0));
        assert_eq!(trades_taker[1].maker_order_id, maker_sell2.id);

        // Book state: Bids empty, Asks should have remaining part of maker2
        assert!(book.bids.is_empty());
        assert_eq!(book.asks.len(), 1);
        let resting_ask_level = book.asks.get(&dec!(100.0)).unwrap();
        assert_eq!(resting_ask_level.len(), 1);
        assert_eq!(resting_ask_level[0].id, maker_sell2.id);
        assert_eq!(resting_ask_level[0].remaining_quantity, 3); // 8 - 5 = 3
        assert_eq!(resting_ask_level[0].status, OrderStatus::PartiallyFilled);
    }

    #[test]
    fn test_match_multiple_price_levels() {
        setup_logging();
        let mut book = OrderBook::new("TEST".to_string());
        // Add resting sells at different prices
        let maker_sell1 = Order::new(OrderSide::Sell, "TEST".to_string(), dec!(100.0), 5); // Best price
        let maker_sell2 = Order::new(OrderSide::Sell, "TEST".to_string(), dec!(100.5), 8); // Next best

        book.add_order(maker_sell1.clone());
        book.add_order(maker_sell2.clone());
        assert_eq!(book.asks.len(), 2); // Two price levels

        // Taker buys 10 shares, price aggressive enough to hit both levels
        let taker_buy = Order::new(OrderSide::Buy, "TEST".to_string(), dec!(101.0), 10);
        let (status_taker, trades_taker) = book.add_order(taker_buy.clone());

        assert_eq!(status_taker, OrderStatus::Filled);
        assert_eq!(trades_taker.len(), 2); // Should trade against both levels

        // Trade 1 (against best price maker1)
        assert_eq!(trades_taker[0].quantity, 5);
        assert_eq!(trades_taker[0].price, dec!(100.0)); // Trade at maker1's price
        assert_eq!(trades_taker[0].maker_order_id, maker_sell1.id);

        // Trade 2 (against next best price maker2)
        assert_eq!(trades_taker[1].quantity, 5); // Remaining 5 for taker
        assert_eq!(trades_taker[1].price, dec!(100.5)); // Trade at maker2's price
        assert_eq!(trades_taker[1].maker_order_id, maker_sell2.id);

        // Book state: Bids empty, Asks should have remaining part of maker2 at 100.5
        assert!(book.bids.is_empty());
        assert_eq!(book.asks.len(), 1); // Only one price level should remain
        let resting_ask_level = book.asks.get(&dec!(100.5)).unwrap();
        assert_eq!(resting_ask_level.len(), 1);
        assert_eq!(resting_ask_level[0].id, maker_sell2.id);
        assert_eq!(resting_ask_level[0].remaining_quantity, 3); // 8 - 5 = 3
        assert_eq!(resting_ask_level[0].status, OrderStatus::PartiallyFilled);
    }

    #[test]
    fn test_add_invalid_order_rejected() {
        setup_logging();
        let mut book = OrderBook::new("TEST".to_string());
        let wrong_symbol = Order::new(OrderSide::Buy, "WRONG_SYMBOL".to_string(), dec!(100.0), 10);
        let zero_price = Order::new(OrderSide::Buy, "TEST".to_string(), dec!(0), 10);
        let zero_quantity = Order::new(OrderSide::Buy, "TEST".to_string(), dec!(100.0), 0);
        let negative_price = Order::new(OrderSide::Buy, "TEST".to_string(), dec!(-50.0), 10);

        let (status1, trades1) = book.add_order(wrong_symbol);
        assert_eq!(status1, OrderStatus::Rejected);
        assert!(trades1.is_empty());

        let (status2, trades2) = book.add_order(zero_price);
        assert_eq!(status2, OrderStatus::Rejected);
        assert!(trades2.is_empty());

        let (status3, trades3) = book.add_order(zero_quantity);
        assert_eq!(status3, OrderStatus::Rejected);
        assert!(trades3.is_empty());

        let (status4, trades4) = book.add_order(negative_price);
        assert_eq!(status4, OrderStatus::Rejected);
        assert!(trades4.is_empty());

        // Ensure book is still empty after rejections
        assert!(book.bids.is_empty());
        assert!(book.asks.is_empty());
    }
}