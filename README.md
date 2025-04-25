# Flashbook: High-Frequency Order Book Simulator

Flashbook simulates the core components and interactions of a modern electronic financial exchange's limit order book (LOB). It's designed to demonstrate concepts like concurrency, inter-process communication, and language trade-offs in a high-frequency trading context.

## Core Features

*   **Rust Matching Engine:** High-performance core matching limit orders using Price-Time priority.
*   **Python Market Simulator:** Runs multiple configurable trading agents (Noise, Market Maker, Momentum) using `asyncio`.
*   **Python API Service (FastAPI):** Provides a REST API for order submission and a WebSocket endpoint for real-time data streaming.
*   **Redis Pub/Sub:** Used as a low-latency message broker for communication between services.
*   **Real-time Dashboard:** Simple HTML/JS frontend visualizing BBO, order book depth, recent trades, agent performance (PnL, position, open orders), and price charts via WebSockets.
*   **Simulation Controls:** Pause, Resume, Reset Market, and trigger Market Shock events via the dashboard.
*   **Basic Risk Management:** Agents have position limits and minimum bankroll thresholds.

## Architecture

The system uses a decoupled microservice architecture:

*   **Frontend (JS):** Connects via WebSockets.
*   **API Service (Python/FastAPI):** Handles HTTP orders and WebSocket connections, talks to Redis.
*   **Simulator (Python/Asyncio):** Runs agents, talks to API (HTTP) and Redis (Pub/Sub).
*   **Matching Engine (Rust/Tokio):** Talks to Redis (Pub/Sub).
*   **Redis:** Message Broker (run via Docker).

## Tech Stack

*   **Backend:** Rust (Tokio, Serde, redis-rs), Python (FastAPI, Asyncio, Pydantic, redis-py, httpx)
*   **Frontend:** HTML, CSS, JavaScript, Chart.js
*   **Messaging:** Redis
*   **Containerization:** Docker (for Redis)
*   **Build:** Cargo (Rust), Pip/Venv (Python)

## Setup & Running

**(Prerequisites: Rust, Python >= 3.9, Docker, Git)**

1.  **Clone:** `git clone https://github.com/JustinMoon-exe/Flashbook.git`
2.  `cd Flashbook`
3.  **Python Env:**
    ```bash
    python -m venv venv
    source venv/bin/activate  # Or .\venv\Scripts\activate on Windows
    pip install -r requirements.txt
    ```
4.  **Build Rust Engine:**
    ```bash
    cd rust_matching_engine
    cargo build --release
    cd ..
    ```
5.  **Start Redis:**
    ```bash
    docker run -d --name flashbook-redis -p 6379:6379 redis:latest
    ```
6.  **Run Components (in separate terminals, from project root):**
    *   **Engine:** `./rust_matching_engine/target/release/engine_subscriber` (or `.exe`)
    *   **API:** `uvicorn python_services.app.main:app --host 127.0.0.1 --port 8000`
    *   **Simulator:** `python -m market_simulator.simulator`
7.  **Open Dashboard:** Open `dashboard/index.html` in your web browser.

*(Note: Ensure Redis is running before starting other components. Default connections assume localhost.)*

---

*Justin Moonjeli - CSC 4330*
