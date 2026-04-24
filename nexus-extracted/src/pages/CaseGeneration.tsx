import React, { useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Upload, Link as LinkIcon, Sparkles, Plus, Trash2, CheckCircle, Loader2, FileCode } from "lucide-react";
import { generateTestCases } from "@/src/services/gemini";
import { cn } from "@/src/lib/utils";

interface TestCase {
  title: string;
  steps: string[];
  expectedOutcome: string;
}

export const CaseGeneration: React.FC = () => {
  const [inputType, setInputType] = useState<'doc' | 'swagger'>('doc');
  const [inputValue, setInputValue] = useState('');
  const [generating, setGenerating] = useState(false);
  const [cases, setCases] = useState<TestCase[]>([]);

  const handleGenerate = async () => {
    if (!inputValue.trim()) return;
    setGenerating(true);
    try {
      const result = await generateTestCases(inputValue, inputType);
      setCases(result);
    } catch (err) {
      console.error(err);
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className="space-y-8 pb-12">
      <div className="flex flex-col gap-1">
        <h2 className="text-2xl font-bold tracking-tight text-gray-900">Case Generation Center</h2>
        <p className="text-gray-500 text-sm">Transform requirements or API specs into executable test plans instantly.</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 items-start">
        <div className="space-y-6">
          <div className="bg-white border border-gray-200 rounded-xl shadow-sm p-6 space-y-6">
            <div className="flex p-1 bg-gray-100 rounded-lg self-start border border-gray-200">
              <button
                onClick={() => setInputType('doc')}
                className={cn(
                  "px-4 py-1.5 rounded-md text-xs font-bold transition-all",
                  inputType === 'doc' ? "bg-white text-blue-600 shadow-sm" : "text-gray-500 hover:text-gray-900"
                )}
              >
                Requirements Doc
              </button>
              <button
                onClick={() => setInputType('swagger')}
                className={cn(
                  "px-4 py-1.5 rounded-md text-xs font-bold transition-all ml-1",
                  inputType === 'swagger' ? "bg-white text-blue-600 shadow-sm" : "text-gray-500 hover:text-gray-900"
                )}
              >
                Swagger / OpenAPI
              </button>
            </div>

            <div className="space-y-2">
              <label className="text-[10px] font-bold text-gray-400 uppercase tracking-widest block">
                {inputType === 'doc' ? 'Requirements Detail' : 'OpenAPI Definition'}
              </label>
              <textarea
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                rows={12}
                className="w-full bg-gray-50 rounded-xl border border-gray-100 p-4 text-xs font-mono focus:border-blue-500 focus:bg-white outline-none transition-all placeholder:text-gray-300"
                placeholder={inputType === 'doc' ? "Paste user stories, PRDs or acceptance criteria..." : "Paste Swagger JSON or URL..."}
              />
            </div>

            <button
              onClick={handleGenerate}
              disabled={generating || !inputValue}
              className="w-full py-3 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-xl font-bold flex items-center justify-center gap-2 transition-all shadow-lg shadow-blue-600/10 active:scale-[0.98]"
            >
              {generating ? (
                <>
                  <Loader2 className="animate-spin" size={18} />
                  AI Architecting Plan...
                </>
              ) : (
                <>
                  <Sparkles size={18} />
                  Generate AI Test Cases
                </>
              )}
            </button>
          </div>

          <div className="bg-white border-2 border-dashed border-gray-200 rounded-xl p-8 flex flex-col items-center justify-center gap-3 group hover:border-blue-300 transition-colors cursor-pointer">
            <div className="p-3 bg-gray-50 rounded-full text-gray-400 group-hover:bg-blue-50 group-hover:text-blue-600 transition-all">
              <Upload size={24} />
            </div>
            <p className="text-sm font-bold text-gray-700">Drag & Drop Documents</p>
            <p className="text-[10px] text-gray-400 font-medium">Supports PDF, DOCX, Markdown</p>
          </div>
        </div>

        <div className="space-y-6">
          <div className="flex items-center justify-between px-2">
            <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
              Draft Selection
              <span className="bg-gray-100 text-gray-600 px-2.5 py-0.5 rounded text-[10px] font-mono font-bold border border-gray-200">{cases.length}</span>
            </h3>
            {cases.length > 0 && (
              <button className="text-xs font-bold text-blue-600 hover:underline">
                Add All to Suite
              </button>
            )}
          </div>

          <div className="space-y-4 max-h-[720px] overflow-y-auto pr-2 custom-scrollbar">
            <AnimatePresence mode="popLayout">
              {cases.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-80 bg-white border border-dashed border-gray-200 rounded-2xl text-gray-300">
                  <FileCode size={40} className="mb-4 opacity-50" />
                  <p className="text-sm font-medium">No cases generated yet.</p>
                </div>
              ) : (
                cases.map((c, i) => (
                  <motion.div
                    key={i}
                    initial={{ opacity: 0, x: 10 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: i * 0.05 }}
                    className="bg-white border border-gray-200 rounded-xl p-5 shadow-sm relative group hover:border-blue-300 transition-all"
                  >
                    <div className="flex justify-between items-start mb-4">
                      <h4 className="font-bold text-gray-900 pr-8 text-sm">{c.title}</h4>
                      <button 
                        onClick={() => setCases(prev => prev.filter((_, idx) => idx !== i))}
                        className="text-gray-400 hover:text-red-500 transition-colors"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                    <div className="space-y-4">
                      <div>
                        <span className="text-[9px] font-bold uppercase tracking-widest text-gray-400">Steps</span>
                        <ul className="mt-2 space-y-2">
                          {c.steps.map((s, si) => (
                            <li key={si} className="text-xs flex gap-2">
                              <span className="text-blue-500 font-mono font-bold text-[9px] mt-0.5">{si + 1}</span>
                              <span className="text-gray-600 leading-relaxed">{s}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                      <div className="pt-4 border-t border-gray-50 flex flex-col gap-1">
                        <span className="text-[9px] font-bold uppercase tracking-widest text-emerald-600">Expected Outcome</span>
                        <p className="text-xs text-gray-500 italic leading-relaxed">
                          "{c.expectedOutcome}"
                        </p>
                      </div>
                    </div>
                    <button className="absolute bottom-5 right-5 p-2 bg-blue-600 rounded-lg text-white opacity-0 group-hover:opacity-100 transition-all scale-90 group-hover:scale-100 shadow-lg shadow-blue-600/20 active:scale-95">
                      <Plus size={14} />
                    </button>
                  </motion.div>
                ))
              )}
            </AnimatePresence>
          </div>
        </div>
      </div>
    </div>
  );
};
