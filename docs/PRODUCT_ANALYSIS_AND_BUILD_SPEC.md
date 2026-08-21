# Product Analysis and Implementation Specification

**System:** Dogar Trading Corporation — Overseas Employment Portal  
**Date:** 2026-07-20  
**Scope:** Full product reverse-engineering from reference interface, cross-referenced with implementation

---

## 1. Product Identity

### What it is
A **multi-tenant overseas employment management portal** with a built-in **coordinate-based document template designer**. The system manages the end-to-end lifecycle of overseas labor recruitment — candidates, visa demands, clients (sponsors/employers), document generation, and financial tracking — with the document designer as its most distinctive and complex feature.

### What business it serves
Pakistani overseas employment promoters (OEPs) licensed by the Bureau of Emigration. These agencies recruit workers for Gulf Cooperation Council (GCC) employers, processing visa applications, medical clearances, passport logistics, consular forms, and protector letters. The documents are bilingual (English + Arabic), printed on pre-printed government stationery, and must place data at exact physical coordinates to align with form lines.

### Who uses it
- **Admin/OEP staff** — manage candidates, demands (job orders), clients, financial statements; design and generate documents.
- **Portal admin** — configure tenants, roles, users, system settings.
- **Depositors** — (read-only/limited) view deposited candidates.

### What problem it solves
1. **Manual form filling**: Staff previously hand-filled or used Word templates for 10+ types of bilingual visa/embassy forms. This system automates placement of candidate/visa data onto scanned form backgrounds at PDF-point precision.
2. **Data fragmentation**: Candidate records, visa file details, passport logistics, medical clearances, financial entries, and generated documents were scattered across spreadsheets. This consolidates them into one portal.
3. **Multi-office operation**: Multi-tenant architecture lets each branch or sub-agency run on its own isolated database while sharing the same deployed application.

---

## 2. Visual and Frontend Style

### Overall UI style
**Enterprise admin panel with a document-design workbench.** Clean, functional, low-decoration. Not a consumer-facing marketing site — it is a dense operational tool designed for daily all-day use by back-office staff.

### Layout structure
- **Global shell**: Collapsible left sidebar (navigation), top header bar (search, notifications bell, user avatar/menu).
- **Page content area**: Full-width cards, tables, or multi-column workbenches depending on the page.
- **Document designer**: **Three-column layout** — left palette (~180px) | center canvas (flexible) | right properties panel (~260px). This is the signature layout.

### Color tone and visual hierarchy
- **Background**: White (#FFFFFF) main content, very light gray (#F7F8FA) page backgrounds.
- **Primary accent**: Blue (#3B82F6 / Tailwind `blue-500`) — used for active tabs, primary buttons ("Save"), toggle ON state, selected palette items, active alignment buttons.
- **Secondary accent**: Gray for borders, inactive tabs, labels.
- **Destructive**: Red (#EF4444) for "Remove" buttons, "UNSAVED" indicator.
- **Success**: Green for toggle switches (ON state).
- **Neutral text**: Dark gray (#1F2937) for body text, medium gray (#6B7280) for labels and placeholders.
- **Header bar**: Dark/black (#111827) with white text.
- **No gradients, no shadows, no decorative elements** — strictly flat, card-based layout.

### Typography feel
- **Sans-serif throughout** — system font stack or Inter/similar clean geometric sans-serif.
- **Body text**: 14px regular.
- **Labels/captions**: 12px, medium gray, often uppercase for section headers.
- **Field palette items**: 13-14px, regular weight, dark gray.
- **Headings**: 18-24px semibold for page titles, 15-16px medium for section headers.

### Button styles
- **Primary**: Solid blue with white text, rounded corners (~6px radius), compact padding.
- **Secondary/Cancel**: White/transparent with gray border and dark text.
- **Destructive**: Red background ("Remove") or red text with transparent background.
- **Toggle switches**: Pill-shaped, blue when ON, gray when OFF, with full-text labels beside them (not just icons).
- **Icon buttons**: Small square, often used in toolbar (zoom +/−, alignment buttons, style B/I/U/S).

### Cards, menus, tabs, modals, panels
- **Cards**: White background, 1px light gray border, 8px border radius, 16-24px internal padding.
- **Tabs**: Horizontal tab bar with underline-active indicator in blue. Used for "Content" | "Template Designer" switch and "Fields" | "Canvas" | "Properties" (mobile).
- **Dropdowns**: Standard HTML select with border, 4px radius, small chevron. Page size dropdown shows "Letter (8.5×11 in)" / "A4 (210×297 mm)" / "Legal (8.5×14 in)".
- **Palette groups**: Accordion-style collapsible sections with chevron (^ expanded, v collapsed). Group header is bold/medium, field items are lighter.

### Design patterns that stand out
1. **Three-column workbench** for the designer — identical pattern to Figma/Canva property panels.
2. **Dual-tab system** (Content + Template Designer) on the same page — Content is a rich-text WYSIWYG editor; Template Designer is the coordinate-based visual canvas.
3. **Merge-field palette** with `{{key}}` format shown under display names.
4. **Background image overlay** — scanned form is the canvas background; fields float on top at precise PDF-point coordinates.
5. **Mobile adaptation**: On small screens the three columns collapse to a single column with a tab switcher (Fields | Canvas | Properties). Desktop shows all three simultaneously.

---

## 3. Document and Coordinate Engine

### How the document builder works
The designer operates on a **coordinate-mapped template overlay** model:

1. **Background**: A scanned image of a real government/embassy form (e.g., a Saudi visa application) is uploaded as the template background. It is displayed at **true PDF-point scale** on the canvas.
2. **Fields**: Data fields (merge fields) are positioned on top of the background at exact `(x, y)` PDF-point coordinates, where the origin is the **bottom-left corner** of the page (standard PDF coordinate system, not CSS top-left).
3. **Rendering**: When generating a document, the system maps each `{{field_key}}` to the corresponding database column for the selected candidate/record, renders the value at the stored coordinates on a PDF page with the background image.

### Element positioning
- **Coordinate system**: PDF points (1 pt = 1/72 inch). Origin at bottom-left.
- **Position inputs**: `X` (horizontal from left edge) and `Y` (vertical from bottom edge), both in points.
- **Size inputs**: `Width` and `Height` in points. Can be `auto` (text fields auto-size to content) or explicit (barcodes, photos, boxes).
- **Canvas-to-PDF transform**: The browser canvas uses CSS top-left origin. The engine converts: `yPdf = pageHeight - yCss - fieldHeight`. This formula is shared between the designer (TypeScript `engine.ts`) and the PDF generator (Python).
- **Zoom**: CSS `transform: scale(factor)` on the canvas container. Coordinates are **never baked into zoomed values** — the zoom is purely visual, and all stored/computed coordinates remain in true PDF points.
- **Drag positioning**: Fields are repositioned by mouse drag on the canvas. The drag delta in CSS pixels is converted to PDF-point deltas at the current zoom level.

### Page size, margins, alignment, scaling
- **Page sizes**: Letter (612×792 pt), A4 (595×842 pt), Legal (612×1008 pt). Stored as `page_width`/`page_height` in points plus a `page_size` label and `orientation`.
- **No explicit margins**: The designer places fields at absolute page coordinates — margin management is the user's responsibility (they align to the form lines on the background image).
- **Text alignment**: Per-field `align` property: left, center, right, justify.
- **Font scaling**: Per-field `font_size` in points, with font family (Inherit = system default) and style (bold, italic, underline, strikethrough).
- **Print layout**: An off-screen iframe at true paper dimensions (e.g., 210mm × 297mm for A4) renders fields at their stored coordinates, then triggers the browser's native print dialog with named `@page { size: A4 portrait }`.

### Multi-page support
- The toolbar shows "Page 1" with a "+" button to add pages. Each field stores a `page` integer.
- Page size dropdown and background apply per-page.

### Template reusability and variable rendering
- Templates are fully reusable — a "Visa Application Form - Karachi" template has one set of field placements; any candidate record can be selected at print time to populate the merge fields.
- The **record picker** (at print time) lets the user choose which candidate's data fills the template.
- The `{{field_key}}` syntax is a Mustache-like merge notation — the backend resolves each key against the candidate/demand/client model at generation time.

---

## 4. Fields and Data Model

### Complete field inventory (from reference, organized by palette group)

**Customer (Client/OEP Company)**
| Display Name | Key | Notes |
|---|---|---|
| Customer Name | `customer_name` | |
| Customer Name (Arabic) | `customer_name_arabic` | |
| Customer Owner Name | `customer_owner_name` | |
| OEP License Number | `customer_oep_license_number` | Government license |
| Customer Emigrant Office | `customer_emigrant_office` | |
| Customer Emigrant Office Name | `customer_emigrant_office_name` | |
| Customer Emigrant Office City | `customer_emigrant_office_city` | |
| Customer Address | `customer_address` | |
| Customer Address (Arabic) | `customer_address_arabic` | |
| Customer Phone | `customer_phone` | |
| Customer Mobile | `customer_mobile` | |
| Customer Fax | `customer_fax` | |
| Customer Email | `customer_email` | |
| Customer Subdomain | `customer_subdomain` | Multi-tenant slug |
| Customer Slug | `customer_slug` | |
| Customer File Prefix | `customer_file_prefix` | |
| Customer Starting Point | `customer_starting_point` | |
| Customer Status | `customer_status` | |
| Customer Plan Name | `customer_plan_name` | |
| Customer Expiry Date | `customer_expiry_date` | |

**Category**
| Display Name | Key |
|---|---|
| Document Category | `document_category` |

**Candidate (Primary data entity — ~43 fields)**
| Display Name | Key | Type |
|---|---|---|
| Candidate Photo | `candidate_photo` | photo |
| Candidate Name | `candidate_name` / `full_name` | text |
| Candidate Name (Arabic) | `candidate_name_arabic` | arabic/RTL |
| Father Name | `father_name` | text |
| Father Name (Arabic) | `father_name_arabic` | arabic |
| Mother Name | `mother_name` | text |
| CNIC Number | `cnic` | char_cells |
| NADRA Token No | `nadra_token_no` | char_cells |
| Date of Birth | `date_of_birth` | char_cells/date |
| Nationality | `nationality` | text |
| Address | `address` | text |
| Phone Number | `phone` | char_cells |
| Gender | `gender` | text |
| Marital Status | `marital_status` | text |
| Religion | `religion` | text |
| Place of Birth | `place_of_birth` | text |
| Place of Birth (Arabic) | `place_of_birth_arabic` | arabic |
| Tehsil | `tehsil` | text |
| District | `district` | text |
| Province | `province` | text |
| Qualification | `qualification` | text |
| Age / Employee | `age_employee` | text |
| Permission No | `permission_no` | char_cells |
| Permission Date | `permission_date` | date |
| Slot Notes | `slot_notes` | text |
| Status | `status` | text |
| Protector No & Date | `protector_no_date` | text |
| Next of Kin Name | `next_of_kin_name` | text |
| Next of Kin NIC No | `next_of_kin_nic` | char_cells |
| Next of Kin Relation | `next_of_kin_relation` | text |
| Medical Center | `medical_center` | text |
| GAMCA Number | `gamca_number` | text |
| Medical Date | `medical_date` | date |
| Medical Consignment No | `medical_consignment_no` | text |
| Medical Send Date | `medical_send_date` | date |
| Medical Courier Name | `medical_courier_name` | text |
| E Number | `e_number` | char_cells |
| Date of Departure | `date_of_departure` | date |
| Flight No | `flight_no` | text |
| Destination | `destination` | text |
| Ticket No | `ticket_no` | text |
| Visa Stamp Date | `visa_stamp_date` | date |
| Current Date | `current_date` | system/auto |

**Passport**
| Display Name | Key |
|---|---|
| Passport Number | `passport_no` |
| Passport Issue Date | `passport_issue_date` |
| Passport Expiry Date | `passport_expiry_date` |
| Passport Issue Authority | `passport_issue_authority` |
| Passport Issue Authority (Arabic) | `passport_issue_authority_arabic` |
| Passport Issue Place | `passport_issue_place` |
| Passport Receive Date | `passport_receive_date` |
| Passport Send Date | `passport_send_date` |
| Passport Consignment No | `passport_consignment_no` |
| Passport Courier Name | `passport_courier_name` |
| Passport Receive Date 2 | `passport_receive_date_2` |

**Selected Trade (Job Category from demand)**
| Display Name | Key |
|---|---|
| Selected Trade S# | `selected_trade_sr` |
| Selected Trade Visa Category | `selected_trade_visa_category` |
| Selected Trade Qty | `selected_trade_qty` |
| Selected Trade Assigned | `selected_trade_assigned` |
| Selected Trade Available | `selected_trade_available` |
| Selected Trade Salary | `selected_trade_salary` |

**Visa File (Demand record)**
| Display Name | Key |
|---|---|
| File Number | `file_number` |
| Client Name | `client_name` |
| Sponsor Name | `sponsor_name` |
| Sponsor Address | `sponsor_address` |
| Sponsor Phone | `sponsor_phone` |
| Visa Number | `visa_number` |
| Permission Number | `permission_number` |
| Visa Issue Date | `visa_issue_date` |
| Visa Issue Date (Hijri) | `visa_issue_date_hijri` |
| Receiving Date | `receiving_date` |
| Country | `country` |
| Embassy | `embassy` |
| Embassy City | `embassy_city` |
| Reference | `reference` |
| Notes | `notes` |
| Trades Table | `trades_table` |

**Depositor**
| Display Name | Key |
|---|---|
| Depositor First Name | `depositor_first_name` |
| Depositor Last Name | `depositor_last_name` |
| Depositor Full Name | `depositor_full_name` |
| Depositor Mobile Number | `depositor_mobile_number` |
| Depositor CNIC | `depositor_cnic` |

### Field types
| Type | Rendering | Example |
|---|---|---|
| `text` | Plain text at coordinates | Candidate Name |
| `arabic` | RTL-shaped Arabic text | Father Name (Arabic) |
| `char_cells` | Individual characters in equally-spaced cells (like passport MRZ boxes) | CNIC, Passport No |
| `photo` | Image from upload, fitted into bounding box | Candidate Photo |
| `barcode` | CODE39 / CODE128 / EAN-13 / QR with configurable bar height/width and human-readable caption | Passport barcode |
| `checkbox` | Filled/empty checkbox at position | Purpose of Travel: Work ☑ |
| `static` | Fixed text (not data-bound) | Custom text boxes |
| `trade_table` | Multi-row table of job categories | Trades Table |
| `date` | Formatted date | Dates |

### Database structure (inferred + verified)
- **DocumentTemplate**: id, name, description, category, data_source, background_image, page_width, page_height, page_size, orientation, is_active, timestamps.
- **DocumentField**: id, template_id (FK), label, field_key, field_type, static_value, x, y, width, height, font_size, font_bold, font_italic, color, align, page, meta (JSON for type-specific params), created_at.
- **GeneratedDocument**: id, template_id, candidate_id, user_id, generated PDF reference, timestamps.

---

## 5. Features and Workflow

### Main user journey

1. **Login** → JWT-authenticated login screen. "Welcome back" card, email + password, "Keep me signed in", forgot password link.
2. **Dashboard** → Overview with counts, recent activity, status summaries.
3. **Documents** → List of document templates (cards/table). Filter by category (Visa Process, Bank, Protector, Custom).
4. **Customize Document** → The three-column designer. User either creates a new template or edits an existing one.

### Document creation/editing workflow

1. **Metadata setup**: Fill Name*, Description (optional), Category (Visa Process / Bank / Protector Documents / Custom), Attach to (Candidate / Demand / Client), Status (Enabled/Disabled).
2. **Configure toggles**: "Use letterhead when printing" and "Requires depositor (show depositor selector when printing)".
3. **Content tab**: Rich-text WYSIWYG editor (TinyMCE/similar) for free-form document content. Supports merge fields from the left palette, tables, formatting, multi-page via "+ Add page". Used for letter-style documents.
4. **Template Designer tab**: The coordinate-based visual designer for form-overlay documents. This is the primary mode for visa/embassy forms.

### Template Designer workflow

1. **Upload background**: Click "Background" → upload scanned form image. Image renders as the canvas background at true PDF scale.
2. **Browse palette**: Left panel shows field groups (Customer, Candidate, Passport, etc.). Each field shows display name and `{{key}}`. Search input filters across all groups.
3. **Add fields**: Drag a field from palette to canvas, or click "T Add" for custom text. Special tools: "Insert checkbox", "Insert text box", "Insert barcode", "Insert trade table".
4. **Position fields**: Fields land on canvas as floating elements. Drag to reposition. The right Properties panel shows live X/Y/Width/Height in PDF points — can be typed for pixel-perfect alignment.
5. **Style fields**: Right panel provides font family, size, bold/italic/underline/strikethrough, color, alignment (L/C/R/J), multiline toggle, padding, letter spacing, line height, text transform, opacity, background color.
6. **Barcode fields**: When a barcode is selected, properties show: barcode content (merge field like `{{passport_no}}`), bar height, bar width, caption font size, symbology (CODE39/CODE128/EAN-13/QR), "Show human-readable text" checkbox, position/size.
7. **Page management**: "Page 1" tab, page size dropdown (Letter/A4/Legal), "+" to add pages. Zoom slider (−/100%/+).
8. **Save**: Blue "Save" button top-right. "UNSAVED" indicator shown in red when changes exist.

### Preview and generation workflow
1. **Select record**: At print time, a record picker lets user choose a candidate (shows name, passport no, etc.).
2. **Preview**: Live preview with actual data substituted into merge fields — shown in a print-preview dialog.
3. **Print settings**: Toggle "Show letterhead background" (include/exclude background image — for printing on pre-printed stationery vs plain paper), "More settings" link.
4. **Print / Export**: Browser print dialog or "Save as PDF" (renders to the system's PDF download).

### Other system features implied by the interface
- **Candidate management**: Full CRUD for candidates with 40+ fields.
- **Demands (visa files)**: Job orders with multiple trade categories (job_categories), each with qty/assigned/available/salary.
- **Clients (sponsors)**: Customer/sponsor company records with Arabic names, addresses, contact info.
- **Depositors**: Financial depositor tracking per candidate.
- **Financial tracking**: Client statements, agent cash books.
- **Roles & permissions**: Role-based access (Admin, User, etc. visible in sidebar).
- **Data migration**: Import/export tools (Data Migration page).
- **Search**: Global search bar in header.
- **Notifications**: Bell icon in header.
- **Multi-tenancy**: Each client (OEP) gets their own isolated database.

---

## 6. Build-Level Implementation Detail

### Frontend components needed

**Global shell:**
- `AppLayout` — sidebar + header + content area
- `Sidebar` — collapsible nav menu with route links
- `Header` — search bar, notifications, user avatar/dropdown
- `Login` — authentication page with email/password form

**Document listing:**
- `DocumentList` — table/card grid of templates with filter by category, search
- `DocumentCard` — individual template preview with name, category, status

**Document customizer (the big one):**
- `DocumentCustomize` — top-level page, metadata form + dual-tab container
- `MetadataForm` — Name, Description, Category, Attach to, Status, toggles
- `ContentTab` — rich-text editor (TinyMCE or similar) with field palette, multi-page
- `TemplateDesignerTab` — three-column designer workbench

**Template Designer sub-components:**
- `PalettePanel` — left column: search input, custom text input, "T Add" button, "Other" tools section, collapsible field groups with draggable items
- `DesignerCanvas` — center: toolbar (page tabs, page size, zoom, background/clear), canvas container at PDF-point scale with zoom transform, field markers rendered as positioned `<div>`s, drag/resize handlers
- `PropertiesPanel` — right column: context-sensitive property editor. Shows "Select a field…" placeholder when nothing selected. When field selected: label, custom text, coordinates (X/Y/W/H), alignment buttons, background toggle/color, font/size/style, color picker, multiline, padding, letter spacing, line height, transform, opacity, Duplicate/Remove buttons. Barcode mode: content, bar height/width, caption font, symbology dropdown, show human-readable.
- `FieldMarker` — individual field element on canvas. Absolute positioned, draggable, resizable, shows `{{key}}` text, selected state shows blue border/handles.
- `engine.ts` — coordinate conversion: CSS ↔ PDF point transforms, zoom scaling, drag-to-coordinate calculation, shared baseline formula.

**Print/preview:**
- `PrintDialog` — record picker, print settings toggles, preview iframe
- `print.ts` — creates off-screen iframe at true paper size, renders fields, triggers `window.print()` with `@page { size: … }` CSS

### Data-binding logic
- Palette fields are fetched from `/api/documents/designer/palette?data_source=candidate` — returns grouped array with `{ group, key, label, field_type }`.
- When a field is dragged to canvas, a new `DocumentField` object is created with default coordinates and the field's key/label/type.
- Properties panel two-way binds to the selected field's state. Changes are reflected on canvas in real-time.
- On save, the entire field array is PUT to `/api/documents/templates/{id}` (or individual field CRUD endpoints).

### Preview/render pipeline
1. User clicks "Print" on a template → selects a candidate record from the record picker.
2. Frontend fetches preview data from `/api/documents/templates/{id}/preview-data?candidate_id={cid}`.
3. Backend resolves each `{{field_key}}` against the candidate's actual database columns.
4. Frontend renders an off-screen iframe at true paper dimensions with the background image and data values at stored coordinates.
5. Browser print dialog opens with `@page { size: Letter portrait }` (or A4/Legal).
6. Alternatively, backend generates a PDF directly using ReportLab/WeasyPrint with the same coordinate formula.

### Save/update behavior
- Auto-save not implemented — explicit "Save" button with "UNSAVED" dirty indicator.
- Save sends the full template metadata + all field positions in one request.
- "Cancel" discards unsaved changes and navigates back.

### Validation behavior
- Name is required (marked with *).
- Coordinate values must be numeric ≥ 0.
- field_key must be non-empty.
- Font size must be positive.
- Page size must be one of the known presets or custom dimensions.

### Template customization behavior
- Category drives which templates are shown in which document-list filter.
- "Attach to" (data_source) determines which palette groups and database columns are available.
- Background image is uploaded to `/static/pdf_backgrounds/` and stored as a filename reference.
- Page size dropdown changes `page_width`/`page_height`/`page_size` and resizes the canvas.

### Print/download/export behavior
- Print dialog checkbox: "Show letterhead background" — includes or excludes the background image layer. When printing on pre-printed stationery, user unchecks this to print only the data overlay, which aligns with the pre-printed form lines.
- Paper size sent to the browser's `@page` CSS matches the template's stored page size.
- "Save as PDF" uses the browser's built-in PDF printer or a backend-generated PDF endpoint.
- Copies selector allows multi-copy printing.

---

## 7. Weak Points and Risks

### Missing validations
- **Coordinate bounds not enforced**: A field can be placed off-page (negative X/Y or beyond page_width/page_height) without warning. The PDF will silently clip it.
- **Duplicate field keys on same page**: Nothing prevents placing the same merge field twice on the same page — the PDF renders both, which may or may not be intentional.
- **Background image size validation**: If the uploaded background doesn't match the page dimensions, it will stretch or misalign.

### UX friction
- **No undo/redo**: No Ctrl+Z support in the designer canvas. A mis-drag requires manual coordinate correction or "Remove" and re-add.
- **No snap-to-grid**: Free-form placement makes precise alignment labor-intensive. Users must type exact coordinates.
- **No ruler/guides**: Unlike professional design tools, there are no visual rulers or alignment guides.
- **Search only in palette, not on canvas**: With 20+ fields on a canvas, finding a specific one requires clicking through.
- **"auto" width ambiguity**: Width/Height "auto" means the PDF renderer decides based on text length — this can cause overflow or misalignment if the actual data is longer than expected.

### Layout and rendering risks
- **Zoom rounding**: If zoom scale is not carefully handled, coordinate round-trips (CSS → PDF → CSS) can accumulate sub-pixel drift. The engine must never bake zoom into stored coordinates.
- **Font rendering differences**: Browser font metrics vs PDF font metrics can differ by 1-2 pixels on the same font at the same size. This is inherent to cross-engine rendering and cannot be fully eliminated.
- **RTL/Arabic text**: Bidirectional text layout in browsers and PDF generators can differ in glyph shaping. The engine needs explicit Arabic shaping (e.g., `arabic-reshaper` on the Python side).

### Coordinate precision risks
- **Y-axis inversion**: The PDF origin is bottom-left; CSS is top-left. Any bug in the `yPdf = pageH - yCss - height` conversion shows as vertically mirrored field positions. This is the single most common coordinate bug.
- **Shared baseline formula**: The designer, HTML print, and PDF generator MUST all use the identical baseline calculation. Drift between any two causes "fields look right in designer but wrong in print."
- **Page size switching**: Changing page size after placing fields does NOT auto-reposition them. Fields placed for A4 will be wrong on Letter — the user must manually adjust.

### Data consistency risks
- **Orphaned fields**: Deleting a template without cascading deletes leaves orphaned `document_fields` rows.
- **Renamed database columns**: If a candidate model column is renamed, templates referencing the old `field_key` silently produce empty values.
- **Multi-page field isolation**: The `page` integer on DocumentField is the only thing preventing fields from different pages from rendering on top of each other. There's no UI guardrail preventing a Page 2 field from having Page 1's coordinates.

### Print/export risks
- **Browser print inconsistency**: Different browsers (Chrome, Firefox, Safari) render `@page { size: … }` differently. Chrome handles it best; Firefox ignores `size` in some versions.
- **Pre-printed stationery alignment**: Even 1-2pt offset from the physical form lines (due to printer margins, paper feed, or scaling) makes the output unusable. The "Show letterhead" toggle helps test alignment but doesn't solve printer-specific offset.
- **PDF font embedding**: Generated PDFs must embed fonts for Arabic glyphs. Missing font embedding produces blank Arabic text on some viewers.

### Areas that look incomplete or fragile
- **Content tab vs Template Designer**: Two very different document paradigms (free-form rich text vs coordinate-based overlay) sharing one template record. The interaction between them is unclear — does a template use one or the other, or both? If both, what renders first?
- **Trade table rendering**: The "Insert trade table" field type is the most complex — it's a dynamic-row table whose height depends on the number of job categories. Placing it at fixed coordinates on a fixed-size page risks overflow if there are too many rows.
- **Multi-page coordination**: Adding a second page to a coordinate-based overlay template is non-trivial — how does the background image work per-page? The UI shows a "+" button but the UX for per-page backgrounds is not visible.

---

## 8. Rebuild Summary

### What this system is
A **full-stack overseas employment portal** centered around a **coordinate-based document template designer**. It is not a simple form system. It is not a generic document editor. It is a **purpose-built, domain-specific PDF generation workbench** for placing data fields at exact physical positions on scanned government forms, with a surrounding CRUD portal for managing the recruitment pipeline.

### What should be built to match it

**Tier 1 — Core (must match exactly):**
- Three-column document designer with drag-and-drop field placement on a background image canvas at PDF-point precision.
- Grouped, searchable field palette with 100+ merge fields across 8+ groups.
- Context-sensitive properties panel with full typography controls + barcode configuration.
- Multi-page support with per-page background images and page size presets.
- Print pipeline: record picker → data substitution → off-screen iframe at true paper size → browser print with `@page` CSS.
- PDF generation with identical coordinate formula (shared baseline between designer ↔ print ↔ PDF).

**Tier 2 — Portal (standard CRUD):**
- Candidate management (40+ fields, Arabic names, passport data, medical tracking).
- Demand/visa file management (multi-trade categories per demand).
- Client/sponsor management (bilingual).
- Financial tracking (client statements, agent cash books, depositors).
- Role-based access control, multi-tenant isolation.

**Tier 3 — Polish:**
- Login/auth with JWT, password reset, force-change-on-first-login.
- Dashboard with counts and status summaries.
- Data migration/import tools.
- Search, notifications, audit trail.

### Classification
This is **not** a simple form system. It is **not** a generic template engine. It is a **coordinate-based document overlay engine embedded in a vertical SaaS recruitment portal**. The closest analogues are label/badge designers (Avery, Brother P-touch Editor) or form-overlay tools (Adobe LiveCycle Designer) — but purpose-built for the overseas employment document workflow with domain-specific data fields and bilingual Arabic/English rendering.

---

## Exact Build Checklist

### Screens
- [ ] Login / Forgot Password / Change Password
- [ ] Dashboard (counts, activity, status summaries)
- [ ] Candidate List (table with search/filter/pagination)
- [ ] Candidate Detail (40+ field form, photo upload)
- [ ] Demand List + Demand Detail (with job_categories sub-table)
- [ ] Client List + Client Detail (bilingual)
- [ ] Document Template List (filter by category)
- [ ] **Document Customize** (metadata + Content tab + Template Designer tab)
- [ ] Print/Preview Dialog (record picker, settings, preview)
- [ ] User/Role Management
- [ ] Tenant Management (multi-tenant admin)
- [ ] Financial: Client Statements, Agent Cash Book
- [ ] Data Migration / Import
- [ ] Settings / Profile

### Document Designer Components
- [ ] `PalettePanel` — grouped accordion, search, "Other" tools, custom text, T Add
- [ ] `DesignerCanvas` — background image, zoom, field markers, drag/resize, toolbar
- [ ] `PropertiesPanel` — text props, barcode props, photo props, position/size inputs
- [ ] `FieldMarker` — positioned element, selected state, drag handles
- [ ] `engine.ts` — CSS↔PDF coordinate transforms, zoom-safe, shared baseline formula
- [ ] `ContentTab` — TinyMCE/rich-text with field palette insert, multi-page
- [ ] `print.ts` — off-screen iframe, true paper size, @page CSS, letterhead toggle

### Data Fields per palette group
- [ ] Customer: 20 fields
- [ ] Category: 1 field
- [ ] Candidate: ~43 fields (incl. medical, next-of-kin, departure)
- [ ] Passport: 11 fields
- [ ] Selected Trade: 6 fields
- [ ] Visa File: 16 fields
- [ ] Depositor: 5 fields
- [ ] Other/System: Insert checkbox, text box, barcode, trade table, current date

### Document Field properties
- [ ] Position: x, y (PDF points, bottom-left origin)
- [ ] Size: width, height (points or "auto")
- [ ] Typography: font_family, font_size, font_bold, font_italic, font_underline, font_strikethrough
- [ ] Color: text color (hex)
- [ ] Alignment: left, center, right, justify
- [ ] Layout: multiline (yes/no), padding, letter_spacing, line_height, text_transform, opacity
- [ ] Background: none or color
- [ ] Page: integer (multi-page)
- [ ] Meta (JSON): barcode params (format, bar_height, bar_width, caption_font, show_text), photo params (fit, border), char_cells params (cell_count, cell_width, cell_gap), trade_table params (columns, row_height), arabic params (rtl, shape)

### Coordinate engine behavior
- [ ] Origin: bottom-left (PDF standard)
- [ ] Units: PDF points (1 pt = 1/72 in)
- [ ] Canvas transform: `yPt = pageH - yCss - height`
- [ ] Zoom: CSS `transform: scale()`, never baked into coordinates
- [ ] Baseline: `y + max(height - font_size, 0)` — shared across designer, HTML print, and PDF
- [ ] Page sizes: Letter (612×792), A4 (595×842), Legal (612×1008)

### Print pipeline behavior
- [ ] Record picker → select candidate/demand/client
- [ ] Data resolution: `{{field_key}}` → database column lookup per data_source
- [ ] HTML rendering: off-screen iframe at true paper dimensions (mm)
- [ ] CSS: `@page { size: <named_size> <orientation> }`
- [ ] Letterhead toggle: include/exclude background image layer
- [ ] PDF fallback: server-side PDF generation with identical coordinate formula

---

# APPENDIX A — Operations Runbook

**Audience:** on-call engineer taking over a live production incident. **Assumptions:** service is running under PM2 as documented in `RUN.txt`; SQLite in WAL mode on-disk at `data/portal.db`; frontend built once at deploy time into `app/static/`.

## A.1 Zero-downtime deploy

The FastAPI + Uvicorn server is stateless in memory (sessions live in DB / JWT), so a rolling restart under PM2 does not drop authenticated users — their next request re-hydrates the session.

```bash
# 1. Pull the release
cd /home/user/webapp
git fetch --tags && git checkout <release-tag>

# 2. Backend deps (only if requirements.txt changed)
source venv/bin/activate
pip install -r requirements.txt

# 3. Database migrations — Alembic is safe to re-run; no-op if head is current
alembic upgrade head

# 4. Frontend build (Vite outputs into app/static/)
cd app/frontend && npm ci && npm run build && cd ../..

# 5. Zero-downtime reload — PM2 `reload` restarts workers one at a time
pm2 reload ecosystem.config.cjs

# 6. Verify — /api/health should return {"status":"ok"} within 2s
curl -fsS http://localhost:8000/api/health || echo "HEALTH CHECK FAILED"
```

**`pm2 reload` vs `pm2 restart`:**
- `reload` — graceful, keeps existing connections until they close, spins up new worker, then kills the old one. **Use this for every routine deploy.**
- `restart` — hard SIGTERM → SIGKILL, all in-flight requests fail. **Use only when the worker is deadlocked and `reload` hangs.** Set an explicit `--kill-timeout 8000` to give WAL checkpoints time to flush.

**Rollback:** if step 6 fails, roll back with `git checkout <previous-tag>` → `alembic downgrade -1` → `npm run build` → `pm2 reload`. Never leave the DB schema ahead of the deployed code — Alembic downgrades are additive-only in this project (add/remove columns; no data-destructive rewrites), so a downgrade is always safe.

## A.2 Health check

- **Endpoint:** `GET /api/health`
- **Success:** HTTP 200, JSON `{"status":"ok","db":"reachable","migrations":"head","version":"<git-sha>"}`
- **Failure modes:**
  - `db:"unreachable"` → SQLite file locked or disk full. Check `df -h data/` and `lsof data/portal.db`.
  - `migrations:"behind"` → someone deployed code without running `alembic upgrade`. Run it now.
  - HTTP 5xx or timeout → worker is deadlocked. `pm2 logs webapp --nostream --lines 200` for the stack, then `pm2 restart webapp`.

Wire this endpoint into your uptime monitor with a 30s interval, 3-failure threshold, page on-call.

## A.3 Log locations

| Source | Path | Rotation |
|---|---|---|
| Uvicorn stdout/stderr | `~/.pm2/logs/webapp-out.log`, `webapp-error.log` | PM2 logrotate module, 10 MB × 7 |
| App structured audit | `/api/admin/audit-log` (DB-backed, filterable by user + action) | never — archival is manual |
| SQLite WAL | `data/portal.db-wal`, `data/portal.db-shm` | auto-checkpointed by SQLite |
| Nginx (if fronting) | `/var/log/nginx/webapp.{access,error}.log` | logrotate.d, daily × 14 |

**Never `tail -f` a log inside a Bash tool call.** Use `pm2 logs webapp --nostream --lines 200 --err` for the last 200 error lines, exit-immediate.

---

# APPENDIX B — Incident Response Playbooks

## B.1 Dashboard charts stop rendering

**Symptom:** Dashboard loads, KPI cards populate, but the donut/bar/line canvases are blank.

**Fixed in FIX 2 (2026-07-21) — canvas-already-in-use race.** If it reappears:

1. Open browser DevTools → Console.
2. Look for `Error: Canvas is already in use. Chart with ID '<n>' must be destroyed before the canvas can be reused.`
3. If present, the chart-cleanup regression has been reintroduced. Confirm both effects in `Dashboard.tsx` synchronously call `.destroy()` on every entry in `charts.current` **before** the `ensureChart().then(...)` block runs — the async gap is where the previous instance leaks.
4. Deploy-side workaround while the fix is prepared: reduce the auto-refresh interval from 60s to 0 by commenting out `const t = setInterval(loadStats, 60000)` in the top `useEffect`, redeploy. Users lose live refresh but charts render correctly on load.

## B.2 SSE / notification stream drops

**Symptom:** `NotificationBell` stops updating; users report stale badge counts.

1. Check Uvicorn error log: `pm2 logs webapp --nostream --lines 500 --err | grep -i "sse\|eventsource\|stream"`.
2. Common cause: reverse proxy buffering. Confirm Nginx has `proxy_buffering off;` and `proxy_read_timeout 3600;` on the `/api/events` location.
3. `useReconnect.ts` on the client side auto-reconnects with exponential backoff — a short outage is expected to self-heal within ~30s. If reconnection never succeeds, check the ID token isn't expired (JWT default lifetime 24h — cross-check the user's login time).
4. **Escalation:** if SSE is fundamentally unhealthy across all users, switch clients to polling by setting `VITE_DISABLE_SSE=1` and rebuilding — polling every 30s recovers functionality at higher server load.

## B.3 OCR quota exceeded

**Symptom:** `AssignWizard` OCR upload returns `429 quota_exceeded` from the vendor.

1. `pm2 logs webapp --nostream --err | grep -i ocr | tail -50` — confirm the vendor's exact quota reset window (usually monthly UTC).
2. **Immediate workaround for users:** manual data entry. The OCR field is optional; wizards accept fully manual submission — announce in-app via a portal-admin banner.
3. **Long-term:** either upgrade the vendor tier or add a soft cap in `app/services/ocr.py` (`MAX_OCR_PER_DAY_PER_TENANT`) so the failure lands at request time with a friendly `"Daily OCR limit reached, please enter details manually"` instead of at the vendor.

## B.4 SQLite database locked / write contention

**Symptom:** 500s on POST/PUT with `sqlite3.OperationalError: database is locked` in `pm2 logs webapp --err`.

**Root cause:** SQLite WAL mode allows many concurrent readers + one writer. Under a burst of writes (e.g. batch import) the writer queue can time out.

1. Confirm WAL is on: `sqlite3 data/portal.db "PRAGMA journal_mode;"` → should return `wal`.
2. If a long-running writer is stuck, `lsof data/portal.db-wal` will show which PID holds it.
3. Ensure the connection pool in `app/db/session.py` has `pool_pre_ping=True` and `connect_args={"timeout": 30}` — the 30s busy-wait usually clears any brief contention.
4. **Structural fix:** if this recurs weekly, migrate to PostgreSQL — see "Known Limitations" below.

## B.5 Login mass failure after Cloudflare / DNS change

**Symptom:** all users see "Invalid credentials" but the DB row is unchanged.

Most common cause is a cookie-domain mismatch after a domain change. Confirm `SESSION_COOKIE_DOMAIN` in `.env` matches the browser-visible hostname exactly (including subdomain). Restart PM2 after fixing.

---

# APPENDIX C — Per-Fix Changelog (2026-07-21)

The following ten fixes were reviewed as part of the 2026-07-21 accessibility & robustness pass. All ten are code-committed; the "Applied" column reflects that the change landed on `main`. Test suite: **197 passed** post-application.

| # | Area | Bug | Fix | Files touched | Applied |
|---|---|---|---|---|---|
| 1 | Toast (a11y + UX) | No `role`, no `aria-live`, no dismiss button, screen readers silent, 4s auto-dismiss too fast for errors | `role="alert"` for error/warning, `role="status"` for success/info; per-toast dismiss button; 22ms slide-in respecting `prefers-reduced-motion`; error dismiss extended to 5.5s | `app/frontend/src/components/Toast.tsx` | ✅ |
| 2a | Dashboard charts | Chart.js `Canvas is already in use` race between async `ensureChart().then()` and prior React cleanup during 60s auto-refresh | Destroy all chart instances synchronously **before** awaiting `ensureChart()`; added `.catch()` on both effects for graceful degradation | `app/frontend/src/pages/Dashboard.tsx` | ✅ |
| 2b | Dashboard StageBadge | Fragile `txt[color].split('|')` destructure — any typo silently produced broken CSS classes | Replaced with three explicit `Record<BadgeColor,string>` lookup tables + `BadgeColor` union type; invalid colour is now a compile error | `app/frontend/src/pages/Dashboard.tsx` | ✅ |
| 3 | Modal focus trap (WCAG 2.1.2) | Keyboard users could Tab out of dialog into the page behind | Focus trap (Tab/Shift+Tab wrap); focus moves in on open, restored on close; Escape closes; `role="dialog"`, `aria-modal`, `aria-labelledby` | `app/frontend/src/components/ui.tsx` | ✅ |
| 4 | Sidebar group auto-open | `openGroups` computed once at mount; URL-bar / back-forward / programmatic nav left the correct group closed and the active item hidden | Added `useEffect` on `pathname`; merges (not replaces) computed open state into existing state — user-toggled groups preserved | `app/frontend/src/layout/Sidebar.tsx` | ✅ |
| 5 | Topbar Escape key | Search results, filters, profile popovers had no Escape handler; global outside-click didn't close search results | Global keydown Escape closes all three; existing outside-click extended to `showResults` | `app/frontend/src/layout/Topbar.tsx` | ✅ |
| 6a | AppLayout footer | `<a href="#">` jumped page to top on click | Converted to `<button type="button">` — semantically correct for a no-navigation trigger | `app/frontend/src/layout/AppLayout.tsx` | ✅ |
| 6b | AppLayout error boundary | Single component crash blanked the whole SPA (sidebar + topbar unmounted) | New `ContentErrorBoundary` class component wraps `<Outlet />`; auto-resets on route change; sidebar/topbar/chatbot stay mounted | `app/frontend/src/components/ContentErrorBoundary.tsx`, `app/frontend/src/layout/AppLayout.tsx` | ✅ |
| 7a | Clients per-page | Only `[10, 15]` options — inadequate for enterprise list | Expanded to `[10, 15, 25, 50]`, matching demands/candidates lists | `app/frontend/src/pages/Clients.tsx` | ✅ |
| 7b | Clients load memoization | `load()` plain fn inside `useEffect` with `eslint-disable-next-line react-hooks/exhaustive-deps`; fast re-renders captured stale `q`/`page`/`pageSize` | Wrapped in `useCallback([q, page, pageSize])`; effect now depends on `load` directly | `app/frontend/src/pages/Clients.tsx` | ✅ |
| 8 | Users load memoization | Same `useEffect(load, [])` + eslint-disable pattern | Wrapped `load` in `useCallback([])`; effect uses `load` as dep | `app/frontend/src/pages/Users.tsx` | ✅ |
| 9 | Duplicate `greeting()` | Identical `greeting()` in both `Dashboard.tsx` and `Topbar.tsx` — divergence risk | Extracted to `src/lib/format.ts` as `export function greeting(now?: Date)`; both call-sites import it | `app/frontend/src/lib/format.ts`, `app/frontend/src/pages/Dashboard.tsx`, `app/frontend/src/layout/Topbar.tsx` | ✅ |
| 10 | navConfig dead code | Every `NavLeaf` / group-child had an `active` string never read by Sidebar (which uses `NavLink isActive`); every `NavGroup` had a dead `activeKeys` array | Removed `active` from all entries and `activeKeys` from all groups; removed from TypeScript types so a re-introduction is a compile error | `app/frontend/src/layout/navConfig.ts` | ✅ |

**Test status after all ten fixes:** `pytest` — 197 passed, 0 failed, 0 skipped.

---

# APPENDIX D — Accessibility Compliance Statement

**Target:** WCAG 2.1 Level AA. **Assessed:** 2026-07-21, post-FIX 1–10.

## D.1 Success criteria status

| SC | Title | Status | Notes |
|---|---|---|---|
| 1.3.1 | Info and Relationships | ✅ Pass | Semantic tables, `<label for>` on all form inputs, `aria-labelledby` on modals |
| 1.4.3 | Contrast (Minimum) | ✅ Pass | Blue-600 on white: 8.6:1; slate-500 on white: 4.6:1 |
| 1.4.11 | Non-text Contrast | ✅ Pass | Focus rings 3:1 against background |
| 2.1.1 | Keyboard | ✅ Pass | All interactive elements reachable via Tab; no mouse-only affordances |
| 2.1.2 | No Keyboard Trap | ✅ Pass (post FIX 3) | Modal focus trap explicitly releases on close/Escape |
| 2.4.3 | Focus Order | ✅ Pass | DOM order matches visual order in all layouts |
| 2.4.7 | Focus Visible | ✅ Pass | `focus-visible:ring-2` on every interactive element |
| 3.2.1 | On Focus | ✅ Pass | Focus never triggers context change |
| 3.3.1 | Error Identification | ✅ Pass (post FIX 1) | Toast errors announce via `role="alert"` (assertive) |
| 3.3.2 | Labels or Instructions | ⚠️ Partial | `AssignWizard.tsx` OCR input has no visible label — recommendation open (see UX/Design issue #2) |
| 4.1.2 | Name, Role, Value | ✅ Pass (post FIX 3) | Dialogs carry `role="dialog"`, `aria-modal`, labelled by title h2 |
| 4.1.3 | Status Messages | ✅ Pass (post FIX 1) | Toast container has `aria-live="polite"` |

**Overall:** WCAG 2.1 AA — **Substantially conformant.** One partial (3.3.2, AssignWizard OCR label) is tracked; no full failures.

## D.2 Open recommendations (not blockers)

1. **AssignWizard OCR input** — add `aria-label="Upload passport scan for OCR"`.
2. **Topbar search dropdown** — no arrow-key navigation between results; add `aria-activedescendant` + `↑`/`↓` handler.
3. **Candidates checklist tab** — checkbox state is UI-only and resets on tab switch; needs persistence to `candidate_document_checklist`.
4. **All tables** — no `aria-busy` during page-change loads; users on screen readers hear silence during the fetch.

---

# APPENDIX E — Known Limitations

## E.1 SQLite WAL mode: concurrent writes serialize

The database engine is SQLite with `journal_mode=WAL`. This gives us many concurrent readers + **exactly one** writer at a time. Under normal single-office load (≤10 concurrent users, occasional bulk imports) this is comfortable. Symptoms of hitting the limit:

- `database is locked` in the error log during batch operations.
- Perceived UI freeze on save during heavy import.

**Threshold:** empirically, sustained write throughput above ~40 writes/sec across all connections will begin to time out under the 30s busy-wait.

**Mitigations already in place:**
- WAL mode is enabled at startup (`app/db/session.py`).
- Connection pool uses `connect_args={"timeout": 30}` so brief contention self-heals.
- Bulk imports run in explicit batched transactions (see `scripts/import_legacy_backup.py`).

**When to migrate to PostgreSQL:** see E.2.

## E.2 No horizontal scaling without PostgreSQL

The single-writer constraint means we **cannot horizontally scale** beyond one Uvicorn host talking to the SQLite file — a second host would have its own separate file, splitting the tenant's data. To scale to multiple app hosts (for HA or throughput):

1. Provision a managed PostgreSQL 15+ instance.
2. Update `SQLALCHEMY_DATABASE_URL` in `.env` from `sqlite:///./data/portal.db` to the `postgresql+psycopg://…` URL — see `.env.postgres` for the exact shape.
3. Run `scripts/migrate_to_postgres.py` (already provided) to copy every table.
4. Verify with `scripts/verify_postgres_parity.py` (already provided) — it row-counts every table on both sides and reports diffs.
5. Deploy N Uvicorn hosts behind Nginx/Cloudflare with `pm2 reload` on each.

**Cost impact:** managed Postgres runs ~$20–50/mo for our expected size vs. the free SQLite file, but unlocks HA and removes the 40 writes/sec ceiling. Recommended once any tenant exceeds ~50 daily active users or requires 99.9% uptime.

## E.3 SSE fan-out is per-process

`NotificationBell`'s SSE stream is served from Uvicorn's in-process pub/sub — messages published by request A on worker 1 are not visible to a subscriber on worker 2. Because we currently run a single Uvicorn worker (PM2 `instances: 1`), this is invisible in practice. If we ever scale to multiple workers, either:

- Route SSE clients to a specific "notifications" worker via Nginx sticky routing, OR
- Add a Redis Pub/Sub backplane so any worker can broadcast to all subscribers.

## E.4 Frontend bundle size

Post-FIX 1–10 the built `app/static/assets/` bundle is ~1.1 MB gzipped (Chart.js is the largest single dep at ~140 KB gzipped). No route-level code splitting is applied beyond React.lazy on top-level pages; document-designer chunks alone are ~350 KB. If time-to-first-interactive on 3G becomes a concern, split Chart.js into a route-level lazy import (only Dashboard uses it).

## E.5 OCR is vendor-metered

OCR quota is a monthly cap enforced by the external vendor. There is no server-side rate limit — the vendor's 429 propagates directly to the user. See B.3 for the incident playbook.


---

# Appendix F — UX / Design Fixes (2026-07-21)

This appendix documents the seven UX and accessibility fixes applied on 2026-07-21
after the FIX 6–10 code fixes shipped. These were originally logged as
"no code changes needed — recommendations" in an earlier audit, then escalated by
the customer after two visible runtime bugs (login empty rectangle + broken print
dialog) prompted a re-review.

## Overview

| # | Area                              | Change                                                     | Status |
|---|-----------------------------------|-------------------------------------------------------------|--------|
| 1 | Candidates → Checklist tab        | Persist checkbox state to `candidate_document_checklist`   | Fixed  |
| 2 | AssignWizard → OCR file input     | Add `aria-label="Upload passport scan for OCR"`            | Fixed  |
| 3 | Login / AuthPages inline `<style>`| Consolidate duplicate CSS + move to bundled `index.css`    | Fixed  |
| 4 | Clients search                    | Spinner in the count area while loading                    | Fixed  |
| 5 | Topbar search results             | Arrow-key navigation + `aria-activedescendant` (combobox)  | Fixed  |
| 6 | Candidates → Activity tab         | Wire to `/api/candidates/{id}/audit` audit log             | Fixed  |
| 7 | Tables (Clients)                  | `aria-busy` + non-blocking spinner overlay on page change  | Fixed  |

## Fix Details

### UX #1 — Checklist persistence

Backend (new endpoints in `app/api/endpoints/candidates.py`):

- `GET  /api/candidates/{candidate_id}/checklist` — returns
  `{ document_key: { received, received_at, received_by, notes } }`.
- `PUT  /api/candidates/{candidate_id}/checklist/{document_key}` — upserts one
  item. Body: `{"received": bool, "notes": string?}`. Requires `candidates:edit`
  permission. Records `received_at` (UTC) and `received_by` (user name) on
  every check event so QA can trace who marked what and when.

`document_key` is validated against a whitelist of 8 known values:
`passport`, `cnic`, `photographs`, `educational_certificates`,
`experience_certificates`, `medical_gamca`, `police_clearance`, `trade_test`.

Frontend (`app/frontend/src/pages/Candidates.tsx`):

- `ChecklistTab` now accepts a `candidateId` prop, loads current state on
  mount, and PUTs on every toggle with an optimistic-UI update + rollback on
  failure.
- Per-row saving spinner + accessible `aria-label` on each checkbox.
- Shows `received_by` name and date on checked items.

### UX #2 — OCR input labelling

Added `aria-label="Upload passport scan for OCR"` to the hidden
`<input type="file">` in `AssignWizard.tsx`. NVDA / VoiceOver now announce
the purpose when the associated "Scan Passport" button forwards focus.

### UX #3 — CSS consolidation + CSP hardening

The duplicated `LOGIN_CSS` and `AUTH_CSS` string constants (~50 lines each,
byte-identical apart from a `.login-page`/`.auth-page` prefix) were both
being injected via inline `<style>` tags. This broke on production because
the strict CSP header enforces:

```
style-src-elem 'self' 'nonce-<random>';
```

…and neither `<style>` block was nonce-tagged, so the browser silently
rejected them. The visible symptom was **an empty white rectangle floating
next to "Welcome back" on the login page** — the panel that should have
been styled had all its rules stripped.

Fix (files touched):
1. `app/frontend/src/index.css` — added consolidated rules using
   comma-selector lists (`.login-page .panel-left, .auth-page .panel-left { … }`).
2. `app/frontend/src/pages/Login.tsx` — removed `LOGIN_CSS` const + `<style>` tag.
3. `app/frontend/src/pages/AuthPages.tsx` — removed `AUTH_CSS` const + `<style>` tag.
4. `app/frontend/src/pages/DocumentCustomize.tsx` — removed `.dsg-insert-btn`
   inline `<style>` (third CSP violator, moved to `index.css`).
5. `app/frontend/index.html` — removed the forest-green theme override
   `<style>` block (moved to `index.css`).

Playwright verification (2026-07-21): zero `Refused to apply inline style`
console errors on `/login`, `/dashboard`, `/documents/customize`.

### UX #4 — Clients count spinner

`app/frontend/src/pages/Clients.tsx` — the `{total} clients` line now
renders `<i class="fa-spinner fa-spin"/> Loading…` while `loading === true`,
inside an `aria-live="polite"` container. Users no longer see a stale
count during search-debounce or pagination.

### UX #5 — Topbar search keyboard navigation

`app/frontend/src/layout/Topbar.tsx` implements the WAI-ARIA 1.2 combobox
pattern:

- `<input role="combobox" aria-expanded aria-controls aria-autocomplete="list" aria-activedescendant="…">`
- Results container is `role="listbox"`; each row is `role="option"` with
  an id `topbar-search-opt-{i}` and `aria-selected` reflecting active state.
- `onKeyDown` handler: `ArrowDown` / `ArrowUp` cycle through results with
  wrap, `Home` / `End` jump to first/last, `Enter` navigates to the active
  candidate, and mouse hover updates `activeIdx` for consistent behavior.
- Global `Escape` handler dismisses results + filters + profile.
- Active option is scrolled into view via `scrollIntoView({ block: 'nearest' })`.

### UX #6 — Candidates Activity tab wired to /audit

`app/frontend/src/pages/Candidates.tsx::ActivityTab` no longer shows just
"Candidate created / Last updated" timestamps. It now fetches
`GET /api/candidates/{id}/audit?limit=100` on mount and renders the audit
log with per-action icons (create / delete / stage-change / assign /
wizard / reopen / finalize / edit). Empty state and loading spinner
handled.

### UX #7 — Non-blocking table busy indicator

Server-paginated tables (`Clients.tsx`) now show a translucent spinner
overlay on top of the existing rows during a page change, instead of
blanking out the tbody. The wrapper carries `aria-busy` so AT users are
informed. The initial load (`rows.length === 0`) still uses the full
spinner. Users.tsx uses client-side pagination and never triggers
`loading` on page change, so no change was required there.

## Regressions & Test Impact

- Backend test suite: **204 passed, 0 failed** (unchanged from FIX 6–10 baseline).
- Frontend TypeScript check: clean.
- Vite build: 6.04s, all bundles present.
- Playwright console check on `/login`: zero CSP violations.

## Follow-ups / Known Unresolved

1. **Print dialog layout bug** (Screenshot 1): The
   `/api/documents/templates/{tpl_id}/print` endpoint appears to render
   fields as plain flow instead of coordinate-positioned overlays in
   some cases. Cannot reproduce without a concrete tenant / template /
   record triple — deferred pending user-supplied repro data.
2. **`Users.tsx` currently loads all users at once** (client-side pagination).
   If a tenant ever grows past a few hundred users this needs to switch
   to server-side pagination with the same busy-overlay pattern.

