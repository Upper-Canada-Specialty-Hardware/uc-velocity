import type { Part } from '@/types'

/** The fields of a PO line (committed or staged) that decide what it reads as. */
interface POLineLike {
  item_type: string
  part?: Part | null
  description?: string | null
}

/**
 * The description text of a PO part line: its own text, else the inventory text.
 * Part lines store their own description, so purchasing can reword it for one PO
 * (issue #283); older Velocity lines have none and still show the inventory text.
 * @param line - A committed or staged PO line.
 * @param description - Optional text to use instead of the line's stored text (a staged edit).
 * @returns The text to show and edit, or "" when there is nothing to show.
 */
export function getPOLineDescriptionText(line: POLineLike, description?: string): string {
  const text = (description ?? line.description ?? '').trim() // staged edit wins over stored text
  if (line.item_type === 'part' && line.part) return text || line.part.description // blank -> inventory
  return text
}

/**
 * The full label of a PO line as screens and PDFs print it.
 * Part lines read "part number - description"; misc lines read their own text.
 * @param line - A committed or staged PO line.
 * @param fallback - Label when the line has no text at all.
 * @param description - Optional text to use instead of the line's stored text (a staged edit).
 * @returns The line's label.
 */
export function getPOLineLabel(line: POLineLike, fallback: string, description?: string): string {
  const text = getPOLineDescriptionText(line, description)
  if (line.item_type === 'part' && line.part) return `${line.part.part_number} - ${text}` // number stays the link to inventory
  return text || fallback
}
