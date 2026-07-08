"""Web front-end: a phone-friendly form over the same domain + engine + report.

Carries no Qt dependency — reuses ``bridge_mc.engine`` and
``bridge_mc.report.render_html`` directly, so the whole simulator runs headless
in a small Linux container and is driven from any browser.
"""
