import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import LaborForecasting from './pages/LaborForecasting'
import AIRecommendations from './pages/AIRecommendations'
import AIAssistant from './pages/AIAssistant'
import DataQuality from './pages/DataQuality'
import Login from './pages/Login'
import { LABOR_FORECASTING_HOME } from './components/layout/Sidebar'
import { AuthProvider } from './auth/AuthContext'
import { RequireAuth } from './auth/RequireAuth'

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          {/* Everything below the guard: no valid session, no application. */}
          <Route
            path="*"
            element={
              <RequireAuth>
                <Routes>
                  <Route path="/" element={<Navigate to="/data-quality" replace />} />
                  <Route path="/labor-forecasting" element={<Navigate to={`/labor-forecasting/${LABOR_FORECASTING_HOME}`} replace />} />
                  <Route path="/labor-forecasting/:section" element={<LaborForecasting />} />
                  <Route path="/ai-recommendations" element={<AIRecommendations />} />
                  <Route path="/ai-assistant" element={<AIAssistant />} />
                  <Route path="/model-performance" element={<Navigate to="/labor-forecasting/model-performance" replace />} />
                  <Route path="/data-quality" element={<DataQuality />} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </RequireAuth>
            }
          />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
