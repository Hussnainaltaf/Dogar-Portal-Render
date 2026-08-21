import { useEffect, useMemo, useState } from 'react'
import { api, apiError, TENANT_INFO, type TenantInfo } from '@/lib/api'
import { FONT_STACKS, applyTenantBranding as applyBranding, resolveFontStack } from '@/lib/branding'
import { ImageUpload } from '@/components/ImageUpload'
import { useToast } from '@/components/Toast'

type BrandingForm = {
  shortName: string
  subtitle: string
  color: string
  fontKey: string
  officeName: string
  licenseNumber: string
  officeAddress: string
  logo: string
}

const DEFAULT_FORM: BrandingForm = {
  shortName: '',
  subtitle: 'Overseas Employment Promoters',
  color: '#2563eb',
  fontKey: 'system',
  officeName: '',
  licenseNumber: '',
  officeAddress: '',
  logo: '',
}

function toForm(value: TenantInfo | null | undefined): BrandingForm {
  return {
    shortName: value?.shortName || '',
    subtitle: value?.subtitle || DEFAULT_FORM.subtitle,
    color: value?.color || DEFAULT_FORM.color,
    fontKey: value?.fontKey || DEFAULT_FORM.fontKey,
    officeName: value?.officeName || '',
    licenseNumber: value?.licenseNumber || '',
    officeAddress: value?.officeAddress || '',
    logo: value?.logo || '',
  }
}

function toTenantInfo(payload: any): TenantInfo {
  return {
    slug: String(payload.slug || ''),
    name: String(payload.name || payload.company_name || ''),
    shortName: String(payload.shortName || payload.short_name || ''),
    color: String(payload.color || payload.primary_color || DEFAULT_FORM.color),
    fontKey: String(payload.fontKey || payload.font_key || DEFAULT_FORM.fontKey),
    logo: String(payload.logo || payload.logo_url || ''),
    officeName: String(payload.officeName || payload.office_name || ''),
    subtitle: String(payload.subtitle || DEFAULT_FORM.subtitle),
    licenseNumber: String(payload.licenseNumber || payload.license_number || ''),
    officeAddress: String(payload.officeAddress || payload.office_address || ''),
    contactPhone: payload.contactPhone || payload.contact_phone || undefined,
    contactEmail: payload.contactEmail || payload.contact_email || undefined,
  }
}

function publishBranding(payload: any) {
  const tenant = toTenantInfo(payload)
  applyBranding(tenant)
  window.dispatchEvent(new CustomEvent('tenant-branding-updated', { detail: tenant }))
  return tenant
}

export function BrandingSettings() {
  const toast = useToast()
  const [form, setForm] = useState<BrandingForm>(() => toForm(TENANT_INFO))
  const [loading, setLoading] = useState(Boolean(TENANT_INFO))
  const [saving, setSaving] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadPct, setUploadPct] = useState(0)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!TENANT_INFO) {
      setLoading(false)
      return
    }
    api.get('/settings/branding')
      .then(({ data }) => setForm(toForm(toTenantInfo(data))))
      .catch((e) => {
        const message = apiError(e, 'Could not load tenant branding')
        setError(message)
        toast.show(message, 'error')
      })
      .finally(() => setLoading(false))
  }, [toast])

  const fontOptions = useMemo(() => Object.keys(FONT_STACKS), [])
  const set = (key: keyof BrandingForm, value: string) => setForm((current) => ({ ...current, [key]: value }))

  const save = async () => {
    setSaving(true)
    setError('')
    try {
      const { data } = await api.put('/settings/branding', {
        short_name: form.shortName.trim(),
        subtitle: form.subtitle.trim(),
        primary_color: form.color.trim(),
        font_key: form.fontKey,
        office_name: form.officeName.trim(),
        license_number: form.licenseNumber.trim(),
        office_address: form.officeAddress.trim(),
      })
      const tenant = publishBranding(data)
      setForm(toForm(tenant))
      toast.show('Branding saved for this recruitment office', 'success')
    } catch (e) {
      const message = apiError(e, 'Failed to save tenant branding')
      setError(message)
      toast.show(message, 'error')
    } finally {
      setSaving(false)
    }
  }

  const uploadLogo = async (file: File | null) => {
    if (!file) return
    if (!/^image\/(png|jpe?g|webp)$/.test(file.type)) {
      toast.show('Logo must be PNG, JPG, or WEBP', 'error')
      return
    }
    if (file.size > 3 * 1024 * 1024) {
      toast.show('Logo must be under 3 MB', 'error')
      return
    }
    setUploading(true)
    setUploadPct(1)
    setError('')
    try {
      const body = new FormData()
      body.append('file', file)
      const { data } = await api.post('/settings/branding/logo', body, {
        headers: { 'Content-Type': 'multipart/form-data' },
        onUploadProgress: (event) => {
          if (event.total) setUploadPct(Math.round((event.loaded / event.total) * 100))
        },
      })
      const tenant = publishBranding(data)
      setForm(toForm(tenant))
      toast.show('Logo uploaded for this recruitment office', 'success')
    } catch (e) {
      const message = apiError(e, 'Logo upload failed')
      setError(message)
      toast.show(message, 'error')
    } finally {
      setUploading(false)
      setUploadPct(0)
    }
  }

  if (!TENANT_INFO) {
    return (
      <div className="card border-amber-200 bg-amber-50">
        <div className="card-body space-y-2 text-amber-900">
          <h2 className="font-bold flex items-center gap-2"><i className="fa-solid fa-building-shield" /> Tenant Branding</h2>
          <p className="text-sm">Branding settings are available inside an office portal. Open a tenant portal from Portal Admin to edit its logo, colors, fonts, and printed identity. The root Dogar portal remains unchanged.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-5 max-w-4xl" data-testid="branding-settings">
      <div className="card border-blue-100 bg-blue-50/60">
        <div className="card-body flex flex-col sm:flex-row sm:items-start gap-3">
          <i className="fa-solid fa-circle-info text-blue-600 mt-0.5" />
          <div>
            <h2 className="font-bold text-slate-800">Brand this recruitment office</h2>
            <p className="text-sm text-slate-600 mt-1">These values apply only to <strong>{TENANT_INFO.name}</strong>. They are used by the sidebar, topbar, login screen, and printed candidate and demand documents for this tenant.</p>
          </div>
        </div>
      </div>

      {error && <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800" role="alert">{error}</div>}

      {loading ? (
        <div className="card"><div className="card-body text-center py-16 text-slate-400"><i className="fa-solid fa-spinner fa-spin text-2xl text-blue-600" /></div></div>
      ) : (
        <>
          <section className="card"><div className="card-body space-y-4">
            <h3 className="font-bold text-slate-800 flex items-center gap-2"><i className="fa-solid fa-image text-blue-600" /> Logo</h3>
            <div className="max-w-[180px]">
              <ImageUpload
                value={form.logo}
                onChange={uploadLogo}
                uploading={uploading}
                progress={uploadPct}
                fit="contain"
                aspect="1 / 1"
                maxSizeMb={3}
                acceptRe={/^image\/(png|jpe?g|webp)$/}
                accept="image/png,image/jpeg,image/webp"
                hint="PNG, JPG, or WEBP · max 3 MB"
              />
            </div>
            <p className="text-xs text-slate-500">The uploaded logo is rendered from the current tenant's asset path, not from a shared Dogar asset.</p>
          </div></section>

          <section className="card"><div className="card-body space-y-4">
            <h3 className="font-bold text-slate-800 flex items-center gap-2"><i className="fa-solid fa-swatchbook text-blue-600" /> Brand identity</h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <label className="block"><span className="text-[13px] font-semibold text-slate-700">Short name</span>
                <input className="form-input w-full mt-1" value={form.shortName} onChange={(e) => set('shortName', e.target.value)} maxLength={50} /></label>
              <label className="block"><span className="text-[13px] font-semibold text-slate-700">Subtitle / tagline</span>
                <input className="form-input w-full mt-1" value={form.subtitle} onChange={(e) => set('subtitle', e.target.value)} maxLength={150} /></label>
              <label className="block"><span className="text-[13px] font-semibold text-slate-700">Primary brand color</span>
                <div className="flex items-center gap-2 mt-1"><input type="color" className="form-input w-14 h-[38px] p-1" value={/^#[0-9a-f]{6}$/i.test(form.color) ? form.color : '#2563eb'} onChange={(e) => set('color', e.target.value)} aria-label="Brand color picker" /><input className="form-input flex-1 font-mono" value={form.color} onChange={(e) => set('color', e.target.value)} placeholder="#2563eb" maxLength={20} /></div></label>
              <label className="block"><span className="text-[13px] font-semibold text-slate-700">Interface & document font</span>
                <select className="form-input w-full mt-1" value={form.fontKey} onChange={(e) => set('fontKey', e.target.value)} style={{ fontFamily: resolveFontStack(form.fontKey) }}>
                  {fontOptions.map((key) => <option key={key} value={key} style={{ fontFamily: FONT_STACKS[key] }}>{key === 'system' ? 'System default' : key[0].toUpperCase() + key.slice(1)}</option>)}
                </select>
              </label>
            </div>
            <p className="text-xs text-slate-500">The font is stored as a validated key and resolved consistently in the browser and document renderer.</p>
          </div></section>

          <section className="card"><div className="card-body space-y-4">
            <h3 className="font-bold text-slate-800 flex items-center gap-2"><i className="fa-solid fa-file-signature text-blue-600" /> Printed office identity</h3>
            <p className="text-xs text-slate-500">These values appear on this office's printed candidate profiles, receipts, undertakings, and demand files.</p>
            <label className="block"><span className="text-[13px] font-semibold text-slate-700">Recruiting office name</span><input className="form-input w-full mt-1" value={form.officeName} onChange={(e) => set('officeName', e.target.value)} maxLength={200} /></label>
            <label className="block"><span className="text-[13px] font-semibold text-slate-700">OEP licence number</span><input className="form-input w-full mt-1 font-mono" value={form.licenseNumber} onChange={(e) => set('licenseNumber', e.target.value)} maxLength={100} /></label>
            <label className="block"><span className="text-[13px] font-semibold text-slate-700">Office address</span><textarea className="form-input w-full mt-1" rows={3} value={form.officeAddress} onChange={(e) => set('officeAddress', e.target.value)} maxLength={400} /></label>
          </div></section>

          <div className="flex justify-end"><button onClick={save} disabled={saving} className="btn btn-primary inline-flex items-center gap-2">{saving ? <i className="fa-solid fa-spinner fa-spin" /> : <i className="fa-solid fa-floppy-disk" />} Save branding</button></div>
        </>
      )}
    </div>
  )
}

export default BrandingSettings

export const brandingFormDefaults = DEFAULT_FORM
