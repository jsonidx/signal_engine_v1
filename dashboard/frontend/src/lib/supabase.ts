/**
 * src/lib/supabase.ts — Supabase client singleton
 *
 * Environment variables (set in dashboard/frontend/.env.local):
 *   VITE_SUPABASE_URL      — https://<your-project-ref>.supabase.co
 *   VITE_SUPABASE_ANON_KEY — eyJ... (from Supabase Settings → API)
 */
import { createClient } from '@supabase/supabase-js'

const supabaseUrl  = import.meta.env.VITE_SUPABASE_URL  as string
const supabaseAnon = import.meta.env.VITE_SUPABASE_ANON_KEY as string

if (!supabaseUrl || !supabaseAnon) {
  console.warn(
    '[supabase] VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY not set — ' +
    'create dashboard/frontend/.env.local with these values.'
  )
}

export const supabase = createClient(supabaseUrl ?? '', supabaseAnon ?? '')
