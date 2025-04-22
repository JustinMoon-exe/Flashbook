// rust_matching_engine/src/bin/subscriber.rs

use tokio::sync::Mutex;
use std::collections::HashMap;
use std::sync::Arc;
use std::env; // For getting environment variables like REDIS_URL
// Remove unused import: use std::time::Duration;

// Import items from our library crate
// Note: Some imports might still show as unused depending on exact usage below
use rust_matching_engine::{
    Order, OrderBook, Trade, OrderStatus, BboUpdate, OrderBookSnapshot, PriceLevelInfo
};

// Import necessary external crates and traits
use futures_util::stream::StreamExt;
use redis::aio::MultiplexedConnection;
use redis::AsyncCommands;

// Redis channel names
const ORDER_SUBMIT_CHANNEL: &str = "orders:new";
const TRADE_EXECUTION_CHANNEL: &str = "trades:executed";
const ORDER_UPDATE_CHANNEL: &str = "orders:updated";
const BBO_UPDATE_CHANNEL_PREFIX: &str = "marketdata:bbo:";
const BOOK_SNAPSHOT_CHANNEL_PREFIX: &str = "marketdata:book:";
const SNAPSHOT_DEPTH: usize = 5; // Order book depth for snapshots

// Type alias for shared order books map
type OrderBookMap = Arc<Mutex<HashMap<String, OrderBook>>>;

#[tokio::main]
async fn main() -> redis::RedisResult<()> {
    // Setup logging
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();
    log::info!("Starting Rust Matching Engine Subscriber...");

    // --- Redis Connection Setup ---
    let redis_url = env::var("REDIS_URL").unwrap_or_else(|_| "redis://127.0.0.1:6379/".to_string());
    log::info!("Connecting to Redis at: {}", redis_url);
    let redis_client = match redis::Client::open(redis_url) {
        Ok(client) => client,
        Err(e) => { log::error!("Failed to open Redis client: {}", e); return Err(e); }
    };

    let publish_conn: MultiplexedConnection = match redis_client.get_multiplexed_async_connection().await {
         Ok(conn) => conn,
         Err(e) => { log::error!("Failed to get multiplexed Redis connection for publishing: {}", e); return Err(e); }
    };
    log::info!("Established multiplexed Redis connection for publishing.");

    let mut pubsub_conn = match redis_client.get_async_connection().await {
         Ok(conn) => conn.into_pubsub(),
         Err(e) => { log::error!("Failed to get Redis connection for PubSub: {}", e); return Err(e); }
    };
    log::info!("Established Redis connection for PubSub.");
    // --- End Redis Connection Setup ---

    // Shared state for order books
    let order_books: OrderBookMap = Arc::new(Mutex::new(HashMap::new()));

    // Subscribe to the new order channel
    match pubsub_conn.subscribe(ORDER_SUBMIT_CHANNEL).await {
        Ok(_) => log::info!("Subscribed to Redis channel: {}", ORDER_SUBMIT_CHANNEL),
        Err(e) => { log::error!("Failed to subscribe to {}: {}", ORDER_SUBMIT_CHANNEL, e); return Err(e); }
    }

    // Get a stream of messages
    let mut msg_stream = pubsub_conn.on_message();

    // --- Main Message Processing Loop ---
    while let Some(msg) = msg_stream.next().await {
        let payload: String = match msg.get_payload() {
             Ok(p) => p,
             Err(e) => { log::error!("Failed to get payload from Redis message: {}", e); continue; }
        };
        log::debug!("Received message payload: {}", payload);

        let order: Order = match serde_json::from_str(&payload) {
            Ok(o) => o,
            Err(e) => { log::error!("Failed to deserialize order JSON: {}. Payload: {}", e, payload); continue; }
        };
        log::info!("Deserialized order: {}", order.id);

        // Spawn a task for processing
        let books_clone = Arc::clone(&order_books);
        let order_clone = order.clone();
        let mut publish_conn_clone = publish_conn.clone();

        tokio::spawn(async move {
            let symbol = order_clone.symbol.clone();
            let mut books_guard = books_clone.lock().await; // Lock mutex

            let book = books_guard.entry(symbol.clone())
                        .or_insert_with(|| { log::info!("Creating new order book for symbol: {}", symbol); OrderBook::new(symbol) });

            // --- Process the Order ---
            let (final_status, trades) = book.add_order(order_clone.clone());
            log::info!("Order {} processed. Status: {:?}, Trades Generated: {}", order_clone.id, final_status, trades.len());

            // --- Check and Publish BBO Update ---
            let (bid_price, bid_qty, ask_price, ask_qty) = book.get_bbo_with_qty();
            let current_bbo = BboUpdate::new(book.symbol().to_string(), bid_price, bid_qty, ask_price, ask_qty);

            let bbo_changed = match book.last_bbo() {
                Some(last) => last.bid_price != current_bbo.bid_price || last.bid_qty != current_bbo.bid_qty || last.ask_price != current_bbo.ask_price || last.ask_qty != current_bbo.ask_qty,
                None => current_bbo.bid_price.is_some() || current_bbo.ask_price.is_some(),
            };

            if bbo_changed {
                log::info!("BBO changed for {}: Bid={:?}({:?}), Ask={:?}({:?})",
                         current_bbo.symbol, current_bbo.bid_price.map(|p| p.to_string()), current_bbo.bid_qty,
                         current_bbo.ask_price.map(|p| p.to_string()), current_bbo.ask_qty);
                *book.last_bbo_mut() = Some(current_bbo.clone()); // Update last known BBO

                match serde_json::to_string(&current_bbo) { // Pass by reference
                     Ok(bbo_json) => {
                         let channel = format!("{}{}", BBO_UPDATE_CHANNEL_PREFIX, current_bbo.symbol);
                         log::debug!("Publishing BBO update to {}: {}", channel, bbo_json);
                         let res: Result<usize, redis::RedisError> = publish_conn_clone.publish(&channel, &bbo_json).await;
                         match res {
                             Ok(count) => log::info!("Published BBO update for {} to {} ({} subscribers)", current_bbo.symbol, channel, count),
                             Err(e) => log::error!("Failed to publish BBO update for {} to Redis: {}", current_bbo.symbol, e),
                         }
                     }
                     Err(e) => { log::error!("Failed to serialize BBO update {:?}: {}", current_bbo.symbol, e); }
                }
            } else {
                log::debug!("BBO unchanged for {}", book.symbol());
            }

            // --- Check and Publish Order Book Snapshot ---
            let current_snapshot = book.get_snapshot(SNAPSHOT_DEPTH);

            // Compare with last published snapshot
            let snapshot_changed = match book.last_snapshot() {
                 // Use '*' to dereference 'last' before comparing with 'current_snapshot'
                 Some(last) => *last != current_snapshot, // <-- CORRECTED COMPARISON
                 None => !current_snapshot.bids.is_empty() || !current_snapshot.asks.is_empty(),
            };

            if snapshot_changed {
                log::info!("Snapshot changed for {}", current_snapshot.symbol);
                 *book.last_snapshot_mut() = Some(current_snapshot.clone()); // Update last known snapshot

                 match serde_json::to_string(&current_snapshot) { // Pass by reference
                     Ok(snapshot_json) => {
                         let channel = format!("{}{}", BOOK_SNAPSHOT_CHANNEL_PREFIX, current_snapshot.symbol);
                         log::debug!("Publishing snapshot update to {}: {}...", channel, &snapshot_json[..std::cmp::min(snapshot_json.len(), 150)]);
                         let res: Result<usize, redis::RedisError> = publish_conn_clone.publish(&channel, &snapshot_json).await;
                         match res {
                             Ok(count) => log::info!("Published snapshot update for {} to {} ({} subscribers)", current_snapshot.symbol, channel, count),
                             Err(e) => log::error!("Failed to publish snapshot update for {} to Redis: {}", current_snapshot.symbol, e),
                         }
                     }
                     Err(e) => { log::error!("Failed to serialize snapshot update {:?}: {}", current_snapshot.symbol, e); }
                 }
            } else {
                log::debug!("Snapshot unchanged for {}", book.symbol());
            }

            // --- Publish Trade Results ---
            for trade in trades {
                match serde_json::to_string(&trade) {
                    Ok(trade_json) => {
                        log::debug!("Publishing trade: {}", trade_json);
                        let res: Result<usize, redis::RedisError> = publish_conn_clone.publish(TRADE_EXECUTION_CHANNEL, &trade_json).await;
                        match res {
                             Ok(count) => log::info!("Published trade {} to {} ({} subscribers)", trade.trade_id, TRADE_EXECUTION_CHANNEL, count),
                             Err(e) => log::error!("Failed to publish trade {} to Redis: {}", trade.trade_id, e),
                        }
                    }
                    Err(e) => { log::error!("Failed to serialize trade {:?}: {}", trade.trade_id, e); }
                }
            }

            // --- Publish Order Update for Taker Order ---
            let mut updated_order = order_clone;
            updated_order.status = final_status;

            match serde_json::to_string(&updated_order) {
                 Ok(order_update_json) => {
                     log::debug!("Publishing order update: {}", order_update_json);
                     let res: Result<usize, redis::RedisError> = publish_conn_clone.publish(ORDER_UPDATE_CHANNEL, &order_update_json).await;
                     match res {
                          Ok(count) => log::info!("Published order update {} to {} ({} subscribers)", updated_order.id, ORDER_UPDATE_CHANNEL, count),
                          Err(e) => log::error!("Failed to publish order update {} to Redis: {}", updated_order.id, e),
                     }
                 }
                 Err(e) => { log::error!("Failed to serialize order update {:?}: {}", updated_order.id, e); }
            }

        }); // End of tokio::spawn
    } // End while loop

    log::warn!("Redis message stream ended. Subscriber shutting down.");
    Ok(())
} // End main