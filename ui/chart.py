import platform
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# 한글 폰트 설정
if platform.system() == 'Darwin':
    plt.rcParams['font.family'] = 'AppleGothic'
else:
    plt.rcParams['font.family'] = 'Malgun Gothic'
plt.rcParams['axes.unicode_minus'] = False

def draw_market_chart(fig, canvas, df, ticker, tf_text, combos):
    if df is None or df.empty: return
    
    if "1H" in tf_text: plot_df = df.iloc[-120:] 
    elif "4H" in tf_text: plot_df = df.iloc[-90:] 
    else: plot_df = df.iloc[-90:] 
        
    fig.clf()
    
    show_vol = combos['vol'] != "X"
    show_macd = combos['macd'] == "O"
    show_rsi = combos['rsi'] != "100"
    show_mfi = combos['mfi'] != "100"
    show_momentum = show_rsi or show_mfi
    
    ratios = [4]
    if show_vol: ratios.append(1)
    if show_macd: ratios.append(1)
    if show_momentum: ratios.append(1)
    
    gs = fig.add_gridspec(len(ratios), 1, height_ratios=ratios, hspace=0.1)
    
    axes = []
    ax_main = fig.add_subplot(gs[0, 0])
    axes.append(ax_main)
    
    idx = 1
    ax_vol, ax_macd, ax_momentum = None, None, None
    
    if show_vol:
        ax_vol = fig.add_subplot(gs[idx, 0], sharex=ax_main)
        axes.append(ax_vol)
        idx += 1
    if show_macd:
        ax_macd = fig.add_subplot(gs[idx, 0], sharex=ax_main)
        axes.append(ax_macd)
        idx += 1
    if show_momentum:
        ax_momentum = fig.add_subplot(gs[idx, 0], sharex=ax_main)
        axes.append(ax_momentum)
        idx += 1

    bg_color = '#131722'
    grid_color = '#2a2e39'
    text_color = '#787b86'
    
    for ax in axes:
        ax.set_facecolor(bg_color)
        ax.tick_params(colors=text_color, labelsize=9)
        ax.yaxis.tick_right() 
        ax.grid(True, color=grid_color, linestyle='-', linewidth=0.5)
        for spine in ax.spines.values(): spine.set_color(grid_color)
            
    for ax in axes[:-1]: plt.setp(ax.get_xticklabels(), visible=False)

    up = plot_df[plot_df['close'] >= plot_df['open']]
    down = plot_df[plot_df['close'] < plot_df['open']]
    
    color_up = '#26a69a'
    color_down = '#ef5350'
    
    if "1H" in tf_text: width, width2 = 0.03, 0.005 
    elif "4H" in tf_text: width, width2 = 0.12, 0.02 
    else: width, width2 = 0.6, 0.05
    
    ax_main.bar(up.index, up['close']-up['open'], width, bottom=up['open'], color=color_up)
    ax_main.bar(up.index, up['high']-up['low'], width2, bottom=up['low'], color=color_up)
    ax_main.bar(down.index, down['open']-down['close'], width, bottom=down['close'], color=color_down)
    ax_main.bar(down.index, down['high']-down['low'], width2, bottom=down['low'], color=color_down)

    if combos['ma'] != "0":
        ma_val = int(combos['ma'])
        if f'MA_{ma_val}' in plot_df.columns:
            ax_main.plot(plot_df.index, plot_df[f'MA_{ma_val}'], color='#fcca46', linewidth=1.2, label=f'MA{ma_val}')
        
    if combos['bb'] == "O":
        ax_main.plot(plot_df.index, plot_df['bb_upper'], color='#2962ff', linewidth=0.8, alpha=0.5)
        ax_main.plot(plot_df.index, plot_df['bb_lower'], color='#2962ff', linewidth=0.8, alpha=0.5)
        ax_main.fill_between(plot_df.index, plot_df['bb_upper'], plot_df['bb_lower'], color='#2962ff', alpha=0.08)

    if combos['st'] == "O" and 'supertrend' in plot_df.columns:
        up_st = plot_df[plot_df['supertrend_up'] == True]
        down_st = plot_df[plot_df['supertrend_up'] == False]
        ax_main.scatter(up_st.index, up_st['supertrend'], color=color_up, s=12, marker='^', zorder=5)
        ax_main.scatter(down_st.index, down_st['supertrend'], color=color_down, s=12, marker='v', zorder=5)

    ax_main.set_title(f"[실시간] {ticker} - {tf_text}", color='#d1d4dc', fontsize=11, fontweight='bold', loc='left', pad=8)

    if show_vol and ax_vol:
        v_colors = [color_up if c >= o else color_down for c, o in zip(plot_df['close'], plot_df['open'])]
        ax_vol.bar(plot_df.index, plot_df['volume'], color=v_colors, alpha=0.6, width=width)
        ax_vol.set_ylabel('거래량', color=text_color, fontsize=9)

    if show_macd and ax_macd:
        ax_macd.plot(plot_df.index, plot_df['macd'], color='#2962ff', linewidth=1, label='MACD')
        ax_macd.plot(plot_df.index, plot_df['macd_signal'], color='#ff9800', linewidth=1, label='Signal')
        macd_hist = plot_df['macd'] - plot_df['macd_signal']
        hist_colors = [color_up if val > 0 else color_down for val in macd_hist]
        ax_macd.bar(plot_df.index, macd_hist, color=hist_colors, alpha=0.5, width=width)
        ax_macd.set_ylabel('MACD', color=text_color, fontsize=9)

    if show_momentum and ax_momentum:
        if show_rsi: ax_momentum.plot(plot_df.index, plot_df['rsi'], color='#b2cdcc', linewidth=1.2, label='RSI')
        if show_mfi: ax_momentum.plot(plot_df.index, plot_df['mfi'], color='#ffe882', linewidth=1.2, label='MFI', alpha=0.7)
        ax_momentum.axhline(70, color=color_down, linestyle='--', linewidth=0.8, alpha=0.5)
        ax_momentum.axhline(30, color=color_up, linestyle='--', linewidth=0.8, alpha=0.5)
        ax_momentum.set_ylabel('모멘텀', color=text_color, fontsize=9)
        ax_momentum.set_ylim(0, 100)

    if "Day" in tf_text or "일봉" in tf_text:
        axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
        axes[-1].xaxis.set_major_locator(mdates.DayLocator(interval=5)) 
    elif "4H" in tf_text:
        axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
        axes[-1].xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 12])) 
    else: 
        axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
        axes[-1].xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 6, 12, 18])) 
        
    for label in axes[-1].get_xticklabels(): 
        label.set_rotation(45)   
        label.set_ha('right')    

    for ax in axes:
        handles, labels = ax.get_legend_handles_labels()
        if handles: ax.legend(loc='upper left', frameon=False, labelcolor=text_color, fontsize=8)

    fig.subplots_adjust(left=0.06, right=0.92, top=0.95, bottom=0.1)
    canvas.draw()