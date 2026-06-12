import type { Metadata } from 'next'
import Link from 'next/link'
import './globals.css'

export const metadata: Metadata = {
  title: 'DEWMIX CRM',
  description: 'Agentic CRM dashboard for DEWMIX Hardware',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full">
      <body className="h-full flex bg-gray-50">
        {/* Sidebar */}
        <aside className="w-56 flex-none bg-gray-900 flex flex-col">
          <div className="px-4 py-5 border-b border-gray-700">
            <p className="text-white font-bold text-lg tracking-tight">DEWMIX</p>
            <p className="text-gray-400 text-xs mt-0.5">Hardware CRM</p>
          </div>
          <nav className="flex-1 px-2 py-4 space-y-1">
            <NavLink href="/conversations">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
              </svg>
              Conversations
            </NavLink>
            <NavLink href="/insights">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
              </svg>
              Insights
            </NavLink>
          </nav>
          <div className="px-4 py-3 border-t border-gray-700">
            <p className="text-gray-500 text-xs">Phase 5</p>
          </div>
        </aside>

        {/* Main content */}
        <main className="flex-1 overflow-hidden flex flex-col min-w-0">
          {children}
        </main>
      </body>
    </html>
  )
}

function NavLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      className="flex items-center gap-2.5 px-3 py-2 rounded-md text-sm text-gray-300
                 hover:bg-gray-800 hover:text-white transition-colors"
    >
      {children}
    </Link>
  )
}
