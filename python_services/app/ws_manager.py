# python_services/app/ws_manager.py
from fastapi import WebSocket
from typing import List, Dict
import json
import asyncio

class ConnectionManager:
    def __init__(self):
        # Store active connections, potentially associating them with user IDs or topics later
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        """Accepts a new WebSocket connection."""
        await websocket.accept()
        self.active_connections.append(websocket)
        print(f"New WebSocket connection accepted: {websocket.client}")

    def disconnect(self, websocket: WebSocket):
        """Removes a WebSocket connection."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            print(f"WebSocket connection closed: {websocket.client}")

    async def send_personal_message(self, message: str, websocket: WebSocket):
        """Sends a message to a specific WebSocket."""
        try:
            await websocket.send_text(message)
        except Exception as e:
            print(f"Error sending personal message to {websocket.client}: {e}")
            # Optionally disconnect on error
            # self.disconnect(websocket)

    async def broadcast(self, message: str):
        """Broadcasts a message to all connected WebSockets."""
        # Create a list of tasks for sending messages concurrently
        tasks = []
        # Iterate over a copy in case disconnect modifies the list during iteration
        for connection in self.active_connections[:]:
            tasks.append(self._send_to_connection(message, connection))

        # Wait for all send tasks to complete (or handle errors)
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle potential errors during broadcast
        for result, connection in zip(results, self.active_connections[:]):
            if isinstance(result, Exception):
                print(f"Error broadcasting to {connection.client}: {result}")
                # Optionally disconnect problematic clients
                # self.disconnect(connection)

    async def _send_to_connection(self, message: str, websocket: WebSocket):
        """Helper to send message to a single connection, handling potential errors."""
        try:
             await websocket.send_text(message)
        except Exception as e:
             # Let the gather call handle the exception logging/disconnection
             raise e # Re-raise the exception to be caught by asyncio.gather

# Create a single instance of the manager to be used across the application
manager = ConnectionManager()