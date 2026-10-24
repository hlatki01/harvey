import { createContext, useCallback, useContext, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { cn } from '@/lib/utils'

type ToastType = 'success' | 'error'
interface ToastItem { id: number; msg: string; type: ToastType }

const ToastCtx = createContext<(msg: string, type?: ToastType) => void>(() => {})
export const useToast = () => useContext(ToastCtx)

let nextId = 1

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const show = useCallback((msg: string, type: ToastType = 'success') => {
    const id = nextId++
    setItems(list => [...list, { id, msg, type }])
    setTimeout(() => setItems(list => list.filter(t => t.id !== id)), 2600)
  }, [])
  return (
    <ToastCtx.Provider value={show}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-[2000] flex flex-col gap-2" role="status" aria-live="polite">
        <AnimatePresence>
          {items.map(t => (
            <motion.div
              key={t.id}
              initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
              className={cn(
                'max-w-sm rounded-md px-3.5 py-2 text-[13px] shadow-pop',
                t.type === 'error' ? 'bg-bad text-white' : 'bg-text text-bg',
              )}
            >
              {t.msg}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastCtx.Provider>
  )
}
