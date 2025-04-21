// rust_matching_engine/src/lib.rs

// Add chrono and uuid as dependencies later if needed for timestamps/ids
// For now, just basic fields matching the Python model

#[derive(Debug, Clone)] // Add traits for basic functionality
pub enum OrderStatus {
    New,
    Filled,
    PartiallyFilled,
    Cancelled,
}

#[derive(Debug, Clone)]
pub enum OrderSide {
    Buy,
    Sell,
}

#[derive(Debug, Clone)]
pub struct Order {
    pub order_id: String, // Using String for simplicity now
    pub symbol: String,
    pub side: OrderSide,
    pub price: f64, // Using f64 for price, consider fixed-point later if needed
    pub quantity: u64, // Using u64 for quantity
    // pub timestamp: DateTime<Utc>, // Requires chrono crate
    pub status: OrderStatus,
}

// Add the default test module back if cargo init overwrote it
#[cfg(test)]
mod tests {
    #[test]
    fn it_works() {
        let result = 2 + 2;
        assert_eq!(result, 4);
    }
}