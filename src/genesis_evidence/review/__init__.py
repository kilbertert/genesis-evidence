"""Human review and knowledge-card publication.

``service`` is imported lazily (via consumers importing ``.service`` directly,
and review stores importing ``.scope``) so that importing this package does not
eagerly pull ``core.store`` through the service and create an import cycle.
"""
