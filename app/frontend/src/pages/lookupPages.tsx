import SimpleListPage, { FieldDef } from './SimpleListPage'

const VISA: FieldDef[] = [
  { key: 'name', label: 'Name', required: true },
  { key: 'code', label: 'Code' },
  { key: 'description', label: 'Description', type: 'textarea' },
  { key: 'is_active', label: 'Active', type: 'checkbox' },
]
const EMBASSY: FieldDef[] = [
  { key: 'name', label: 'Name', required: true },
  { key: 'country', label: 'Country' },
  { key: 'city', label: 'City' },
  { key: 'address', label: 'Address', type: 'textarea' },
  { key: 'phone', label: 'Phone' },
]
const CITY: FieldDef[] = [
  { key: 'name', label: 'Name', required: true },
  { key: 'province', label: 'Province' },
  { key: 'country', label: 'Country' },
]
const MEDICAL: FieldDef[] = [
  { key: 'name', label: 'Name', required: true },
  { key: 'city', label: 'City' },
  { key: 'address', label: 'Address', type: 'textarea' },
  { key: 'phone', label: 'Phone' },
]
const CONTACT: FieldDef[] = [
  { key: 'name', label: 'Name', required: true },
  { key: 'company', label: 'Company' },
  { key: 'email', label: 'Email' },
  { key: 'phone', label: 'Phone' },
  { key: 'note', label: 'Note', type: 'textarea' },
]
const SERVICE: FieldDef[] = [
  { key: 'name', label: 'Name', required: true },
  { key: 'amount', label: 'Amount', type: 'number' },
  { key: 'description', label: 'Description', type: 'textarea' },
  { key: 'is_active', label: 'Active', type: 'checkbox' },
]

export const VisaCategoriesPage = () => <SimpleListPage title="Visa Categories" icon="fa-solid fa-tags" endpoint="visa-categories" fields={VISA} columns={[{ key: 'name', label: 'Name' }, { key: 'code', label: 'Code' }, { key: 'description', label: 'Description' }, { key: 'is_active', label: 'Active' }]} searchPlaceholder="Search categories…" />
export const EmbassiesPage = () => <SimpleListPage title="Embassies" icon="fa-solid fa-landmark" endpoint="embassies" fields={EMBASSY} columns={[{ key: 'name', label: 'Name' }, { key: 'country', label: 'Country' }, { key: 'city', label: 'City' }, { key: 'phone', label: 'Phone' }]} searchPlaceholder="Search embassies…" />
export const CitiesPage = () => <SimpleListPage title="Cities" icon="fa-solid fa-location-dot" endpoint="cities" fields={CITY} searchPlaceholder="Search cities…" />
export const MedicalCentersPage = () => <SimpleListPage title="Medical Centers" icon="fa-solid fa-stethoscope" endpoint="medical-centers" fields={MEDICAL} columns={[{ key: 'name', label: 'Name' }, { key: 'city', label: 'City' }, { key: 'phone', label: 'Phone' }]} searchPlaceholder="Search medical centers…" />
export const ContactsPage = () => <SimpleListPage title="Contacts" icon="fa-regular fa-address-book" endpoint="contacts" fields={CONTACT} columns={[{ key: 'name', label: 'Name' }, { key: 'company', label: 'Company' }, { key: 'phone', label: 'Phone' }, { key: 'email', label: 'Email' }]} searchPlaceholder="Search contacts…" />
export const ServiceChargesPage = () => <SimpleListPage title="Service Charges" icon="fa-solid fa-dollar-sign" endpoint="service-charges" fields={SERVICE} columns={[{ key: 'name', label: 'Name' }, { key: 'amount', label: 'Amount' }, { key: 'is_active', label: 'Active' }]} searchPlaceholder="Search service charges…" />
