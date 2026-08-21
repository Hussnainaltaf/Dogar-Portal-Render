"""Application service layer.

Services in this package implement business logic shared across API endpoints:
- pdf_engine       — PDF generation with background overlay (protector letters, visa forms)
- passport_ocr     — OCR passport extraction and validation
- dtcbot / dtcbot_agent / dtcbot_llm — AI chatbot integration
- email            — SMTP email sending (password reset, notifications)
- cache            — Redis-backed caching layer
- overlay_engine   — Document template overlay and page-size engine
- arabic_translate — Arabic translation utilities
- passport_vision  — Computer vision passport processing
"""
