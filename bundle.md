---
bundle:
  name: context-intelligence
  version: 0.1.0
  description: >
    Context intelligence: event-driven property graph builder
    for Amplifier sessions.

includes:
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main

tools:
  - module: tool-graph-query
    source: git+https://github.com/colombod/amplifier-bundle-context-intelligence@main#subdirectory=modules/tool-graph-query
  - module: tool-blob-read
    source: git+https://github.com/colombod/amplifier-bundle-context-intelligence@main#subdirectory=modules/tool-blob-read

hooks:
  - module: hook-context-intelligence
    source: git+https://github.com/colombod/amplifier-bundle-context-intelligence@main#subdirectory=modules/hook-context-intelligence
    config:
      context_intelligence_server_url: "${AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL:}"
      workspace: "${AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE:}"
      log_level: "${AMPLIFIER_CONTEXT_INTELLIGENCE_LOG_LEVEL:INFO}"
      dispatch_timeout: "${AMPLIFIER_CONTEXT_INTELLIGENCE_DISPATCH_TIMEOUT:30}"
      dispatch_failure_threshold: "${AMPLIFIER_CONTEXT_INTELLIGENCE_DISPATCH_FAILURE_THRESHOLD:3}"

agents:
  include:
    - context-intelligence:context-intelligence-analyst

---

# Context Intelligence

---

@foundation:context/shared/common-system-base.md
