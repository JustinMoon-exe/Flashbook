// rust_matching_engine/src/bin/subscriber.rs

use tokio::sync::Mutex;
use std::collections::HashMap;
use std::sync::Arc;
use std::env;
use serde::Deserialize;

// Import items from our library crate
use rust_matching_engine::{
    Order, OrderBook, OrderStatus, BboUpdate, OrderBookSnapshot
};

use futures_util::stream::StreamExt;
use redis::aio::ConnectionLike; // Use this trait for pubsub connection type
use redis::aio::MultiplexedConnection; // Use this for publisher
use redis::AsyncCommands;

// Redis channel names
const ORDER_SUBMIT_CHANNEL: &str = "orders:new";
const ENGINE_CONTROL_CHANNEL: &str = "engine:control";
const MARKET_EVENTS_CHANNEL: &str = "market:events";
const TRADE_EXECUTION_CHANNEL: &str = "trades:executed";
const ORDER_UPDATE_CHANNEL: &str = "orders:updated";
const BBO_UPDATE_CHANNEL_PREFIX: &str = "marketdata:bbo:";
const BOOK_SNAPSHOT_CHANNEL_PREFIX: &str = "marketdata:book:";
const SNAPSHOT_DEPTH: usize = 5;

// Type alias for shared order books map
type OrderBookMap = Arc<Mutex<HashMap<String, OrderBook>>>;

// Structure for Engine Control Commands
#[derive(Deserialize, Debug)]
struct EngineControlCommand { command: String, }

// Structure for Market Event Payload
#[derive(Deserialize, Debug)]
struct MarketEventPayload { symbol: String, #[allow(dead_code)] percent_shift: f64, }


#[tokio::main]
async fn main() -> redis::RedisResult<()> {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();
    log::info!("Starting Rust Matching Engine Subscriber...");

    // --- Redis Connection Setup ---
    let redis_url = env::var("REDIS_URL").unwrap_or_else(|_| "redis://127.0.0.1:6379/".to_string());
    log::info!("Connecting to Redis at: {}", redis_url);
    let redis_client = redis::Client::open(redis_url)?;

    let publish_conn: MultiplexedConnection = redis_client.get_multiplexed_async_connection().await?;
    log::info!("Established multiplexed Redis connection for publishing.");

    // --- CORRECTED: Use dedicated Connection for PubSub ---
    let mut sub_conn = redis_client.get_async_connection().await?; // Get standard async connection
    log::info!("Established dedicated Redis connection for subscribing.");
    let mut pubsub = sub_conn.into_pubsub(); // Turn it into PubSub

    // Subscribe to channels
    pubsub.subscribe(ORDER_SUBMIT_CHANNEL).await?;
    log::info!("Subscribed to: {}", ORDER_SUBMIT_CHANNEL);
    pubsub.subscribe(ENGINE_CONTROL_CHANNEL).await?;
    log::info!("Subscribed to: {}", ENGINE_CONTROL_CHANNEL);
    pubsub.subscribe(MARKET_EVENTS_CHANNEL).await?;
    log::info!("Subscribed to: {}", MARKET_EVENTS_CHANNEL);
    // --- End Redis Connection Setup Correction ---

    let order_books: OrderBookMap = Arc::new(Mutex::new(HashMap::new()));
    let mut msg_stream = pubsub.on_message();

    log::info!("Entering main message processing loop...");
    // --- Main Message Processing Loop ---
    while let Some(msg) = msg_stream.next().await {
        let channel_name = msg.get_channel_name();
        let payload: String = match msg.get_payload() {
             Ok(p) => p, Err(e) => { log::error!("Failed to get payload: {}", e); continue; }
        };
        log::debug!("Received msg on '{}': {}", channel_name, payload);

        // --- Handle Engine Control Command ---
        if channel_name == ENGINE_CONTROL_CHANNEL {
            match serde_json::from_str::<EngineControlCommand>(&payload) {
                Ok(cmd) => {
                    log::info!("Received Engine Control: {:?}", cmd);
                    if cmd.command == "reset_engine" { log::warn!(">>> ENGINE RESET initiated <<<"); let mut books = order_books.lock().await; books.clear(); log::info!("Cleared all books."); }
                    else { log::warn!("Unknown engine command: {}", cmd.command); }
                } Err(e) => log::error!("Failed parse engine control: {}. Payload: {}", e, payload),
            }
            continue;
        }

        // --- Handle Market Event Command ---
        if channel_name == MARKET_EVENTS_CHANNEL {
             match serde_json::from_str::<MarketEventPayload>(&payload) {
                 Ok(event_data) => {
                     log::warn!(">>> MARKET EVENT: Symbol={}, Shift={:.2}% <<<", event_data.symbol, event_data.percent_shift * 100.0);
                     let mut books_guard = order_books.lock().await;
                     if let Some(book) = books_guard.get_mut(&event_data.symbol) {
                         log::info!("Applying market event (clearing book): {}", event_data.symbol);
                         book.clear_book();

                         let symbol_clone = event_data.symbol;
                         let mut publish_conn_clone = publish_conn.clone();
                         drop(books_guard); // Drop lock before spawning

                         tokio::spawn(async move { // Spawn task to publish empty states
                             let cleared_bbo = BboUpdate::new(symbol_clone.clone(), None, None, None, None);
                             match serde_json::to_string(&cleared_bbo) {
                                 Ok(bbo_json) => { let chan = format!("{}{}", BBO_UPDATE_CHANNEL_PREFIX, symbol_clone); let _: Result<(),_> = publish_conn_clone.publish(&chan, &bbo_json).await.map_err(|e| log::error!("FAIL Pub CLEARED BBO {}: {}", symbol_clone, e)); log::info!("Pub CLEARED BBO for {}", symbol_clone);},
                                 Err(e) => log::error!("FAIL Serialize CLEARED BBO {}: {}", symbol_clone, e)
                             }
                             let cleared_snapshot = OrderBookSnapshot::new(symbol_clone.clone(), vec![], vec![]);
                             match serde_json::to_string(&cleared_snapshot) {
                                Ok(snap_json) => { let chan = format!("{}{}", BOOK_SNAPSHOT_CHANNEL_PREFIX, symbol_clone); let _: Result<(),_> = publish_conn_clone.publish::<_, _, ()>(&chan, &snap_json).await.map_err(|e| log::error!("FAIL Pub CLEARED Snap {}: {}", symbol_clone, e)); log::info!("Pub CLEARED Snapshot for {}", symbol_clone); },
                                Err(e) => log::error!("FAIL Serialize CLEARED Snapshot {}: {}", symbol_clone, e)
                             }
                         });
                     } else { log::warn!("Market event for unknown symbol: {}", event_data.symbol); }
                 } Err(e) => log::error!("Failed parse market event: {}. Payload: {}", e, payload),
             }
            continue;
        }

        // --- Handle New Order Submission ---
        if channel_name == ORDER_SUBMIT_CHANNEL {
            // Add explicit type hint for from_str
            let order_result = serde_json::from_str::<Order>(&payload);
            let order: Order = match order_result {
                Ok(mut o) => { o.ensure_remaining_quantity(); o },
                Err(e) => { log::error!("Failed deserialize order: {}. Payload: {}", e, payload); continue; }
            };
            log::info!("Deserialized order ID: {}", order.id);

            let books_clone = Arc::clone(&order_books);
            let order_id_for_task = order.id;
            let symbol_for_task = order.symbol.clone();
            let mut publish_conn_clone = publish_conn.clone();

            tokio::spawn(async move {
                let mut books_guard = books_clone.lock().await;
                let book = books_guard.entry(symbol_for_task.clone()).or_insert_with(|| OrderBook::new(symbol_for_task));

                let (final_status, trades) = book.add_order(order); // order moved
                log::info!("Order {} processed. Status: {:?}, Trades: {}", order_id_for_task, final_status, trades.len());

                // --- BBO Update ---
                let (bid_p, bid_q, ask_p, ask_q) = book.get_bbo_with_qty();
                let current_bbo = BboUpdate::new(book.symbol().to_string(), bid_p, bid_q, ask_p, ask_q);
                // Use current_bbo for comparison
                let bbo_changed = book.last_bbo().as_ref() != Some(&current_bbo);
                if bbo_changed {
                    log::debug!("BBO changed for {}", current_bbo.symbol);
                    *book.last_bbo_mut() = Some(current_bbo.clone());
                    // Use current_bbo for serialization
                    match serde_json::to_string(&current_bbo) {
                         Ok(json) => { let ch = format!("{}{}", BBO_UPDATE_CHANNEL_PREFIX, current_bbo.symbol); let _: Result<(),_> = publish_conn_clone.publish(&ch, &json).await.map_err(|e| log::error!("FAIL Pub BBO {}: {}", current_bbo.symbol, e)); },
                         Err(e) => log::error!("FAIL Serialize BBO {}: {}", current_bbo.symbol, e),
                    }
                }

                // --- Snapshot Update ---
                let current_snapshot = book.get_snapshot(SNAPSHOT_DEPTH);
                // Use current_snapshot for comparison
                let snapshot_changed = book.last_snapshot().as_ref() != Some(&current_snapshot);
                if snapshot_changed {
                    log::debug!("Snapshot changed for {}", current_snapshot.symbol);
                     *book.last_snapshot_mut() = Some(current_snapshot.clone());
                     // Use current_snapshot for serialization
                     match serde_json::to_string(&current_snapshot) {
                         Ok(json) => { let ch = format!("{}{}", BOOK_SNAPSHOT_CHANNEL_PREFIX, current_snapshot.symbol); let _: Result<(),_> = publish_conn_clone.publish(&ch, &json).await.map_err(|e| log::error!("FAIL Pub Snap {}: {}", current_snapshot.symbol, e)); },
                         Err(e) => log::error!("FAIL Serialize Snap {}: {}", current_snapshot.symbol, e),
                     }
                }

                // --- Publish Trades ---
                for trade in trades {
                    log::info!("Pub Trade - Maker: {}, Taker: {}", trade.maker_order_id, trade.taker_order_id);
                    match serde_json::to_string(&trade) {
                        Ok(json) => { let _: Result<(),_> = publish_conn_clone.publish(TRADE_EXECUTION_CHANNEL, &json).await.map_err(|e| log::error!("FAIL Pub Trade {}: {}", trade.trade_id, e)); },
                        Err(e) => log::error!("FAIL Serialize Trade {:?}: {}", trade.trade_id, e),
                    }
                }

                // --- Publish Order Update (Taker) ---
                let update_payload = serde_json::json!({ "id": order_id_for_task, "status": final_status, "remaining_quantity": if final_status == OrderStatus::Filled { Some(0) } else { None } });
                match serde_json::to_string(&update_payload) {
                     Ok(json) => { let _: Result<(),_> = publish_conn_clone.publish(ORDER_UPDATE_CHANNEL, &json).await.map_err(|e| log::error!("FAIL Pub OrderUp {}: {}", order_id_for_task, e)); },
                     Err(e) => { log::error!("FAIL Serialize OrderUp {:?}: {}", order_id_for_task, e); }
                 }

            }); // End tokio::spawn
        } else { log::warn!("Msg on unhandled channel: {}", channel_name); }
    } // End while loop

    log::warn!("Redis stream ended. Subscriber shutting down."); Ok(())
} // End main