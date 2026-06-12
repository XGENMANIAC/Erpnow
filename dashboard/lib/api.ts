import type {
  Conversation,
  ConversationListResponse,
  DailySummary,
  MessageListResponse,
} from './types'

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  conversations: {
    list(params?: { status?: string; limit?: number; offset?: number }) {
      const q = new URLSearchParams()
      if (params?.status) q.set('status', params.status)
      if (params?.limit != null) q.set('limit', String(params.limit))
      if (params?.offset != null) q.set('offset', String(params.offset))
      return apiFetch<ConversationListResponse>(`/api/conversations?${q}`)
    },

    get(id: number) {
      return apiFetch<Conversation>(`/api/conversations/${id}`)
    },

    messages(id: number, params?: { limit?: number; before_id?: number }) {
      const q = new URLSearchParams()
      if (params?.limit != null) q.set('limit', String(params.limit))
      if (params?.before_id != null) q.set('before_id', String(params.before_id))
      return apiFetch<MessageListResponse>(`/api/conversations/${id}/messages?${q}`)
    },

    takeover(id: number) {
      return apiFetch<{ ok: boolean; status: string }>(`/api/conversations/${id}/takeover`, {
        method: 'POST',
      })
    },

    release(id: number) {
      return apiFetch<{ ok: boolean; status: string }>(`/api/conversations/${id}/release`, {
        method: 'POST',
      })
    },

    reply(id: number, message: string) {
      return apiFetch<{ ok: boolean }>(`/api/conversations/${id}/reply`, {
        method: 'POST',
        body: JSON.stringify({ message }),
      })
    },
  },

  insights: {
    daily(date?: string) {
      const q = date ? `?target_date=${date}` : ''
      return apiFetch<DailySummary>(`/api/insights/daily${q}`)
    },
  },
}
