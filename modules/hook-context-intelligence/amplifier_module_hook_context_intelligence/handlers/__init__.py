"""Event handlers for the context-intelligence hook module.

Seven handlers, each conforming to the EventHandler protocol:
- SessionHandler — :Session nodes
- OrchestratorRunHandler — :OrchestratorRun and :Step:PromptStep nodes
- StepHandler — :Step:AssistantStep nodes
- RecipeStepHandler — :Step:RecipeStep nodes
- ToolExecutionHandler — :ToolExecution nodes
- SystemEventHandler — :Event:ContextCompaction, :Event:CancelRequested, etc.
- DefaultHandler — :Event:{DerivedFullScope} (dynamic labels)
"""
