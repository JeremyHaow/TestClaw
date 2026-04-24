import React from "react";
import { motion, AnimatePresence } from "motion/react";
import { NAV_ITEMS } from "@/src/constants";
import { cn } from "@/src/lib/utils";
import { ChevronLeft, ChevronRight } from "lucide-react";

interface SidebarProps {
  activeId: string;
  onNavigate: (id: string) => void;
  collapsed: boolean;
  setCollapsed: (v: boolean) => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ activeId, onNavigate, collapsed, setCollapsed }) => {
  return (
    <motion.aside
      initial={false}
      animate={{ width: collapsed ? 80 : 256 }}
      className="h-screen bg-white border-r border-gray-200 flex flex-col sticky top-0 z-20"
    >
      <div className="p-6 border-b border-gray-100 flex items-center justify-between h-16">
        {!collapsed && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="flex items-center gap-2 overflow-hidden"
          >
            <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center shrink-0">
               <div className="w-4 h-4 border-2 border-white rounded-sm"></div>
            </div>
            <span className="font-bold text-lg tracking-tight text-gray-900 truncate">TestFlow AI</span>
          </motion.div>
        )}
        {collapsed && (
          <div className="mx-auto w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center shrink-0">
             <div className="w-4 h-4 border-2 border-white rounded-sm"></div>
          </div>
        )}
        {!collapsed && (
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="p-1.5 hover:bg-gray-100 rounded-md transition-colors text-gray-400 hover:text-gray-600"
          >
            <ChevronLeft size={18} />
          </button>
        )}
      </div>

      <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto custom-scrollbar">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const isActive = activeId === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onNavigate(item.id)}
              className={cn(
                "w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-200 group relative",
                isActive
                  ? "bg-blue-50 text-blue-700 font-medium"
                  : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
              )}
            >
              <Icon 
                size={20} 
                className={cn(
                  "shrink-0", 
                  isActive ? "text-blue-600" : "text-gray-400 group-hover:text-gray-600"
                )} 
              />
              {!collapsed && (
                <motion.span
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  className="truncate"
                >
                  {item.label}
                </motion.span>
              )}
              {collapsed && (
                <div className="absolute left-full ml-4 px-2 py-1 bg-gray-900 text-white text-[10px] rounded opacity-0 group-hover:opacity-100 pointer-events-none transition-opacity whitespace-nowrap z-50">
                  {item.label}
                </div>
              )}
            </button>
          );
        })}
      </nav>
      
      {collapsed && (
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="mx-auto mb-4 p-2 hover:bg-gray-100 rounded-md transition-colors text-gray-400"
        >
          <ChevronRight size={20} />
        </button>
      )}

      <div className="p-4 border-t border-gray-100 bg-gray-50/30">
        <div className={cn("flex items-center gap-3 p-2", collapsed ? "justify-center" : "")}>
          <div className="w-9 h-9 rounded-full bg-gray-200 shrink-0 border border-white shadow-sm flex items-center justify-center text-xs font-bold text-gray-500">
            JH
          </div>
          {!collapsed && (
            <div className="min-w-0">
              <p className="text-sm font-semibold text-gray-900 truncate">Jeremy Hao</p>
              <p className="text-[10px] text-gray-500 font-bold uppercase tracking-wider">Enterprise Plan</p>
            </div>
          )}
        </div>
      </div>
    </motion.aside>
  );
};
