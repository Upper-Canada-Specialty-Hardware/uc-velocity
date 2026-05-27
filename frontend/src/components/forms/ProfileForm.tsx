import { useState, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { api } from "@/api/client"
import type { Profile, ProfileCreate, ProfileType, ContactCreate, PhoneType, ContactPhoneCreate } from "@/types"
import { Plus, Trash2, Phone, User } from "lucide-react"

// Helper to generate temporary IDs for new items
const generateTempId = () => `temp-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`

interface PhoneFormData {
  tempId: string
  type: PhoneType
  number: string
}

interface ContactFormData {
  id?: number // Existing contact ID (undefined for new)
  tempId: string
  name: string
  job_title: string
  email: string
  phone_numbers: PhoneFormData[]
}

interface ProfileFormProps {
  profile?: Profile // If provided, we're editing; otherwise creating
  defaultType?: ProfileType
  onSuccess?: (profile?: Profile) => void
  onCancel?: () => void
}

export function ProfileForm({ profile, defaultType = "customer", onSuccess, onCancel }: ProfileFormProps) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isEditing = !!profile

  // Profile fields
  const [name, setName] = useState("")
  const [type, setType] = useState<ProfileType>(defaultType)
  const [pst, setPst] = useState("")
  const [address, setAddress] = useState("")
  const [postalCode, setPostalCode] = useState("")
  const [defaultDiscountPercent, setDefaultDiscountPercent] = useState("")

  // Contacts state
  const [contacts, setContacts] = useState<ContactFormData[]>([])

  const createEmptyContact = (): ContactFormData => ({
    tempId: generateTempId(),
    name: "",
    job_title: "",
    email: "",
    phone_numbers: []
  })

  const createEmptyPhone = (): PhoneFormData => ({
    tempId: generateTempId(),
    type: "work",
    number: ""
  })

  // Initialize form
  useEffect(() => {
    if (profile) {
      setName(profile.name)
      setType(profile.type)
      setPst(profile.pst)
      setAddress(profile.address)
      setPostalCode(profile.postal_code)
      setDefaultDiscountPercent(profile.default_discount_percent?.toString() || "")
      setContacts(profile.contacts.map(c => ({
        id: c.id,
        tempId: `existing-${c.id}`,
        name: c.name,
        job_title: c.job_title || "",
        email: c.email || "",
        phone_numbers: c.phone_numbers.map(p => ({
          tempId: `existing-${p.id}`,
          type: p.type,
          number: p.number
        }))
      })))
    } else {
      // Start with one empty contact
      setContacts([createEmptyContact()])
    }
  }, [profile])

  // Contact management functions
  const addContact = () => {
    setContacts([...contacts, createEmptyContact()])
  }

  const removeContact = (tempId: string) => {
    if (contacts.length <= 1) {
      setError("At least one contact is required")
      return
    }
    setContacts(contacts.filter(c => c.tempId !== tempId))
    setError(null)
  }

  const updateContact = (tempId: string, field: keyof ContactFormData, value: string) => {
    setContacts(contacts.map(c =>
      c.tempId === tempId ? { ...c, [field]: value } : c
    ))
  }

  // Phone management functions
  const addPhone = (contactTempId: string) => {
    setContacts(contacts.map(c =>
      c.tempId === contactTempId
        ? { ...c, phone_numbers: [...c.phone_numbers, createEmptyPhone()] }
        : c
    ))
  }

  const removePhone = (contactTempId: string, phoneTempId: string) => {
    setContacts(contacts.map(c =>
      c.tempId === contactTempId
        ? { ...c, phone_numbers: c.phone_numbers.filter(p => p.tempId !== phoneTempId) }
        : c
    ))
  }

  const updatePhone = (contactTempId: string, phoneTempId: string, field: keyof PhoneFormData, value: string) => {
    setContacts(contacts.map(c =>
      c.tempId === contactTempId
        ? {
            ...c,
            phone_numbers: c.phone_numbers.map(p =>
              p.tempId === phoneTempId ? { ...p, [field]: value } : p
            )
          }
        : c
    ))
  }

  // Validation
  const isFormValid = () => {
    if (!name.trim() || !pst.trim() || !address.trim() || !postalCode.trim()) return false
    if (contacts.length === 0) return false
    return contacts.every(c => c.name.trim() !== "")
  }

  // Convert form data to API format
  const buildContactCreate = (contact: ContactFormData): ContactCreate => ({
    name: contact.name.trim(),
    job_title: contact.job_title.trim() || undefined,
    email: contact.email.trim() || undefined,
    phone_numbers: contact.phone_numbers.map(p => ({
      type: p.type,
      number: p.number.trim()
    })).filter(p => p.number !== "") // Only include non-empty phone numbers
  })

  // Submit handler
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError(null)

    try {
      if (isEditing && profile) {
        // Update profile fields
        await api.profiles.update(profile.id, {
          name: name.trim(),
          type,
          pst: pst.trim(),
          address: address.trim(),
          postal_code: postalCode.trim(),
          default_discount_percent: defaultDiscountPercent ? parseFloat(defaultDiscountPercent) : undefined,
        })

        // Sync contacts: delete removed, update existing, add new
        const existingContactIds = new Set(profile.contacts.map(c => c.id))
        const currentContactIds = new Set(contacts.filter(c => c.id).map(c => c.id!))

        // Delete contacts that were removed
        for (const contactId of existingContactIds) {
          if (!currentContactIds.has(contactId)) {
            await api.profiles.deleteContact(profile.id, contactId)
          }
        }

        // Update existing contacts and add new ones
        for (const contact of contacts) {
          if (contact.id) {
            // Update existing contact
            await api.profiles.updateContact(profile.id, contact.id, {
              name: contact.name.trim(),
              job_title: contact.job_title.trim() || undefined,
              email: contact.email.trim() || undefined,
              phone_numbers: contact.phone_numbers.map(p => ({
                type: p.type,
                number: p.number.trim()
              })).filter(p => p.number !== "")
            })
          } else {
            // Add new contact
            await api.profiles.addContact(profile.id, buildContactCreate(contact))
          }
        }
      } else {
        // Create new profile with contacts
        const profileData: ProfileCreate = {
          name: name.trim(),
          type,
          pst: pst.trim(),
          address: address.trim(),
          postal_code: postalCode.trim(),
          default_discount_percent: defaultDiscountPercent ? parseFloat(defaultDiscountPercent) : undefined,
          contacts: contacts.map(buildContactCreate)
        }
        const created = await api.profiles.create(profileData)
        onSuccess?.(created)
        return
      }
      onSuccess?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to ${isEditing ? 'update' : 'create'} profile`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6 max-h-[70vh] overflow-y-auto pr-2">
      {/* Error display */}
      {error && (
        <div className="text-sm text-destructive bg-destructive/10 p-3 rounded-md border border-destructive/20">
          {error}
        </div>
      )}

      {/* Profile Information Section */}
      <div className="space-y-4">
        <h3 className="text-lg font-medium">Profile Information</h3>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="name">{type === "customer" ? "Company Name" : "Supplier Name"} *</Label>
            <Input
              id="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g., Acme Corporation"
              required
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="type">Type *</Label>
            <Select value={type} onValueChange={(v) => setType(v as ProfileType)}>
              <SelectTrigger>
                <SelectValue placeholder="Select type" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="customer">Customer</SelectItem>
                <SelectItem value="vendor">Vendor</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="space-y-2">
          <Label htmlFor="pst">PST (Provincial Tax Number) *</Label>
          <Input
            id="pst"
            value={pst}
            onChange={(e) => setPst(e.target.value)}
            placeholder="e.g., PST-1234567"
            required
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="address">Address *</Label>
          <Input
            id="address"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            placeholder="e.g., 123 Main Street, Suite 100"
            required
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="postalCode">Postal Code *</Label>
          <Input
            id="postalCode"
            value={postalCode}
            onChange={(e) => setPostalCode(e.target.value)}
            placeholder="e.g., V6B 1A1"
            required
          />
        </div>

        {/* Default Discount — only for vendors */}
        {type === "vendor" && (
          <div className="space-y-2">
            <Label htmlFor="defaultDiscount">Default Discount (%)</Label>
            <Input
              id="defaultDiscount"
              type="number"
              step="0.01"
              min="0"
              max="100"
              value={defaultDiscountPercent}
              onChange={(e) => setDefaultDiscountPercent(e.target.value)}
              placeholder="0"
            />
            <p className="text-xs text-muted-foreground">
              Applied to all parts from this vendor unless overridden per-part.
            </p>
          </div>
        )}
      </div>

      <Separator />

      {/* Contacts Section */}
      <div className="space-y-4">
        <div className="flex justify-between items-center">
          <h3 className="text-lg font-medium">Contacts (minimum 1 required)</h3>
          <Button type="button" variant="outline" size="sm" onClick={addContact}>
            <Plus className="h-4 w-4 mr-1" /> Add Contact
          </Button>
        </div>

        {contacts.map((contact, index) => (
          <Card key={contact.tempId}>
            <CardHeader className="pb-3">
              <div className="flex justify-between items-center">
                <CardTitle className="text-base flex items-center gap-2">
                  <User className="h-4 w-4" />
                  Contact {index + 1}
                </CardTitle>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => removeContact(contact.tempId)}
                  disabled={contacts.length <= 1}
                  className="text-destructive hover:text-destructive"
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>Contact Name *</Label>
                  <Input
                    value={contact.name}
                    onChange={(e) => updateContact(contact.tempId, 'name', e.target.value)}
                    placeholder="e.g., John Smith"
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label>Job Title</Label>
                  <Input
                    value={contact.job_title}
                    onChange={(e) => updateContact(contact.tempId, 'job_title', e.target.value)}
                    placeholder="e.g., Project Manager"
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label>Email</Label>
                <Input
                  type="email"
                  value={contact.email}
                  onChange={(e) => updateContact(contact.tempId, 'email', e.target.value)}
                  placeholder="e.g., john@example.com"
                />
              </div>

              {/* Phone Numbers */}
              <div className="space-y-2">
                <div className="flex justify-between items-center">
                  <Label className="flex items-center gap-2">
                    <Phone className="h-4 w-4" />
                    Phone Numbers
                  </Label>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => addPhone(contact.tempId)}
                  >
                    <Plus className="h-4 w-4 mr-1" /> Add Phone
                  </Button>
                </div>

                {contact.phone_numbers.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No phone numbers added</p>
                ) : (
                  <div className="space-y-2">
                    {contact.phone_numbers.map((phone) => (
                      <div key={phone.tempId} className="flex gap-2 items-center">
                        <Select
                          value={phone.type}
                          onValueChange={(v) => updatePhone(contact.tempId, phone.tempId, 'type', v)}
                        >
                          <SelectTrigger className="w-28">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="work">Work</SelectItem>
                            <SelectItem value="mobile">Mobile</SelectItem>
                          </SelectContent>
                        </Select>
                        <Input
                          value={phone.number}
                          onChange={(e) => updatePhone(contact.tempId, phone.tempId, 'number', e.target.value)}
                          placeholder="(555) 123-4567"
                          className="flex-1"
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => removePhone(contact.tempId, phone.tempId)}
                          className="text-destructive hover:text-destructive"
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Form Actions */}
      <div className="flex justify-end gap-2 pt-4 sticky bottom-0 bg-background pb-2">
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={loading || !isFormValid()}>
          {loading ? (isEditing ? "Saving..." : "Creating...") : (isEditing ? "Save Changes" : "Create Profile")}
        </Button>
      </div>
    </form>
  )
}
