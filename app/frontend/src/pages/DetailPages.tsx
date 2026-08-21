// Client Detail moved to its own file (full v2-parity: hero + stat tiles +
// 4 tabs + edit/contact/statement modals + print). Re-export for App.tsx.
export { ClientDetail } from './ClientDetail'

// NOTE: The old read-only "DocumentBuilder" page was removed — it duplicated
// the full Template Designer (coordinate engine) served at
// /documents/customize/:tplId. The backend 301-redirects /documents/builder/*.
//
// REMOVED 2026-07-30 (operator request): the `AgentCashbook` page that used to
// live here, together with the whole Sub-Agents feature. Its imports went with
// it, which is why this module is now a single re-export. See section 4.11 of
// PROJECT_DOCUMENTATION.md.
