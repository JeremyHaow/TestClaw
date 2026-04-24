import { LayoutDashboard, Settings2, FileCode, Beaker, MonitorPlay, BarChart3 } from "lucide-react";

export const NAV_ITEMS = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "agent-config", label: "Agent Config", icon: Settings2 },
  { id: "case-gen", label: "Case Generation", icon: FileCode },
  { id: "api-testing", label: "API Testing", icon: Beaker },
  { id: "ui-testing", label: "UI Testing", icon: MonitorPlay },
  { id: "reports", label: "Reports", icon: BarChart3 },
];
