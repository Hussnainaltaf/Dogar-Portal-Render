// ---------------------------------------------------------------------------
// AssignWizard — 7-step "Assign Candidate to <trade>" wizard (full v2 parity).
//
// AUDIT FIX (2026-07-07, FIX 2 / Option A):
//   Previously this component had only 5 steps and never touched the backend
//   candidate-wizard API — it POSTed straight to /candidates/ then /assign.
//   The backend (app/core/workflow.py WIZARD_STEPS) declares 7 steps and ships
//   a full resumable-draft API (/candidates/wizard/*). Two data domains that
//   the Candidate model actually persists — Medical & Protector and Visa &
//   Travel — had NO editing UI at all.
//
//   This rewrite:
//     • Adds the 2 missing steps (Medical & Protector, Visa & Travel) so the
//       frontend step list now matches the backend's 7 WIZARD_STEPS exactly.
//     • Wires CREATE mode through the draft/resume API:
//         POST   /candidates/wizard                    (open draft on the trade)
//         PATCH  /candidates/wizard/{id}/step/{n}       (persist each step)
//         POST   /candidates/wizard/{id}/finalize       (create candidate + auto-assign)
//       finalize auto-assigns to the trade and enforces slot capacity, so the
//       separate /demands/trades/{id}/assign call is no longer needed.
//     • EDIT mode keeps the simpler PUT /candidates/{id} path (no draft), now
//       covering the new medical/visa fields too.
//
// Steps: Personal Info → Identification (OCR) → Employment → Next of Kin
//        → Medical & Protector → Visa & Travel → Charge Summary
// ---------------------------------------------------------------------------
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, apiError } from '@/lib/api'
import { useToast } from '@/components/Toast'
import { ImageUpload } from '@/components/ImageUpload'
import { useDialogKeys } from '@/hooks/useDialogKeys'

export interface AssignWizardProps {
  /** When set → CREATE mode: create candidate + assign to this trade slot.
   *  NOTE: tradeId IS the backend job_category_id (JobCategory.id). */
  tradeId?: number | null
  tradeName?: string
  salary?: number
  /** Permission prefix for the Employment step (e.g. "DTC/LHR/8201/"). */
  permissionPrefix?: string
  /** Salary currency label for the Charge Summary (e.g. "SAR"). */
  currency?: string
  demandId?: number
  /** When set → EDIT mode: load + PUT this candidate (no re-assignment). */
  editId?: number | null
  onClose: () => void
  /** Called after a successful save so the parent can reload trades/summary. */
  onSaved: () => void
}

// Labels MUST match app/core/workflow.py WIZARD_STEPS (7 steps).
const STEPS = [
  'Personal Info',        // 1  personal_info
  'Identification',       // 2  identification
  'Employment',           // 3  employment
  'Next of Kin',          // 4  next_of_kin
  'Medical & Protector',  // 5  medical_protector
  'Visa & Travel',        // 6  visa_travel
  'Charge Summary',       // 7  charge_summary
]
const TOTAL = STEPS.length

// Which Candidate columns belong to each step (1-indexed). Used to slice the
// flat form into per-step payloads for PATCH /candidates/wizard/{id}/step/{n}.
// The backend finalize() flat-merges every slice, so keys are Candidate columns.
const STEP_FIELDS: Record<number, string[]> = {
  1: ['full_name', 'name_arabic', 'mother_name', 'father_name', 'father_name_arabic',
      'gender', 'marital_status', 'religion', 'date_of_birth', 'place_of_birth',
      'place_of_birth_arabic', 'nationality', 'address', 'phone', 'email', 'tehsil',
      'district', 'province'],
  2: ['passport_no', 'passport_issue_date', 'passport_expiry_date', 'issuing_authority',
      'issuing_authority_arabic', 'passport_issue_place', 'cnic', 'nadra_token_no'],
  3: ['permission_no', 'permission_date', 'qualification', 'profession', 'age_employee'],
  4: ['next_of_kin_name', 'next_of_kin_nic', 'next_of_kin_relation'],
  5: ['protector_no', 'protector_date', 'medical_center', 'gamca_number', 'medical_date',
      'medical_consignment_no', 'medical_send_date', 'medical_courier_name'],
  6: ['e_number', 'visa_stamp_date', 'date_of_departure', 'flight_no', 'destination', 'ticket_no'],
  7: ['salary', 'price', 'ticket_included', 'accommodation_allowance', 'food_allowance', 'slot_notes'],
}

type Form = Record<string, string>

const EMPTY: Form = {
  // Step 1 — Personal Info
  full_name: '', name_arabic: '', mother_name: '', father_name: '', father_name_arabic: '',
  gender: '', marital_status: '', religion: 'Islam', date_of_birth: '', place_of_birth: '',
  place_of_birth_arabic: '', nationality: 'PAKISTANI', address: '', phone: '', email: '',
  tehsil: '', district: '', province: '',
  // Step 2 — Identification
  passport_no: '', passport_issue_date: '', passport_expiry_date: '', issuing_authority: 'PAKISTAN',
  issuing_authority_arabic: '', passport_issue_place: '', cnic: '', nadra_token_no: '',
  // Step 3 — Employment
  permission_no: '', permission_date: '', qualification: 'N/a', profession: '', age_employee: '',
  // Step 4 — Next of Kin
  next_of_kin_name: '', next_of_kin_nic: '', next_of_kin_relation: '',
  // Step 5 — Medical & Protector
  protector_no: '', protector_date: '', medical_center: '', gamca_number: '', medical_date: '',
  medical_consignment_no: '', medical_send_date: '', medical_courier_name: '',
  // Step 6 — Visa & Travel
  e_number: '', visa_stamp_date: '', date_of_departure: '', flight_no: '', destination: '', ticket_no: '',
  // Step 7 — Charge Summary
  salary: '', price: '', ticket_included: 'No', accommodation_allowance: '', food_allowance: '',
  slot_notes: '',
}

// The Employment step shows the permission NUMBER as prefix + suffix. We keep a
// separate suffix input and compose the full permission_no on save.
function stripPrefix(full: string, prefix: string): string {
  if (full && prefix && full.startsWith(prefix)) return full.slice(prefix.length)
  return full || ''
}

export default function AssignWizard(props: AssignWizardProps) {
  const { tradeId, tradeName = '—', salary, permissionPrefix = 'DTC/LHR/—/', currency = 'SAR', demandId, editId, onClose, onSaved } = props
  const toast = useToast()
  const isEdit = !!editId
  const [step, setStep] = useState(1)
  const [form, setForm] = useState<Form>(() => ({ ...EMPTY, salary: salary ? String(salary) : '' }))
  const [permSuffix, setPermSuffix] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveLabel, setSaveLabel] = useState<string | null>(null)
  const [ocrStatus, setOcrStatus] = useState<{ kind: 'info' | 'ok' | 'err'; msg: string } | null>(null)
  const [scanning, setScanning] = useState(false)
  // CREATE mode: the backend wizard-draft id (created lazily on first "Next").
  const wizardIdRef = useRef<number | null>(null)
  const ocrRef = useRef<HTMLInputElement>(null)
  // Selected candidate photo (via the polished ImageUpload). Kept in state so
  // the preview persists across wizard steps and is read at submit time.
  const [photoFile, setPhotoFile] = useState<File | null>(null)

  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }))

  // EDIT mode → load candidate, pre-fill matching fields.
  useEffect(() => {
    if (!editId) return
    api.get(`/candidates/${editId}`).then(({ data }) => {
      setForm((f) => {
        const next: Form = { ...f }
        Object.keys(EMPTY).forEach((k) => {
          let v = (data as any)[k]
          if (v === null || v === undefined) return
          if ((k.includes('date') || k.includes('_at')) && typeof v === 'string' && v.length >= 10) v = v.slice(0, 10)
          next[k] = String(v)
        })
        return next
      })
      // Recover the permission suffix from the stored full permission_no.
      const fullPerm = (data as any).permission_no
      if (fullPerm) setPermSuffix(stripPrefix(String(fullPerm), permissionPrefix))
    }).catch((e) => toast.show(apiError(e, 'Could not load candidate'), 'error'))
  }, [editId, toast, permissionPrefix])

  // Esc to close + scroll lock + focus-in + focus trap + focus restore.
  // This used to be a local Escape listener plus lockBodyScroll(): correct as
  // far as it went, but it had no focus trap, so Tab walked out of a dialog
  // that declares aria-modal="true" and into the page behind it. Shared with
  // every other overlay now, so the four that had NO keyboard handling at all
  // cannot drift back apart from this one.
  const wizardCardRef = useDialogKeys<HTMLDivElement>(true, onClose)

  const scanPassport = useCallback(async (file: File) => {
    setScanning(true)
    setOcrStatus({ kind: 'info', msg: '⏳ Scanning passport…' })
    const fd = new FormData()
    fd.append('file', file)
    try {
      const { data } = await api.post('/ocr/passport', fd, { headers: { 'Content-Type': 'multipart/form-data' } })

      // Backend contract (2026-07-08 refactor): /ocr/passport now returns 200
      // with { ok: false, reason: 'no_ocr_backend' | 'unreadable_image', user_message }
      // when no backend is available or the image can't be parsed -- NOT a 5xx.
      // This lets us render an inline hint next to the button instead of a
      // silent axios global-error toast.
      if (data && data.ok === false) {
        if (data.reason === 'no_ocr_backend') {
          setOcrStatus({
            kind: 'info',
            msg: '⚙️ ' + (data.user_message || 'Automatic passport scanning is not available on this server. Please type the passport details below.'),
          })
        } else {
          setOcrStatus({
            kind: 'err',
            msg: '⚠️ ' + (data.user_message || 'No fields could be read. Please type the details manually.'),
          })
        }
        return
      }

      const fields = (data && data.fields) || {}
      const keys = ['passport_no', 'passport_issue_date', 'passport_expiry_date', 'passport_issue_place',
        'nationality', 'full_name', 'father_name', 'date_of_birth', 'gender', 'cnic', 'nadra_token_no',
        'place_of_birth', 'issuing_authority']
      setForm((f) => {
        const next = { ...f }
        keys.forEach((k) => { if (fields[k]) next[k] = String(fields[k]) })
        return next
      })
      const filled = keys.filter((k) => fields[k]).length
      if (filled) setOcrStatus({ kind: 'ok', msg: `✅ Passport scanned (${data.method || 'ocr'}) — ${filled} field(s) auto-filled.` })
      else setOcrStatus({ kind: 'err', msg: '⚠️ No fields could be read. Please type the details manually.' })
    } catch (e: any) {
      // Legacy path: older servers may still return 503 here. Show the same
      // helpful hint so the wizard is never a dead end.
      const detail = e?.response?.status === 503
        ? 'OCR is unavailable on this server — type the passport details manually.'
        : apiError(e, 'Scan failed')
      setOcrStatus({ kind: 'err', msg: '❌ ' + detail })
    } finally {
      setScanning(false)
    }
  }, [])

  // Compose the flat, cleaned payload for the current form (used by finalize/PUT).
  function composedForm(): Record<string, any> {
    const out: Record<string, any> = { ...form }
    // Compose permission_no from prefix + suffix (empty suffix → empty number).
    out.permission_no = permSuffix ? `${permissionPrefix}${permSuffix}` : ''
    return out
  }

  // Slice the composed form into just the fields owned by `stepNo`.
  function stepPayload(stepNo: number): Record<string, any> {
    const composed = composedForm()
    const out: Record<string, any> = {}
    for (const k of STEP_FIELDS[stepNo] || []) {
      if (composed[k] !== undefined && composed[k] !== '') out[k] = composed[k]
    }
    return out
  }

  // CREATE mode: ensure a wizard draft exists (lazily), then persist a step.
  async function persistStep(stepNo: number, advance: boolean) {
    if (isEdit) return // edit mode does not use the draft API
    // Lazily open the draft on the trade the first time we save.
    if (wizardIdRef.current == null) {
      const { data } = await api.post('/candidates/wizard', { job_category_id: tradeId })
      wizardIdRef.current = data.id
    }
    await api.patch(`/candidates/wizard/${wizardIdRef.current}/step/${stepNo}`, {
      data: stepPayload(stepNo),
      advance,
    })
  }

  // "Next" — in create mode persist the current step as a resumable draft.
  async function next() {
    const cur = step
    if (!isEdit) {
      try {
        await persistStep(cur, true)
      } catch (e) {
        toast.show(apiError(e, 'Could not save this step'), 'error')
        return
      }
    }
    setStep((s) => Math.min(s + 1, TOTAL))
  }

  async function save() {
    if (saving) return
    setSaving(true)

    // EDIT mode → single PUT with the full composed candidate payload.
    if (isEdit) {
      const payload = composedForm()
      Object.keys(payload).forEach((k) => { if (payload[k] === '') delete payload[k] })
      try {
        await api.put(`/candidates/${editId}`, payload)
        const pf = photoFile
        if (pf) {
          try { const pfd = new FormData(); pfd.append('file', pf); await api.post(`/candidates/${editId}/photo`, pfd, { headers: { 'Content-Type': 'multipart/form-data' } }) } catch { /* non-fatal */ }
        }
        toast.show('✅ Candidate updated', 'success')
        onSaved(); onClose()
      } catch (e) {
        toast.show(apiError(e, 'Could not update candidate'), 'error')
        setSaving(false)
      }
      return
    }

    // CREATE mode → save final step into the draft, then finalize (which creates
    // the candidate AND auto-assigns to the trade, enforcing slot capacity).
    let candidateId: number | null = null
    try {
      await persistStep(TOTAL, false) // persist Charge Summary (step 7)
      const { data } = await api.post(`/candidates/wizard/${wizardIdRef.current}/finalize`)
      candidateId = data?.candidate?.id ?? null
    } catch (e) {
      const msg = apiError(e)
      const slotFull = /fully assigned|no slots/i.test(msg)
      if (slotFull) {
        // finalize saved the candidate but refused the assignment (trade full).
        toast.show('⚠️ This trade is fully assigned — no open slots. The candidate profile was saved as a draft.', 'error')
        setSaveLabel('Slot Full')
        onSaved()
        return // keep disabled to avoid duplicate finalize
      }
      toast.show(apiError(e, 'Could not save candidate'), 'error')
      setSaving(false)
      return
    }

    // Optional photo upload (non-fatal).
    const pf = photoFile
    if (pf && candidateId) {
      try { const pfd = new FormData(); pfd.append('file', pf); await api.post(`/candidates/${candidateId}/photo`, pfd, { headers: { 'Content-Type': 'multipart/form-data' } }) } catch { /* non-fatal */ }
    }

    toast.show(`✅ Candidate saved and assigned to ${tradeName}`, 'success')
    onSaved(); onClose()
  }

  const inp = 'mt-1 w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200 focus:border-blue-400'

  return createPortal(
    <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm flex items-start justify-center z-[100] overflow-y-auto py-6 px-3 animate-fade-in"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      {/* aria-labelledby -> the visible <h3>, so the accessible name follows
          the wizard's own title ("Edit Candidate #N" / "Assign Candidate to
          <trade>") instead of a hardcoded string that could drift. This
          7-step wizard carried no dialog role at all until 2026-08-13. */}
      <div ref={wizardCardRef} className="bg-white rounded-xl shadow-2xl w-full max-w-3xl my-4 flex flex-col max-h-[92vh]" role="dialog" aria-modal="true" aria-labelledby="assign-wizard-title">
        {/* Header */}
        <div className="px-6 py-4 border-b flex items-center justify-between">
          <h3 id="assign-wizard-title" className="font-bold text-slate-800 text-base">
            {isEdit ? <>Edit Candidate <span className="text-blue-600">#{editId}</span></>
              : <>Assign Candidate to <span className="text-blue-600">{tradeName}</span></>}
          </h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700 text-xl" aria-label="Close"><i className="fa-solid fa-xmark" /></button>
        </div>

        {/* Stepper */}
        <div className="px-6 py-3 border-b bg-slate-50 overflow-x-auto">
          <div className="flex items-center justify-between gap-2 text-xs min-w-[640px]">
            {STEPS.map((label, i) => {
              const n = i + 1
              const done = n < step
              const active = n === step
              return (
                <div key={n} className="flex-1 flex items-center gap-2">
                  <span className={`w-7 h-7 shrink-0 rounded-full flex items-center justify-center font-semibold ${done ? 'bg-emerald-500 text-white' : active ? 'bg-blue-600 text-white' : 'bg-slate-200 text-slate-500'}`}>
                    {done ? <i className="fa-solid fa-check text-xs" /> : n}
                  </span>
                  <span className={active ? 'font-semibold text-slate-700 whitespace-nowrap' : 'text-slate-500 whitespace-nowrap'}>{label}</span>
                </div>
              )
            })}
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5">
          {/* STEP 1 — Personal Info */}
          {step === 1 && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <Field label="Name" req><input className={inp} placeholder="Full name" value={form.full_name} onChange={(e) => set('full_name', e.target.value)} /></Field>
              <Field label="Name (Arabic)"><input dir="rtl" className={inp} value={form.name_arabic} onChange={(e) => set('name_arabic', e.target.value)} /></Field>
              <Field label="Mother Name"><input className={inp} value={form.mother_name} onChange={(e) => set('mother_name', e.target.value)} /></Field>
              <Field label="Father Name" req><input className={inp} value={form.father_name} onChange={(e) => set('father_name', e.target.value)} /></Field>
              <Field label="Father Name (Arabic)"><input dir="rtl" className={inp} value={form.father_name_arabic} onChange={(e) => set('father_name_arabic', e.target.value)} /></Field>
              <Field label="Gender" req>
                <select className={inp} value={form.gender} onChange={(e) => set('gender', e.target.value)}>
                  <option value="">Select…</option><option>Male</option><option>Female</option>
                </select>
              </Field>
              <Field label="Marital Status" req>
                <select className={inp} value={form.marital_status} onChange={(e) => set('marital_status', e.target.value)}>
                  <option value="">Select…</option><option>Single</option><option>Married</option><option>Divorced</option><option>Widowed</option>
                </select>
              </Field>
              <Field label="Religion"><input className={inp} value={form.religion} onChange={(e) => set('religion', e.target.value)} /></Field>
              <Field label="Date Of Birth" req><input type="date" className={inp} value={form.date_of_birth} onChange={(e) => set('date_of_birth', e.target.value)} /></Field>
              <Field label="Place Of Birth"><input className={inp} value={form.place_of_birth} onChange={(e) => set('place_of_birth', e.target.value)} /></Field>
              <Field label="Place Of Birth (Arabic)"><input dir="rtl" className={inp} value={form.place_of_birth_arabic} onChange={(e) => set('place_of_birth_arabic', e.target.value)} /></Field>
              <Field label="Nationality"><input className={inp} value={form.nationality} onChange={(e) => set('nationality', e.target.value)} /></Field>
              <Field label="Address" span3><input className={inp} value={form.address} onChange={(e) => set('address', e.target.value)} /></Field>
              <Field label="Phone No"><input className={inp} value={form.phone} onChange={(e) => set('phone', e.target.value)} /></Field>
              {/* `email` is bound on the OEP form and displayed read-only on the
                  candidate detail panel, but until 2026-08-12 there was no input
                  for it ANYWHERE in the app -- so it was blank for all 2,675
                  records and printed blank on every form by construction. The
                  backend already accepted it (_coerce allows any Candidate
                  column); only this control was missing. */}
              <Field label="Email"><input type="email" className={inp} value={form.email} onChange={(e) => set('email', e.target.value)} /></Field>
              <Field label="Tehsil"><input className={inp} value={form.tehsil} onChange={(e) => set('tehsil', e.target.value)} /></Field>
              <Field label="District"><input className={inp} value={form.district} onChange={(e) => set('district', e.target.value)} /></Field>
              <Field label="Province">
                <select className={inp} value={form.province} onChange={(e) => set('province', e.target.value)}>
                  <option value="">Select…</option><option>Punjab</option><option>Sindh</option><option>KPK</option><option>Balochistan</option><option>Islamabad</option><option>AJK</option><option>Gilgit-Baltistan</option>
                </select>
              </Field>
              <Field label="Candidate Photo" span3>
                <div className="max-w-[150px]">
                  <ImageUpload
                    value={form.photo ? `/static/uploads/photos/${form.photo}` : undefined}
                    onChange={setPhotoFile}
                    fit="cover"
                    aspect="3 / 4"
                    maxSizeMb={5}
                    hint="Passport-style photo · white background."
                  />
                </div>
              </Field>
            </div>
          )}

          {/* STEP 2 — Identification (OCR) */}
          {step === 2 && (
            <>
              <div className="mb-4 p-3 bg-blue-50 border border-blue-200 rounded-lg">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-sm font-semibold text-slate-800"><i className="fa-solid fa-camera-retro text-blue-600 mr-1" /> Real-time Passport OCR</div>
                    <div className="text-xs text-slate-600 mt-0.5">Scan a passport image — passport no, dates, nationality &amp; place will auto-fill below.</div>
                  </div>
                  <button type="button" disabled={scanning} onClick={() => ocrRef.current?.click()}
                    className="bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white text-xs font-semibold px-3 py-2 rounded-md whitespace-nowrap">
                    <i className={`fa-solid ${scanning ? 'fa-spinner fa-spin' : 'fa-upload'} mr-1`} /> {scanning ? 'Scanning…' : 'Scan Passport'}
                  </button>
                  <input ref={ocrRef} type="file" accept="image/*" className="hidden"
                    aria-label="Upload passport scan for OCR"
                    onChange={(e) => { const f = e.target.files?.[0]; if (f) scanPassport(f); e.target.value = '' }} />
                </div>
                {ocrStatus && (
                  <div className={`mt-2 text-xs rounded-md p-2 ${ocrStatus.kind === 'ok' ? 'bg-emerald-50 text-emerald-700' : ocrStatus.kind === 'err' ? 'bg-rose-50 text-rose-700' : 'bg-blue-50 text-blue-700'}`}>{ocrStatus.msg}</div>
                )}
              </div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <Field label="Passport No"><input className={inp} value={form.passport_no} onChange={(e) => set('passport_no', e.target.value)} /></Field>
                <Field label="Passport Issue Date"><input type="date" className={inp} value={form.passport_issue_date} onChange={(e) => set('passport_issue_date', e.target.value)} /></Field>
                <Field label="Passport Expiry Date"><input type="date" className={inp} value={form.passport_expiry_date} onChange={(e) => set('passport_expiry_date', e.target.value)} /></Field>
                <Field label="Issuing Authority"><input className={inp} value={form.issuing_authority} onChange={(e) => set('issuing_authority', e.target.value)} /></Field>
                <Field label="Issuing Authority (Arabic)"><input dir="rtl" placeholder="پاکستان" className={inp} value={form.issuing_authority_arabic} onChange={(e) => set('issuing_authority_arabic', e.target.value)} /></Field>
                <Field label="Passport Issue Place"><input className={inp} value={form.passport_issue_place} onChange={(e) => set('passport_issue_place', e.target.value)} /></Field>
                <Field label="CNIC No"><input placeholder="34601-1234567-1" className={inp} value={form.cnic} onChange={(e) => set('cnic', e.target.value)} /></Field>
                <Field label="Nadra Token No"><input placeholder="n/a" className={inp} value={form.nadra_token_no} onChange={(e) => set('nadra_token_no', e.target.value)} /></Field>
              </div>
            </>
          )}

          {/* STEP 3 — Employment */}
          {step === 3 && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <Field label="Permission No">
                <div className="mt-1 flex">
                  <span className="px-3 py-2 text-xs font-semibold bg-slate-100 border border-r-0 rounded-l-md text-slate-600 whitespace-nowrap">{permissionPrefix}</span>
                  <input placeholder="Suffix" className="flex-1 border border-slate-300 rounded-r-md px-3 py-2 text-sm" value={permSuffix} onChange={(e) => setPermSuffix(e.target.value)} />
                </div>
              </Field>
              <Field label="Permission Date"><input type="date" className={inp} value={form.permission_date} onChange={(e) => set('permission_date', e.target.value)} /></Field>
              <Field label="Qualification"><input className={inp} value={form.qualification} onChange={(e) => set('qualification', e.target.value)} /></Field>
              <Field label="Profession"><input className={inp} value={form.profession} onChange={(e) => set('profession', e.target.value)} /></Field>
              <Field label="Age"><input type="number" min="0" className={inp} value={form.age_employee} onChange={(e) => set('age_employee', e.target.value)} /></Field>
            </div>
          )}

          {/* STEP 4 — Next of Kin */}
          {step === 4 && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <Field label="Next Of Kin Name"><input className={inp} value={form.next_of_kin_name} onChange={(e) => set('next_of_kin_name', e.target.value)} /></Field>
              <Field label="Next Of Kin NIC No"><input placeholder="12345-1234567-1" className={inp} value={form.next_of_kin_nic} onChange={(e) => set('next_of_kin_nic', e.target.value)} /></Field>
              <Field label="Next Of Kin Relation"><input className={inp} value={form.next_of_kin_relation} onChange={(e) => set('next_of_kin_relation', e.target.value)} /></Field>
            </div>
          )}

          {/* STEP 5 — Medical & Protector */}
          {step === 5 && (
            <>
              <div className="text-sm font-semibold text-slate-700 mb-3"><i className="fa-solid fa-notes-medical text-rose-500 mr-1" /> Medical &amp; Protector</div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <Field label="Protector No"><input className={inp} value={form.protector_no} onChange={(e) => set('protector_no', e.target.value)} /></Field>
                <Field label="Protector Date"><input type="date" className={inp} value={form.protector_date} onChange={(e) => set('protector_date', e.target.value)} /></Field>
                <Field label="Medical Center"><input className={inp} value={form.medical_center} onChange={(e) => set('medical_center', e.target.value)} /></Field>
                <Field label="GAMCA Number"><input className={inp} value={form.gamca_number} onChange={(e) => set('gamca_number', e.target.value)} /></Field>
                <Field label="Medical Date"><input type="date" className={inp} value={form.medical_date} onChange={(e) => set('medical_date', e.target.value)} /></Field>
                <Field label="Medical Consignment No"><input className={inp} value={form.medical_consignment_no} onChange={(e) => set('medical_consignment_no', e.target.value)} /></Field>
                <Field label="Medical Send Date"><input type="date" className={inp} value={form.medical_send_date} onChange={(e) => set('medical_send_date', e.target.value)} /></Field>
                <Field label="Medical Courier Name"><input className={inp} value={form.medical_courier_name} onChange={(e) => set('medical_courier_name', e.target.value)} /></Field>
              </div>
            </>
          )}

          {/* STEP 6 — Visa & Travel */}
          {step === 6 && (
            <>
              <div className="text-sm font-semibold text-slate-700 mb-3"><i className="fa-solid fa-plane-departure text-sky-600 mr-1" /> Visa &amp; Travel</div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <Field label="E-Number"><input className={inp} value={form.e_number} onChange={(e) => set('e_number', e.target.value)} /></Field>
                <Field label="Visa Stamp Date"><input type="date" className={inp} value={form.visa_stamp_date} onChange={(e) => set('visa_stamp_date', e.target.value)} /></Field>
                <Field label="Date Of Departure"><input type="date" className={inp} value={form.date_of_departure} onChange={(e) => set('date_of_departure', e.target.value)} /></Field>
                <Field label="Flight No"><input className={inp} value={form.flight_no} onChange={(e) => set('flight_no', e.target.value)} /></Field>
                <Field label="Destination"><input className={inp} value={form.destination} onChange={(e) => set('destination', e.target.value)} /></Field>
                <Field label="Ticket No"><input className={inp} value={form.ticket_no} onChange={(e) => set('ticket_no', e.target.value)} /></Field>
              </div>
            </>
          )}

          {/* STEP 7 — Charge Summary */}
          {step === 7 && (
            <>
              <div className="text-sm font-semibold text-slate-700 mb-3"><i className="fa-solid fa-receipt text-emerald-600 mr-1" /> Charge Summary (this assignment)</div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <Field label="Salary">
                  <div className="mt-1 flex">
                    <input type="number" step="0.01" className="flex-1 border border-slate-300 rounded-l-md px-3 py-2 text-sm" value={form.salary} onChange={(e) => set('salary', e.target.value)} />
                    <span className="px-3 py-2 text-xs font-semibold bg-slate-100 border border-l-0 rounded-r-md text-slate-600">{currency}</span>
                  </div>
                </Field>
                <Field label="Price (Service Charge)"><input type="number" step="0.01" className={inp} value={form.price} onChange={(e) => set('price', e.target.value)} /></Field>
                <Field label="Ticket Included">
                  <select className={inp} value={form.ticket_included} onChange={(e) => set('ticket_included', e.target.value)}><option>No</option><option>Yes</option></select>
                </Field>
                <Field label="Accommodation Allowance"><input className={inp} value={form.accommodation_allowance} onChange={(e) => set('accommodation_allowance', e.target.value)} /></Field>
                <Field label="Food Allowance"><input className={inp} value={form.food_allowance} onChange={(e) => set('food_allowance', e.target.value)} /></Field>
                <Field label="Slot Notes" span3><textarea rows={2} className={inp} value={form.slot_notes} onChange={(e) => set('slot_notes', e.target.value)} /></Field>
              </div>
              {!isEdit && (
                <div className="mt-4 text-xs text-slate-500 bg-slate-50 border border-slate-200 rounded-lg p-3">
                  <i className="fa-solid fa-circle-info mr-1 text-blue-500" /> On <strong>Save</strong>, a new Candidate will be created and assigned to <span className="font-semibold text-slate-700">{tradeName}</span> in this Demand File.
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t bg-slate-50 flex items-center justify-between">
          {step > 1
            ? <button type="button" onClick={() => setStep((s) => s - 1)} className="px-4 py-2 text-sm text-slate-600 hover:bg-white border border-slate-300 rounded-md"><i className="fa-solid fa-arrow-left mr-1" /> Back</button>
            : <span />}
          <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-slate-500 hover:text-slate-700 mr-auto ml-2">Cancel</button>
          {step < TOTAL
            ? <button type="button" onClick={next} className="px-5 py-2 text-sm bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-md">Next <i className="fa-solid fa-arrow-right ml-1" /></button>
            : <button type="button" disabled={saving || !!saveLabel} onClick={save} className="px-5 py-2 text-sm bg-blue-600 hover:bg-blue-700 disabled:opacity-70 text-white font-semibold rounded-md">
                {saveLabel
                  ? <><i className="fa-solid fa-triangle-exclamation mr-1" /> {saveLabel}</>
                  : saving
                    ? <><i className="fa-solid fa-spinner fa-spin mr-1" /> Saving…</>
                    : isEdit
                      ? <><i className="fa-solid fa-floppy-disk mr-1" /> Save Changes</>
                      : <><i className="fa-solid fa-check mr-1" /> Save &amp; Assign</>}
              </button>}
        </div>
      </div>
    </div>,
    document.body,
  )
}

function Field({ label, req, span3, children }: { label: string; req?: boolean; span3?: boolean; children: React.ReactNode }) {
  return (
    <label className={span3 ? 'md:col-span-3' : ''}>
      <span className="text-slate-600 text-sm">{label}{req && <span className="text-rose-500"> *</span>}</span>
      {children}
    </label>
  )
}
