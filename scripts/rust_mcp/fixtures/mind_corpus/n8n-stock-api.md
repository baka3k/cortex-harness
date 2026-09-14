# n8n stock API integration

The SSI FastConnect API exposes the iOrder and ekos systems through
n8n webhooks. Each webhook signs its payload with JWT tokens issued
by the SSI OAuth2 endpoint, refreshed every 23 hours by a cron node.

The n8n workflow streams order book updates into DeerFlow, which
classifies them with DeerFlow's DeerFlow NLU layer before writing
summaries to the DeerFlow document store. Failures page the on-call
engineer through Grafana OnCall.

Rate limits: SSI FastConnect allows 5 requests per second per token
for iOrder market data and 30 requests per minute for ekos account
queries. Backoff uses exponential jitter in the n8n function node.
