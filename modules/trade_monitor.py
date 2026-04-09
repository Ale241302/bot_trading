import json
import os
import MetaTrader5 as mt5

class TradeMonitor:
    def __init__(self, memory, notion):
        self.memory = memory
        self.notion = notion
        self.filepath = "active_trades.json"
        self._load_active_trades()

    def _load_active_trades(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r') as f:
                    self.active_trades = json.load(f)
            except Exception:
                self.active_trades = {}
        else:
            self.active_trades = {}

    def _save_active_trades(self):
        with open(self.filepath, 'w') as f:
            json.dump(self.active_trades, f)

    def add_trade(self, ticket: int, symbol: str, notion_page_id: str, action: str, lot_size: float, price_open: float, reason: str):
        self.active_trades[str(ticket)] = {
            "symbol": symbol,
            "notion_page_id": notion_page_id,
            "action": action,
            "lot_size": lot_size,
            "price_open": price_open,
            "reason": reason
        }
        self._save_active_trades()

    def check_closed_trades(self):
        open_positions = mt5.positions_get()
        if open_positions is None:
            return  # Error obteniendo posiciones

        open_tickets = [p.ticket for p in open_positions]
        closed_tickets = []

        for ticket_str, data in self.active_trades.items():
            ticket = int(ticket_str)
            if ticket not in open_tickets:
                # Comprobar historial de deals para obtener profit
                deals = mt5.history_deals_get(position=ticket)
                price_close = None
                profit = 0.0

                if deals and len(deals) > 0:
                    profit = sum([d.profit for d in deals])
                    price_close = deals[-1].price
                else:
                    # Alternativa si deals no trae resultados
                    pass

                # Actualizar Notion y Pinecone
                print(f"Monitor: Operación cerrada {ticket}. Actualizando DBs...")
                
                if data.get("notion_page_id"):
                    self.notion.update_operation(data["notion_page_id"], price_close, profit)

                self.memory.update_operation(
                    ticket=ticket,
                    symbol=data["symbol"],
                    action=data["action"],
                    lot_size=data["lot_size"],
                    price_open=data["price_open"],
                    reason=data["reason"],
                    price_close=price_close,
                    result_usd=profit
                )
                
                closed_tickets.append(ticket_str)

        for t in closed_tickets:
            del self.active_trades[t]

        if closed_tickets:
            self._save_active_trades()
