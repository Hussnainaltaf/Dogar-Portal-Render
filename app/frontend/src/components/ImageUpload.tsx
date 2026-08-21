import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * ImageUpload — a polished, reusable image picker with:
 *   • drag-and-drop OR click to browse
 *   • an instant, CRISP local preview (object-fit controlled by `fit`)
 *   • consistent cropping via a fixed aspect ratio box
 *   • client-side type/size validation with clear inline errors
 *   • a real upload progress bar (driven by the caller via `progress`)
 *
 * The component is deliberately "controlled" for the network part: the parent
 * performs the actual upload (so it owns the endpoint, FormData shape and auth)
 * and passes back `progress` (0–100) and `uploading`. The component owns only
 * the local file selection + preview UX.
 */
export interface ImageUploadProps {
  /** Existing remote image URL to show before the user picks a new file. */
  value?: string
  /** Called with the chosen File (already validated) or null when cleared. */
  onChange: (file: File | null) => void
  /** 0–100 upload progress; when > 0 and < 100 the bar is shown. */
  progress?: number
  /** True while the parent is uploading (disables the control). */
  uploading?: boolean
  /** How the preview image is fitted inside the frame. */
  fit?: 'cover' | 'contain'
  /** Aspect ratio of the preview frame, e.g. '1 / 1', '4 / 3', '3 / 4'. */
  aspect?: string
  /** Max size in MB (default 5). */
  maxSizeMb?: number
  /** Accepted mime pattern (default common raster + svg). */
  acceptRe?: RegExp
  /** accept attribute for the file input. */
  accept?: string
  /** Helper text shown under the control. */
  hint?: string
  /** Label above the control. */
  label?: string
  className?: string
}

export function ImageUpload({
  value,
  onChange,
  progress = 0,
  uploading = false,
  fit = 'cover',
  aspect = '1 / 1',
  maxSizeMb = 5,
  acceptRe = /^image\/(png|jpe?g|webp|svg\+xml|gif)$/,
  accept = 'image/png,image/jpeg,image/webp,image/svg+xml,image/gif',
  hint,
  label,
  className = '',
}: ImageUploadProps) {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [localUrl, setLocalUrl] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const [dragOver, setDragOver] = useState(false)

  // Revoke the object URL when it changes / on unmount to avoid a memory leak.
  useEffect(() => {
    return () => { if (localUrl) URL.revokeObjectURL(localUrl) }
  }, [localUrl])

  const accept_ = useCallback(
    (file: File | null | undefined) => {
      setErr('')
      if (!file) return
      if (!acceptRe.test(file.type)) {
        setErr('Unsupported file type. Use PNG, JPG, WEBP or SVG.')
        return
      }
      if (file.size > maxSizeMb * 1024 * 1024) {
        setErr(`Image is too large — keep it under ${maxSizeMb} MB.`)
        return
      }
      if (localUrl) URL.revokeObjectURL(localUrl)
      setLocalUrl(URL.createObjectURL(file))
      onChange(file)
    },
    [acceptRe, maxSizeMb, localUrl, onChange],
  )

  const clear = () => {
    if (localUrl) URL.revokeObjectURL(localUrl)
    setLocalUrl(null)
    setErr('')
    onChange(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  const shown = localUrl || value || ''
  const showBar = uploading || (progress > 0 && progress < 100)

  return (
    <div className={className}>
      {label && <span className="block text-[13px] font-semibold text-slate-700 mb-1.5">{label}</span>}

      <div
        role="button"
        tabIndex={0}
        aria-label="Upload image"
        onClick={() => !uploading && inputRef.current?.click()}
        onKeyDown={(e) => { if ((e.key === 'Enter' || e.key === ' ') && !uploading) { e.preventDefault(); inputRef.current?.click() } }}
        onDragOver={(e) => { e.preventDefault(); if (!uploading) setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => { e.preventDefault(); setDragOver(false); if (!uploading) accept_(e.dataTransfer.files?.[0]) }}
        className={`relative overflow-hidden rounded-xl border-2 border-dashed transition-colors cursor-pointer select-none
          ${dragOver ? 'border-blue-400 bg-blue-50/60' : err ? 'border-red-300 bg-red-50/40' : 'border-slate-300 bg-slate-50 hover:border-blue-300 hover:bg-slate-100/60'}
          ${uploading ? 'pointer-events-none opacity-80' : ''}`}
        style={{ aspectRatio: aspect }}
      >
        {shown ? (
          <img
            src={shown}
            alt="Preview"
            draggable={false}
            className="absolute inset-0 w-full h-full"
            // crisp rendering; cover crops consistently, contain keeps whole image
            style={{ objectFit: fit, objectPosition: 'center', imageRendering: 'auto' }}
          />
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-400 gap-1.5 px-3 text-center">
            <i className="fa-solid fa-cloud-arrow-up text-2xl" />
            <span className="text-[12.5px] font-medium text-slate-500">Click or drag an image here</span>
            <span className="text-[11px] text-slate-400">PNG, JPG, WEBP · up to {maxSizeMb} MB</span>
          </div>
        )}

        {/* Hover overlay to change the image */}
        {shown && !uploading && (
          <div className="absolute inset-0 bg-black/0 hover:bg-black/35 transition-colors flex items-center justify-center opacity-0 hover:opacity-100">
            <span className="text-white text-[12.5px] font-semibold bg-black/50 rounded-full px-3 py-1.5">
              <i className="fa-solid fa-camera-rotate mr-1.5" />Change
            </span>
          </div>
        )}

        {/* Progress bar */}
        {showBar && (
          <div className="absolute inset-x-0 bottom-0 bg-black/40 backdrop-blur-sm px-3 py-2">
            <div className="flex items-center justify-between text-[11px] text-white/90 mb-1">
              <span><i className="fa-solid fa-spinner fa-spin mr-1.5" />Uploading…</span>
              <span className="font-mono">{Math.round(progress)}%</span>
            </div>
            <div className="h-1.5 rounded-full bg-white/25 overflow-hidden">
              <div className="h-full bg-blue-400 transition-[width] duration-150" style={{ width: `${Math.max(4, progress)}%` }} />
            </div>
          </div>
        )}
      </div>

      <div className="flex items-center justify-between mt-1.5 gap-2">
        <p className={`text-[11.5px] ${err ? 'text-red-600' : 'text-slate-400'} min-w-0`}>
          {err ? <><i className="fa-solid fa-circle-exclamation mr-1" />{err}</> : (hint || '')}
        </p>
        {shown && !uploading && (
          <button type="button" onClick={(e) => { e.stopPropagation(); clear() }}
            className="text-[11.5px] text-slate-500 hover:text-red-600 font-medium whitespace-nowrap">
            <i className="fa-regular fa-trash-can mr-1" />Remove
          </button>
        )}
      </div>

      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="hidden"
        onChange={(e) => { accept_(e.target.files?.[0]); }}
      />
    </div>
  )
}

export default ImageUpload
