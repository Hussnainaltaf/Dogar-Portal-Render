// Stub for src/lib/api.ts used by optionsCache.check.ts.
//
// It records every request the reference-list cache issues so the check can
// assert on REQUEST COUNTS — which is the whole point of the cache. Returning a
// single canned row of every shape the loaders read keeps the projections
// (id-valued vs name-valued) testable in the same pass.
export const calls: string[] = []

export const api = {
  get(url: string, cfg?: any) {
    calls.push(url + '?' + JSON.stringify(cfg?.params || {}))
    const items: any[] = [{
      id: 1,
      company_name: 'Acme',
      file_number: '100',
      file_number_display: 'DTC/100',
      name: 'Driver',
      is_active: true,
    }]
    return Promise.resolve({ data: { total: 1, items } })
  },
}
