import { NavLink } from 'react-router-dom'

const NAV = [
  { to: '/playground', label: '🔍 根因分类' },
  { to: '/assistant', label: '🛎️ 值班助手' },
]

/**
 * 公共头部。原实现是把 nav 与 title 拼进 HTML 字符串再返回整页；
 * 现在标题/描述由页面传入，切换路由时 Header 不重新挂载。
 * children 用来塞页面自己的状态徽标（如值班助手页的知识库条数）。
 */
export default function Header({ title, desc, children }) {
  return (
    <header>
      <div>
        <h1>{title}</h1>
        <p>{desc}</p>
        {children}
      </div>
      <nav className="nav">
        {NAV.map(({ to, label }) => (
          <NavLink key={to} to={to} className={({ isActive }) => (isActive ? 'active' : undefined)}>
            {label}
          </NavLink>
        ))}
      </nav>
    </header>
  )
}
