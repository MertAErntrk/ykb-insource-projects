import { BrowserRouter } from "react-router"
import { ToastProvider } from "./components/ui/index.ts"
import { AppRoutes } from "./router.tsx"
import { AuthProvider } from "./state/auth.tsx"
import { WizardProvider } from "./state/wizard.tsx"

export default function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <WizardProvider>
          <AuthProvider>
            <AppRoutes />
          </AuthProvider>
        </WizardProvider>
      </ToastProvider>
    </BrowserRouter>
  )
}
