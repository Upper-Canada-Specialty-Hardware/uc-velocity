import * as React from "react"
import { useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"
import { Check, ChevronsUpDown, Plus, X } from "lucide-react"

export interface SearchableMultiSelectOption {
  value: string
  label: string
  description?: string
}

interface SearchableMultiSelectProps<T> {
  options: SearchableMultiSelectOption[]
  selected: string[]
  onChange: (values: string[]) => void
  placeholder?: string
  searchPlaceholder?: string
  emptyMessage?: string
  className?: string
  disabled?: boolean

  // Create feature
  allowCreate?: boolean
  createLabel?: string
  createDialogTitle?: string
  createForm?: React.ReactNode
  onCreateSuccess?: (newItem: T) => void
}

export function SearchableMultiSelect<T>({
  options,
  selected,
  onChange,
  placeholder = "Select items...",
  searchPlaceholder = "Search...",
  emptyMessage = "No items found.",
  className,
  disabled,
  allowCreate = false,
  createLabel = "Create New",
  createDialogTitle = "Create New Item",
  createForm,
  onCreateSuccess,
}: SearchableMultiSelectProps<T>) {
  const [open, setOpen] = useState(false)
  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState("")

  // Filter options based on search
  const filteredOptions = options.filter(
    (opt) =>
      opt.label.toLowerCase().includes(searchQuery.toLowerCase()) ||
      opt.description?.toLowerCase().includes(searchQuery.toLowerCase())
  )

  const handleSelect = (value: string) => {
    if (selected.includes(value)) {
      onChange(selected.filter((v) => v !== value))
    } else {
      onChange([...selected, value])
    }
  }

  const handleRemove = (value: string, e: React.MouseEvent) => {
    e.stopPropagation()
    onChange(selected.filter((v) => v !== value))
  }

  const handleCreateClick = () => {
    setOpen(false)
    setCreateDialogOpen(true)
  }

  const handleCreateFormSuccess = (newItem: T) => {
    setCreateDialogOpen(false)
    onCreateSuccess?.(newItem)
  }

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            role="combobox"
            aria-expanded={open}
            disabled={disabled}
            className={cn("w-full justify-between min-h-9 h-auto", className)}
          >
            {selected.length === 0 ? (
              <span className="text-muted-foreground">{placeholder}</span>
            ) : (
              <div className="flex flex-wrap gap-1">
                {selected.map((value) => {
                  const option = options.find((o) => o.value === value)
                  return (
                    <Badge key={value} variant="secondary" className="mr-1">
                      {option?.label || value}
                      <button
                        className="ml-1 ring-offset-background rounded-full outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
                        onClick={(e) => handleRemove(value, e)}
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </Badge>
                  )
                })}
              </div>
            )}
            <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-[var(--radix-popover-trigger-width)] p-0" align="start">
          <Command shouldFilter={false}>
            <CommandInput
              placeholder={searchPlaceholder}
              value={searchQuery}
              onValueChange={setSearchQuery}
            />
            <CommandList onWheel={(e) => e.stopPropagation()}>
              {filteredOptions.length === 0 && (
                <CommandEmpty>{emptyMessage}</CommandEmpty>
              )}

              {filteredOptions.length > 0 && (
                <CommandGroup>
                  {filteredOptions.map((option) => (
                    <CommandItem
                      key={option.value}
                      value={option.label}
                      onSelect={() => handleSelect(option.value)}
                    >
                      <Check
                        className={cn(
                          "mr-2 h-4 w-4",
                          selected.includes(option.value)
                            ? "opacity-100"
                            : "opacity-0"
                        )}
                      />
                      <div className="flex flex-col">
                        <span>{option.label}</span>
                        {option.description && (
                          <span className="text-sm text-muted-foreground">
                            {option.description}
                          </span>
                        )}
                      </div>
                    </CommandItem>
                  ))}
                </CommandGroup>
              )}

              {allowCreate && (
                <>
                  <CommandSeparator />
                  <CommandGroup>
                    <CommandItem
                      onSelect={handleCreateClick}
                      className="text-primary"
                    >
                      <Plus className="mr-2 h-4 w-4" />
                      {createLabel}
                    </CommandItem>
                  </CommandGroup>
                </>
              )}
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>

      {/* Create Dialog */}
      {allowCreate && createForm && (
        <Dialog open={createDialogOpen} onOpenChange={setCreateDialogOpen}>
          <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle>{createDialogTitle}</DialogTitle>
            </DialogHeader>
            {React.cloneElement(createForm as React.ReactElement<any>, {
              onSuccess: handleCreateFormSuccess,
              onCancel: () => setCreateDialogOpen(false),
            })}
          </DialogContent>
        </Dialog>
      )}
    </>
  )
}
