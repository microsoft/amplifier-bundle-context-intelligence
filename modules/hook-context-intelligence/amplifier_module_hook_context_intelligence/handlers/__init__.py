"""Context Intelligence event handlers.

Only LoggingHandler is retained in the thin-forwarder bundle.
All graph-creation handlers (SessionHandler, OrchestratorRunHandler,
StepHandler, ToolExecutionHandler, RecipeHandler, DefaultHandler,
SystemEventHandler) have been moved to the amplifier-context-intelligence
server.
"""
