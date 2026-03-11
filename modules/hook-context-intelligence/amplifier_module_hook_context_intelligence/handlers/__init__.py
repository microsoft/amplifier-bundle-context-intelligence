"""Event handlers for the context-intelligence hook module.

Eight handlers, each conforming to the EventHandler protocol:
- SessionHandler — :Session nodes
- OrchestratorRunHandler — :OrchestratorRun and :Step:PromptStep nodes
- StepHandler — :Step:AssistantStep nodes
- RecipeHandler — recipe orchestration events (:Event:RecipeStart, :Event:RecipeStep, etc.)
- ToolExecutionHandler — :ToolExecution nodes
- SystemEventHandler — :Event:ContextCompaction, :Event:CancelRequested, etc.
- DefaultHandler — :Event:{DerivedFullScope} (dynamic labels)
- LoggingHandler — always-on flat JSONL session file writer
"""
