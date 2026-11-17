import {
  Activity, Bot, FileText, FlaskConical, History, LayoutDashboard, PlusCircle, Settings,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
}

export const NAV_SECTIONS: { title: string; items: NavItem[] }[] = [
  {
    title: "Workspace",
    items: [
      { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
      { to: "/tasks/new", label: "New Task", icon: PlusCircle },
      { to: "/executions", label: "Active Executions", icon: Activity },
      { to: "/history", label: "Execution History", icon: History },
    ],
  },
  {
    title: "Outputs",
    items: [
      { to: "/reports", label: "Reports", icon: FileText },
      { to: "/evaluation", label: "Evaluation", icon: FlaskConical },
    ],
  },
  {
    title: "Configure",
    items: [
      { to: "/agents", label: "Agent Configuration", icon: Bot },
      { to: "/settings", label: "Settings", icon: Settings },
    ],
  },
];

export const ALL_NAV: NavItem[] = NAV_SECTIONS.flatMap((s) => s.items);

export function titleForPath(pathname: string): string {
  if (/^\/tasks\/(?!new)[^/]+/.test(pathname)) return "Live Execution";
  if (/^\/reports\/.+/.test(pathname)) return "Report";
  const exact = ALL_NAV.find((n) => (n.end ? pathname === n.to : pathname.startsWith(n.to) && n.to !== "/"));
  return exact?.label ?? (pathname === "/" ? "Overview" : "AgentFlow");
}
