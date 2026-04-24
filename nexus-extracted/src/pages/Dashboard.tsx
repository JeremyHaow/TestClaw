import React from "react";
import { 
  LineChart, Line, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell
} from "recharts";
import { motion } from "motion/react";
import { Activity, Beaker, FileCode, CheckCircle2, AlertTriangle, Clock, MonitorPlay } from "lucide-react";
import { cn } from "@/src/lib/utils";

const data = [
  { name: "Mon", pass: 85, total: 100 },
  { name: "Tue", pass: 92, total: 100 },
  { name: "Wed", pass: 78, total: 100 },
  { name: "Thu", pass: 95, total: 100 },
  { name: "Fri", pass: 88, total: 100 },
  { name: "Sat", pass: 98, total: 100 },
  { name: "Sun", pass: 96, total: 100 },
];

const aiStats = [
  { name: "Boundary", value: 45 },
  { name: "Exploratory", value: 30 },
  { name: "Regression", value: 25 },
];

const COLORS = ["#3B82F6", "#10B981", "#8B5CF6"];

export const Dashboard: React.FC = () => {
  return (
    <div className="space-y-8 pb-12">
      <div className="flex flex-col gap-1">
        <h2 className="text-2xl font-bold tracking-tight text-gray-900">System Overview</h2>
        <p className="text-gray-500">Real-time telemetry and AI execution metrics.</p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        {[
          { label: "测试通过率", value: "98.42%", icon: CheckCircle2, color: "text-blue-600", bg: "bg-blue-50", trend: "+1.2%", trendColor: "text-emerald-600" },
          { label: "AI 生成用例", value: "1,428", icon: FileCode, color: "text-gray-700", bg: "bg-gray-100", trend: "Coverage: 82%", trendColor: "text-blue-600" },
          { label: "Active Agents", value: "Stable", icon: Activity, color: "text-indigo-600", bg: "bg-indigo-50", trend: "12 nodes online", trendColor: "text-gray-500" },
          { label: "Execution Time", value: "120ms", icon: Clock, color: "text-amber-600", bg: "bg-amber-50", trend: "p95 latency", trendColor: "text-gray-500" },
        ].map((stat, i) => (
          <motion.div
            key={stat.label}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.1 }}
            className="bg-white p-6 rounded-xl border border-gray-200 shadow-sm transition-all hover:shadow-md group"
          >
            <div className="flex items-center justify-between mb-4">
              <div className={`p-2.5 rounded-lg ${stat.bg} ${stat.color} transition-transform group-hover:scale-110`}>
                <stat.icon size={20} />
              </div>
              <span className={`text-xs font-bold ${stat.trendColor}`}>{stat.trend}</span>
            </div>
            <div>
              <p className="text-[10px] font-bold text-gray-400 uppercase tracking-widest">{stat.label}</p>
              <h3 className="text-3xl font-light text-gray-900 mt-1">{stat.value}</h3>
            </div>
          </motion.div>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
        {/* Pass Rate Chart */}
        <motion.div 
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="lg:col-span-2 bg-white border border-gray-200 rounded-xl shadow-sm p-6 overflow-hidden h-[420px]"
        >
          <div className="flex items-center justify-between mb-8">
            <h3 className="font-semibold text-gray-900">Success Rate History</h3>
            <div className="flex items-center gap-3 text-[10px] font-bold text-gray-400 uppercase tracking-widest">
              <div className="flex items-center gap-1.5">
                <div className="w-2 h-2 rounded-full bg-blue-500" /> Weekly Pass Rate
              </div>
            </div>
          </div>
          <div className="h-[300px]">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data} margin={{ top: 10, right: 10, left: -25, bottom: 0 }}>
                <defs>
                  <linearGradient id="colorPass" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#2563EB" stopOpacity={0.1}/>
                    <stop offset="95%" stopColor="#2563EB" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#F3F4F6" vertical={false} />
                <XAxis 
                  dataKey="name" 
                  stroke="#9CA3AF" 
                  fontSize={11} 
                  tickLine={false} 
                  axisLine={false} 
                  dy={10}
                />
                <YAxis 
                  stroke="#9CA3AF" 
                  fontSize={11} 
                  tickLine={false} 
                  axisLine={false} 
                  tickFormatter={(value) => `${value}%`}
                />
                <Tooltip 
                  contentStyle={{ backgroundColor: "#FFFFFF", border: "1px solid #E5E7EB", borderRadius: "8px", boxShadow: "0 4px 6px -1px rgb(0 0 0 / 0.1)" }}
                  itemStyle={{ color: "#2563EB", fontWeight: "bold" }}
                />
                <Area type="monotone" dataKey="pass" stroke="#2563EB" strokeWidth={2.5} fillOpacity={1} fill="url(#colorPass)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </motion.div>

        {/* AI Distribution */}
        <motion.div 
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.1 }}
          className="bg-white border border-gray-200 rounded-xl shadow-sm p-6 overflow-hidden h-[420px] flex flex-col"
        >
          <h3 className="font-semibold text-gray-900 mb-8">AI Generation Mix</h3>
          <div className="flex-1 min-h-0">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={aiStats} layout="vertical" margin={{ left: -15, right: 15 }}>
                <XAxis type="number" hide />
                <YAxis dataKey="name" type="category" stroke="#9CA3AF" fontSize={11} tickLine={false} axisLine={false} />
                <Tooltip 
                  cursor={{ fill: "#F9FAFB" }}
                  contentStyle={{ backgroundColor: "#FFFFFF", border: "1px solid #E5E7EB", borderRadius: "8px" }}
                />
                <Bar dataKey="value" barSize={12} radius={[0, 4, 4, 0]}>
                  {aiStats.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={index === 0 ? "#2563EB" : "#9CA3AF"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-8 space-y-3">
            {aiStats.map((stat, i) => (
              <div key={stat.name} className="flex items-center justify-between text-xs">
                <span className="text-gray-500 font-medium uppercase tracking-tight">{stat.name}</span>
                <span className="font-bold text-gray-900">{stat.value}%</span>
              </div>
            ))}
          </div>
        </motion.div>
      </div>

      {/* Recent Activity */}
      <div className="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
          <h3 className="font-semibold text-gray-900">最近执行记录</h3>
          <button className="text-xs font-bold text-blue-600 hover:underline px-3 py-1">查看全部</button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="bg-gray-50 text-gray-500 border-b border-gray-100">
                <th className="px-6 py-3 font-semibold uppercase tracking-wider text-[10px]">测试名称</th>
                <th className="px-6 py-3 font-semibold uppercase tracking-wider text-[10px]">类型</th>
                <th className="px-6 py-3 font-semibold uppercase tracking-wider text-[10px]">耗时</th>
                <th className="px-6 py-3 font-semibold uppercase tracking-wider text-[10px]">状态</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {[
                { name: "Auth Endpoint Stress Test", time: "m ago", latency: "120ms", status: "Success", type: "API" },
                { name: "Checkout Flow UI Scan", time: "15m ago", latency: "45s", status: "Warning", type: "UI" },
                { name: "User Profile Edge Case Gen", time: "1h ago", latency: "1.2s", status: "Success", type: "Case" },
                { name: "Database Proxy Latency", time: "3h ago", latency: "2.4s", status: "Error", type: "System" },
              ].map((item, i) => (
                <tr key={i} className="hover:bg-gray-50 transition-colors group">
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-3">
                       <div className={cn(
                         "p-1.5 rounded-lg",
                         item.type === "API" ? "bg-blue-50 text-blue-600" :
                         item.type === "UI" ? "bg-indigo-50 text-indigo-600" : "bg-gray-100 text-gray-500"
                       )}>
                         {item.type === "API" ? <Beaker size={14} /> : <MonitorPlay size={14} />}
                       </div>
                       <span className="font-medium text-gray-900">{item.name}</span>
                    </div>
                  </td>
                  <td className="px-6 py-4 text-gray-500 text-xs font-medium uppercase">{item.type} Testing</td>
                  <td className="px-6 py-4 text-gray-400 font-mono text-xs">{item.latency}</td>
                  <td className="px-6 py-4">
                     <span className={cn(
                       "px-2 py-0.5 text-[10px] font-bold rounded-full border",
                       item.status === 'Success' ? "bg-emerald-50 text-emerald-700 border-emerald-100" :
                       item.status === 'Warning' ? "bg-amber-50 text-amber-700 border-amber-100" : "bg-red-50 text-red-700 border-red-100"
                     )}>
                       {item.status.toUpperCase()}
                     </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
