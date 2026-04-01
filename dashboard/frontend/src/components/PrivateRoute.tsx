/**
 * src/components/PrivateRoute.tsx — Redirect to /login if unauthenticated
 */
import { Navigate } from 'react-router-dom'
import { useAuth } from '../lib/AuthContext'
import type { ReactNode } from 'react'

export function PrivateRoute({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center">
        <div className="text-gray-400 text-sm">Loading…</div>
      </div>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}
