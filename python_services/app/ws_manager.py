from fastapi import WebSocket
from typing import List, Dict
import json
import asyncio

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        print(f"New WebSocket connection accepted: {websocket.client}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            print(f"WebSocket connection closed: {websocket.client}")

    async def send_personal_message(self, message: str, websocket: WebSocket):
        try:
            await websocket.send_text(message)
        except Exception as e:
            print(f"Error sending personal message to {websocket.client}: {e}")

    async def broadcast(self, message: str):
        tasks = []
        for connection in self.active_connections[:]:
            tasks.append(self._send_to_connection(message, connection))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result, connection in zip(results, self.active_connections[:]):
            if isinstance(result, Exception):
                print(f"Error broadcasting to {connection.client}: {result}")

    async def _send_to_connection(self, message: str, websocket: WebSocket):
        try:
            await websocket.send_text(message)
        except Exception as e:
            raise e

manager = ConnectionManager()
