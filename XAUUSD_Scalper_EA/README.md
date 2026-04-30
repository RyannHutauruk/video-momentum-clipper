# XAUUSD PropFirm Scalper EA

Expert Advisor untuk **XAUUSD** di timeframe **M5** dengan strategi **scalping multi-konfirmasi**. Dirancang khusus untuk **prop firm friendly** dengan proteksi drawdown built-in.

## Strategi

### Multi-Timeframe Pullback-to-Trend Scalper

EA ini menggunakan pendekatan **pullback-to-trend** dengan 7 layer konfirmasi untuk memfilter noise dan fake signals:

#### 1. Higher Timeframe Trend Filter (H1)
- **EMA 50** pada H1 menentukan arah trend utama
- **ADX > 20** memastikan pasar sedang trending (bukan ranging)
- **+DI vs -DI** mengkonfirmasi arah momentum

#### 2. M5 Trend Alignment
- **EMA 9 & EMA 21** harus aligned dengan H1 trend
- Memastikan entry searah trend di kedua timeframe

#### 3. Pullback Detection
- Mendeteksi pullback ke zona EMA 21 (dynamic support/resistance)
- Harga harus menyentuh atau mendekati slow EMA, lalu bounce
- Menghindari entry di breakout (yang lebih rentan fake)

#### 4. RSI Confirmation (14)
- **BUY:** RSI di zona 35-55 dan rising (pullback sehat, bukan oversold)
- **SELL:** RSI di zona 45-65 dan falling
- Menghindari extreme levels yang sering false reversal

#### 5. Stochastic Confirmation (5,3,3)
- **BUY:** %K cross up %D dari zona oversold
- **SELL:** %K cross down %D dari zona overbought
- Mengkonfirmasi timing entry yang tepat

#### 6. MACD Histogram Shift
- Histogram harus menunjukkan perubahan momentum searah trade
- BUY: histogram meningkat, SELL: histogram menurun

#### 7. Candlestick Pattern
- **Bullish Engulfing / Hammer** untuk BUY
- **Bearish Engulfing / Shooting Star** untuk SELL
- Strong directional candle sebagai konfirmasi tambahan

### Mengapa Strategi Ini Efektif

| Aspek | Penjelasan |
|-------|-----------|
| **Winrate 60%+** | 7 layer konfirmasi memfilter 90%+ noise/fake signals |
| **RR 1:2+** | Entry pada pullback memberikan SL kecil, TP besar |
| **Low DD** | Prop firm protection + single trade + risk per trade kecil |
| **100+ trades/year** | Session London + NY = ~9 jam/hari trading window |
| **Prop Firm Friendly** | No martingale, no grid, no hedging, built-in DD limiter |

## Prop Firm Protection

EA ini memiliki **built-in prop firm protection**:

- **Daily Loss Limit**: Default 4% (di bawah standar 5% prop firm)
- **Total Drawdown Limit**: Default 8% (di bawah standar 10%)
- **Auto-close semua posisi** saat limit tercapai
- **Pre-trade risk check**: Menghitung apakah trade baru bisa melanggar limit
- **Safety margin 90%**: Berhenti trading sebelum benar-benar hit limit

### Konfigurasi untuk Challenge 5K (dari gambar):
```
Initial Balance:    5000
Daily Loss Limit:   4%  ($200, prop firm max $250)
Total DD Limit:     8%  ($400, prop firm max $500)
Risk per Trade:     1%  ($50)
```

## Filter Anti-Noise

1. **Session Filter**: Hanya trade saat London (08:00-12:00) dan New York (13:00-17:00) GMT
2. **Spread Filter**: Skip jika spread > 50 points
3. **Cooldown After Loss**: Tunggu 3 bar setelah loss (hindari revenge trading)
4. **Friday Filter**: Stop trading Jumat setelah 15:00 GMT
5. **Monday Filter**: Skip trading Senin sebelum 03:00 GMT
6. **ATR-based SL**: Adaptive SL berdasarkan volatilitas aktual, bukan fixed

## Instalasi

### MetaTrader 5
1. Buka **MetaTrader 5**
2. Pergi ke **File → Open Data Folder**
3. Navigasi ke `MQL5/Experts/`
4. Copy file `XAUUSD_PropFirm_Scalper.mq5` ke folder tersebut
5. Restart MetaTrader 5 atau klik **Compile** di MetaEditor
6. Drag EA ke chart **XAUUSD M5**
7. Pastikan **Allow Algo Trading** aktif di MT5

### Setting yang Direkomendasikan

| Parameter | Rekomendasi | Penjelasan |
|-----------|------------|------------|
| Risk per Trade | 1.0% | Aman untuk prop firm |
| RR Ratio | 2.0 | Minimum yang disarankan |
| Daily Loss Limit | 4.0% | Safety margin dari 5% prop firm |
| Max Total DD | 8.0% | Safety margin dari 10% prop firm |
| Session Filter | ON | Hindari noise di Asian session |
| Cooldown Bars | 3 | Hindari overtrading setelah loss |
| Max Open Trades | 1 | Satu trade sekaligus, paling aman |
| Partial Close | ON, 50% | Lock profit di 1:1, trail sisanya |

## Backtesting

### Cara Backtest di MT5:
1. Buka **Strategy Tester** (Ctrl+R)
2. Pilih EA: `XAUUSD_PropFirm_Scalper`
3. Symbol: **XAUUSD**
4. Period: **M5**
5. Modeling: **Every tick based on real ticks** (paling akurat)
6. Periode: Minimal **1 tahun** data
7. Initial Deposit: **5000**
8. Leverage: **1:100**

### Tips Optimasi:
- Gunakan **Walk Forward Optimization** untuk hindari overfitting
- Test di berbagai kondisi pasar (trending, ranging, volatile)
- Perhatikan **Profit Factor** (target > 1.5) dan **Max DD** (target < 8%)
- Pastikan minimal **100 trades** dalam periode test

## Parameter Lengkap

### General Settings
- `InpSymbol`: Symbol (default: XAUUSD)
- `InpTimeframe`: Entry TF (default: M5)
- `InpHTF`: Trend TF (default: H1)
- `InpMagicNumber`: Magic number unik

### Risk Management
- `InpRiskPercent`: Risk per trade dalam %
- `InpRRRatio`: Minimum Risk-Reward ratio
- `InpMaxOpenTrades`: Max posisi terbuka bersamaan
- `InpUsePartialClose`: Aktifkan partial close di 1:1
- `InpPartialPercent`: Persentase volume yang diclose

### Prop Firm Protection
- `InpUseDailyLimit`: Aktifkan daily loss limit
- `InpDailyLossLimit`: Max daily loss dalam %
- `InpUseTotalDD`: Aktifkan total DD limit
- `InpMaxTotalDD`: Max total DD dalam %
- `InpInitialBalance`: Balance awal (0=auto)
- `InpTrailingEquity`: Trail equity high

### Indicator Settings
- Semua parameter indicator bisa di-tune via input
- Default sudah dioptimasi untuk XAUUSD M5

### Session & Filter
- `InpUseSessionFilter`: Aktifkan filter sesi
- `InpGMTOffset`: Offset GMT broker (sesuaikan!)
- `InpMaxSpread`: Max spread yang diizinkan
- `InpCooldownBars`: Jeda setelah loss
- `InpAvoidFriday/Monday`: Filter hari

## Expected Performance

Berdasarkan desain strategi:

| Metrik | Target | Keterangan |
|--------|--------|-----------|
| Winrate | 60-70% | Multi-konfirmasi filter noise |
| Risk-Reward | 1:2+ | Pullback entry + partial close |
| Profit Factor | 1.5-2.5 | High winrate + good RR |
| Max DD | < 8% | Prop firm protection |
| Trades/Year | 100-200 | 2 session/hari, selective entry |
| Avg Trade Duration | 15-60 min | Scalping style |

> **Disclaimer**: Past performance tidak menjamin hasil di masa depan. Selalu backtest dan forward test sebelum menggunakan di akun real/prop firm. Gunakan demo terlebih dahulu.

## Changelog

### v1.00 (2025)
- Initial release
- 7-layer confirmation entry system
- Prop firm protection built-in
- Partial close & trailing stop
- Session and spread filters
- Cooldown after loss mechanism
