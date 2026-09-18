import type { ReactNode } from 'react'
import { Sidebar } from './Sidebar'
import { Header } from './Header'

export function PageContainer({ title, subtitle, children, fill = false }: { title: string; subtitle?: string; children: ReactNode; fill?: boolean }) {
  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[var(--color-bg)]">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header title={title} subtitle={subtitle} />
        {/* fill: the page owns the remaining height and scrolls internally
            (chat-style layouts); default: the page scrolls as one column. */}
        <main className={`flex flex-1 flex-col p-6 ${fill ? 'overflow-hidden' : 'overflow-y-auto'}`}>
          {fill ? children : <div className="flex flex-col gap-5">{children}</div>}
        </main>
      </div>
    </div>
  )
}

export function LoadingState({ message = 'Loading data…' }: { message?: string }) {
  return (
    <div className="flex h-40 items-center justify-center text-sm text-[var(--color-text-muted)]">
      <div className="animate-pulse">{message}</div>
    </div>
  )
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="flex h-40 flex-col items-center justify-center gap-2 rounded-xl border border-red-200 bg-red-50 text-sm text-red-700">
      <span className="font-medium">Unable to load data</span>
      <span className="text-xs text-red-600">{message}</span>
    </div>
  )
}

export function EmptyState({ message = 'Nothing here yet.' }: { message?: string }) {
  return (
    <div className="flex h-40 items-center justify-center rounded-lg border border-dashed border-[var(--color-border)] text-sm text-[var(--color-text-muted)]">
      {message}
    </div>
  )
}
