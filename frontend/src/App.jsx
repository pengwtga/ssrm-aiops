import { Navigate, Route, Routes } from 'react-router-dom'
import Playground from './pages/Playground.jsx'
import Assistant from './pages/Assistant.jsx'

// 路由表：原服务端渲染是两个独立 HTML 页面（/playground、/assistant），
// 改造后合成一个 SPA，切换页面不再整页刷新，公共头部只渲染一次。
export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/playground" replace />} />
      <Route path="/playground" element={<Playground />} />
      <Route path="/assistant" element={<Assistant />} />
      <Route path="*" element={<Navigate to="/playground" replace />} />
    </Routes>
  )
}
