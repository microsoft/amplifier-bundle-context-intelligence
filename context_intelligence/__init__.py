"""context_intelligence — structured session data library.

This package organises functionality across three architectural levels:

Level 1 — Pure Transforms
    Stateless functions that convert raw data into structured representations.
    No I/O, no side effects. These can be tested in complete isolation.

Level 2 — Network I/O
    Functions and classes that communicate with the context-intelligence server
    (graph store, blob storage). Depend only on Level 1 transforms.

Level 3 — Filesystem + Orchestration
    Code that reads session files from disk, drives upload pipelines, and
    coordinates the other levels. Depends on Levels 1 and 2.

Imports are deferred to Task 9 when the public API is finalised.
"""
