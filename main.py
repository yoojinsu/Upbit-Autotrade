import sys
import time
import datetime
import threading
import requests
import os
import pandas as pd
import pyupbit
import json

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QGridLayout, QLabel, QLineEdit, 
                             QComboBox, QPushButton, QGroupBox, QTextEdit, 
                             QMessageBox, QFileDialog)
from PyQt5.QtCore import pyqtSignal, QTimer, Qt
from PyQt5.QtGui import QFont

import matplotlib
matplotlib.use('Qt5Agg')
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from config import CONFIG_FILE, HISTORY_FILE, LOG_FILE, EXCEL_STAT_FILE
from api.websocket import WebSocketWorker
from core.strategy import calculate_indicators_and_target
from ui.chart import draw_market_chart

class TradingBotApp(QMainWindow):
    log_signal = pyqtSignal(str)
    summary_signal = pyqtSignal(float, float)
    live_ui_signal = pyqtSignal(float, bool, float, list, str)
    chart_signal = pyqtSignal(object) 
    balance_signal = pyqtSignal(float, float, str)
    
    rest_tick_signal = pyqtSignal(float, float, str) 
    buy_completed_signal = pyqtSignal(float, float, float, float, str) 
    sell_completed_signal = pyqtSignal(float, float, float, float, str)
    order_error_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("업비트 자동매매 by Jinsu")
        self.resize(1200, 1150)
        
        self.setStyleSheet("""
            QWidget { background-color: #131722; color: #d1d4dc; font-family: 'Segoe UI', 'Malgun Gothic'; font-size: 13px; }
            QGroupBox { border: 1px solid #2a2e39; border-radius: 6px; margin-top: 15px; background-color: #1e222d; }
            QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 10px; color: #ffffff; font-weight: bold; font-size: 14px; }
            QLabel { background-color: transparent; }
            QLineEdit, QComboBox { background-color: #2a2e39; border: 1px solid #363c4e; border-radius: 4px; padding: 6px; color: #d1d4dc; }
            QLineEdit:focus, QComboBox:focus { border: 1px solid #2962ff; }
            QLineEdit:disabled, QComboBox:disabled { background-color: #131722; color: #5d616d; border: 1px solid #1e222d; }
            QPushButton { background-color: #2962ff; border-radius: 6px; padding: 10px; font-weight: bold; color: #ffffff; border: none; }
            QPushButton:hover { background-color: #1e53e5; }
            QPushButton:disabled { background-color: #2a2e39; color: #787b86; }
            #StopBtn { background-color: #ef5350; }
            #StopBtn:hover { background-color: #d32f2f; }
            QTextEdit { background-color: #1e222d; border: 1px solid #2a2e39; border-radius: 6px; padding: 8px; color: #b2b5be; }
        """)

        self.is_running = False
        self.ws_worker = None
        self.upbit = None
        self.error_count = 0
        self.last_chart_time = 0

        self.buy_total_spent = 0    
        self.trade_history = []
        self.total_profit = 0.0     
        
        self.actual_buy_price = 0.0
        self.holding_min_price = 0.0
        self.buy_slippage = 0.0
        self.indicator_labels = []
        self.active_slack_url = "" 
        
        self.shared_current_price = 0.0
        self.shared_current_volume = 0.0
        self.shared_buy_target = 0.0
        self.shared_strategy_cond = False
        self.shared_sell_time = None
        self.is_executing_order = False
        
        self.last_ui_update_time = 0.0 

        self.acc_volume = 0.0
        self.candle_open_time = None
        self.yesterday_vol_ma5 = 0.0
        self.tf_seconds = 86400
        self.static_cond = False

        self.last_ws_recv_ts = 0.0
        self.last_rest_price_ts = 0.0
        self.rest_fallback_active = False
        self.buy_reentry_required = False
        self.buy_reentry_logged = False
        self.last_seen_price = 0.0
        self.last_rest_log_ts = 0.0
        self.buy_coin_amount = 0.0
        self.max_recovery_buy_gap_pct = 0.3 
        
        self.ws_connected = False 

        # --- 시그널 연결 ---
        self.log_signal.connect(self.append_log)
        self.summary_signal.connect(self.update_summary_ui)
        self.live_ui_signal.connect(self.update_live_ui)
        self.chart_signal.connect(self.update_chart)
        self.balance_signal.connect(self.update_balance_ui)
        
        self.rest_tick_signal.connect(self.process_market_tick)
        self.buy_completed_signal.connect(self.on_buy_completed)
        self.sell_completed_signal.connect(self.on_sell_completed)
        self.order_error_signal.connect(lambda e: self.log(f"⚠️ 주문/동기화 에러: {e}"))
        self.order_error_signal.connect(self.release_order_lock) 

        self.init_ui()
        self.load_config()
        self.load_history()

        self.health_timer = QTimer(self)
        self.health_timer.timeout.connect(self.check_system_health)
        self.health_timer.start(10000)
        self.slack_timer = QTimer(self)
        self.slack_timer.timeout.connect(self.send_regular_slack)

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(20)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        health_frame = QWidget()
        health_layout = QHBoxLayout(health_frame)
        health_layout.setContentsMargins(5, 0, 5, 0)
        lbl_health_title = QLabel("시스템 상태")
        lbl_health_title.setFont(QFont("Arial", 10, QFont.Bold))
        lbl_health_title.setStyleSheet("color: #787b86;")
        self.health_light = QLabel()
        self.health_light.setFixedSize(10, 10)
        self.health_light.setStyleSheet("background-color: #787b86; border-radius: 5px;")
        self.lbl_last_update = QLabel("마지막 업데이트: 없음")
        self.lbl_last_update.setStyleSheet("color: #787b86; font-size: 11px;")
        health_layout.addWidget(lbl_health_title)
        health_layout.addWidget(self.health_light)
        health_layout.addStretch()
        health_layout.addWidget(self.lbl_last_update)
        left_layout.addWidget(health_frame)

        input_group = QGroupBox("API 및 계정 설정")
        input_layout = QGridLayout(input_group)
        labels = ["액세스 키:", "시크릿 키:", "코인 종목:", "K-값:"]
        self.entries = {}
        for i, text in enumerate(labels):
            lbl = QLabel(text)
            lbl.setStyleSheet("color: #b2b5be;")
            input_layout.addWidget(lbl, i, 0)
            entry = QLineEdit()
            if "시크릿" in text: entry.setEchoMode(QLineEdit.Password)
            input_layout.addWidget(entry, i, 1)
            self.entries[text] = entry
            
        lbl_tf = QLabel("기준 봉(Timeframe):")
        lbl_tf.setStyleSheet("color: #b2b5be;")
        input_layout.addWidget(lbl_tf, 4, 0)
        self.combo_tf = QComboBox()
        self.combo_tf.addItems(["1일봉 (Daily)", "4시간봉 (4H)", "1시간봉 (1H)"])
        input_layout.addWidget(self.combo_tf, 4, 1)

        lbl_k_hint = QLabel("* 동적 노이즈 비율을 원하면 '동적K' 입력")
        lbl_k_hint.setStyleSheet("color: #787b86; font-size: 11px;")
        input_layout.addWidget(lbl_k_hint, 5, 1)
        left_layout.addWidget(input_group)

        strat_group = QGroupBox("보조지표 필터 설정")
        strat_layout = QGridLayout(strat_group)
        
        strat_settings = [
            ("이동평균선(MA):", ["0", "3", "5", "10", "20", "50", "60"], "ma"),
            ("RSI 제한:", ["100", "70", "80"], "rsi"),
            ("MFI 제한:", ["100", "80"], "mfi"),
            ("거래량(> MA5 * X):", [], "vol"),
            ("MACD 골든크로스:", ["O", "X"], "macd"),
            ("볼린저밴드 하단:", ["O", "X"], "bb"),
            ("슈퍼트렌드 상승:", ["O", "X"], "st")
        ]
        
        self.combos = {}
        row, col = 0, 0
        for label_text, values, key in strat_settings:
            lbl = QLabel(label_text)
            lbl.setStyleSheet("color: #b2b5be;")
            strat_layout.addWidget(lbl, row, col)
            cb = QComboBox()
            cb.addItems(values)
            
            if key == "vol":
                cb.setEditable(True)
                cb.setCurrentText("X")
                cb.setStyleSheet("QComboBox::drop-down { width: 0px; border: none; }")
            
            strat_layout.addWidget(cb, row, col+1)
            self.combos[key] = cb
            col += 2
            if col > 2:
                col = 0; row += 1
        left_layout.addWidget(strat_group)

        slack_group = QGroupBox("제어판")
        slack_layout = QVBoxLayout(slack_group)
        slack_inner = QHBoxLayout()
        lbl_slack = QLabel("슬랙(Slack) URL:")
        lbl_slack.setStyleSheet("color: #b2b5be;")
        slack_inner.addWidget(lbl_slack)
        self.ent_slack_url = QLineEdit()
        slack_inner.addWidget(self.ent_slack_url)
        slack_layout.addLayout(slack_inner)
        
        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("매매 시작")
        self.start_btn.clicked.connect(self.start_bot)
        
        self.stop_btn = QPushButton("매매 중지")
        self.stop_btn.setObjectName("StopBtn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_bot)
        
        self.excel_btn = QPushButton("엑셀 내보내기")
        self.excel_btn.setStyleSheet("background-color: #2a2e39; color: #d1d4dc;")
        self.excel_btn.clicked.connect(self.export_to_excel)
        
        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.stop_btn)
        btn_layout.addWidget(self.excel_btn)
        slack_layout.addLayout(btn_layout)
        left_layout.addWidget(slack_group)

        wallet_group = QGroupBox("지갑 잔고")
        wallet_layout = QHBoxLayout(wallet_group)
        self.lbl_krw_bal = QLabel("KRW: 0 원")
        self.lbl_krw_bal.setFont(QFont("Arial", 12, QFont.Bold))
        self.lbl_krw_bal.setStyleSheet("color: #26a69a;")
        
        self.lbl_coin_bal = QLabel("COIN: 0")
        self.lbl_coin_bal.setFont(QFont("Arial", 12, QFont.Bold))
        self.lbl_coin_bal.setStyleSheet("color: #fcca46;")
        
        wallet_layout.addWidget(self.lbl_krw_bal)
        wallet_layout.addWidget(self.lbl_coin_bal)
        left_layout.addWidget(wallet_group)

        summary_group = QGroupBox("수익률 요약")
        summary_layout = QHBoxLayout(summary_group)
        self.lbl_total_profit = QLabel("수익금: 0 원")
        self.lbl_total_profit.setFont(QFont("Arial", 14, QFont.Bold))
        self.lbl_total_roi = QLabel("수익률: 0.00%")
        self.lbl_total_roi.setFont(QFont("Arial", 14, QFont.Bold))
        summary_layout.addWidget(self.lbl_total_profit)
        summary_layout.addWidget(self.lbl_total_roi)
        left_layout.addWidget(summary_group)

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        left_layout.addWidget(self.log_area)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        target_group = QGroupBox("실시간 목표가 및 필터 상태")
        target_layout = QVBoxLayout(target_group)
        
        top_status_layout = QHBoxLayout()
        self.lbl_target_price = QLabel("목표가: 대기 중...")
        self.lbl_target_price.setFont(QFont("Arial", 14, QFont.Bold))
        self.lbl_target_price.setStyleSheet("color: #d1d4dc;")
        
        self.lbl_current_price = QLabel("현재가: 대기 중...")
        self.lbl_current_price.setFont(QFont("Arial", 14, QFont.Bold))
        self.lbl_current_price.setStyleSheet("color: #fcca46;")

        self.lbl_buy_price = QLabel("매수가: -")
        self.lbl_buy_price.setFont(QFont("Arial", 14, QFont.Bold))
        self.lbl_buy_price.setStyleSheet("color: #787b86;")
        
        self.lbl_pnl = QLabel("손익: -")
        self.lbl_pnl.setFont(QFont("Arial", 14, QFont.Bold))
        self.lbl_pnl.setStyleSheet("color: #787b86;")
        
        self.lbl_condition_status = QLabel("상태: 대기 중...")
        self.lbl_condition_status.setFont(QFont("Arial", 14, QFont.Bold))
        self.lbl_condition_status.setStyleSheet("color: #787b86;")
        
        top_status_layout.addWidget(self.lbl_target_price)
        top_status_layout.addWidget(self.lbl_current_price) 
        top_status_layout.addWidget(self.lbl_buy_price)
        top_status_layout.addWidget(self.lbl_pnl)
        top_status_layout.addWidget(self.lbl_condition_status)
        target_layout.addLayout(top_status_layout)

        self.details_widget = QWidget()
        self.details_layout = QGridLayout(self.details_widget)
        self.lbl_initial_details = QLabel("필터 상세 정보가 여기에 표시됩니다.")
        self.lbl_initial_details.setStyleSheet("color: #787b86;")
        self.details_layout.addWidget(self.lbl_initial_details, 0, 0)
        target_layout.addWidget(self.details_widget)
        right_layout.addWidget(target_group)

        chart_group = QGroupBox("실시간 시장 차트 (Pro)")
        chart_layout = QVBoxLayout(chart_group)
        chart_layout.setContentsMargins(5, 15, 5, 5)
        
        self.fig = Figure(figsize=(8, 10), dpi=100)
        self.fig.patch.set_facecolor('#1e222d') 
        self.canvas = FigureCanvas(self.fig)
        chart_layout.addWidget(self.canvas)
        right_layout.addWidget(chart_group, 1)
        
        main_layout.addWidget(left_panel, 35) 
        main_layout.addWidget(right_panel, 65) 

    def release_order_lock(self, _=None):
        self.is_executing_order = False

    def set_controls_enabled(self, enabled):
        for entry in self.entries.values(): entry.setEnabled(enabled)
        for combo in self.combos.values(): combo.setEnabled(enabled)
        self.combo_tf.setEnabled(enabled)
        self.ent_slack_url.setEnabled(enabled)

    def update_balance_ui(self, krw, coin_bal, coin_name):
        self.lbl_krw_bal.setText(f"KRW: {krw:,.0f} 원")
        coin_str = f"{coin_bal:.8f}".rstrip('0').rstrip('.') if coin_bal > 0 else "0"
        self.lbl_coin_bal.setText(f"{coin_name}: {coin_str}")

    def update_chart(self, df):
        # UI 차트 모듈로 데이터 전달
        current_combos = {key: cb.currentText() for key, cb in self.combos.items()}
        draw_market_chart(self.fig, self.canvas, df, self.ticker, self.combo_tf.currentText(), current_combos)

    def update_live_ui(self, buy_target, strategy_cond, current_price, condition_details, now_str):
        self.lbl_last_update.setText(f"마지막 업데이트: {now_str}")
        self.lbl_target_price.setText(f"목표가: {buy_target:,.0f} 원")
        
        if self.buy_total_spent > 0:
            self.lbl_condition_status.setText("상태: 보유 중 (매도 대기)")
            self.lbl_condition_status.setStyleSheet("color: #2962ff;")
        elif strategy_cond:
            self.lbl_condition_status.setText("상태: 조건 충족 (매수 대기)")
            self.lbl_condition_status.setStyleSheet("color: #26a69a;")
        else:
            self.lbl_condition_status.setText("상태: 탐색 중 (조건 미충족)")
            self.lbl_condition_status.setStyleSheet("color: #ef5350;")

        if len(self.indicator_labels) != len(condition_details):
            for i in reversed(range(self.details_layout.count())): 
                widget = self.details_layout.itemAt(i).widget()
                if widget: widget.deleteLater()
            self.indicator_labels.clear()
            
            if not condition_details:
                lbl = QLabel("활성화된 필터가 없습니다.")
                lbl.setStyleSheet("color: #787b86;")
                self.details_layout.addWidget(lbl, 0, 0)
            else:
                row_idx, col_idx = 0, 0
                for _ in condition_details:
                    lbl = QLabel("")
                    lbl.setFont(QFont("Arial", 11, QFont.Bold))
                    self.details_layout.addWidget(lbl, row_idx, col_idx)
                    self.indicator_labels.append(lbl)
                    col_idx += 1
                    if col_idx > 1:
                        col_idx = 0; row_idx += 1

        if condition_details and len(self.indicator_labels) == len(condition_details):
            for i, detail in enumerate(condition_details):
                color = "#26a69a" if detail['passed'] else "#ef5350"
                mark = "●" if detail['passed'] else "○"
                self.indicator_labels[i].setText(f"{mark} {detail['name']}: {detail['value']}")
                self.indicator_labels[i].setStyleSheet(f"color: {color}; padding: 3px;")

    def update_summary_ui(self, profit, roi):
        color = "#26a69a" if profit > 0 else "#ef5350" if profit < 0 else "#d1d4dc"
        self.lbl_total_profit.setText(f"수익금: {profit:,.0f} 원")
        self.lbl_total_profit.setStyleSheet(f"color: {color};")
        self.lbl_total_roi.setText(f"수익률: {roi:.2f}%")
        self.lbl_total_roi.setStyleSheet(f"color: {color};")

    def append_log(self, full_msg):
        self.log_area.append(full_msg)
        with open(LOG_FILE, "a", encoding="utf-8") as f: f.write(full_msg + "\n")

    def log(self, message):
        curr_time = datetime.datetime.now().strftime('[%H:%M:%S] ')
        self.log_signal.emit(curr_time + message)

    def send_slack(self, msg):
        url = self.active_slack_url
        if not url: return
            
        def async_send():
            try:
                response = requests.post(url, json={"text": msg}, timeout=5)
                if response.status_code != 200:
                    self.log(f"⚠️ 슬랙 발송 실패 [{response.status_code}]: {response.text}")
            except Exception as e:
                self.log(f"⚠️ 슬랙 연결 에러 (URL을 확인해주세요): {e}")
        
        threading.Thread(target=async_send, daemon=True).start()

    def sync_position_from_exchange(self, log_prefix="동기화"):
        try:
            coin_name = self.ticker.split('-')[-1]
            coin_bal = float(self.upbit.get_balance(coin_name) or 0.0)
            avg_buy_price = float(self.upbit.get_avg_buy_price(coin_name) or 0.0)

            self.buy_coin_amount = coin_bal

            if coin_bal > 0 and avg_buy_price > 0 and (coin_bal * avg_buy_price) > 5000:
                self.actual_buy_price = avg_buy_price
                self.buy_total_spent = coin_bal * avg_buy_price
                if self.holding_min_price <= 0:
                    self.holding_min_price = avg_buy_price
                self.log(f"🔄 [{log_prefix}] 서버 평단가 동기화 완료 - 평단가: {avg_buy_price:,.0f}원 / 수량: {coin_bal:.8f}")
            else:
                self.actual_buy_price = 0.0
                self.buy_total_spent = 0.0
                self.buy_coin_amount = 0.0
                self.buy_reentry_required = False
                self.buy_reentry_logged = False
        except Exception as e:
            self.log(f"⚠️ 서버 평단가 동기화 실패: {e}")

    def get_rest_current_price(self, retry=2):
        for attempt in range(retry + 1):
            try:
                price = pyupbit.get_current_price(self.ticker)
                if price is not None:
                    self.last_rest_price_ts = time.time()
                    return float(price)
            except Exception as e:
                if attempt == retry: raise
                time.sleep(0.2)
        return None

    def should_buy_now(self, current_price, source="WS"):
        if self.buy_total_spent > 0 or not self.shared_strategy_cond or self.shared_buy_target <= 0:
            return False

        target_price = self.shared_buy_target
        prev_price = self.last_seen_price
        crossed_up = prev_price > 0 and prev_price <= target_price < current_price
        over_target = current_price > target_price
        at_or_over_target = current_price >= target_price
        gap_pct = ((current_price - target_price) / target_price) * 100 if target_price > 0 else 0.0

        if prev_price <= 0 and at_or_over_target and not self.buy_reentry_required:
            if gap_pct <= self.max_recovery_buy_gap_pct:
                self.log(f"🟡 [{source}] 시작/복구 직후 목표가 상단이지만 허용 이격률 이내라 예외 매수 허용 - 현재가: {current_price:,.0f} / 목표가: {target_price:,.0f} / 이격률: {gap_pct:.3f}%")
                return True
            self.buy_reentry_required = True
            if not self.buy_reentry_logged:
                self.buy_reentry_logged = True
                self.log(f"⛔ [{source}] 시작/복구 직후 목표가 상단에서 확인되어 추격매수 방지 대기 시작 - 현재가: {current_price:,.0f} / 목표가: {target_price:,.0f} / 이격률: {gap_pct:.3f}%")
            return False

        if source == "REST" and over_target and not self.buy_reentry_required:
            if gap_pct <= self.max_recovery_buy_gap_pct:
                self.log(f"🟡 [REST] 목표가 상단 복구지만 허용 이격률 이내라 예외 매수 허용 - 현재가: {current_price:,.0f} / 목표가: {target_price:,.0f} / 이격률: {gap_pct:.3f}%")
                return True
            self.buy_reentry_required = True
            if not self.buy_reentry_logged:
                self.buy_reentry_logged = True
                self.log(f"⛔ [REST] 목표가 상단에서 복구되어 추격매수 방지 대기 시작 - 현재가: {current_price:,.0f} / 목표가: {target_price:,.0f} / 이격률: {gap_pct:.3f}%")
            return False

        if self.buy_reentry_required:
            if current_price <= target_price:
                self.buy_reentry_required = False
                self.buy_reentry_logged = False
                self.log(f"🔁 목표가 재터치 확인 - 재돌파 시 매수 재개 (현재가: {current_price:,.0f} / 목표가: {target_price:,.0f})")
            return False

        if prev_price <= 0: return False
        return crossed_up

    def process_market_tick(self, current_price, current_volume=0.0, source="WS"):
        self.shared_current_price = current_price

        if source == "WS":
            self.last_ws_recv_ts = time.time()
            self.acc_volume += current_volume

            if self.rest_fallback_active:
                self.rest_fallback_active = False
                self.log("✅ 웹소켓 수신이 복구되어 REST 폴백을 종료합니다.")

        current_time = time.time()

        if current_time - self.last_ui_update_time >= 0.1:
            source_mark = "WS" if source == "WS" else "REST"
            self.lbl_current_price.setText(f"현재가: {current_price:,.0f} 원 ({source_mark})")

            if self.buy_total_spent > 0 and self.actual_buy_price > 0:
                self.lbl_buy_price.setText(f"매수가: {self.actual_buy_price:,.0f} 원")
                pnl_pct = ((current_price - self.actual_buy_price) / self.actual_buy_price) * 100
                pnl_color = "#ef5350" if pnl_pct > 0 else "#2962ff" if pnl_pct < 0 else "#d1d4dc"
                mark = "▲" if pnl_pct > 0 else "▼" if pnl_pct < 0 else "-"

                self.lbl_pnl.setText(f"손익: {pnl_pct:+.2f}% {mark}")
                self.lbl_pnl.setStyleSheet(f"color: {pnl_color};")
                self.lbl_buy_price.setStyleSheet("color: #d1d4dc;")
            else:
                self.lbl_buy_price.setText("매수가: -")
                self.lbl_pnl.setText("손익: -")
                self.lbl_buy_price.setStyleSheet("color: #787b86;")
                self.lbl_pnl.setStyleSheet("color: #787b86;")

            if self.candle_open_time and self.yesterday_vol_ma5 > 0:
                now = datetime.datetime.now()
                elapsed_seconds = max((now - self.candle_open_time).total_seconds(), 1)
                projected_vol = self.acc_volume * (self.tf_seconds / elapsed_seconds)

                vol_val = self.combos['vol'].currentText()
                vol_passed = True

                if vol_val != "X":
                    vol_passed = projected_vol > (self.yesterday_vol_ma5 * float(vol_val))
                    for lbl in self.indicator_labels:
                        if "Vol" in lbl.text():
                            color = "#26a69a" if vol_passed else "#ef5350"
                            mark = "●" if vol_passed else "○"
                            lbl.setText(f"{mark} Vol: 예측 {projected_vol:,.0f} > {self.yesterday_vol_ma5:,.0f} * {vol_val}")
                            lbl.setStyleSheet(f"color: {color}; padding: 3px;")
                            break

                self.shared_strategy_cond = self.static_cond and vol_passed

            self.last_ui_update_time = current_time

        if not self.is_running or self.is_executing_order: return

        now = datetime.datetime.now()

        if self.shared_sell_time and now >= self.shared_sell_time:
            if self.buy_total_spent > 0:
                self.execute_sell(current_price, trigger_source=source)
            return

        if self.shared_sell_time and now < self.shared_sell_time:
            if self.should_buy_now(current_price, source=source):
                self.execute_buy(current_price, trigger_source=source)

        if self.buy_total_spent > 0 and self.actual_buy_price > 0:
            self.holding_min_price = min(self.holding_min_price, current_price)

        self.last_seen_price = current_price

    def rest_fallback_monitor(self):
        while self.is_running:
            try:
                now_ts = time.time()
                extreme_silence = (now_ts - self.last_ws_recv_ts) > 60 if self.last_ws_recv_ts > 0 else True

                if extreme_silence or not getattr(self, 'ws_connected', True):
                    if not self.rest_fallback_active and (now_ts - self.last_rest_log_ts) > 60:
                        self.rest_fallback_active = True
                        self.last_rest_log_ts = now_ts
                        reason = "웹소켓 연결 끊김 감지" if not getattr(self, 'ws_connected', True) else "60초 이상 데이터 없음(프리징 의심)"
                        self.log(f"⚠️ 수신 문제 발생({reason}) - 안전을 위해 REST 폴백 모드로 보완합니다.")

                    rest_price = self.get_rest_current_price()
                    if rest_price is not None:
                        self.rest_tick_signal.emit(rest_price, 0.0, "REST")

                time.sleep(1.0) 
            except Exception as e:
                self.error_count += 1
                self.log(f"⚠️ REST 폴백 에러: {e}")
                time.sleep(2.0)

    def handle_ws_status(self, msg):
        if msg == "WS_CONNECTED":
            self.ws_connected = True
            self.log("✅ 웹소켓 실시간 연결 정상 확인")
        elif msg.startswith("WS_DISCONNECTED"):
            self.ws_connected = False
            self.log(f"⚠️ 웹소켓 끊김 감지 ({msg})")
        else:
            self.log(f"WS Error: {msg}")

    def data_update_logic(self):
        while self.is_running:
            try:
                tf_text = self.combo_tf.currentText()
                if "4H" in tf_text:
                    interval, tf_delta = "minute240", datetime.timedelta(minutes=240)
                elif "1H" in tf_text:
                    interval, tf_delta = "minute60", datetime.timedelta(minutes=60)
                else:
                    interval, tf_delta = "day", datetime.timedelta(days=1)

                coin_name = self.ticker.split('-')[-1]
                try:
                    krw_bal = self.upbit.get_balance("KRW")
                    coin_bal = self.upbit.get_balance(coin_name)
                    self.balance_signal.emit(float(krw_bal or 0.0), float(coin_bal or 0.0), coin_name)
                except Exception: pass

                df = pyupbit.get_ohlcv(self.ticker, interval=interval, count=250)
                
                if df is not None and len(df) >= 60:
                    current_combos = {key: cb.currentText() for key, cb in self.combos.items()}
                    k_val_str = self.entries["K-값:"].text().strip()
                    
                    # Core 전략 모듈로 데이터 전달
                    res = calculate_indicators_and_target(df, k_val_str, tf_text, current_combos)
                    buy_target, strategy_cond, condition_details, current_vol, candle_open_time, yesterday_vol_ma5, tf_seconds, static_cond = res

                    self.shared_buy_target = buy_target
                    self.shared_strategy_cond = strategy_cond
                    self.acc_volume = current_vol
                    self.candle_open_time = candle_open_time
                    self.yesterday_vol_ma5 = yesterday_vol_ma5
                    self.tf_seconds = tf_seconds
                    self.static_cond = static_cond
                    
                    self.shared_sell_time = candle_open_time + tf_delta - datetime.timedelta(seconds=10)

                    now_str = datetime.datetime.now().strftime('%H:%M:%S')
                    self.live_ui_signal.emit(buy_target, strategy_cond, self.shared_current_price, condition_details, now_str)
                    
                    c_time = time.time()
                    if c_time - self.last_chart_time >= 15:
                        self.chart_signal.emit(df.copy())
                        self.last_chart_time = c_time

            except Exception as e:
                self.error_count += 1
                self.log(f"⚠️ 데이터 갱신 에러: {e}")
            
            time.sleep(15) 

    def execute_buy(self, current_price, trigger_source="WS"):
        if self.is_executing_order: return
        self.is_executing_order = True  

        def async_buy_task():
            try:
                krw_before = float(self.upbit.get_balance("KRW") or 0.0)
                if krw_before > 5000:
                    buy_vol = krw_before * 0.9995
                    self.upbit.buy_market_order(self.ticker, buy_vol)
                    
                    time.sleep(0.7) 

                    coin_name = self.ticker.split('-')[-1]
                    coin_bal = float(self.upbit.get_balance(coin_name) or 0.0)
                    avg_buy_price = float(self.upbit.get_avg_buy_price(coin_name) or 0.0)
                    krw_after = float(self.upbit.get_balance("KRW") or 0.0)
                    actual_spent = max(krw_before - krw_after, 0.0)

                    if coin_bal > 0 and avg_buy_price > 0 and (coin_bal * avg_buy_price) > 5000:
                        buy_total_spent = actual_spent if actual_spent > 0 else (coin_bal * avg_buy_price)
                        self.buy_completed_signal.emit(current_price, avg_buy_price, buy_total_spent, coin_bal, trigger_source)
                    else:
                        self.order_error_signal.emit("매수 후 잔고 조회 실패 또는 5000원 미만 체결")
                else:
                    self.order_error_signal.emit("KRW 잔고 부족 (5000원 이하)")
            except Exception as e:
                self.order_error_signal.emit(f"매수 쓰레드 오류: {e}")

        threading.Thread(target=async_buy_task, daemon=True).start()

    def on_buy_completed(self, current_price, actual_buy_price, buy_total_spent, coin_bal, trigger_source):
        self.actual_buy_price = actual_buy_price
        self.buy_total_spent = buy_total_spent
        self.buy_coin_amount = coin_bal
        if self.holding_min_price <= 0:
            self.holding_min_price = self.actual_buy_price

        self.buy_slippage = (((self.actual_buy_price - self.shared_buy_target) / self.shared_buy_target) * 100 if self.shared_buy_target > 0 else 0)

        self.lbl_condition_status.setText("상태: 보유 중 (매도 대기)")
        self.lbl_condition_status.setStyleSheet("color: #2962ff;")

        self.save_trade("매수", self.ticker, self.actual_buy_price, self.buy_coin_amount, self.buy_total_spent)
        self.send_slack(f"🔔 [매수] {self.ticker}\n목표가: {self.shared_buy_target:,.0f}원\n실제 평단가: {self.actual_buy_price:,.0f}원\n트리거: {trigger_source}")
        self.buy_reentry_required = False
        self.buy_reentry_logged = False
        self.log(f"⚡ [{trigger_source}] 목표가 돌파 매수 완료 - 실제 평단가: {self.actual_buy_price:,.0f}원")
        
        self.is_executing_order = False 

    def execute_sell(self, current_price, trigger_source="WS"):
        if self.is_executing_order: return
        self.is_executing_order = True

        def async_sell_task():
            try:
                coin_name = self.ticker.split('-')[-1]
                bal = float(self.upbit.get_balance(coin_name) or 0.0)
                krw_before = float(self.upbit.get_balance("KRW") or 0.0)

                if bal > 0 and (bal * current_price) > 5000:
                    self.upbit.sell_market_order(self.ticker, bal)
                    time.sleep(0.7)

                    krw_after = float(self.upbit.get_balance("KRW") or 0.0)
                    sell_total = max(krw_after - krw_before, bal * current_price)
                    
                    self.sell_completed_signal.emit(current_price, bal, sell_total, krw_after, trigger_source)
                else:
                    self.order_error_signal.emit("매도할 코인 잔고가 5000원 미만입니다.")
            except Exception as e:
                self.order_error_signal.emit(f"매도 쓰레드 오류: {e}")

        threading.Thread(target=async_sell_task, daemon=True).start()

    def on_sell_completed(self, current_price, bal, sell_total, krw_after, trigger_source):
        profit = sell_total - self.buy_total_spent
        roi = (profit / self.buy_total_spent * 100) if self.buy_total_spent > 0 else 0
        drop_rate = ((self.holding_min_price - self.actual_buy_price) / self.actual_buy_price) * 100 if self.actual_buy_price > 0 else 0

        self.save_daily_stats_to_excel(current_price, roi, drop_rate)
        self.save_trade("매도", self.ticker, current_price, bal, sell_total, profit, roi)
        self.send_slack(f"💰 [매도] {self.ticker}\n수익: {profit:,.0f}원 ({roi:.2f}%)\n트리거: {trigger_source}")
        self.log(f"💰 [{trigger_source}] 타임프레임 종가 청산 매도 완료 (수익: {roi:.2f}%)")

        self.buy_total_spent = 0.0
        self.shared_buy_target = 0.0
        self.actual_buy_price = 0.0
        self.holding_min_price = 0.0
        self.buy_coin_amount = 0.0
        
        self.is_executing_order = False 

    def load_history(self):
        if os.path.exists(HISTORY_FILE):
            try:
                df = pd.read_csv(HISTORY_FILE)
                self.trade_history = df.to_dict('records')
                sells = df[df['구분'] == '매도']
                self.total_profit = sells['수익금'].sum()
                buys = df[df['구분'] == '매수']
                total_buy_sum = buys['총금액'].sum()
                cum_roi = (self.total_profit / total_buy_sum * 100) if total_buy_sum > 0 else 0
                self.summary_signal.emit(self.total_profit, cum_roi)
            except: pass

    def save_daily_stats_to_excel(self, sell_price, profit_rate, drop_rate):
        data = {"매도일시": datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), "목표 매수가": round(self.shared_buy_target, 2), "실제 매수가": round(self.actual_buy_price, 2), "매도가": round(sell_price, 2), "수익률(%)": round(profit_rate, 2), "슬리피지(%)": round(self.buy_slippage, 4), "낙폭(%)": round(drop_rate, 2)}
        try:
            df_new = pd.DataFrame([data])
            df_final = pd.concat([pd.read_excel(EXCEL_STAT_FILE), df_new], ignore_index=True) if os.path.exists(EXCEL_STAT_FILE) else df_new
            df_final.to_excel(EXCEL_STAT_FILE, index=False)
        except Exception as e: self.log(f"⚠️ 엑셀 저장 에러: {e}")

    def save_trade(self, side, ticker, price, amount, total, profit=0, profit_rate=0):
        data = {"시간": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), "구분": side, "종목": ticker, "가격": price, "수량": amount, "총금액": total, "수익금": round(profit, 0), "수익률(%)": round(profit_rate, 2)}
        try:
            self.trade_history.append(data)
            pd.DataFrame([data]).to_csv(HISTORY_FILE, mode='a', index=False, header=not os.path.exists(HISTORY_FILE), encoding="utf-8-sig")
            if side == "매도":
                df_all = pd.read_csv(HISTORY_FILE)
                total_p = df_all[df_all['구분'] == '매도']['수익금'].sum()
                total_b = df_all[df_all['구분'] == '매수']['총금액'].sum()
                self.summary_signal.emit(total_p, (total_p / total_b * 100) if total_b > 0 else 0)
        except: pass
        return data

    def export_to_excel(self):
        if not self.trade_history: return
        path, _ = QFileDialog.getSaveFileName(self, "엑셀 파일 저장", "", "Excel Files (*.xlsx)")
        if path: 
            pd.DataFrame(self.trade_history).to_excel(path, index=False)
            QMessageBox.information(self, "성공", "파일이 성공적으로 저장되었습니다.")

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
                self.entries["액세스 키:"].setText(config.get("access_key", ""))
                self.entries["시크릿 키:"].setText(config.get("secret_key", ""))
                self.entries["코인 종목:"].setText(config.get("ticker", "KRW-BTC"))
                self.entries["K-값:"].setText(config.get("k_value", "0.5"))
                
                tf_val = config.get("timeframe", "1일봉 (Daily)")
                idx = self.combo_tf.findText(tf_val)
                if idx >= 0: self.combo_tf.setCurrentIndex(idx)
                
                self.ent_slack_url.setText(config.get("slack_url", ""))
                for key, cb in self.combos.items():
                    val = config.get(key, "X" if key in ["vol","macd","bb","st"] else "100" if key in ["rsi","mfi"] else "0")
                    index = cb.findText(val)
                    if index >= 0: 
                        cb.setCurrentIndex(index)
                    elif key == "vol":
                        cb.setCurrentText(str(val))

    def save_config(self):
        config = { 
            "access_key": self.entries["액세스 키:"].text().strip(), 
            "secret_key": self.entries["시크릿 키:"].text().strip(), 
            "ticker": self.entries["코인 종목:"].text().strip(), 
            "k_value": self.entries["K-값:"].text().strip(), 
            "timeframe": self.combo_tf.currentText(),
            "slack_url": self.ent_slack_url.text().strip() 
        }
        for key, cb in self.combos.items(): config[key] = cb.currentText()
        with open(CONFIG_FILE, "w", encoding="utf-8") as f: json.dump(config, f, indent=4)

    def check_system_health(self):
        color = "#26a69a" if self.is_running else "#787b86"
        if self.is_running and self.error_count > 0: color = "#ff9800"
        self.health_light.setStyleSheet(f"background-color: {color}; border-radius: 5px;")

    def send_regular_slack(self):
        if self.is_running: self.send_slack(f"⏱️ [시스템 리포트] {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n✅ 웹소켓 자동매매 봇이 정상 작동 중입니다.")

    def start_bot(self):
        self.save_config()
        try:
            self.upbit = pyupbit.Upbit(self.entries["액세스 키:"].text(), self.entries["시크릿 키:"].text())
            if self.upbit.get_balance("KRW") is None: raise Exception("API 인증에 실패했습니다. 키를 다시 확인해주세요.")
            
            self.ticker = self.entries["코인 종목:"].text().strip()
            k_val = self.entries["K-값:"].text().strip()
            if k_val != "동적K": float(k_val)

            coin_name = self.ticker.split('-')[-1]
            current_coin_bal = self.upbit.get_balance(coin_name)
            
            if current_coin_bal is not None and current_coin_bal > 0:
                avg_buy_price = self.upbit.get_avg_buy_price(coin_name)
                if avg_buy_price > 0 and (current_coin_bal * avg_buy_price) > 5000:
                    self.actual_buy_price = float(avg_buy_price)
                    self.buy_total_spent = current_coin_bal * self.actual_buy_price
                    self.holding_min_price = self.actual_buy_price
                    self.log(f"🔄 [동기화] 업비트 잔고 확인 - 평단가: {self.actual_buy_price:,.0f}원")
                else:
                    self.actual_buy_price = 0.0
                    self.buy_total_spent = 0.0
            else:
                self.actual_buy_price = 0.0
                self.buy_total_spent = 0.0

            self.active_slack_url = self.ent_slack_url.text().strip()
            self.is_running = True
            
            self.ws_connected = False 
            
            self.ws_worker = WebSocketWorker(self.ticker)
            self.ws_worker.trade_signal.connect(self.on_ws_trade_update)
            self.ws_worker.error_signal.connect(self.handle_ws_status)
            self.ws_worker.status_signal.connect(lambda m: self.log(f"WS 정보: {m}"))
            self.ws_worker.start()

            self.last_ws_recv_ts = 0.0
            self.rest_fallback_active = False
            self.last_seen_price = 0.0
            self.buy_reentry_required = False
            self.buy_reentry_logged = False

            threading.Thread(target=self.data_update_logic, daemon=True).start()
            threading.Thread(target=self.rest_fallback_monitor, daemon=True).start()

            self.set_controls_enabled(False)
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.start_btn.setStyleSheet("background-color: #2a2e39; color: #787b86;")
            
            self.log(f"▶️ 초저지연 매매 시작됨: {self.ticker}")
            
            tf_val = self.combo_tf.currentText()
            k_val = self.entries["K-값:"].text().strip()
            ma_val = self.combos['ma'].currentText()
            rsi_val = self.combos['rsi'].currentText()
            mfi_val = self.combos['mfi'].currentText()
            vol_val = self.combos['vol'].currentText()
            macd_val = self.combos['macd'].currentText()
            bb_val = self.combos['bb'].currentText()
            st_val = self.combos['st'].currentText()

            start_msg = (
                f"🚀 *[Upbit 봇 구동 시작]*\n"
                f"🪙 *종목:* {self.ticker}\n"
                f"⏱️ *기준:* {tf_val} (K: {k_val})\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📈 *[활성화된 필터 조건]*\n"
                f"• MA 돌파: {ma_val if ma_val != '0' else '사용안함'}\n"
                f"• RSI 제한: {rsi_val if rsi_val != '100' else '사용안함'}\n"
                f"• MFI 제한: {mfi_val if mfi_val != '100' else '사용안함'}\n"
                f"• 거래량(Vol): {vol_val if vol_val != 'X' else '사용안함'}\n"
                f"• MACD 골크: {'사용 (O)' if macd_val == 'O' else '사용안함'}\n"
                f"• 볼린저밴드: {'하단 돌파 (O)' if bb_val == 'O' else '사용안함'}\n"
                f"• 슈퍼트렌드: {'상승장 (O)' if st_val == 'O' else '사용안함'}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⚡ 초저지연 모니터링을 시작합니다."
            )
            self.send_slack(start_msg)
            self.slack_timer.start(3600000) 
            
        except Exception as e: 
            QMessageBox.critical(self, "오류", str(e))
            self.set_controls_enabled(True)

    def stop_bot(self):
        self.is_running = False
        if self.ws_worker: self.ws_worker.stop()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.start_btn.setStyleSheet("background-color: #2962ff; color: white;")
        self.set_controls_enabled(True)

        self.log("🛑 매매 중지됨")
        self.lbl_target_price.setText("목표가: 대기 중...")
        self.lbl_current_price.setText("현재가: 대기 중...")
        self.lbl_buy_price.setText("매수가: -")
        self.lbl_pnl.setText("손익: -")
        self.lbl_condition_status.setText("상태: 중지됨")
        self.lbl_condition_status.setStyleSheet("color: #787b86;")
        self.slack_timer.stop()
        self.rest_fallback_active = False

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = TradingBotApp()
    window.show()
    sys.exit(app.exec_())