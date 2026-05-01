import pandas as pd
import numpy as np
import datetime

def calculate_indicators_and_target(df, k_val_str, tf_text, combos):
    for ma in [3, 5, 10, 20, 50, 60]: df[f'MA_{ma}'] = df['close'].rolling(window=ma).mean()
    
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / np.where(loss == 0, 1e-9, loss)
    df['rsi'] = np.where(loss == 0, 100, 100 - (100 / (1 + rs)))
    
    df['vol_ma5'] = df['volume'].rolling(window=5).mean()
    noise = 1 - abs(df['open'] - df['close']) / (df['high'] - df['low'])
    df['noise_ma20'] = noise.rolling(window=20).mean()

    df['ema12'] = df['close'].ewm(span=12, adjust=False).mean()
    df['ema26'] = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = df['ema12'] - df['ema26']
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()

    df['bb_ma20'] = df['close'].rolling(window=20).mean()
    df['bb_std'] = df['close'].rolling(window=20).std()
    df['bb_lower'] = df['bb_ma20'] - 2 * df['bb_std']
    df['bb_upper'] = df['bb_ma20'] + 2 * df['bb_std'] 

    typical_price = (df['high'] + df['low'] + df['close']) / 3
    raw_money_flow = typical_price * df['volume']
    money_flow_direction = np.where(typical_price > typical_price.shift(1), 1, -1)
    positive_flow = np.where(money_flow_direction == 1, raw_money_flow, 0)
    negative_flow = np.where(money_flow_direction == -1, raw_money_flow, 0)
    
    pos_flow_sum = pd.Series(positive_flow).rolling(window=14).sum()
    neg_flow_sum = pd.Series(negative_flow).rolling(window=14).sum()
    money_ratio = pos_flow_sum / np.where(neg_flow_sum == 0, 1e-9, neg_flow_sum)
    df['mfi'] = np.where(neg_flow_sum == 0, 100, 100 - (100 / (1 + money_ratio)))

    tr = np.maximum(df['high'] - df['low'], np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1))))
    atr = tr.rolling(10).mean()
    hl2 = (df['high'] + df['low']) / 2
    
    upperband = (hl2 + (3 * atr)).values
    lowerband = (hl2 - (3 * atr)).values
    close_val = df['close'].values
    st_dir = np.ones(len(df))
    
    for i in range(1, len(df)):
        if close_val[i] > upperband[i-1]: st_dir[i] = 1
        elif close_val[i] < lowerband[i-1]: st_dir[i] = -1
        else: st_dir[i] = st_dir[i-1]
        if st_dir[i] == 1: lowerband[i] = max(lowerband[i], lowerband[i-1])
        else: upperband[i] = min(upperband[i], upperband[i-1])
            
    df['supertrend_up'] = st_dir == 1
    df['supertrend'] = np.where(st_dir == 1, lowerband, upperband) 

    today = df.iloc[-1]       
    yesterday = df.iloc[-2]   

    open_today = today['open']
    current_vol = today['volume']
    
    range_prev = yesterday['high'] - yesterday['low']
    noise_prev = yesterday['noise_ma20']

    target = open_today + (range_prev * noise_prev) if k_val_str == "동적K" else open_today + (range_prev * float(k_val_str))
    buy_target = max(target, open_today)

    now = datetime.datetime.now()
    candle_open_time = df.index[-1]
    
    if "4H" in tf_text: tf_seconds = 14400
    elif "1H" in tf_text: tf_seconds = 3600
    else: tf_seconds = 86400

    elapsed_seconds = max((now - candle_open_time).total_seconds(), 1)
    projected_vol = current_vol * (tf_seconds / elapsed_seconds)

    strategy_cond = True
    condition_details = [] 
    
    ma_val = combos['ma']
    if ma_val != "0": 
        ma_target = yesterday[f'MA_{ma_val}']
        passed = bool(open_today > ma_target)
        condition_details.append({'name': f'MA({ma_val})', 'passed': passed, 'value': f"{open_today:,.0f} > {ma_target:,.0f}"})
        strategy_cond &= passed
        
    rsi_val = int(combos['rsi'])
    if rsi_val != 100: 
        passed = bool(yesterday['rsi'] < rsi_val)
        condition_details.append({'name': 'RSI', 'passed': passed, 'value': f"{yesterday['rsi']:.1f} < {rsi_val}"})
        strategy_cond &= passed
        
    vol_val = combos['vol']
    if vol_val != "X": 
        passed = bool(projected_vol > (yesterday['vol_ma5'] * float(vol_val)))
        condition_details.append({'name': 'Vol', 'passed': passed, 'value': f"Est {projected_vol:,.0f} > {yesterday['vol_ma5']:,.0f} * {vol_val}"})
        strategy_cond &= passed
        
    if combos['macd'] == "O": 
        passed = bool(yesterday['macd'] > yesterday['macd_signal'])
        condition_details.append({'name': 'MACD', 'passed': passed, 'value': f"{yesterday['macd']:.1f} > {yesterday['macd_signal']:.1f}"})
        strategy_cond &= passed
        
    if combos['bb'] == "O": 
        passed = bool(open_today > yesterday['bb_lower'])
        condition_details.append({'name': 'Bollinger', 'passed': passed, 'value': f"{open_today:,.0f} > L.Band {yesterday['bb_lower']:,.0f}"})
        strategy_cond &= passed
        
    mfi_val = int(combos['mfi'])
    if mfi_val != 100: 
        passed = bool(yesterday['mfi'] < mfi_val)
        condition_details.append({'name': 'MFI', 'passed': passed, 'value': f"{yesterday['mfi']:.1f} < {mfi_val}"})
        strategy_cond &= passed
        
    if combos['st'] == "O": 
        passed = bool(yesterday['supertrend_up'] == True)
        condition_details.append({'name': 'STrend', 'passed': passed, 'value': "상승장" if passed else "하락장"})
        strategy_cond &= passed

    static_cond = True
    for detail in condition_details:
        if detail['name'] != 'Vol':
            static_cond &= detail['passed']

    return (buy_target, strategy_cond, condition_details, current_vol, 
            candle_open_time, yesterday['vol_ma5'], tf_seconds, static_cond)