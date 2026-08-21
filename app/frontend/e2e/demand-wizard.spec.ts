import { expect, test, type BrowserContext, type Page, type Route } from '@playwright/test'

const blankForm = {
  file_number: 'DTC/786/9901', receiving_date: '2026-07-22', status: 'active', client_id: '1', client_label: 'Acme Recruiting',
  visa_number: 'V-100', bataka_number: 'B-100', visa_issue_date: '2026-07-20', visa_issue_date_hijri: '', permission_no: '', permission_date: '',
  country: 'Saudi Arabia', embassy: 'Islamabad', reference: 'SYNC-REF', notes: '', sponsor_name: 'Original sponsor', sponsor_name_arabic: '',
  sponsor_phone: '', sponsor_alt_phone: '', sponsor_address: '', sponsor_address_arabic: '',
}
const draft = (step = 3) => ({ version: 1, step, form: blankForm, positions: [{ trade: 'Electrician', quantity: '2', salary: '1800' }], attachments: [], updatedAt: Date.now() })

async function mockPortal(context: BrowserContext, demandPost?: (route: Route) => Promise<void>) {
  // Keep these UI tests deterministic: a fulfilled SSE route closes immediately,
  // which makes EventSource reconnect and activates the polling fallback forever.
  await context.addInitScript(() => {
    class MockEventSource extends EventTarget {
      static readonly CONNECTING = 0
      static readonly OPEN = 1
      static readonly CLOSED = 2
      readonly CONNECTING = 0
      readonly OPEN = 1
      readonly CLOSED = 2
      readonly readyState = 0
      readonly withCredentials = true
      constructor(readonly url: string) { super() }
      close() {}
    }
    Object.defineProperty(window, 'EventSource', { configurable: true, value: MockEventSource })
  })
  await context.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/users/me')) return route.fulfill({ json: { id: 1, name: 'Admin', email: 'admin@example.com', role: 'admin' } })
    if (url.pathname.endsWith('/users/me/permissions')) return route.fulfill({ json: { permissions: ['*'] } })
    if (url.pathname.endsWith('/demands/config/next-file-number')) return route.fulfill({ json: { display: 'DTC/786/9901' } })
    if (url.pathname.endsWith('/clients/')) return route.fulfill({ json: { items: [{ id: 1, company_name: 'Acme Recruiting' }] } })
    if (url.pathname.endsWith('/lookups/embassies')) return route.fulfill({ json: { items: [{ id: 1, name: 'Islamabad' }] } })
    if (url.pathname.endsWith('/lookups/cities')) return route.fulfill({ json: { items: [{ id: 1, name: 'Riyadh' }] } })
    if (url.pathname.endsWith('/lookups/visa-categories')) return route.fulfill({ json: { items: [{ id: 1, name: 'Electrician', is_active: true }] } })
    if (url.pathname.endsWith('/demands/') && route.request().method() === 'POST' && demandPost) return demandPost(route)
    if (url.pathname.endsWith('/demands/')) return route.fulfill({ json: { items: [], total: 0 } })
    if (url.pathname.endsWith('/realtime/status-stream')) {
      return route.fulfill({
        contentType: 'text/event-stream',
        headers: { 'cache-control': 'no-cache' },
        body: 'retry: 60000\nevent: status\ndata: {"version":"e2e"}\n\n',
      })
    }
    if (url.pathname.endsWith('/realtime/status-snapshot')) return route.fulfill({ json: { version: 'e2e' } })
    return route.fulfill({ json: { items: [], total: 0 } })
  })
}

async function openWizard(page: Page, initial = draft()) {
  await page.addInitScript((value) => localStorage.setItem('dogar:demand-wizard:draft:v1', JSON.stringify(value)), initial)
  await page.goto('/demands')
  await page.getByRole('button', { name: 'Add Demand File' }).evaluate((button: HTMLButtonElement) => button.click())
  await expect(page.getByRole('heading', { name: 'Create Demand File', level: 2 })).toBeVisible()
}

test('synchronizes draft values, wizard step, and attachment metadata across tabs', async ({ context }) => {
  test.setTimeout(60_000)
  await mockPortal(context)
  const appPage = await context.newPage()
  const peerTab = await context.newPage()
  await openWizard(appPage, draft(2))

  // A lightweight same-origin peer exercises the browser's real storage event
  // without booting a second copy of the large portal SPA in constrained CI.
  await peerTab.goto('/api/e2e-peer-tab')
  await appPage.getByRole('textbox', { name: 'Sponsor name', exact: true }).fill('Synced sponsor')
  await expect.poll(() => peerTab.evaluate(() => {
    const value = JSON.parse(localStorage.getItem('dogar:demand-wizard:draft:v1') || '{}')
    return value.form?.sponsor_name
  })).toBe('Synced sponsor')

  await peerTab.evaluate(() => {
    const key = 'dogar:demand-wizard:draft:v1'
    const value = JSON.parse(localStorage.getItem(key) || '{}')
    const attachment = { id: 'passport.pdf:12:1', name: 'passport.pdf', size: 12, type: 'application/pdf', lastModified: 1 }
    localStorage.setItem(key, JSON.stringify({ ...value, step: 3, attachments: [attachment], updatedAt: Date.now() }))
  })
  await expect(appPage.getByText('Visa positions', { exact: true }).first()).toBeVisible()

  await peerTab.evaluate(() => {
    const key = 'dogar:demand-wizard:draft:v1'
    const value = JSON.parse(localStorage.getItem(key) || '{}')
    localStorage.setItem(key, JSON.stringify({ ...value, step: 2, form: { ...value.form, sponsor_name: 'Remote sponsor' }, updatedAt: Date.now() }))
  })
  await expect(appPage.getByRole('textbox', { name: 'Sponsor name', exact: true })).toHaveValue('Remote sponsor')
  await expect(appPage.getByTestId('attachment-metadata-list')).toContainText('passport.pdf')
})

test('maps structured field issues inline, focuses first invalid field, preserves state, and creates no toast', async ({ context }) => {
  await mockPortal(context, async (route) => route.fulfill({ status: 422, json: { detail: [
    { loc: ['body', 'visa_number'], msg: 'Visa number is already registered', type: 'value_error' },
    { loc: ['body', 'positions', 0, 'quantity'], msg: 'Quantity is invalid', type: 'value_error' },
  ] } }))
  const page = await context.newPage()
  await openWizard(page)
  await page.getByTestId('demand-submit').click()
  const visa = page.locator('#demand-visa_number')
  await expect(visa).toBeFocused()
  await expect(page.getByRole('alert')).toContainText('Visa number is already registered')
  await expect(page.getByTestId('toast-error')).toHaveCount(0)
  await expect(page.locator('#demand-file_number')).toHaveValue('DTC/786/9901')
  await visa.fill('V-101')
  await expect(page.getByText('Visa number is already registered')).toHaveCount(0)
})

test('shows exactly one toast for a non-field backend failure and no inline field alerts', async ({ context }) => {
  await mockPortal(context, async (route) => route.fulfill({ status: 500, json: { detail: 'Database temporarily unavailable' } }))
  const page = await context.newPage()
  await openWizard(page)
  await page.getByTestId('demand-submit').click()
  await expect(page.getByTestId('toast-error')).toHaveCount(1)
  await expect(page.getByTestId('toast-error')).toContainText('Database temporarily unavailable')
  await expect(page.locator('[data-field-error]')).toHaveCount(0)
})

test('exposes pending loading state, disables submit, and sends only one backend request', async ({ context }) => {
  let submissions = 0
  await mockPortal(context, async (route) => {
    submissions += 1
    await new Promise((resolve) => setTimeout(resolve, 500))
    await route.fulfill({ status: 201, json: { id: 42 } })
  })
  const page = await context.newPage()
  await openWizard(page)
  const submit = page.getByTestId('demand-submit')
  await submit.evaluate((button: HTMLButtonElement) => {
    button.click()
    button.click()
  })
  await expect(submit).toBeDisabled()
  await expect(submit).toContainText('Creating demand file')
  await page.waitForTimeout(700)
  expect(submissions).toBe(1)
})
