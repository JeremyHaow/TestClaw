/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState } from "react";
import { Sidebar } from "./components/Sidebar";
import { Dashboard } from "./pages/Dashboard";
import { AgentConfig } from "./pages/AgentConfig";
import { CaseGeneration } from "./pages/CaseGeneration";
import { ApiTesting } from "./pages/ApiTesting";
import { UiTesting } from "./pages/UiTesting";
import { Reports } from "./pages/Reports";
import { motion, AnimatePresence } from "motion/react";
import { Search, Bell, HelpCircle, ChevronRight } from "lucide-react";

export default function App() {
  const [activeTab, setActiveTab] = useState("dashboard");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  const getPageTitle = () => {
    switch (activeTab) {
      case "dashboard": return "Dashboard";
      case "agent-config": return "AI Agent Configuration";
      case "case-gen": return "Case Generation Center";
      case "api-testing": return "API Test Execution";
      case "ui-testing": return "UI Test Automation";
      case "reports": return "Test Execution Reports";
      default: return "Nexus AI";
    }
  };

  const renderContent = () => {
    switch (activeTab) {
      case "dashboard":
        return <Dashboard />;
      case "agent-config":
        return <AgentConfig />;
      case "case-gen":
        return <CaseGeneration />;
      case "api-testing":
        return <ApiTesting />;
      case "ui-testing":
        return <UiTesting />;
      case "reports":
        return <Reports />;
      default:
        return <Dashboard />;
    }
  };

  return (
    <div className="flex min-h-screen bg-gray-50 text-gray-900 font-sans">
      <Sidebar 
        activeId={activeTab} 
        onNavigate={setActiveTab} 
        collapsed={sidebarCollapsed} 
        setCollapsed={setSidebarCollapsed} 
      />
      
      <main className="flex-1 flex flex-col min-w-0 h-screen overflow-hidden">
        {/* Header */}
        <header className="h-16 border-b border-gray-200 bg-white px-8 flex items-center justify-between shrink-0 z-40">
          <div className="flex items-center gap-3">
             <h1 className="text-lg font-semibold text-gray-900">{getPageTitle()}</h1>
          </div>

          <div className="flex items-center gap-6">
            <div className="hidden md:flex items-center gap-3 bg-gray-100 px-4 py-1.5 rounded-lg border border-gray-200 w-80">
              <Search size={16} className="text-gray-400" />
              <input 
                type="text" 
                placeholder="Search resources..." 
                className="bg-transparent border-none outline-none text-sm w-full placeholder:text-gray-500"
              />
            </div>
            
            <div className="flex items-center gap-2">
              <button className="p-2 text-gray-400 hover:bg-gray-100 hover:text-gray-900 rounded-lg transition-all relative">
                <Bell size={18} />
                <span className="absolute top-2 right-2 w-1.5 h-1.5 bg-blue-600 rounded-full border-2 border-white" />
              </button>
              <button className="p-2 text-gray-400 hover:bg-gray-100 hover:text-gray-900 rounded-lg transition-all">
                <HelpCircle size={18} />
              </button>
              <div className="h-6 w-px bg-gray-200 mx-2" />
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-mono bg-emerald-50 text-emerald-700 px-2 py-0.5 rounded border border-emerald-200 font-bold tracking-tight">AGENT ONLINE</span>
                <button className="px-4 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-xs font-bold rounded-md shadow-sm transition-all shadow-blue-600/10">Run Suite</button>
              </div>
            </div>
          </div>
        </header>

        {/* Content Area */}
        <div className="flex-1 overflow-y-auto custom-scrollbar p-8">
          <AnimatePresence mode="wait">
            <motion.div
              key={activeTab}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.2 }}
            >
              {renderContent()}
            </motion.div>
          </AnimatePresence>
        </div>
      </main>

      <style>{`
        .custom-scrollbar::-webkit-scrollbar {
          width: 5px;
        }
        .custom-scrollbar::-webkit-scrollbar-track {
          background: transparent;
        }
        .custom-scrollbar::-webkit-scrollbar-thumb {
          background: #E5E7EB;
          border-radius: 10px;
        }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover {
          background: #2563EB;
        }
        .active-shadow {
          box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
        }
      `}</style>
    </div>
  );
}
