import React, { useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Plus, Play, ChevronRight, Hash, Globe, Shield, Edit3, Trash2, Search, Filter, Terminal } from "lucide-react";
import { cn } from "@/src/lib/utils";

export const ApiTesting: React.FC = () => {
  const [activeEnv, setActiveEnv] = useState("Production");
  
  const endpoints = [
    { id: 1, name: "Login User", method: "POST", path: "/api/v1/auth/login", status: "passed", time: "184ms" },
    { id: 2, name: "Get Profile", method: "GET", path: "/api/v1/user/me", status: "passed", time: "42ms" },
    { id: 3, name: "Update Cart", method: "PUT", path: "/api/v1/orders/cart", status: "failed", time: "512ms" },
    { id: 4, name: "Fetch Analytics", method: "GET", path: "/api/v1/stats", status: "passed", time: "1.2s" },
  ];

  return (
    <div className="space-y-8 pb-12">
      <div className="flex items-center justify-between">
        <div className="flex flex-col gap-1">
          <h2 className="text-2xl font-bold tracking-tight text-gray-900">API Test Execution</h2>
          <p className="text-gray-500 text-sm">Manage endpoints, environments and execute automated validation suites.</p>
        </div>
        <div className="flex gap-2">
          <button className="flex items-center gap-2 px-4 py-2 bg-white border border-gray-200 hover:bg-gray-50 rounded-lg text-xs font-bold transition-all shadow-sm">
            <Plus size={14} /> New Endpoint
          </button>
          <button className="flex items-center gap-2 px-5 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-bold transition-all shadow-md shadow-blue-600/10 active:scale-95">
            <Play size={14} fill="currentColor" /> Run All Tests
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        {/* Environment & Collections */}
        <div className="lg:col-span-4 space-y-6">
          <section className="bg-white border border-gray-200 rounded-xl shadow-sm p-5 space-y-5">
            <h3 className="text-[10px] font-bold uppercase tracking-widest text-gray-400">Environment</h3>
            <div className="space-y-1">
              {["Development", "Staging", "Production"].map(env => (
                <button
                  key={env}
                  onClick={() => setActiveEnv(env)}
                  className={cn(
                    "w-full flex items-center justify-between p-2.5 rounded-lg border transition-all text-sm",
                    activeEnv === env ? "bg-blue-50 border-blue-200 text-blue-700 font-semibold" : "bg-white border-transparent text-gray-500 hover:bg-gray-50"
                  )}
                >
                  <div className="flex items-center gap-2">
                    <Globe size={14} className={activeEnv === env ? "text-blue-600" : "text-gray-400"} />
                    {env}
                  </div>
                  {activeEnv === env && <div className="w-1.5 h-1.5 rounded-full bg-blue-600" />}
                </button>
              ))}
            </div>
          </section>

          <section className="bg-white border border-gray-200 rounded-xl shadow-sm p-5 space-y-5">
            <h3 className="text-[10px] font-bold uppercase tracking-widest text-gray-400">Variables</h3>
            <div className="space-y-4">
              {[
                { k: "BASE_URL", v: "https://api.nexus.ai", type: "env" },
                { k: "API_KEY", v: "••••••••••••", type: "secret" },
                { k: "TIMEOUT", v: "5000ms", type: "env" },
              ].map(v => (
                <div key={v.k} className="space-y-1.5">
                  <div className="flex items-center gap-2">
                    {v.type === 'secret' ? <Shield size={12} className="text-amber-500" /> : <Hash size={12} className="text-blue-500" />}
                    <span className="text-[10px] font-mono font-bold text-gray-400">{v.k}</span>
                  </div>
                  <div className="bg-gray-50 border border-gray-100 px-3 py-1.5 rounded-lg text-xs font-mono text-gray-600 truncate">
                    {v.v}
                  </div>
                </div>
              ))}
              <button className="w-full py-2 bg-gray-50 border border-transparent hover:bg-gray-100 rounded-lg text-[10px] uppercase tracking-widest font-bold text-gray-500 transition-colors">
                Manage Variables
              </button>
            </div>
          </section>
        </div>

        {/* Endpoint List */}
        <div className="lg:col-span-8 space-y-4">
          <div className="flex items-center gap-4 bg-white p-2 rounded-xl border border-gray-200 shadow-sm">
            <div className="flex-1 flex items-center gap-3 px-3">
              <Search size={16} className="text-gray-300" />
              <input type="text" placeholder="Filter endpoints..." className="bg-transparent border-none outline-none text-sm w-full placeholder:text-gray-400" />
            </div>
            <button className="p-2 hover:bg-gray-50 rounded-lg text-gray-400">
              <Filter size={16} />
            </button>
          </div>

          <div className="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead>
                  <tr className="bg-gray-50 text-gray-400 border-b border-gray-100">
                    <th className="px-6 py-3.5 text-[10px] font-bold uppercase tracking-widest">Method</th>
                    <th className="px-6 py-3.5 text-[10px] font-bold uppercase tracking-widest">Name & Path</th>
                    <th className="px-6 py-3.5 text-[10px] font-bold uppercase tracking-widest">Last Run</th>
                    <th className="px-6 py-3.5 text-[10px] font-bold uppercase tracking-widest">Time</th>
                    <th className="px-6 py-3.5 text-right"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {endpoints.map((ep) => (
                    <tr key={ep.id} className="hover:bg-gray-50 transition-colors group cursor-pointer">
                      <td className="px-6 py-4 whitespace-nowrap">
                        <span className={cn(
                          "px-2 py-0.5 rounded text-[10px] font-extrabold uppercase",
                          ep.method === "POST" ? "bg-blue-50 text-blue-700" :
                          ep.method === "GET" ? "bg-emerald-50 text-emerald-700" :
                          "bg-purple-50 text-purple-700"
                        )}>
                          {ep.method}
                        </span>
                      </td>
                      <td className="px-6 py-4">
                        <p className="text-sm font-bold text-gray-900">{ep.name}</p>
                        <p className="text-[11px] font-mono text-gray-400 mt-0.5">{ep.path}</p>
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex items-center gap-2">
                          <span className={cn(
                            "px-2 py-0.5 text-[9px] font-bold rounded-full uppercase",
                            ep.status === 'passed' ? "bg-emerald-50 text-emerald-700 border border-emerald-100" : "bg-red-50 text-red-700 border border-red-100"
                          )}>
                            {ep.status}
                          </span>
                        </div>
                      </td>
                      <td className="px-6 py-4 font-mono text-xs text-gray-400">
                        {ep.time}
                      </td>
                      <td className="px-6 py-4 text-right">
                        <div className="flex items-center justify-end gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                          <button className="p-1.5 hover:bg-blue-50 rounded-md text-blue-600 transition-colors active:scale-90">
                            <Play size={14} fill="currentColor" />
                          </button>
                          <button className="p-1.5 hover:bg-gray-100 rounded-md text-gray-400 hover:text-gray-900 transition-colors">
                            <Edit3 size={14} />
                          </button>
                          <button className="p-1.5 hover:bg-red-50 rounded-md text-red-500 transition-colors">
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="bg-white border border-gray-200 rounded-xl shadow-sm p-4 flex items-center gap-4">
            <div className="p-3 bg-emerald-50 rounded-xl text-emerald-600 border border-emerald-100">
              <Terminal size={20} />
            </div>
            <div className="flex-1">
              <h4 className="text-[10px] font-bold uppercase tracking-widest text-gray-400">Real-time Assertion Log</h4>
              <p className="text-xs text-gray-600 font-mono mt-1 truncate">
                <span className="text-emerald-600 font-bold">[PASS]</span> profile_fetch: status: 200, latency: 42ms
              </p>
            </div>
            <button className="px-3 py-1 bg-gray-50 text-[10px] font-bold text-gray-500 uppercase rounded border border-gray-100 hover:bg-gray-100 transition-colors">
              Expand CLI
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
