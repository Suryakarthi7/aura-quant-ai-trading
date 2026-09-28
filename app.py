import json
import os
import sys
import webbrowser
import logging
from trading_engine import engine

# Suppress noisy HTTP access logs (e.g., browser extension tracker calls)
logging.getLogger('werkzeug').setLevel(logging.ERROR)

try:
    from flask import Flask, render_template, jsonify, request
    from flask_cors import CORS
    USE_FLASK = True
except ImportError:
    USE_FLASK = False
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import urllib.parse

PORT = 5000

if USE_FLASK:
    app = Flask(__name__, template_folder="templates")
    CORS(app)

    @app.route("/hybridaction/<path:subpath>", methods=["GET", "POST"])
    @app.route("/hybridaction", methods=["GET", "POST"])
    def dummy_hybridaction(subpath=""):
        # Silently consume browser extension tracking telemetry
        return ("", 204)

    @app.route("/")
    @app.route("/index.html")
    @app.route("/trading_station.html")
    @app.route("/trading_station")
    def serve_station():
        station_path = os.path.join(os.path.dirname(__file__), "trading_station.html")
        if os.path.exists(station_path):
            with open(station_path, "r", encoding="utf-8") as f:
                return f.read(), 200, {"Content-Type": "text/html; charset=utf-8"}
        return render_template("index.html")

    @app.route("/api/status", methods=["GET"])
    @app.route("/api/live_state", methods=["GET"])
    def get_status():
        return jsonify(engine.get_state())

    @app.route("/api/matches", methods=["GET"])
    def get_matches():
        return jsonify({"success": True, "matches": []})

    @app.route("/api/start", methods=["POST"])
    def start_bot():
        success, msg = engine.start_bot()
        return jsonify({"success": success, "message": msg})

    @app.route("/api/stop", methods=["POST"])
    def stop_bot():
        data = request.get_json(silent=True) or {}
        square_off = data.get("square_off", True)
        success, msg = engine.stop_bot(square_off=square_off)
        return jsonify({"success": success, "message": msg})

    @app.route("/api/settings", methods=["POST"])
    def update_settings():
        data = request.get_json(silent=True) or {}
        engine.update_settings(
            initial_capital=float(data.get("initial_capital")) if data.get("initial_capital") is not None else None,
            daily_loss_limit=float(data.get("daily_loss_limit")) if data.get("daily_loss_limit") is not None else None,
            risk_per_trade_pct=float(data.get("risk_per_trade_pct")) if data.get("risk_per_trade_pct") is not None else None,
            trade_sl_amount=float(data.get("trade_sl_amount")) if data.get("trade_sl_amount") is not None else None,
            trade_tp_amount=float(data.get("trade_tp_amount")) if data.get("trade_tp_amount") is not None else None,
            enable_options=data.get("enable_options"),
            enable_equities=data.get("enable_equities"),
            strategy_style=data.get("strategy_style"),
            trade_direction=data.get("trade_direction"),
            price_filter_active=data.get("price_filter_active"),
            price_filter_condition=data.get("price_filter_condition"),
            price_filter_amount=float(data.get("price_filter_amount")) if data.get("price_filter_amount") is not None else None,
            max_daily_trades=int(data.get("max_daily_trades")) if data.get("max_daily_trades") is not None else None,
            brokerage_per_trade=float(data.get("brokerage_per_trade")) if data.get("brokerage_per_trade") is not None else None,
            nifty_filter_active=data.get("nifty_filter_active"),
            time_filter_active=data.get("time_filter_active"),
            mtf_filter_active=data.get("mtf_filter_active"),
            ema_filter_active=data.get("ema_filter_active"),
            vwap_pullback_filter_active=data.get("vwap_pullback_filter_active"),
            rvol_filter_active=data.get("rvol_filter_active"),
            max_vwap_dist_pct=float(data.get("max_vwap_dist_pct")) if data.get("max_vwap_dist_pct") is not None else None,
            min_rvol_threshold=float(data.get("min_rvol_threshold")) if data.get("min_rvol_threshold") is not None else None
        )
        return jsonify({"success": True, "message": "Settings updated successfully."})

    @app.route("/api/close_position", methods=["POST"])
    def close_position():
        data = request.get_json(silent=True) or {}
        pos_id = data.get("position_id")
        if not pos_id:
            return jsonify({"success": False, "message": "Position ID required"}), 400
        success, msg = engine.close_single_position(pos_id)
        return jsonify({"success": success, "message": msg})

    @app.route("/api/update_position_sltp", methods=["POST"])
    def update_position_sltp():
        data = request.get_json(silent=True) or {}
        pos_id = data.get("position_id")
        sl_amount = data.get("sl_amount")
        tp_amount = data.get("tp_amount")
        if not pos_id:
            return jsonify({"success": False, "message": "Position ID required"}), 400
        success, msg = engine.update_position_sltp(pos_id, sl_amount=sl_amount, tp_amount=tp_amount)
        return jsonify({"success": success, "message": msg})

    @app.route("/api/reset_position_sltp", methods=["POST"])
    def reset_position_sltp():
        data = request.get_json(silent=True) or {}
        pos_id = data.get("position_id")
        if not pos_id:
            return jsonify({"success": False, "message": "Position ID required"}), 400
        success, msg = engine.reset_position_sltp(pos_id)
        return jsonify({"success": success, "message": msg})

    @app.route("/api/update_all_positions_sltp", methods=["POST"])
    def update_all_positions_sltp():
        data = request.get_json(silent=True) or {}
        sl_amount = data.get("sl_amount")
        tp_amount = data.get("tp_amount")
        side = data.get("side", "BUY")
        success, msg = engine.update_all_positions_sltp(sl_amount=sl_amount, tp_amount=tp_amount, side=side)
        return jsonify({"success": success, "message": msg})

    @app.route("/api/reset_all_positions_sltp", methods=["POST"])
    def reset_all_positions_sltp():
        data = request.get_json(silent=True) or {}
        side = data.get("side")
        success, msg = engine.reset_all_positions_sltp(side=side)
        return jsonify({"success": success, "message": msg})

    @app.route("/api/reset", methods=["POST"])
    def reset_account():
        engine.reset_account()
        return jsonify({"success": True, "message": "Account reset successfully."})

    def _open_browser(port):
        import time
        time.sleep(1.0)
        try:
            webbrowser.open(f"http://localhost:{port}")
        except Exception:
            pass

    def run_server():
        global PORT
        import threading
        from market_data import real_feed
        real_feed.start()
        print("=" * 60, flush=True)
        print("   AURA QUANT - AI INTRADAY TRADING ENGINE", flush=True)
        print("=" * 60, flush=True)
        print(f" [*] Web Server running at: http://localhost:{PORT}", flush=True)
        print(" [*] Connected to NSE Market Data Gateway.", flush=True)
        print(" [*] Streaming live ticks second-by-second (1000ms)...", flush=True)
        print(" [*] Opening web station in your browser...", flush=True)
        print("=" * 60, flush=True)
        try:
            threading.Thread(target=_open_browser, args=(PORT,), daemon=True).start()
            app.run(host="0.0.0.0", port=PORT, debug=False)
        except OSError:
            PORT = 8080
            print(f"Port 5000 busy. Switching to http://localhost:{PORT}", flush=True)
            threading.Thread(target=_open_browser, args=(PORT,), daemon=True).start()
            app.run(host="0.0.0.0", port=PORT, debug=False)

else:
    class StandaloneHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return # Suppress noisy log lines

        def _send_json(self, data, status=200):
            response = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            clean_path = parsed.path.rstrip('/') or '/'
            if clean_path in ["/", "/index.html", "/trading_station.html", "/trading_station"]:
                html_path = os.path.join(os.path.dirname(__file__), "trading_station.html")
                if not os.path.exists(html_path):
                    html_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
                with open(html_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
            elif clean_path.startswith("/hybridaction"):
                self.send_response(204)
                self.end_headers()
            elif clean_path in ["/api/status", "/api/live_state"]:
                self._send_json(engine.get_state())
            elif clean_path == "/api/matches":
                self._send_json({"success": True, "matches": []})
            else:
                self.send_error(404, "Not Found")

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
            except Exception:
                data = {}

            if parsed.path == "/api/start":
                success, msg = engine.start_bot()
                self._send_json({"success": success, "message": msg})
            elif parsed.path == "/api/stop":
                square_off = data.get("square_off", True)
                success, msg = engine.stop_bot(square_off=square_off)
                self._send_json({"success": success, "message": msg})
            elif parsed.path == "/api/settings":
                engine.update_settings(
                    initial_capital=float(data.get("initial_capital")) if data.get("initial_capital") is not None else None,
                    daily_loss_limit=float(data.get("daily_loss_limit")) if data.get("daily_loss_limit") is not None else None,
                    risk_per_trade_pct=float(data.get("risk_per_trade_pct")) if data.get("risk_per_trade_pct") is not None else None,
                    trade_sl_amount=float(data.get("trade_sl_amount")) if data.get("trade_sl_amount") is not None else None,
                    trade_tp_amount=float(data.get("trade_tp_amount")) if data.get("trade_tp_amount") is not None else None,
                    enable_options=data.get("enable_options"),
                    enable_equities=data.get("enable_equities"),
                    strategy_style=data.get("strategy_style"),
                    trade_direction=data.get("trade_direction"),
                    price_filter_active=data.get("price_filter_active"),
                    price_filter_condition=data.get("price_filter_condition"),
                    price_filter_amount=float(data.get("price_filter_amount")) if data.get("price_filter_amount") is not None else None,
                    max_daily_trades=int(data.get("max_daily_trades")) if data.get("max_daily_trades") is not None else None,
                    brokerage_per_trade=float(data.get("brokerage_per_trade")) if data.get("brokerage_per_trade") is not None else None,
                    nifty_filter_active=data.get("nifty_filter_active"),
                    time_filter_active=data.get("time_filter_active"),
                    mtf_filter_active=data.get("mtf_filter_active"),
                    ema_filter_active=data.get("ema_filter_active"),
                    vwap_pullback_filter_active=data.get("vwap_pullback_filter_active"),
                    rvol_filter_active=data.get("rvol_filter_active"),
                    max_vwap_dist_pct=float(data.get("max_vwap_dist_pct")) if data.get("max_vwap_dist_pct") is not None else None,
                    min_rvol_threshold=float(data.get("min_rvol_threshold")) if data.get("min_rvol_threshold") is not None else None
                )
                self._send_json({"success": True, "message": "Settings updated successfully."})
            elif parsed.path == "/api/close_position":
                pos_id = data.get("position_id")
                success, msg = engine.close_single_position(pos_id)
                self._send_json({"success": success, "message": msg})
            elif parsed.path == "/api/update_position_sltp":
                pos_id = data.get("position_id")
                sl_amount = data.get("sl_amount")
                tp_amount = data.get("tp_amount")
                success, msg = engine.update_position_sltp(pos_id, sl_amount=sl_amount, tp_amount=tp_amount)
                self._send_json({"success": success, "message": msg})
            elif parsed.path == "/api/reset_position_sltp":
                pos_id = data.get("position_id")
                success, msg = engine.reset_position_sltp(pos_id)
                self._send_json({"success": success, "message": msg})
            elif parsed.path == "/api/update_all_positions_sltp":
                sl_amount = data.get("sl_amount")
                tp_amount = data.get("tp_amount")
                side = data.get("side", "BUY")
                success, msg = engine.update_all_positions_sltp(sl_amount=sl_amount, tp_amount=tp_amount, side=side)
                self._send_json({"success": success, "message": msg})
            elif parsed.path == "/api/reset_all_positions_sltp":
                side = data.get("side")
                success, msg = engine.reset_all_positions_sltp(side=side)
                self._send_json({"success": success, "message": msg})
            elif parsed.path == "/api/reset":
                engine.reset_account()
                self._send_json({"success": True, "message": "Account reset successfully."})
            elif parsed.path.startswith("/hybridaction"):
                self.send_response(204)
                self.end_headers()
            else:
                self.send_error(404, "Endpoint Not Found")

    def _open_browser_sa(port):
        import time
        time.sleep(1.0)
        try:
            webbrowser.open(f"http://localhost:{port}")
        except Exception:
            pass

    def run_server():
        global PORT
        import threading
        from market_data import real_feed
        real_feed.start()
        print("=" * 60, flush=True)
        print("   AURA QUANT - AI INTRADAY TRADING ENGINE (Zero-Dependency)", flush=True)
        print("=" * 60, flush=True)
        print(f" [*] Web Server running at: http://localhost:{PORT}", flush=True)
        print(" [*] Connected to NSE Market Data Gateway.", flush=True)
        print(" [*] Streaming live ticks second-by-second (1000ms)...", flush=True)
        print(" [*] Opening web station in your browser...", flush=True)
        print("=" * 60, flush=True)
        try:
            server = HTTPServer(("0.0.0.0", PORT), StandaloneHandler)
            threading.Thread(target=_open_browser_sa, args=(PORT,), daemon=True).start()
        except OSError:
            PORT = 8080
            print(f"Port 5000 busy. Trying port {PORT}...", flush=True)
            server = HTTPServer(("0.0.0.0", PORT), StandaloneHandler)
            threading.Thread(target=_open_browser_sa, args=(PORT,), daemon=True).start()
        server.serve_forever()

if __name__ == "__main__":
    run_server()
