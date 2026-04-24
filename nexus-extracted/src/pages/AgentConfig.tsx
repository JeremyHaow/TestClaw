import React, { useState } from "react";
import { motion } from "motion/react";
import { Save, Bot, Cpu, MessageSquare, Zap, ShieldCheck } from "lucide-react";
import { cn } from "@/src/lib/utils";

export const AgentConfig: React.FC = () => {
  const [activeModel, setActiveModel] = useState("gemini-3.1-pro");
  const [agentType, setAgentType] = useState("explorer");

  const models = [
    { id: "gemini-3.1-pro", name: "Gemini 3.1 Pro", desc: "Best for complex reasoning and multi-step UI paths.", icon: Cpu },
    { id: "gemini-3-flash", name: "Gemini 3 Flash", desc: "Fast for rapid API regression and status checks.", icon: Zap },
    { id: "custom-llm", name: "Custom LLM Registry", desc: "Connect your enterprise model via internal proxy.", icon: ShieldCheck },
  ];

  const types = [
    { id: "explorer", name: "探索性 (Exploratory)", desc: "Aggressively finds new paths and undefined behaviors." },
    { id: "boundary", name: "严格边界 (Strict Boundary)", desc: "Strictly focuses on edge cases and validation logic." },
    { id: "performance", name: "Performance Profiler", desc: "Identifying bottleneck patterns during execution." },
  ];

  return (
    <div className="max-w-6xl space-y-8 pb-12">
      <div className="flex items-center justify-between">
        <div className="flex flex-col gap-1">
          <h2 className="text-2xl font-bold tracking-tight text-gray-900">Agent Configuration</h2>
          <p className="text-gray-500 text-sm">Define the intelligence and behavior of your AI testing workforce.</p>
        </div>
        <div className="flex gap-3">
          <button className="flex items-center gap-2 px-4 py-2 bg-gray-900 hover:bg-black text-white rounded-md text-sm font-bold transition-all shadow-sm active:scale-95">
            <Save size={16} />
            保存设置
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
        <div className="lg:col-span-8 space-y-8">
          {/* Model Selection */}
          <section className="bg-white border border-gray-200 rounded-xl shadow-sm p-6 space-y-4">
            <h3 className="text-xs font-bold text-gray-400 uppercase tracking-widest flex items-center gap-2">
               Intelligence Core
            </h3>
            <div className="grid grid-cols-1 gap-2">
              {models.map((model) => (
                <button
                  key={model.id}
                  onClick={() => setActiveModel(model.id)}
                  className={cn(
                    "flex items-center gap-4 p-4 rounded-lg border transition-all text-left",
                    activeModel === model.id
                      ? "bg-blue-50 border-blue-200 ring-1 ring-blue-100"
                      : "bg-white border-gray-100 hover:bg-gray-50"
                  )}
                >
                  <div className={cn(
                    "p-2.5 rounded-lg shrink-0",
                    activeModel === model.id ? "bg-blue-600 text-white shadow-md shadow-blue-600/20" : "bg-gray-100 text-gray-400"
                  )}>
                    <model.icon size={18} />
                  </div>
                  <div>
                    <h4 className={cn("text-sm font-bold", activeModel === model.id ? "text-blue-700" : "text-gray-900")}>{model.name}</h4>
                    <p className="text-xs text-gray-500 mt-0.5">{model.desc}</p>
                  </div>
                </button>
              ))}
            </div>
          </section>

          {/* System Prompt */}
          <section className="bg-white border border-gray-200 rounded-xl shadow-sm p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-bold text-gray-400 uppercase tracking-widest">Agent Directives (System Prompt)</h3>
              <span className="text-[10px] font-mono text-gray-400">Context: 4,208 Tokens</span>
            </div>
            <div className="relative group rounded-xl border border-gray-100 bg-gray-50/50 focus-within:border-blue-300 focus-within:bg-white transition-all">
              <textarea
                rows={10}
                className="w-full bg-transparent p-4 outline-none resize-none font-mono text-xs leading-relaxed text-gray-700 h-[300px]"
                defaultValue={`Act as a Senior QA Automation Engineer. 
Your goal is to perform ${agentType === 'explorer' ? 'exploratory' : 'strict boundary'} testing.

Core Instructions:
1. Maximize coverage by traversing unexpected DOM paths.
2. Prioritize security vulnerabilities in form submissions.
3. Log all network latency spikes >200ms.
4. If a state change is detected, re-verify previous assumptions.`}
              />
              <div className="absolute bottom-4 right-4 flex gap-2">
                <button className="px-3 py-1 bg-white border border-gray-200 rounded text-[10px] font-bold text-gray-500 hover:text-blue-600 shadow-sm transition-colors uppercase">Optimize</button>
                <button className="px-3 py-1 bg-white border border-gray-200 rounded text-[10px] font-bold text-gray-500 hover:text-red-500 shadow-sm transition-colors uppercase">Reset</button>
              </div>
            </div>
          </section>
        </div>

        {/* Sidebar Settings */}
        <div className="lg:col-span-4 space-y-6">
          <section className="bg-white border border-gray-200 rounded-xl shadow-sm p-6 space-y-4">
            <h3 className="text-xs font-bold text-gray-400 uppercase tracking-widest">测试偏好 (Strategy)</h3>
            <div className="grid grid-cols-1 gap-2">
              {types.map((type) => (
                <button
                  key={type.id}
                  onClick={() => setAgentType(type.id)}
                  className={cn(
                    "w-full p-3 rounded-lg border text-left transition-all",
                    agentType === type.id
                      ? "bg-blue-100 border-blue-200 text-blue-700"
                      : "bg-white border-gray-100 text-gray-500 hover:bg-gray-50"
                  )}
                >
                  <p className="text-xs font-bold">{type.name}</p>
                </button>
              ))}
            </div>
          </section>

          <section className="bg-white border border-gray-200 rounded-xl shadow-sm p-6 space-y-6">
            <h3 className="text-xs font-bold text-gray-400 uppercase tracking-widest">Hyperparameters</h3>
            <div className="space-y-6">
              {[
                { label: "Creativity", value: 0.7, p: "70%" },
                { label: "Search Depth", value: 5, p: "50%" },
                { label: "Step Limit", value: 50, p: "50%" },
              ].map((p) => (
                <div key={p.label} className="space-y-2">
                  <div className="flex justify-between text-[11px] font-bold uppercase tracking-tight">
                    <span className="text-gray-400">{p.label}</span>
                    <span className="text-blue-600 font-mono">{p.value}</span>
                  </div>
                  <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
                    <div 
                      className="h-full bg-blue-600" 
                      style={{ width: p.p }} 
                    />
                  </div>
                </div>
              ))}
            </div>
          </section>

          <div className="bg-blue-600 rounded-xl p-6 text-white shadow-xl shadow-blue-600/20">
             <div className="text-[10px] opacity-70 uppercase font-bold tracking-widest mb-1">AI Analysis</div>
             <p className="text-sm leading-relaxed mb-4">Discover 3 potential high-risk APIs. Suggest calling "Case Generation" ASAP.</p>
             <button className="text-xs font-bold bg-white text-blue-600 px-3 py-1.5 rounded shadow-sm active:scale-95 transition-all">立即优化</button>
          </div>
        </div>
      </div>
    </div>
  );
};
