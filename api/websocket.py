import asyncio
import websockets
from PyQt5.QtCore import QThread, pyqtSignal

try:
    import ujson as json
except ImportError:
    import json

class WebSocketWorker(QThread):
    trade_signal = pyqtSignal(float, float)
    error_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str)

    def __init__(self, ticker):
        super().__init__()
        self.ticker = ticker
        self.is_running = True

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.ws_connect())

    async def ws_connect(self):
        uri = "wss://api.upbit.com/websocket/v1"
        req = [
            {"ticket": "auto_trader_ultra_low_latency"},
            {"type": "trade", "codes": [self.ticker], "isOnlyRealtime": True}
        ]

        reconnect_delay = 1
        while self.is_running:
            try:
                self.status_signal.emit(f"웹소켓 연결 시도 중... ({self.ticker})")
                async with websockets.connect(
                    uri,
                    ping_interval=20,
                    ping_timeout=10,
                    open_timeout=10,
                    close_timeout=5,
                    max_queue=2000
                ) as ws:
                    self.status_signal.emit("웹소켓 연결 성공")
                    self.error_signal.emit("WS_CONNECTED") 
                    reconnect_delay = 1

                    await ws.send(json.dumps(req))

                    while self.is_running:
                        data = await ws.recv()
                        res = json.loads(data)

                        if 'trade_price' in res and 'trade_volume' in res:
                            self.trade_signal.emit(float(res['trade_price']), float(res['trade_volume']))

            except Exception as e:
                self.error_signal.emit(f"WS_DISCONNECTED: {e}") 
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 15)

    def stop(self):
        self.is_running = False
        self.quit()
        self.wait()