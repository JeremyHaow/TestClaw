import React, { useState } from "react";
import { motion } from "motion/react";
import { MousePointer2, Layout, Database, Sparkles, Wand2, PlayCircle, Code, Eye, Layers } from "lucide-react";
import { cn } from "@/src/lib/utils";

export const UiTesting: React.FC = () => {
  const [activeTab, setActiveTab] = useState("elements");

  const pageObjects = [
    { name: "Auth", elements: 12, screen: "Login/Signup" },
    { name: "MainDashboard", elements: 45, screen: "Home" },
    { name: "CartSidebar", elements: 18, screen: "Global" },
    { name: "SettingsPanel", elements: 24, screen: "User/Settings" },
  ];

  return (
    <div className="space-y-8 pb-12">
      <div className="flex items-center justify-between">
        <div className="flex flex-col gap-1">
          <h2 className="text-2xl font-bold tracking-tight text-gray-900">UI Automation</h2>
          <p className="text-gray-500 text-sm">Visual element mapping and AI-generated interaction scripts.</p>
        </div>
        <button className="flex items-center gap-2 px-6 py-2 bg-blue-600 hover:bg-black text-white rounded-lg font-bold transition-all shadow-md active:scale-95 text-xs">
          <Wand2 size={16} />
          Scan DOM Context
        </button>
      </div>

      <div className="flex gap-1 p-1 bg-gray-100 border border-gray-200 rounded-lg self-start">
        {[
          { id: "elements", label: "Page Objects", icon: Layout },
          { id: "scripts", label: "AI Scripts", icon: Code },
          { id: "recorder", label: "Recorder", icon: MousePointer2 },
        ].map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={cn(
              "flex items-center gap-2 px-4 py-1.5 rounded-md text-xs font-bold transition-all",
              activeTab === tab.id ? "bg-white text-blue-600 shadow-sm" : "text-gray-500 hover:text-gray-900"
            )}
          >
            <tab.icon size={14} />
            {tab.label}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        {/* Left List */}
        <div className="lg:col-span-4 space-y-4">
          <div className="flex items-center justify-between px-2">
            <h3 className="text-[10px] font-bold uppercase tracking-widest text-gray-400">Page Libraries</h3>
            <button className="text-[10px] font-bold text-blue-600 hover:underline">+ Import Node</button>
          </div>
          <div className="grid grid-cols-1 gap-3">
            {pageObjects.map((po) => (
              <button
                key={po.name}
                className="bg-white border border-gray-200 p-4 rounded-xl text-left hover:border-blue-300 transition-all flex items-center justify-between group shadow-sm"
              >
                <div>
                  <h4 className="font-bold text-gray-900 text-sm">{po.name}</h4>
                  <p className="text-[10px] text-gray-400 mt-0.5 font-bold uppercase tracking-tight">{po.screen}</p>
                </div>
                <div className="text-right">
                  <p className="text-xl font-bold font-mono text-blue-600">{po.elements}</p>
                  <p className="text-[9px] text-gray-400 uppercase font-bold tracking-widest">Nodes</p>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Center Canvas / Detail */}
        <div className="lg:col-span-8 space-y-6">
          <div className="bg-white border border-gray-200 rounded-xl shadow-sm min-h-[500px] flex flex-col p-6 border-blue-100 bg-blue-50/10">
            <div className="flex items-center justify-between mb-8">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-blue-600 rounded-lg text-white shadow-lg shadow-blue-600/20">
                  <Sparkles size={18} />
                </div>
                <div>
                  <h3 className="font-bold text-gray-900">MainDashboard.po</h3>
                  <p className="text-[10px] font-bold text-blue-500 uppercase tracking-widest">Visual Analysis Enabled</p>
                </div>
              </div>
              <div className="flex items-center gap-1.5 px-1 py-1 bg-gray-50 border border-gray-100 rounded-lg">
                <button className="p-1.5 hover:bg-white hover:shadow-sm rounded-md text-gray-400 hover:text-blue-600 transition-all"><Eye size={16} /></button>
                <button className="p-1.5 hover:bg-white hover:shadow-sm rounded-md text-gray-400 hover:text-blue-600 transition-all"><Layers size={16} /></button>
                <div className="w-px h-5 bg-gray-200 mx-1" />
                <button className="flex items-center gap-2 px-3 py-1.5 bg-blue-600 hover:bg-black text-white rounded-md text-[10px] font-bold transition-all shadow-sm">
                  <PlayCircle size={14} /> Run Script
                </button>
              </div>
            </div>

            <div className="flex-1 border border-gray-200 rounded-2xl bg-gray-900 overflow-hidden flex flex-col shadow-inner">
              <div className="flex-1 p-6 font-mono text-xs space-y-4 overflow-y-auto custom-scrollbar">
                <div className="text-gray-500 italic">{'// AI Synthesis Output (v3.1)'}</div>
                <div>
                  <span className="text-pink-400 font-bold">const</span> <span className="text-blue-400">DashboardActions</span> = {'{'}
                </div>
                <div className="pl-6 space-y-2">
                  <div>
                    <span className="text-emerald-400">toggleFilters</span>: <span className="text-amber-400">async</span> () ={'>'} {'{'}
                  </div>
                  <div className="pl-6 text-gray-400">
                    <span className="text-blue-400">await</span> {'page.locator('}<span className="text-emerald-400 underline decoration-emerald-400/30">'[data-test="filter-btn"]'</span>{').click();'}
                  </div>
                  <div>{'  },'}</div>
                  
                  <div>
                    <span className="text-emerald-400">searchForProduct</span>: <span className="text-amber-400">async</span> (query) ={'>'} {'{'}
                  </div>
                  <div className="pl-6 text-gray-400">
                    <span className="text-blue-400">await</span> {'page.fill('}<span className="text-emerald-400 underline decoration-emerald-400/30">'#main-search'</span>{', query);'}
                    <br />
                    <span className="text-blue-400">await</span> {'page.keyboard.press('}<span className="text-emerald-400">'Enter'</span>{');'}
                  </div>
                  <div>{'  },'}</div>
                </div>
                <div>{'};'}</div>
              </div>
              <div className="h-10 border-t border-gray-800 bg-black px-4 flex items-center justify-between">
                <div className="flex gap-4">
                   <span className="text-[9px] font-bold text-gray-500 uppercase tracking-widest">Type: Playwright</span>
                   <span className="text-[9px] font-bold text-emerald-500 uppercase tracking-widest">Mode: AI-Optimized</span>
                </div>
                <button className="text-[9px] font-bold text-gray-400 hover:text-white uppercase transition-colors tracking-widest">Regenerate</button>
              </div>
            </div>
          </div>
          
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-white border border-gray-200 rounded-xl p-4 flex items-center gap-4 shadow-sm hover:shadow-md transition-shadow">
              <div className="p-2.5 bg-amber-50 rounded-lg text-amber-600 border border-amber-100">
                <Database size={20} />
              </div>
              <div>
                <p className="text-[10px] text-gray-400 font-bold uppercase tracking-widest">Mock Context</p>
                <p className="text-sm font-bold text-gray-900 truncate">User Context V2 (Staging)</p>
              </div>
            </div>
             <div className="bg-white border border-gray-200 rounded-xl p-4 flex items-center gap-4 shadow-sm hover:shadow-md transition-shadow">
              <div className="p-2.5 bg-purple-50 rounded-lg text-purple-600 border border-purple-100">
                <Layout size={20} />
              </div>
              <div>
                <p className="text-[10px] text-gray-400 font-bold uppercase tracking-widest">Viewport</p>
                <p className="text-sm font-bold text-gray-900">1920x1080 (Desktop)</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
