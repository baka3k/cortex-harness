# MCP Tools Reference and Pipeline Decomposition

## 1) MCP functions are defined where?

Primary MCP server definitions are in:
- stock_mcp/server.py

REST API (for n8n HTTP calls) is in:
- stock_mcp/http_api.py

Reusable subprocess-based runner is in:
- stock_mcp/analysis_runner.py

## 2) Existing high-level MCP tools (original)

Defined in stock_mcp/server.py:
- health_check
- analyze_symbol
- analyze_watchlist
- get_symbol_decision
- get_latest_report
- get_watchlist_config
- set_watchlist_config
- list_watchlist_configs

These are convenient but still relatively coarse-grained and can be slow when chained.

## 3) New granular pipeline MCP tools (step-by-step)

To support n8n customization, the pipeline is decomposed into independent MCP tools.
All are defined in stock_mcp/server.py.

### Step 1: Security profile
- pipeline_step_1_security_info
- Input: symbol, market
- Output: parsed security profile + raw payload
- Purpose: get static metadata without running full pipeline

### Step 2: Price data fetch
- pipeline_step_2_price_data
- Input: symbol, from_date, to_date, market
- Output: row counts + small preview for daily price and OHLC
- Purpose: fetch data only (no indicators/AI)

### Step 3: Indicators + signals
- pipeline_step_3_indicators_signals
- Input: symbol, from_date, to_date, market
- Output: latest indicator row + signal tail
- Purpose: compute technicals and composite BUY/SELL/NEUTRAL only

### Step 4-5: Flow analytics
- pipeline_step_4_5_flow_analytics
- Input: symbol, from_date, to_date, market
- Output:
  - foreign_flow
  - foreign_value_flow
  - order_flow
  - put_through
- Purpose: isolate liquidity/flow analysis from other heavy steps

### Step 6: Benchmark + breadth
- pipeline_step_6_benchmark_breadth
- Input: symbol, from_date, to_date, benchmark, market
- Output:
  - benchmark_comparison
  - market_breadth
- Purpose: relative performance and market context only

### Step 7: Risk + drawdown
- pipeline_step_7_risk_drawdown
- Input: symbol, from_date, to_date, market
- Output:
  - risk
  - drawdown
- Purpose: stop-loss/take-profit, VaR, max drawdown, volatility

### Step 8: Backtest
- pipeline_step_8_backtest
- Input: symbol, from_date, to_date, market, initial_capital, commission_pct
- Output: backtest metrics
- Purpose: strategy validation independent from report/LLM

### Step 9: Multi-timeframe
- pipeline_step_9_multi_timeframe
- Input: symbol, from_date, to_date, market
- Output: daily/weekly/monthly confluence
- Purpose: confirm trend across horizons

### Step 10: Sector peers
- pipeline_step_10_sector_peers
- Input: symbol, peers_csv, from_date, to_date, market
- Output: peer comparison and ranking
- Purpose: relative ranking against custom peer list

### Step 11: AI-1 weighted
- pipeline_step_11_ai_weighted
- Input: symbol, from_date, to_date, benchmark, market
- Output: weighted scorer result
- Purpose: rule-based AI signal only

### Step 12a: AI-2 ML
- pipeline_step_12_ai_ml
- Input: symbol, from_date, to_date, market, ml_model_path
- Output: ML recommendation + probability
- Purpose: ML signal only

### Step 12b: AI-3 LLM
- pipeline_step_12_ai_llm
- Input: symbol, from_date, to_date, benchmark, market, provider, llm_model, ml_model_path
- Output: LLM advice and optional AI-1/AI-2 context
- Purpose: run LLM reasoning without forcing full monolithic run

## 4) Current docs status

There is partial doc in docs/n8n_stock_api.md for old high-level tools.
This file (docs/mcp_pipeline_tools.md) documents the n