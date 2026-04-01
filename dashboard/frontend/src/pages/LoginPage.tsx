/**
 * src/pages/LoginPage.tsx — Email/password + magic link login
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../lib/AuthContext'

type Mode = 'signin' | 'signup' | 'magic'

export function LoginPage() {
  const { signIn, signUp, sendMagicLink } = useAuth()
  const navigate = useNavigate()

  const [mode,     setMode]     = useState<Mode>('signin')
  const [email,    setEmail]    = useState('')
  const [password, setPassword] = useState('')
  const [error,    setError]    = useState<string | null>(null)
  const [info,     setInfo]     = useState<string | null>(null)
  const [busy,     setBusy]     = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setInfo(null)
    setBusy(true)

    try {
      if (mode === 'magic') {
        const { error } = await sendMagicLink(email)
        if (error) setError(error)
        else setInfo('Magic link sent — check your email.')
      } else if (mode === 'signup') {
        const { error } = await signUp(email, password)
        if (error) setError(error)
        else setInfo('Account created — check your email to confirm.')
      } else {
        const { error } = await signIn(email, password)
        if (error) setError(error)
        else navigate('/')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        {/* Logo / title */}
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold text-white tracking-tight">Signal Engine</h1>
          <p className="text-gray-400 text-sm mt-1">Quantitative trading signals</p>
        </div>

        {/* Card */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 shadow-xl">
          {/* Mode tabs */}
          <div className="flex gap-1 mb-6 bg-gray-800 rounded-lg p-1">
            {(['signin', 'signup', 'magic'] as Mode[]).map((m) => (
              <button
                key={m}
                onClick={() => { setMode(m); setError(null); setInfo(null) }}
                className={`flex-1 text-xs py-1.5 rounded-md font-medium transition-colors ${
                  mode === m
                    ? 'bg-blue-600 text-white'
                    : 'text-gray-400 hover:text-white'
                }`}
              >
                {m === 'signin' ? 'Sign in' : m === 'signup' ? 'Sign up' : 'Magic link'}
              </button>
            ))}
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-xs text-gray-400 mb-1">Email</label>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2
                           text-sm text-white placeholder-gray-500 focus:outline-none
                           focus:border-blue-500 transition-colors"
                placeholder="you@example.com"
              />
            </div>

            {mode !== 'magic' && (
              <div>
                <label className="block text-xs text-gray-400 mb-1">Password</label>
                <input
                  type="password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2
                             text-sm text-white placeholder-gray-500 focus:outline-none
                             focus:border-blue-500 transition-colors"
                  placeholder="••••••••"
                />
              </div>
            )}

            {error && (
              <p className="text-red-400 text-xs bg-red-900/30 border border-red-800 rounded-lg px-3 py-2">
                {error}
              </p>
            )}
            {info && (
              <p className="text-green-400 text-xs bg-green-900/30 border border-green-800 rounded-lg px-3 py-2">
                {info}
              </p>
            )}

            <button
              type="submit"
              disabled={busy}
              className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white
                         font-medium text-sm py-2.5 rounded-lg transition-colors"
            >
              {busy ? 'Please wait…' : mode === 'signin' ? 'Sign in' : mode === 'signup' ? 'Create account' : 'Send magic link'}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
