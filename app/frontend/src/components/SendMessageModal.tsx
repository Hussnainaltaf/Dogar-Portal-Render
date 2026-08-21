import { useEffect, useState } from 'react'
import { Modal } from '@/components/ui'
import { useToast } from '@/components/Toast'
import {
  apiError,
  getMessagingStatus,
  sendMessage,
  type MessageChannel,
  type MessagingStatus,
} from '@/lib/api'

/**
 * Real WhatsApp / SMS send dialog (Task 9).
 *
 * Replaces the old client-side `wa.me` deep-link with a genuine server-side
 * Twilio send. The message is POSTed to /api/messaging/send behind the auth
 * layer; credentials never touch the browser. When the backend runs in
 * dry-run mode (Twilio not configured) the modal clearly says so.
 */
export function SendMessageModal({
  open,
  onClose,
  to,
  candidateId,
  candidateName,
}: {
  open: boolean
  onClose: () => void
  to?: string
  candidateId?: number
  candidateName?: string
}) {
  const toast = useToast()
  const [channel, setChannel] = useState<MessageChannel>('whatsapp')
  const [phone, setPhone] = useState(to || '')
  const [body, setBody] = useState('')
  const [sending, setSending] = useState(false)
  const [status, setStatus] = useState<MessagingStatus | null>(null)

  useEffect(() => {
    if (!open) return
    setPhone(to || '')
    setBody('')
    getMessagingStatus().then(setStatus).catch(() => setStatus(null))
  }, [open, to])

  const providerMode = status?.mode
  const channelEnabled =
    channel === 'whatsapp' ? status?.whatsapp_enabled : status?.sms_enabled

  async function handleSend() {
    if (!phone.trim()) {
      toast.show('Enter a recipient phone number', 'error')
      return
    }
    if (!body.trim()) {
      toast.show('Enter a message', 'error')
      return
    }
    setSending(true)
    try {
      const res = await sendMessage({ to: phone.trim(), body: body.trim(), channel, candidate_id: candidateId })
      if (res.status === 'dry_run') {
        toast.show('Provider not configured — message logged (dry-run), not sent', 'info')
      } else {
        toast.show(`${channel === 'whatsapp' ? 'WhatsApp' : 'SMS'} ${res.status} to ${res.to}`, 'success')
      }
      onClose()
    } catch (err) {
      toast.show(apiError(err, 'Failed to send message'), 'error')
    } finally {
      setSending(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} maxWidth="max-w-lg"
      title={<span><i className="fa-solid fa-paper-plane text-blue-600 mr-2" />Send message{candidateName ? ` · ${candidateName}` : ''}</span>}>
      <div className="p-5 space-y-4">
        {/* Channel toggle */}
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setChannel('whatsapp')}
            className={`flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-semibold border transition
              ${channel === 'whatsapp' ? 'bg-emerald-50 border-emerald-300 text-emerald-700' : 'bg-white border-slate-200 text-slate-600 hover:bg-slate-50'}`}>
            <i className="fa-brands fa-whatsapp text-base" /> WhatsApp
          </button>
          <button
            type="button"
            onClick={() => setChannel('sms')}
            className={`flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-semibold border transition
              ${channel === 'sms' ? 'bg-blue-50 border-blue-300 text-blue-700' : 'bg-white border-slate-200 text-slate-600 hover:bg-slate-50'}`}>
            <i className="fa-solid fa-comment-sms text-base" /> SMS
          </button>
        </div>

        {/* Provider mode notice */}
        {status && (
          <div className={`text-[12px] rounded-lg px-3 py-2 border flex items-center gap-2
            ${providerMode === 'live' && channelEnabled
              ? 'bg-emerald-50 border-emerald-200 text-emerald-700'
              : 'bg-amber-50 border-amber-200 text-amber-700'}`}>
            <i className={`fa-solid ${providerMode === 'live' && channelEnabled ? 'fa-circle-check' : 'fa-triangle-exclamation'}`} />
            {providerMode === 'live' && channelEnabled
              ? `Live via Twilio${(channel === 'whatsapp' ? status.whatsapp_from : status.sms_from) ? ` (from ${channel === 'whatsapp' ? status.whatsapp_from : status.sms_from})` : ''}`
              : providerMode === 'live'
                ? `${channel === 'whatsapp' ? 'WhatsApp' : 'SMS'} sender not configured — will be logged as dry-run`
                : 'Twilio not configured — message will be logged (dry-run), not actually sent'}
          </div>
        )}

        <div>
          <label className="block text-[12px] font-semibold text-slate-600 mb-1">Recipient number</label>
          <input
            type="tel"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="+92 300 1234567"
            className="w-full px-3 py-2 rounded-lg border border-slate-200 text-sm focus:ring-2 focus:ring-blue-200 focus:border-blue-400 outline-none"
          />
          <p className="text-[11px] text-slate-400 mt-1">Any format — automatically normalised to international (E.164).</p>
        </div>

        <div>
          <label className="block text-[12px] font-semibold text-slate-600 mb-1">Message</label>
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={5}
            maxLength={1600}
            placeholder="Type your message…"
            className="w-full px-3 py-2 rounded-lg border border-slate-200 text-sm focus:ring-2 focus:ring-blue-200 focus:border-blue-400 outline-none resize-none"
          />
          <p className="text-[11px] text-slate-400 mt-1 text-right">{body.length}/1600</p>
        </div>
      </div>

      <div className="px-5 py-3.5 border-t border-slate-100 flex items-center justify-end gap-2">
        <button onClick={onClose} disabled={sending}
          className="px-4 py-2 rounded-lg text-sm font-semibold text-slate-600 hover:bg-slate-100 disabled:opacity-50">
          Cancel
        </button>
        <button onClick={handleSend} disabled={sending}
          className="px-4 py-2 rounded-lg text-sm font-semibold text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-60 flex items-center gap-2">
          {sending ? <><i className="fa-solid fa-spinner fa-spin" /> Sending…</> : <><i className="fa-solid fa-paper-plane" /> Send</>}
        </button>
      </div>
    </Modal>
  )
}
