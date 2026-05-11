import { useUser } from "@clerk/react"

/**
 * Returns true only when the signed-in user has `publicMetadata.role === "admin"`.
 *
 * Note: this is a client-side gate for hiding UI surfaces only. The backend must
 * still enforce admin-only operations independently — never trust the client.
 */
export function useIsAdmin(): boolean {
  const { isLoaded, isSignedIn, user } = useUser()
  if (!isLoaded || !isSignedIn || !user) return false
  const role = user.publicMetadata?.role
  return role === "admin"
}
