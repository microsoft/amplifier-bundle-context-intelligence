"""Blob read tool module — reads binary/text blobs from the context-intelligence server.

Implements the Amplifier Tool protocol.  Configuration is resolved lazily
via the ``context_intelligence.config_resolver`` coordinator capability
registered by the hook-context-intelligence module.
"""
