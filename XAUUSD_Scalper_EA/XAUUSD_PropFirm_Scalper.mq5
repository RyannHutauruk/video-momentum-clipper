//+------------------------------------------------------------------+
//|                              XAUUSD_PropFirm_Scalper.mq5         |
//|                              Multi-Confirmation M5 Scalper       |
//|                              Prop Firm Friendly Edition           |
//+------------------------------------------------------------------+
#property copyright   "PropFirm Scalper EA"
#property version     "1.00"
#property description "XAUUSD M5 Multi-Confirmation Scalper - Prop Firm Friendly"
#property description "Strategy: Pullback-to-Trend with RSI+Stochastic+MACD Confirmation"
#property description "Designed for 60%+ winrate, 1:2+ RR, low drawdown"

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\SymbolInfo.mqh>
#include <Trade\AccountInfo.mqh>

//+------------------------------------------------------------------+
//| INPUT PARAMETERS                                                  |
//+------------------------------------------------------------------+

//--- General Settings
input group "=== General Settings ==="
input string   InpSymbol          = "XAUUSD";       // Symbol to trade
input ENUM_TIMEFRAMES InpTimeframe = PERIOD_M5;      // Entry timeframe
input ENUM_TIMEFRAMES InpHTF      = PERIOD_H1;       // Higher timeframe for trend
input long     InpMagicNumber     = 20250430;         // Magic number
input string   InpComment         = "PropScalper";    // Order comment

//--- Risk Management
input group "=== Risk Management ==="
input double   InpRiskPercent     = 1.0;    // Risk per trade (% of balance)
input double   InpMinLotSize      = 0.01;   // Minimum lot size
input double   InpMaxLotSize      = 10.0;   // Maximum lot size
input double   InpRRRatio         = 2.0;    // Reward-to-Risk ratio (minimum 2.0)
input int      InpMaxOpenTrades   = 1;      // Max simultaneous trades
input bool     InpUsePartialClose = true;   // Use partial close at 1:1
input double   InpPartialPercent  = 50.0;   // Partial close percentage

//--- Prop Firm Protection
input group "=== Prop Firm Protection ==="
input bool     InpUseDailyLimit   = true;   // Enable daily loss limit
input double   InpDailyLossLimit  = 4.0;    // Max daily loss (% of initial balance)
input bool     InpUseTotalDD      = true;   // Enable total DD limit
input double   InpMaxTotalDD      = 8.0;    // Max total drawdown (% of initial balance)
input double   InpInitialBalance  = 0.0;    // Initial balance (0=auto detect)
input bool     InpTrailingEquity  = true;   // Trail equity high for DD calc

//--- Trend Filter (H1)
input group "=== Trend Filter (H1) ==="
input int      InpHTF_EMA_Period  = 50;     // H1 EMA period for trend direction
input int      InpHTF_ADX_Period  = 14;     // H1 ADX period
input double   InpHTF_ADX_Min     = 20.0;   // Minimum ADX for trend strength

//--- Entry Indicators (M5)
input group "=== Entry Indicators (M5) ==="
input int      InpFastEMA         = 9;      // Fast EMA period
input int      InpSlowEMA         = 21;     // Slow EMA period
input int      InpRSI_Period      = 14;     // RSI period
input double   InpRSI_BuyZoneLow  = 35.0;   // RSI buy zone low
input double   InpRSI_BuyZoneHigh = 55.0;   // RSI buy zone high
input double   InpRSI_SellZoneLow = 45.0;   // RSI sell zone low
input double   InpRSI_SellZoneHigh= 65.0;   // RSI sell zone high
input int      InpStoch_K         = 5;      // Stochastic %K period
input int      InpStoch_D         = 3;      // Stochastic %D period
input int      InpStoch_Slowing   = 3;      // Stochastic slowing
input double   InpStoch_OB        = 80.0;   // Stochastic overbought level
input double   InpStoch_OS        = 20.0;   // Stochastic oversold level
input int      InpMACD_Fast       = 12;     // MACD fast EMA
input int      InpMACD_Slow       = 26;     // MACD slow EMA
input int      InpMACD_Signal     = 9;      // MACD signal period

//--- Stop Loss / Take Profit
input group "=== SL/TP Settings ==="
input int      InpATR_Period      = 14;     // ATR period for SL
input double   InpATR_SL_Multi    = 1.5;    // ATR multiplier for SL
input double   InpMinSL_Points    = 50;     // Minimum SL in points
input double   InpMaxSL_Points    = 300;    // Maximum SL in points

//--- Trailing Stop
input group "=== Trailing Stop ==="
input bool     InpUseTrailing     = true;   // Enable trailing stop
input double   InpTrailActivation = 1.0;    // Activate trailing after x:1 RR achieved
input double   InpTrailStep       = 0.5;    // Trail by ATR multiplier

//--- Session Filter
input group "=== Session Filter (Server Time) ==="
input bool     InpUseSessionFilter= true;   // Enable session filter
input int      InpLondonStart     = 8;      // London session start hour
input int      InpLondonEnd       = 12;     // London session end hour
input int      InpNYStart         = 13;     // New York session start hour
input int      InpNYEnd           = 17;     // New York session end hour
input int      InpGMTOffset       = 0;      // Broker GMT offset (hours)

//--- Spread & Volatility Filter
input group "=== Filters ==="
input double   InpMaxSpread       = 50.0;   // Max spread in points
input int      InpCooldownBars    = 3;      // Cooldown bars after a loss
input bool     InpAvoidFriday     = true;   // Avoid trading on Friday after 15:00
input bool     InpAvoidMonday     = true;   // Avoid trading on Monday before 03:00

//--- Candlestick Confirmation
input group "=== Candle Confirmation ==="
input bool     InpRequireCandle   = true;   // Require candle pattern confirmation
input double   InpMinCandleBody   = 0.4;    // Min body/range ratio for valid candle

//+------------------------------------------------------------------+
//| GLOBAL VARIABLES                                                  |
//+------------------------------------------------------------------+
CTrade         trade;
CPositionInfo  posInfo;
CSymbolInfo    symInfo;
CAccountInfo   accInfo;

// Indicator handles
int hEMA_Fast, hEMA_Slow;
int hHTF_EMA, hHTF_ADX;
int hRSI, hStoch, hMACD;
int hATR;

// State tracking
double         gInitialBalance;
double         gDailyStartBalance;
double         gEquityHigh;
datetime       gLastTradeDay;
datetime       gLastLossTime;
int            gLastLossBar;
bool           gDailyLimitHit;
bool           gTotalDDHit;
int            gTodayTrades;
int            gTodayWins;
int            gTodayLosses;
double         gTodayProfit;

//+------------------------------------------------------------------+
//| Expert initialization function                                    |
//+------------------------------------------------------------------+
int OnInit()
{
   // Validate symbol
   if(!SymbolSelect(InpSymbol, true))
   {
      Print("Symbol ", InpSymbol, " not found!");
      return INIT_FAILED;
   }
   
   symInfo.Name(InpSymbol);
   
   // Setup trade object
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(30);
   trade.SetTypeFilling(ORDER_FILLING_FOK);
   
   // Create indicator handles - M5
   hEMA_Fast = iMA(InpSymbol, InpTimeframe, InpFastEMA, 0, MODE_EMA, PRICE_CLOSE);
   hEMA_Slow = iMA(InpSymbol, InpTimeframe, InpSlowEMA, 0, MODE_EMA, PRICE_CLOSE);
   hRSI      = iRSI(InpSymbol, InpTimeframe, InpRSI_Period, PRICE_CLOSE);
   hStoch    = iStochastic(InpSymbol, InpTimeframe, InpStoch_K, InpStoch_D, InpStoch_Slowing, MODE_SMA, STO_LOWHIGH);
   hMACD     = iMACD(InpSymbol, InpTimeframe, InpMACD_Fast, InpMACD_Slow, InpMACD_Signal, PRICE_CLOSE);
   hATR      = iATR(InpSymbol, InpTimeframe, InpATR_Period);
   
   // Create indicator handles - H1
   hHTF_EMA  = iMA(InpSymbol, InpHTF, InpHTF_EMA_Period, 0, MODE_EMA, PRICE_CLOSE);
   hHTF_ADX  = iADX(InpSymbol, InpHTF, InpHTF_ADX_Period);
   
   // Validate handles
   if(hEMA_Fast == INVALID_HANDLE || hEMA_Slow == INVALID_HANDLE ||
      hRSI == INVALID_HANDLE || hStoch == INVALID_HANDLE ||
      hMACD == INVALID_HANDLE || hATR == INVALID_HANDLE ||
      hHTF_EMA == INVALID_HANDLE || hHTF_ADX == INVALID_HANDLE)
   {
      Print("Failed to create indicator handles!");
      return INIT_FAILED;
   }
   
   // Initialize balance tracking
   gInitialBalance = (InpInitialBalance > 0) ? InpInitialBalance : accInfo.Balance();
   gDailyStartBalance = accInfo.Balance();
   gEquityHigh = accInfo.Equity();
   gLastTradeDay = 0;
   gLastLossTime = 0;
   gLastLossBar = 0;
   gDailyLimitHit = false;
   gTotalDDHit = false;
   gTodayTrades = 0;
   gTodayWins = 0;
   gTodayLosses = 0;
   gTodayProfit = 0.0;
   
   Print("=== XAUUSD PropFirm Scalper Initialized ===");
   Print("Initial Balance: ", gInitialBalance);
   Print("Daily Loss Limit: ", InpDailyLossLimit, "% = $", NormalizeDouble(gInitialBalance * InpDailyLossLimit / 100.0, 2));
   Print("Max Total DD: ", InpMaxTotalDD, "% = $", NormalizeDouble(gInitialBalance * InpMaxTotalDD / 100.0, 2));
   
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                   |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   IndicatorRelease(hEMA_Fast);
   IndicatorRelease(hEMA_Slow);
   IndicatorRelease(hHTF_EMA);
   IndicatorRelease(hHTF_ADX);
   IndicatorRelease(hRSI);
   IndicatorRelease(hStoch);
   IndicatorRelease(hMACD);
   IndicatorRelease(hATR);
   
   Print("=== XAUUSD PropFirm Scalper Deinitialized ===");
   Print("Today Trades: ", gTodayTrades, " | Wins: ", gTodayWins, " | Losses: ", gTodayLosses);
}

//+------------------------------------------------------------------+
//| Expert tick function                                              |
//+------------------------------------------------------------------+
void OnTick()
{
   // Update symbol info
   symInfo.RefreshRates();
   
   // Update daily tracking
   UpdateDailyTracking();
   
   // Update equity high
   double currentEquity = accInfo.Equity();
   if(currentEquity > gEquityHigh && InpTrailingEquity)
      gEquityHigh = currentEquity;
   
   // Check prop firm limits
   if(!CheckPropFirmLimits())
      return;
   
   // Manage existing positions (trailing stop, partial close)
   ManagePositions();
   
   // Only check for new signals on new bar
   if(!IsNewBar())
      return;
   
   // Check if we can open new trades
   if(CountMyPositions() >= InpMaxOpenTrades)
      return;
   
   // Check filters
   if(!CheckAllFilters())
      return;
   
   // Check for entry signals
   int signal = GetEntrySignal();
   
   if(signal != 0)
      ExecuteTrade(signal);
}

//+------------------------------------------------------------------+
//| Update daily tracking                                             |
//+------------------------------------------------------------------+
void UpdateDailyTracking()
{
   MqlDateTime dt;
   TimeCurrent(dt);
   datetime today = StringToTime(StringFormat("%04d.%02d.%02d", dt.year, dt.mon, dt.day));
   
   if(today != gLastTradeDay)
   {
      // New day
      gLastTradeDay = today;
      gDailyStartBalance = accInfo.Balance();
      gDailyLimitHit = false;
      gTodayTrades = 0;
      gTodayWins = 0;
      gTodayLosses = 0;
      gTodayProfit = 0.0;
      
      Print("--- New Trading Day: ", TimeToString(today, TIME_DATE), " ---");
      Print("Daily Start Balance: $", NormalizeDouble(gDailyStartBalance, 2));
   }
}

//+------------------------------------------------------------------+
//| Check prop firm limits                                            |
//+------------------------------------------------------------------+
bool CheckPropFirmLimits()
{
   double currentEquity = accInfo.Equity();
   double currentBalance = accInfo.Balance();
   
   // Check daily loss limit
   if(InpUseDailyLimit)
   {
      double dailyLossMax = gInitialBalance * InpDailyLossLimit / 100.0;
      double dailyPnL = currentEquity - gDailyStartBalance;
      
      if(dailyPnL <= -dailyLossMax)
      {
         if(!gDailyLimitHit)
         {
            gDailyLimitHit = true;
            Print("!!! DAILY LOSS LIMIT REACHED: $", NormalizeDouble(MathAbs(dailyPnL), 2),
                  " / $", NormalizeDouble(dailyLossMax, 2), " !!!");
            CloseAllPositions("Daily limit reached");
         }
         return false;
      }
   }
   
   // Check total drawdown limit
   if(InpUseTotalDD)
   {
      double maxDDAmount = gInitialBalance * InpMaxTotalDD / 100.0;
      double totalDD = gEquityHigh - currentEquity;
      
      if(totalDD >= maxDDAmount)
      {
         if(!gTotalDDHit)
         {
            gTotalDDHit = true;
            Print("!!! TOTAL DD LIMIT REACHED: $", NormalizeDouble(totalDD, 2),
                  " / $", NormalizeDouble(maxDDAmount, 2), " !!!");
            CloseAllPositions("Total DD limit reached");
         }
         return false;
      }
   }
   
   return true;
}

//+------------------------------------------------------------------+
//| Check all filters before entry                                    |
//+------------------------------------------------------------------+
bool CheckAllFilters()
{
   // Session filter
   if(InpUseSessionFilter && !IsValidSession())
   {
      return false;
   }
   
   // Spread filter
   double currentSpread = symInfo.Spread();
   if(currentSpread > InpMaxSpread)
   {
      return false;
   }
   
   // Cooldown after loss
   if(InpCooldownBars > 0 && gLastLossBar > 0)
   {
      int currentBar = iBars(InpSymbol, InpTimeframe);
      if((currentBar - gLastLossBar) < InpCooldownBars)
      {
         return false;
      }
   }
   
   // Friday filter
   if(InpAvoidFriday)
   {
      MqlDateTime dt;
      TimeCurrent(dt);
      if(dt.day_of_week == 5 && (dt.hour + InpGMTOffset) >= 15)
         return false;
   }
   
   // Monday filter
   if(InpAvoidMonday)
   {
      MqlDateTime dt;
      TimeCurrent(dt);
      if(dt.day_of_week == 1 && (dt.hour + InpGMTOffset) < 3)
         return false;
   }
   
   return true;
}

//+------------------------------------------------------------------+
//| Check if current time is within valid trading session              |
//+------------------------------------------------------------------+
bool IsValidSession()
{
   MqlDateTime dt;
   TimeCurrent(dt);
   int hour = dt.hour + InpGMTOffset;
   
   // Normalize hour
   if(hour < 0) hour += 24;
   if(hour >= 24) hour -= 24;
   
   // London session
   if(hour >= InpLondonStart && hour < InpLondonEnd)
      return true;
   
   // New York session
   if(hour >= InpNYStart && hour < InpNYEnd)
      return true;
   
   return false;
}

//+------------------------------------------------------------------+
//| Check for new bar                                                 |
//+------------------------------------------------------------------+
bool IsNewBar()
{
   static datetime lastBarTime = 0;
   datetime currentBarTime = iTime(InpSymbol, InpTimeframe, 0);
   
   if(currentBarTime != lastBarTime)
   {
      lastBarTime = currentBarTime;
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Get entry signal: +1=Buy, -1=Sell, 0=No signal                   |
//+------------------------------------------------------------------+
int GetEntrySignal()
{
   //=== STEP 1: Higher Timeframe Trend Filter ===
   int htfTrend = GetHTFTrend();
   if(htfTrend == 0)
      return 0;
   
   //=== STEP 2: M5 Trend Alignment ===
   double emaFast[], emaSlow[];
   ArraySetAsSeries(emaFast, true);
   ArraySetAsSeries(emaSlow, true);
   
   if(CopyBuffer(hEMA_Fast, 0, 1, 3, emaFast) < 3) return 0;
   if(CopyBuffer(hEMA_Slow, 0, 1, 3, emaSlow) < 3) return 0;
   
   bool m5Uptrend = (emaFast[0] > emaSlow[0]) && (emaFast[1] > emaSlow[1]);
   bool m5Downtrend = (emaFast[0] < emaSlow[0]) && (emaFast[1] < emaSlow[1]);
   
   //=== STEP 3: Pullback Detection ===
   double close1 = iClose(InpSymbol, InpTimeframe, 1);
   double close2 = iClose(InpSymbol, InpTimeframe, 2);
   double high1  = iHigh(InpSymbol, InpTimeframe, 1);
   double low1   = iLow(InpSymbol, InpTimeframe, 1);
   double open1  = iOpen(InpSymbol, InpTimeframe, 1);
   
   bool buyPullback = false;
   bool sellPullback = false;
   
   // Buy pullback: price touched or came near slow EMA in uptrend
   if(htfTrend == 1 && m5Uptrend)
   {
      // Price pulled back near or below slow EMA, then bounced
      if(low1 <= emaSlow[0] * 1.001 && close1 > emaSlow[0])
         buyPullback = true;
      // Or price is between fast and slow EMA (pullback zone)
      if(close1 >= emaSlow[0] && close1 <= emaFast[0] && close1 > open1)
         buyPullback = true;
   }
   
   // Sell pullback: price touched or came near slow EMA in downtrend
   if(htfTrend == -1 && m5Downtrend)
   {
      if(high1 >= emaSlow[0] * 0.999 && close1 < emaSlow[0])
         sellPullback = true;
      if(close1 <= emaSlow[0] && close1 >= emaFast[0] && close1 < open1)
         sellPullback = true;
   }
   
   if(!buyPullback && !sellPullback)
      return 0;
   
   //=== STEP 4: RSI Confirmation ===
   double rsi[];
   ArraySetAsSeries(rsi, true);
   if(CopyBuffer(hRSI, 0, 1, 3, rsi) < 3) return 0;
   
   bool rsiBuyOK = false;
   bool rsiSellOK = false;
   
   // RSI in buy zone (pulled back but not oversold - healthy pullback)
   if(rsi[0] >= InpRSI_BuyZoneLow && rsi[0] <= InpRSI_BuyZoneHigh && rsi[0] > rsi[1])
      rsiBuyOK = true;
   
   // RSI in sell zone (pulled back but not overbought)
   if(rsi[0] <= InpRSI_SellZoneHigh && rsi[0] >= InpRSI_SellZoneLow && rsi[0] < rsi[1])
      rsiSellOK = true;
   
   //=== STEP 5: Stochastic Confirmation ===
   double stochK[], stochD[];
   ArraySetAsSeries(stochK, true);
   ArraySetAsSeries(stochD, true);
   if(CopyBuffer(hStoch, 0, 1, 3, stochK) < 3) return 0;
   if(CopyBuffer(hStoch, 1, 1, 3, stochD) < 3) return 0;
   
   bool stochBuyOK = false;
   bool stochSellOK = false;
   
   // Stochastic cross up from oversold zone
   if(stochK[0] > stochD[0] && stochK[1] <= stochD[1] && stochK[0] < 50)
      stochBuyOK = true;
   // Or Stochastic turning up from low zone
   if(stochK[0] > stochK[1] && stochK[1] < InpStoch_OS + 10 && stochK[0] < 50)
      stochBuyOK = true;
   
   // Stochastic cross down from overbought zone
   if(stochK[0] < stochD[0] && stochK[1] >= stochD[1] && stochK[0] > 50)
      stochSellOK = true;
   // Or Stochastic turning down from high zone
   if(stochK[0] < stochK[1] && stochK[1] > InpStoch_OB - 10 && stochK[0] > 50)
      stochSellOK = true;
   
   //=== STEP 6: MACD Histogram Confirmation ===
   double macdMain[], macdSignal[];
   ArraySetAsSeries(macdMain, true);
   ArraySetAsSeries(macdSignal, true);
   if(CopyBuffer(hMACD, 0, 1, 3, macdMain) < 3) return 0;
   if(CopyBuffer(hMACD, 1, 1, 3, macdSignal) < 3) return 0;
   
   double hist0 = macdMain[0] - macdSignal[0];
   double hist1 = macdMain[1] - macdSignal[1];
   
   bool macdBuyOK = (hist0 > hist1); // Histogram increasing (momentum shifting up)
   bool macdSellOK = (hist0 < hist1); // Histogram decreasing
   
   //=== STEP 7: Candlestick Pattern Confirmation ===
   bool candleBuyOK = true;
   bool candleSellOK = true;
   
   if(InpRequireCandle)
   {
      candleBuyOK = IsBullishPattern(1);
      candleSellOK = IsBearishPattern(1);
   }
   
   //=== FINAL SIGNAL EVALUATION ===
   // BUY: All confirmations must align
   if(buyPullback && rsiBuyOK && stochBuyOK && macdBuyOK && candleBuyOK)
   {
      Print("BUY Signal - Trend:", htfTrend, " RSI:", NormalizeDouble(rsi[0], 1),
            " StochK:", NormalizeDouble(stochK[0], 1), " MACD Hist:", NormalizeDouble(hist0, 5));
      return 1;
   }
   
   // SELL: All confirmations must align
   if(sellPullback && rsiSellOK && stochSellOK && macdSellOK && candleSellOK)
   {
      Print("SELL Signal - Trend:", htfTrend, " RSI:", NormalizeDouble(rsi[0], 1),
            " StochK:", NormalizeDouble(stochK[0], 1), " MACD Hist:", NormalizeDouble(hist0, 5));
      return -1;
   }
   
   return 0;
}

//+------------------------------------------------------------------+
//| Get higher timeframe trend direction                              |
//+------------------------------------------------------------------+
int GetHTFTrend()
{
   double htfEma[];
   double htfADX[], htfPDI[], htfMDI[];
   ArraySetAsSeries(htfEma, true);
   ArraySetAsSeries(htfADX, true);
   ArraySetAsSeries(htfPDI, true);
   ArraySetAsSeries(htfMDI, true);
   
   if(CopyBuffer(hHTF_EMA, 0, 0, 3, htfEma) < 3) return 0;
   if(CopyBuffer(hHTF_ADX, 0, 0, 3, htfADX) < 3) return 0;
   if(CopyBuffer(hHTF_ADX, 1, 0, 3, htfPDI) < 3) return 0;
   if(CopyBuffer(hHTF_ADX, 2, 0, 3, htfMDI) < 3) return 0;
   
   double htfClose = iClose(InpSymbol, InpHTF, 0);
   
   // ADX must show trend strength
   if(htfADX[0] < InpHTF_ADX_Min)
      return 0;
   
   // Uptrend: price above H1 EMA and +DI > -DI
   if(htfClose > htfEma[0] && htfPDI[0] > htfMDI[0])
      return 1;
   
   // Downtrend: price below H1 EMA and -DI > +DI
   if(htfClose < htfEma[0] && htfMDI[0] > htfPDI[0])
      return -1;
   
   return 0;
}

//+------------------------------------------------------------------+
//| Check for bullish candlestick pattern                             |
//+------------------------------------------------------------------+
bool IsBullishPattern(int shift)
{
   double open1  = iOpen(InpSymbol, InpTimeframe, shift);
   double close1 = iClose(InpSymbol, InpTimeframe, shift);
   double high1  = iHigh(InpSymbol, InpTimeframe, shift);
   double low1   = iLow(InpSymbol, InpTimeframe, shift);
   double open2  = iOpen(InpSymbol, InpTimeframe, shift + 1);
   double close2 = iClose(InpSymbol, InpTimeframe, shift + 1);
   
   double range1 = high1 - low1;
   if(range1 <= 0) return false;
   
   double body1 = MathAbs(close1 - open1);
   double bodyRatio = body1 / range1;
   
   // Bullish engulfing
   if(close1 > open1 && close2 < open2)  // Current bullish, prev bearish
   {
      if(close1 > open2 && open1 < close2)  // Engulfs previous
         if(bodyRatio >= InpMinCandleBody)
            return true;
   }
   
   // Bullish pin bar (hammer)
   double lowerWick = MathMin(open1, close1) - low1;
   double upperWick = high1 - MathMax(open1, close1);
   if(lowerWick > body1 * 2.0 && upperWick < body1 * 0.5 && close1 > open1)
   {
      if(bodyRatio >= InpMinCandleBody * 0.5)  // Pin bars have smaller body ratio
         return true;
   }
   
   // Strong bullish candle
   if(close1 > open1 && bodyRatio >= InpMinCandleBody * 1.5)
      return true;
   
   return false;
}

//+------------------------------------------------------------------+
//| Check for bearish candlestick pattern                             |
//+------------------------------------------------------------------+
bool IsBearishPattern(int shift)
{
   double open1  = iOpen(InpSymbol, InpTimeframe, shift);
   double close1 = iClose(InpSymbol, InpTimeframe, shift);
   double high1  = iHigh(InpSymbol, InpTimeframe, shift);
   double low1   = iLow(InpSymbol, InpTimeframe, shift);
   double open2  = iOpen(InpSymbol, InpTimeframe, shift + 1);
   double close2 = iClose(InpSymbol, InpTimeframe, shift + 1);
   
   double range1 = high1 - low1;
   if(range1 <= 0) return false;
   
   double body1 = MathAbs(close1 - open1);
   double bodyRatio = body1 / range1;
   
   // Bearish engulfing
   if(close1 < open1 && close2 > open2)
   {
      if(open1 > close2 && close1 < open2)
         if(bodyRatio >= InpMinCandleBody)
            return true;
   }
   
   // Bearish pin bar (shooting star)
   double upperWick = high1 - MathMax(open1, close1);
   double lowerWick = MathMin(open1, close1) - low1;
   if(upperWick > body1 * 2.0 && lowerWick < body1 * 0.5 && close1 < open1)
   {
      if(bodyRatio >= InpMinCandleBody * 0.5)
         return true;
   }
   
   // Strong bearish candle
   if(close1 < open1 && bodyRatio >= InpMinCandleBody * 1.5)
      return true;
   
   return false;
}

//+------------------------------------------------------------------+
//| Execute trade with proper lot sizing and SL/TP                    |
//+------------------------------------------------------------------+
void ExecuteTrade(int direction)
{
   // Get ATR for SL calculation
   double atr[];
   ArraySetAsSeries(atr, true);
   if(CopyBuffer(hATR, 0, 1, 1, atr) < 1) return;
   
   double atrValue = atr[0];
   double point = symInfo.Point();
   double ask = symInfo.Ask();
   double bid = symInfo.Bid();
   int digits = symInfo.Digits();
   
   // Calculate SL distance in points
   double slPoints = atrValue / point * InpATR_SL_Multi;
   
   // Clamp SL
   if(slPoints < InpMinSL_Points) slPoints = InpMinSL_Points;
   if(slPoints > InpMaxSL_Points) slPoints = InpMaxSL_Points;
   
   double slDistance = slPoints * point;
   double tpDistance = slDistance * InpRRRatio;
   
   double sl, tp, entryPrice;
   
   if(direction == 1) // BUY
   {
      entryPrice = ask;
      sl = NormalizeDouble(entryPrice - slDistance, digits);
      tp = NormalizeDouble(entryPrice + tpDistance, digits);
   }
   else // SELL
   {
      entryPrice = bid;
      sl = NormalizeDouble(entryPrice + slDistance, digits);
      tp = NormalizeDouble(entryPrice - tpDistance, digits);
   }
   
   // Calculate lot size based on risk
   double lotSize = CalculateLotSize(slPoints);
   if(lotSize < InpMinLotSize) lotSize = InpMinLotSize;
   if(lotSize > InpMaxLotSize) lotSize = InpMaxLotSize;
   
   // Validate lot size
   double minLot = SymbolInfoDouble(InpSymbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(InpSymbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(InpSymbol, SYMBOL_VOLUME_STEP);
   
   lotSize = MathMax(lotSize, minLot);
   lotSize = MathMin(lotSize, maxLot);
   lotSize = NormalizeDouble(MathFloor(lotSize / lotStep) * lotStep, 2);
   
   if(lotSize < minLot)
   {
      Print("Lot size too small after normalization: ", lotSize);
      return;
   }
   
   // Check if this trade would exceed daily loss if it hits SL
   if(InpUseDailyLimit)
   {
      double potentialLoss = slPoints * point * lotSize * SymbolInfoDouble(InpSymbol, SYMBOL_TRADE_TICK_VALUE) / SymbolInfoDouble(InpSymbol, SYMBOL_TRADE_TICK_SIZE);
      double dailyLossMax = gInitialBalance * InpDailyLossLimit / 100.0;
      double currentDailyLoss = gDailyStartBalance - accInfo.Equity();
      
      if((currentDailyLoss + potentialLoss) > dailyLossMax * 0.9) // 90% safety margin
      {
         Print("Trade would risk exceeding daily loss limit. Skipping.");
         return;
      }
   }
   
   // Execute trade
   bool result;
   string orderComment = StringFormat("%s_%s", InpComment, (direction == 1) ? "BUY" : "SELL");
   
   if(direction == 1)
      result = trade.Buy(lotSize, InpSymbol, entryPrice, sl, tp, orderComment);
   else
      result = trade.Sell(lotSize, InpSymbol, entryPrice, sl, tp, orderComment);
   
   if(result)
   {
      gTodayTrades++;
      Print("Trade opened: ", (direction == 1) ? "BUY" : "SELL",
            " | Lot: ", lotSize,
            " | Entry: ", entryPrice,
            " | SL: ", sl, " (", NormalizeDouble(slPoints, 0), " pts)",
            " | TP: ", tp, " (", NormalizeDouble(slPoints * InpRRRatio, 0), " pts)",
            " | RR: 1:", InpRRRatio);
   }
   else
   {
      Print("Trade failed: ", trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Calculate lot size based on risk percentage                       |
//+------------------------------------------------------------------+
double CalculateLotSize(double slPoints)
{
   double balance = accInfo.Balance();
   double riskAmount = balance * InpRiskPercent / 100.0;
   
   double tickValue = SymbolInfoDouble(InpSymbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(InpSymbol, SYMBOL_TRADE_TICK_SIZE);
   double point = symInfo.Point();
   
   if(tickValue <= 0 || tickSize <= 0 || point <= 0)
      return InpMinLotSize;
   
   double slMoney = slPoints * point * tickValue / tickSize;
   
   if(slMoney <= 0)
      return InpMinLotSize;
   
   double lots = riskAmount / slMoney;
   
   return lots;
}

//+------------------------------------------------------------------+
//| Manage existing positions (trailing, partial close)               |
//+------------------------------------------------------------------+
void ManagePositions()
{
   double atr[];
   ArraySetAsSeries(atr, true);
   if(CopyBuffer(hATR, 0, 0, 1, atr) < 1) return;
   
   double atrValue = atr[0];
   double point = symInfo.Point();
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!posInfo.SelectByIndex(i))
         continue;
      
      if(posInfo.Magic() != InpMagicNumber)
         continue;
      
      if(posInfo.Symbol() != InpSymbol)
         continue;
      
      double openPrice = posInfo.PriceOpen();
      double currentSL = posInfo.StopLoss();
      double currentTP = posInfo.TakeProfit();
      double currentPrice = (posInfo.PositionType() == POSITION_TYPE_BUY) ? symInfo.Bid() : symInfo.Ask();
      double posProfit = posInfo.Profit() + posInfo.Swap() + posInfo.Commission();
      
      // Calculate SL distance from entry
      double slDistFromEntry = MathAbs(openPrice - currentSL);
      
      // Partial close at 1:1
      if(InpUsePartialClose)
      {
         HandlePartialClose(posInfo, openPrice, slDistFromEntry, currentPrice);
      }
      
      // Trailing stop
      if(InpUseTrailing)
      {
         HandleTrailingStop(posInfo, openPrice, slDistFromEntry, currentPrice, atrValue);
      }
   }
}

//+------------------------------------------------------------------+
//| Handle partial close at 1:1 RR                                    |
//+------------------------------------------------------------------+
void HandlePartialClose(CPositionInfo &pos, double openPrice, double slDist, double currentPrice)
{
   double profitDist = 0;
   
   if(pos.PositionType() == POSITION_TYPE_BUY)
      profitDist = currentPrice - openPrice;
   else
      profitDist = openPrice - currentPrice;
   
   // Check if we've reached 1:1
   if(profitDist >= slDist)
   {
      // Check if we haven't already partially closed (volume check)
      double currentVolume = pos.Volume();
      double minLot = SymbolInfoDouble(InpSymbol, SYMBOL_VOLUME_MIN);
      double lotStep = SymbolInfoDouble(InpSymbol, SYMBOL_VOLUME_STEP);
      
      double closeVolume = NormalizeDouble(currentVolume * InpPartialPercent / 100.0, 2);
      closeVolume = NormalizeDouble(MathFloor(closeVolume / lotStep) * lotStep, 2);
      
      if(closeVolume >= minLot && currentVolume > minLot)
      {
         // Move SL to breakeven first
         double newSL = openPrice;
         if(pos.PositionType() == POSITION_TYPE_BUY && currentPrice > openPrice + slDist)
         {
            if(currentSL(pos) < openPrice)
            {
               trade.PositionModify(pos.Ticket(), newSL, pos.TakeProfit());
               trade.PositionClosePartial(pos.Ticket(), closeVolume);
               Print("Partial close at 1:1 | Volume closed: ", closeVolume, " | SL moved to BE");
            }
         }
         else if(pos.PositionType() == POSITION_TYPE_SELL && currentPrice < openPrice - slDist)
         {
            if(currentSL(pos) > openPrice || currentSL(pos) == 0)
            {
               trade.PositionModify(pos.Ticket(), newSL, pos.TakeProfit());
               trade.PositionClosePartial(pos.Ticket(), closeVolume);
               Print("Partial close at 1:1 | Volume closed: ", closeVolume, " | SL moved to BE");
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Helper to get current SL                                          |
//+------------------------------------------------------------------+
double currentSL(CPositionInfo &pos)
{
   return pos.StopLoss();
}

//+------------------------------------------------------------------+
//| Handle trailing stop                                              |
//+------------------------------------------------------------------+
void HandleTrailingStop(CPositionInfo &pos, double openPrice, double slDist, double currentPrice, double atrValue)
{
   double point = symInfo.Point();
   int digits = symInfo.Digits();
   
   double profitDist = 0;
   if(pos.PositionType() == POSITION_TYPE_BUY)
      profitDist = currentPrice - openPrice;
   else
      profitDist = openPrice - currentPrice;
   
   // Only trail after activation level
   double activationDist = slDist * InpTrailActivation;
   if(profitDist < activationDist)
      return;
   
   double trailDist = atrValue * InpTrailStep;
   double newSL;
   
   if(pos.PositionType() == POSITION_TYPE_BUY)
   {
      newSL = NormalizeDouble(currentPrice - trailDist, digits);
      if(newSL > pos.StopLoss() && newSL > openPrice)
      {
         trade.PositionModify(pos.Ticket(), newSL, pos.TakeProfit());
      }
   }
   else // SELL
   {
      newSL = NormalizeDouble(currentPrice + trailDist, digits);
      if((newSL < pos.StopLoss() || pos.StopLoss() == 0) && newSL < openPrice)
      {
         trade.PositionModify(pos.Ticket(), newSL, pos.TakeProfit());
      }
   }
}

//+------------------------------------------------------------------+
//| Count positions with our magic number                             |
//+------------------------------------------------------------------+
int CountMyPositions()
{
   int count = 0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(posInfo.SelectByIndex(i))
      {
         if(posInfo.Magic() == InpMagicNumber && posInfo.Symbol() == InpSymbol)
            count++;
      }
   }
   return count;
}

//+------------------------------------------------------------------+
//| Close all positions                                               |
//+------------------------------------------------------------------+
void CloseAllPositions(string reason)
{
   Print("Closing all positions: ", reason);
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(posInfo.SelectByIndex(i))
      {
         if(posInfo.Magic() == InpMagicNumber && posInfo.Symbol() == InpSymbol)
         {
            trade.PositionClose(posInfo.Ticket());
         }
      }
   }
}

//+------------------------------------------------------------------+
//| OnTradeTransaction - Track wins/losses                            |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD)
   {
      CDealInfo deal;
      if(deal.SelectByIndex(HistoryDealsTotal() - 1))
      {
         if(deal.Magic() == InpMagicNumber && deal.Symbol() == InpSymbol)
         {
            if(deal.Entry() == DEAL_ENTRY_OUT || deal.Entry() == DEAL_ENTRY_OUT_BY)
            {
               double profit = deal.Profit() + deal.Swap() + deal.Commission();
               gTodayProfit += profit;
               
               if(profit > 0)
               {
                  gTodayWins++;
                  Print("WIN: $", NormalizeDouble(profit, 2), " | Today W/L: ", gTodayWins, "/", gTodayLosses);
               }
               else if(profit < 0)
               {
                  gTodayLosses++;
                  gLastLossBar = iBars(InpSymbol, InpTimeframe);
                  Print("LOSS: $", NormalizeDouble(profit, 2), " | Today W/L: ", gTodayWins, "/", gTodayLosses);
               }
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Display info on chart                                             |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   // Can be expanded for chart button controls
}

//+------------------------------------------------------------------+
