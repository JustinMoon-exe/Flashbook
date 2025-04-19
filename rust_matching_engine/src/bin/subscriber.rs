// rust_matching_engine/src/bin/subscriber.rs

use tokio::sync::Mutex; // Tokio's Mutex for async locking
use std::collections::HashMap;
use std::sync::Arc; // Arc for thread-safe reference counting

// Import items from our library crate
// Note: Trade and OrderStatus might still show as unused imports, which is okay for now.
use rust_matching_engine::{Order, OrderBook, Trade, OrderStatus};

// Added imports:
use futures_util::stream::StreamExt; // Trait needed for msg_stream.next()
use redis::aio::MultiplexedConnection; // Cloneable connection type
use redis::AsyncCommands; // Required for publish command

// Redis channel names (must match Python)
const ORDER_SUBMIT_CHANNEL: &str = "orders:new";
const TRADE_EXECUTION_CHANNEL: &str = "trades:executed";
const ORDER_UPDATE_CHANNEL: &str = "orders:updated"; // For status updates

// Type alias for shared, mutable state for OrderBooks
// Arc allows sharing across async tasks, Mutex allows safe mutation
type OrderBookMap = Arc<Mutex<HashMap<String, OrderBook>>>;

#[tokio::main]
async fn main() -> redis::RedisResult<()> {
    // Initialize logging
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    log::info!("Starting Rust Matching Engine Subscriber...");

    // Redis client
    let redis_client = redis::Client::open("redis://127.0.0.1:6379/")?; // Use 127.0.0.1 for clarity

    // --- Get Connections (Updated) ---
    // Use a MultiplexedConnection for publishing - it's cloneable
    let publish_conn: MultiplexedConnection = redis_client.get_multiplexed_async_connection().await?;
    log::info!("Established multiplexed Redis connection for publishing.");

    // PubSub still uses its own connection type, derived from a standard async connection
    // This doesn't need to be cloneable as it's consumed by the stream loop directly.
    let mut pubsub_conn = redis_client.get_async_connection().await?.into_pubsub();
    log::info!("Established Redis connection for PubSub.");
    // --- End Updated Connections ---


    // Shared state for order books (one book per symbol)
    let order_books: OrderBookMap = Arc::new(Mutex::new(HashMap::new()));

    // Subscribe to the new order channel
    pubsub_conn.subscribe(ORDER_SUBMIT_CHANNEL).await?;
    log::info!("Subscribed to Redis channel: {}", ORDER_SUBMIT_CHANNEL);

    // Get a stream of messages
    let mut msg_stream = pubsub_conn.on_message();

    // Ensure StreamExt trait is in scope for .next()
    while let Some(msg) = msg_stream.next().await {
        let payload: String = match msg.get_payload() {
             Ok(p) => p,
             Err(e) => {
                log::error!("Failed to get payload from Redis message: {}", e);
                continue; // Skip this message
             }
        };
        log::debug!("Received message payload: {}", payload);

        // Deserialize JSON payload into Rust Order struct
        let order: Order = match serde_json::from_str(&payload) {
            Ok(o) => o,
            Err(e) => {
                log::error!("Failed to deserialize order JSON: {}. Payload: {}", e, payload);
                continue; // Skip invalid message
            }
        };

        log::info!("Deserialized order: {:?}", order.id); // Log with ID

        // --- Access OrderBook (using Arc<Mutex<...>>) ---
        // Clone Arc for the new task/block (cheap)
        let books_clone = order_books.clone();
        let order_clone = order.clone(); // Clone order data for processing

        // --- Clone the publish connection (NOW WORKS because it's MultiplexedConnection) ---
        let mut publish_conn_clone = publish_conn.clone();

        // Spawn a Tokio task to handle order processing concurrently
        // This prevents blocking the main message loop if processing takes time
        tokio::spawn(async move {
             let mut books = books_clone.lock().await; // Lock the Mutex to get access to the HashMap

             // Get or create the order book for the symbol
             let book = books.entry(order_clone.symbol.clone())
                           .or_insert_with(|| {
                                log::info!("Creating new order book for symbol: {}", order_clone.symbol);
                                OrderBook::new(order_clone.symbol.clone())
                           });

             // --- Process the Order ---
             // Note: add_order takes ownership if not cloned, but we cloned `order_clone`
             let (final_status, trades) = book.add_order(order_clone.clone()); // Process the cloned order

             log::info!("Order {} processed. Status: {:?}, Trades: {}", order_clone.id, final_status, trades.len());

             // --- Publish Results ---
             // 1. Publish executed trades
             for trade in trades {
                 match serde_json::to_string(&trade) {
                     Ok(trade_json) => {
                         log::debug!("Publishing trade: {}", trade_json);
                         // Use the cloned connection here
                         let res: Result<(), redis::RedisError> = publish_conn_clone.publish(TRADE_EXECUTION_CHANNEL, &trade_json).await;
                         if let Err(e) = res {
                             log::error!("Failed to publish trade {} to Redis: {}", trade.trade_id, e);
                         } else {
                             log::info!("Published trade {} to {}", trade.trade_id, TRADE_EXECUTION_CHANNEL);
                         }
                     }
                     Err(e) => {
                        log::error!("Failed to serialize trade {:?}: {}", trade, e);
                     }
                 }
             }

             // 2. Publish order status update (optional, but useful)
             // Create a simple update structure or just re-publish the updated order
             let mut updated_order = order_clone;
             updated_order.status = final_status;
             // Ideally, get remaining_quantity from the book state after matching
             // book.add_order would need to return more info, or we query the book here.
             // For now, just publish the final status derived from the return value

             match serde_json::to_string(&updated_order) {
                  Ok(order_update_json) => {
                      log::debug!("Publishing order update: {}", order_update_json);
                      // Use the cloned connection here
                      let res: Result<(), redis::RedisError> = publish_conn_clone.publish(ORDER_UPDATE_CHANNEL, &order_update_json).await;
                      if let Err(e) = res {
                          log::error!("Failed to publish order update {} to Redis: {}", updated_order.id, e);
                      } else {
                           log::info!("Published order update {} to {}", updated_order.id, ORDER_UPDATE_CHANNEL);
                      }
                  }
                  Err(e) => {
                     log::error!("Failed to serialize order update {:?}: {}", updated_order, e);
                  }
             }

        }); // End of tokio::spawn

    } // End while loop

    log::warn!("Redis message stream ended. Subscriber shutting down.");
    Ok(())
}