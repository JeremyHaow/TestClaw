import React, { useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { BarChart3, FileText, Bug, AlertCircle, CheckCircle2, Search, Download, ExternalLink, BrainCircuit } from "lucide-react";
import { cn } from "@/src/lib/utils";

export const Reports: React.FC = () => {
  const [selectedReport, setSelectedReport] = useState<number | null>(0);

  const reports = [
    { id: 0, title: "Auth Regression Suite", date: "Today, 10:24 AM", result: "Fail", failures: 2, coverage: "98%" },
    { id: 1, title: "Daily Checkout Scan", date: "Today, 08:00 AM", result: "Pass", failures: 0, coverage: "92%" },
    { id: 2, title: "Post-Deployment Smoke", date: "Yesterday, 11:45 PM", result: "Pass", failures: 0, coverage: "100%" },
    { id: 3, title: "User Onboarding Flow", date: "Yesterday, 04:30 PM", result: "Warning", failures: 0, coverage: "85%" },
  ];

  return (
    <div className="space-y-8 pb-12">
      <div className="flex flex-col gap-1">
        <h2 className="text-2xl font-bold tracking-tight text-gray-900">Analytics & Reports</h2>
        <p className="text-gray-500 text-sm">Comprehensive execution logs with AI-powered failure diagnostics.</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        {/* Report List */}
        <div className="lg:col-span-4 space-y-4">
          <div className="relative group">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 group-focus-within:text-blue-500 transition-colors" size={14} />
            <input 
              type="text" 
              placeholder="Filter reports..."
              className="w-full bg-white border border-gray-200 rounded-lg pl-10 pr-4 py-2 text-xs outline-none focus:border-blue-500 transition-all font-medium"
            />
          </div>
          <div className="space-y-2">
            {reports.map((report) => (
              <button
                key={report.id}
                onClick={() => setSelectedReport(report.id)}
                className={cn(
                  "w-full p-4 rounded-xl border transition-all text-left group shadow-sm",
                  selectedReport === report.id
                    ? "bg-blue-50 border-blue-200"
                    : "bg-white border-gray-100 hover:border-blue-200 hover:bg-gray-50"
                )}
              >
                <div className="flex justify-between items-start mb-2">
                  <h4 className={cn("font-bold text-sm truncate pr-4", selectedReport === report.id ? "text-blue-700" : "text-gray-900")}>{report.title}</h4>
                  <div className={cn(
                    "px-2 py-0.5 rounded-full text-[9px] font-bold uppercase tracking-widest",
                    report.result === 'Pass' ? "bg-emerald-50 text-emerald-700 font-extrabold" :
                    report.result === 'Fail' ? "bg-red-50 text-red-700 font-extrabold" : 
                    "bg-amber-50 text-amber-700 font-extrabold"
                  )}>
                    {report.result}
                  </div>
                </div>
                <div className="flex items-center justify-between text-[10px] text-gray-400 font-medium">
                  <span>{report.date}</span>
                  <div className="flex items-center gap-3">
                    <span className="flex items-center gap-1"><Bug size={10} /> {report.failures}</span>
                    <span className="flex items-center gap-1"><FileText size={10} /> {report.coverage}</span>
                  </div>
                </div>
              </button>
            ))}
          </div>
          <button className="w-full py-2.5 bg-gray-50 border border-transparent rounded-lg text-[10px] font-bold text-gray-400 uppercase tracking-widest hover:text-gray-600 hover:bg-gray-100 transition-all">
            Load More History
          </button>
        </div>

        {/* Report Detail */}
        <div className="lg:col-span-8 space-y-6">
          <AnimatePresence mode="wait">
            {selectedReport !== null ? (
              <motion.div
                key={selectedReport}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                className="space-y-6"
              >
                {/* Header Card */}
                <div className="bg-white border border-gray-200 rounded-xl shadow-sm p-6 flex items-center justify-between">
                  <div className="flex items-center gap-4">
                    <div className="p-3 bg-blue-50 rounded-xl text-blue-600 border border-blue-100 shadow-sm">
                      <BarChart3 size={24} />
                    </div>
                    <div>
                      <h3 className="text-lg font-bold text-gray-900">{reports[selectedReport].title}</h3>
                      <p className="text-xs text-gray-400 font-mono">Trace ID: NEX-8829-4410-X</p>
                    </div>
                  </div>
                  <div className="flex gap-1.5 p-1 bg-gray-50 rounded-lg">
                    <button className="p-2 hover:bg-white hover:shadow-sm rounded-md text-gray-400 hover:text-blue-600 transition-all"><Download size={18} /></button>
                    <button className="p-2 hover:bg-white hover:shadow-sm rounded-md text-gray-400 hover:text-blue-600 transition-all"><ExternalLink size={18} /></button>
                  </div>
                </div>

                {/* Failure Analysis (Alert if Fail) */}
                {reports[selectedReport].result === 'Fail' && (
                  <div className="bg-red-50 border border-red-100 rounded-xl p-8 space-y-4 shadow-xl shadow-red-500/5">
                    <div className="flex items-center gap-2 text-red-600">
                      <BrainCircuit size={18} />
                      <h4 className="font-bold uppercase tracking-widest text-[10px]">AI Diagnostic Insight</h4>
                    </div>
                    <div className="flex flex-col md:flex-row gap-8 items-start">
                      <div className="flex-1 space-y-3">
                        <p className="text-sm text-gray-700 leading-relaxed font-medium">
                          "I detected an inconsistent state in the <span className="mono bg-white px-1.5 py-0.5 border border-red-100 rounded text-red-600 font-bold">auth_payload</span>. The server returned <span className="mono font-bold text-red-700 whitespace-nowrap">403 Forbidden</span> during the session token rotation."
                        </p>
                        <div className="flex flex-wrap gap-4 pt-2">
                           <div className="flex items-center gap-2 text-[10px] font-bold text-red-500 uppercase tracking-tight">
                             <AlertCircle size={14} /> Cause: Middleware Race condition
                           </div>
                           <div className="flex items-center gap-2 text-[10px] font-bold text-blue-600 uppercase tracking-tight">
                             <CheckCircle2 size={14} /> Suggested: Add atomic lock to cache update
                           </div>
                        </div>
                      </div>
                      <div className="shrink-0 w-32 h-32 rounded-xl bg-white border border-red-100 flex flex-col items-center justify-center text-[10px] text-gray-400 text-center p-4 gap-2 shadow-sm">
                        <div className="text-xl font-bold text-red-600">98%</div>
                        <span className="font-bold uppercase tracking-tight">Pattern Match</span>
                      </div>
                    </div>
                  </div>
                )}

                {/* Log View */}
                <div className="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
                  <div className="bg-gray-50/50 px-6 py-3 border-b border-gray-100 flex items-center justify-between">
                    <h4 className="text-[10px] font-bold uppercase tracking-widest text-gray-400">Execution Timeline</h4>
                    <span className="text-[10px] font-mono font-bold text-blue-600 bg-blue-50 px-2 py-0.5 rounded">48 Total Steps</span>
                  </div>
                  <div className="p-6 font-mono text-[11px] space-y-3 max-h-[400px] overflow-y-auto custom-scrollbar bg-gray-50 border-b border-gray-50">
                    {[
                      { t: "10:24:01.002", lv: "INFO", m: "Initializing headless browser instance (Playwright/Chrome)..." },
                      { t: "10:24:01.450", lv: "INFO", m: "Navigating to: https://nexus-app-staging.ai/login" },
                      { t: "10:24:02.112", lv: "DEBUG", m: "DOM Content Loaded (284ms)" },
                      { t: "10:24:02.120", lv: "INFO", m: "Filling credentials for user: automated_test_user_7" },
                      { t: "10:24:03.001", lv: "INFO", m: "Submitting auth form [Button: Login]" },
                      { t: "10:24:03.450", lv: "WARN", m: "Latent response detected: 449ms in [POST] /api/v1/auth/login" },
                      { t: "10:24:04.011", lv: "ERROR", m: "Assertion Error: Expected status 200, got 403" },
                      { t: "10:24:04.012", lv: "ERROR", m: "Traceback saved to DIAG_NEX_8829.json" },
                      { t: "10:24:04.015", lv: "INFO", m: "Closing session. Test Failed." },
                    ].map((log, i) => (
                      <div key={i} className="flex gap-4 group">
                        <span className="text-gray-300 shrink-0 font-bold">{log.t}</span>
                        <span className={cn(
                          "shrink-0 font-bold w-12",
                          log.lv === 'ERROR' ? "text-red-500" : 
                          log.lv === 'WARN' ? "text-amber-500" : "text-blue-500"
                        )}>{log.lv}</span>
                        <span className="text-gray-600 group-hover:text-gray-900 transition-colors">{log.m}</span>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <div className="bg-white border border-gray-200 rounded-xl shadow-sm p-6">
                    <h4 className="text-[10px] font-bold uppercase tracking-widest text-gray-400 mb-6">Coverage Heatmap</h4>
                    <div className="grid grid-cols-10 gap-1.5">
                      {Array.from({ length: 40 }).map((_, i) => (
                        <div 
                          key={i} 
                          className={cn(
                            "aspect-square rounded-[2px] transition-colors hover:scale-110 cursor-help shadow-sm",
                            i % 7 === 0 ? "bg-emerald-500" :
                            i % 4 === 0 ? "bg-emerald-300" :
                            i % 12 === 0 ? "bg-gray-100" : "bg-emerald-100"
                          )} 
                          title={`Segment ${i}: 85% coverage`}
                        />
                      ))}
                    </div>
                  </div>
                  <div className="bg-white border border-gray-200 rounded-xl shadow-sm p-6 flex flex-col justify-between">
                     <h4 className="text-[10px] font-bold uppercase tracking-widest text-gray-400 mb-6">Performance Baseline</h4>
                     <div className="flex items-end gap-1.5 h-24 px-2">
                        {[40, 60, 30, 80, 50, 90, 70, 45, 65, 35].map((h, i) => (
                          <div 
                            key={i} 
                            className="bg-blue-100 w-full hover:bg-blue-600 transition-all rounded-t-sm cursor-help hover:h-full group relative" 
                            style={{ height: `${h}%` }}
                          >
                            <div className="absolute -top-6 left-1/2 -translate-x-1/2 bg-gray-900 text-white text-[9px] px-1.5 py-0.5 rounded opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap">{h}ms</div>
                          </div>
                        ))}
                     </div>
                     <p className="text-[9px] text-gray-400 mt-4 italic font-bold uppercase tracking-tighter">Relative execution delta (ms)</p>
                  </div>
                </div>
              </motion.div>
            ) : (
              <div className="h-[400px] flex flex-col items-center justify-center text-gray-300 bg-white border-2 border-dashed border-gray-100 rounded-2xl">
                <BarChart3 size={40} className="opacity-20 mb-3" />
                <p className="text-sm font-medium">Select a report to view diagnostics.</p>
              </div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
};
